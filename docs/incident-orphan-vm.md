# Orphan VMs — a VM Proxmox has and Terraform does not

Why this file exists: on 2026-08-28, run
[33172015639](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/actions/runs/33172015639)
failed with

```
Error: All attempts fail:
#1: received an HTTP 500 response - Reason: SSH public key validation error
  with proxmox_virtual_environment_vm.vm["ubuntu-static-01"]
```

and left VM 100 running on the node with 51 GB of disks, no cloud-init
configuration, and no entry in Terraform state. DOC-005 asks for the orphan-VM
procedure to be exercised against a throwaway VM; this one exercised itself.

## Why an orphan is created by a *failed* apply

The instinct is that a failed create leaves nothing behind. That is not how this
resource fails.

`proxmox_virtual_environment_vm` creates a guest in two phases: clone the
template, then write the configuration onto the clone. The clone is a Proxmox
task that runs to completion on its own — on 2026-08-28 it transferred 50 GiB
and reported `TASK OK`. The configuration write is a separate call, and it is
the one that rejected the SSH key.

Terraform records a resource only once creation *succeeds*. So the clone's
output — a real VM, with real disks, consuming real space — exists with nothing
pointing at it.

It gets worse quietly: `main.tf` sets no `vm_id`, so the next apply asks Proxmox
for the next free ID rather than reusing the one it failed on. The orphan is
never revisited. That is FEAT-002-A1 ([#57](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/57)),
and it is the reason this failure mode compounds instead of self-correcting.

## Detection

**From the run.** An apply that fails on `proxmox_virtual_environment_vm` after
the clone step has almost certainly left one. The tell is a `qmclone` task that
succeeded inside a run that did not.

**On the node.** Compare what Proxmox has against what Terraform knows:

```bash
ls /etc/pve/nodes/<node>/qemu-server/
ls -la --time-style=full-iso /etc/pve/nodes/<node>/qemu-server/
```

A `.conf` whose modification time matches the failed run, and whose name is one
of your inventory names, is the orphan. Confirm from the task log:

```bash
tail -5 /var/log/pve/tasks/index
```

The UPID carries a hex timestamp; `qmclone` entries name the template they cloned
from, not the VM they produced.

**Against state.** The apply job prints `terraform state list` before it runs.
A VM present on the node and absent from that listing is the definition of this
incident.

**The signature of an unconfigured orphan** is a `.conf` carrying only what the
template had — no `ipconfig0`, no `cicustom`, no `sshkeys`, and CPU and memory
at the *template's* values rather than the inventory's. That combination means
the configuration write never happened, which in turn means nothing in the guest
was ever customised.

## Recovery

Two options, and the choice is not close for an unconfigured orphan.

### Stop first: is it on `var.protected_vm_ids`?

Three VMs on this node are **deliberately unmanaged** — the runner that would be
running this command, the resolver every first-boot script waits for, and the
management path you are probably connected through. See
[unmanaged-vms.md](unmanaged-vms.md).

**Two of the three checks below pass for all of them.** They are not in
`terraform state list`, because nothing manages them; and their `.conf` carries
no `cicustom` or `ipconfig0`, because they were built by hand. Only the third —
does the name and creation time match a failed run — would stop you, and it asks
you to notice.

`reconcile_inventory.py` reports them as `protected` rather than as orphans, and
that is the check to trust over this list.

### Destroy — the default

An orphan with no cloud-init has never been what the inventory asked for. It has
no data, no identity and no Arc registration. Destroy it and let the next apply
build the VM properly.

```bash
qm destroy <vmid> --purge
```

**This is destructive and irreversible.** `--purge` removes the disks as well as
the configuration, and also drops the VM from any backup job and HA
configuration that referenced it. Before running it, confirm all three:

- the VM is not in `terraform state list`
- the `.conf` has no `cicustom` or `ipconfig0` — nothing ever configured it
- the name and creation time match the failed run, not something older

If any of those does not hold, you are not looking at an orphan from this
incident. Stop.

### Import — when the guest matters

If the orphan has been running long enough to hold something, or it did get its
cloud-init and only the state write was lost, adopt it instead:

```bash
terraform import 'proxmox_virtual_environment_vm.vm["<inventory-key>"]' <node>/<vmid>
```

Then plan, and read the diff carefully. Terraform will want to reconcile the VM
with the configuration, and for a partially configured guest that can mean a
replacement — which puts you back where you started, with the deletion now
approved. The import path is only worth it when the plan comes back clean or
close to it.

## After recovery

The snippet is not an orphan. It lives in state, because the file resource is
created before the VM and its create *did* succeed — so the next apply reconciles
it normally. Leave it alone.

Re-run the apply by hand; a fix that touches only secrets or the node produces
no commit, and this workflow triggers on push:

```powershell
gh workflow run terraform-apply.yml
```

## What this does not cover

Both of DOC-005's other two runbooks now exist, and neither is repeated here.

**Lost state** — [state-recovery.md](state-recovery.md). It waited on FEAT-001
building the mechanism a procedure would use; the restore is written and has
been exercised in a drill, and FEAT-001-A3 ([#148](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/pull/148))
adds the backup it restores from.

**Failed Arc onboarding** — [incident-arc-onboarding.md](incident-arc-onboarding.md).
This section used to say it had "not happened yet in a form worth writing up",
and that the detection steps should be written against a real failure rather
than an imagined one. That is still true of the *recovery* steps, and that file
says so about itself. What changed the balance is that the failure modes turned
out to be readable from the code rather than needing an incident — including one
that reports nothing anywhere, where a missing service principal makes every
Arc-enabled guest skip onboarding while the apply stays green.

A guest in that state is **not an orphan**. Nothing about it is inconsistent:
Proxmox has it, Terraform has it, and the only thing wrong happened inside it
after Terraform reported success.
