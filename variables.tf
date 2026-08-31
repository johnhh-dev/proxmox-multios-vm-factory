variable "proxmox_endpoint" {
  type        = string
  description = "Proxmox API endpoint, scheme and port included. Settable per environment as a repository variable (KAN-012)."
  default     = "https://192.168.10.25:8006"
}

variable "proxmox_api_token" {
  type        = string
  description = "Proxmox API token in the form user@realm!tokenid=uuid. See docs/proxmox-api-token.md for which identity this is."
  sensitive   = true
}

variable "proxmox_node_name" {
  type        = string
  description = "Name of the Proxmox node VMs are created on, as it appears in /etc/pve/nodes."
  default     = "pve"
}

variable "template_vmid_linux" {
  type        = number
  description = "Proxmox template VMID for Linux."
  default     = 9900
}

variable "template_vmid_windows" {
  type        = number
  description = "Proxmox template VMID for Windows."
  default     = 9917
}

# OPS-003-A1 (#171). VM IDs this factory must never manage.
#
# gha-runner-01 is VM 1110 on the same node this factory clones into. Measured
# 2026-08-30: `qm list` shows it running with onboot=1, and its MAC resolves to
# 192.168.10.34 in the node's ARP table. So the machine holding Terraform state
# is a guest of the hypervisor whose state it describes.
#
# Nothing else distinguishes 1110 from any other VM ID. The API token is
# Administrator on / with propagate (confirmed on the node), the inventory guard
# compares desired against state and 1110 is in neither, and DOC-001 (#59) is
# the issue that will populate local.vms from an inventory that contains it.
# An import followed by a destroy would terminate the machine running the
# destroy, mid-run, leaving state written or not depending on timing - which is
# the orphan case at the worst possible moment.
#
# A plan-time refusal is the cheap version of preventing that. It is not the
# whole answer: OPS-003-A3 asks whether the runner should be on this node at
# all, and that is the only version that removes the cycle rather than guarding
# it.
#
# Four, and they are on the list for two different reasons. Keeping the
# difference visible matters more than a tidy single rule, because the two
# behave differently when someone asks "should this one be here too?".
#
# Circular - the factory needs it in order to run, so managing it means an
# apply can remove its own prerequisite:
#
#   1110  gha-runner-01  holds the state. Destroying it terminates the run
#                        doing the destroying.
#   1103  dns-01         answers the DNS every first-boot script waits for
#                        before it can install a package or reach Azure.
#   1104  wg-vpn-01      the management path into the lab. Rebuilding it while
#                        working through it is the shape of mistake that ends a
#                        session.
#
# Declared off limits by the operator, which is a sufficient reason on its own:
#
#   1105  elastic-01     added 2026-08-30 on the lab owner's instruction.
#
# **This repository does not know why, and does not guess.** The guest is
# unmanaged, so it runs no agent this factory can query, and nothing here can
# see what it holds. An earlier version of this comment argued elastic-01 did
# *not* belong here because "losing it is recoverable by the thing that lost
# it" - that was reasoning about a machine nobody had looked inside, and the
# person who has is the one who decided.
#
# A deny-list does not require the denier to justify themselves to the code.
# What it requires is that the entry be visible, which is what this comment is
# for - if the reason is ever written down somewhere, link it from here.
#
# Refusing is not the same as protecting. Nothing here stops `qm destroy` on
# the node, and nothing stops an operator removing the ID from this list. It
# stops the plan that nobody meant to write, which is the failure DOC-001 is
# one import away from.
variable "protected_vm_ids" {
  type        = list(number)
  description = "VM IDs this factory refuses to manage - either because it needs them in order to run, or because the operator has declared them off limits (OPS-003, #171)."
  default     = [1110, 1103, 1104, 1105]
}

