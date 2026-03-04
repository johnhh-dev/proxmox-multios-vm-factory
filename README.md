
# 🏗 Proxmox VM Factory Lab

Terraform‑drevet VM‑provisjonering i **Proxmox** med automatisk **Azure Arc onboarding**, styrt via **GitHub Actions**.

Dette repoet implementerer en **GitOps‑drevet VM factory** for et hjemmelabmiljø. Virtuelle maskiner opprettes i Proxmox, konfigureres via cloud‑init / cloudbase‑init og onboardes automatisk til Azure Arc.

---

# 📐 Arkitekturoversikt

GitHub Repo  
↓  
GitHub Actions (terraform plan/apply)  
↓  
Self‑hosted Runner  
↓  
Proxmox API  
↓  
VM clone fra template  
↓  
cloud‑init / cloudbase‑init  
↓  
Azure Arc agent install  
↓  
Azure Arc

---

# 🖥 Infrastrukturplattform

**Hypervisor**
- Proxmox VE

**Node**
- `pve`

**Storage**
- `local` → cloud‑init snippets  
- `local-lvm` → VM disks

**Network**
- `vmbr0`

---

# 🧠 VM Factory Design

VM‑er defineres i Terraform via en inventory‑struktur i `locals.tf`.

Eksempel:

```hcl
vms = {
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
}
```

---

# ⚙️ Funksjonalitet

| Feature | Supported |
|-------|------|
Linux VM | ✅ |
Windows VM | ✅ |
DHCP networking | ✅ |
Static IP | ✅ |
Azure Arc onboarding | ✅ |
Arc disabled | ✅ |

---

# 📦 Terraform struktur

```
.
├── main.tf
├── locals.tf
├── variables.tf
├── providers.tf
├── outputs.tf
├── checks.tf
│
├── cloudinit/
│   ├── linux.yaml.tftpl
│   └── windows.yaml.tftpl
│
└── .github/
    ├── workflows/
    │   ├── terraform-plan.yml
    │   ├── terraform-apply.yml
    │   └── terraform-destroy.yml
    │
    └── scripts/
        ├── extract_arc_names_from_plan.py
        └── extract_arc_names_from_state.py
```

---

# ☁ Azure Arc

VM‑er onboardes til Azure via:

```
azcmagent connect
```

Autentisering skjer via **Service Principal** lagret som GitHub secrets.

Secrets brukt:

```
TF_VAR_arc_sp_id
TF_VAR_arc_sp_secret
TF_VAR_arc_tenant_id
TF_VAR_arc_subscription_id
TF_VAR_arc_resource_group
TF_VAR_arc_location
TF_VAR_arc_cloud
```

---

# 🔐 Service Principal

Service Principal må ha:

```
Contributor
```

på resource group:

```
rg-arc-vm-factory
```

---

# 🔄 Deployment workflow

Ved push til `main`:

```
terraform init
terraform plan
terraform show tfplan
cleanup old Arc resources
terraform apply
```

Resultat:

1. VM opprettes i Proxmox  
2. cloud‑init kjører  
3. Azure Arc agent installeres  
4. VM vises i Azure Portal

---

# 🗑 Destroy workflow

Ved destroy:

```
terraform destroy
```

Workflow gjør:

1. Leser terraform state  
2. Finner Arc‑enabled VM‑er  
3. Sletter Arc resources  
4. Destroyer VM i Proxmox

Resultat:

```
No orphan Azure Arc resources
```

---

# 📊 Status

| Component | Status |
|-----------|--------|
Proxmox API | ✅ |
Terraform | ✅ |
Self‑hosted runner | ✅ |
Persistent state | ✅ |
Static IP support | ✅ |
Azure Arc auto‑connect | ✅ |
Arc cleanup | ✅ |
CI/CD pipeline | ✅ |

---

# 🧠 Designvalg

Terraform state lagres på runner:

```
/opt/terraform-state/proxmox-ubuntu-vm-factory
```

Arc opprettes via cloud‑init ved provisioning.

```
arc = true
```

Hvis Arc settes til false etter deploy må VM reconnectes eller reprovisioneres.

---

# 🚀 Mulige neste steg

- Windows template pipeline  
- MicroK8s cluster provisioning  
- Terraform modules for VM profiles  
- Azure Policy via Arc  
- Automated patching via Azure Update Manager  

---

# 📜 License

MIT
