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
        address = "192.168.10.20/24"
        gateway = "192.168.10.1"
      }
      arc = true
    }*/

    /*ubuntu-testdhcp-01 = {
      os        = "linux"
      cores     = 2
      memory_mb = 4096
      network = {
        type = "dhcp"
      }
      arc = true
    }*/


    /*win-srv-01 = {
      os        = "windows"
      cores     = 4
      memory_mb = 8192
      network = {
        type = "dhcp"
      }
      arc = true
    }*/


  }
  # ------------------------------------------------------------

  # Profiles (optional): set per-VM by profile="small|medium|large".
  #
  # A profile is shorthand for a set of defaults, not an override. Anything the
  # VM states for itself wins over the profile it names, so `profile = "small"`
  # together with `cores = 8` gives eight cores. The full order is the table on
  # vms_normalized below (BUG-005).
  profiles = {
    small  = { cores = 2, memory_mb = 4096 }
    medium = { cores = 4, memory_mb = 8192 }
    large  = { cores = 8, memory_mb = 16384 }
  }

  # OS defaults
  os_defaults = {
    linux = {
      template_vmid   = var.template_vmid_linux
      vendor_data_tpl = "${path.module}/cloudinit/linux.yaml.tftpl"
    }
    windows = {
      template_vmid   = var.template_vmid_windows
      vendor_data_tpl = "${path.module}/cloudinit/windows.yaml.tftpl"
    }
  }

  # FEAT-009-A2. The disk size each template carries, keyed the same way as
  # os_defaults so the validation rule can look it up by the VM's `os`.
  #
  # Declared rather than discovered: the real size lives in Proxmox, and
  # reading it would mean an API call during plan. Both default to null, which
  # disables the shrink rule - a floor invented in this file would block
  # legitimate sizes and give false confidence about illegitimate ones. Set
  # them from `qm config <template-vmid>` and the rule starts working.
  template_disk_gb = {
    linux   = var.template_disk_gb_linux
    windows = var.template_disk_gb_windows
  }

  # ------------------------------------------------------------
  # Node SSH identity (SEC-006-A3)
  # ------------------------------------------------------------
  # One definition, read by both providers.tf and the validation rule below, so
  # the connection the provider makes and the configuration the rule approves
  # cannot disagree. BUG-018 is what happens when they do.
  #
  # The normalisation is not cosmetic, and the tests found it rather than the
  # author predicting it. An unset GitHub secret arrives as TF_VAR_...="", which
  # is an empty *string* and not null - and the provider rejects that outright:
  #
  #   Error: expected "ssh.0.password" to not be an empty string, got
  #
  # So a lab that moved to a key and left the password secret in place, empty,
  # would fail every plan with a message about a field it deliberately is not
  # using. Empty becomes null here, once, and null is the same as not writing
  # the line at all.
  ssh_password = trimspace(var.proxmox_ssh_password == null ? "" : var.proxmox_ssh_password) == "" ? null : var.proxmox_ssh_password

  ssh_private_key = trimspace(var.proxmox_ssh_private_key == null ? "" : var.proxmox_ssh_private_key) == "" ? null : var.proxmox_ssh_private_key

  ssh_agent_socket = trimspace(var.proxmox_ssh_agent_socket == null ? "" : var.proxmox_ssh_agent_socket) == "" ? null : var.proxmox_ssh_agent_socket

  # ------------------------------------------------------------
  # Resolvers (BUG-018)
  # ------------------------------------------------------------
  # One list, and the only answer in this configuration to "which resolvers
  # does a guest use". It reaches a guest twice: through
  # initialization.dns.servers on the VM resource, and - on Windows - through
  # the first-boot script, which sets DNS itself because Cloudbase-Init has not
  # applied the cloud-init network configuration by the time Arc onboarding
  # needs to resolve aka.ms.
  #
  # Those two used to disagree. The VM resource followed var.dns_server while
  # windows.yaml.tftpl carried @("192.168.10.2","192.168.10.1") at four sites,
  # so changing var.dns_server moved the Linux guests and left the Windows ones
  # on the old resolver. The failure is quiet - DNS still works, just not the
  # DNS that was configured - and it surfaces much later as an Arc onboarding
  # failure whose message names an address nobody set.
  dns_servers = concat([var.dns_server], var.dns_servers_fallback)

  # The same list as the body of a PowerShell array literal, so the Windows
  # template interpolates one value in one place instead of spelling addresses
  # out at each call site. jsonencode per element rather than a bare join: it is
  # the quoting that makes each element a PowerShell string, and it is not this
  # file's business to assume an address never needs escaping.
  dns_servers_ps = join(",", [for s in local.dns_servers : jsonencode(s)])

  # KAN-011-A3. The management sources as the body of a PowerShell array
  # literal, rendered here for the same reason and in the same way as the
  # resolvers above: one interpolation in the template, and jsonencode per
  # element so the quoting is not this file's guess.
  #
  # Empty stays empty rather than becoming @() with a placeholder, because the
  # template branches on the count and an empty literal is the honest input for
  # "no restriction was configured".
  management_sources_ps = join(",", [for s in var.management_source_cidrs : jsonencode(s)])

  # Global defaults
  vm_defaults = {
    os      = "windows"
    profile = null

    cores     = 2
    memory_mb = 4096

    # FEAT-002-A1. Null means "let Proxmox pick the next free ID", which is what
    # this factory did for every VM until now - and the reason the README's IDs
    # 100-104 were not reproducible from this configuration. Setting it makes
    # the ID part of the declared inventory.
    #
    # It stays optional rather than becoming required because DOC-001 (#59) is
    # what decides which VM gets which ID, and that decision needs the real
    # Proxmox inventory in front of it. Until then an unset vm_id behaves
    # exactly as before.
    #
    # Read the migration note in main.tf before setting this on a VM that
    # already exists: a vm_id that disagrees with the one the VM has is a
    # replacement, not a relabel.
    vm_id = null

    # FEAT-009-A1. Null means "keep whatever the template carries", which is
    # what every VM did before - main.tf declared no disk block at all, so disk
    # size was not expressible in the inventory.
    #
    # Opt-in on purpose: with this unset, main.tf emits no disk block and the
    # VM resource is byte for byte what it was. Measured against a plan with a
    # disk already in state - no diff. That matters because the alternative,
    # always declaring a disk block, would make this factory start managing a
    # disk it did not create on every VM that already exists.
    #
    # Read the two notes in main.tf before setting it. Growing is an in-place
    # resize; shrinking plans clean and then fails at the hypervisor.
    disk_gb = null

    # Which disk to resize. It must be the interface the *template's* existing
    # disk uses, or Proxmox is being asked to add a second disk rather than
    # grow the first - see the note in main.tf.
    disk_interface = "scsi0"

    network = {
      type    = "dhcp"
      address = null
      gateway = null
    }

    # Windows optional knobs (template-dependent)
    windows = {
      admin_password = var.windows_admin_password
      enable_winrm   = var.windows_enable_winrm_default

      # KAN-015. Per-VM for the same reason enable_winrm is: one guest that has
      # a reason to need the old transport should not make that decision for
      # the whole repository. Defaults from the variable, which is false, so a
      # VM that says nothing gets Negotiate.
      winrm_allow_unencrypted = var.windows_winrm_allow_unencrypted_default

      # SEC-001c-A4. Per VM for the same reason the two above are: one guest
      # that genuinely wants a console session should not put the administrator
      # password in every other guest's registry.
      autologon = var.windows_autologon_default
    }

    # `arc` has no entry here on purpose. local.arc_input below is total - it
    # produces the complete Arc object for every VM, var.arc_enabled_default
    # included - and the merge in vms_normalized always overrides whatever sits
    # here. A second, differently shaped Arc default would only be one more
    # shape for Terraform to reconcile (BUG-006).
  }

  # ------------------------------------------------------------
  # Azure Arc input normalization (BUG-006)
  # ------------------------------------------------------------
  # `arc` may be omitted, a bool, or an object, and `arc.tags` may be a string
  # "k=v,k=v" or a map {k="v"}. Those four forms are the input. This is the one
  # shape the rest of the configuration ever sees:
  #
  #   { enabled = bool, resource_name = string, tags_map = map(string), tags_string = string }
  #
  # Every VM must land on that same shape here, because local.vms_final is built
  # by a `for` expression: `for ... => ...` builds a map, a map has exactly one
  # element type, and Terraform has to unify the object types of every element
  # to find it. The previous expression returned `{}` for an omitted `arc`,
  # `{enabled}` for a bool and `{enabled, resource_name, tags}` for an object,
  # so it failed on any inventory mixing the bool and object forms - the exact
  # mix the README and the worked example at the top of this file recommend -
  # and on any object-form `arc` whose attributes were not all the same type.
  #
  # `tags` is the same trap one level down: a string on one VM and a map on
  # another do not unify either. Both forms are read here and neither leaves
  # this block; downstream sees only the rendered string and an always-map.

  # A map for every VM, whichever form `tags` was written in. A string-form or
  # absent `tags` yields an empty map rather than an error, so the shape holds.
  arc_tags_map = {
    for name, vm in local.vms : name => try(tomap(vm.arc.tags), {})
  }

  # Tags in the format the Azure Arc CLI wants: "k=v,k=v", no spaces. The map
  # form is rendered sorted by key so the string is stable across plans; the
  # string form is passed through as written.
  arc_tags_string = {
    for name, vm in local.vms : name => (
      length(local.arc_tags_map[name]) > 0
      ? join(",", [for k in sort(keys(local.arc_tags_map[name])) : "${k}=${local.arc_tags_map[name][k]}"])
      : trimspace(try(tostring(vm.arc.tags), ""))
    )
  }

  arc_input = {
    for name, vm in local.vms : name => {
      # Reading `.enabled` off a bool fails, converting an object to bool fails,
      # and both fail when `arc` was omitted - so exactly one of these three
      # succeeds for any given input form.
      enabled = try(
        tobool(vm.arc.enabled),
        tobool(vm.arc),
        var.arc_enabled_default,
      )

      # Default the Arc resource name to the VM key, both when the attribute is
      # absent (the `try` fallback) and when it is written out as null (the
      # `coalesce`).
      resource_name = coalesce(try(tostring(vm.arc.resource_name), null), name)

      tags_map    = local.arc_tags_map[name]
      tags_string = local.arc_tags_string[name]
    }
  }

  # Normalize VMs to a stable schema for resources/templates
  #
  # BUG-005. `merge` takes the rightmost occurrence of a key, so the argument
  # order is the precedence rule. This used to read defaults -> vm -> profile,
  # which put the profile to the right of the values the author wrote by hand
  # and let it win: a VM declaring `profile = "small"` and `cores = 8` was
  # provisioned with two cores. Nothing about that is an error, so nothing said
  # so - not the plan, not the guest, only the VM's resource graph in Proxmox
  # weeks later. A default that overrides an explicit value is not a default.
  #
  # Precedence, weakest to strongest:
  #
  #   1. local.vm_defaults    global defaults
  #   2. local.profiles[...]  the named profile, when the VM sets one
  #   3. the VM's own keys    what the author wrote for this VM
  #   4. local.os_defaults    template_vmid and vendor_data_tpl
  #   5. the computed block   name, network, windows, arc
  #
  # BUG-005-A2: layers 4 and 5 sit to the *right* of the VM on purpose. Both are
  # derived rather than configured, and `template_vmid` and `vendor_data_tpl`
  # follow from `os` and have to keep following it - otherwise a VM could pair
  # `os = "linux"` with a Windows `template_vmid` and be cloned from the wrong
  # image, with the Linux cloud-init, and no rule in validation_errors to catch
  # the pairing. Moving the VM to the far right would read tidier as "explicit
  # always wins" and would break that binding, which is why the rule is stated
  # here rather than left to be inferred from the argument order.
  vms_normalized = {
    for name, vm in local.vms : name => merge(
      local.vm_defaults,
      try(local.profiles[vm.profile], {}),
      vm,
      lookup(local.os_defaults, try(vm.os, local.vm_defaults.os), local.os_defaults[local.vm_defaults.os]),
      {
        name    = name
        network = merge(local.vm_defaults.network, try(vm.network, {}))
        windows = merge(local.vm_defaults.windows, try(vm.windows, {}))

        # Already complete and already one shape - see arc_input above. There is
        # nothing left to merge a default into.
        arc = local.arc_input[name]
      }
    )
  }

  # The map used by resources
  vms_final = local.vms_normalized

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

  # The arc_tags_map_safe helper is gone: it chose between a map and a bare `{}`
  # inside a `for` expression, which is the same unification defect BUG-006 fixed
  # one level up. v.arc.tags_map is already a map for every VM.

  # ------------------------------------------------------------
  # Static network shape (FEAT-003)
  # ------------------------------------------------------------
  # The old rule checked only that address and gateway were non-null, so
  # "192.168.10.300/24", a gateway on a different subnet, and the same address
  # on two VMs all planned clean and produced a guest with no working network -
  # discovered by SSH timing out, not by the pipeline.
  #
  # These helpers exist because the rules cannot call cidrhost() directly. A
  # `for` expression that hits a malformed value raises rather than skipping,
  # and one raised expression fails the whole `error_message` - so a single bad
  # address would blind every rule in this file, which is the same failure mode
  # the nonsensitive() note below describes from a different direction. Every
  # parse is wrapped in can() here, once, and the rules read booleans.
  #
  # `[.]` rather than an escaped dot: HCL and the regex engine each take
  # backslash escapes, and a character class says the same thing without
  # double-escaping it.
  ipv4_cidr_re = "^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+/[0-9]+$"
  ipv4_host_re = "^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$"

  # Null normalised to "" so nothing downstream passes a null into regex() or
  # cidrhost(). An absent address is already the existing rule's business.
  net_static = {
    for k, v in local.vms_final : k => {
      address = try(v.network.address, null) == null ? "" : tostring(v.network.address)
      gateway = try(v.network.gateway, null) == null ? "" : tostring(v.network.gateway)
    }
    if v.network.type == "static"
  }

  # The regex fixes the shape and cidrhost() judges the values: "999.1.1.1/24"
  # and "192.168.10.20/33" both match the pattern and both fail cidrhost(). The
  # regex is still required, because cidrhost() accepts IPv6 and every guest
  # this factory builds is IPv4.
  net_address_ok = {
    for k, n in local.net_static : k => (
      n.address != "" &&
      can(regex(local.ipv4_cidr_re, n.address)) &&
      can(cidrhost(n.address, 0))
    )
  }

  # A gateway is a host, not a network, so it carries no prefix of its own. /32
  # is the cheapest way to ask cidrhost() whether the four octets are in range.
  net_gateway_ok = {
    for k, n in local.net_static : k => (
      n.gateway != "" &&
      can(regex(local.ipv4_host_re, n.gateway)) &&
      can(cidrhost("${n.gateway}/32", 0))
    )
  }

  # Same subnet iff the gateway, masked with the address's prefix, gives the
  # same network address. True when either value is malformed: that is not this
  # rule's fault to report, and the shape rules above already name it. One
  # message per fault.
  net_gateway_in_subnet = {
    for k, n in local.net_static : k => (
      local.net_address_ok[k] && local.net_gateway_ok[k]
      ? cidrhost("${n.gateway}/${split("/", n.address)[1]}", 0) == cidrhost(n.address, 0)
      : true
    )
  }

  # Keyed by VM, valued by the host part - "192.168.10.20/24" and
  # "192.168.10.20/16" are the same address to a guest, so the prefix is
  # dropped before comparing. Only well-formed addresses take part.
  net_address_host = {
    for k, n in local.net_static : k => split("/", n.address)[0]
    if local.net_address_ok[k]
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

    # ------------------------------------------------------------
    # VM name (BUG-020)
    # ------------------------------------------------------------
    # The inventory key is not just a key. It becomes the Proxmox VM name, the
    # hostname and FQDN written into the guest, and - on Windows - the argument
    # to Rename-Computer. Each of those has its own rules, none of them was
    # checked, and so an unusable name was found at first boot in the guest
    # rather than at plan time. On Windows it is found inside
    # Set-HostnameImmediateBestEffort, which ends in `throw`, which abandons the
    # rest of first boot: no autologon, no RDP, no Arc.

    # BUG-020-A1. Lowercase letters, digits and inner hyphens. An underscore is
    # not valid in a hostname label and the value reaches /etc/hosts and the
    # guest FQDN unaltered; uppercase survives Proxmox but not the round trip
    # through DNS, which hands the name back lowercased so that it no longer
    # matches what Arc onboarded.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': invalid name - use lowercase letters, digits and inner hyphens only (no underscore, no uppercase, no leading or trailing hyphen)",
        k
      )
      if !can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", k))
    ],

    # BUG-020-A3. One DNS label, and a label stops at 63 characters.
    [
      for k, v in local.vms_final :
      format("VM '%s': name is %d characters; a DNS label stops at 63", k, length(k))
      if length(k) > 63
    ],

    # BUG-020-A2. The Windows limit is lower than the DNS one and exists for an
    # unrelated reason, so the message says which reason: Rename-Computer sets
    # the NetBIOS computer name, and a NetBIOS name is 15 characters. A
    # 16-character name is a perfectly good DNS label that Windows will not take.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': name is %d characters; a Windows VM stops at 15 because Rename-Computer sets the NetBIOS computer name",
        k,
        length(k)
      )
      if v.os == "windows" && length(k) > 15
    ],

    # BUG-020-A4. arc.resource_name is a different name under different rules -
    # that is what the override is for (BUG-019) - so it cannot inherit the VM
    # rules above. Azure accepts letters, digits, '.', '_' and '-' for
    # Microsoft.HybridCompute/machines. Checked only when Arc is enabled: with
    # Arc off the value defaults to the VM name, never leaves Terraform, and
    # would only produce a second error about a string already covered.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': invalid arc.resource_name '%s' - Azure allows letters, digits, '.', '_' and '-', starting with a letter or digit",
        k,
        v.arc.resource_name
      )
      if v.arc.enabled && !can(regex("^[A-Za-z0-9][A-Za-z0-9._-]*$", v.arc.resource_name))
    ],

    # The documented ceiling for Microsoft.HybridCompute/machines.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': arc.resource_name is %d characters; Azure stops at 54",
        k,
        length(v.arc.resource_name)
      )
      if v.arc.enabled && length(v.arc.resource_name) > 54
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

    # FEAT-003-A1. Malformed address. Gated on a non-empty value so a VM that
    # named no address at all gets the rule above and not this one as well.
    [
      for k, n in local.net_static :
      format(
        "VM '%s': network.address '%s' is not an IPv4 CIDR - expected a form like 192.168.10.20/24",
        k,
        n.address
      )
      if n.address != "" && !local.net_address_ok[k]
    ],

    # FEAT-003-A1. Malformed gateway. A prefix here is the common way to get it
    # wrong - the address next to it has one - so the message says so rather
    # than only calling the value invalid.
    [
      for k, n in local.net_static :
      format(
        "VM '%s': network.gateway '%s' is not a bare IPv4 address - expected a form like 192.168.10.1, with no prefix",
        k,
        n.gateway
      )
      if n.gateway != "" && !local.net_gateway_ok[k]
    ],

    # FEAT-003-A2. A gateway outside the subnet is the failure that looks most
    # like a working configuration: both values parse, Proxmox accepts them, and
    # the guest boots with an address and no route off its own network.
    [
      for k, n in local.net_static :
      format(
        "VM '%s': network.gateway %s is outside the subnet of network.address %s - the guest would boot with no route off its own network",
        k,
        n.gateway,
        n.address
      )
      if !local.net_gateway_in_subnet[k]
    ],

    # FEAT-003-A3. Two guests on one address. Neither VM is more wrong than the
    # other, so both are named, and each message names the other - an operator
    # reading one line should not have to search the inventory for its partner.
    [
      for k, a in local.net_address_host :
      format(
        "VM '%s': network.address %s is also used by %s - a duplicate static address breaks the network for every guest holding it",
        k,
        a,
        join(", ", [for k2, a2 in local.net_address_host : k2 if a2 == a && k2 != k])
      )
      if length([for k2, a2 in local.net_address_host : k2 if a2 == a]) > 1
    ],

    # FEAT-003-A4. address or gateway alongside type = "dhcp". Nothing breaks -
    # main.tf sends null for a DHCP guest either way - which is the problem: the
    # inventory states an address the guest will never have, and the next reader
    # believes it.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': network.type=dhcp, but the inventory also sets %s - a DHCP guest ignores it, so the inventory would claim an address the guest never has",
        k,
        join(" and ", compact([
          try(v.network.address, null) == null ? "" : "network.address",
          try(v.network.gateway, null) == null ? "" : "network.gateway",
        ]))
      )
      if v.network.type == "dhcp" &&
      (try(v.network.address, null) != null || try(v.network.gateway, null) != null)
    ],

    # FEAT-002-A2. Proxmox accepts 100 to 999999999; below 100 is reserved for
    # its own internal use and is refused at the API, which would otherwise
    # surface as a failed create rather than a failed plan - and a failed create
    # is the orphan case (docs/incident-orphan-vm.md), so this is worth catching
    # early rather than cleaning up after.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': vm_id %d is out of range - Proxmox allows 100 to 999999999, and below 100 is reserved",
        k,
        v.vm_id
      )
      if v.vm_id != null && (v.vm_id < 100 || v.vm_id > 999999999)
    ],

    # A whole number, because Proxmox has no other kind of VM ID and 100.5 would
    # otherwise reach the API as a string it rejects for a reason that does not
    # name the field.
    [
      for k, v in local.vms_final :
      format("VM '%s': vm_id must be a whole number, got %v", k, v.vm_id)
      if v.vm_id != null && floor(v.vm_id) != v.vm_id
    ],

    # Two VMs on one ID. Proxmox would refuse the second create, but only after
    # the first had been built - so the apply fails halfway with one VM made and
    # one not, which is the state this rule exists to avoid reaching. Each
    # message names the other VM for the same reason the duplicate-address rule
    # does: finding the partner should not require reading the whole inventory.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': vm_id %d is also declared by %s",
        k,
        v.vm_id,
        join(", ", [
          for k2, v2 in local.vms_final : k2
          if k2 != k && v2.vm_id != null && v2.vm_id == v.vm_id
        ])
      )
      if v.vm_id != null && length([
        for k2, v2 in local.vms_final : k2
        if v2.vm_id != null && v2.vm_id == v.vm_id
      ]) > 1
    ],

    # FEAT-009-A2. What this rule can and cannot do is worth stating, because
    # the issue asks for something Terraform is not in a position to check.
    #
    # "A size below the template's own disk must fail at plan" needs the
    # template's disk size, and nothing in this configuration knows it - it
    # lives in Proxmox, and reading it would mean an API call during plan.
    # var.template_disk_gb_* is how that unknowable becomes a declared
    # constant: state the template's size once and the rule enforces it. Left
    # null, the rule is silent, because a floor invented here would be worse
    # than no floor.
    #
    # This matters more than an ordinary range check. Proxmox cannot shrink a
    # disk, but Terraform plans a shrink as an ordinary in-place update - so
    # without this the operator gets a clean plan and an apply that fails at
    # the hypervisor.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': disk_gb %d is smaller than the %s template's disk (%d GB). Proxmox cannot shrink a disk - this would plan clean and fail at apply",
        k,
        v.disk_gb,
        v.os,
        local.template_disk_gb[v.os]
      )
      # floor() guards the format() above, not the comparison. This rule was
      # unreachable while template_disk_gb was null, and setting the measured
      # sizes exposed it: `format("%d", 40.5)` is an error, not a message, so a
      # fractional disk_gb crashed the plan here instead of being reported by
      # the whole-number rule below - which is the rule that exists to say what
      # is wrong with it.
      #
      # One fault, one message. A value that is not a whole number is not also
      # a shrink; it is a value this rule cannot describe.
      if v.disk_gb != null &&
      floor(v.disk_gb) == v.disk_gb &&
      local.template_disk_gb[v.os] != null &&
      v.disk_gb < local.template_disk_gb[v.os]
    ],

    # A positive whole number of gigabytes. Proxmox takes an integer size, and
    # a fractional or zero value reaches the API as something it rejects with a
    # message that does not name the VM.
    [
      for k, v in local.vms_final :
      format("VM '%s': disk_gb must be a whole number of gigabytes above zero, got %v", k, v.disk_gb)
      if v.disk_gb != null && (v.disk_gb <= 0 || floor(v.disk_gb) != v.disk_gb)
    ],

    # A disk block is only emitted when disk_gb is set, so an interface without
    # a size is a value that does nothing - the kind of quiet no-op FEAT-003-A4
    # refuses for network settings, for the same reason.
    [
      for k, v in local.vms_final :
      format("VM '%s': disk_interface is set but disk_gb is not, so no disk block is emitted and the interface is ignored", k)
      if v.disk_gb == null && try(v.disk_interface, null) != null && v.disk_interface != local.vm_defaults.disk_interface
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
        for tk, tv in v.arc.tags_map : 1
        if strcontains(tk, ",") ||
        strcontains(tk, "=") ||
        strcontains(tostring(tv), ",") ||
        strcontains(tostring(tv), "=")
      ]) > 0
    ],

    # KAN-015. windows.winrm_allow_unencrypted on a guest whose WinRM is off is
    # a value that does nothing: the template emits no WinRM block at all, so
    # the inventory would record a transport decision the guest never makes.
    # Same shape and same reason as FEAT-003-A4 and the disk_interface rule -
    # a quiet no-op is refused because the next reader believes it.
    #
    # Scoped to Windows because vm_defaults.windows reaches every VM through
    # the merge in vms_normalized, so a Linux guest carries both keys too and
    # would otherwise be told about a template it does not use.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': windows.winrm_allow_unencrypted is set but windows.enable_winrm is false, so no WinRM configuration is emitted and the setting is ignored",
        k
      )
      if v.os == "windows" && !v.windows.enable_winrm && v.windows.winrm_allow_unencrypted
    ],

    # KAN-011-A3. A management source that is not an IPv4 CIDR.
    #
    # Set-NetFirewallRule -RemoteAddress takes the value as written and a
    # malformed entry is not an error there - it is a rule that matches nothing
    # or, worse, one Windows interprets differently than the author meant. The
    # guest is already up by the time that would be visible, so it is checked
    # here, with FEAT-003's helpers rather than a second address grammar.
    #
    # A prefix is required. "192.168.10.0" without one is a host, and an
    # operator who means the subnet and writes the network address gets a rule
    # matching exactly one address that nothing is on.
    [
      for s in var.management_source_cidrs :
      format("management_source_cidrs entry '%s' is not an IPv4 CIDR - each entry takes a prefix, and a single host is /32", s)
      if trimspace(s) == "" || !can(regex(local.ipv4_cidr_re, s)) || !can(cidrhost(s, 0))
    ],

    # KAN-011-A3, and the one combination this refuses outright.
    #
    # windows.winrm_allow_unencrypted reinstates Basic over HTTP, which
    # SEC-008-A5 measured and windows-winrm.md records as an accepted exposure:
    # the administrator credential is recoverable from the wire on port 5985.
    # That was accepted against an implied audience of the lab network.
    #
    # With management_source_cidrs empty, the rule the template enables carries
    # the built-in scope - any remote address - so the audience is anything the
    # bridge will deliver to. The two settings were each reviewed alone and
    # their combination never was.
    #
    # Refused rather than warned, on the SEC-007 precedent: password SSH is a
    # deliberate choice and turning it on without a password to go with it is
    # blocked. Here the missing half is where the wire is, and satisfying it
    # costs one CIDR.
    #
    # Per VM rather than on the variable, so a lab with the default on and no
    # Windows guest is not told to configure a firewall for a template it does
    # not use - the same gating the Windows password rule keeps.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': windows.winrm_allow_unencrypted is true and management_source_cidrs is empty. The credential is recoverable from the wire on port 5985 and the firewall rule accepts any source. Set management_source_cidrs, or leave the transport on Negotiate.",
        k
      )
      if v.os == "windows" && v.windows.enable_winrm && v.windows.winrm_allow_unencrypted && length(var.management_source_cidrs) == 0
    ],

    # SEC-006-A3. The node SSH identity, and the one combination that cannot
    # work: none of the three.
    #
    # Worth catching here rather than letting the provider find it. Snippet
    # upload is the first thing that needs this connection, and it happens
    # *after* the clone - so a run with no usable identity does not fail
    # cleanly, it fails with a VM already built and a snippet that never
    # arrived. That is the orphan shape from docs/incident-orphan-vm.md, reached
    # from a new direction.
    #
    # nonsensitive() on the emptiness tests, and only on them, for the reason
    # SEC-007's rule states below: a sensitive value anywhere in this list taints
    # the whole error_message expression and blinds every other rule in the file.
    # Whether a credential is empty is not the credential.
    (
      !var.proxmox_ssh_agent &&
      nonsensitive(local.ssh_password == null) &&
      nonsensitive(local.ssh_private_key == null)
      ? ["no node SSH identity is configured. The provider uploads cloud-init snippets over SSH, not through the API, so an apply would clone the VM and then fail with nowhere to put its configuration. Set TF_VAR_PROXMOX_SSH_PASSWORD, or TF_VAR_PROXMOX_SSH_PRIVATE_KEY, or proxmox_ssh_agent = true. See docs/proxmox-api-token.md."]
      : []
    ),

    # An agent socket path with the agent off is a value that does nothing - the
    # provider is not being asked to use an agent at all - and the configuration
    # would record a decision that has no effect. Same shape and same reason as
    # FEAT-003-A4 and the disk_interface rule.
    (
      !var.proxmox_ssh_agent && local.ssh_agent_socket != null
      ? ["proxmox_ssh_agent_socket is set but proxmox_ssh_agent is false, so the socket is ignored and the connection uses a password or key instead."]
      : []
    ),

    # SEC-001c. A Windows VM with no administrator password. The template
    # already refuses this - it throws on an empty decode - but it does so at
    # first boot, inside the guest, from a script whose `throw` abandons
    # everything after it: no rename, no RDP, no Arc (BUG-007's shape). The VM
    # exists, boots, and is unreachable, and the only record is a log file on a
    # machine nobody can log into.
    #
    # A plan-time refusal costs nothing and happens before the clone. Same
    # reasoning and same construction as SEC-007's rule below, including
    # nonsensitive() on the emptiness test only - whether a password is empty is
    # not the password, and a sensitive value anywhere in this list would blind
    # every other rule in the file.
    #
    # Gated on a Windows VM existing, so a Linux-only lab is not told to set a
    # secret nothing would read.
    (
      nonsensitive(trimspace(var.windows_admin_password == null ? "" : var.windows_admin_password) == "") &&
      anytrue([for k, v in local.vms_final : v.os == "windows"])
      ? ["a Windows VM is declared but windows_admin_password is empty. First boot would throw on the empty value and abandon the rest of provisioning - no rename, no RDP and no Arc - leaving a VM nobody can log in to. Set TF_VAR_WINDOWS_ADMIN_PASSWORD."]
      : []
    ),

    # KAN-012-A4. The resolver list, which nothing checked.
    #
    # It mattered less when var.dns_server could only be changed by editing this
    # repository. Since KAN-012 made the topology settable from repository
    # variables, an empty or mistyped value reaches a plan without anyone
    # reviewing a diff - and an empty string in this list is not an absent
    # resolver, it is a resolver named "". That reaches
    # initialization.dns.servers on every VM and, on Windows, the PowerShell
    # array the first-boot script sets DNS from.
    #
    # The failure is the quiet kind BUG-018 described: DNS still works, because
    # the guest falls back to whatever DHCP offered, and it is not the DNS
    # anybody configured. It surfaces much later as an Arc onboarding failure
    # naming a resolver nobody set.
    (
      length(local.dns_servers) == 0
      ? ["no resolvers are configured. var.dns_server plus var.dns_servers_fallback produce an empty list, and every guest would be handed nothing for initialization.dns.servers."]
      : []
    ),

    [
      for s in local.dns_servers :
      format("resolver '%s' is not a bare IPv4 address - var.dns_server and var.dns_servers_fallback each take an address, with no prefix and no hostname", s)
      if trimspace(s) == "" || !can(regex(local.ipv4_host_re, s)) || !can(cidrhost("${s}/32", 0))
    ],

    # OPS-003-A1. A VM ID the factory must not manage, declared anyway.
    #
    # The one that matters is the runner: destroying VM 1110 would terminate the
    # process doing the destroying. Nothing else in this configuration would
    # stop it - the inventory guard compares desired against state, and a VM
    # being newly declared is in neither.
    #
    # Refused at plan time rather than caught later, because there is no later:
    # by the time an apply reaches the API call, the decision has been made.
    [
      for k, v in local.vms_final :
      format(
        "VM '%s': vm_id %d is on the protected list and must not be managed by this factory. %s",
        k,
        v.vm_id,
        v.vm_id == 1110 ? "That is the runner (gha-runner-01) - destroying it would terminate the run doing the destroying. See #171." :
        v.vm_id == 1103 ? "That is dns-01 - every first-boot script waits for it to resolve before it can do anything. See docs/unmanaged-vms.md." :
        v.vm_id == 1104 ? "That is wg-vpn-01 - the management path into the lab. See docs/unmanaged-vms.md." :
        v.vm_id == 1105 ? "That is elastic-01, which the lab owner has declared off limits. See docs/unmanaged-vms.md." :
        "See var.protected_vm_ids."
      )
      if v.vm_id != null && contains(var.protected_vm_ids, v.vm_id)
    ],

    # OPS-005. A node the factory would build on that it cannot reach.
    #
    # var.proxmox_node_name decides where the VM goes; var.proxmox_ssh_nodes
    # says how to reach each node for the snippet upload. If the name is not a
    # key of the map the provider has no SSH route to it, and the way that fails
    # is not an error at plan time - it is a VM created through the API on one
    # node and a snippet uploaded to whichever node the provider does know.
    #
    # Both are repository variables since KAN-012, so this is now a web form
    # away rather than a pull request away.
    (
      !contains(keys(var.proxmox_ssh_nodes), var.proxmox_node_name)
      ? [format(
        "proxmox_node_name is '%s', which is not a key of proxmox_ssh_nodes (%s). The provider would create VMs on that node and have no SSH route to upload their cloud-init snippets to it.",
        var.proxmox_node_name,
        join(", ", sort(keys(var.proxmox_ssh_nodes)))
      )]
      : []
    ),

    # An empty map is the same fault with a different shape, and would otherwise
    # produce no `node` blocks at all - a provider that silently cannot upload
    # anywhere.
    (
      length(var.proxmox_ssh_nodes) == 0
      ? ["proxmox_ssh_nodes is empty. The provider uploads cloud-init snippets over SSH and would have no node to upload them to."]
      : []
    ),

    # SEC-007. The finding is a compound: the template forced password SSH on,
    # and `coalesce(var.linux_vm_password, "")` turned an unset secret into a
    # blank password. Either alone is survivable. Together they boot a VM with
    # password login enabled on a passwordless-sudo account with no password,
    # and nothing in the pipeline said so.
    #
    # The template half is fixed by linux_password_auth defaulting to false.
    # This is the other half: turning it on without a password to go with it is
    # now refused at plan time rather than discovered on a running guest.
    #
    # nonsensitive() on the emptiness test, and only on it. A sensitive value
    # anywhere in this list taints the whole `error_message` expression, and
    # Terraform then refuses to render *any* of the messages - so one rule
    # touching a password would silently blind every other rule in the file.
    # Whether a password is empty is not the password.
    #
    # Gated on a Linux VM existing so a Windows-only inventory is not told about
    # a Linux flag it never set.
    (
      var.linux_password_auth &&
      nonsensitive(trimspace(var.linux_vm_password == null ? "" : var.linux_vm_password) == "") &&
      anytrue([for k, v in local.vms_final : v.os == "linux"])
      ? ["linux_password_auth is on but linux_vm_password is empty - that boots a guest with password SSH enabled and a blank password for a passwordless-sudo account. Set TF_VAR_LINUX_VM_PASSWORD, or leave linux_password_auth off and use the SSH key."]
      : []
    )
  ])
}
