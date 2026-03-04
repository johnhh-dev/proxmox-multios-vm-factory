# 🏗 Proxmox Ubuntu VM Factory

### Terraform-driven VM provisioning with Azure Arc integration

![Terraform](https://img.shields.io/badge/IaC-Terraform-623CE4?logo=terraform)
![Proxmox](https://img.shields.io/badge/Hypervisor-Proxmox-orange)
![Azure Arc](https://img.shields.io/badge/Azure-Arc-blue)
![CI/CD](https://img.shields.io/badge/CI/CD-GitHub%20Actions-black)

Infrastructure-as-Code platform for provisioning Ubuntu virtual machines
on **Proxmox VE** using **Terraform**, automatically onboarded to
**Azure Arc** via **GitHub Actions**.

------------------------------------------------------------------------

# 📐 Architecture Overview

``` mermaid
flowchart TD
Dev[Developer Push to GitHub]
GitHub[GitHub Repository]
Actions[GitHub Actions CI/CD]
Terraform[Terraform]
Proxmox[Proxmox VE]
VMs[Ubuntu Virtual Machines]
CloudInit[Cloud-Init]
AzureArc[Azure Arc]

Dev --> GitHub
GitHub --> Actions
Actions --> Terraform
Terraform --> Proxmox
Proxmox --> VMs
VMs --> CloudInit
CloudInit --> AzureArc
```

------------------------------------------------------------------------

# 🚀 Key Features

✔ Automated VM provisioning in **Proxmox**\
✔ Static network configuration via **cloud-init**\
✔ Automatic **Azure Arc onboarding**\
✔ Automatic **Arc cleanup before VM destroy**\
✔ CI/CD managed infrastructure via **GitHub Actions**\
✔ Idempotent Terraform workflows\
✔ No orphan Arc resources

------------------------------------------------------------------------

# 📦 Repository Structure

    .
    ├── main.tf
    ├── providers.tf
    ├── variables.tf
    ├── locals.tf
    ├── outputs.tf
    ├── cloudinit/
    │   └── base.yaml.tftpl
    └── .github/
        ├── workflows/
        │   ├── terraform-plan.yml
        │   ├── terraform-apply.yml
        │   └── terraform-destroy.yml
        └── scripts/
            ├── extract_arc_names_from_plan.py
            └── extract_arc_names_from_state.py

------------------------------------------------------------------------

# ⚙ Infrastructure Platform

  Component             Value
  --------------------- -----------------------------
  Hypervisor            Proxmox VE
  VM Template           Ubuntu Template (VMID 9000)
  Storage               local-lvm
  Cloud-init Snippets   local
  Network Bridge        vmbr0

------------------------------------------------------------------------

# 🔐 Azure Arc Configuration

Azure Arc machines are registered as:

    Microsoft.HybridCompute/machines/<vm-name>

Required GitHub Secrets:

  Secret                       Description
  ---------------------------- ---------------------------------
  TF_VAR_arc_sp_id             Service Principal Client ID
  TF_VAR_arc_sp_secret         Service Principal Secret
  TF_VAR_arc_tenant_id         Azure Tenant ID
  TF_VAR_arc_subscription_id   Azure Subscription ID
  TF_VAR_arc_resource_group    Resource Group for Arc machines
  TF_VAR_arc_location          Azure region
  TF_VAR_arc_cloud             AzureCloud

The Service Principal must have **Contributor** permissions on the Arc
resource group.

------------------------------------------------------------------------

# 🔁 VM Lifecycle

### Deploy

``` bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Cloud-init then:

-   Configures hostname
-   Creates users
-   Installs qemu guest agent
-   Installs Azure Arc agent
-   Connects VM to Azure Arc

------------------------------------------------------------------------

# 🔧 Self‑Hosted Runner

  Setting     Value
  ----------- -------------------------
  Runner      gha-runner-01
  Labels      self-hosted, Linux, X64
  Execution   systemd service

Terraform state location:

    /opt/terraform-state/proxmox-ubuntu-vm-factory

Backend:

    local

------------------------------------------------------------------------

# 🔮 Future Enhancements

Planned improvements:

-   Windows VM template
-   Multi‑OS VM factory
-   Azure Policy integration
-   Azure Monitor integration
-   Custom RBAC roles

------------------------------------------------------------------------

# ⚠ Important

This repository assumes:

-   Self‑hosted GitHub runner
-   Proxmox API access
-   Azure Service Principal authentication

Running Terraform locally without CI/CD may bypass lifecycle safeguards.