# FEAT-009-A2. The disk size each template carries, in gigabytes.
#
# Null by default, and that disables the shrink check rather than guessing.
# Terraform cannot read the template's disk size - it lives in Proxmox and
# finding it would mean an API call during plan - so this is how an unknowable
# becomes a declared constant. A floor invented here would block legitimate
# sizes and give false confidence about the rest.
#
# Worth setting, because the failure it catches is the expensive kind: Proxmox
# cannot shrink a disk, but Terraform plans a shrink as an ordinary in-place
# update, so without this an operator gets a clean plan and an apply that fails
# at the hypervisor. Read the real values with `qm config <template-vmid>`.
variable "template_disk_gb_linux" {
  type        = number
  description = "Disk size in GB of the Linux template, enabling the disk_gb shrink check. Null disables it."

  # Measured 2026-08-30, not guessed: `qm config 9900` on the node reports
  # scsi0: zfs-vmstore:base-9900-disk-1,size=50G on ubuntu-template.
  #
  # It was null, which disabled the shrink rule entirely - and that rule exists
  # for a failure that costs an apply: Proxmox cannot shrink a disk, but
  # Terraform plans a shrink as an ordinary in-place update, so without this an
  # operator gets a clean plan and a failure at the hypervisor.
  #
  # Re-measure if the template is rebuilt. A stale value here is worse than null
  # in one direction only - it would reject a legitimate size - and that fails
  # loudly at plan time rather than quietly at apply.
  default = 50
}

variable "template_disk_gb_windows" {
  type        = number
  description = "Disk size in GB of the Windows template, enabling the disk_gb shrink check. Null disables it."

  # Measured 2026-08-30, not guessed: `qm config 9917` on the node reports
  # sata0: zfs-vmstore:base-9917-disk-0,size=100G on win-server-2022-template.
  #
  # It was null, which disabled the shrink rule entirely - and that rule exists
  # for a failure that costs an apply: Proxmox cannot shrink a disk, but
  # Terraform plans a shrink as an ordinary in-place update, so without this an
  # operator gets a clean plan and a failure at the hypervisor.
  #
  # Re-measure if the template is rebuilt. A stale value here is worse than null
  # in one direction only - it would reject a legitimate size - and that fails
  # loudly at plan time rather than quietly at apply.
  default = 100
}
variable "ssh_public_key" {
  type        = string
  description = "Public key installed for the Linux guest login account. Key authentication only reaches a guest because the first-boot config is attached as vendor-data (SPIKE-003)."
}

variable "bridge" {
  type        = string
  description = "Proxmox network bridge every VM's single interface attaches to."
  default     = "vmbr0"
}

variable "dns_server" {
  type        = string
  description = "Primary resolver offered to every guest. Leads local.dns_servers, which reaches a guest twice (BUG-018)."
  default     = "192.168.10.2"
}

# KAN-012-A1/A3. The name a guest resolves to decide its network is ready.
#
# It replaces two different answers to the same question. Windows resolved
# "aka.ms" against each configured resolver in turn; Linux pinged the literal
# 1.1.1.1 and then ran `getent hosts management.azure.com`, which goes through
# /etc/resolv.conf and therefore does not prove the resolvers this factory
# configured are the ones answering. Two definitions of "the network works",
# reached by different means, disagreeing about what counts - which is BUG-018's
# shape one level up, and what KAN-012's acceptance criterion means by "Linux
# and Windows receive the same approved DNS and domain intent".
#
# 1.1.1.1 was also the last environment-specific address embedded in a guest
# script, which that criterion forbids outright. A lab with egress filtering
# would have had every Linux guest wait the full five minutes and then carry on
# regardless, because the loop falls through rather than failing.
#
# aka.ms rather than a resolver address or the gateway: it is a name, so
# resolving it tests DNS rather than just L3, and it is a name both guests
# already need - the Connected Machine agent is downloaded from it, and
# var.arc_install_script_url defaults to https://aka.ms/azcmagent. A lab with no
# internet egress should point this at something local that it does have.
variable "network_probe_host" {
  type        = string
  description = "Hostname a guest resolves at first boot to decide its network and DNS are ready. Must be resolvable from the lab (KAN-012)."
  default     = "aka.ms"
}

