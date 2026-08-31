# Building the two templates

DOC-002-A3. [operator-setup.md](operator-setup.md) §4 has said this since it was
written:

> **Two templates**, at the VMIDs above. An Ubuntu cloud image and a Windows
> image with Cloudbase-Init. **How they were built is not recorded anywhere in
> this repository** — that gap is real and belongs to DOC-002-A3.

This is that record, and [ADR 0003](adr/0003-template-provenance.md) is the
decision it implements: option A, *"a specification derived from the machine,
each row citing what was read and when"*, with option B — Packer — as the thing
that would make the specification checkable rather than merely written.

## What this is not

**It is not a transcript of how `9900` and `9917` were built.** Nobody recorded
that, which is ADR 0003 §1's whole finding. Reconstructing it from the running
templates and presenting the reconstruction as history would be the same mistake
`cloudbase-init.conf.wrong-and-kept` made: a document that looks like evidence.

What follows builds a template that **satisfies §2**. A template built this way
and the one running today are two machines that meet the same specification, and
that is the strongest claim available until B lands.

**It is not a substitute for measuring.** §4 below is the part that matters: a
template is only known to satisfy §2 once a guest cloned from it has been read.

---

## 1 · Before either build

| | |
|---|---|
| Where | On the Proxmox node, as `root`. This is a node operation, not a Terraform one — nothing in this repository builds a template |
| VMIDs | `9900` Linux, `9917` Windows. They are `var.template_vmid_linux` / `_windows`, and changing them is a repository variable, not an edit |
| Storage | The same pool the factory clones onto — `zfs-vmstore` in this lab (`var.vm_datastore_id`) |
| Bridge | `var.bridge`, `vmbr0` |

**Do not build over a template the factory is using.** A clone in flight against
a VMID being rewritten is the failure nothing here can recover from. Build at a
scratch VMID, verify per §4, and only then move it.

---

## 2 · Linux — `9900`

Every requirement below is [ADR 0003 §2](adr/0003-template-provenance.md)'s
Linux table, which was **measured** on VM 101 on 2026-08-30 rather than derived.

### Source image

An Ubuntu **cloud** image — not a desktop or server ISO. The cloud image is the
one that ships cloud-init, and cloud-init is the entire delivery mechanism.

```bash
# 22.04 is what the measurement found. Record the exact filename and its
# checksum wherever this build is logged: "jammy" is a moving target and
# tomorrow's download is not today's image.
wget https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img
sha256sum jammy-server-cloudimg-amd64.img
```

Pinning by filename alone is what [version-pinning.md](version-pinning.md)
argues against everywhere else in this repository. The same reasoning applies
here and there is nothing to enforce it with, which is one more entry in ADR
0003's case for B.

### Build

```bash
qm create 9900 --name ubuntu-template --memory 4096 --cores 2 \
  --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-single --ostype l26

qm importdisk 9900 jammy-server-cloudimg-amd64.img zfs-vmstore
qm set 9900 --scsi0 zfs-vmstore:vm-9900-disk-0,discard=on,ssd=1
qm set 9900 --ide2 zfs-vmstore:cloudinit --boot order=scsi0
qm set 9900 --serial0 socket --vga serial0      # the cloud image expects one
qm set 9900 --agent enabled=1                   # FEAT-002 reads through it
qm disk resize 9900 scsi0 50G                   # var.template_disk_gb_linux
qm template 9900
```

`--agent enabled=1` is not optional in practice. The guest agent is how
`verify_first_boot.py` reads the first-boot marker, how the post-apply smoke
test learns a guest's address, and how §4 below is done at all.

The disk size is a **declared constant** in `var.template_disk_gb_linux`, and it
is what makes FEAT-009's shrink check work. Build a different size and change
the variable in the same commit — a stale value there rejects a legitimate size,
which fails loudly at plan; the opposite silently fails at the hypervisor.

### What must be true afterwards

| Requirement | How to check on a guest cloned from it |
|---|---|
| Ubuntu cloud image | `lsb_release -d` |
| Login account `ubuntu` | `grep -A2 default_user /etc/cloud/cloud.cfg` — the *image's* answer, not what `main.tf` asked for |
| `ssh` resolves to the SSH unit | `systemctl show sshd.service` — expect `Id=ssh.service`. Both names work on Ubuntu; §2 records why the reverse test proves nothing |
| `sshd_config.d` Include | `grep -i include /etc/ssh/sshd_config` |
| Debian-family package names | `qemu-guest-agent`, `curl`, `ca-certificates` resolve |
| Network at first boot | `package_update: true` needs it; so does Arc |
| cloud-init ≥ 25.3 | `grep version /var/lib/cloud/instance/boot-finished` — **the version that wrote the file, not the one installed now.** The guest upgrades itself after first boot, and reading `cloud-init --version` over-constrains the requirement by a release |

---

## 3 · Windows — `9917`

