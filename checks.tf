# BUG-001: this was a `check` block. Terraform `check` assertions are advisory -
# a failed assertion prints a warning, leaves the exit code at 0 and lets the
# apply proceed. Every rule in local.validation_errors was therefore a comment
# with extra steps: a VM written as `os = "Linux"` warned, fell through the
# lookup in locals.tf to the Windows defaults, and was cloned from the Windows
# template.
#
# A resource precondition is the blocking form. It is evaluated while the plan
# is generated, so an invalid inventory fails `terraform plan` with a non-zero
# exit code - before review, not after - and the error carries the same
# messages, naming the offending VM and what is wrong with it.
#
# terraform_data is the built-in no-op resource, so the guard costs one state
# entry and no API call. It deliberately has no `input` and no dependency on any
# VM: preconditions are evaluated for a resource whether or not it has changes,
# so the whole inventory is re-checked on every plan, including a plan that
# changes nothing. The rules themselves are unchanged and live in locals.tf.
resource "terraform_data" "vm_factory_config" {
  lifecycle {
    precondition {
      condition     = length(local.validation_errors) == 0
      error_message = join("\n", local.validation_errors)
    }
  }
}
