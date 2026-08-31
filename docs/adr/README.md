# Decision records

Why this directory exists: the audit (#32) could not tell whether local Terraform
state was a considered trade-off or an accident, because the README recorded
design decisions as three sentences with no reasoning and no alternatives. A
decision nobody can distinguish from an accident cannot be reviewed, and cannot
be revisited on purpose.

DOC-006 (#74) is the issue this serves.

## What counts as a decision record here

Something that says what was chosen, **what was rejected and why**, and what
would change the answer. A record that only states the outcome is documentation;
it is the rejected options that make it reviewable.

Not everything lives in this directory. Some decisions were written where they
are used and are better off there — a numbered ADR that duplicates them would
create a second source of truth, which is worse than an index entry. The table
below is the index; the location column says where the record actually is.

## The records

| Decision | Where | Status |
|---|---|---|
| How secrets reach a guest at first boot | [ADR 0001](0001-guest-secret-delivery.md) | Accepted — options A–E considered, C and D chosen |
| Whether to split first-boot config into static and per-VM documents | [ADR 0002](0002-vendor-data-split.md) | Accepted — **not** split (SPIKE-003) |
| How the two templates get built, and what the factory requires of them | [ADR 0003](0003-template-provenance.md) | Accepted — document then automate, with the specification measured (SPIKE-002) |
| The procedure that satisfies that specification | [../template-build.md](../template-build.md) | Accepted (DOC-002-A3) — written, never walked |
| What happens when Arc cleanup fails during a destroy | [../arc-cleanup.md](../arc-cleanup.md) | Accepted (BUG-004-A4) |
| Why a failed Arc onboarding does not fail anything, and what to do about it | [../incident-arc-onboarding.md](../incident-arc-onboarding.md) | Accepted (BUG-007-A6, DOC-005) |
| Whether a vendor-data change rebuilds a guest | [../guest-config-changes.md](../guest-config-changes.md) | Accepted (BUG-012-A2) |
| What may execute on the lab runner | [../runner-trust-boundary.md](../runner-trust-boundary.md) | Accepted (SEC-004) |
| What gates a push to `main`, measured rather than assumed | [../release-process.md](../release-process.md) | Accepted (KAN-017-A1, A7) |
| Why every dependency is pinned, and how they get updated | [../version-pinning.md](../version-pinning.md) | Accepted (BUG-016, CHORE-006) |
| Why plan output is redacted | [../plan-output-redaction.md](../plan-output-redaction.md) | Accepted (SEC-003) |
| Restoring state, and importing a VM Terraform forgot | [../state-recovery.md](../state-recovery.md) | Exercised (FEAT-001-A4, A5) |
| Where Terraform state lives, and what a remote backend costs | [ADR 0004](0004-terraform-state.md) | Proposed — FEAT-001-A1 |
| Whether the runner should move off the node it manages | [ADR 0005](0005-runner-location.md) | Accepted — stay, and move the state instead (OPS-003-A3) |
| Backing state up, and what a same-disk copy does not buy | [../state-recovery.md](../state-recovery.md) | Accepted (FEAT-001-A3) |

## The one that was missing

State living in a single local file was never decided — it is what the
repository started with, and for a long time writing a record for it would have
been inventing deliberation that did not happen.

[ADR 0004](0004-terraform-state.md) is that decision, made rather than
described. FEAT-001-A3 had already settled where a *backup* goes; 0004 settles
where the state itself should go, and says plainly what it costs — every plan
gains a hard dependency on Azure, which a plan reviewing a comment change does
not have today.

Two things it deliberately leaves open, and both are named there rather than
left for a reader to notice: where the encryption key for the interim step
lives, and whether locking is worth solving on its own given that the
concurrency group already covers the case that has actually happened.

**DOC-006-A2 is unblocked by it.** What is still true meanwhile: losing the
runner loses the state and its backups together, and DOC-001 (#59) should not
import anything until the first step of 0004 §5 is in place. See
[../runner-trust-boundary.md](../runner-trust-boundary.md) for what else that
host holds.

## Adding one

Number it in sequence, and cover: the context, every option considered
including the rejected ones with the reason, the decision, what is explicitly
accepted as residual risk, and what would change the answer. ADR 0001 is the
model — in particular its §5 *"paths explicitly accepted"* and §8 *"what would
change this decision"*, which are the sections that make a record useful a year
later.

Then add a row above.
