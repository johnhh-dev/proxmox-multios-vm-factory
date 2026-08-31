# ADR 0002 · Whether to split the first-boot config into static and per-VM documents

SPIKE-003 (#72). Time-boxed investigation, and the outcome is a **decision not to
split**. Recorded here rather than closed silently, because "we looked and the
answer was no" is worth as much as a change when the next person has the same
idea.

The spike's premise was that if the static, non-secret portion of bootstrap
moved to a separate document, the per-VM part would shrink — reducing both what
gets rendered into Terraform state and what has to be escaped for BUG-010.

Part of it already shipped: `cicustom: user=` → `vendor=` landed as #124, for a
different and more urgent reason. This records the rest.

## 1 · What the provider and Cloudbase-Init support (A1)

`proxmox_virtual_environment_vm.initialization` exposes exactly four slots:

| Slot | Used by this factory |
|---|---|
| `user_data_file_id` | **must stay free** |
| `vendor_data_file_id` | the first-boot config |
| `meta_data_file_id` | unused, but has a defined meaning |
| `network_data_file_id` | unused, but has a defined meaning |

Each takes **one file**. There is no list, so "a static document plus a per-VM
document" means spending a second slot, and only two are spendable.

`user_data` is not one of them, and that is the finding #124 was built on:
Proxmox expresses `ciuser`, `sshkeys` and `cipassword` *only* by rendering them
into the user-data it generates. Overriding that slot discards all three — key
authentication to every Linux guest silently stopped working. So the slot that
looks most natural for a second document is the one that must stay untouched.

That leaves `meta_data` and `network_data`. Both have defined semantics that
cloud-init and Cloudbase-Init already act on — instance-id and network
configuration. Putting arbitrary bootstrap in either means overloading a
document another component reads, which is the same class of mistake as
overriding user-data, discovered later and by a stranger.

**Conclusion: there is no free slot.** A split is not a refactor here; it is a
trade of one working mechanism for another.

## 2 · What is actually per-VM (A2)

Measured from the `templatefile` call in `main.tf`:

| | Count | Values |
|---|---|---|
| **Per-VM** | 6 | `hostname`, `fqdn`, `windows_enable_winrm`, `arc_enabled`, `arc_resource_name`, `arc_tags` |
| **Lab-wide** | 10 | `linux_password_auth`, `windows_admin_password`, `dns_servers`, `arc_cloud`, `arc_install_script_url`, `arc_tenant_id`, `arc_subscription_id`, `arc_resource_group`, `arc_location`, `arc_access_token` |

So the premise holds numerically: most interpolated values are identical for
every guest, and the scripts around them — 209 lines of Linux, 635 of Windows —
are almost entirely static.

**But the axis is wrong.** The expensive property is not "differs per VM", it is
"differs per run". And the value that differs per run is `arc_access_token`,
which is *lab-wide*. It would land in the static half of any static/per-VM
split and rewrite it on every apply anyway.

That is not hypothetical. It is why the snippet is replaced on every apply
today, documented in [../guest-config-changes.md](../guest-config-changes.md),
and it is what made every apply rebuild the guests until BUG-012 (#132).

A split along the axis this spike proposed would therefore reduce churn by
nothing.

## 3 · What the split was supposed to buy, and where that came from instead

| Goal | Delivered by | Would a split add anything? |
|---|---|---|
| Fewer secrets in state | SEC-001a (minted token), SEC-001b (`cipassword`) | No — the remaining secret is the Windows password, and it would move with the per-VM half |
| Less to escape (BUG-010) | BUG-010 — free-form values cross the boundary base64-encoded | No — the encoding is per value, not per document |
| Smaller reviewed diff | SEC-003 marks the rendered document `sensitive`, so the plan shows `(sensitive value)` | No |

Every benefit the spike was chasing arrived through a different change while it
sat unscheduled. That is the substantive reason to close it, rather than the
slot constraint — the slot constraint only says a split would be awkward; this
says it would be pointless.

## 4 · Decision

**Keep the single vendor-data document per VM.**

- No cicustom slot is free without giving up a working mechanism
- The per-run value is lab-wide, so the proposed axis does not reduce churn
- Every benefit sought has been delivered by SEC-001a, SEC-001b, SEC-003 and
  BUG-010

## 5 · What would change this answer

- **A per-run credential stops being rendered into the document.** Then the
  document becomes genuinely static between applies, and a static/per-VM split
  would start to mean something — though it would still need a slot.
- **Proxmox or the provider grows a second vendor-data slot**, or accepts a list.
- **The Windows password moves to a transient file** (SEC-001c, #118). That is a
  split, of a kind — but along the *secret* axis, not the static one, and it is
  already the plan.
- **A third guest OS arrives.** Two templates share almost nothing; three might
  make a common static document worth the slot.

---

## Measured on a guest, 2026-08-30

**SPIKE-003's change fixed Linux and appears to have disabled Windows.**

VM 101 has no `C:\cloudbase-firstboot-test.log` and no run-once marker — both
written among the first actions of `windows.yaml.tftpl`. Its hostname *is*
correct, so Cloudbase-Init ran and applied `SetHostNamePlugin` from the
meta-data Proxmox generates. What never ran is the document this repository
writes.

Cloudbase-Init's ConfigDrive service reads `user_data`. Nothing in the
template's real configuration
([`cloudbase-init.conf.actual`](../../cloudinit/cloudbase-init.conf.actual))
names a vendor-data handler, and its eight plugins contain no equivalent.

The Linux reasoning that motivated `vendor=` is unaffected and still correct —
overriding user-data there discarded `ciuser`, `cipassword` and `sshkeys`, which
is why key authentication had silently never worked. **The mistake was applying
one guest's answer to both**, and the asymmetry that makes per-OS attachment
plausible was in front of us: the Windows template presets `ciuser:
Administrator`, so it has far less to lose from `user=` than a Linux guest does.

### A1, confirmed

The hypothesis above was close and wrong in one way that matters. From
`/usr/share/perl5/PVE/QemuServer/Cloudinit.pm` on the node:

```perl
generate_nocloud       ->  '/vendor-data'
generate_configdrive2  ->  '/openstack/latest/vendor_data.json'
```

**The vendor-data is delivered to Windows.** It is not missing; it lands in an
OpenStack JSON slot. Cloudbase-Init's ConfigDrive service reads `user_data`
through `UserDataPlugin`, and none of the template's eight plugins executes
`vendor_data.json`. Delivered, never run — which is exactly what VM 101 shows.

### The decision that follows

**Per-OS attachment.** `vendor=` for Linux, where SPIKE-003's reasoning is
unchanged and still correct, and `user=` for Windows, which reads that slot.

Windows gives up much less than Linux would. Proxmox's generated user-data
carries `ciuser` and `cipassword`; `main.tf` emits no `user_account` for
Windows, so there is no `cipassword` to lose, and the first-boot script sets the
Administrator password itself.

**It costs one thing, and it is not small.** The script and `cipassword` are
mutually exclusive on Windows — one needs Proxmox's generated user-data and the
other replaces it. That closes off the route
[ADR 0001 §9](0001-guest-secret-delivery.md) had just opened for SEC-001c, and
that section now records the three-way choice rather than two options.

Expect a rebuild of the Windows guest. A rebuild is required regardless, because
cloud-init does not re-run first-boot logic on a guest that has already booted.
