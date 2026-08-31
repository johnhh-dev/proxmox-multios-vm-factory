# SPIKE-003. This file used to be attached as `cicustom: user=`, and that is a
# replacement rather than an addition: Proxmox expresses `ciuser`, `sshkeys` and
# `cipassword` only by rendering them into the user-data it generates, so
# overriding user-data discarded all three. The VM configuration still listed
# `sshkeys`, it still validated, and it reached no guest.
#
# Measured on VM 100 before this change:
#
#   strings /dev/zvol/rpool/data/vm-100-cloudinit | grep -c ssh_authorized_keys
#   0
#
# Key authentication to every Linux guest this factory has ever built was
# therefore not working, and nothing said so - password authentication was
# forced on by the same template, so the guests were reachable and the gap was
# invisible.
#
# `cicustom: vendor=` is a second document rather than a substitute. Proxmox
# generates its user-data as usual, and cloud-init applies this on top.
resource "proxmox_virtual_environment_file" "vendor_data" {
  for_each = local.vms_final

  node_name    = var.proxmox_node_name
  datastore_id = var.snippets_datastore
  content_type = "snippets"
  overwrite    = true

  source_raw {
    # SEC-003: the rendered template carries both guest passwords and the Arc
    # service-principal secret. `terraform plan` prints the diff of this
    # attribute in full, and the job log is readable by anyone with read access
    # to the repository. sensitive() marks the rendered string so the plan shows
    # "(sensitive value)" instead of the body.
    #
    # The mark is applied here rather than relied upon to propagate: coalesce()
    # returns the "" literal whenever its sensitive argument is null, and a
    # literal carries no mark, so an unset password would silently un-redact the
    # whole snippet.
    data = sensitive(templatefile(each.value.vendor_data_tpl, {
      hostname = each.value.name
      fqdn     = "${each.value.name}.${var.search_domain}"

      # SEC-007. Not a secret. The template needs it because it decides both
      # ssh_pwauth and what runcmd writes to sshd_config, and those two must
      # agree.
      linux_password_auth = var.linux_password_auth

      # Windows (template-dependent; optional)
      #
      # BUG-018: dns_servers is local.dns_servers rendered as the body of a
      # PowerShell array. Only the Windows template consumes it - it sets DNS
      # in-guest, because Cloudbase-Init has not applied the cloud-init network
      # configuration by the time Arc needs to resolve - and it is the same
      # local that reaches every guest through initialization.dns.servers
      # below, so the two cannot drift the way they had.
      windows_admin_password = var.windows_admin_password == null ? "" : var.windows_admin_password
      windows_enable_winrm   = each.value.windows.enable_winrm
      dns_servers            = local.dns_servers_ps

      # KAN-012-A3. One name, reaching both templates, for the check each of
      # them makes before it decides the network is usable. It used to be two
      # different checks against two different targets.
      network_probe_host = var.network_probe_host

      # KAN-015. Which WinRM transport the guest is configured for. False - the
      # default - means the template sets no auth options at all and the
      # service keeps Negotiate; true reinstates Basic over an unencrypted
      # transport, which is the S2 exposure SEC-008-A5 recorded. Passed rather
      # than read from a variable inside the template because the decision is
      # per VM, like enable_winrm above.
      windows_winrm_allow_unencrypted = each.value.windows.winrm_allow_unencrypted

      # KAN-011-A3. Which sources may reach RDP and WinRM on this guest. A
      # repository-wide variable rather than a per-VM one, unlike the three
      # around it: where an administrator connects from is a property of the
      # lab, not of the machine being built, and one guest disagreeing about it
      # is a guest nobody can reach. Empty means the built-in rules keep the
      # scope they ship with, which is any address.
      management_source_cidrs = local.management_sources_ps

      # SEC-001c-A4. Whether the guest writes the administrator password into
      # the registry for a one-time autologon. False by default now - see the
      # variable - so ADR 0001 path 8 does not happen at all unless a VM asks.
      windows_autologon = each.value.windows.autologon

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

      # SEC-001a: a token minted for this run, not the service-principal
      # secret. What survives in the snippet and in state is a credential that
      # has already expired, which is the whole point of ADR 0001's option C.
      arc_access_token = var.arc_access_token
    }))

    file_name = "${each.value.name}-vendor-data.yaml"
  }
}

