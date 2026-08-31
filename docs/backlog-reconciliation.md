# The two backlogs

This repository has been tracking the same work twice. Issues **#15–#31** are a
Kanban backlog (`KAN-001` … `KAN-017`) written before the audit; issues
**#32–#120** are the audit's remediation plan (`EPIC-000`, `SEC-`, `BUG-`,
`DOC-`, `FEAT-`, `CHORE-`, `SPIKE-`). They describe overlapping work in
different words.

DOC-003-A7 named this — *"fold the surviving items into this board … so there is
one backlog rather than two"* — and folded the README's *Future Improvements*
section in. This is the other half: the KAN issues themselves.

**Nothing is closed by this file.** Closing an issue is a judgement about whether
the work is done, and several of these are genuinely ambiguous. What this does is
put the mapping somewhere both halves can see it, so the judgement is made once.

## Delivered — the audit issue has shipped

| KAN | Audit equivalent | Shipped in |
|---|---|---|
| [KAN-003](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/16) Disable Linux password auth by default | SEC-007 (#47) | #125 — `linux_password_auth`, default false |
| [KAN-014](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/17) Remove plaintext Windows autologon credentials | SEC-008 (#48) | #133 — `Set-LocalUser`, and `DefaultPassword` deleted on second boot |
| [KAN-002](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/18) Baseline quality gates on pull requests | CHORE-002 (#44) | `checks.yml` — actionlint, fmt, validate, tflint, the Python suites, gitleaks |
| [KAN-016](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/20) Automated tests for Arc lifecycle helpers | CHORE-002 (#44) | `test_arc_extractors.py`, `test_arc_registration.py` |
| [KAN-004](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/21) Complete the Azure Arc disable lifecycle | BUG-004 (#43) | #43 — cleanup in the destroy workflow, [arc-cleanup.md](arc-cleanup.md) |
| [KAN-015](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/19) Secure Windows remote administration | — (no audit equivalent) | #147 — Basic over an unencrypted transport is opt-in and off; SEC-008-A5's recorded finding is acted on |
| [KAN-012](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/24) Make network and DNS values environment-driven | — (no audit equivalent) | #154 and #156 — repository variables reach Terraform, and the last hard-coded address is out of the Linux template |

These seven look closable. Read the audit issue's acceptance criteria before
closing the KAN one — they are not always the same scope, and KAN-004 in
particular is narrower than what shipped.

## Same work, still open

| KAN | Audit equivalent | Status |
|---|---|---|
| [KAN-001](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/15) Reconcile actual and declared VM inventories | DOC-001 (#59) | open — needs the real Proxmox inventory |
| [KAN-005](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/23) Move state to a resilient remote backend | FEAT-001 (#56) | open — A1 is decided in [#160](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/pull/160); nothing has moved yet |
| [KAN-006](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/26) Multi-node MicroK8s cluster | FEAT-004 (#62) | open, backlog |
| [KAN-007](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/27) Kubernetes applications with GitOps | FEAT-010 (#68) | open, backlog |
| [KAN-008](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/28) Integrate Azure Monitor | FEAT-005 (#63) | open, backlog |
| [KAN-009](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/29) Azure Policy baseline | FEAT-006 (#64) | open, backlog |
| [KAN-010](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/30) Automate patching with Update Manager | FEAT-008 (#66) | open, backlog |
| [KAN-013](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/31) Operational runbooks | DOC-005 (#73) | done — #128 orphan VMs, #143 lost state, #150 failed Arc onboarding, #158 the release process |

For these the question is which issue survives, not whether the work is done.
The audit issues carry acceptance criteria and evidence requirements; the KAN
issues carry the Kanban metadata. Keeping both means every future change has two
places to update.

## No audit equivalent — these are the ones that would be lost

Four KAN issues described work the audit never covered, and **two of them have
since shipped** — they are in *Delivered* above. That is the argument this file
was making: closing the KAN backlog wholesale would have lost them.

| KAN | What it asks for | Why the audit missed it |
|---|---|---|
| [KAN-011](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/25) Harden WireGuard and the management network | VPN and management-network hardening | The audit scoped itself to the repository — Terraform, workflows, scripts, templates. WireGuard is configured on a guest, not from here. |
| [KAN-017](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/22) Controlled apply and deployment verification | A gate between plan and apply, and a post-apply smoke test | Partly done. Post-apply verification exists (#151, #157) and the gates are documented (#158) — which found that there are none: no branch protection on this plan, and `prod` has no protection rules. A3 and A6 remain. |

KAN-015 was the sharpest of these when this file was written — SEC-008 had
measured the exposure and left it open on purpose, so there was a recorded
finding with no issue tracking the fix. It survived, and #147 acted on it. That
is the case for keeping the other two.

## What happened to it

The recommendation below was carried out on 2026-08-29. Fifteen issues closed:
seven delivered — KAN-002, 003, 004, 013, 014, 016, and DOC-005/006 — and seven
KAN duplicates closed against the audit issue that carries the acceptance
criteria, each with a comment naming the evidence.

**KAN-012 and KAN-015 shipped before the tidy-up rather than being lost in it**,
which was the argument this file was making. KAN-011 and KAN-017 are the two
left in *No audit equivalent*.

Everything still open needs someone at the lab —
[lab-access-required.md](lab-access-required.md) is that list, ordered by what
it unblocks.

## Recommendation

1. Close the five in **Delivered**, each with a comment naming the PR.
2. For **Same work, still open**, keep the audit issue and close the KAN one as a
   duplicate, linking it — the audit issues carry the acceptance criteria.
3. Keep the two left in **No audit equivalent**, and relabel them into the
   audit's scheme so one board describes everything.

The point is not tidiness. Two backlogs mean a finding recorded in one and fixed
in the other looks unaddressed from both.
