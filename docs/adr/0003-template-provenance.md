# ADR 0003 · How the two templates get built

- **Status:** Accepted — **C**, in the order A then B, with A's specification
  measured rather than written
- **Date:** 2026-08-29 · concluded 2026-08-30
- **Spike:** SPIKE-002 (#71) · **Related:** DOC-002-A3, SEC-001c (#118)
- **Deciders:** `@iac`

---

## 1 · The finding, restated

Templates `9900` (Linux) and `9917` (Windows) are referenced by
[`variables.tf`](../../variables.tf) and **were built by hand and are documented
nowhere.** They are an undeclared dependency of the entire factory: if either is
lost or drifts, nothing in this repository can rebuild it.

Everything the repository pins — provider version, action SHAs, Terraform
version ([version-pinning.md](../version-pinning.md)) — sits on top of two
artefacts with no version, no provenance and no rebuild path. `terraform apply`
is reproducible; what it clones is not.

## 2 · What the repository *requires* a template to contain (SPIKE-002-A3)

This is the half that can be established without touching the node, and it had
not been written down before. It was derived from what the first-boot documents
assume, not from inspecting an image.

**The Linux half is no longer derived.** Every row below was measured on
`ubuntu-dhcp-01` (VM 101) on 2026-08-30, through the guest agent. The Windows
table after it is still derived, and will stay that way until a Windows VM is
declared — `win-srv-01` is commented out in `locals.tf`.

### Linux — `template_vmid_linux`

| Requirement | Where the requirement comes from | Measured on VM 101 |
|---|---|---|
| An Ubuntu cloud image | [`linux.yaml.tftpl`](../../cloudinit/linux.yaml.tftpl) header, CHORE-004-A4 | `ubuntu 22.04` |
| Login account named `ubuntu` | hardcoded in the template **and** in `main.tf`'s `user_account.username` | `/etc/cloud/cloud.cfg` sets `default_user: name: ubuntu` — the image's own default, not something cloud-init was told |
| SSH unit named `ssh`, not `sshd` | `systemctl restart ssh` in `runcmd` | `ssh.service`, enabled — **and `sshd.service` is an alias of it.** See below |
| sshd built with the `sshd_config.d` Include | `ssh_pwauth` is applied through `50-cloud-init.conf` | one `Include` line; `50-cloud-init.conf` present and holding `PasswordAuthentication no`. openssh 8.9p1 |
| Debian-family package names | `packages:` lists `qemu-guest-agent`, `curl`, `ca-certificates` | all three resolve and are installed |
| A package manager with network at first boot | `package_update: true` | the same three arrived, so it had both |
| cloud-init new enough to apply `cicustom: vendor=` | SPIKE-003 (#124) — the whole delivery mechanism | **25.3**, not 26.1. See below |
| Network reachability before Arc onboarding | the onboarding script waits 60 × 5s for it | Arc reports `Connected`, recorded in [what the guests confirm](../verified-on-the-guests.md) |

Two rows say something the requirement did not.

**`sshd.service` is an alias, so both names work.** `systemctl show sshd.service`
reports `Id=ssh.service`, `Names=ssh.service sshd.service` — one unit under two
names. The requirement is not that the unit be *named* `ssh`; it is that `ssh`
resolve to it, and Ubuntu satisfies that while also accepting `sshd`. The
template's own comment is about RHEL-family images, where the unit really is
only `sshd` and `systemctl restart ssh` really does fail, and that stands. What
does not follow is the reverse test: **`systemctl restart ssh` succeeding does
not tell you the image is Debian-family**, because it succeeds under both names
here. Anyone using it to identify an image would read it wrong.

**The cloud-init that applied the vendor-data was 25.3.** `cloud-init --version`
on the running guest says `26.1-0ubuntu1~22.04.1`, and taking that as the answer
would have been wrong: `/var/lib/cloud/instance/boot-finished` records the
version that wrote it, and that is `25.3-0ubuntu1~22.04.1`. The guest was
upgraded after first boot — which is what `package_update: true` is for. So the
evidenced lower bound for `cicustom: vendor=` is 25.3, and the template ships
that. Reading 26.1 off the running guest would have over-constrained the
requirement by a release.

### Windows — `template_vmid_windows`

| Requirement | Where the requirement comes from |
|---|---|
| Cloudbase-Init installed and running as a service | it is what executes `windows.yaml.tftpl` at all |
| ConfigDrive metadata service enabled | `cloudbase-init.conf.actual`, read from the guest |
| `UserDataPlugin` enabled | the whole first-boot script is user-data |
| `NetworkConfigPlugin`, `SetHostNamePlugin` | same file |
| Sysprepped with the `specialize` hook | [`Unattend.xml`](../../cloudinit/Unattend.xml) runs Cloudbase-Init there |
| PowerShell 5.1 as the `#ps1_sysnative` host | BUG-017's `NativeCommandError` behaviour is 5.1-specific |
| `PersistAllDeviceInstalls` on generalize | `Unattend.xml` |

**Both `Unattend.xml` and the Cloudbase-Init configuration are inputs to
building the template, not to provisioning a guest.** Nothing in `main.tf` or
either workflow reads them. They are in `cloudinit/` beside the two `.tftpl`
files that *are* read at provision time, which is misleading, and the second is
named `.example` — so the repository does not claim to know what the real
template's configuration says.

That is SPIKE-002-A3's answer: **everything in those two files is baked in at
build time; nothing in them is applied at provision time.** The `.tftpl` files
are the provision-time half, and they are the only half this repository
controls.

## 3 · A finding that changed another issue, and then changed back

**This section previously drew a conclusion from the wrong file, and said so at
the time.** It is kept rather than rewritten, because the caveat it carried is
what made the correction cheap.

It reasoned from `cloudinit/cloudbase-init-proxmox.conf.example` — renamed to
`cloudbase-init.conf.wrong-and-kept` and kept only as evidence — which enables
three plugins and not `SetUserPasswordPlugin`, that a password Proxmox writes
via `cipassword` would be applied by nothing — and named that as the thing
SEC-001c (#118) was waiting on. It also said, in bold: *"the file is an
`.example`. The real template's `cloudbase-init.conf` has not been read, and
could already differ."*

It differs.

### What is actually installed (SPIKE-002-A1)

Read on 2026-08-29 from VM 101, cloned from template `9917`, through the guest
agent — recorded verbatim in
[`cloudinit/cloudbase-init.conf.actual`](../../cloudinit/cloudbase-init.conf.actual):

| | example | actual |
|---|---|---|
| plugins | 3 | **8** |
| `inject_user_password` | absent | **`true`** |
| `username` / `groups` | absent | `Administrator` / `Administrators` |
| `allow_reboot` | `false` | **`true`** |
| MTU, licensing, SSH keys, local scripts | — | present |

**`SetUserPasswordPlugin` was the wrong thing to look for.** That is the
OpenStack plugin name. Cloudbase-Init injects a password through
`CreateUserPlugin` together with `inject_user_password=true`, and both are
enabled here. The inference in the previous version of this section is
withdrawn.

### Where the gap actually is

Withdrawn is not the same as reversed, and the mechanism is now mapped rather
than guessed at. On the Proxmox side, from
`/usr/share/perl5/PVE/QemuServer/Cloudinit.pm` on the node:

- `cloudinit_userdata()` writes `password: $password` into the **user-data**
  document when `cipassword` is set, and `generate_configdrive2()` uses it.
- `configdrive2_gen_metadata()` writes no `admin_pass`.

On the guest side, `CreateUserPlugin` reads the password from
`service.get_admin_password()`, which for the ConfigDrive service is
`admin_pass` in **meta-data**.

So Proxmox puts the password in one document and the plugin that would inject it
reads another. What remains open is narrow and testable: whether
`UserDataPlugin`'s cloud-config handling acts on a top-level `password:` key.

**That is one apply away from an answer**, where before it was an assertion in
[ADR 0001 §3D](0001-guest-secret-delivery.md) with nothing behind it. SEC-001c's
blocker is relocated, not removed.

### What this says about the record rather than the template

Two ADRs reasoned from a file in this repository about a machine outside it, and
one of them was wrong. Both flagged the uncertainty; only one of them was
wrong *because* of it. The lesson worth keeping is the one §1 already makes —
the templates are an undeclared dependency, and a repository cannot document a
machine it has never read.

## 4 · Options (SPIKE-002-A2, A4)

### A · Documented manual build

Write the build down as a runbook; keep building by hand.

| | |
|---|---|
| Cost | Low — one document, no new tooling |
| Reproducibility | **Depends on a human following it exactly.** The failure mode is silent drift: a template rebuilt slightly differently, and a guest that behaves differently for reasons nothing records |
| Rebuild after loss | Possible, slowly |
| Verifiability | None. Nothing checks the template matches the document |

### B · Packer

Build both templates from a committed HCL definition, run on demand.

| | |
|---|---|
| Cost | **The honest number is not "a Packer file".** It is: a build host or runner with QEMU access, Proxmox API credentials scoped to template creation, an ISO or cloud-image source that is itself pinned, a Windows answer file for the unattended install, and a place to keep build artefacts. Plus a second CI path that can write to the hypervisor |
| Reproducibility | The point. The template becomes a reviewable artefact, and §2's table stops being derived and becomes asserted |
| Rebuild after loss | `packer build` |
| Verifiability | A rebuild can be diffed against what is running |
| Fit with this repo | `packer-plugin-proxmox` targets Proxmox directly. Version-pinning (BUG-016) already has the machinery for pinning the plugin and the source image |

### C · Both, in that order

Document first, automate second, and treat the document as the specification the
Packer build must satisfy.

| | |
|---|---|
| Cost | A's cost now, B's cost later |
| Reproducibility | A's until B lands |
| The thing it buys | **A written specification before an implementation of it.** §2 above is most of one already, and it was derivable in an afternoon from files already in the repository — which is the argument that A is cheap |

## 5 · Decision (SPIKE-002-A4)

**C — document first, automate second — and the specification is the measured
one in §2, not a written description of it.**

### The reason that expired, and the one that replaced it

This section previously recommended C *"and start with A immediately, because
the Windows half is blocking a P0"*, and stopped there, because A1 needed node
access.

**That reason is gone.** A1 has been done, twice: the Windows half on 2026-08-29
from VM 101 (§3), the Linux half on 2026-08-30 through the guest agent (§2). And
it did not close SEC-001c — §3 relocated that blocker to a narrower question
about `UserDataPlugin` rather than removing it. So the argument that "A unblocks
a P0 this month" was true of the activity and not of the outcome, and it is not
the argument to keep.

The one that survives is the one §3 produced by accident:

**The `.example` was option A already, and it is what went wrong.** A file in
this repository described the template's Cloudbase-Init configuration. Nobody
had read the template. Two ADRs reasoned from it, one of them wrongly, and the
error survived review because the document looked like evidence. Manual-build
documentation *with nothing measured behind it* is not a cheap version of
build-as-code — it is a second source of truth that can drift silently, which is
the failure mode A's own cost row predicted and did not price.

So A is accepted in one form only: **a specification derived from the machine,
each row citing what was read and when.** That is what §2 now is, and it is
worth stating that it took two afternoons rather than a project — twelve rows
across two guests, using the guest agent this factory already configures.

### What that buys, that a written document did not

A measured specification is checkable. `sshd.service` being an alias and
cloud-init being 25.3 rather than 26.1 are both facts that only exist because
someone asked the guest; both would have been recorded wrong by a careful person
writing the same table from the templates. **Those two rows are the argument for
B on the day B becomes affordable** — a Packer build can be diffed against a
specification that was measured, and cannot usefully be diffed against one that
was inferred.

### Still C, and B is still not now

B's cost column has not changed and is still an estimate rather than a
measurement: a build host with QEMU access, credentials scoped to template
creation, a pinned source image, a Windows answer file, somewhere to keep
artefacts, and a second CI path that can write to the hypervisor. §6's second
bullet is the reason that is not a small ask here — the lab has one runner, and
it is a guest of the node (OPS-003, #171).

**The immediate step is no longer a measurement. It is a rebuild path**, and the
first half of one is now writable: §2 says what the Linux template must contain,
in measured terms, and DOC-002-A3 is where it belongs as a runbook rather than
as an ADR section. The Windows half stays derived until a Windows VM is declared
— `win-srv-01` is commented out in `locals.tf`, and OPS-004 (#176) is why.

### What this closes

SPIKE-002's four activities: A1 in §3 and §2, A2 in §4, A3 in §2, A4 here. A
spike produces a decision rather than a shipped change, per the card-naming
table in EPIC-000 (#32), and this is it. **What it does not close is
DOC-002-A3**, which must document the templates either way, and which now has a
measured §2 to write from instead of an inference.

## 6 · What is explicitly not decided

- **Whether Packer is worth it.** B's cost column is an estimate, not a
  measurement, and the time-box in #71 is three days — enough to build one
  template and find out, not enough to conclude from reading.
- **Where a Packer build would run.** The lab has one self-hosted runner,
  already inside SEC-004's trust boundary, and giving it template-creation
  rights on the hypervisor widens what a compromised workflow can do. That is a
  SEC-006-A4 conversation, not a Packer one.
- **Whether the templates should be pinned by content.** A hash of a template
  disk would make drift detectable, and nothing here proposes a mechanism.

## 7 · What would change this decision

- **A template is lost.** Then B's cost is paid whether or not it was chosen,
  and it is paid under pressure.
- **A second Linux distribution is wanted.** CHORE-004-A4 lists eight
  assumptions that are Ubuntu-specific; a second image makes them selectable
  per template, and hand-building two divergent images is where A stops being
  viable.
- ~~**SPIKE-002-A1 finds the real Cloudbase-Init configuration already differs
  from the `.example`.**~~ **This fired.** It differs — eight plugins against
  three, and `inject_user_password=true` (§3). Kept struck through rather than
  deleted, because a trigger that fires and is quietly removed leaves a record
  that looks as though nothing happened.

  What it changed is §5's form of A: the specification is measured, and the
  `.example` is retired to `cloudbase-init.conf.wrong-and-kept` as evidence.
  What it did **not** change is the order. "The case for build-as-code stops
  being about convenience" was written expecting the finding to make B urgent;
  the finding was in a document rather than in a build, so it argues for
  measuring the specification, which is cheap, rather than for automating the
  build, which is not. That distinction is worth keeping visible: the trigger
  was right about the severity and wrong about the remedy.

Three more, for the decision as it now stands:

- **A Windows VM is declared again.** §2's Windows half is still derived, and
  the guest agent makes measuring it the same afternoon's work the Linux half
  took. OPS-004-A3's rebuild (#176) is the occasion.
- **The measured specification and the template disagree.** Nothing re-runs §2,
  so a template rebuilt by hand can drift out from under it. Today the defence
  is that the table cites dates; §6's third bullet is the mechanism that would
  do better.
- **A second runner exists, or the build moves off the lab runner.** That is
  most of B's cost and the whole of §6's second bullet.

---

## References

- [`cloudinit/Unattend.xml`](../../cloudinit/Unattend.xml) — the sysprep hook that starts Cloudbase-Init
- [`cloudinit/cloudbase-init.conf.wrong-and-kept`](../../cloudinit/cloudbase-init.conf.wrong-and-kept) — the file §3 reasoned from, kept and marked
- [`cloudinit/cloudbase-init.conf.actual`](../../cloudinit/cloudbase-init.conf.actual) — what is installed
- [ADR 0001 §3D and §9](0001-guest-secret-delivery.md) — the `cipassword` question this spike bears on
- [version-pinning.md](../version-pinning.md) — what is pinned today, and what is not
- [`packer-plugin-proxmox`](https://github.com/hashicorp/packer-plugin-proxmox)
