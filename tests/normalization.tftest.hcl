# CHORE-005-A1. Native `terraform test` coverage for the inventory model.
#
# This does not overlap test_config_validation.py, and the split is deliberate.
# That suite plans a deliberately broken inventory per rule and asserts the plan
# *fails* - it proves the rules block. It cannot easily say what a value came
# out as, because a plan that succeeds prints resources rather than locals.
#
# This one asserts on the locals directly. Different question: not "does an
# invalid inventory stop the pipeline" but "does a valid one normalize to the
# shape the resources are about to consume". BUG-006 lived entirely in that
# second question - four input forms for `arc` that had to unify into one object
# type - and it produced no error to assert on, only a type mismatch at plan
# time or a wrong value at apply time.
#
# ## Everything here is an invariant, not a fixture
#
# `terraform test` cannot override a local value, so these runs see whatever
# `local.vms` declares. Asserting that ubuntu-static-01 has two cores would
# therefore be a test of today's lab rather than of the model, and it would go
# red the moment DOC-001 (#59) populates the inventory - failing for a reason
# that has nothing to do with a defect.
#
# So every assertion below holds for *any* inventory, including an empty one,
# and is written with alltrue() over local.vms_final. Where a specific input
# form needs exercising, that is test_config_validation.py's override-file
# mechanism, not this file.
#
# Run: terraform init -backend=false && terraform test
# -backend=false matters: without it this reads the state path on the runner,
# which a test has no business touching. `command = plan` reaches no hypervisor.

# Only the three variables that have no default, and only in the shape the
# provider checks before anything else - it validates the token's format at
# configure time, so it has to look like one. Nothing here reaches the
# hypervisor.
#
# The value of proxmox_ssh_password says what it is on purpose. The literal
# "not-a-real-password" trips gitleaks' `hashicorp-tf-password` rule, which
# matches `*_password = "..."` in HCL - the Python fixtures use the same string
# and never trip it, because that rule only reads HCL. The fix is a value that
# is visibly a fixture rather than an allowlist entry: a scanner exception here
# would also cover a real credential added later.
#
# linux_vm_password is deliberately absent. It defaults to null, nothing in
# these runs needs it, and adding it would put a second password literal in an
# HCL file for no reason.
#
# windows_admin_password is present, and it was not before SEC-001c. It has a
# default of null like the Linux one, but local.vms declares win-srv-01 and the
# new rule refuses a Windows VM with no administrator password - so without a
# value here every run in this file fails on the inventory rather than on what
# it is testing. That the omission was caught by these tests rather than by a
# guest is the rule working: the failure it exists to prevent is a VM that
# boots, throws inside first boot, and is unreachable.
#
# Same visibly-a-fixture form as proxmox_ssh_password above, for the same
# gitleaks reason.
variables {
  proxmox_api_token      = "root@pam!t=00000000-0000-0000-0000-000000000000"
  ssh_public_key         = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest test@example"
  proxmox_ssh_password   = "unused-by-command-plan"
  windows_admin_password = "unused-by-command-plan"
}

# ---------------------------------------------------------------------------
# arc_input: four input forms, one output shape (BUG-006)
# ---------------------------------------------------------------------------
# `arc` may be absent, a bool, or an object, and `arc.tags` a string or a map.
# local.vms_final is built by a `for` expression, so every element must unify to
# one object type. Before BUG-006 the expression returned a different shape per
# form and failed on any inventory mixing them - which is the mix the worked
# example in locals.tf recommends.

run "arc_input_is_one_shape_for_every_vm" {
  command = plan

  assert {
    condition = alltrue([
      for k, v in local.arc_input : can(tobool(v.enabled))
    ])
    error_message = "arc.enabled must be a bool for every VM, whichever form `arc` was written in"
  }

  # A map and a string for every VM, never null - that is what keeps the shape
  # uniform, and what lets the validation rules read them unconditionally.
  assert {
    condition = alltrue([
      for k, v in local.arc_input : can(length(v.tags_map)) && can(length(v.tags_string))
    ])
    error_message = "arc tags must render as a map and a string for every VM, never null"
  }

  # BUG-019. The Arc machine name defaults to the inventory key but may be
  # overridden, and it is never empty - the destroy path looks machines up by it.
  assert {
    condition = alltrue([
      for k, v in local.arc_input : length(trimspace(v.resource_name)) > 0
    ])
    error_message = "arc.resource_name must never be empty; the Arc cleanup path looks machines up by it"
  }
}

run "map_tags_render_sorted_so_the_string_is_stable" {
  command = plan

  # The map form is joined in sorted key order. An unstable rendering would show
  # as a snippet diff on every plan for no reason - and, before BUG-012, as a
  # rebuilt guest. Re-deriving it here proves the sort is in the expression
  # rather than an accident of map iteration order.
  assert {
    condition = alltrue([
      for k, v in local.vms_final :
      v.arc.tags_string == join(",", [
        for tk in sort(keys(v.arc.tags_map)) : "${tk}=${v.arc.tags_map[tk]}"
      ])
      if length(v.arc.tags_map) > 0
    ])
    error_message = "map-form arc.tags must render sorted by key"
  }
}

# ---------------------------------------------------------------------------
# vms_normalized (BUG-005)
# ---------------------------------------------------------------------------

