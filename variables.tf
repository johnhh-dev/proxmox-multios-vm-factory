variable "proxmox_endpoint" {
  type    = string
  default = "https://192.168.10.25:8006"
} 

variable "proxmox_api_token" {
  type      = string
  sensitive = true
}

variable "proxmox_node_name" {
  type    = string
  default = "pve"
}

variable "template_vmid" {
  type        = number
  description = "Legacy (linux) template VMID. Prefer template_vmid_linux."
  default     = 9000
}

variable "template_vmid_linux" {
  type        = number
  description = "Proxmox template VMID for Linux."
  default     = 9000
}

variable "template_vmid_windows" {
  type        = number
  description = "Proxmox template VMID for Windows."
  default     = 9170
}
variable "ssh_public_key" {
  type = string
}

variable "bridge" {
  type    = string
  default = "vmbr0"
}

variable "gateway" {
  type    = string
  default = "192.168.10.1"
}

variable "dns_server" {
  type    = string
  default = "192.168.10.2"
}

variable "search_domain" {
  type    = string
  default = "home"
}

variable "snippets_datastore" {
  type    = string
  default = "local"
}
variable "proxmox_ssh_username" {
  type        = string
  description = "SSH username used by the Proxmox provider for node operations (e.g. uploading snippets)."
  default     = "root"
}

variable "proxmox_ssh_password" {
  type        = string
  description = "SSH password used by the Proxmox provider for node operations."
  sensitive   = true
}

variable "proxmox_ssh_node_address" {
  type        = string
  description = "IP/FQDN the provider should use to SSH to the Proxmox node."
  default     = "192.168.10.25"
}

variable "proxmox_ssh_port" {
  type        = number
  description = "SSH port on the Proxmox node."
  default     = 22
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


variable "windows_enable_winrm_default" {
  type        = bool
  description = "Enable WinRM in Windows user-data by default."
  default     = true
}
# --- Azure Arc (optional) ---
variable "arc_enabled_default" {
  type    = bool
  default = false
}

variable "arc_tenant_id" {
  type      = string
  default   = ""
  sensitive = true
}

variable "arc_subscription_id" {
  type      = string
  default   = ""
  sensitive = true
}

variable "arc_resource_group" {
  type    = string
  default = ""
}

variable "arc_location" {
  type    = string
  default = ""
}

variable "arc_cloud" {
  type    = string
  default = "AzureCloud"
}

variable "arc_install_script_url" {
  type    = string
  default = "https://aka.ms/azcmagent"
}

variable "arc_sp_id" {
  type      = string
  default   = ""
  sensitive = true
}

variable "arc_sp_secret" {
  type      = string
  default   = ""
  sensitive = true
}
