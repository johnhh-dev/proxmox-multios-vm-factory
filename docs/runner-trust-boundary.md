# The `gha-runner-01` trust boundary

Written for SEC-004 (#36). Records what the lab runner holds, what may execute
on it, who can cause that to happen, and what must be true of the machine
itself before the workflows will run.

Read this before changing a workflow trigger, adding a step that runs
repository content, or adding a secret.

## What the runner is

`gha-runner-01` is a persistent, self-hosted GitHub Actions runner on the lab
network. It is not ephemeral: the workspace, the installed tooling and the
Terraform state directory all survive between jobs, so anything a job leaves
behind is available to the next one.

It has network reach to the Proxmox hypervisor and outbound reach to Azure.

## What it holds

Every one of these is a repository-level secret, which means it is readable by
**any** workflow in this repository, not only by `terraform-apply`:

| Secret | What it grants |
|---|---|
| `TF_VAR_PROXMOX_API_TOKEN` | Proxmox API — create, modify and destroy VMs |
| `TF_VAR_PROXMOX_SSH_USERNAME` / `TF_VAR_PROXMOX_SSH_PASSWORD` | SSH to the Proxmox node, used by the provider for snippet upload |
| `TF_VAR_ARC_SP_ID` / `TF_VAR_ARC_SP_SECRET` | Azure service principal for Arc onboarding, scoped to `rg-arc-home-lab`. Runner-side only since SEC-001a — see below |
| `TF_VAR_ARC_TENANT_ID` / `TF_VAR_ARC_SUBSCRIPTION_ID` | Azure tenant and subscription identifiers |
| `TF_VAR_LINUX_VM_PASSWORD` | Linux guest `ubuntu` password |
| `TF_VAR_WINDOWS_ADMIN_PASSWORD` | Windows guest Administrator password |
| `TF_VAR_SSH_PUBLIC_KEY` | Public half only; not a secret, stored as one |

Two of those names are no longer Terraform variables at all. SEC-001a stopped
handing the Arc service-principal secret to a guest: the root module declares
neither `arc_sp_id` nor `arc_sp_secret`, and what reaches the snippet is a
short-lived token minted per run by
[`.github/actions/arc-token`](../.github/actions/arc-token/action.yml). The
service principal stays here, on the runner, where that action and
[`arc-cleanup`](../.github/actions/arc-cleanup/action.yml) both read it — which
is precisely the boundary this document draws. The `TF_VAR_` prefix on the two
names is now historical: Terraform ignores an environment variable it has no
declaration for, and one naming scheme beat inventing a second for two values.

Every other name is the Terraform variable, uppercased, and it is the only name
read for that variable. BUG-003-A4 removed the legacy aliases — `PX_API_TOKEN`,
`PX_SSH_USER`, `PX_SSH_PASS`, `SSH_PUBKEY`, `TF_VAR_VM_PASSWORD` and
`VM_PASSWORD`. A secret still stored under one of those names is a secret
nothing reads, and it should be deleted rather than left as a credential with no
consumer.

Beyond the secrets, the machine holds the Terraform state file at
`/opt/terraform-state/proxmox-ubuntu-vm-factory/terraform.tfstate`. State is
unencrypted on disk and, until SEC-001 (#46) is resolved, contains the rendered
cloud-init snippet with both guest passwords and the Arc secret in cleartext.
**Filesystem read access to this runner is equivalent to holding the
credentials**, whether or not a job is running.

### The `prod` environment protects nothing today

`terraform-apply` and `terraform-destroy` declare `environment: prod`. That
environment currently has **no protection rules, no required reviewers and no
environment-scoped secrets** — every secret above is repository-scoped. The
declaration therefore adds no gate and no isolation. Moving the lab credentials
into the environment and adding required reviewers is the structural fix.

Re-verified 2026-08-24 for CHORE-003-A3, against the API rather than by reading
the settings page:

```
$ gh api repos/:owner/:repo/environments/prod --jq '{protection_rules, can_admins_bypass}'
{"can_admins_bypass":true,"protection_rules":[]}
```

`protection_rules` is empty. No reviewer is required, no wait timer applies, and
no branch policy restricts which ref may deploy. **A push to `main` reaches the
hypervisor with no human approval step**, and the `environment: prod` line in
both workflows buys nothing but a label in the run summary.

This cannot be fixed from the repository — required reviewers are an environment
setting, behind the same paid-plan limitation that blocks branch protection (see
"Merging is review" below). It is recorded here because CHORE-003-A3 asks for
the setting to be confirmed and written down, and the honest answer is that it
is absent. The queue discipline CHORE-003-A1 adds is a serialisation guarantee,
not an approval gate; do not read it as one.

## What may execute on it

`terraform plan` is not a read-only operation with respect to code. It
evaluates `locals.tf` and renders both cloud-init templates through
`templatefile`. Repository content is *interpreted* during a plan, so "we only
plan on PRs" is not a containment argument.

The rule is therefore: **only code that has already passed review by someone
with write access may execute on `gha-runner-01`.**

| Trigger | Runner | Gate |
|---|---|---|
| `push` to `main` (apply) | `gha-runner-01` | Reachable only by merging, which is review |
| `workflow_dispatch` (destroy) | `gha-runner-01` | Needs write access, plus the typed `DESTROY` confirmation (SEC-005) |
| `pull_request` from a branch in this repository (`plan`) | `gha-runner-01` | Pushing the branch needs write access |
| `pull_request` from a fork (`fork-validate`) | `ubuntu-latest` | No secrets, no lab reach, `-backend=false` |
| `pull_request` / `push` (`checks`) | `ubuntu-latest` | No secrets |

The split is enforced in `terraform-plan.yml` by a job-level condition on
`github.event.pull_request.head.repo.full_name == github.repository`.

### "Merging is review" is a convention here, not a gate

The first row of that table leans on merge being a reviewed step. Nothing
enforces that. `checks.yml` exists to be the required status check on `main`
(CHORE-002-A6, #44) and it **cannot be set**: branch protection and repository
rulesets are both gated behind a paid plan for private repositories.

Verified 2026-08-24 with an owner token:

```text
GET /repos/johnhh-dev/proxmox-multios-vm-factory-v2/branches/main/protection  -> 403
GET /repos/johnhh-dev/proxmox-multios-vm-factory-v2/rulesets                  -> 403
"Upgrade to GitHub Pro or make this repository public to enable this feature."
```

This is the account plan, not a token scope: no API call and no setting in the
web UI will do it while the repository is private on a free plan.

So a commit can reach `main` - and therefore `terraform-apply` on
`gha-runner-01`, which triggers on push to `main` - without `checks` ever having
been green for it. The gate runs on every pull request and is read by a human;
it is not machine-enforced at the merge boundary. Treat a red `checks` run as
blocking by hand.

Three ways out, in the order they cost:

1. **Gate the apply instead of the merge.** A first step in
   `terraform-apply.yml` that looks up the check runs for the pushed commit and
   refuses to run unless `checks` succeeded. It protects the lab rather than the
   branch, needs `checks: read`, and works on any plan.
2. **GitHub Pro** on the account that owns the repository. Then A6 is one call:

   ```bash
   echo '{
     "required_status_checks": {"strict": false, "contexts": ["checks"]},
     "enforce_admins": false,
     "required_pull_request_reviews": null,
     "restrictions": null
   }' | gh api -X PUT --input - \
     repos/johnhh-dev/proxmox-multios-vm-factory-v2/branches/main/protection
   ```

3. **Make the repository public.** Read "If the repository is ever made public"
   below before treating this as a route to A6 - that decision is about the
   secrets in past job logs and state, not about branch protection.

CHORE-002-A6 stays open until one of them is done.

## Who can trigger it

The repository is **private** (verified 2026-08-22). A fork requires read
access, which requires an invitation, so today there is no path for an
anonymous outsider to reach any job. The exposure SEC-004 closes is *latent*,
not open: it would become live the moment the repository is made public or an
outside collaborator is added.

The *Fork pull request workflows → Require approval* setting does **not apply
here**: GitHub rejects it for private repositories, because a private
repository cannot receive a fork PR from someone without read access in the
first place. Verified 2026-08-22 — the API returns *"Fork PR approval is not
allowed for private repositories"*. The `fork-validate` job in
`terraform-plan.yml` is therefore dormant today; it exists so that the day the
repository is opened up, fork PRs already have somewhere safe to land instead
of falling through to the lab runner.

Checked at the same time: **Settings → Actions → General → Workflow
permissions** is already *Read repository contents*, and *Allow GitHub Actions
to create and approve pull requests* is off. The explicit `permissions:` blocks
added in SEC-004-A3 make each workflow state its own scope rather than inherit
that default, so a later change to the repository setting cannot silently widen
them.

One setting still needs a human check: **Settings → Actions → Runners** — the
`gha-runner-01` runner group must not be shared with other repositories. A
shared group means another repository's workflows can schedule onto this host.

### If the repository is ever made public

Revisit this document **before** the visibility change, not after. At minimum:
enable fork-PR approval (it becomes available), confirm `fork-validate`
actually catches fork PRs, and treat every secret in the table above as
exposed until rotated — the state file and past job logs predate these gates.

## What the runner must provide

The workflows no longer install or create anything with `sudo` (SEC-004-A4).
The runner account previously had passwordless `sudo` for the sake of one
`mkdir`, which handed a root escalation to every step on the box, including any
step running repository content. Provision the following once, out of band:

**The runner is a guest of the hypervisor it holds credentials for.** Measured
2026-08-30: `gha-runner-01` is VM **1110** on `pve`, running with `onboot=1`, at
192.168.10.34. So this host is not beside the lab, it is in it — the API token
here is `Administrator` on `/` and could destroy the machine using it.

`var.protected_vm_ids` refuses a plan that declares 1110, which is the cheap
half. The other half — whether the runner should be on this node at all — is
decided in [ADR 0005](adr/0005-runner-location.md): **it stays, and the state
leaves instead.** The reasoning in one line is that every consequence OPS-003
lists follows from the runner *holding the state*, not from its *being a guest*,
and only one of those two is affordable to change today.

That leaves the deny-list holding the lifecycle cycle on its own, which is a
repository guard and not a hypervisor one. SEC-006-A2's restricted role is what
would hold it in the right place.

**And the failure domain is smaller than that first read suggested.** Measured
2026-08-30, after the claim above was written: this is a two-node cluster
(`homelab`: `pve` and `pve2`), and **VM 1110 replicates to `pve2`** — along with
every other hand-built guest. So a replica of the runner's disk, and therefore
of the Terraform state on it, exists on a second machine.

Three qualifications, because that is a mitigation rather than a solution:

- **Replication is asynchronous, and the window is 15 minutes.** Every job runs
  on `*/15` (`pvesr list`, measured 2026-08-30), so the replica of the runner's
  disk - and therefore of the Terraform state on it - can be a quarter of an
  hour old. An apply that finishes and is followed by a node failure inside that
  window leaves a replica describing a lab that no longer matches it. All seven
  jobs report `State OK` and `FailCount 0`, so the schedule is being kept.
- **Nothing in this repository put it there or checks it.** It is infrastructure
  configured outside the factory, and it can be removed without anything here
  noticing.
- **The cluster has no qdevice.** Expected votes 2, total votes 2 — so losing
  either node makes the survivor inquorate and `/etc/pve` read-only, which is
  the failure BUG-024 already caught once. A replica on a node that cannot form
  a quorum is not immediately a recovery.

[ADR 0004](adr/0004-terraform-state.md) rejected self-hosted S3 for storing
"state describing the lab, in the lab". That reasoning still holds — replication
narrows the window, it does not leave the failure domain.

1. `/opt/terraform-state/proxmox-ubuntu-vm-factory` — created and owned by the
   runner account, mode `0700`. The workflows assert it exists and is writable
   and fail with a pointer here if not.

   Its `backups/` subdirectory is **not** provisioned here: the backup step
   creates it `0700` on first use (FEAT-001-A3). It holds up to twenty copies
   of the state file, each `0600`, and each carrying the same cleartext
   credentials the state does — so it is inside this trust boundary and inside
   SEC-001e's purge, not beside them.
2. The Azure CLI (`az`) on `PATH`. The apply workflow used to pipe
   `https://aka.ms/InstallAzureCLIDeb` into `sudo bash` mid-job, on the host
   holding the credentials, in a job that had those credentials in its
   environment. It now asserts `az` is present and fails if it is not.
3. `python3` and `terraform` are supplied by the runner image and by
   `setup-terraform` respectively; no action needed.

The runner account should not have passwordless `sudo` at all once the above is
in place. Removing it is a host change and is not verified by CI.

## Review

- **Owner:** `@sec`
- **Reviewed:** pending — this document is the artefact SEC-004-A5 asks for and
  needs `@sec` sign-off before #36 closes.
