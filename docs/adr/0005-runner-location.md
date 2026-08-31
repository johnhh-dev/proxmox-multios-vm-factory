# ADR 0005 · Where the runner lives

- **Status:** Accepted — stay on `pve`; take the state off it instead
- **Date:** 2026-08-30
- **Issue:** OPS-003-A3 ([#171](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/171)) · **Related:** [ADR 0004](0004-terraform-state.md), [runner-trust-boundary.md](../runner-trust-boundary.md)
- **Deciders:** `@iac` + `@ops`

---

## 1 · The activity, and the assumption inside it

OPS-003-A3 reads:

> Decide whether the runner should move off this node entirely. **That is the
> only version that actually removes the cycle**, and it has a cost this lab may
> not want to pay.

The clause in bold is the thing to test before answering. It treats "the cycle"
as one thing. Reading the issue's own evidence, it is two, and moving the runner
addresses one of them.

## 2 · The two cycles

### The lifecycle cycle — the factory can destroy the machine running it

`gha-runner-01` is VM **1110 on `pve`**, the node this factory clones into. A
destroy that reached 1110 would terminate the run doing the destroying, leaving
state written or not depending on timing.

**Moving the runner to `pve2` does not remove this.** The API token is
`Administrator` on `/`, propagating — confirmed on the node — and that is
cluster-wide. A runner on `pve2` can destroy a VM on `pve2` exactly as easily.

**Moving it off the cluster does remove it**, because there is then no VMID for
a plan to name.

What holds it today is OPS-003-A1: `var.protected_vm_ids` refuses a plan that
declares 1110. Worth being exact about what that is and is not — as
`variables.tf` already says: *"Refusing is not the same as protecting. Nothing
here stops `qm destroy` on the node, and nothing stops an operator removing the
ID from this list. It stops the plan that nobody meant to write."*

### The failure-domain cycle — the record of the lab is stored in the lab

The state file and its twenty backups are on the runner's disk, and the runner's
disk is on the node's storage. ADR 0004 §4D rejected self-hosted S3 for *"state
describing the lab, stored in the lab"* without knowing that already described
the status quo.

**Moving the runner off the cluster removes this too** — but so does moving the
*state*, and only one of those is already decided.

## 3 · Options

### A · Stay on `pve`, and do nothing else

| | |
|---|---|
| Cost | None |
| Lifecycle cycle | A1's deny-list, which is a repository guard and not a hypervisor one |
| Failure domain | Unchanged. `pve` dies and the lab and its record go together |
| Honest summary | This is the status quo with the finding recorded and not acted on |

### B · Move the runner to `pve2`

`pve2` runs **0 VMs**; `pve` runs 10. VM 1110 already replicates to `pve2`, so
the migration target is a machine that already holds a copy.

| | |
|---|---|
| Cost | One migration. `pve2` hosts something for the first time, which changes what it is for |
| Lifecycle cycle | **Not removed.** The token is cluster-wide |
| Failure domain | Narrowed, not removed: state survives losing `pve`, not losing the cluster |
| The catch | With no qdevice, `pve` dying leaves `pve2` **inquorate**. The runner would be up, holding good state, and unable to write anything to the cluster it survived |

That last row is what makes B look better than it is. The scenario it buys —
`pve` dies, the runner lives — is one where the runner cannot act until someone
runs `pvecm expected 1` by hand, which is the same intervention a restore needs.

### C · Move the runner off the cluster entirely

| | |
|---|---|
| Cost | A machine this lab does not have. Plus re-provisioning the runner, its state directory, its credentials and the `[self-hosted, gha-runner-01]` label |
| Lifecycle cycle | **Removed** |
| Failure domain | **Removed** |
| Honest summary | Correct, and not affordable today |

### D · Stay on `pve`, and move the state off the runner

This is [ADR 0004](0004-terraform-state.md)'s decision, already made: encrypted
backups off-host now, `azurerm` after SEC-001e.

| | |
|---|---|
| Cost | Already accepted in ADR 0004 — every plan gains a hard dependency on Azure once C lands there |
| Lifecycle cycle | Unchanged, still A1's guard |
| Failure domain | **Removed**, and removed better than by C: the record leaves the lab rather than moving to another machine in it |
| What it reframes | The runner stops being irreplaceable. A worker holding no unique state is a machine to rebuild, not a machine to protect |

---

## 4 · Decision

**A plus D. The runner stays on `pve`; the thing that must not be lost with it
leaves instead.**

Three reasons, in the order they carry weight.

**The harm OPS-003 describes is about the state, not about the runner.** The
issue's own sharpest sentence is *"the machine holding Terraform state is a guest
of the hypervisor whose state it describes"* — and every consequence it lists
follows from *holding the state*, not from *being a guest*. A runner that holds
nothing unique is a machine that can be rebuilt from the runbook, and losing it
costs an afternoon rather than the mapping between configuration and every VM in
the lab.

**B is a half-measure that costs a migration and buys a scenario it cannot act
in.** It does not touch the lifecycle cycle at all, and the failure it improves
leaves the survivor inquorate.

**C is the right answer at a price this lab has not agreed to pay.** It is not
rejected on the merits; it is deferred, and §6 says what would bring it forward.

## 5 · What is deliberately accepted

**The lifecycle cycle stays**, held by a repository-level guard that an operator
can remove and that the hypervisor knows nothing about. Accepted because the
plan-time refusal covers the case that would actually happen — DOC-001 importing
nine unmanaged guests, one of which is the runner — and because the alternative
that removes it is C.

**The runner is still a single point of failure for *running* anything.** No
apply, no destroy, no plan on a pull request. That is unchanged by this decision
and unchanged by ADR 0004; it is a second runner that fixes it, which ADR 0004
§6 already names as something that would change *its* decision too.

**`pve2` continues to host nothing.** That is a deliberate consequence of
rejecting B, and it interacts with the qdevice question:
[proxmox-cluster-quorum.md](../proxmox-cluster-quorum.md) records that losing
the node that hosts nothing freezes management of the node that hosts
everything.

## 6 · What would change this

- **The lab acquires a machine that is not `pve` or `pve2`.** Then C is
  affordable, and it should be taken. Note that the qdevice needs exactly the
  same thing — a third host that is neither node — so these two open items point
  at one acquisition, and neither issue says so.
- **A second runner appears.** ADR 0004 §6 already says the local backend stops
  being viable then. It also makes C cheaper, because re-provisioning is
  something the lab would then have done twice.
- **The state moves and the runner still cannot be rebuilt.** D's whole argument
  is that a runner holding nothing unique is replaceable. If there is no
  procedure for rebuilding it, that is an assertion rather than a fact — and
  [runner-trust-boundary.md](../runner-trust-boundary.md) describes what the
  runner *holds*, not how to make another one.
- **`var.protected_vm_ids` is removed or the token is narrowed.** The first
  would leave the lifecycle cycle unheld and force C. The second — SEC-006-A2's
  restricted role — would hold it at the hypervisor instead of in this
  repository, which is strictly better than what A1 does.

---

## References

- [OPS-003 (#171)](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/171) — the finding, and A1, A2, A4
- [ADR 0004](0004-terraform-state.md) §5b — the measurement this reasons from
- [runner-trust-boundary.md](../runner-trust-boundary.md) — what the runner holds
- [proxmox-cluster-quorum.md](../proxmox-cluster-quorum.md) — why `pve2` being empty is not neutral
- [unmanaged-vms.md](../unmanaged-vms.md) — why 1110 is on the deny-list
