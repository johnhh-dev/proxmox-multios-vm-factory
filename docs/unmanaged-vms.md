# The nine VMs Terraform does not manage

DOC-001-A2 (#59) asks for a lifecycle decision per VM found on the node: import
into state, retain deliberately unmanaged, rebuild, or retire. This is the
inventory that decision has to be made against, and an argument for each.

**Four of the nine are now decided** and refused at plan time by
`var.protected_vm_ids`. The rest are still an argument rather than a decision,
which is a judgement about a lab this repository only partly describes.

Captured from `pve` on 2026-08-30. See the README for the full table including
the managed guest and both templates.

## Building the table yourself

`.github/scripts/reconcile_inventory.py` produces it. #144 built it for
DOC-001-A1 and nothing named it anywhere, so it was findable only by listing
that directory:

```bash
# on the node
qm list > qm-list.txt

# on the runner
terraform show -json > state.json
terraform console -no-color <<< 'jsonencode(keys(local.vms))' > declared.json
#   the tool takes the bracketed form terraform prints, or one name per line

python3 .github/scripts/reconcile_inventory.py \
  --qm-list qm-list.txt \
  --state state.json \
  --declared declared.json
```

**The two ID lists are no longer yours to remember.** Omitting
`--protected-vmids` or `--template-vmids` reads them from `variables.tf` — the
same `var.protected_vm_ids` this page argues about, and the two
`var.template_vmid_*` values — and the run says which it picked up:

```
--protected-vmids not given; read var.protected_vm_ids from variables.tf
```

This page used to say **"Pass both ID lists"** in bold instead, and the failure
of not doing so was a false `orphan` beside a template or beside the runner,
with an action column pointing at a runbook whose default is
`qm destroy --purge`. A tool that can read the answer should not have depended
on the operator having read a warning.

Both flags still work and still win. `--protected-vmids ''` means *none*,
deliberately — that is not the same as leaving the flag out, and it is what to
use when comparing against a lab that does not have these IDs.

`state.json` carries every input variable in cleartext (SEC-002) — delete it
when you are done.

## The four on `var.protected_vm_ids`

All four are refused at plan time. They are there for **two different reasons**,
and the difference is worth keeping visible, because it changes how the next
"should this one be here too?" gets answered.

### Circular — the factory needs it in order to run

| VMID | Name | Why |
|---|---|---|
| 1110 | `gha-runner-01` | **Holds the state.** Destroying it terminates the run doing the destroying — OPS-003 (#171) |
| 1103 | `dns-01` | Every guest is handed `var.dns_server` and `var.dns_servers_fallback`; the first-boot scripts wait for DNS before they can do anything |
| 1104 | `wg-vpn-01` | The management path into the lab. Rebuilding it while working through it is the shape of mistake that ends a session |

Importing any of these means Terraform managing a machine it needs in order to
run, so an apply could remove its own prerequisite. The failure mode of getting
it wrong is losing the ability to fix it.

### Declared off limits by the operator

| VMID | Name | Why |
|---|---|---|
| 1105 | `elastic-01` | Added 2026-08-30 on the lab owner's instruction |

**This repository does not know why, and does not guess.** The guest is
unmanaged, so it runs no agent this factory can query, and nothing here can see
what it holds.

An earlier version of this page listed `elastic-01` as an ordinary import
candidate, on the reasoning that *"losing it is recoverable by the thing that
lost it"*. That was reasoning about a machine nobody had looked inside. The
person who has is the one who decided, and a deny-list does not require the
denier to justify themselves to the configuration.

What it does require is that the entry be **visible**, which is what this table
is for. If the reason is written down somewhere, link it from here.

## The two that are ordinary candidates

| VMID | Name | Note |
|---|---|---|
| 1100 | `microk8s-01` | FEAT-004 (#62) is written as *build* a multi-node cluster; one node is already running. Adopting it changes that issue from "build" to "adopt and extend" |
| 1101 | `ubuntu-utils-01` | Referenced nowhere in this repository |

`elastic-01` was in this group until 2026-08-30 and is now on the protected
list — see above.

**These are where an import would actually teach something**, and they are also
where it can go wrong cheaply. FEAT-002 measured the rule that matters: setting
`vm_id` to the ID a guest already has forces no replacement; setting a different
one **destroys and rebuilds it**. So an import is safe exactly as far as the IDs
are checked one at a time.

## The two with no obvious answer

| VMID | Name | Note |
|---|---|---|
| 1106 | `macos-ventura` | Stopped, `onboot=0`, referenced nowhere. Retire or record why it stays |
| — | the templates 9900 / 9917 | Not VMs to manage. SPIKE-002 (#71) asked whether they should be built from code; [ADR 0003](adr/0003-template-provenance.md) is the answer |

## Proxmox reuses VM IDs, and that has already bitten

Observed on 2026-08-30. `win-srv-01` was VMID 101; it was destroyed, and the
next apply built `ubuntu-dhcp-01` — which Proxmox also gave 101, because
`vm_id` is unset and it assigns the lowest free ID.

Terraform is right either way. What is wrong is anything **outside** Terraform
that remembered the number: a firewall rule, a monitoring target, a backup job,
a note in a runbook, a `qm` command in someone's history. All of them now point
at a different machine, and nothing announced the change.

Two things follow for this page:

- **Declaring `vm_id` is the fix**, and FEAT-002 already measured what it costs:
  setting it to the ID a guest already has forces no replacement. The current
  guest could be pinned at 101 today.
- **An import inherits this.** A guest adopted without a declared `vm_id` keeps
  whatever it has until something destroys and recreates it, at which point the
  ID is whatever is free.

The post-apply smoke test now prints the assigned IDs, so two runs can be
compared. It cannot detect the reuse — it has no memory of the previous apply —
and putting the number where a human will see it is the whole of what a check
can do here.

## What has to be true before any import

1. **FEAT-001-A2.** [ADR 0004 §5](adr/0004-terraform-state.md) makes the state
   purge (SEC-001e, #120) a precondition for the backend, and DOC-001-A3 asks
   for a working restore before any import. #148 takes the backup and #143
   proved the restore against a scratch state — **not against the lab's own**.
2. **A plan read line by line.** DOC-001-A5: do not apply if any action is
   unexplained. An import that produces a `replace` is the one to stop on.
3. **One VM at a time.** DOC-001-A4 asks for small reviewable steps, and the
   reason is in the table above: the cheap mistakes and the unrecoverable ones
   are next to each other.

## What the guests would inherit

Worth knowing before adopting one, because import does not mean "leave alone":

- **A vendor-data or user-data document.** Every managed VM gets one, and it
  runs at first boot only. An imported guest does not re-run it, but a later
  `-replace` would — see [guest-config-changes.md](guest-config-changes.md).
- **The Arc default.** `var.arc_enabled_default` is false, so an imported VM
  onboards nothing unless its inventory entry asks.
- **Replication is not managed here.** Every unmanaged guest above replicates
  to `pve2` on a `*/15` schedule, so a replica can be a quarter of an hour
  old; a VM the factory builds does not replicate at all. Importing one does
  not preserve its replication job, and nothing in this repository would
  recreate it.

  That asymmetry is not an oversight in the reading. Replication here is
  configured **per guest, by hand, outside this repository**, so a guest the
  factory builds gets no job and an imported one keeps none. Measured with
  `pvesr list` and `pvesr status` on 2026-08-30: all seven jobs `State OK`,
  `FailCount 0`, each run 2-6 seconds.

That last point is the one most likely to be missed, and it is the one with no
warning attached anywhere else.
