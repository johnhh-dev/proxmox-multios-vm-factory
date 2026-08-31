# Failed Arc onboarding — a guest that booted and never reached Azure

DOC-005 (#73), the third of the three runbooks it asks for.
[incident-orphan-vm.md](incident-orphan-vm.md) covers a VM Proxmox has and
Terraform does not; [state-recovery.md](state-recovery.md) covers a lost state.
This one covers a VM that came up perfectly and is not in Azure.

[arc-cleanup.md](arc-cleanup.md) is the mirror image — an Azure machine left
behind after the VM is gone — and is not repeated here.

## The shape of this failure

**The apply succeeds. The VM is healthy. Nothing is red.** Arc onboarding runs
inside the guest, after Terraform has finished and reported success, and no
step anywhere reports whether it worked.

That is deliberate — BUG-007-A6 made a failed onboarding stop aborting the rest
of Windows first boot, because it was leaving guests with no autologon and no
RDP over an Azure problem — but the consequence is that **the only place a
failure is recorded is inside the guest**, and nobody looks there unless they
already suspect something.

So the first question is never "why did it fail". It is "which of the four
stages did it get to".

## The chain

| # | Stage | Where it runs | Where a failure shows |
|---|---|---|---|
| 1 | Mint a token from the service principal | runner, `mint_arc_token.py` | job log, `::error::arc token:` — **or nowhere, see below** |
| 2 | Token rendered into vendor-data, uploaded as a snippet | runner → Proxmox node | job log, as a Terraform error |
| 3 | Guest runs the onboarding script at first boot | the guest | `/var/log/arc-onboard.log` · `C:\arc-onboard.log` |
| 4 | Machine appears in Azure | Azure | the post-apply smoke test, as a `::warning::` (KAN-017-A5) |

Stages 1 and 2 fail loudly and fail the workflow. Stage 3 still fails nothing at
all — the guest records it and no one is told.

Stage 4 is now checked, and warns rather than fails, because "not yet" and
"never" are indistinguishable from outside the guest. A run that just built an
Arc VM waits up to five minutes for the machine to appear before giving up, so a
warning here means it did not show up in that window — not that it never will.

## Start here: the failure that reports nothing

**Arc is configured per VM in `locals.tf`, and the credentials that make it work
are configured in GitHub secrets. Nothing checks that the two agree.**

If `TF_VAR_ARC_SP_ID`, `TF_VAR_ARC_SP_SECRET` or `TF_VAR_ARC_TENANT_ID` is
missing or empty, `mint_arc_token.py` does this and **exits 0**:

```text
arc token: no service principal configured, so no token is minted.
Guests will skip Arc onboarding.
```

The guest then does the matching thing, and also exits 0:

```text
Azure Arc: no onboarding token was minted for this run. Skipping connect.
```

The apply is green. The VM works. `arc = true` in the inventory is simply
untrue, and has been for every VM built since whenever the secret went missing.

**Check this first**, because it costs one grep of the job log and explains the
majority of "it just never appeared in Azure":

```bash
gh run view <run-id> --log | grep -i "arc token:"
```

`no service principal configured` means stop here and fix the secrets —
[operator-setup.md §1](operator-setup.md) lists them. Nothing in the guest is
wrong.

This is a deliberate design choice, not an oversight: Arc is optional per VM and
optional for the lab, so failing the mint would break an apply for a lab that
never asked for Arc. The cost is this failure mode, which is why it is the first
section of this file.

## Stage 3 — the guest tried and could not

### Where the guest wrote it down

**Linux**

```bash
sudo cat /var/log/arc-onboard.log
sudo azcmagent show
```

**Windows**

```powershell
Get-Content C:\arc-onboard.log
Get-Content C:\cloudbase-firstboot-test.log   # says whether onboarding failed
& "$env:ProgramFiles\AzureConnectedMachineAgent\azcmagent.exe" show
```

`cloudbase-firstboot-test.log` ends with one of two lines, and the difference is
the whole diagnosis:

```text
Cloudbase first-boot script complete
Cloudbase first-boot script complete, but Azure Arc onboarding failed - see C:\arc-onboard.log
```

Note that on Linux the onboarding script deletes itself when it finishes, once
SEC-001d-A1 ([#149](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/pull/149))
lands. Its absence is not evidence of anything, and the log is what survives on
purpose.

### Reading what the log says

| Log line | Meaning | Fix |
|---|---|---|
| `Azure Arc: disabled for this VM` | `arc` is false in `locals.tf` for this guest | inventory, not a failure |
| `no onboarding token was minted for this run` | stage 1 — see above | secrets |
| `enabled but required TF_VAR_arc_* values are missing` | subscription, tenant, resource group or location is empty | secrets |
| `Waiting for network/DNS...` repeated to `(60/60)` | the guest never got off its own network | network, DNS, gateway — see below |
| `Connect failed. Sleeping 30s...` ten times | Azure was reached and refused | read the `azcmagent` output above it |

### The two that look alike and are not

**No network.** The Linux script waits up to five minutes (60 × 5s) for a
default route and a DNS answer before it even tries. Since KAN-012 that is the
same pair the Windows script has always checked — a local route test, then
resolving `var.network_probe_host` — rather than a ping to a hard-coded public
address, which failed in any lab that filters egress ICMP while the network was
in fact fine. If it exhausts that, the problem is
FEAT-003 territory: a static address with a gateway outside its subnet plans
clean and boots a guest with no route off its own network. `local.dns_servers`
is the resolver list, and it reaches the guest twice (BUG-018) — through
`initialization.dns.servers` and, on Windows, through the first-boot script,
which sets DNS itself precisely because Cloudbase-Init has not applied the
network configuration by the time Arc needs to resolve `aka.ms`.

**Network fine, Azure refused.** The token is the usual reason, and it has two
distinct forms.

### The one the ADR predicted

**The token expires.** ADR 0001 chose a short-lived access token over the
service-principal secret, and §8 names this as the thing that would change the
decision:

> Access tokens prove too short-lived for a slow Windows first boot → move to
> `--service-principal-cert` (option C's reserve).

The token is minted **on the runner, during the apply**. The clock starts there,
not when the guest boots. Everything between — the clone, the snippet upload,
Proxmox starting the VM, the guest reaching the network, a Windows first boot
that installs the agent and reboots — is spent.

Symptom: `azcmagent connect` fails immediately rather than timing out, and the
`azcmagent` output names an authentication error rather than a network one. If
onboarding failed on a guest that clearly had working DNS, this is the first
hypothesis.

It has not been observed here. If it is observed, that is the evidence ADR
0001 §8 asks for, and the answer is `--service-principal-cert`, not a longer
token — a token long enough to survive an arbitrary boot delay is a
service-principal secret with extra steps.

### The other token failure

**A name that is already taken.** If a machine of the same name exists in Azure
from a previous life of this VM, the connect can fail on the name rather than on
the credential. That is the orphan case from the other direction —
[arc-cleanup.md](arc-cleanup.md) explains why an Arc machine survives a VM that
did not, and how to find it.

## Recovery

### The thing that does not work

**Re-running the apply does not retry onboarding.** This is the single most
important line in this file.

A vendor-data change replaces the *snippet* and deliberately leaves the VM
alone (BUG-012-A2, [guest-config-changes.md](guest-config-changes.md)), and
cloud-init does not re-run first-boot logic on a guest that has already booted.
So a re-run mints a fresh token, writes it to the node, and the guest never
reads it. The workflow goes green and nothing has changed.

### Option A — onboard the running guest by hand

Right when the guest matters and a rebuild is expensive. Mint a token the same
way the runner does:

```bash
TENANT=...; SP_ID=...; SP_SECRET=...
TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/$TENANT/oauth2/v2.0/token" \
  -d grant_type=client_credentials \
  -d client_id="$SP_ID" \
  -d client_secret="$SP_SECRET" \
  -d scope='https://management.azure.com/.default' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Then, in the guest, via a config file rather than an argument — a process
argument is readable by anything that can enumerate processes, which is the
whole of SEC-001a:

```bash
umask 077
printf '{"access-token":"%s"}\n' "$TOKEN" > /tmp/arc-connect.json
azcmagent connect --config /tmp/arc-connect.json \
  --tenant-id "$TENANT" \
  --subscription-id "$SUBSCRIPTION" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --resource-name "$NAME"
rm -f /tmp/arc-connect.json
```

`$NAME` must be the name Terraform will use for cleanup, or a later destroy will
delete nothing and leave an orphan. Read it out of state rather than assuming it
is the VM name — the override in `arc.resource_name` exists precisely so the two
need not match (BUG-019):

```bash
terraform state show 'terraform_data.arc_registration["<vm-name>"]'
```

For `AzureUSGovernment` and `AzureChinaCloud` the login host and scope differ;
the table is in `.github/scripts/mint_arc_token.py`, and it is closed on purpose
— an unknown cloud is a configuration error, not a reason to mint a public-cloud
token against a government subscription.

### Option B — rebuild the guest

Right when the guest holds nothing and first boot is cheap. Deliberate, and
destructive:

```bash
terraform apply -replace='proxmox_virtual_environment_vm.vm["<vm-name>"]'
```

**Read the plan.** This destroys and recreates the VM, and everything in it goes.
The replace is also what gets a *changed* first-boot configuration into an
existing guest, which is the same mechanism for a different reason.

## After recovery

1. **Confirm in Azure**, not in the guest's log:

   ```bash
   az resource show -g "$RESOURCE_GROUP" -n "$NAME" \
     --resource-type Microsoft.HybridCompute/machines -o table
   ```
2. **Confirm the name matches `terraform_data.arc_registration`**, or the next
   destroy leaves an orphan.
3. If the cause was stage 1, **check every other Arc-enabled VM**. A missing
   service principal is not a per-guest fault: every VM built while the secret
   was absent skipped onboarding, silently and identically.

## What this does not cover

- **An Arc machine left in Azure after its VM is gone** —
  [arc-cleanup.md](arc-cleanup.md).
- **A VM Proxmox has and Terraform does not** —
  [incident-orphan-vm.md](incident-orphan-vm.md).
- **Anything Arc does after onboarding** — policy, monitoring, update
  management. FEAT-005, FEAT-006 and FEAT-008 are those, and none has been built.
- **A check that would have caught this automatically.** There is none. Nothing
  in the pipeline verifies that an Arc-enabled VM reached Azure, which is why
  this file starts with "the apply succeeds, nothing is red". KAN-017 (#22) is
  the issue for post-apply verification, and it is open.

## Not verified

**None of the recovery paths has been executed.** Unlike
[state-recovery.md](state-recovery.md), which records a drill that was actually
run, this file is written from the code that produces the failures — the mint
script's exit paths, both templates' logging, and the arc-cleanup policy — not
from an incident. The commands are the ones the pipeline itself uses, with the
argument shapes taken from the same source, but the first person to follow them
against a real failed onboarding is testing them.
