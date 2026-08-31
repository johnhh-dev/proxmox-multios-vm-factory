# BUG-019. Arc cleanup used to delete the Azure resource named after the Proxmox
# VM. That is only the default. locals.tf sets
#
#   resource_name = coalesce(try(local.arc_input[name].resource_name, null), name)
#
# so a VM written as `arc = { enabled = true, resource_name = "..." }` onboards
# under that name instead - and the override exists precisely so the two need not
# match. Where they diverged, `az resource delete` targeted a name that does not
# exist, `|| true` swallowed the failure, the workflow reported success, and the
# real Arc machine was left behind to block the next onboarding under the same
# name.
#
# Both extractors read Terraform's own JSON, and that JSON describes resources,
# not locals - so for the mapping to be visible to them it has to be a resource.
# This is that resource: one no-op marker per Arc-enabled VM carrying the pair
# the cleanup needs. It costs one state entry per VM and no API call.
#
# `each.key` is the VM name, not merely its inventory key: vms_normalized in
# locals.tf sets `name = name` from the same key, so the two cannot drift.
#
# Deliberately no `triggers_replace` and no dependency on the VM. The marker is a
# lookup table, not a lifecycle hook - the extractors decide *what* is being
# removed from the VM resources themselves and consult this only for the name.
# Tying its lifecycle to the VM's would add churn and an unknown value at plan
# time without changing any answer.
resource "terraform_data" "arc_registration" {
  for_each = {
    for name, vm in local.vms_final : name => vm.arc.resource_name
    if vm.arc.enabled
  }

  input = {
    vm_name       = each.key
    resource_name = each.value
  }
}