# BUG-018. The lab's second resolver is the gateway, which also answers DNS.
# It used to exist only as a literal inside windows.yaml.tftpl: depended on by
# every Windows guest, declared by nothing, and invisible to anyone changing
# var.dns_server. An empty list means "no fallback".
variable "dns_servers_fallback" {
  type        = list(string)
  description = "Resolvers offered to a guest after var.dns_server, in order."
  default     = ["192.168.10.1"]
}

variable "search_domain" {
  type        = string
  description = "DNS search domain, and the suffix the guest FQDN is built from."
  default     = "home"
}

# KAN-011-A3. Which sources may reach a guest's management services.
#
# docs/management-network.md found this by reading the template rather than the
# network: the two flows this repository opens, it opens to everything.
#
#   Enable-NetFirewallRule -DisplayGroup "Remote Desktop"
#   Enable-NetFirewallRule -DisplayGroup "Windows Remote Management"
#
# Neither narrows -RemoteAddress, so each rule is enabled carrying the scope the
# built-in rule ships with, which on a default Windows install is any remote
# address. KAN-011's acceptance criterion is that RDP and WinRM are reachable
# only through approved management sources; today they are reachable from
# anything the bridge will deliver.
#
# ## Why this defaults to unrestricted rather than to the lab subnet
#
# Because the value that would be right is not knowable from here, and the way
# it fails is the expensive way. Section 3 of management-network.md records the
# VPN address range as unknown - it lives in a wg0.conf on a guest this
# repository does not configure - so defaulting to 192.168.10.0/24 would be a
# guess about where an administrator connects from. A guess that is wrong locks
# the operator out of the guest, and the recovery is the Proxmox console.
#
# That is the same shape as rotating a WireGuard key with no second way in,
# which unmanaged-vms.md already calls the mistake that ends a session. So an
# empty list keeps today's behaviour exactly, and the guest's first-boot log
# says which of the two it was given rather than leaving it to be inferred.
#
# ## What is not optional
#
# Basic authentication over an unencrypted transport with no source restriction
# is refused at plan time - see locals.tf. That combination puts the
# administrator credential on the wire for anyone who can reach port 5985, and
# "anyone" is what the unrestricted rule means.
#
# Entries are IPv4 CIDRs, because that is what -RemoteAddress takes and what
# FEAT-003's helpers can already check. A single host is /32.
variable "management_source_cidrs" {
  type        = list(string)
  description = "IPv4 CIDRs allowed to reach RDP and WinRM on a Windows guest. Empty means unrestricted, which is what the built-in rules do today (KAN-011)."
  default     = []
}

variable "snippets_datastore" {
  type        = string
  description = "Datastore holding the cloud-init vendor-data snippets. Must have the snippets content type enabled."
  default     = "local"
}

variable "vm_datastore_id" {
  type        = string
  description = "Datastore for the VM's cloud-init disk (must match a storage pool that actually exists on the node, e.g. from `pvesm status`)."
  default     = "zfs-vmstore"
}
# SEC-006-A1. Certificate validation for the Proxmox API.
#
# Defaults to true - meaning validation is OFF - because that is what the lab
# needs today: Proxmox presents the self-signed certificate a fresh install
# generates, and the runner does not trust it. Defaulting to false would break
# every apply, so this change makes the setting explicit and cheap to flip
# rather than flipping it.
#
# It is an S2 finding for as long as it stays true. `insecure = true` means a
# machine on the lab network that can answer for the endpoint address gets the
# API token, and nothing about the connection would look wrong. See SEC-006
# (#55) and docs/proxmox-api-token.md for what has to exist before it can be
# turned off.
variable "proxmox_tls_insecure" {
  type        = bool
  description = "Skip TLS verification for the Proxmox API. True until a trusted certificate is issued (SEC-006)."
  default     = true
}

