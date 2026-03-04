check "vm_factory_config" {
  assert {
    condition     = length(local.validation_errors) == 0
    error_message = join("\n", local.validation_errors)
  }
}
