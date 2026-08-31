# Operator setup and secrets reference

Everything this lab needs before a run can succeed, and what breaks when a piece
is missing. Written for DOC-002 (#60), because the alternative was reading three
workflows and a Python script to find out which thirteen secrets exist.

Nothing here contains a credential value. Where a value has to be obtained, this
says where from.

## 1. GitHub secrets

Thirteen, and **one secret name per variable — no aliases and no fallback
chain.** Each name is the Terraform variable name, uppercased and prefixed
`TF_VAR_`. The table below is generated from the same list the workflows use, in
[`render_terraform_env.py`](../.github/scripts/render_terraform_env.py); if the two
ever disagree, that file is right and this one is stale.

| Secret | Terraform variable | Required | What it is |
|---|---|---|---|
| `TF_VAR_PROXMOX_API_TOKEN` | `proxmox_api_token` | **yes** | Proxmox API token, `user@realm!tokenid=uuid`. See [proxmox-api-token.md](proxmox-api-token.md) |
| `TF_VAR_PROXMOX_SSH_PASSWORD` | `proxmox_ssh_password` | one of three | Password for the node SSH account. See below |
| `TF_VAR_PROXMOX_SSH_PRIVATE_KEY` | `proxmox_ssh_private_key` | one of three | PEM key for the same connection, instead of the password |
| `TF_VAR_SSH_PUBLIC_KEY` | `ssh_public_key` | **yes** | Public key installed for the `ubuntu` account on Linux guests |
| `TF_VAR_PROXMOX_SSH_USERNAME` | `proxmox_ssh_username` | no | Defaults to `root` in `variables.tf` |
| `TF_VAR_LINUX_VM_PASSWORD` | `linux_vm_password` | no | Console password for `ubuntu`, delivered as cipassword (SEC-001b) |
| `TF_VAR_WINDOWS_ADMIN_PASSWORD` | `windows_admin_password` | if any VM is Windows | Windows Administrator password. Refused at plan time if a Windows VM is declared without it (SEC-001c) — first boot would otherwise throw and abandon the rename, RDP and Arc |
| `TF_VAR_ARC_TENANT_ID` | `arc_tenant_id` | no | Entra tenant |
| `TF_VAR_ARC_SUBSCRIPTION_ID` | `arc_subscription_id` | no | Azure subscription |
| `TF_VAR_ARC_RESOURCE_GROUP` | `arc_resource_group` | no | Resource group holding the Arc machines |
| `TF_VAR_ARC_LOCATION` | `arc_location` | no | Azure region for the Arc machines |
| `TF_VAR_ARC_CLOUD` | `arc_cloud` | no | Defaults to `AzureCloud` |
| `TF_VAR_ARC_SP_ID` | *(not a Terraform input)* | no | Service principal app id |
| `TF_VAR_ARC_SP_SECRET` | *(not a Terraform input)* | no | Service principal secret |

The last two are named `TF_VAR_*` but the root module declares neither. Since
SEC-001a the guest receives a short-lived minted token rather than the service
principal, so Terraform has no use for them — but two runner-side consumers do:
`.github/actions/arc-token` mints the token with them, and
`.github/actions/arc-cleanup` calls `az` with them. Terraform silently ignores a
`TF_VAR_` variable it has no declaration for, so the names were kept rather than
introducing a second naming scheme for two values.

### What fails, and where

| Situation | Where it fails |
|---|---|
| A **required** secret is missing | `terraform-env` step, exit 1, naming the secret. Before Terraform runs. |
| An **optional** secret is missing | Nothing fails. The step logs the name; the variable is left unset. |
| The Arc service principal is missing | `arc-token` logs *"no service principal configured, so no token is minted"* and the guest skips Arc onboarding |
| `TF_VAR_LINUX_VM_PASSWORD` unset **and** `linux_password_auth = true` | `terraform plan` — refused by a validation rule (SEC-007) |
| `TF_VAR_WINDOWS_ADMIN_PASSWORD` unset, Windows VM in inventory | **Guest first boot.** The script throws on an empty password. Not caught in CI. |

That last row is the one to know about: an unset optional secret is not an error
here, and for Windows the consequence arrives on the guest rather than in the
pipeline.

**The node SSH identity is "one of three" (SEC-006-A3).** The provider uploads
cloud-init snippets over SSH, not through the API, and that connection needs a
password, a private key, or the runner's SSH agent. Terraform refuses a plan
with none of them and names all three in the error. An empty secret counts as
absent, not as a blank password.

Prefer the agent: with `TF_VAR_proxmox_ssh_agent=true` neither credential is a
Terraform input at all, so neither lands in the cleartext `variables` block of a
plan JSON (SEC-002) and neither is a repository secret to rotate.
[proxmox-api-token.md](proxmox-api-token.md) has the migration, in order.

## 2. Repository variables

None of these is a secret, and **before KAN-012 none of them reached Terraform
either.** GitHub repository variables live in the `vars` expression context;
they are not environment variables. Nothing referenced `vars` except
`TF_BOOTSTRAP`, which needs its own explicit `env:` mapping — and that mapping
is the proof. So an operator following the SEC-006-A1 procedure would have set
`TF_VAR_proxmox_tls_insecure=false`, seen a green apply, and believed
certificate validation was on while it was still off.

The `terraform-env` action now passes `vars` alongside `secrets`, and
`render_terraform_env.py` carries an allowlist of which ones Terraform may see.
**A variable outside that list is ignored**, because `vars` is writable by
anyone with repository admin and is not reviewed the way code is — an
unfiltered pass-through would make every Terraform input remotely settable. No
secret is in the list, and a test enforces that the two tables stay disjoint.

**An unset or blank variable is omitted, not blanked.** `variables.tf` holds the
default, and emitting `""` would override that default with an empty string
rather than fall back to it — the same trap SEC-006-A3 hit from the provider's
side.

| Variable | Purpose |
|---|---|
| `TF_BOOTSTRAP` | Authorises the first apply into an empty state. See §6. Not a `TF_VAR_`; read directly by the guard |

### Connection

| Variable | Default in `variables.tf` |
|---|---|
| `TF_VAR_proxmox_endpoint` | `https://192.168.10.25:8006` |
| `TF_VAR_proxmox_node_name` | `pve` |
| `TF_VAR_proxmox_ssh_nodes` | `{ pve = "192.168.10.25", pve2 = "192.168.10.26" }` — every node a VM might be placed on. Changing `proxmox_node_name` alone is enough because of this (OPS-005) |
| `TF_VAR_proxmox_ssh_port` | `22` |
| `TF_VAR_proxmox_tls_insecure` | `true` — set `false` once a trusted certificate exists (SEC-006-A1) |
| `TF_VAR_proxmox_ssh_agent` | `false` — `true` stores no credential at all (SEC-006-A3) |
| `TF_VAR_proxmox_ssh_agent_socket` | unset — uses `$SSH_AUTH_SOCK` |

### Where things live on the node

| Variable | Default |
|---|---|
| `TF_VAR_snippets_datastore` | `local` |
| `TF_VAR_vm_datastore_id` | `zfs-vmstore` |
| `TF_VAR_template_vmid_linux` / `_windows` | `9900` / `9917` |
| `TF_VAR_template_disk_gb_linux` / `_windows` | `50` / `100` — measured from `qm config` 2026-08-30. Re-measure if a template is rebuilt (FEAT-009) |

### Network and DNS

| Variable | Default |
|---|---|
| `TF_VAR_bridge` | `vmbr0` |
| `TF_VAR_dns_server` | `192.168.10.2` |
| `TF_VAR_dns_servers_fallback` | `["192.168.10.1"]` — HCL list syntax |
| `TF_VAR_search_domain` | `home` |
| `TF_VAR_network_probe_host` | `aka.ms` — the name a guest resolves to decide its network is ready, one definition for both guests. Point it somewhere local if the lab has no internet egress |

### Guest defaults that are decisions, not secrets

| Variable | Default |
|---|---|
| `TF_VAR_linux_password_auth` | `false` (SEC-007) |
| `TF_VAR_windows_enable_winrm_default` | `true` |
| `TF_VAR_windows_winrm_allow_unencrypted_default` | `false` (KAN-015) |
| `TF_VAR_windows_autologon_default` | `false` (SEC-001c) — on, the administrator password reaches the registry until the next boot |
| `TF_VAR_arc_enabled_default` | `false` |
| `TF_VAR_arc_install_script_url` | `https://aka.ms/azcmagent` |

A bool variable is checked before Terraform sees it: `yes`, `on` and the like
are refused with a message naming the variable, rather than surfacing as a type
conversion failure on a line nobody wrote.

## 3. Everything else lives in `variables.tf`

**There are no secrets for the lab's topology**, and since KAN-012 there is no
merge either: the table in §2 is the set of values that can be changed per
environment from repository settings. Anything not in that list still needs an
edit to `variables.tf` and a pull request — which is the right cost for a value
nobody expects to differ between labs, and the wrong one for an endpoint.

`arc_location`, `arc_cloud` and `proxmox_ssh_username` are the odd three out.
None is a secret, but all three are resolved from the *secrets* table for
historical reasons, so they are set as secrets rather than as variables. Moving
them would break every lab that has already set them, so it is its own change.

## 4. Proxmox prerequisites

- **Two templates**, at the VMIDs above. An Ubuntu cloud image and a Windows
  image with Cloudbase-Init. [template-build.md](template-build.md) is how to
  build one that satisfies [ADR 0003](adr/0003-template-provenance.md) §2
  (DOC-002-A3). It is not a transcript of how `9900` and `9917` were built —
  nobody recorded that, and reconstructing it would be inventing history.
- **A snippets-capable datastore.** `local` must have `snippets` in its content
  types, or the vendor-data upload fails.
- **An API token** — [proxmox-api-token.md](proxmox-api-token.md) covers
  creation and the privilege separation that is still outstanding (SEC-006, #55).
- **SSH access to the node**, because the provider uploads snippets over SSH
  rather than through the API. Currently `root` with a password; SEC-006 covers
  moving off that.
- **A quorate cluster.** A single node that was once clustered will refuse API
  writes when inquorate — [proxmox-cluster-quorum.md](proxmox-cluster-quorum.md).

## 5. Azure prerequisites — only if any VM sets `arc`

- A **resource group** matching `TF_VAR_ARC_RESOURCE_GROUP`.
- A **service principal** with rights to onboard and delete Arc machines in it.
  Onboarding needs *Azure Connected Machine Onboarding*; the destroy path also
  deletes machines, which onboarding alone does not permit — see
  [arc-cleanup.md](arc-cleanup.md).
- **`Microsoft.HybridCompute` registered** on the subscription.

Leave the Arc secrets unset and every guest skips onboarding cleanly. That is a
supported configuration, not a degraded one.

## 6. The runner

Applies and plans run on a self-hosted runner labelled `gha-runner-01`. What it
holds and what may execute on it is
[runner-trust-boundary.md](runner-trust-boundary.md) — read it before adding a
workflow that touches the lab.

Two things must be true before a run works:

1. **`/opt/terraform-state/proxmox-ubuntu-vm-factory` exists and is writable by
   the runner account.** Create it during runner provisioning. No workflow step
   uses `sudo` — the runner account had passwordless sudo purely to create this
   directory, which handed every step a root escalation for one `mkdir`
   (SEC-004-A4). The workflows now only assert the directory is there.
2. **"Require approval for all external collaborators" is enabled** in
   Settings → Actions → General. The fork gate in `terraform-plan.yml` is the
   code half; this is the other half, and neither is sufficient alone.

State is a local file on that runner, unbacked-up. Losing the runner loses the
state — FEAT-001 (#56) is the open issue for that, and it is why DOC-001 (#59)
will not import anything until it lands.

### `TF_BOOTSTRAP`

The apply workflow refuses to create VMs when the inventory is non-empty and the
state is empty, because applying then would build guests Terraform immediately
forgets — the orphan case in [incident-orphan-vm.md](incident-orphan-vm.md).

| Inventory | State | Verdict |
|---|---|---|
| empty | empty | proceed |
| empty | non-empty | proceed, loudly — this apply tears the lab down |
| non-empty | empty | **blocked** unless `TF_BOOTSTRAP=true` |
| non-empty | non-empty | proceed |

Set `TF_BOOTSTRAP=true` only for a genuine first run against a lab that has no
state file at all, and unset it immediately afterwards. It authorises exactly
the third row and **only when no state file exists**: an existing state file
listing nothing is not a first run, it is a state that was truncated or restored
empty, and bootstrap does not override that. Leaving it set turns the guard off
for the case it exists to catch.

## 6b. What actually gates an apply

Nothing approves one. There is no branch protection on this repository — the API
returns `403 Upgrade to GitHub Pro or make this repository public` — and the
`prod` environment has no protection rules, so `environment: prod` scopes the
secrets without gating anything.

[release-process.md](release-process.md) has the measured table, what it would
take to have a real approval gate, and the routine and emergency procedures.

## 7. Order of setup

1. Templates built, snippets datastore enabled, API token created
2. The three required secrets added
3. Runner provisioned, state directory created, external-collaborator approval on
4. Arc secrets added, or deliberately skipped
5. `local.vms` still empty — merge and confirm a clean plan first
6. Add one VM, review the plan line by line, apply

## Not done

**DOC-002-A7 — nobody has followed this guide from scratch.** The acceptance
criterion asks for a second person to walk it and for their friction points to
be recorded, and that has not happened. §4's Proxmox prerequisites are still
described from the configuration rather than from a rebuild — and the templates
half of that is now [template-build.md](template-build.md), which carries the
same caveat: **nobody has followed it either.** It is derived from a
specification that was measured, which is a better starting point than the gap
it replaces and is not the same as having been walked.
