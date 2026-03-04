output "vm_inventory" {
  description = "Normalized VM inventory used by Terraform."
  value = {
    for k, v in local.vms_final : k => {
      name       = v.name
      os         = v.os
      cores      = v.cores
      memory_mb  = v.memory_mb
      ip         = v.network.type == "dhcp" ? "dhcp" : v.network.address
      arc_enabled = v.arc.enabled
    }
  }
}
