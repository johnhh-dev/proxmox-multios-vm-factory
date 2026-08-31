terraform {
  required_version = "~> 1.15.0"

  # Persist Terraform state on the self-hosted runner so resources can be
  # updated/destroyed across workflow runs.
  backend "local" {
    path = "/opt/terraform-state/proxmox-ubuntu-vm-factory/terraform.tfstate"
  }

  required_providers {
    proxmox = {
      source = "bpg/proxmox"
      # CHORE-001-A3: `>= 0.77.0` floated to whatever the registry served that
      # day. Combined with the missing lock file, a provider release could
      # change behaviour between a reviewed plan and the apply of that same
      # plan. `~> 0.111.0` accepts 0.111.x patch releases and nothing further;
      # .terraform.lock.hcl fixes the exact version and its checksums on top.
      # Widening the minor is a deliberate edit, reviewed like any other.
      version = "~> 0.111.0"
    }
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token

  # SEC-006-A1, first half. This was the literal `true`, which meant certificate
  # validation was off for every Proxmox API call and turning it on was a code
  # change, a pull request and an apply - so it stayed off.
  #
  # It is a variable now, still defaulting to true. That default is not an
  # endorsement: it is what the lab currently needs, because the Proxmox API
  # presents the self-signed certificate a fresh install generates and the
  # runner does not trust it. Flipping the default without a trusted
  # certificate in place would break every apply, which is why this change
  # deliberately does not do that.
  #
  # What it does change is the cost of fixing it. Once the certificate exists,
  # `TF_VAR_proxmox_tls_insecure=false` is a repository variable, not an edit -
  # and the value is visible in one place rather than being a literal buried in
  # a provider block. docs/proxmox-api-token.md carries the procedure.
  insecure = var.proxmox_tls_insecure

  # SEC-006-A3. Three optional attributes where there used to be one required
  # one. All three are `optional` in the provider schema (verified against
  # bpg/proxmox 0.111.1 with `terraform providers schema -json`; password and
  # private_key are both `sensitive: true` there, agent is a plain bool), so a
  # null is the same as not writing the line - which is what makes "pick one"
  # expressible here at all.
  #
  # Nothing decides between them in this block on purpose. The provider prefers
  # the agent when it is on and falls back to what else it was given, and
  # encoding that preference here would create a second place for it to be
  # wrong. What this configuration does own is refusing to run with *none* of
  # them, and that lives in locals.tf where it can be a plan-time error rather
  # than a failed snippet upload half way through an apply.
  ssh {
    username     = var.proxmox_ssh_username
    password     = local.ssh_password
    private_key  = local.ssh_private_key
    agent        = var.proxmox_ssh_agent
    agent_socket = local.ssh_agent_socket

    # One block per cluster node rather than one for "the node". The provider
    # selects by name at upload time, so declaring both means changing
    # var.proxmox_node_name is sufficient on its own - which it was not before,
    # and the way it failed was silent: the VM created on one node and its
    # snippet uploaded to the other.
    dynamic "node" {
      for_each = var.proxmox_ssh_nodes
      content {
        name    = node.key
        address = node.value
        port    = var.proxmox_ssh_port
      }
    }
  }
}