variable "proxmox_ssh_username" {
  type        = string
  description = "SSH username used by the Proxmox provider for node operations (e.g. uploading snippets)."
  default     = "root"
}

# SEC-006-A3. The node SSH identity. Three ways to supply it, and the change is
# that there is now more than one.
#
# Snippet upload happens over SSH, not the API, so no API token narrowing
# reaches it - SEC-006-A4's least-privilege work does nothing for this
# connection. Until now the only option was a password for `root`, which is
# both halves of the S2 finding at once: no privilege separation, and a stored
# credential.
#
# The password stays supported and keeps working. What it no longer is, is the
# only option - and it is no longer required, so a lab that has moved to a key
# does not have to keep a root password in its secrets to satisfy a mapping
# table. Which of the three is in use is decided in locals.tf, where a
# configuration with none of them is refused at plan time rather than failing
# during the snippet upload of an apply that has already cloned a VM.
variable "proxmox_ssh_password" {
  type        = string
  description = "SSH password for node operations. One of password, private key or agent is required (SEC-006-A3)."
  sensitive   = true
  default     = null
}

# The middle option: a key Terraform holds. Better than the password because it
# can be issued to a non-root account and revoked without changing anyone's
# login, and because a key can be restricted at the authorized_keys line.
#
# It is still a credential in Terraform's inputs, which matters more than it
# looks: `terraform show -json` emits a `variables` block carrying every input
# in cleartext (SEC-002), which is why both workflows delete tfplan.json.
# Moving from the password to this changes which secret is in that block, not
# whether one is.
variable "proxmox_ssh_private_key" {
  type        = string
  description = "PEM private key for node SSH. Alternative to proxmox_ssh_password (SEC-006-A3)."
  sensitive   = true
  default     = null
}

# The answer to the question SEC-006-A3 actually asks - "confirm whether the
# provider's SSH agent mode removes the need for a stored credential entirely".
#
# It removes it from *Terraform*, and that is a real reduction rather than a
# relabelling: with this on, neither a password nor a key is a Terraform input,
# so neither appears in the `variables` block of a plan or state JSON, and
# neither is a repository secret that has to be rotated. One of the five
# cleartext values the apply workflow deletes tfplan.json to contain simply
# stops existing.
#
# What it does not do is remove the credential from the runner. The key is on
# that host, in an agent, loaded by something outside this repository -
# docs/runner-trust-boundary.md is where that lives, and SEC-004's boundary is
# what bounds it. So: the honest answer is "yes for Terraform, no for the
# runner", and the runner half was already an accepted path in ADR 0001 section 5.
variable "proxmox_ssh_agent" {
  type        = bool
  description = "Use the runner's SSH agent for node operations, so no credential is a Terraform input (SEC-006-A3)."
  default     = false
}

# Where the agent listens, when it is not $SSH_AUTH_SOCK. Null lets the provider
# read the environment, which is what a normally-configured agent wants.
variable "proxmox_ssh_agent_socket" {
  type        = string
  description = "Override the SSH agent socket path. Null uses $SSH_AUTH_SOCK."
  default     = null
}

