resource "proxmox_virtual_environment_file" "user_data" {
  for_each = local.vms_final

  node_name    = var.proxmox_node_name
  datastore_id = var.snippets_datastore
  content_type = "snippets"
  overwrite    = true

  source_raw {
    data = templatefile(each.value.user_data_tpl, {
      hostname       = each.value.name
      fqdn           = "${each.value.name}.${var.search_domain}"
      plain_password = coalesce(var.linux_vm_password, "")

      # Windows (template-dependent; optional)
      windows_admin_password = coalesce(var.windows_admin_password, "")
      windows_enable_winrm   = each.value.windows.enable_winrm

      # Azure Arc (optional; enabled per-VM via locals.tf)
      arc_enabled            = each.value.arc.enabled
      arc_resource_name      = each.value.arc.resource_name
      arc_tags               = each.value.arc.tags_string
      arc_cloud              = var.arc_cloud
      arc_install_script_url = var.arc_install_script_url
      arc_tenant_id          = var.arc_tenant_id
      arc_subscription_id    = var.arc_subscription_id
      arc_resource_group     = var.arc_resource_group
      arc_location           = var.arc_location
      arc_sp_id              = var.arc_sp_id
      arc_sp_secret          = var.arc_sp_secret
    })

    file_name = "${each.value.name}-user-data.yaml"
  }
}

resource "proxmox_virtual_environment_vm" "vm" {
  for_each  = local.vms_final
  name      = each.value.name
  node_name = var.proxmox_node_name

  clone {
    vm_id = each.value.template_vmid
  }

  cpu {
    cores = each.value.cores
  }

  memory {
    dedicated = each.value.memory_mb
  }

  network_device {
    bridge = var.bridge
  }

  initialization {
    dynamic "user_account" {
      for_each = each.value.os == "linux" ? [1] : []
      content {
        username = "ubuntu"
        keys     = [var.ssh_public_key]
      }
    }

    dns {
      servers = [var.dns_server]
      domain  = var.search_domain
    }

    ip_config {
      ipv4 {
        address = each.value.network.type == "dhcp" ? "dhcp" : each.value.network.address
        gateway = each.value.network.type == "dhcp" ? null   : each.value.network.gateway
      }
    }

    user_data_file_id = proxmox_virtual_environment_file.user_data[each.key].id
  }
}
