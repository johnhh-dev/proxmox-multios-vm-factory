# The Proxmox API token — which identity, and how to read a 401

Why this file exists: on 2026-08-28, three consecutive applies on `main` failed
for three unrelated reasons. The third, run
[33154660339](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/actions/runs/33154660339),
stopped at the preflight check with

```
https://192.168.10.25:8006 returned HTTP 401 for /cluster/status.
The apply would talk to the same endpoint with the same token.
```

The token had been re-entered under `root@pam` after CHORE-007 renamed the
repository secrets. The identity this project actually uses is `terraform@pve`.
Nothing in the repository said so, and finding out took about an hour of
bisecting curl output. This is that hour, written down.

## The identity

| | |
|---|---|
| **User** | `terraform@pve` — realm `pve`, **not** `root@pam` |
| **Token ID** | `gha` |
| **Privilege separation** | off (`privsep 0`) — the token inherits the user's privileges |
| **Privileges today** | `Administrator` on `/`, propagating |

The full token string is `terraform@pve!gha=<uuid>`, and that whole string —
user, realm, token ID, uuid — is what goes into the secret.

`Administrator` on `/` is more than the module needs. Narrowing it to clone,
configure, destroy and snippet-write is SEC-006-A4 ([#55](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/55)).
Whoever does that should update this table in the same PR.

## Transport: `insecure = true` (SEC-006-A1)

Certificate validation for the API is **off**, and has been since the beginning.
It is now `var.proxmox_tls_insecure` rather than a literal, still defaulting to
`true` — the default is what the lab needs, not an endorsement.

What it costs while it stays on: a machine on the lab network that can answer
for `192.168.10.25:8006` receives the API token, and nothing about the
connection looks wrong from this side. The token is the one described above,
with `Administrator` on `/`.

To turn it off, in order:

1. **Trust the cluster CA.** Pinning the node's own certificate also works —
   an earlier version of this step said it did not, and that was tested with the
   wrong tool. Both are shown, because the difference between them is about
   renewal rather than about whether they work.

   The node's certificate is issued by the PVE cluster CA, not self-signed:

   ```text
   subject = OU=PVE Cluster Node, O=Proxmox Virtual Environment, CN=pve.local
   issuer  = CN=Proxmox Virtual Environment, OU=3f41fbd7-..., O=PVE Cluster Manager CA
   ```

   `openssl verify` refuses the leaf as an anchor, which is what the earlier
   version of this step reasoned from:

   ```console
   $ openssl verify -CAfile /etc/pve/local/pve-ssl.pem /etc/pve/local/pve-ssl.pem
   error 20 at 0 depth lookup: unable to get local issuer certificate

   $ openssl verify -CAfile /etc/pve/pve-root-ca.pem /etc/pve/local/pve-ssl.pem
   OK
   ```

   **But that answers a different question from the one step 2 asks.** TLS
   verification treats a certificate supplied as a trust anchor as one, even
   when it is not a CA. Measured against the endpoint, with a control to prove
   the flag is honoured at all:

   ```console
   $ curl --cacert <an unrelated CA>          https://192.168.10.25:8006/...
   curl: (60) SSL certificate problem: unable to get local issuer certificate

   $ curl --cacert /etc/pve/pve-root-ca.pem   https://192.168.10.25:8006/...
   curl: (22) The requested URL returned error: 401

   $ curl --cacert <the leaf>                 https://192.168.10.25:8006/...
   curl: (22) The requested URL returned error: 401
   ```

   A 401 means the handshake succeeded and only the credentials were missing.
   The unrelated CA fails at TLS, so the flag is doing its job — and both the CA
   and the leaf are accepted. OpenSSL 3.5.5.

   **What has not been measured is Terraform.** Its TLS stack is Go's, not
   OpenSSL's, and whether Go accepts a non-CA leaf as an anchor is a separate
   question nobody here has answered. That is one more reason to take the CA.

   Take the CA:

   ```bash
   # on the node
   cat /etc/pve/pve-root-ca.pem > pve-cluster-ca.crt

   # on gha-runner-01
   sudo cp pve-cluster-ca.crt /usr/local/share/ca-certificates/pve-cluster-ca.crt
   sudo update-ca-certificates
   ```

   **The reason to prefer it is renewal**, which the note at the end of this
   section worries about. The node's certificate expires **2028-05-09**; the CA
   runs to **2036-05-01**. Pinning the leaf means redoing this in two years,
   with every apply failing at once as the symptom — which is a good reason to
   take the CA and not the same thing as the leaf being broken.

   A certificate from a CA the runner already trusts is still better if one is
   available, because it survives the cluster being rebuilt - the cluster CA
   does not.

2. **Check it from the runner before changing anything:**

   ```bash
   curl --fail https://192.168.10.25:8006/api2/json/version   # no -k
   ```

   That command failing is the whole point — if it fails, Terraform would fail
   too, and finding out here costs nothing.

   **The name will not be what fails.** The certificate's CN is `pve.local`, but
   its subject alternative names cover the endpoint as configured — measured
   2026-08-30:

   ```text
   IP Address:127.0.0.1, IP Address:0:0:0:0:0:0:0:1, DNS:localhost,
   IP Address:192.168.10.25, DNS:pve, DNS:pve.local
   ```

   So `var.proxmox_endpoint` does not need to change. That was worth checking
   rather than assuming either way.

3. **Set the repository variable** `TF_VAR_proxmox_tls_insecure` to `false`.
   No code change and no PR, which is the reason the literal became a variable.

   **This step did not work until KAN-012.** Repository variables live in the
   `vars` expression context and are not environment variables; nothing passed
   them to Terraform. Setting it produced a green apply with validation still
   off — the worst shape a security control can fail in, because the operator
   has evidence it worked. `terraform-env` passes `vars` now, and
   `render_terraform_env.py` carries the allowlist that this variable is on.
   The job log lists what it picked up:

   ```text
   from repository variables: TF_VAR_proxmox_tls_insecure
   ```

   If that line does not name the variable, it did not take effect.

4. **Run a plan.** A TLS failure surfaces as a provider error naming the
   endpoint, not as a resource diff.

If the **CA** is ever replaced — a cluster rebuild — step 1 has to happen again,
and the symptom will be every apply failing at once. A reissued *node*
certificate does not, which is the point of pinning the CA rather than the leaf.

## Least privilege (SEC-006-A2 and A4)

`Administrator` on `/` is what the token has and far more than the module uses.
A role scoped to what it actually does, as a starting point:

```bash
pveum role add TerraformFactory --privs   "VM.Allocate,VM.Clone,VM.Config.Disk,VM.Config.CPU,VM.Config.Memory,VM.Config.Network,VM.Config.Options,VM.Config.Cloudinit,VM.PowerMgmt,VM.Audit,VM.Monitor,Datastore.Allocate,Datastore.AllocateSpace,Datastore.Audit,Sys.Audit"

pveum acl modify / --user terraform@pve --role TerraformFactory
```

Where each comes from:

| Privilege | Used by |
|---|---|
| `VM.Clone`, `VM.Allocate` | `clone` in main.tf |
| `VM.Config.*` | cores, memory, network device, disk (FEAT-009), cloud-init |
| `VM.PowerMgmt` | the destroy path and the shutdown timeouts |
| `VM.Audit`, `VM.Monitor` | reading state back, and the guest agent |
| `Datastore.Allocate*` | the cloud-init disk and the VM disks |
| `Datastore.Audit` | the snippet upload |
| `Sys.Audit` | the cluster preflight (`/cluster/status`) |

**The current state is measured; the proposal is not.** Confirmed on the node
2026-08-30: `terraform@pve` holds `Administrator` on `/` with `propagate=1`, and
no `TerraformFactory` role exists. The identity table above is accurate, and
everything below this paragraph is still a proposal.

**This list is derived from what the configuration does, not from a run that
proved it sufficient.** SEC-006-A5 is the verification: clone, snippet upload,
provision and destroy under the restricted identity, all four. Expect to add a
privilege or two — do it by reading the 403, which names the path and the
missing privilege, rather than by widening back to `Administrator`.

Snippet upload happens over **SSH**, not the API, so no API privilege covers it.
Nothing above reaches that connection. It is the next section.

## The node SSH identity (SEC-006-A3)

This is a *second* identity, and the least-privilege work above does nothing for
it. The provider uses it to write cloud-init snippets to
`/var/lib/vz/snippets/` on the node.

There are three ways to supply it now, where there used to be one. Terraform
refuses a plan with none of them, naming all three — the check is in
`locals.tf`, and it is a plan-time refusal rather than a provider error because
**snippet upload happens after the clone**: an apply with no usable identity
would otherwise build a VM and then discover it has nowhere to put that VM's
configuration, which is the orphan shape from
[incident-orphan-vm.md](incident-orphan-vm.md).

| Setting | Credential in Terraform's inputs? | Credential on the runner? |
|---|---|---|
| `proxmox_ssh_password` | yes — in the `variables` block of any plan JSON | yes |
| `proxmox_ssh_private_key` | yes — same block | yes |
| `proxmox_ssh_agent = true` | **no** | yes, in an agent |

That middle column is the point, and it is easy to under-read. `terraform show
-json` emits every input variable in cleartext (SEC-002) — which is why both
workflows `rm -f tfplan.json`. Moving from the password to a key changes *which*
secret is in that block. Moving to the agent removes one of the five entirely.

### Answering the question A3 actually asks

> confirm whether the provider's SSH agent mode removes the need for a stored
> credential entirely

**Yes for Terraform, no for the runner.** With `proxmox_ssh_agent = true`
neither a password nor a key is a Terraform input, so neither is a repository
secret, neither appears in plan or state JSON, and neither has a rotation
procedure to forget. The key still exists on `gha-runner-01`, loaded into an
agent by something outside this repository — which is ADR 0001 §5's accepted
Path 1 and [runner-trust-boundary.md](runner-trust-boundary.md)'s subject, not a
new exposure.

### Moving off the root password

In order, and each step is checkable before the next:

1. **Create a non-root account on the node** with write access to the snippets
   datastore. `root` is the current identity purely because it always was.

   ```bash
   # on the Proxmox node
   useradd -m -s /bin/bash terraform-snippets
   install -d -o terraform-snippets -g root -m 0755 /var/lib/vz/snippets
   ```

2. **Issue it a key**, on the runner, and authorise it:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/proxmox-snippets -C 'terraform snippet upload'
   ssh-copy-id -i ~/.ssh/proxmox-snippets.pub terraform-snippets@192.168.10.25
   ```

3. **Prove it works before Terraform depends on it.** This failing here costs
   nothing; the same failure inside an apply costs a half-built VM:

   ```bash
   ssh -i ~/.ssh/proxmox-snippets terraform-snippets@192.168.10.25      'touch /var/lib/vz/snippets/.write-test && rm /var/lib/vz/snippets/.write-test'
   ```

4. **Then switch.** Either set `TF_VAR_PROXMOX_SSH_USERNAME` and
   `TF_VAR_PROXMOX_SSH_PRIVATE_KEY` as repository secrets, or — preferred —
   load the key into the runner's agent and set
   `TF_VAR_proxmox_ssh_agent=true` as a repository *variable*, which stores no
   credential anywhere GitHub can see.

5. **Delete the root password secret.** It is optional now; leaving it in place
   means the S2 finding is still one variable away from being live. An empty
   secret is normalised to null and is not an error — see below.

### The empty-string trap

An unset GitHub secret arrives as `TF_VAR_...=""`, which is an empty *string*
and not null. The provider rejects that outright:

```text
Error: expected "ssh.0.password" to not be an empty string, got
```

So a lab that moved to a key and left the password secret in place, empty, would
have failed every plan with a message about a field it deliberately was not
using. `locals.tf` normalises empty to null once, and both the provider block
and the validation rule read that same local — so the connection Terraform makes
and the configuration the rule approves cannot disagree.

This was found by the test suite, not predicted.

## Where the value travels

`TF_VAR_PROXMOX_API_TOKEN` (repository secret)
→ [`render_terraform_env.py`](../.github/scripts/render_terraform_env.py) maps it to `TF_VAR_proxmox_api_token`
→ [`preflight_cluster.py`](../.github/scripts/preflight_cluster.py) reads it for `/cluster/status`
→ the `proxmox` provider reads it as `var.proxmox_api_token` ([`providers.tf`](../providers.tf))

Two consequences worth knowing:

**The secret holds no `PVEAPIToken=` prefix.** The preflight script builds the
header itself as `"Authorization: PVEAPIToken=" + token`. A value that already
carries the prefix produces `PVEAPIToken=PVEAPIToken=…`, and Proxmox then reads
everything up to the `!` as a username.

**One secret name, no fallbacks.** BUG-003-A4 removed the legacy `PX_API_TOKEN`
alias deliberately — see [`runner-trust-boundary.md`](runner-trust-boundary.md).
A token stored under any other name is a secret nothing reads.

## Reading a 401

Proxmox puts the reason in the HTTP status line, and the reason distinguishes
failures that look identical from the workflow log. These were confirmed against
this cluster on 2026-08-28:

| Status line | What it means |
|---|---|
| `401 No ticket` | No `Authorization` header arrived at all — the header never left your shell, or the secret is empty |
| `401 no tokenid specified` | A header arrived but the value has no `!tokenid` part |
| `401 no such user ('…')` | The `user@realm` part does not exist — often a doubled `PVEAPIToken=` prefix |
| `401 no such token 'x' for user 'y'` | The user exists, that token ID does not — the wrong realm, or the token was deleted |

`no such token 'gha' for user 'root@pam'` is the exact signature of the
2026-08-28 incident: right token ID, right uuid, wrong realm.

Note that a bare `curl` shows none of this. Proxmox answers a 401 with an **empty
body**, and curl exits 0 on HTTP errors unless you pass `-f`. Without `-i` the
command looks like it printed nothing at all.

## Checking a token from a workstation

```powershell
$tok = Read-Host 'Proxmox token (user@realm!tokenid=uuid)'
curl.exe -k -sS -i --max-time 15 -H "Authorization: PVEAPIToken=$tok" https://192.168.10.25:8006/api2/json/version
```

`Read-Host` keeps the credential off the command line, so it does not land in
PSReadLine history. Use `curl.exe` with the extension — in Windows PowerShell
`curl` is an alias for `Invoke-WebRequest`, where `-k` and `-H` mean something
else entirely. Keep the double quotes: the header must reach curl as **one**
argument, or curl reads the tail as a second URL and reports
`URL rejected: Bad hostname` while sending no credential at all.

## Rotating the token

The uuid cannot be read back from the API — it is displayed once, at creation.
Proxmox does keep it in `/etc/pve/priv/token.cfg`, but treat that as a recovery
path of last resort rather than the procedure.

```bash
pveum user token remove terraform@pve gha
pveum user token add terraform@pve gha --privsep 0
```

`--privsep 0` matters: with privilege separation on, a fresh token starts with
**no** permissions regardless of what `terraform@pve` holds, and the failure
surfaces as HTTP 500 on clone or destroy rather than as a 401 anyone would
recognise as an auth problem.

The ACL lives on the user, not on the token, so `Administrator` survives the
rotation and does not need to be re-granted.

Then update the secret and confirm:

```powershell
gh secret set TF_VAR_PROXMOX_API_TOKEN --body "terraform@pve!gha=<uuid>"
```

Rotation invalidates the old token immediately. GitHub Actions is the only
consumer in this repository; anything else holding `terraform@pve!gha` breaks at
the same moment.

## What the preflight check does with it

[`preflight_cluster.py`](../.github/scripts/preflight_cluster.py) runs before
every apply and destroy ([`terraform-apply.yml`](../.github/workflows/terraform-apply.yml),
[`terraform-destroy.yml`](../.github/workflows/terraform-destroy.yml)) and reads
`/cluster/status` once. That single call answers two questions in the order they
can fail: whether the token authenticates, and whether the cluster has quorum
([`proxmox-cluster-quorum.md`](proxmox-cluster-quorum.md)).

A token failure therefore hides a quorum failure behind it. After fixing a 401,
expect the quorum gate to be the next thing evaluated — on 2026-08-28 both were
live at once, an hour apart.