run "os_decides_the_template_and_the_vm_cannot_take_it_back" {
  command = plan

  # BUG-005-A2. os_defaults sits to the right of the VM in the merge on purpose,
  # so template_vmid and vendor_data_tpl keep following `os`. Otherwise a VM
  # could pair os = "linux" with a Windows template_vmid and be cloned from the
  # wrong image, with no rule in validation_errors able to catch the pairing -
  # by the time validation looks, the two agree with each other.
  assert {
    condition = alltrue([
      for k, v in local.vms_final :
      v.template_vmid == local.os_defaults[v.os].template_vmid
    ])
    error_message = "template_vmid must follow os, whatever the VM declared"
  }
  assert {
    condition = alltrue([
      for k, v in local.vms_final :
      endswith(v.vendor_data_tpl, "${v.os}.yaml.tftpl")
    ])
    error_message = "the rendered template must follow os"
  }
}

run "every_vm_reaches_the_resources_with_a_complete_shape" {
  command = plan

  # vm_defaults.network carries address and gateway as null, so all three keys
  # exist however the VM wrote its network block. The validation rules and
  # main.tf both read them unconditionally.
  assert {
    condition = alltrue([
      for k, v in local.vms_final :
      contains(["dhcp", "static"], v.network.type) &&
      can(v.network.address) && can(v.network.gateway)
    ])
    error_message = "every VM must carry a complete network object"
  }

  # The key becomes the Proxmox name, the guest hostname and the FQDN.
  assert {
    condition = alltrue([
      for k, v in local.vms_final : v.name == k
    ])
    error_message = "the inventory key must be the VM name"
  }

  assert {
    condition = alltrue([
      for k, v in local.vms_final : v.cores > 0 && v.memory_mb > 0
    ])
    error_message = "cores and memory_mb must resolve to positive numbers for every VM"
  }

  # KAN-015. main.tf passes both of these into templatefile unconditionally,
  # for a Linux VM as much as a Windows one, and templatefile fails on a null
  # where the template expects a value. vm_defaults.windows is what makes them
  # total; this asserts that it still is, whatever an inventory writes in its
  # `windows` block.
  assert {
    condition = alltrue([
      for k, v in local.vms_final :
      can(tobool(v.windows.enable_winrm)) && can(tobool(v.windows.winrm_allow_unencrypted)) &&
      can(tobool(v.windows.autologon))
    ])
    error_message = "every VM must carry all three Windows booleans, whether or not it is a Windows guest"
  }
}

# ---------------------------------------------------------------------------
# Autologon (SEC-001c)
# ---------------------------------------------------------------------------
# ADR 0001 path 8 is the administrator password sitting in HKLM Winlogon as
# cleartext REG_SZ. It closes by not asking for autologon, so the invariant is
# the direction of the default: a guest that states nothing must not get it.

run "autologon_is_never_the_default" {
  command = plan

  assert {
    condition     = var.windows_autologon_default == false
    error_message = "windows_autologon_default must stay false - a guest that states nothing keeps its password out of the registry"
  }

  assert {
    condition = alltrue([
      for k, v in local.vms_final :
      v.windows.autologon == false || try(local.vms[k].windows.autologon, null) == true
    ])
    error_message = "a VM only gets autologon by asking for it in its own `windows` block"
  }
}

# ---------------------------------------------------------------------------
# WinRM transport (KAN-015)
# ---------------------------------------------------------------------------
# The invariant is the direction of the default, not a particular VM's value.
# An inventory may opt a guest into the old Basic-over-unencrypted transport -
# that is what the flag is for - but a guest that says nothing must not get it,
# because that is exactly how the exposure SEC-008-A5 recorded became universal
# in the first place.

run "the_unencrypted_transport_is_never_the_default" {
  command = plan

  assert {
    condition     = var.windows_winrm_allow_unencrypted_default == false
    error_message = "windows_winrm_allow_unencrypted_default must stay false - a guest that states nothing gets Negotiate"
  }

  assert {
    condition = alltrue([
      for k, v in local.vms_final :
      v.windows.winrm_allow_unencrypted == false
      || try(local.vms[k].windows.winrm_allow_unencrypted, null) == true
    ])
    error_message = "a VM only gets the unencrypted transport by asking for it in its own `windows` block"
  }
}

# ---------------------------------------------------------------------------
# validation_errors, positive side
# ---------------------------------------------------------------------------
# The negative side - one broken inventory per rule - is
# test_config_validation.py, which needs a *failing* plan and so cannot live
# here. This is the half that catches a rule firing when it should not.

run "the_declared_inventory_produces_no_validation_errors" {
  command = plan

  assert {
    condition     = length(local.validation_errors) == 0
    error_message = "the inventory in locals.tf must plan clean"
  }
}

# ---------------------------------------------------------------------------
# Resolvers (BUG-018)
# ---------------------------------------------------------------------------
# One list reaching a guest twice: through initialization.dns.servers, and on
# Windows through the first-boot script. They were maintained separately and
# drifted, so changing var.dns_server moved the Linux guests and left the
# Windows ones behind.

run "resolvers_are_one_list_rendered_two_ways" {
  command = plan

  assert {
    condition     = local.dns_servers[0] == var.dns_server
    error_message = "var.dns_server must lead the resolver list"
  }
  assert {
    condition     = length(local.dns_servers) == 1 + length(var.dns_servers_fallback)
    error_message = "dns_servers must be var.dns_server plus the fallbacks, with nothing dropped"
  }

  # jsonencode per element, so the PowerShell form quotes each address rather
  # than assuming an address never needs escaping.
  assert {
    condition     = local.dns_servers_ps == join(",", [for s in local.dns_servers : jsonencode(s)])
    error_message = "the PowerShell resolver list must be built from the same local"
  }
}
