# What the guests confirm

This repository is careful about what it has *not* watched work. Almost every
record ends with a "not verified" section, and until 2026-08-30 that was the
honest state of nearly everything.

Some of it has now been watched. This is that list — kept separate from the
records themselves so a claim and its evidence do not drift, and because a
repository with thirty "unverified" notes and no counterpart reads as if nothing
works.

Read through the Proxmox guest agent, and from the apply that built the current
guest.

The lab changed on 2026-08-30: `ubuntu-static-01` and `win-srv-01` were
destroyed and `ubuntu-dhcp-01` was built. That rebuild is the most useful thing
that has happened to this list, because a guest created *after* a change is the
only way to watch that change work.

## Confirmed working

| Change | What the guest shows |
|---|---|
| **SEC-007** — password SSH is opt-in | `sshd -T` reports `passwordauthentication no` on VM 100. The effective value, not the file |
| **BUG-018** — one resolver list, reaching the guest | `resolvectl dns` reports `192.168.10.2 192.168.10.1` on eth0 — `var.dns_server` then the fallback, in order |
| **FEAT-002** — the guest agent | `qemu-guest-agent` is `active`, and the agent answered every query used to build this page |
| **SEC-001a** — Arc via a short-lived token | VM 100 connected 2026-08-28 and `azcmagent show` reports `Agent Status: Connected` |
| **SPIKE-003** — vendor-data reaches a Linux guest | `/var/lib/cloud/instance/vendor-data.txt` is 8569 bytes; cloud-init consumed it and `boot-finished` exists |
| **BUG-024** — the cluster preflight | run against the real `/cluster/status`: `Cluster 'homelab' is quorate.` And against the same payload with `pve2` down, it refuses with the offline node named and `pvecm expected 1` as the fix |
| **DOC-001's comparison tool** | run against the real `qm list` for the first time. It works — and reported the runner as an `orphan`, which is what #193 fixed |
| **SEC-001d-A1** — the onboarding script removes itself | `/usr/local/sbin/arc-onboard.sh` is **gone** on `ubuntu-dhcp-01`. It is still present on the guest that predated the change, which is what ADR 0001 §8a records |
| **KAN-017-A5** — post-apply smoke tests | ran on the apply of 2026-08-30: `inventory: 1 VM(s), all present in state` · `guest: 1 VM(s) reporting an address` · `arc: 1 of 1 machine(s) present in Azure` |
| **OPS-004's check** — first boot actually ran | `Checked first-boot completion on 1 guest(s). Every guest reports its first-boot configuration completed.` The `agent/file-read` call works |
| **The convergence check** — BUG-012 has not regressed | `No changes after apply - the configuration converges.` |
| **A clean destroy** | after removing two VMs: one snippet on the node for the one remaining guest, `100.conf` gone, no orphaned disks |
| **ADR 0003 §2's Linux requirements** | all eight measured on VM 101. The section said outright that it was *derived, not from inspecting an image*; the Linux half no longer is |
| **Arc cleanup, end to end** | after the two VMs were destroyed, `az resource list` over the **whole** resource group returns exactly one machine. Nothing is left behind |

The Arc one is worth pausing on. The README said Arc onboarding had *"never been
observed to complete on a guest"* for two days after it had completed, because
nothing looked and nothing reported.

**The last four rows all became true on one apply**, and three of them are
checks this repository added in the days before it. They were written with "no
run has done this" notes attached; the notes can come off.

`SEC-001d-A1` is the sharpest of them. #149 changed the template, so it could
only ever be proved by a guest built afterwards — and until 2026-08-30 there
was not one.

## Confirmed broken

| Change | What the guest shows |
|---|---|
| **The Windows first-boot script** | No log, no run-once marker on VM 101. It has never run — OPS-004 (#176) |

Five merged pull requests depend on that script executing. None of them has.

## Confirmed, and not what the record said

| Record | Measured |
|---|---|
| ADR 0001 path 7, *"Closed by SEC-001d-A1"* | closed for guests built after it; the script is still on VM 100, which predates it |
| ADR 0001 path 6, *"caches raw user-data"* | the cache is `vendor-data.txt`, `0600` root — and `/dev/sr0` holds the same bytes, group `cdrom`, which `ubuntu` is in |
| SEC-006-A1, *"pinning the leaf does not work"* | it does. `openssl verify` and TLS verification answer different questions; the CA is still preferable, for renewal |
| SEC-001d, *"snippets from every apply are still on the node"* | two files, both current, one per managed VM |
| ADR 0003 §2, *"SSH unit named `ssh`, not `sshd`"* | true, and weaker than it reads. `sshd.service` is an **alias** of `ssh.service` on Ubuntu — one unit, two names — so `systemctl restart sshd` works here too |
| ADR 0003 §2, *"cloud-init new enough for `cicustom: vendor=`"* | evidenced at **25.3**, not the 26.1 the running guest reports. `boot-finished` records the version that wrote it; the guest was upgraded afterwards |

## What is still unwatched

- **Every Windows-side change**, behind OPS-004 — and #178's fix for it is
  itself unexercised, because no Windows VM is declared. `win-srv-01` is
  commented out in `locals.tf`; uncommenting it is what tests both.
- **SEC-006-A5** — nothing has connected with certificate validation on, under a
  restricted role, or with an SSH key.
- **KAN-017-A6** — no concurrency, failed-apply or restore drill against the
  lab's own state.
- **FEAT-001-A2** — state is still unencrypted, and SEC-001e has not run.
- **Everything in the README's Kubernetes and services sections.** MicroK8s,
  ArgoCD, MetalLB, the Arc-Kubernetes connection, and what `ubuntu-utils-01`
  actually hosts. Nothing in this repository references any of it, so none of it
  can appear on this page until someone reads those guests — which is the same
  guest-agent afternoon that produced the rows above.

## How this was read

Through `qm guest exec` and `qm guest cmd` from the node, which needs the guest
agent — enabled by FEAT-002, so this method exists because of one of the changes
it verifies.

A guest is not its template, and the difference caught two of these. The three
packages being installed does not show the image pre-baked them — cloud-init
installed them at first boot, which is what that row is actually about. The
`ubuntu` account being present does not show the image ships it either, since
`main.tf` asks for that name anyway; `/etc/cloud/cloud.cfg`'s `default_user` is
the image's own answer and that is what was read. And the running cloud-init
version is not the one that did the work, because the guest upgraded itself
after first boot.

Nothing here required a credential from the guest, and nothing was changed on
one. Anything that would have needed a credential to check —
`audit_state_secrets.py`, `audit_node_snippets.py` — was deliberately left for
an operator, because running it over SSH would put the credential on a command
line.

That still holds for the node one. It stopped holding for the state one: the
apply workflow runs `audit_state_secrets.py` on the runner, where the
credentials are already in the job environment and nothing is typed. So what
state holds is now recorded per apply rather than per SSH session — and what a
person is still needed for is a **rotated-out** value, which appears in no
environment variable and only they know.
