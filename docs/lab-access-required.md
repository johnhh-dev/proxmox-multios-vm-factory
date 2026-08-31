# What is left, and needs someone at the lab

Everything still open in this repository is blocked on the same thing: a person
with access to the runner, the Proxmox node, or a guest. Nothing here can be
closed by writing more code.

That work is currently spread across seven issues and four decision records.
This is the same list in one place, ordered by what it unblocks rather than by
priority label — because two of these are gating each other and neither issue
says so.

## Done on 2026-08-30

Someone connected to the node. What that settled, so nobody repeats it:

| Was | Now |
|---|---|
| **SPIKE-002-A1** — one `Get-Content` decides whether a P0 can close | done (#170). The real Cloudbase-Init config has eight plugins, not the example's three, and `inject_user_password=true`. It withdrew claims in **two** ADRs |
| **SEC-001d-A3** — inspect the node filesystem | done. Two snippets, both current, one per managed VM |
| **SEC-001d-A4** — do backups or replication hold copies? | done (#175). No backup jobs, no vzdump archives, no snapshots of the snippets dataset |
| **DOC-001-A1** — capture the inventory | done (#177). Eleven VMs, nine unmanaged, in a **two-node cluster** |
| **SEC-006-A1's procedure** | corrected (#173). It pinned the leaf, which `openssl verify` refuses; the CA is the right file |
| **SEC-006-A2/A4's privilege table** | confirmed. `terraform@pve` holds `Administrator` on `/`, propagating |
| **FEAT-009's template sizes** | measured (#173). 50 GB and 100 GB, so the shrink check works for the first time |

**And it found what nothing was looking for.** The Windows first-boot script has
never run — OPS-004 (#176) — so five merged pull requests believed to have taken
effect had not. The mechanism is confirmed: `cicustom: vendor=` lands in
`/openstack/latest/vendor_data.json` and Cloudbase-Init executes nothing there.

The lesson is not that the node needed reading. It is that **everything above
was answerable in an afternoon and had been open for months**, and that the one
finding nobody had an issue for was the one that mattered most.

## 1 · Rebuild the Windows guest

**OPS-004-A3** (#176). #178 changes the Windows attachment to `cicustom: user=`,
which the guest actually reads. Nothing proves it works until a guest is built
with it — and a rebuild is required regardless, because cloud-init does not
re-run first-boot logic on a guest that has already booted.

**VM 101 no longer exists.** It was destroyed on 2026-08-30 by the apply on
commit `9bf6c57`, which commented `ubuntu-dhcp-01` out of `local.vms`. So this
is now a create rather than a replace, and it needs a `win-srv-01` entry put
back into the inventory first. **Read the plan** either way: Proxmox will hand
the new guest whatever ID is free, and 101 is free.

Afterwards, on the guest:

```powershell
Test-Path C:\cloudbase-firstboot-test.log
Test-Path C:\ProgramData\vm-factory-firstboot.done
Get-Content C:\Windows\System32\LogFiles\Firewall\pfirewall.log -Tail 5
```

The third line is KAN-011-A6 and is new: the script now enables the Windows
Firewall log before it opens RDP or WinRM, so a rebuilt guest should have that
file with the RDP session you are typing into already in it. An empty or
missing file means logging did not take, and the first-boot log says which of
its two branches ran.

If the first two are true, then **six merged changes take effect for the first
time** — SEC-008, BUG-007, BUG-010's Windows half, KAN-015, SEC-001c and now
KAN-011-A6 — and A4 is to check each actually does what its PR claims.

**If you ran the second line before 2026-08-30, run it again.** It held a
literal U+000B where the `\v` of `\vm-factory` should have been — the escape
expansion `verify_first_boot.py` guards against, in the document that tells a
person what to type. Nothing renders a vertical tab, so the path looked correct
and `Test-Path` returned `False`: the same answer a guest whose first boot never
ran would give. `check_guest_paths.py` now fails the `checks` workflow on it.

## 2 · What is in the state, on the runner

**SEC-001e-A1** (#120). **Half of this is no longer a task.** Every apply now
runs the audit against the state directory on the runner and reports what it
found, so "which credentials appear in state" is answered on each run rather
than once by someone with an SSH session.

What a person still has to do is the other half, and it is the half SEC-001e-A4
is about: **the workflow can only scan for the values it currently holds.** A
credential that has since been rotated is in the twenty backups and in no
environment variable, so nothing but a person who knows the old value can find
it. Run this on `gha-runner-01` — VM 1110 on the same node (OPS-003, #171) —
with the **rotated-out** values supplied:

```bash
cd /opt/terraform-state/proxmox-ubuntu-vm-factory
SECRET_VARS='TF_VAR_proxmox_api_token,TF_VAR_proxmox_ssh_password,TF_VAR_windows_admin_password,TF_VAR_arc_access_token'   python3 /path/to/repo/.github/scripts/audit_state_secrets.py .
```

It scans the twenty backups as well as the live file, and it prints no
credential. **[ADR 0004](adr/0004-terraform-state.md) makes this a
precondition** for the state backend rather than a follow-up — and the apply
step above is what will show it has stayed true once the purge has happened.

## 3 · The half of the inventory that is still a judgement

**DOC-001** ([#59](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/59)).
A1 is done — the node side was `qm list`, and the result is in the README. What
is left needs the runner and a decision:

```bash
terraform state list       # on the runner, the other half of the comparison
```

[#180](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/pull/180) argues each of the nine, and becomes `docs/unmanaged-vms.md` when it lands. The two things to
carry into it:

- **Three should not be imported at all** — the runner, `dns-01` and
  `wg-vpn-01`. Managing a machine the factory needs in order to run is a cycle.
- **Importing a guest does not preserve its replication job.** All seven
  unmanaged guests replicate to `pve2`, every 15 minutes; nothing here would
  recreate that. A guest can be adopted and quietly lose the only copy of
  itself.

Read [FEAT-002's note in `main.tf`](../main.tf) first. Setting a `vm_id` that
differs from what a guest already has **forces replacement** — measured, not
assumed.

## 4 · The identity nothing has connected with

**SEC-006-A5** ([#55](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/55)).
Three separate things are written down and none has been exercised:

| Change | Procedure | Never run with |
|---|---|---|
| Certificate validation on | [proxmox-api-token.md](proxmox-api-token.md) §Transport | `proxmox_tls_insecure=false` |
| A restricted API role | same file, §Least privilege | the `TerraformFactory` role |
| Node SSH by key or agent | same file, §The node SSH identity | anything but the root password |

The privilege list is **derived from what the configuration does, not proven by
a run**. Expect to add one or two — do it by reading the 403, which names the
path and the missing privilege, rather than widening back to `Administrator`.

Since #154, all three are repository variables rather than code changes. The job
log names what it picked up: if `from repository variables:` does not list the
one you set, it did not take effect.

## 5 · The gates that do not exist

**KAN-017-A6** ([#22](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/22)).
Two of its five cases cannot be run at all — there is no approval to deny and no
plan artifact to go stale, per
[release-process.md](release-process.md). The three that can:

- two concurrent runs queue rather than interleave
- a failed apply leaves state and lab consistent
- a restore from `backups/` recovers from it

The third has been drilled against a scratch state ([state-recovery.md](state-recovery.md) §4)
and never against the lab's own.

## 6 · The qdevice nobody has installed

[proxmox-cluster-quorum.md](proxmox-cluster-quorum.md) recommended one and
nothing has changed: expected votes 2, total votes 2, no `device { }` block.

Measured 2026-08-30, and the numbers make it sharper than the arithmetic does.
All **9** VMs run on `pve`; **0** run on `pve2`. So losing `pve2` — the node
that hosts nothing — makes `pve` inquorate and freezes every create, start, stop
and destroy on the node that hosts everything.

It needs a third vote from somewhere that is not either node:

```bash
apt install corosync-qdevice      # on both PVE nodes
pvecm qdevice setup <qnetd-ip>    # on one of them
```

The qnetd host can be anything small that is not `pve` or `pve2` — which, given
`gha-runner-01` is itself a guest of `pve`, is a shorter list than it looks.

## 7 · The management flows nobody has tested

**KAN-011-A3 to A7** ([#25](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/25)).
A1's matrix and A2's boundaries are written —
[management-network.md](management-network.md) — and it took no lab access,
because most of the model was already declared in `variables.tf`,
`providers.tf` and the two guest templates.

What it could not settle is everything about the one flow this repository does
not configure, plus the negative half of every other:

```bash
wg show                       # on wg-vpn-01: port, peers, allowed IPs, key age
pve-firewall status           # on the node: whether there is a firewall at all
```

Then, from a device that is **not** on the VPN, attempt RDP and WinRM to a
Windows guest. The criterion is not that the sixteen documented flows work — it
is that a path which is not one of them is denied.

Two things the matrix already found that needed no measurement: the guest
firewall rules this repository enables were enabled for any remote address, and
**not one inbound flow to a guest was logged anywhere**.

The second now has a mechanism (KAN-011-A6). The Windows first-boot script turns
on the firewall log for every profile, allowed and dropped, before it enables
either rule group — so the negative tests above have somewhere to leave a trace.
**It has never run**, for the reason section 1 is about, which is why the check
for it is on that list rather than this one.

The first now has a mechanism and no value. `var.management_source_cidrs`
narrows both rule groups, and it is empty because **nothing here knows the VPN
range** — so this is the one item on this page where the measurement above is
also the fix:

```bash
wg show          # the allowed IPs are what management_source_cidrs should be
```

Until then a Windows guest logs `reachable from ANY source` on every first boot,
which is the honest version of what it did silently before.

## What no amount of lab access fixes

Three things are recorded as **impossible rather than pending**, so nobody
spends an afternoon rediscovering them:

- `sensitive()` cannot keep a value out of state — [ADR 0001 §2](adr/0001-guest-secret-delivery.md)
- ADR 0001's option D cannot satisfy SEC-001c's first criterion for this
  template — [§9](adr/0001-guest-secret-delivery.md)
- there is no approval gate on `main`, and none is available on this GitHub plan
  — [release-process.md](release-process.md)
