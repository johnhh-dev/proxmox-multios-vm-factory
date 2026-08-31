# Azure Arc cleanup: when it runs, and what happens when it fails

Written for BUG-004-A4, which asks for the failure policy to be decided
explicitly rather than inherited. Fold this into the incident runbook when
DOC-005 (#73) lands; until then it is the only written record of the decision.

The opposite failure - a guest that booted and never reached Azure at all - is
[incident-arc-onboarding.md](incident-arc-onboarding.md).

## Why cleanup exists at all

Onboarding a machine to Azure Arc creates a `Microsoft.HybridCompute/machines`
resource in `rg-arc-home-lab`. Terraform does not manage that resource — the
guest creates it by running `azcmagent connect` during first boot — so
destroying the VM does not remove it.

A stale machine resource **blocks re-onboarding under the same name**. The next
create fails inside the guest, long after the workflow that caused it reported
success, and the error says nothing about a destroy that happened days earlier.
That delay is the whole reason this is worth failing a job over.

## Where it runs

| Workflow | Source of names | Covers |
|----------|-----------------|--------|
| `terraform-apply.yml` | the plan (`terraform show -json tfplan`) | VMs a plan is about to destroy or replace |
| `terraform-destroy.yml` | state (`terraform show -json`) | every Arc-enabled VM in state |

Both call `.github/actions/arc-cleanup`, which holds the Azure half once. Before
BUG-004 the apply path had its own copy and the destroy path had none, which is
how the destroy workflow spent months contradicting the README.

Both run **before** the Terraform operation they accompany. Once a VM is gone,
its Arc resource is no longer reachable from state and can only be found by
searching Azure by hand.

## Where the Azure session comes from

`az login`, the subscription selection and the `Microsoft.HybridCompute`
registration are [`az_session.sh`](../.github/scripts/az_session.sh), shared
with the post-apply smoke test (KAN-017-A5). It reports rather than decides:

| Exit | Meaning | What cleanup does | What the smoke test does |
|---|---|---|---|
| 0 | ready | proceed | query Azure |
| 2 | Arc not configured for this lab | **loud skip** — machines are being left behind | "nothing to verify" |
| 1 | configured, and broken | fail the job | fail the job |

The two callers read `2` differently, which is why the script does not decide.

## The failure policy

| Outcome | Verdict |
|---------|---------|
| No Arc credentials configured | Skip, with a `::notice::` saying the resources are being left behind |
| Names file absent or empty | Nothing to do |
| Named resource not present in Azure | `::warning::`, continue |
| Named resource present, delete fails | **Fail the job** |
| Extractor crashes | **Fail the job** |

### Why failing is the right default

The previous code was `az resource delete --ids "$ID" || true`. That reported
success in all three of the last cases, including the one BUG-019 found: a
delete aimed at the wrong name, matching nothing, leaving the real resource
orphaned.

Failing **before** the destroy means a run that cannot clean up Azure destroys
nothing. The lab is left exactly as it was, the operator fixes the Azure-side
problem, and re-runs. The alternative — destroy anyway — trades a failed
workflow for an orphan that surfaces as an unrelated-looking failure at the next
onboarding. A failed workflow is visible, reversible and cheap; an orphan is
none of those.

### Why an absent resource is only a warning

It is legitimately ambiguous. The machine may never have onboarded (first boot
failed, Arc was enabled but the guest never reached Azure), or it may have been
removed by hand, or the name may be wrong. Only the third is a defect, and
Azure cannot tell us which we are looking at.

Since BUG-019 the name comes from `terraform_data.arc_registration` rather than
from the VM name, so the third case is now the least likely of the three — which
is what makes tolerating it reasonable. The warning still names the resource, so
a run full of them is a signal worth chasing.

## Handling of the JSON files

Both `tfplan.json` and `state.json` are secret-bearing, for different reasons:

- **Plan JSON** carries a `variables` block with every input variable in
  cleartext — the Arc service-principal secret, the Proxmox API token, the
  Proxmox root SSH password, both guest admin passwords. `sensitive = true` does
  not redact it, and it is populated even when the plan changes nothing.
  Verified against a real artifact during SEC-002 (#34).
- **State JSON** has no `variables` block, but holds `source_raw.data` for every
  `proxmox_virtual_environment_file.user_data` — the fully rendered cloud-init
  snippet, with the same credentials inside it. The exposure therefore scales
  with the number of managed VMs, rather than being unconditional.

Neither is ever uploaded as an artifact. Both are deleted immediately after the
extractor has read them, and again in an `always()` step so a cancelled run
leaves nothing on the self-hosted runner.

`arc_delete_names.txt` holds only Arc machine names and is safe to keep.

SEC-001 (#46) closes the state-JSON exposure. It does not close the plan-JSON
one, which is a property of `terraform show -json` itself.

## Recovering from an orphan

If a destroy has already left one behind:

```bash
az login --service-principal -u "$ARC_SP_ID" -p "$ARC_SP_SECRET" --tenant "$ARC_TENANT_ID"
az account set --subscription "$ARC_SUBSCRIPTION_ID"

# What is actually there
az resource list -g rg-arc-home-lab --resource-type Microsoft.HybridCompute/machines -o table

az resource delete --ids "/subscriptions/$ARC_SUBSCRIPTION_ID/resourceGroups/rg-arc-home-lab/providers/Microsoft.HybridCompute/machines/<name>"
```

Compare that listing against `terraform output -json vm_inventory` — anything in
Azure with no corresponding VM is an orphan.