resource "proxmox_virtual_environment_vm" "vm" {
  for_each  = local.vms_final
  name      = each.value.name
  node_name = var.proxmox_node_name

  # FEAT-002-A1. Null lets Proxmox assign the next free ID, which is what this
  # factory always did - and is why the README's IDs 100-104 could not be
  # reproduced from this configuration, and why a failed create is never
  # retried against the same ID (see docs/incident-orphan-vm.md).
  #
  # FEAT-002-A6, and read this before setting it on a VM that already exists.
  # vm_id is ForceNew. Measured against a plan with real state:
  #
  #   vm_id set to the ID the VM already has  -> no replacement
  #   vm_id set to a different ID             -> "# forces replacement"
  #
  # So adopting IDs for running guests is safe only if each value matches what
  # that guest already has. Writing the README's table into the inventory
  # without checking would destroy and rebuild every VM whose real ID differs.
  # Confirm with `terraform state show` or `qm list` per VM, one at a time, and
  # read the plan before applying. Which VM gets which ID is DOC-001's (#59)
  # decision, not this file's.
  vm_id = each.value.vm_id

  clone {
    vm_id = each.value.template_vmid
  }

  cpu {
    cores = each.value.cores
  }

  # FEAT-009-A1. Emitted only when the VM asks for a size. With disk_gb unset
  # this block produces nothing and the resource is exactly what it was before
  # FEAT-009 - verified against a plan with a disk already in state, which
  # showed no diff. Always declaring a disk block would instead make this
  # factory start managing a disk it did not create, on every VM that already
  # exists.
  #
  # FEAT-009-A3, measured against a plan with real-shaped state:
  #
  #   50 -> 80   in-place update, no replacement
  #   50 -> 50   no diff
  #   50 -> 20   in-place update, no replacement  <- and this one is a trap
  #
  # Proxmox cannot shrink a disk. `qm resize` refuses it outright, and there is
  # no safe way to do it from underneath a filesystem. But Terraform has no way
  # to know that: it sees a number going down and plans an ordinary update. So
  # a shrink produces a *clean plan* and then fails during apply, which is the
  # one shape of failure this repository keeps trying to eliminate. The
  # validation rule in locals.tf refuses a shrink it can see - see there for
  # what it can and cannot see.
  #
  # `interface` must name the disk the template already has. Point it at a
  # different one and Proxmox is being asked to attach a second disk rather
  # than grow the first. The default is scsi0 because that is the common
  # Proxmox default; it has NOT been checked against the two templates this
  # factory clones, because no VM in the inventory sets disk_gb yet. Check
  # `qm config <template-vmid>` before the first one does.
  dynamic "disk" {
    for_each = each.value.disk_gb == null ? [] : [1]
    content {
      interface = each.value.disk_interface
      size      = each.value.disk_gb
    }
  }

  memory {
    dedicated = each.value.memory_mb
  }

  network_device {
    bridge = var.bridge
  }

  # FEAT-002-A3. Both templates install and start qemu-guest-agent, and the VM
  # resource never asked for it - so `qm agent <vmid> ping` answered "No QEMU
  # guest agent configured" on every guest this factory has built, and the
  # provider had no way to read back an address. That is why vm_inventory
  # reports the string "dhcp" for a DHCP guest rather than the address it has.
  #
  # The trade-off this introduces: with the agent enabled the provider waits for
  # it during create, so a guest whose agent never starts fails the apply after
  # the timeout rather than succeeding with an unknown address. That is the
  # right direction to be wrong in - a VM the lab cannot query is a VM the
  # destroy path cannot shut down gracefully either - but it does mean a broken
  # guest image now fails the apply instead of quietly producing a VM.
  agent {
    enabled = true
  }

  # FEAT-002-A5. Without these a guest that will not shut down gracefully holds
  # the destroy open indefinitely, and the destroy holds the terraform-lab-state
  # concurrency group with it - so one unresponsive Windows VM blocks every
  # later apply as well. Five minutes is generous for a lab guest and finite,
  # which is the property that matters.
  timeout_shutdown_vm = 300
  timeout_stop_vm     = 300

  initialization {
    datastore_id = var.vm_datastore_id

    dynamic "user_account" {
      for_each = each.value.os == "linux" ? [1] : []
      content {
        username = "ubuntu"
        keys     = [var.ssh_public_key]

        # SEC-001b. The provider sends this to Proxmox as cipassword instead of
        # the template writing it into the snippet with chpasswd. It is still an
        # attribute in state - marked sensitive by the provider - but it is no
        # longer free text inside a rendered document sitting in
        # /var/lib/vz/snippets/ for the life of the VM.
        #
        # This only works because the first-boot config is attached as
        # `cicustom: vendor=` (SPIKE-003, #124). Under the old `user=`
        # attachment Proxmox's generated user-data was discarded, and cipassword
        # with it - the password would have reached nothing.
        #
        # null rather than "" when unset: an empty string is a password Proxmox
        # would set, and a blank password on a passwordless-sudo account is the
        # outcome SEC-007 (#125) exists to prevent.
        password = var.linux_vm_password
      }
    }

    dns {
      servers = local.dns_servers
      domain  = var.search_domain
    }

    ip_config {
      ipv4 {
        address = each.value.network.type == "dhcp" ? "dhcp" : each.value.network.address
        gateway = each.value.network.type == "dhcp" ? null : each.value.network.gateway
      }
    }

    # SPIKE-003. vendor_data_file_id, not user_data_file_id - see the comment on
    # the file resource. The user_account block above is the thing this restores:
    # it only ever reached a guest through the user-data Proxmox generates, and
    # overriding that discarded it.
    #
    # BUG-012. This is composed from source_raw[0].file_name rather than read off
    # the file resource's `.id`, and the difference is the whole issue.
    #
    # Both `source_raw.data` and `vendor_data_file_id` are ForceNew in the
    # provider. So: the rendered vendor-data changes -> the snippet resource is
    # *replaced* -> every computed attribute of a replaced resource, `.id`
    # included, is unknown at plan time -> vendor_data_file_id is unknown ->
    # the VM is replaced too.
    #
    # arc_access_token above is minted per run (SEC-001a), so the rendered
    # document differed on every single apply, and that chain fired every time.
    # Measured on this repository: run 33180698859 - a merge commit changing no
    # configuration at all - planned "3 to destroy" and rebuilt VM 100. So did
    # an apply whose only change was comments in a .tftpl, and one whose only
    # change was validation rules in locals.tf. Every push to main was
    # destroying and recreating the lab's guests, losing everything in them.
    #
    # source_raw[0].file_name is set from configuration rather than computed, so
    # it is known during the snippet's replacement and this expression stays
    # stable across it. It is still a reference to the file resource, so
    # Terraform keeps the dependency edge and still creates the snippet before
    # the VM that consumes it - what it no longer does is treat a rewritten
    # snippet as a reason to rebuild the guest.
    #
    # The policy this settles (BUG-012-A2): a vendor-data change replaces the
    # *snippet*, visibly, and leaves the VM alone. It therefore does not reach
    # an already-running guest - cloud-init does not re-run first-boot logic -
    # and getting it there is a deliberate `terraform apply -replace`. See
    # docs/guest-config-changes.md. A silent no-op would be the unacceptable
    # outcome; a rebuild nobody asked for is the one that was actually happening.
    # OPS-004 (#176). Which slot the first-boot document goes in, per OS,
    # because the two guests read different ones and until now both were given
    # the same answer.
    #
    # Measured on VM 101 on 2026-08-30: no C:\cloudbase-firstboot-test.log and
    # no run-once marker - both written among the first actions of
    # windows.yaml.tftpl. The hostname *was* correct, so Cloudbase-Init ran and
    # applied SetHostNamePlugin from the meta-data Proxmox generates. The
    # document this repository writes never executed.
    #
    # Confirmed in /usr/share/perl5/PVE/QemuServer/Cloudinit.pm on the node,
    # which is the part that turns a symptom into a cause:
    #
    #   generate_nocloud       -> '/vendor-data'
    #   generate_configdrive2  -> '/openstack/latest/vendor_data.json'
    #
    # So the vendor-data *is* delivered to Windows. It lands in an OpenStack
    # JSON slot, and Cloudbase-Init's ConfigDrive service reads `user_data`
    # through UserDataPlugin - nothing in the template's eight plugins executes
    # vendor_data.json. Delivered, never run, which is exactly what the guest
    # shows.
    #
    # SPIKE-003's reasoning holds for Linux and is unchanged: overriding
    # user-data there discards ciuser, cipassword and sshkeys, and key
    # authentication to every Linux guest silently never worked because of it.
    # The mistake was applying one guest's answer to both.
    #
    # What Windows gives up by taking `user=` is much less than Linux would.
    # Proxmox's generated user-data carries ciuser and cipassword; main.tf emits
    # no user_account for Windows, so there is no cipassword to lose, and the
    # first-boot script sets the Administrator password itself. sshkeys is not a
    # Windows concept here.
    #
    # It does cost one thing, and ADR 0001 section 9 now records it: the script
    # and `cipassword` are mutually exclusive on Windows, because one needs
    # Proxmox's generated user-data and the other replaces it.
    #
    # Expect this to replace the Windows VM. BUG-012 measured these attachments
    # as ForceNew - the provider schema does not show it, which is a plugin
    # framework plan modifier rather than a schema flag - and a rebuild is
    # required here anyway, because cloud-init does not re-run first-boot logic
    # on a guest that has already booted.
    user_data_file_id = each.value.os == "windows" ? "${var.snippets_datastore}:snippets/${proxmox_virtual_environment_file.vendor_data[each.key].source_raw[0].file_name}" : null

    vendor_data_file_id = each.value.os == "windows" ? null : "${var.snippets_datastore}:snippets/${proxmox_virtual_environment_file.vendor_data[each.key].source_raw[0].file_name}"
  }
}