# The address per node, rather than one address for "the node".
#
# This is a two-node cluster - `homelab`, `pve` and `pve2` - and the
# configuration was written as if it were one machine. That was harmless while
# the topology could only be changed by editing this file and merging it. Since
# KAN-012 both `proxmox_node_name` and the SSH address are repository variables,
# so setting one without the other is a web form away.
#
# The failure that makes is quiet. Point `proxmox_node_name` at `pve2` and leave
# the address alone, and the provider creates the VM on pve2 through the API
# while uploading its cloud-init snippet over SSH to pve. The snippet lands on
# the wrong node, the VM boots without the configuration it was built for, and
# nothing errors - which is OPS-004's shape again, from a different direction.
#
# A map removes the pairing. `node_name` selects which node VMs go to; this says
# how to reach each of them, and the validation rule in locals.tf refuses a
# node_name the map does not cover. The provider takes as many `node` blocks as
# it is given - verified against the schema, where `node` is a list with no
# max_items - so all of them are declared and it picks by name.
variable "proxmox_ssh_nodes" {
  type        = map(string)
  description = "Cluster node name to the address the provider should SSH to. Every node a VM might be placed on needs an entry (OPS-005)."
  default = {
    pve  = "192.168.10.25"
    pve2 = "192.168.10.26"
  }
}

variable "proxmox_ssh_port" {
  type        = number
  description = "SSH port on the Proxmox node."
  default     = 22
}



# SEC-007. Password SSH used to be forced on for every Linux guest: the template
# set ssh_pwauth and then rewrote sshd_config unconditionally, even though
# main.tf installs an SSH key for the same account. A default of false makes it
# what the issue asks for - a deliberate choice - and locals.tf refuses an apply
# that turns it on without a password to go with it, which is the combination
# that produced a blank-password account reachable over SSH.
variable "linux_password_auth" {
  type        = bool
  description = "Allow password authentication over SSH on Linux guests. Off by default; key authentication is configured either way. Requires linux_vm_password to be set."
  default     = false
}

variable "linux_vm_password" {
  type        = string
  description = "Plaintext password for the ubuntu user (cloud-init chpasswd). Use only on trusted networks."
  sensitive   = true
  default     = null
}

# --- Windows (optional; template-dependent) ---
variable "windows_admin_password" {
  type        = string
  description = "Dedicated Windows Administrator password for Cloudbase-Init user-data."
  default     = null
  sensitive   = true
}


# KAN-015. Whether the first-boot script configures WinRM at all.
#
# Still true, and that is a deliberate answer to the note SEC-008-A5 left in
# windows.yaml.tftpl: "it is worse still for being on by default". What made it
# worse was the transport, not the service. With
# windows_winrm_allow_unencrypted_default below now false, a guest that enables
# WinRM gets Negotiate over port 5985 and nothing readable on the wire - which
# is ordinary remote administration rather than an exposure.
#
# Turning the service off by default was the other candidate, and it is
# rejected on cost: WinRM is how this lab reaches a Windows guest at all, and a
# template change does not reach a running guest
# (docs/guest-config-changes.md), so an operator who needed it back would be
# rebuilding the VM to get it. That is a real loss to buy a property the
# variable below already provides.
variable "windows_enable_winrm_default" {
  type        = bool
  description = "Enable WinRM in Windows user-data by default."
  default     = true
}

# SEC-001c-A4. Whether first boot configures autologon at all.
#
# False, and that is a change of default. SEC-008-A3 kept autologon and cut its
# lifetime to one logon instead, giving two reasons: nothing in provisioning
# needs an interactive desktop, but "no Windows guest exists in local.vms to
# test the removal against", and a first boot that quietly stops producing a
# usable desktop is worse than one registry value with a bounded life.
#
# The first reason still holds and is the argument for this change. The second
# has expired - local.vms carries win-srv-01 now - and the third is answered by
# what the same script already does twenty lines further down: it enables RDP
# and opens the firewall for it. A human's first look at the machine is an RDP
# session with the administrator password, not a console session nobody
# requested. Autologon was buying a second way in, at the price of the
# credential being in the registry to buy it.
#
# What this closes is ADR 0001 path 8, which section 5 accepted *conditionally* -
# "autologon needs some credential in the registry to work at all". True, and
# the way out is not to shorten the credential's life further. It is to not ask
# for autologon.
#
# True restores it, per VM, with SEC-008-A2's one-logon lifetime and the
# second-boot cleanup unchanged. It is a cleartext credential in the registry
# for as long as the machine takes to reboot once.
variable "windows_autologon_default" {
  type        = bool
  description = "Configure one-time autologon during Windows first boot. Off by default; when on, the administrator password is written to the registry in cleartext until the second boot (SEC-001c)."
  default     = false
}