§2's Windows table is still **derived**, not measured, and will stay that way
until a Windows VM is declared — `win-srv-01` is commented out in `locals.tf`,
and OPS-004 (#176) is why. Two of its inputs are in this repository, which is
more than the Linux half has.

### Install

An evaluation or licensed Windows Server 2022 ISO, plus the **VirtIO driver
ISO**, installed to a 100 GB disk (`var.template_disk_gb_windows`).

```bash
qm create 9917 --name win-server-2022-template --memory 8192 --cores 4 \
  --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-single --ostype win11 \
  --machine q35 --bios ovmf
```

`sata0` is what the running template uses (measured: `sata0: ...,size=100G`).
The interface matters to this repository only through `var.disk_interface` on a
per-VM basis — a guest whose `disk_gb` names an interface the template does not
have gets a second disk rather than a resized one.

Load the VirtIO storage driver during setup, then install the VirtIO guest tools
and **QEMU guest agent** before anything else. Same reason as Linux: without it
nothing in §4 works.

### Cloudbase-Init

Install Cloudbase-Init, then replace its configuration with
[`cloudinit/cloudbase-init.conf.actual`](../cloudinit/cloudbase-init.conf.actual)
— which is not an example. It was read out of the running template on
2026-08-29, and the parts that are load-bearing are:

| Setting | Why it is not optional |
|---|---|
| `metadata_services=…ConfigDriveService` | Proxmox presents cloud-init as a ConfigDrive. Any other service reads nothing |
| `UserDataPlugin` | The first-boot script **is** user-data. Without this plugin nothing in `windows.yaml.tftpl` ever runs — which is OPS-004, arrived at from the other direction |
| `CreateUserPlugin` + `inject_user_password=true` | How a password reaches the account. `SetUserPasswordPlugin` is the OpenStack name and is the wrong thing to look for (ADR 0003 §3) |
| `SetHostNamePlugin`, `NetworkConfigPlugin` | The hostname and the static-address path |
| `allow_reboot=true` | First boot renames and reboots |

Copy the same file to `cloudbase-init-unattend.conf`, which is what the
`specialize` pass runs — [`Unattend.xml`](../cloudinit/Unattend.xml) names that
path literally.

### Sysprep

```powershell
C:\Windows\System32\Sysprep\sysprep.exe /generalize /oobe /shutdown `
  /unattend:C:\Windows\System32\Sysprep\Unattend.xml
```

Using [`cloudinit/Unattend.xml`](../cloudinit/Unattend.xml) from this
repository. It does three things and each is a §2 row:

- `PersistAllDeviceInstalls` on generalize, so the VirtIO drivers survive
- OOBE skipped, so a clone boots to a usable state with no console interaction
- `RunSynchronousCommand` in `specialize`, which is **what starts Cloudbase-Init
  at all** on a cloned guest

Then `qm template 9917`.

### What must be true afterwards

Everything in the table above, plus PowerShell **5.1** as the `#ps1_sysnative`
host — BUG-017's `NativeCommandError` behaviour is 5.1-specific, and the
first-boot script is written against it. `$PSVersionTable.PSVersion` on a guest.

---

## 4 · Verifying a template you just built

This is the step that turns a build into a measurement, and it is the same
method that produced §2 — clone one guest and read it back through the guest
agent.

```bash
qm clone 9900 999 --name template-check --full
qm start 999
# wait for the agent
qm guest cmd 999 get-osinfo
qm guest exec 999 -- /bin/bash -lc 'lsb_release -d; systemctl show sshd.service | head -3'
```

For Windows, the same shape through `powershell.exe -NoProfile -Command`, which
is how `cloudbase-init.conf.actual` was captured — the command is in that file's
header.

**A guest is not its template**, and ADR 0003 §2 records two rows where the
difference mattered: three packages being installed does not show the image
pre-baked them, and the running cloud-init version is not the one that did the
work. Read the table above rather than inventing a check.

Then `qm destroy 999 --purge`.

## 5 · Recording it

A build that is not recorded leaves the repository exactly where ADR 0003 §1
found it. Three places, and none is optional:

1. **[ADR 0003 §2](adr/0003-template-provenance.md)** — the measured column, with
   the date and what was read. That table is the specification; a build that
   changed something and did not update it has made the specification wrong.
2. **`var.template_disk_gb_linux` / `_windows`** — if the size changed.
3. **[verified-on-the-guests.md](verified-on-the-guests.md)** — if the build was
   verified per §4, because that page is the standing answer to "what has
   actually been watched working".

## 6 · What this does not fix

**A hand-built template is still hand-built.** Nothing checks that the machine
matches §2, and the failure mode is silent drift — a template rebuilt slightly
differently, and a guest that behaves differently for reasons nothing records.
That is option A's cost row in ADR 0003 §4, priced honestly, and it is unchanged
by writing this document.

What changes is the ordering. §2 is a specification a Packer build can be
diffed against, this is the manual procedure that satisfies it, and B stops
being a rewrite and becomes an implementation of something already written down.

The other half stays open too: **the source images are not pinned**, by anything
stronger than a sentence in §2 asking whoever builds one to record the
checksum.
