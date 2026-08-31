# The keys below are a contract. `.github/scripts/postapply_smoke.py` reads
# ip_observed, arc_enabled and arc_resource_name from this output, and
# arc_missing.py reads the same pair through it - so removing or renaming one
# does not break a build, it changes what the post-apply check verifies.
#
# For the arc_* pair that change is silent in the worst direction: an absent key
# makes the expected set empty, which reads as "no VM asks for onboarding". The
# consumers now refuse a document missing any of the three rather than treating
# it as an absent value, so this comment is a pointer rather than the only
# thing holding the contract together.
output "vm_inventory" {
  description = "Normalized VM inventory used by Terraform, and the contract the post-apply checks read."
  value = {
    for k, v in local.vms_final : k => {
      name      = v.name
      os        = v.os
      cores     = v.cores
      memory_mb = v.memory_mb
      # What was asked for. Kept, because a plan-time reader wants the declared
      # value and because ip_observed is null until the guest is up.
      ip = v.network.type == "dhcp" ? "dhcp" : v.network.address

      # FEAT-002-A4. What the guest actually has, read back through the agent
      # enabled in main.tf. Before that agent existed this output could only
      # ever echo the configuration - so a DHCP guest reported the literal
      # string "dhcp", which is the one thing an operator asking "what is its
      # address" already knows.
      #
      # First non-loopback IPv4, or null. The provider returns one list per
      # interface, an interface can have no address while the guest is still
      # booting, and lo is always present and never the answer.
      ip_observed = try(
        [
          for addrs in proxmox_virtual_environment_vm.vm[k].ipv4_addresses :
          addrs[0]
          if length(addrs) > 0 && addrs[0] != "127.0.0.1"
        ][0],
        null
      )

      arc_enabled = v.arc.enabled

      # KAN-017-A5. What this machine is called in Azure, which is not
      # necessarily what it is called here - arc.resource_name overrides it, and
      # BUG-019 exists because the two diverging silently orphaned a machine.
      # Null when Arc is off, so the value cannot be read as a claim that a
      # machine should exist.
      #
      # It is in the output rather than only in terraform_data.arc_registration
      # because reading that marker means `terraform show -json`, which emits
      # every input variable in cleartext (SEC-002). `terraform output -json`
      # emits only declared outputs, and none of these is a secret.
      arc_resource_name = v.arc.enabled ? v.arc.resource_name : null

      # OPS-004. The ID Proxmox actually assigned, read back off the resource
      # rather than from the inventory - `vm_id` is null for a VM that let
      # Proxmox choose, and the post-apply first-boot check needs an address it
      # can call the guest agent on.
      vm_id_actual = proxmox_virtual_environment_vm.vm[k].vm_id
    }
  }
}
