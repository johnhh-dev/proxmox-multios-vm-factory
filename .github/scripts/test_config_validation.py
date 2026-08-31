#!/usr/bin/env python3
"""Tests for the inventory model: blocking validation (BUG-001) and the
normalization precedence underneath it (BUG-005).

Every rule in `local.validation_errors` must fail `terraform plan` with a
non-zero exit code and name the VM it objects to. This runs one plan per rule
against an inventory that violates exactly that rule, plus one plan against a
valid inventory that must succeed with no warnings.

The precedence cases at the end are the other half. A rule can only reject what
it is shown, and BUG-005 was a defect in what the rules - and the resources -
were shown: `merge` in vms_normalized listed the profile after the VM, so a
profile silently overrode the values an author wrote by hand. That produces no
error to assert on, only a wrong number, so those cases read values back out of
the evaluated module rather than checking an exit code.

The suite is its own canary. If the enforcement in checks.tf ever goes back to a
`check` block - or to anything else advisory - every invalid fixture below plans
successfully and every case fails here.

How a fixture is injected: the inventory lives in `local.vms` in locals.tf, and
a local value cannot be set from outside. So the configuration is copied to a
temporary directory and a Terraform *override file* redefines `local.vms` there.
Override files replace top-level definitions individually, so this is the real
root module - the same normalization, the same lookups, the same rules - with
one value swapped. The copy also loses the `backend "local"` block, so a test
never touches the state on the runner.

Plans are made against a syntactically valid but fake Proxmox token. Nothing
here reaches the hypervisor: creating resources requires no API read at plan
time, so the plan resolves entirely from the configuration.

Usage: python3 test_config_validation.py
Requires: terraform on PATH (or TERRAFORM_BIN), network access for `init`.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TERRAFORM = os.environ.get("TERRAFORM_BIN", "terraform")
OVERRIDE = "zz_validation_test_override.tf"

# Only the shape matters; none of it is used against a real endpoint. The token
# is checked for format by the provider before anything else, so it has to look
# like one. The two guest passwords are non-empty because coalesce() rejects a
# null and an empty string together, which would fail the plan for a reason that
# has nothing to do with validation.
FAKE_VARS = {
    "TF_VAR_proxmox_api_token": "root@pam!test=00000000-0000-0000-0000-000000000000",
    "TF_VAR_ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest test@example",
    "TF_VAR_proxmox_ssh_password": "not-a-real-password",
    "TF_VAR_linux_vm_password": "not-a-real-password",
    "TF_VAR_windows_admin_password": "not-a-real-password",
}

# (name, inventory HCL, expected substring of the error message)
#
# Each fixture violates one rule and nothing else, so the expected message is
# the whole story: if a plan fails for some other reason the substring will not
# match and the case fails.
INVALID_CASES = [
    (
        # BUG-001-A5. This is the fall-through the issue describes: "Linux" is
        # not a key of local.os_defaults, so the lookup in locals.tf hands the
        # VM the Windows template and the Windows cloud-init. Under the `check`
        # block it planned - a Windows clone under a Linux name.
        "invalid os (capitalised)",
        """
        bad-os-01 = {
          os      = "Linux"
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-os-01': invalid os 'Linux' (must be one of: linux, windows)",
    ),
    (
        "invalid profile",
        """
        bad-profile-01 = {
          os      = "linux"
          profile = "enormous"
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-profile-01': invalid profile 'enormous' (must be one of: large, medium, small)",
    ),
    (
        "invalid network type",
        """
        bad-net-01 = {
          os      = "linux"
          network = { type = "manual" }
        }
        """,
        "VM 'bad-net-01': invalid network.type 'manual' (must be 'dhcp' or 'static')",
    ),
    (
        "static without gateway",
        """
        bad-static-01 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.30/24"
          }
        }
        """,
        "VM 'bad-static-01': network.type=static requires network.address and network.gateway",
    ),
    (
        "static without address",
        """
        bad-static-02 = {
          os = "linux"
          network = {
            type    = "static"
            gateway = "192.168.10.1"
          }
        }
        """,
        "VM 'bad-static-02': network.type=static requires network.address and network.gateway",
    ),
    (
        "disk_gb that is not a whole number",
        """
        bad-disk-01 = {
          os      = "linux"
          disk_gb = 40.5
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-disk-01': disk_gb must be a whole number of gigabytes above zero",
    ),
    (
        "disk_gb of zero",
        """
        bad-disk-02 = {
          os      = "linux"
          disk_gb = 0
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-disk-02': disk_gb must be a whole number of gigabytes above zero",
    ),
    (
        # A disk_interface with no disk_gb emits no disk block at all, so the
        # value silently does nothing - the same quiet no-op FEAT-003-A4
        # refuses for network settings.
        "disk_interface set without disk_gb",
        """
        bad-disk-03 = {
          os             = "linux"
          disk_interface = "virtio0"
          network        = { type = "dhcp" }
        }
        """,
        "VM 'bad-disk-03': disk_interface is set but disk_gb is not",
    ),
    (
        # KAN-015. A transport decision on a guest that emits no WinRM block at
        # all. Nothing breaks - which is the problem, and the same one
        # FEAT-003-A4 refuses for a DHCP guest carrying a static address: the
        # inventory records a choice the guest never makes, and the next reader
        # believes it.
        "winrm_allow_unencrypted with WinRM disabled",
        """
        winrm-noop-01 = {
          os      = "windows"
          network = { type = "dhcp" }
          windows = {
            enable_winrm            = false
            winrm_allow_unencrypted = true
          }
        }
        """,
        "VM 'winrm-noop-01': windows.winrm_allow_unencrypted is set but windows.enable_winrm is false",
    ),
    (
        # OPS-003-A1 (#171). The runner is VM 1110 on the node this factory
        # manages, so declaring it is one apply away from destroying the machine
        # running the apply. The inventory guard would not catch it: it compares
        # desired against state, and a newly declared VM is in neither.
        "declaring the runner's own VM id",
        """
        adopt-the-runner = {
          os      = "linux"
          vm_id   = 1110
          network = { type = "dhcp" }
        }
        """,
        "vm_id 1110 is on the protected list",
    ),
    (
        # dns-01. Every first-boot script waits for it to resolve, so managing
        # it means an apply can remove the thing the next apply needs.
        "declaring the resolver every guest waits for",
        """
        adopt-dns = {
          os      = "linux"
          vm_id   = 1103
          network = { type = "dhcp" }
        }
        """,
        "That is dns-01",
    ),
    (
        # wg-vpn-01. The management path in - the one where the mistake ends the
        # session it was made in.
        "declaring the management path into the lab",
        """
        adopt-vpn = {
          os      = "linux"
          vm_id   = 1104
          network = { type = "dhcp" }
        }
        """,
        "That is wg-vpn-01",
    ),
    (
        # elastic-01 is on the list for a different reason from the other three
        # - not circularity, but because the lab owner said so. The rule does
        # not distinguish, and should not: a deny-list entry is an entry.
        "declaring a VM the operator has declared off limits",
        """
        adopt-elastic = {
          os      = "linux"
          vm_id   = 1105
          network = { type = "dhcp" }
        }
        """,
        "That is elastic-01, which the lab owner has declared off limits",
    ),
    (
        # And the message has to say which one it is, because "protected" alone
        # tells an operator nothing about why their plan was refused.
        "the refusal names the runner",
        """
        adopt-the-runner-2 = {
          os      = "linux"
          vm_id   = 1110
          network = { type = "dhcp" }
        }
        """,
        "destroying it would terminate the run doing the destroying",
    ),
    (
        # FEAT-002-A2. Below 100 is reserved by Proxmox and refused at the API,
        # which would surface as a failed create - and a failed create is the
        # orphan case, so it is worth a plan-time refusal instead.
        "vm_id below the Proxmox floor",
        """
        bad-vmid-01 = {
          os      = "linux"
          vm_id   = 99
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-vmid-01': vm_id 99 is out of range",
    ),
    (
        "vm_id above the Proxmox ceiling",
        """
        bad-vmid-02 = {
          os      = "linux"
          vm_id   = 1000000000
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-vmid-02': vm_id 1000000000 is out of range",
    ),
    (
        "vm_id that is not a whole number",
        """
        bad-vmid-03 = {
          os      = "linux"
          vm_id   = 100.5
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-vmid-03': vm_id must be a whole number",
    ),
    (
        # Proxmox would refuse the second create, but only after building the
        # first - an apply that fails halfway with one VM made and one not.
        "two VMs declaring the same vm_id",
        """
        dup-vmid-01 = {
          os      = "linux"
          vm_id   = 150
          network = { type = "dhcp" }
        }

        dup-vmid-02 = {
          os      = "linux"
          vm_id   = 150
          network = { type = "dhcp" }
        }
        """,
        "VM 'dup-vmid-01': vm_id 150 is also declared by dup-vmid-02",
    ),
    (
        # FEAT-003-A1. 300 is not an octet. The shape is right, which is the
        # point - a regex alone would pass this, and cidrhost() is what rejects
        # it.
        "static address with an out-of-range octet",
        """
        bad-addr-01 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.300/24"
            gateway = "192.168.10.1"
          }
        }
        """,
        "VM 'bad-addr-01': network.address '192.168.10.300/24' is not an IPv4 CIDR",
    ),
    (
        # A bare host address where a CIDR belongs. Proxmox needs the prefix to
        # know the subnet mask; without one the guest gets no usable route.
        "static address with no prefix",
        """
        bad-addr-02 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20"
            gateway = "192.168.10.1"
          }
        }
        """,
        "VM 'bad-addr-02': network.address '192.168.10.20' is not an IPv4 CIDR",
    ),
    (
        # /33 does not exist in IPv4. Matches the pattern, fails cidrhost().
        "static address with an impossible prefix",
        """
        bad-addr-03 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/33"
            gateway = "192.168.10.1"
          }
        }
        """,
        "VM 'bad-addr-03': network.address '192.168.10.20/33' is not an IPv4 CIDR",
    ),
    (
        # FEAT-003-A1. The gateway carrying a prefix, which is the way this one
        # is usually got wrong - the address beside it has one.
        "gateway with a prefix",
        """
        bad-gw-01 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/24"
            gateway = "192.168.10.1/24"
          }
        }
        """,
        "VM 'bad-gw-01': network.gateway '192.168.10.1/24' is not a bare IPv4 address",
    ),
    (
        "gateway that is not an address at all",
        """
        bad-gw-02 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/24"
            gateway = "router.lab.local"
          }
        }
        """,
        "VM 'bad-gw-02': network.gateway 'router.lab.local' is not a bare IPv4 address",
    ),
    (
        # FEAT-003-A2. Both values are well-formed. Only the pairing is wrong,
        # and this is the case that used to plan clean and boot a guest with no
        # route off its own network.
        "gateway outside the address's subnet",
        """
        bad-gw-03 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/24"
            gateway = "192.168.11.1"
          }
        }
        """,
        "VM 'bad-gw-03': network.gateway 192.168.11.1 is outside the subnet of network.address 192.168.10.20/24",
    ),
    (
        # FEAT-003-A3. Same address, two VMs. Each message names the other.
        "duplicate static address across two VMs",
        """
        dup-01 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/24"
            gateway = "192.168.10.1"
          }
        }

        dup-02 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/24"
            gateway = "192.168.10.1"
          }
        }
        """,
        "VM 'dup-01': network.address 192.168.10.20 is also used by dup-02",
    ),
    (
        # A duplicate is about the host, not the notation. Different prefixes,
        # same address, same collision on the wire.
        "duplicate static address written with different prefixes",
        """
        dup-mask-01 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/24"
            gateway = "192.168.10.1"
          }
        }

        dup-mask-02 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/16"
            gateway = "192.168.10.1"
          }
        }
        """,
        "VM 'dup-mask-01': network.address 192.168.10.20 is also used by dup-mask-02",
    ),
    (
        # FEAT-003-A4. Nothing breaks at apply - main.tf sends null for a DHCP
        # guest regardless - which is exactly why it needs saying. The inventory
        # claims an address the guest will never hold.
        "dhcp with a static address set",
        """
        dhcp-addr-01 = {
          os = "linux"
          network = {
            type    = "static"
            address = "192.168.10.20/24"
            gateway = "192.168.10.1"
          }
        }

        dhcp-addr-02 = {
          os = "linux"
          network = {
            type    = "dhcp"
            address = "192.168.10.21/24"
          }
        }
        """,
        "VM 'dhcp-addr-02': network.type=dhcp, but the inventory also sets network.address",
    ),
    (
        "dhcp with both address and gateway set",
        """
        dhcp-both-01 = {
          os = "linux"
          network = {
            type    = "dhcp"
            address = "192.168.10.21/24"
            gateway = "192.168.10.1"
          }
        }
        """,
        "VM 'dhcp-both-01': network.type=dhcp, but the inventory also sets network.address and network.gateway",
    ),
    (
        # String form. The tag string is interpolated into the azcmagent command
        # line in the guest bootstrap; a newline ends that command.
        "arc tags string with a newline",
        r"""
        bad-tags-01 = {
          os      = "linux"
          network = { type = "dhcp" }
          arc = {
            enabled = true
            tags    = "role=web\nowner=lab"
          }
        }
        """,
        "VM 'bad-tags-01': arc.tags contains invalid characters (newline or quote)",
    ),
    (
        # Map form reaching the same rule through the rendered string, so the
        # hardening is not escaped by writing the tags the other way.
        "arc tags map with a quote",
        r"""
        bad-tags-02 = {
          os      = "linux"
          network = { type = "dhcp" }
          arc = {
            enabled = true
            tags    = { role = "we\"b" }
          }
        }
        """,
        "VM 'bad-tags-02': arc.tags contains invalid characters (newline or quote)",
    ),
    (
        "arc tags map value containing a comma",
        """
        bad-tags-03 = {
          os      = "linux"
          network = { type = "dhcp" }
          arc = {
            enabled = true
            tags    = { role = "web,db" }
          }
        }
        """,
        "VM 'bad-tags-03': arc.tags map keys/values may not contain ',' or '='",
    ),
    (
        "arc tags map key containing an equals sign",
        """
        bad-tags-04 = {
          os      = "linux"
          network = { type = "dhcp" }
          arc = {
            enabled = true
            tags    = { "ro=le" = "web" }
          }
        }
        """,
        "VM 'bad-tags-04': arc.tags map keys/values may not contain ',' or '='",
    ),
]

# BUG-020-A5. One case per rule, in the shapes the issue names: an underscore,
# an uppercase letter, a hyphen left on the end, a Windows name one character
# over the NetBIOS limit, a DNS label one character over 63, and an Arc resource
# name carrying a character Azure will not take.
#
# The keys are quoted because several of them are deliberately not identifiers.
# (name, inventory HCL, environment overrides, expected substring)
#
# SEC-007. Every rule above objects to something in the inventory. This one
# objects to a combination of *variables* - password SSH turned on with no
# password behind it - so the fixture is an ordinary Linux VM and the violation
# is in the environment. The inventory still has to contain a Linux guest,
# because the rule is deliberately silent for a Windows-only lab that never set
# the flag.
VARIABLE_CASES = [
    (
        # FEAT-009-A2. The rule only exists once the operator states the
        # template's size, because Terraform cannot read it. This is the case
        # that matters: Proxmox cannot shrink a disk, but Terraform plans a
        # shrink as an ordinary in-place update - so without this rule the
        # operator gets a clean plan and an apply that fails at the hypervisor.
        "disk_gb smaller than the declared template disk",
        """
        shrink-01 = {
          os      = "linux"
          disk_gb = 20
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_template_disk_gb_linux": "50"},
        "VM 'shrink-01': disk_gb 20 is smaller than the linux template's disk (50 GB)",
    ),
    (
        # SEC-001c. The template already refuses this - it throws on an empty
        # decode - but at first boot, inside the guest, from a script whose
        # throw abandons everything after it: no rename, no RDP, no Arc. The VM
        # exists, boots, and is unreachable, and the only record is a log on a
        # machine nobody can log in to. A plan-time refusal happens before the
        # clone.
        "a Windows VM with no administrator password",
        """
        win-nopw-01 = {
          os      = "windows"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_windows_admin_password": ""},
        "a Windows VM is declared but windows_admin_password is empty",
    ),
    (
        # KAN-012-A4. An empty string in the resolver list is not an absent
        # resolver - it is a resolver named "". It reaches
        # initialization.dns.servers on every VM and, on Windows, the
        # PowerShell array the first-boot script sets DNS from. The failure is
        # the quiet kind BUG-018 described: DNS still works, via whatever DHCP
        # offered, and it is not the DNS anybody configured.
        #
        # It matters more since the topology became settable from repository
        # variables, because a mistyped value now reaches a plan without anyone
        # reviewing a diff.
        "an empty primary resolver",
        """
        dns-empty-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_dns_server": " "},
        "is not a bare IPv4 address",
    ),
    (
        "a resolver written as a hostname",
        """
        dns-host-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_dns_server": "resolver.lab.internal"},
        "resolver 'resolver.lab.internal' is not a bare IPv4 address",
    ),
    (
        # A prefix is the way to get this wrong that looks most like a working
        # value - the static addresses in locals.tf next to it carry one.
        "a resolver written with a prefix",
        """
        dns-cidr-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_dns_server": "192.168.10.2/24"},
        "is not a bare IPv4 address",
    ),
    (
        "an impossible resolver address",
        """
        dns-bad-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_dns_server": "999.1.1.1"},
        "is not a bare IPv4 address",
    ),
    (
        "a blank entry in the fallback list",
        """
        dns-fb-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_dns_servers_fallback": '["192.168.10.1", ""]'},
        "is not a bare IPv4 address",
    ),
    (
        # KAN-011-A3. Set-NetFirewallRule takes -RemoteAddress as written, and
        # a malformed entry is not an error there - it is a rule that matches
        # something other than what the author meant, on a guest that is
        # already up by the time anyone could see it.
        "a management source that is not a CIDR",
        """
        mgmt-cidr-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_management_source_cidrs": '["192.168.10.0"]'},
        "is not an IPv4 CIDR",
    ),
    (
        # A prefix that cannot exist. The regex alone accepts it, which is why
        # the rule asks cidrhost() as well - the same pairing FEAT-003 uses.
        "a management source with an impossible prefix",
        """
        mgmt-cidr-02 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_management_source_cidrs": '["192.168.10.0/33"]'},
        "is not an IPv4 CIDR",
    ),
    (
        # KAN-011-A3's refusal. Each half was reviewed on its own and accepted:
        # SEC-008-A5 measured the transport exposure, and the unrestricted
        # firewall rule is what the template always did. The combination puts a
        # recoverable credential on the wire for any source the bridge reaches.
        "unencrypted WinRM with no source restriction",
        """
        winrm-open-01 = {
          os      = "windows"
          network = { type = "dhcp" }
          windows = {
            enable_winrm            = true
            winrm_allow_unencrypted = true
          }
        }
        """,
        {"TF_VAR_management_source_cidrs": "[]"},
        "recoverable from the wire on port 5985 and the firewall rule accepts any source",
    ),
    (
        # The other side: the rule must stay silent for a lab that has no
        # Windows guest at all, or a Linux-only lab is told to set a secret
        # nothing would read.
        "password auth enabled with an empty password",
        """
        pwauth-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_linux_password_auth": "true", "TF_VAR_linux_vm_password": ""},
        "linux_password_auth is on but linux_vm_password is empty",
    ),
    (
        # OPS-005. A two-node cluster, and both halves of "which node" are
        # repository variables since KAN-012 - so pointing one at pve2 without
        # the other is a web form away, and the way it fails is a VM created on
        # one node with its snippet uploaded to the other.
        "a node name the provider has no SSH route to",
        """
        wrong-node-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_proxmox_node_name": "pve3"},
        "which is not a key of proxmox_ssh_nodes",
    ),
    (
        "no SSH nodes at all",
        """
        no-nodes-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_proxmox_ssh_nodes": "{}"},
        "proxmox_ssh_nodes is empty",
    ),
    (
        # SEC-006-A3. The one node SSH configuration that cannot work. Worth a
        # plan-time refusal because snippet upload happens *after* the clone -
        # so the alternative is an apply that builds a VM and then discovers it
        # has nowhere to put the VM's configuration.
        "no node SSH identity at all",
        """
        ssh-none-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_proxmox_ssh_password": "", "TF_VAR_proxmox_ssh_agent": "false"},
        "no node SSH identity is configured",
    ),
    (
        "agent socket set with the agent off",
        """
        ssh-sock-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {
            "TF_VAR_proxmox_ssh_agent": "false",
            "TF_VAR_proxmox_ssh_agent_socket": "/tmp/agent.sock",
        },
        "proxmox_ssh_agent_socket is set but proxmox_ssh_agent is false",
    ),
]

# (name, inventory HCL, environment overrides)
#
# The other side of the SEC-007 rule. A rule that blocks everything is as broken
# as one that blocks nothing, and the two combinations below are the ones an
# operator will actually have: keys only, and password SSH deliberately on with
# a password to go with it.
VARIABLE_ALLOWED_CASES = [
    (
        # OPS-005, the other side. Selecting the second cluster node must work
        # on its own - that is the whole point of the map, and a rule that
        # demanded the default node would block the move this exists to allow.
        "the second cluster node, selected alone",
        """
        on-pve2-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_proxmox_node_name": "pve2"},
    ),
    (
        # Growing past the template is the whole point of the feature.
        "disk_gb larger than the declared template disk",
        """
        grow-01 = {
          os      = "linux"
          disk_gb = 80
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_template_disk_gb_linux": "50"},
    ),
    (
        # KAN-011-A3, the other side of the refusal. The transport exposure is
        # still accepted where an operator asks for it - what is refused is
        # asking for it with nowhere named. One CIDR satisfies the rule.
        "unencrypted WinRM with the sources named",
        """
        winrm-scoped-01 = {
          os      = "windows"
          network = { type = "dhcp" }
          windows = {
            enable_winrm            = true
            winrm_allow_unencrypted = true
          }
        }
        """,
        {"TF_VAR_management_source_cidrs": '["192.168.10.0/24", "10.8.0.0/24"]'},
    ),
    (
        # And the default. An empty list is what every lab has today, and it
        # must plan clean - this change narrows nothing on its own.
        "no management sources named at all",
        """
        mgmt-default-01 = {
          os      = "windows"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_management_source_cidrs": "[]"},
    ),
    (
        # SEC-001c, the negative side of the Windows rule: a Linux-only
        # inventory with no Windows password must plan clean.
        "no Windows VM and no Windows password",
        """
        linux-only-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_windows_admin_password": ""},
    ),
    (
        # KAN-012-A4, the other side. A single resolver with no fallback is a
        # perfectly ordinary lab, and a rule that demanded two would be blocking
        # a supported configuration.
        "one resolver and an empty fallback list",
        """
        dns-one-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_dns_server": "10.0.0.53", "TF_VAR_dns_servers_fallback": "[]"},
    ),
    (
        "password auth off with no password",
        """
        pwauth-off-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_linux_password_auth": "false", "TF_VAR_linux_vm_password": ""},
    ),
    (
        "password auth on with a password",
        """
        pwauth-on-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_linux_password_auth": "true", "TF_VAR_linux_vm_password": "s3cret"},
    ),
    # SEC-006-A3. Each of the three identities on its own. A rule that only
    # blocked the empty case could still be blocking two of the three ways to
    # fix it, and nothing above would notice.
    (
        "node SSH by private key, with no password",
        """
        ssh-key-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {
            "TF_VAR_proxmox_ssh_password": "",
            # The rule only asks whether this is non-blank; the provider is
            # never asked to parse it, because `command = plan` reaches no
            # hypervisor. A single line keeps a PEM body out of the repository,
            # which the secret scanner would be right to object to.
            "TF_VAR_proxmox_ssh_private_key": "not-a-real-private-key",
        },
    ),
    (
        "node SSH by agent, with no credential of any kind",
        """
        ssh-agent-01 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {"TF_VAR_proxmox_ssh_password": "", "TF_VAR_proxmox_ssh_agent": "true"},
    ),
    (
        "node SSH by agent with an explicit socket",
        """
        ssh-agent-02 = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        {
            "TF_VAR_proxmox_ssh_password": "",
            "TF_VAR_proxmox_ssh_agent": "true",
            "TF_VAR_proxmox_ssh_agent_socket": "/run/user/1000/keyring/ssh",
        },
    ),
]

NAME_CASES = [
    (
        "name with an underscore",
        """
        "bad_name_01" = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad_name_01': invalid name - use lowercase letters, digits and inner hyphens only",
    ),
    (
        "name with an uppercase letter",
        """
        "Bad-Name-01" = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        "VM 'Bad-Name-01': invalid name - use lowercase letters, digits and inner hyphens only",
    ),
    (
        "name with a trailing hyphen",
        """
        "bad-name-" = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        "VM 'bad-name-': invalid name - use lowercase letters, digits and inner hyphens only",
    ),
    (
        # 16 characters, and a valid DNS label - which is the point of the case.
        # Only the Windows rule may object to this name.
        "windows name one character over the NetBIOS limit",
        """
        "win-srv-01234567" = {
          os      = "windows"
          network = { type = "dhcp" }
        }
        """,
        "VM 'win-srv-01234567': name is 16 characters; a Windows VM stops at 15",
    ),
    (
        # 64 characters on a Linux VM, so the Windows rule cannot be what fires.
        "name one character over the DNS label limit",
        """
        "aaaaaaaaaa-bbbbbbbbbb-cccccccccc-dddddddddd-eeeeeeeeee-fffffffff" = {
          os      = "linux"
          network = { type = "dhcp" }
        }
        """,
        "name is 64 characters; a DNS label stops at 63",
    ),
    (
        "arc.resource_name containing a space",
        """
        "arc-name-01" = {
          os      = "linux"
          network = { type = "dhcp" }
          arc = {
            enabled       = true
            resource_name = "not a valid name"
          }
        }
        """,
        "VM 'arc-name-01': invalid arc.resource_name 'not a valid name'",
    ),
]

# BUG-005. Read back through the same normalization the resources use.
# `terraform console` and not `terraform show -json`: plan JSON carries a
# `variables` block holding every input in cleartext (SEC-002), and the apply
# workflow's inventory guard already chose console over plan JSON for that
# reason. Nothing here needs a plan - the values are settled by evaluation.
#
# template_vmid is probed alongside cores/memory_mb because it is the attribute
# BUG-005-A2 protects: it must stay derived from `os` no matter what the VM
# says. vendor_data_tpl is deliberately not probed - it interpolates path.module,
# so asserting on it would compare temporary directory paths.
PRECEDENCE_PROBE = (
    "jsonencode({for k, v in local.vms_final : k => {"
    "cores = v.cores, memory_mb = v.memory_mb, template_vmid = v.template_vmid"
    "}})"
)

# (name, inventory HCL, {vm: {attribute: expected}})
PRECEDENCE_CASES = [
    (
        "profile only",
        """
        prof-only-01 = {
          os      = "linux"
          profile = "large"
          network = { type = "dhcp" }
        }
        """,
        {"prof-only-01": {"cores": 8, "memory_mb": 16384}},
    ),
    (
        "explicit only",
        """
        explicit-01 = {
          os        = "linux"
          cores     = 6
          memory_mb = 12288
          network   = { type = "dhcp" }
        }
        """,
        {"explicit-01": {"cores": 6, "memory_mb": 12288}},
    ),
    (
        # The regression itself. Before BUG-005 this VM planned with two cores,
        # because `small` sat to the right of the explicit 8 in the merge.
        # memory_mb is asserted in the same case on purpose: the profile must
        # still supply what the VM did not state, or "explicit wins" would have
        # been implemented by ignoring the profile altogether.
        "profile plus an explicit override",
        """
        override-01 = {
          os      = "linux"
          profile = "small"
          cores   = 8
          network = { type = "dhcp" }
        }
        """,
        {"override-01": {"cores": 8, "memory_mb": 4096}},
    ),
    (
        # BUG-005-A2. `os` decides the template, and a VM may not take that
        # decision back - the pairing of a Linux OS with a Windows image is not
        # something local.validation_errors can catch, because by the time it
        # looks the two agree with each other.
        "os keeps deciding the template",
        """
        os-pin-01 = {
          os            = "linux"
          template_vmid = 1234
          network       = { type = "dhcp" }
        }
        """,
        {"os-pin-01": {"template_vmid": 9900}},
    ),
]

# One inventory covering every valid form the README and locals.tf document:
# both operating systems, both network types, a profile, and all four shapes of
# `arc` including both tag forms (BUG-006).
#
# FEAT-003. Three static VMs rather than one, because the network rules are as
# capable of blocking too much as too little and the negative side needs a
# fixture: two distinct addresses on one subnet must not read as a duplicate,
# and ubuntu-static-03 carries a /16 so the gateway-in-subnet check is exercised
# against a mask it cannot get right by assuming /24 - 10.0.0.1 is inside
# 10.0.5.7/16 and would be outside the same address read as /24.
VALID_CASE = """
ubuntu-static-01 = {
  os        = "linux"
  cores     = 2
  memory_mb = 4096
  network = {
    type    = "static"
    address = "192.168.10.30/24"
    gateway = "192.168.10.1"
  }
  arc = true
}

ubuntu-static-02 = {
  os      = "linux"
  vm_id   = 150
  network = {
    type    = "static"
    address = "192.168.10.31/24"
    gateway = "192.168.10.1"
  }
  arc = false
}

ubuntu-static-03 = {
  os      = "linux"
  network = {
    type    = "static"
    address = "10.0.5.7/16"
    gateway = "10.0.0.1"
  }
  arc = false
}

ubuntu-dhcp-01 = {
  os      = "linux"
  profile = "medium"
  network = { type = "dhcp" }
  arc     = false
}

ubuntu-dhcp-02 = {
  os      = "linux"
  network = { type = "dhcp" }
}

win-srv-01 = {
  os      = "windows"
  profile = "large"
  network = { type = "dhcp" }
  arc = {
    enabled       = true
    resource_name = "win-srv-01-arc"
    tags          = "role=web,env=lab"
  }
}

win-srv-02 = {
  os      = "windows"
  network = { type = "dhcp" }
  arc = {
    enabled = true
    tags    = { role = "db", env = "lab" }
  }
}

# KAN-015. The other side of the no-op rule. Turning the old transport back on
# is a supported, documented choice - an S2 exposure the operator opted into -
# so it has to plan, and a rule that blocked it would be as broken as one that
# never fired. win-srv-01 and win-srv-02 above cover the default, which is the
# flag absent entirely.
#
# KAN-011-A3 narrowed what "supported" means without changing that. The choice
# is still allowed; what is refused is making it with no source restriction, so
# the plan below is given management_source_cidrs.
win-winrm-01 = {
  os      = "windows"
  network = { type = "dhcp" }
  windows = {
    winrm_allow_unencrypted = true
  }
}
"""


def indent(text: str) -> str:
    return "\n".join("    " + line for line in text.strip().splitlines())


def unwrap(text: str) -> str:
    """Collapse whitespace so a match survives Terraform's line wrapping.

    Terraform hard-wraps diagnostic bodies at the terminal width, so the longer
    messages arrive split across lines at a point that depends on the message,
    not on the rule. Matching on a single-spaced form compares the words.
    """
    return re.sub(r"\s+", " ", text)


def build_workdir(tmp: str) -> str:
    """Copy the root module into tmp, without its backend."""
    work = os.path.join(tmp, "config")
    os.makedirs(work)

    for entry in os.listdir(REPO):
        if entry.endswith(".tf") or entry == ".terraform.lock.hcl":
            shutil.copy2(os.path.join(REPO, entry), work)
    shutil.copytree(os.path.join(REPO, "cloudinit"), os.path.join(work, "cloudinit"))

    # `terraform init` would otherwise want the state path on the runner, which
    # a test has no business reading or locking.
    providers = os.path.join(work, "providers.tf")
    with open(providers, "r", encoding="utf-8") as f:
        text = f.read()
    stripped = re.sub(r'\n\s*backend "local" \{[^}]*\}\n', "\n", text)
    if stripped == text:
        raise SystemExit(
            'FAIL: no `backend "local"` block found in providers.tf to strip. '
            "If the backend moved, update this harness - do not let it run "
            "against the real state."
        )
    with open(providers, "w", encoding="utf-8") as f:
        f.write(stripped)

    return work


def write_inventory(work: str, inventory_hcl: str) -> None:
    with open(os.path.join(work, OVERRIDE), "w", encoding="utf-8") as f:
        f.write("locals {\n  vms = {\n%s\n  }\n}\n" % inventory_hcl)


def run(work: str, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(FAKE_VARS)
    # SEC-007. Applied after FAKE_VARS so a case can blank one of them. An
    # environment variable beats a default in an override file, so a rule about
    # an *unset* secret cannot be exercised any other way.
    env.update(extra_env or {})
    env["TF_IN_AUTOMATION"] = "1"
    return subprocess.run(
        [TERRAFORM, *args],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )


def plan(work: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    return run(
        work, "plan", "-input=false", "-no-color", "-lock=false", extra_env=extra_env
    )


def console(work: str, expression: str) -> dict:
    """Evaluate one expression against the test module and decode the result.

    The expression is a jsonencode(), so console prints a *string* - a quoted,
    escaped JSON document. Hence the second decode.

    -input=false, and stdin closed by handing subprocess the whole expression:
    BUG-022/BUG-023 was a `terraform console` left waiting on a pipe nobody
    wrote to and nobody closed. This harness runs the real binary rather than
    setup-terraform's wrapper, so the shim that caused it is not in the way -
    but the timeout is here anyway, because a block should be a red test in
    seconds rather than a job that hangs until the runner gives up.
    """
    env = dict(os.environ)
    env.update(FAKE_VARS)
    env["TF_IN_AUTOMATION"] = "1"
    result = subprocess.run(
        [TERRAFORM, "console", "-no-color", "-input=false"],
        cwd=work,
        env=env,
        input=expression,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "terraform console exited %d\n%s"
            % (result.returncode, result.stdout + result.stderr)
        )
    return json.loads(json.loads(result.stdout.strip()))


def main() -> int:
    if shutil.which(TERRAFORM) is None and not os.path.isfile(TERRAFORM):
        print(f"FAIL: terraform not found ({TERRAFORM})", file=sys.stderr)
        return 2

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        work = build_workdir(tmp)

        init = run(work, "init", "-input=false", "-no-color")
        if init.returncode != 0:
            print(init.stdout + init.stderr, file=sys.stderr)
            print("FAIL: terraform init failed in the test copy", file=sys.stderr)
            return 2

        for name, inventory, expected in INVALID_CASES + NAME_CASES:
            write_inventory(work, inventory)
            result = plan(work)
            output = result.stdout + result.stderr

            if result.returncode == 0:
                failures.append(f"{name}: plan succeeded; the rule did not block")
                continue
            if unwrap(expected) not in unwrap(output):
                failures.append(
                    f"{name}: plan failed, but not with the expected message.\n"
                    f"  expected: {expected}\n"
                    f"  got:\n{indent(output)}"
                )
                continue
            print(f"ok   blocked: {name}")

        for name, inventory, extra_env, expected in VARIABLE_CASES:
            write_inventory(work, inventory)
            result = plan(work, extra_env)
            output = result.stdout + result.stderr

            if result.returncode == 0:
                failures.append(f"{name}: plan succeeded; the rule did not block")
                continue
            if unwrap(expected) not in unwrap(output):
                failures.append(
                    f"{name}: plan failed, but not with the expected message.\n"
                    f"  expected: {expected}\n"
                    f"  got:\n{indent(output)}"
                )
                continue
            print(f"ok   blocked: {name}")

        for name, inventory, extra_env in VARIABLE_ALLOWED_CASES:
            write_inventory(work, inventory)
            result = plan(work, extra_env)
            output = result.stdout + result.stderr

            if result.returncode != 0:
                failures.append(
                    f"{name}: plan failed, but this combination is legitimate\n"
                    f"{indent(output)}"
                )
                continue
            print(f"ok   allowed: {name}")

        write_inventory(work, VALID_CASE)
        # KAN-011-A3. win-winrm-01 asks for the old transport, and that is now
        # only valid with somewhere named to accept it from - the combination
        # rule in locals.tf. The value belongs here rather than in the
        # inventory because it is repository-wide: where an administrator
        # connects from is a property of the lab, not of a VM.
        result = plan(work, {"TF_VAR_management_source_cidrs": '["192.168.10.0/24"]'})
        output = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"valid inventory: plan failed\n{indent(output)}")
        elif "Warning:" in output:
            failures.append(f"valid inventory: plan emitted a warning\n{indent(output)}")
        else:
            print("ok   planned:  valid inventory")

        for name, inventory, expectations in PRECEDENCE_CASES:
            write_inventory(work, inventory)
            try:
                actual = console(work, PRECEDENCE_PROBE)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                failures.append(
                    f"{name}: could not evaluate the inventory\n{indent(str(exc))}"
                )
                continue

            wrong = [
                f"{vm}.{attr} = {actual.get(vm, {}).get(attr)!r}, expected {expected!r}"
                for vm, attrs in expectations.items()
                for attr, expected in attrs.items()
                if actual.get(vm, {}).get(attr) != expected
            ]
            if wrong:
                failures.append(f"{name}: " + "; ".join(wrong))
                continue
            print(f"ok   resolved: {name}")

    if failures:
        print("", file=sys.stderr)
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    print(
        f"\nAll {len(INVALID_CASES) + len(NAME_CASES) + len(VARIABLE_CASES)} rules "
        f"block, the {len(VARIABLE_ALLOWED_CASES)} legitimate variable "
        f"combinations plan clean, the valid inventory plans clean, and all "
        f"{len(PRECEDENCE_CASES)} precedence cases resolve as documented."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