# KAN-015. Whether WinRM is configured to accept Basic authentication over an
# unencrypted transport.
#
# False, and that is the change. windows.yaml.tftpl used to run
#
#   winrm set winrm/config/service/auth '@{Basic="true"}'
#   winrm set winrm/config/service '@{AllowUnencrypted="true"}'
#
# on every Windows guest. Basic sends the username and password
# base64-encoded, which is an encoding and not encryption, and AllowUnencrypted
# removes the transport protection that would otherwise cover it. Together they
# put the administrator credential on the wire in recoverable form, over HTTP
# on port 5985, to anyone able to observe the lab network. SEC-008-A5 measured
# that and deliberately left it; this is where it gets acted on.
#
# With this off the template touches no WinRM auth setting at all, so the
# service keeps what `winrm quickconfig` leaves it: Negotiate on, Basic off,
# AllowUnencrypted off. Negotiate encrypts the payload at the message layer, so
# the credential is not recoverable from the wire even though the transport is
# still HTTP.
#
# It is not free, and the cost falls on the client rather than the guest: a
# workgroup machine reached over Negotiate has to be trusted by the connecting
# host first. Read docs/windows-winrm.md before concluding that this change
# broke WinRM - the symptom is an immediate error naming TrustedHosts, not a
# hang.
#
# True reinstates the old behaviour, per VM rather than repository-wide, for a
# lab that has a reason. It is an S2 exposure for as long as it is on, and the
# guest's own first-boot log records which of the two transports it was given.
variable "windows_winrm_allow_unencrypted_default" {
  type        = bool
  description = "Configure WinRM for Basic authentication over an unencrypted transport. Off by default; when on, the administrator credential is recoverable from the wire (KAN-015)."
  default     = false
}
# --- Azure Arc (optional) ---
variable "arc_enabled_default" {
  type        = bool
  description = "Whether a VM onboards to Azure Arc when its inventory entry says nothing. Per-VM `arc` in locals.tf overrides."
  default     = false
}

variable "arc_tenant_id" {
  type        = string
  description = "Entra tenant the Arc machine is registered in."
  default     = ""
  sensitive   = true
}

variable "arc_subscription_id" {
  type        = string
  description = "Azure subscription the Arc machine is registered in."
  default     = ""
  sensitive   = true
}

variable "arc_resource_group" {
  type        = string
  description = "Resource group the Arc machine resource is created in."
  default     = ""
}

variable "arc_location" {
  type        = string
  description = "Azure region the Arc machine resource is created in."
  default     = ""
}

variable "arc_cloud" {
  type        = string
  description = "Azure cloud name. One of AzureCloud, AzureUSGovernment, AzureChinaCloud - the login host and ARM scope differ per cloud."
  default     = "AzureCloud"
}

variable "arc_install_script_url" {
  type        = string
  description = "Where the guest downloads the Connected Machine agent from."
  default     = "https://aka.ms/azcmagent"
}

# SEC-001a. The service principal is no longer a Terraform input. It stays on
# the runner, where .github/actions/arc-token exchanges it for the short-lived
# token below and .github/actions/arc-cleanup uses it to talk to Azure - both
# read it straight from the job secrets. That is ADR 0001's accepted Path 1.
#
# Nothing declares `arc_sp_id` or `arc_sp_secret` here on purpose: a value that
# Terraform never reads has no business being a Terraform variable, and
# declaring one would mean carrying a credential into state for tflint's sake.
variable "arc_access_token" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Short-lived Entra access token for Arc onboarding, minted per run by .github/actions/arc-token. Empty disables onboarding in the guest."
}
