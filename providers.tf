terraform {
  required_version = ">= 1.6"

  # Persist Terraform state on the self-hosted runner so resources can be
  # updated/destroyed across workflow runs.
  backend "local" {
    path = "/opt/terraform-state/proxmox-ubuntu-vm-factory/terraform.tfstate"
  }

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = ">= 0.77.0"
    }
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure  = true

  ssh {
    username = var.proxmox_ssh_username
    password = var.proxmox_ssh_password

    node {
      name    = var.proxmox_node_name
      address = var.proxmox_ssh_node_address
      port    = var.proxmox_ssh_port
    }
  }
}
