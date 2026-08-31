# State recovery: restore, and importing a VM Terraform has forgotten

Two procedures that FEAT-001 (#56) asks for, and that DOC-001 (#59) needs before
it can reconcile the inventory. Both have been exercised; the drill output is at
the bottom.

**This file does not close FEAT-001.** It covers restoring a state you still
have a copy of, and adopting a VM that exists in Proxmox but not in state. It
does *not* give you a copy to restore from — there is still no automated backup,
and state still lives as a single file on one runner. That is FEAT-001-A1/A2/A3
and it needs a decision. See [adr/](adr/).

## Where state is

```text
/opt/terraform-state/proxmox-ubuntu-vm-factory/terraform.tfstate
```

On `gha-runner-01`, in a directory created during runner provisioning. The stale
`ubuntu` in the path is deliberate — see the README's runner section.

## 1 · Why a lost state is worse than an empty one

With the state file gone, Terraform does not know the VMs exist. It does not
error; it plans to **create them**. Measured in the drill below: a two-resource
lab with its state deleted planned `2 to add, 0 to change, 0 to destroy`.

Against real VMs that means building a second set alongside the originals, which
Terraform then manages while the originals sit unmanaged and invisible —
[incident-orphan-vm.md](incident-orphan-vm.md), at scale and on purpose.

The apply workflow's inventory guard (BUG-002) is what stands in the way: a
non-empty inventory with an empty state is **blocked** unless `TF_BOOTSTRAP` is
set. If you are recovering, that guard is doing its job. Do not set
`TF_BOOTSTRAP` to get past it.

## 2 · Restore

1. **Stop applies.** Do not push to `main` while recovering. The apply workflow
   runs on push and there is nothing to stop it but you.
2. **Keep what is there**, even if it looks wrong:

   ```bash
   cd /opt/terraform-state/proxmox-ubuntu-vm-factory
   cp terraform.tfstate "terraform.tfstate.before-restore-$(date -Is)" 2>/dev/null || true
   ```

   A truncated state is still evidence about what happened.
3. **Pick a backup.** Since FEAT-001-A3 the apply and destroy workflows take one
   before they touch the backend, into `backups/` beside the state file, named
   for the UTC time and the run that took it:

   ```bash
   ls -lt /opt/terraform-state/proxmox-ubuntu-vm-factory/backups/
   # terraform.tfstate.20260829T142836Z.33255758406
   ```

   The run id is the second half of the name, so the backup can be traced to
   the run it preceded — **and that is the one to take: the backup is the state
   as it was *before* that run**, not after it. Recovering from a bad apply
   means restoring the backup labelled with that apply's own run id.

   The last twenty are kept. If the one you need has aged out, §1 applies.
4. **Copy the backup into place** as `terraform.tfstate`.
5. **Confirm the resources are back:**

   ```bash
   terraform state list
   ```
6. **Plan, and require it empty:**

   ```bash
   terraform plan
   ```

   `No changes.` means the restore is complete. **Anything else means stop.** A
   plan proposing creates means the state is older than the lab; one proposing
   destroys means it is newer, or from a different lab. Neither is fixed by
   applying.

## 3 · Import a VM that exists but is not in state

This is the path DOC-001 needs, and the recovery path when only *part* of the
state is missing.

The import ID is `<node_name>/<vm_id>` — from the provider's own documentation
for the pinned version:

```bash
terraform import proxmox_virtual_environment_vm.ubuntu_vm first-node/4321
```

For this factory the resource is keyed by the inventory name, so the address
carries the key:

```bash
terraform import 'proxmox_virtual_environment_vm.vm["ubuntu-static-01"]' pve/100
```

Order matters:

1. **Declare it first.** The VM must exist in `local.vms` before it can be
   imported — import attaches a real object to a configured address, it does not
   invent the configuration.
2. **Import one VM, then plan.** Not all of them, then plan.
3. **Read the plan attribute by attribute.** A freshly imported VM almost always
   shows a diff, because the inventory describes what you *want* and the guest
   is whatever it happens to be. Every line is a decision: is the configuration
   wrong, or is the guest wrong?
4. **Do not apply to make a diff go away.** On an imported VM an unexplained
   diff is how a running guest gets rebuilt. `vm_id` and `vendor_data_file_id`
   are both `ForceNew` — see the notes in `main.tf`.
5. The snippet is a separate resource. `proxmox_virtual_environment_file` for
   that VM will be *created* by the first apply after import, which is expected
   and harmless: it is a file on the node, not the guest.

Nothing else in this repository imports. `terraform_data.arc_registration`
(arc.tf) and `terraform_data.vm_factory_config` (checks.tf) are local markers
with no remote object — let the apply create them.

## 4 · The drill

Executed 2026-08-28 against a scratch state, two `terraform_data` resources
standing in for VMs. Not the lab's state — the point is the procedure, and
running it against real state would mean breaking it first.

```text
1. baseline state:      terraform_data.vm_a terraform_data.vm_b
2. backup taken:        backup-20260828T220000.tfstate
3. state file deleted
4. plan with no state:  2 to add, 0 to change, 0 to destroy
5. restored from backup
6. state list:          terraform_data.vm_a terraform_data.vm_b
7. plan after restore:  No changes. Your infrastructure matches the configuration.
```

Step 4 is the one to remember: **losing the state does not fail, it proposes to
build everything again.**

## 5 · What is still missing

| | Status |
|---|---|
| Restore procedure | this file, exercised |
| Import procedure | this file |
| Somewhere to restore *from* | **on the same disk** — FEAT-001-A3, see below |
| Surviving the loss of the runner's disk | **missing** — FEAT-001-A1 is undecided |
| State encrypted at rest | **missing** — it is a plaintext file, and so are the backups |
| Locking against concurrent applies | **unconfirmed**, see below |

### On the backup (FEAT-001-A3)

Taken by both the apply and the destroy workflow, immediately after
`terraform init` and before anything else touches the backend, into
`backups/` beside the state file. The last twenty are kept; each is verified
byte-identical to what it copied before the run is allowed to continue, because
a truncated backup restores cleanly and *then* proposes to rebuild the lab.

**It is on the same disk as the state it copies, so it does not answer the
finding in #56.** Losing the runner still loses the mapping. What it does cover
is everything short of that, which is what has actually gone wrong here: a
half-applied plan (BUG-024, run 33074685788), an apply that destroyed guests
nobody meant to destroy (BUG-012), a state truncated or restored empty, and
anyone running `terraform` by hand on the runner outside the concurrency group.

Going off-host is FEAT-001-A1's decision and cannot be made by adding an upload
step. State holds cleartext credentials for its whole history, so an
unencrypted copy anywhere off the runner is SEC-002 with a different filename.

**There is a tool for confirming what is actually in there, and the apply runs
it.** SEC-001e-A1 asks for it, and it used to need someone opening the file by
hand. `terraform-apply.yml` now runs it against this directory on every apply
and reports what it found, so the answer is current rather than as old as the
last SSH session.

Run it by hand when you want to scan for a value the workflow does not hold —
a **rotated-out** credential is in the backups and in no environment variable:

```bash
cd /opt/terraform-state/proxmox-ubuntu-vm-factory
SECRET_VARS='TF_VAR_proxmox_api_token,TF_VAR_proxmox_ssh_password,TF_VAR_windows_admin_password,TF_VAR_arc_access_token'   python3 /path/to/repo/.github/scripts/audit_state_secrets.py .
```

It scans the state file **and every backup**, reports which credential appears
in which file and in which rendering, and never prints a value. Findings are
expected today; after the purge, this returning nothing is SEC-001e's acceptance
criterion.

**SEC-001e's purge must include `backups/`.** Twenty timestamped copies of the
state sit beside it, each holding the same historical cleartext. A purge that
cleans `terraform.tfstate` and leaves them has done nothing.

### On locking (FEAT-001-A6)

Two concurrent applies are currently prevented by the `concurrency` group in the
workflow (CHORE-003), which is a GitHub Actions mechanism — it serialises *runs*,
not writes to the file. Anyone running `terraform apply` on the runner by hand is
outside it.

Terraform's local backend is supposed to take an OS-level lock on the state file.
An attempt to observe that here — two overlapping applies against one local
backend — saw the **second apply succeed**, on Windows. That is not the runner's
platform and so is not a finding about the lab, but it is a reason not to assume
the file lock is doing the work the workflow's concurrency group is visibly
doing. Confirm on `gha-runner-01` before relying on it.

**And the group covers less than it was credited with.** It holds
`terraform-apply` and `terraform-destroy`. It has never held `terraform-plan`,
which plans against the same backend on the same runner for every pull request —
so what keeps a plan out of an apply's way is that `gha-runner-01` takes one job
at a time. That is true, and it is not a property of anything in this repository
(KAN-017-A2).

Every locking command now passes `-lock-timeout=10m`, which is the part that
survives a second runner: a run that cannot take the lock waits for it instead
of failing at once with *Error acquiring the state lock*. It is a wait, not a
repair — a lock left behind by a killed run is still there when the timeout
expires, and `terraform force-unlock` is that path.
