# Getting a configuration change into a guest

What happens when you edit `cloudinit/linux.yaml.tftpl`, `cloudinit/windows.yaml.tftpl`,
or anything else that feeds the rendered vendor-data — and what does not.

## The short version

| | |
|---|---|
| The snippet on the node | **replaced**, every time the rendered document differs |
| The VM | **untouched** |
| The running guest | **unchanged** — cloud-init does not re-run first-boot logic |

A template edit therefore reaches *new* VMs immediately and *existing* ones not
at all. That is the policy (BUG-012-A2), and the rest of this file is why, and
what to do about it.

## Why the VM is deliberately not replaced

Both `source_raw.data` on `proxmox_virtual_environment_file` and
`vendor_data_file_id` on `proxmox_virtual_environment_vm` are `ForceNew` in the
bpg/proxmox provider. Left to chain, that means:

```
rendered vendor-data differs
  -> snippet resource is REPLACED
  -> its computed .id is unknown at plan time
  -> vendor_data_file_id is unknown
  -> the VM is REPLACED
```

And the rendered document differs on **every run**, because `arc_access_token`
is minted per run (SEC-001a) and interpolated into it.

This was not theoretical. Before BUG-012, on this repository:

| Run | What the commit changed | Plan |
|---|---|---|
| `33180698859` | nothing — a merge commit | `0 to add, 0 to change, 3 to destroy` |
| `33182594902` | comments in a `.tftpl` | `2 to add, 0 to change, 2 to destroy` |
| `33182626252` | validation rules in `locals.tf` | `2 to add, 0 to change, 2 to destroy` |

Every push to `main` destroyed and recreated the lab's guests, along with
everything inside them.

`main.tf` now composes `vendor_data_file_id` from
`source_raw[0].file_name` — which comes from configuration and so stays known
while the snippet is being replaced — instead of reading the file resource's
computed `.id`. The dependency edge is unchanged: the snippet is still created
before the VM that consumes it. What is gone is treating a rewritten snippet as
a reason to rebuild a guest.

The snippet's name is stable and the resource sets `overwrite = true`, so there
is exactly one snippet per VM and superseded ones do not accumulate
(BUG-012-A3).

## So how do I get a change into a running guest?

Decide which of these you actually need.

**1. The change only needs to apply to future VMs.** Nothing to do. Merge it.
The next VM built from the template gets it.

**2. The change must reach an existing guest, and the guest is disposable.**
Rebuild it deliberately:

```bash
terraform apply -replace='proxmox_virtual_environment_vm.vm["ubuntu-static-01"]'
```

Read the plan first. This destroys the guest and everything on it, and the new
one gets a new VM ID unless FEAT-002 (#57) has landed. If the VM is Arc-enabled,
the destroy path is what removes the old machine from Azure — see
`docs/arc-cleanup.md`; a rebuild that skips it leaves a stale Arc resource that
blocks re-onboarding under the same name.

**3. The change must reach an existing guest that cannot be rebuilt.** Apply it
in the guest by hand, and record that you did. cloud-init will not do it for
you: the first-boot modules run once, and the run-once markers in both templates
are there on purpose. Treat the template edit as the record of intent and the
manual step as the change.

## What still shows up in the plan

The snippet resource. Every apply prints:

```
# proxmox_virtual_environment_file.vendor_data["<name>"] must be replaced
```

That is expected, and — because of the per-run Arc token — it happens even when
nothing in the repository changed. It is noise, but it is *honest* noise: the
document on the node really is being rewritten. What it no longer implies is
that the guest is being rebuilt.

If that noise ever needs to go away, the fix is to stop rendering a per-run
credential into a document whose content is `ForceNew`, which is SEC-001's
territory rather than this file's.
