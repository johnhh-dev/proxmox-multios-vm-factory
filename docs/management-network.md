# The management flows, and who owns each one

KAN-011-A1 ([#25](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/25))
asks for *"a source, destination, protocol, port, purpose, owner, and logging
matrix for every management flow"*. This is that matrix, and A2's trust
boundaries below it.

**Derived from this repository, not from the network.** Every row cites the file
that declares it, so a row can be checked by reading rather than believed. That
is also the limit: nothing here was observed on the wire, no firewall was read,
and a flow that exists but is declared nowhere in this repository is not in the
table. Section 5 is the list of those.

KAN-011 was one of two issues the audit never covered, and
[backlog-reconciliation.md](backlog-reconciliation.md) says why: *"the audit
scoped itself to the repository — Terraform, workflows, scripts, templates.
WireGuard is configured on a guest, not from here."* That remains true. What was
not true is that **nothing about the model could be written down from here** —
most of the matrix falls out of files already in the tree.

---

## 1 · The matrix

Addresses are the defaults in [`variables.tf`](../variables.tf); a lab that sets
the repository variables from KAN-012 has its own. Owner is a role, since this
lab has one operator wearing all of them, and naming the role is what makes the
row reviewable when it does not.

### Into the lab — the flows a person uses

| # | Source | Destination | Proto/Port | Purpose | Owner | Logged | Declared in |
|---|---|---|---|---|---|---|---|
| 1 | operator device | `wg-vpn-01` (1104) | **unknown** | The management path into the lab | `@ops` | unknown | nothing here — §5 |
| 2 | operator device | Proxmox web UI, `192.168.10.25:8006` | TCP 8006 / HTTPS | Console access, and the recovery path when everything else is down | `@ops` | unknown | [runner-trust-boundary.md](runner-trust-boundary.md) names it as the emergency path |
| 3 | approved sources (`var.management_source_cidrs`), else **any** | Windows guest | TCP 3389 / RDP | First look at a new guest, and the only interactive path since autologon went off by default | `@win` | no | [`windows.yaml.tftpl`](../cloudinit/windows.yaml.tftpl) — `fDenyTSConnections=0`, then `Set-ManagementRuleScope` |
| 4 | approved sources (`var.management_source_cidrs`), else **any** | Windows guest | TCP 5985 / WinRM | Remote administration | `@win` | no | same file, `%{ if windows_enable_winrm }`; transport per [windows-winrm.md](windows-winrm.md) |
| 5 | operator device | Linux guest | TCP 22 / SSH | Remote administration | `@ops` | no | key installed by [`main.tf`](../main.tf) `user_account`; password auth off unless `linux_password_auth` |

### Inside the lab — the flows the factory itself makes

| # | Source | Destination | Proto/Port | Purpose | Owner | Logged | Declared in |
|---|---|---|---|---|---|---|---|
| 6 | `gha-runner-01` (1110) | Proxmox API, `192.168.10.25:8006` | TCP 8006 / HTTPS | Every clone, configure and destroy | `@iac` | Proxmox task log | [`providers.tf`](../providers.tf) `endpoint`, `var.proxmox_endpoint` |
| 7 | `gha-runner-01` | `pve` `192.168.10.25:22`, `pve2` `192.168.10.26:22` | TCP 22 / SSH | Snippet upload — the provider writes user-data over SSH, not over the API | `@iac` | node `auth.log` | `providers.tf` `ssh { }`, `var.proxmox_ssh_nodes`, `var.proxmox_ssh_port` |
| 8 | `gha-runner-01` | Proxmox API | TCP 8006 / HTTPS | Preflight quorum, and reading first-boot markers through the guest agent | `@iac` | Proxmox task log | [`preflight_cluster.py`](../.github/scripts/preflight_cluster.py), [`verify_first_boot.py`](../.github/scripts/verify_first_boot.py) |
| 9 | every guest | `192.168.10.2:53`, then `192.168.10.1:53` | UDP/TCP 53 | Name resolution. **First boot blocks on it** — every guest waits to resolve `var.network_probe_host` before it does anything | `@ops` | unknown | `var.dns_server`, `var.dns_servers_fallback` |
| 10 | `pve` ↔ `pve2` | each other | corosync, and SSH for replication | Cluster membership and the `*/15` replication jobs | `@ops` | node logs | [proxmox-cluster-quorum.md](proxmox-cluster-quorum.md) |

### Out of the lab — what breaks if egress is filtered

| # | Source | Destination | Proto/Port | Purpose | Owner | Logged | Declared in |
|---|---|---|---|---|---|---|---|
| 11 | `gha-runner-01` | `github.com` | TCP 443 | The runner polls for jobs. Outbound only — GitHub never connects in | `@ci` | GitHub Actions run log | [runner-trust-boundary.md](runner-trust-boundary.md) |
| 12 | `gha-runner-01` | `login.microsoftonline.com` | TCP 443 | Minting the short-lived Arc onboarding token | `@ci` | Entra sign-in log | [`mint_arc_token.py`](../.github/scripts/mint_arc_token.py), `var.arc_cloud` |
| 13 | `gha-runner-01` | `management.azure.com` | TCP 443 | Arc cleanup on destroy, and the post-apply Arc check | `@ci` | Azure activity log | [`arc-cleanup`](../.github/actions/arc-cleanup/action.yml), [`arc_missing.py`](../.github/scripts/arc_missing.py) |
| 14 | every guest | `aka.ms` | TCP 443 | The network probe, **and** the Connected Machine agent download | `@ops` | no | `var.network_probe_host`, `var.arc_install_script_url` |
| 15 | every guest | Azure Arc endpoints | TCP 443 | `azcmagent connect`, then the agent's steady-state traffic | `@ops` | Azure activity log | both guest templates |
| 16 | Linux guests | Ubuntu archives | TCP 80/443 | `package_update: true` and three packages at first boot | `@ops` | no | [`linux.yaml.tftpl`](../cloudinit/linux.yaml.tftpl) |

---

## 2 · What the matrix says before anyone touches a firewall

Four things fall out of the table itself. None needed the lab.

**Every inbound management flow is either unauthenticated at the transport
layer or unencrypted, and each is separately recorded as accepted.** Row 6
presents the API token over a connection nothing authenticates —
`var.proxmox_tls_insecure` still defaults to `true`, which
[`providers.tf`](../providers.tf) is explicit is *"what the lab currently needs"*
rather than an endorsement. Row 7 authenticates to the node as `root` with a
password. Row 4's payload is encrypted by Negotiate but its transport is HTTP,
and `windows_winrm_allow_unencrypted` turns even that off. Row 3 has whatever
RDP negotiates against a self-signed certificate. **KAN-011's dependency on
SEC-006 (#55) is therefore not a sequencing convenience — rows 6 and 7 stay on
this list until SEC-006-A5 is exercised.**

**The two flows this repository opens, it opened to everything.** Rows 3 and 4
came from `Enable-NetFirewallRule -DisplayGroup "Remote Desktop"` and the same
call for `"Windows Remote Management"`. Neither narrowed `-RemoteAddress`, so
each rule was enabled with the scope the built-in carries — any remote address,
on a default Windows install.

**KAN-011-A3 is now expressible, and it is still empty.** `var.management_source_cidrs`
is the list of sources the first-boot script narrows both rule groups to. It
defaults to `[]`, which is exactly today's behaviour, and the honest reason is
§3: **the VPN address range is unknown here**, so a default of
`192.168.10.0/24` would be a guess about where an administrator connects from,
and a wrong guess locks the operator out of the guest. That is the same shape as
rotating a WireGuard key with no second way in.

What the empty default does buy: the guest's first-boot log now states which of
the two it was given rather than leaving it to be inferred from the absence of
a line —

```
Remote Desktop: reachable from ANY source - var.management_source_cidrs is
empty, so the built-in rule keeps its default scope (KAN-011).
```

**One combination is refused outright at plan time.** `windows.winrm_allow_unencrypted`
puts the administrator credential on the wire in recoverable form (SEC-008-A5,
[windows-winrm.md](windows-winrm.md)); an unrestricted rule decides *who can be
on that wire*. Each was reviewed alone and accepted; their combination never
was. A Windows guest asking for the old transport with `management_source_cidrs`
empty now fails the plan.

**Row 9 is why `dns-01` is on `var.protected_vm_ids`.** Not because it is
important, but because *first boot blocks on it* — the argument
[unmanaged-vms.md](unmanaged-vms.md) makes. The matrix adds the shape of the
failure: DNS is one host with one fallback, and the fallback is the gateway, so
a lab that loses `192.168.10.2` provisions guests only as fast as the gateway
answers.

**The Logged column was the finding.** KAN-011's acceptance criterion is that
*"relevant connection events are retained in the documented location and can be
reviewed by the owner"*. Of sixteen rows, the ones with an answer were the ones
where a third party keeps the log — Proxmox's task log, GitHub's run log,
Azure's activity log. **Not one inbound flow to a guest was logged anywhere this
repository knows about**, and no owner had ever reviewed one.

**Rows 3 and 4 now have a location (KAN-011-A6).** The Windows first-boot script
turns on the Windows Firewall log for every profile, allowed *and* dropped,
before it enables either management rule group:

```
C:\Windows\System32\LogFiles\Firewall\pfirewall.log
```

Dropped connections are the half that matters for A6's negative tests — a
denial that leaves no trace cannot be shown to have happened — and allowed
connections are what makes a narrowed rule reviewable rather than merely
configured. The cap is 4096 KB per profile; Windows rotates once to
`pfirewall.log.old` and keeps no more, so the disk cannot fill and **the record
is hours rather than weeks**. Shipping it somewhere is where A6 ends and a log
collector begins.

**Rows 1, 2 and 5 to 16 are unchanged, and Linux is not covered.** This
repository configures no firewall on a Linux guest, so there is no rule to log
and nothing here to turn on; `sshd`'s own journal entries are what exists, and
they are the image's doing rather than this repository's. Rows 6 and 7 are the
node's, row 1 is `wg-vpn-01`'s, and none of those is written from here.

---

## 3 · Trust boundaries (KAN-011-A2)

**Approved administrator devices.** None are defined. There is no device
inventory, no certificate, and no allowlist — row 1's WireGuard peer list is the
de-facto boundary, and it lives on a guest this repository does not configure.

**The VPN address range.** Unknown here. Every address this repository names is
in `192.168.10.0/24`; whether VPN clients land in that subnet or are routed into
it is a `wg0.conf` question.

**DNS dependencies.** `192.168.10.2` primary, `192.168.10.1` fallback, reaching
each guest twice — once through Proxmox's cloud-init network configuration and
once through the first-boot document (BUG-018). Both are variables since
KAN-012, so an environment sets them without a code change.

**Emergency console access.** The Proxmox web UI on 8006 (row 2), and it is
outside every other boundary on this page — it does not depend on DNS, on the
VPN guest, or on the runner. [runner-trust-boundary.md](runner-trust-boundary.md)
already treats it as the recovery path, and [state-recovery.md](state-recovery.md)
assumes it. **Which makes row 2 the one flow that must not be narrowed to a path
that depends on any other row**, and the one to test the rotation exercise
against last rather than first.

**The boundary that is not a boundary.** `gha-runner-01` is a guest of the
hypervisor it manages (OPS-003, #171) and holds every credential in rows 6, 7,
11, 12 and 13. There is no network position from which the runner is outside the
lab, so a management model that separates "the lab" from "what administers it"
does not describe this one.

---

## 4 · What a rotation has to survive (A5)

Written from the dependencies above rather than from an exercise, so it is a
checklist to take into one, not a record of one.

| Rotate | Breaks, until updated | Locks you out if you get it wrong |
|---|---|---|
| WireGuard peer key | rows 1, and every other row reached through it | **Yes** — unless row 2 is reachable another way |
| Proxmox API token | rows 6, 8 — every apply and destroy | No — row 2 survives |
| Node SSH credential | row 7 — snippet upload, so applies fail part-way | No |
| Windows administrator password | rows 3, 4 | Per guest. A rebuild is the recovery |
| Arc service principal | rows 12, 13 — onboarding and cleanup | No |

The one worth planning is the first, and the reason is row 2: **the emergency
path is the console, and the console is behind the VPN unless someone is
physically at the lab.** Rotating a WireGuard key without a second way in is the
shape of mistake that ends a session — the same sentence
[unmanaged-vms.md](unmanaged-vms.md) uses about rebuilding `wg-vpn-01` while
working through it.

---

## 5 · What this cannot say, and who can

These are A3 through A7, and every one needs someone at the lab.
[lab-access-required.md](lab-access-required.md) carries them.

| Unknown | Answered by |
|---|---|
| Row 1 in full — WireGuard's port, peers, allowed IPs, key age | `wg show` on `wg-vpn-01` |
| Whether any host or perimeter firewall exists at all | `pve` firewall status, and the gateway's rules |
| The VPN address range, which is what `var.management_source_cidrs` should be set to | `wg show` — until then rows 3 and 4 stay unrestricted by default |
| Whether rows 3 and 4 are reachable from outside the VPN | a connection attempt from off-VPN |
| Whether the Windows firewall log is **actually written** — the script that turns it on has never run (OPS-004) | a rebuilt Windows guest, then `Get-Content C:\Windows\System32\LogFiles\Firewall\pfirewall.log` |
| Whether anything logs an inbound connection to a **Linux** guest | the guest, and wherever its journal goes |
| Which management paths exist that this repository never mentions | the node and the gateway — a flow declared nowhere here is invisible to §1 by construction |

The last row is the one to hold onto. **This matrix is complete with respect to
the repository and cannot be complete with respect to the lab**, so a flow it
does not list is not thereby a flow that does not exist. That distinction is
what KAN-011-A6's negative tests are for: the criterion is not "every row here
is allowed", it is "every path tested that is not a row here is denied".
