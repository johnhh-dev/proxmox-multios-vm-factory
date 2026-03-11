locals {
  # ------------------------------------------------------------
  # VM Factory Inventory (edit this)
  # ------------------------------------------------------------
  # Per-VM options:
  #   os:      "linux" | "windows"
  #   network: { type = "dhcp" } OR { type="static", address="x/y", gateway="x" }
  #   arc:     false | true | { enabled=true, resource_name="...", tags=string|map }
  #
  # Example:
  # vms = {
  #   ubuntu-static-01 = {
  #     os        = "linux"
  #     cores     = 2
  #     memory_mb = 4096
  #     network = {
  #       type    = "static"
  #       address = "192.168.10.30/24"
  #       gateway = "192.168.10.1"
  #     }
  #     arc = true
  #   }
  #
  #   win-dhcp-01 = {
  #     os        = "windows"
  #     cores     = 4
  #     memory_mb = 8192
  #     network   = { type = "dhcp" }
  #     arc       = false
  #   }
  # }
  vms = {


   /*ubuntu-static-01 = {
       os        = "linux"
       cores     = 2
       memory_mb = 4096
       network = {
         type    = "static"
         address = "192.168.10.10/24"
         gateway = "192.168.10.1"
       }
       arc = true
     }*/

    win-srv-01 = {
        os        = "windows"
        cores     = 4
        memory_mb = 8192
        network = {
          type = "dhcp"
        }
        
        arc = true
      }




  }
  # ------------------------------------------------------------

  # Profiles (optional): set per-VM by profile="small|medium|large"
  profiles = {
    small  = { cores = 2, memory_mb = 4096 }
    medium = { cores = 4, memory_mb = 8192 }
    large  = { cores = 8, memory_mb = 16384 }
  }

  # OS defaults
  os_defaults = {
    linux = {
      template_vmid = var.template_vmid_linux
      user_data_tpl = "${path.module}/cloudinit/linux.yaml.tftpl"
    }
    windows = {
      template_vmid = var.template_vmid_windows
      user_data_tpl = "${path.module}/cloudinit/windows.yaml.tftpl"
    }
  }

  # Global defaults
  vm_defaults = {
    os      = "windows"
    profile = null

    cores     = 2
    memory_mb = 4096

    network = {
      type    = "dhcp"
      address = null
      gateway = null
    }

    # Windows optional knobs (template-dependent)
    windows = {
      admin_password = var.windows_admin_password
      enable_winrm   = var.windows_enable_winrm_default
    }

    # Azure Arc
    # tags can be string "k=v,k=v" OR map {k="v"}
    arc = {
      enabled       = var.arc_enabled_default
      resource_name = null
      tags          = null
    }
  }

  # Normalize Arc input: allow arc as bool or object (or omitted)
  arc_input = {
    for name, vm in local.vms : name => (
      try(vm.arc, null) == null ? {} :
      # Always return an object (avoid conditional type mismatch when arc is bool vs object)
      try(
        {
          enabled       = tobool(vm.arc.enabled)
          resource_name = try(vm.arc.resource_name, null)
          tags          = try(vm.arc.tags, null)
        },
        { enabled = tobool(vm.arc) }
      )
    )
  }

  # Normalize VMs to a stable schema for resources/templates
  vms_normalized = {
    for name, vm in local.vms : name => merge(
      local.vm_defaults,
      vm,
      # profile -> cores/memory defaults
      try(local.profiles[vm.profile], {}),
      # os defaults (template + userdata)
      lookup(local.os_defaults, try(vm.os, local.vm_defaults.os), local.os_defaults[local.vm_defaults.os]),
      {
        name = name
        network = merge(local.vm_defaults.network, try(vm.network, {}))
        windows = merge(local.vm_defaults.windows, try(vm.windows, {}))

        arc = merge(
          local.vm_defaults.arc,
          try(local.arc_input[name], {}),
          {
            # default resource_name to VM key if Arc enabled and not specified
            resource_name = coalesce(
              try(local.arc_input[name].resource_name, null),
              name
            )
          }
        )
      }
    )
  }

  # Normalize tags to the Azure Arc CLI format:
  #   "k=v,k=v" (no spaces)
  # If tags is a map, convert to "k=v,k=v" sorted by key.
  vms_with_arc_tags = {
    for name, vm in local.vms_normalized : name => merge(vm, {
      arc = merge(vm.arc, {
        tags_string = (
          vm.arc.tags == null ? "" :
          can(tostring(vm.arc.tags)) ? trimspace(tostring(vm.arc.tags)) :
          join(",", [for k in sort(keys(vm.arc.tags)) : "${k}=${tostring(vm.arc.tags[k])}"])
        )
      })
    })
  }

  # The map used by resources
  vms_final = local.vms_with_arc_tags

  # ------------------------------------------------------------
  # Validation (friendly errors)
  # ------------------------------------------------------------
  
# ------------------------------------------------------------
# Safe helpers (avoid null/type issues during validation)
# ------------------------------------------------------------
profile_safe = {
  for k, v in local.vms_final :
  k => (try(v.profile, null) == null ? "__unset__" : tostring(v.profile))
}

arc_tags_map_safe = {
  for k, v in local.vms_final :
  k => (
    try(v.arc.tags, null) != null && can(keys(v.arc.tags))
    ? v.arc.tags
    : {}
  )
}

validation_errors = flatten([
    # invalid OS
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': invalid os '%s' (must be one of: %s)",
        k,
        v.os,
        join(", ", keys(local.os_defaults))
      )
      if !contains(keys(local.os_defaults), v.os)
    ],

    # invalid profile (if set)
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': invalid profile '%s' (must be one of: %s)",
                k,
        local.profile_safe[k],
        join(", ", keys(local.profiles))
      )
      if local.profile_safe[k] != "__unset__" && !contains(keys(local.profiles), local.profile_safe[k])
    ],

    # invalid network type
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': invalid network.type '%s' (must be 'dhcp' or 'static')",
        k,
        v.network.type
      )
      if !contains(["dhcp", "static"], v.network.type)
    ],

    # static requires address/gateway
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': network.type=static requires network.address and network.gateway",
        k
      )
      if v.network.type == "static" &&
         (try(v.network.address, null) == null || try(v.network.gateway, null) == null)
    ],

    # arc.tags hardening: disallow newline and double-quote (applies to both string and map forms via tags_string)
    [
      for k, v in local.vms_final :
      format("VM '%s': arc.tags contains invalid characters (newline or quote)", k)
      if strcontains(v.arc.tags_string, "\n") || strcontains(v.arc.tags_string, "\"")
    ],
    # arc.tags hardening for map form: disallow ',' or '=' in keys/values (breaks CLI tag parsing)
    [
      for k, v in local.vms_final :
      format("VM '%s': arc.tags map keys/values may not contain ',' or '='", k)
      if length([
        for tk, tv in local.arc_tags_map_safe[k] : 1
        if strcontains(tk, ",") ||
           strcontains(tk, "=") ||
           strcontains(tostring(tv), ",") ||
           strcontains(tostring(tv), "=")
      ]) > 0
    ]
  ])
}
