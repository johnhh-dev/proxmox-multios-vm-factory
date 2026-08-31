# What happens when you push to `main`

KAN-017 (#22), activities A1 and A7. Nobody had written this down, and the
useful half turned out to be the part that is *not* true.

## The short version

**A push to `main` reaches the lab.** No human approves it, no reviewer is
required, and nothing prevents pushing straight to `main` rather than through a
pull request. The gates that do exist are correctness gates, not approval gates:
they stop a *bad* apply, not an *unreviewed* one.

## The sequence

| # | Step | If it fails |
|---|---|---|
| 1 | `checks.yml` — actionlint, `fmt`, `validate`, `tflint`, the Python suites, gitleaks | the run is red; the apply runs anyway, they are separate workflows |
| 2 | `terraform-apply.yml` starts on push to `main` | — |
| 3 | Joins the `terraform-lab-state` concurrency group | queues behind a running apply or destroy — **not** behind a pull request's plan; see the gate table |
| 4 | State directory asserted present and writable | fails before Terraform runs |
| 5 | `terraform init` | fails |
| 6 | **State backed up** to `backups/`, verified byte-identical (FEAT-001-A3) | fails the run |
| 7 | **Cluster preflight** — the cluster can accept writes (BUG-024) | fails before the clone |
| 8 | **Inventory guard** — desired inventory against state (BUG-002) | blocks |
| 9 | `terraform plan -out=tfplan` | fails |
| 10 | Arc cleanup for VMs the plan will destroy (BUG-004) | fails **before** the apply, so nothing is destroyed |
| 11 | `terraform apply` | fails, possibly half-applied |
| 12 | **Post-apply smoke tests** (KAN-017-A5) | fails the run — after the fact |
| 13 | **Convergence check** — a plan straight after the apply must be empty | fails the run — after the fact |
| 14 | Working files removed from the runner | — |

Steps 12 and 13 report rather than prevent, and say so in their own failure
messages — a red run there does not mean the apply failed to land. **They also
all run**: a failing smoke test no longer hides whether first boot completed or
whether the configuration converged, so one apply reports everything wrong with
it. They are conditioned on the apply having succeeded, so a failed apply
produces one failure rather than three. Step 13 is
the one that would have caught BUG-012, where every push to `main` destroyed and
recreated the lab's guests for months without anything noticing.

Steps 6, 7, 8 and 10 all fail *before* anything is touched. That is deliberate,
and it is the shape the repository keeps choosing: a failed workflow is visible,
reversible and cheap; a half-built lab is none of those.

## The gates, measured rather than assumed (A1)

Checked against the repository's actual settings on 2026-08-29, not against what
the workflows imply.

| Control | Configured? | Evidence |
|---|---|---|
| Branch protection on `main` | **No, and not available** | `GET /branches/main/protection` → `403 Upgrade to GitHub Pro or make this repository public` |
| Required reviewer on a pull request | **No** | same |
| Required status checks before merge | **No** | same |
| `prod` environment exists | Yes | `GET /environments` |
| `prod` **approval** before apply | **No** | that environment's `protection_rules` is `[]` |
| `prod` wait timer | **No** | same |
| `prod` deployment branch policy | **No** | `deployment_branch_policy: null` |
| Apply and destroy cannot mutate state at once | **Yes** | `concurrency: terraform-lab-state`, `cancel-in-progress: false`, shared by those two workflows |
| A **plan** cannot run while an apply does | **Not by that group** — `terraform-plan.yml` has never been in it. What serialises them is that `gha-runner-01` is one runner and takes one job at a time, which is a fact about the lab rather than about this repository. `-lock-timeout=10m` on every locking command is the half that does not depend on it (KAN-017-A2) |
| A docs-only commit reaches the lab | **No** | `paths-ignore` on `**/*.md`, `docs/**`, `LICENSE` |
| A fork's pull request is planned against the lab | **No** | `terraform-plan.yml`'s `if:` on `head.repo.full_name`, plus the "require approval for all external contributors" repository setting |
| Destroy needs explicit confirmation | **Yes** | `workflow_dispatch` input, must be the literal `DESTROY` |
| Workflow token can write to the repository | **No** | `permissions: contents: read` at the top of all four |

**`environment: prod` is doing less than it looks like.** It scopes the secrets
and produces a deployment record. It does not gate anything: with no protection
rules, the job does not wait for anyone. Reading `environment: prod` in a
workflow as "an approval happens here" is the mistake this table exists to
prevent.

### What it would take to have a real approval gate

Deployment protection rules on a **private** repository need GitHub Pro, Team or
Enterprise. Two ways out, and neither is free:

1. **Make the repository public.** Environments, required reviewers and branch
   protection all become available. Read
   [runner-trust-boundary.md](runner-trust-boundary.md) first — a public
   repository changes who can open a pull request that a maintainer might run
   against a self-hosted runner holding the Proxmox and Arc credentials.
2. **Upgrade the plan.** Then, in order: a required reviewer on `prod`; branch
   protection on `main` requiring a pull request and the `checks` status;
   `deployment_branch_policy` restricting `prod` to `main`.

Until one of those happens, **the approval step in any description of this
pipeline is a person choosing to open a pull request**, not a control.

## The reviewed plan is not the applied plan (A3)

`terraform-plan.yml` plans the pull request. `terraform-apply.yml` runs its
**own** `terraform plan -out=tfplan` on `main` and applies that. They are
different runs against state that may have moved in between.

So "apply only the approved commit and plan" is **not satisfied**, and it is
worth being clear that this is a design question rather than missing wiring. A
saved plan is only valid against the state it was created from; Terraform
refuses to apply one whose state has moved. Handing a plan artifact from the
pull-request job to the apply job would introduce staleness where there is
currently none — every apply would need the plan re-made after any intervening
change, which for a repository whose apply is triggered by merging is most of
them.

What the current design gives instead: the applied plan is always fresh, always
against the state it will write, and always printed in the job log. What it
costs: nobody has approved *that* plan. A3 is open and this is the trade-off it
has to decide.

## Routine release (A7)

1. Branch, change, open a pull request.
2. `checks.yml` and `terraform-plan.yml` run. **Read the plan.** It is the only
   review of what will happen to the lab, and it is not the plan that will be
   applied — see above.
3. Merge to `main`. The apply starts on the push.
4. **Watch it.** No one is notified if it fails.
5. Check the post-apply smoke test output. Inventory and guest availability fail
   the run; an Arc machine that did not appear is a `::warning::` and will not.

A commit touching only Markdown, `docs/` or `LICENSE` **does not apply**. That
is deliberate, and it means `main` can describe VMs that were never built. An
empty commit or a manual `workflow_dispatch` is how to make it converge.

## Emergency change

**To apply without a push:** `terraform-apply.yml` → Run workflow. It takes no
inputs, because it converges the lab on what `main` already says. It is not a
way around anything — the same concurrency group, the same `prod` environment,
the same inventory guard.

**After a destroy**, one check runs where there used to be none: no Proxmox
resource may remain in state. It fails rather than warns, because unlike an Arc
machine that may still be onboarding, a resource left in state after a destroy
is not ambiguous. What it cannot see is said in the run: **state is the record,
not the node** — a VM the destroy failed to remove *and* dropped from state
would pass, and that is precisely the orphan.

**To tear the lab down:** `terraform-destroy.yml` → Run workflow, type `DESTROY`.
Arc cleanup runs *before* the destroy, and a failed cleanup fails the job with
nothing destroyed — see [arc-cleanup.md](arc-cleanup.md).

**To stop applies entirely:** there is no switch. Disable the workflow in the
Actions tab, or revert the commit. A run already talking to Proxmox is never
cancelled mid-flight (`cancel-in-progress: false`), because a half-applied plan
whose state was never written is the orphan case
([incident-orphan-vm.md](incident-orphan-vm.md)).

## Evidence retention (A7)

| Kept | Where | For |
|---|---|---|
| Job logs, including the full plan text | Actions | GitHub's retention |
| `arc_delete_names.txt` | `apply-debug-<run-id>` artifact | 90 days |
| State backups | `backups/` on the runner, last 20 | until pruned |
| Deployment records for `prod` | Actions → Deployments | — |

**Deliberately not kept:** `tfplan` and `tfplan.json`. `terraform show -json`
emits a `variables` block holding every input in cleartext — the Proxmox API
token, the node SSH credential, both guest passwords and the Arc service
principal — and `sensitive = true` does not redact it. Both are deleted from the
runner workspace whether the run succeeded or failed. Do not add them to an
artifact.

The plan *text* in the job log is safe, and is redacted where it matters:
`sensitive()` on the rendered snippet keeps the guest configuration out of it
([plan-output-redaction.md](plan-output-redaction.md)).

## When it goes wrong

| Symptom | Runbook |
|---|---|
| A VM exists in Proxmox and not in state | [incident-orphan-vm.md](incident-orphan-vm.md) |
| State lost, truncated, or a bad apply to undo | [state-recovery.md](state-recovery.md) |
| A guest booted and never reached Azure | [incident-arc-onboarding.md](incident-arc-onboarding.md) |
| An Arc machine left behind after a destroy | [arc-cleanup.md](arc-cleanup.md) |
| A 401 from the Proxmox API | [proxmox-api-token.md](proxmox-api-token.md) |

## Not covered

**A6 — testing these paths.** Approval denial, a stale plan, two concurrent
runs, a failed apply and a rollback have not been exercised. Two of them cannot
be, because there is no approval to deny and no plan artifact to go stale. The
concurrency group and a failed apply can be, and have not been.

**A3 — the plan artifact**, above.

Neither is a documentation gap; both need the lab.
