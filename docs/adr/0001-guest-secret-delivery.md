# ADR 0001 · How secrets reach a guest at first boot

- **Status:** Proposed — needs `@sec` + `@iac` sign-off
- **Date:** 2026-08-22
- **Spike:** SPIKE-001 (#33) · **Blocks:** SEC-001 (#46), BUG-010 (#52) · partially SEC-007 (#47), SEC-008 (#48)
- **Deciders:** `@sec`, `@iac`

This is a decision record, not a shipped change. Nothing in the repository
behaves differently because this file exists.

---

## 1 · Every path a secret currently travels (SPIKE-001-A1)

Three secrets are in scope: `arc_sp_secret`, `windows_admin_password`,
`linux_vm_password`. Each is passed into `templatefile` in
[`main.tf`](../../main.tf) and the rendered result becomes `source_raw.data`.

| # | Path | Lifetime | Status |
|---|---|---|---|
| 1 | GitHub secret → `TF_VAR_*` in the runner process environment | job | Accepted — see §5 |
| 2 | `templatefile` → `source_raw.data` → **Terraform state** on `gha-runner-01`, unencrypted `local` backend | forever | **OPEN** |
| 3 | `source_raw.data` → SSH upload → **`/var/lib/vz/snippets/<name>-user-data.yaml`** on the Proxmox node | VM lifetime | **OPEN** |
| 4 | `terraform show -json tfplan` → `tfplan.json` `variables` block → build artifact | 90 days | Closed by SEC-002 (#34) |
| 5 | `terraform plan` text → job log | forever | Closed by SEC-003 (#35) |
| 6 | Guest: cloud-init caches the document at **`/var/lib/cloud/instance/vendor-data.txt`** — `0600` root — and the drive it came from stays attached, readable by group `cdrom` | VM lifetime | **OPEN**, and measured in §8a |
| 7 | Guest (Linux): `/usr/local/sbin/arc-onboard.sh` is written `0700` **with the SP secret inlined** and never removed | VM lifetime | Closed by SEC-001d-A1 **for guests built after it** — the script removes itself on every exit path. Still present on VM 100, which predates it; see §8a. The inlined value became a short-lived token in SEC-001a first |
| 8 | Guest (Windows): `HKLM\…\Winlogon\DefaultPassword` set to the cleartext admin password | until `AutoLogonCount` expires — unverified | **OPEN** |
| 9 | Guest (Windows): `net user Administrator <password>` passed as a **process argument** | process lifetime, plus any command-line auditing | Closed by SEC-008 (#133) — `Set-LocalUser` builds no command line |
| 10 | Both guests: `--service-principal-secret <secret>` passed as a **process argument** to `azcmagent` | process lifetime, plus any command-line auditing | Closed by SEC-001a (#122) — `--config` file, and a token rather than the secret |

Paths 4 and 5 were the *exit channels*, and were closed first. Paths 2, 3, 6–10
are the material itself.

**Status column maintained since.** 7, 9 and 10 have since been closed by the
work this decision authorised; the Status cells name the issue and the PR. What
remains open is 2 (state), 3 (the snippet on the node), 6 (cloud-init's cached
copy in the guest) and 8 (the Windows autologon credential, partially accepted
in §5). SEC-001c (#118) and SEC-001e (#120) are the issues that would close 8
and 2.

The observation that follows was true when this record was written and is no
longer:

> A search for `rm -f`, `shred`, `Remove-Item` or `Clear-` across both templates
> returns **nothing**. Neither guest cleans up after itself.

Both do now, for what they consume: the Windows template removes its Arc
connect-config in a `finally` (SEC-001a), and the Linux template's onboarding
script removes itself (SEC-001d-A1). Neither reaches path 6, which is
cloud-init's own cache rather than anything a template wrote.

## 2 · What the provider can and cannot do (SPIKE-001-A4)

The issue asks whether the bpg provider can mark rendered snippet content as
sensitive in state. **It cannot, and marking it sensitive would not solve the
problem anyway.** Both halves verified against bpg/proxmox 0.111.1 and
Terraform 1.15.9.

**The attribute carries no sensitivity flag.** Dumping the provider schema
(`terraform providers schema -json`) shows 30 attributes marked
`sensitive: true` across the provider. `proxmox_virtual_environment_file`'s
`source_raw.data` is not one of them — it has neither `sensitive` nor
`write_only` set.

**And sensitivity is the wrong tool.** A Terraform `sensitive` mark controls
*display*, not *storage*. Demonstrated with a minimal config — a
`terraform_data` resource whose input is wrapped in `sensitive()`:

```
plan output          →  + input = (sensitive value)           ← redacted
terraform.tfstate    →  "password: CANARY-SPIKE001-…"         ← cleartext, 2 occurrences
terraform show -json →  values.input: "password: CANARY-…"    ← cleartext
                        sensitive_values: {"input": true}     ← a label, beside the value
```

This matters directly: **SEC-001's acceptance criterion "`terraform show -json`
contains no credential material" is unreachable by any use of `sensitive`.**
The `sensitive()` call added by SEC-003 in `main.tf` was correct for what it
was for — keeping the body out of the job log — and buys nothing in state. The
comment there should not be read as covering path 2.

Two things the schema dump *did* turn up, both usable:

- **The provider already implements Terraform's write-only argument protocol.**
  `proxmox_acme_dns_plugin.data_wo`, `metrics_server.influx_token_wo` and
  `realm_openid.client_key_wo` all carry `write_only: true`. Write-only
  arguments are never persisted to state at all. There is no
  `source_raw.data_wo` today — but the precedent means asking for one upstream
  is a small, in-pattern request rather than a new capability.
- **`initialization.user_account.password` is `sensitive: true`** — the native
  Proxmox `cipassword` path. The Linux guest password does not need to travel
  through the snippet at all.

`initialization.vendor_data_file_id` also exists, which is the provider-side
answer SPIKE-003 (#72) needs.

## 3 · Options considered (SPIKE-001-A2, A3)

Scored on the four criteria the issue names. "Exposure" is the worst-case
lifetime of the most valuable credential left in the open.

### A · Status quo, marked sensitive

Keep `templatefile`; rely on `sensitive()`.

| | |
|---|---|
| Exposure surface | **Unchanged.** §2 shows the mark does not touch state, and nothing touches the node snippet. Paths 2, 3, 6–10 all stay open |
| Moving parts | None added |
| Offline boot | Works |
| Rotation | Rotate the SP secret → every existing snippet and state entry still holds the old one. No expiry, so an old snippet stays valuable until manually purged |

**Rejected.** This is the current design, and it is what the audit found. It is
listed to make explicit that "we already marked it sensitive" is not a fix.

### B · First-boot fetch from a secret store

Guest boots with a bootstrap identity, fetches real secrets from Azure Key Vault.

| | |
|---|---|
| Exposure surface | Moves the problem. The guest needs a credential to authenticate to the vault, and the only one available is the Arc SP secret — the thing being protected. Circular unless the hypervisor can issue an identity, which Proxmox cannot |
| Moving parts | High — a vault, an access policy, network egress at boot, a failure mode per guest |
| Offline boot | **Fails.** A guest with no Azure reachability at first boot never provisions |
| Rotation | Good, once the bootstrap problem is solved |

**Rejected** for this lab. Sound at scale where a cloud provider issues machine
identities; here it adds a hard boot-time dependency on Azure to solve a
problem the next option solves without one.

### C · Short-lived access token for Arc

Drop `--service-principal-secret`. Mint a Microsoft Entra access token in the
workflow at apply time and pass **that** into user-data.
[`azcmagent connect --access-token`][azc] is a documented, first-class
authentication option: *"Access tokens can also be used for non-interactive
authentication, but they're short-lived… typically used by automation solutions
operating on several servers over a short period of time."*

| | |
|---|---|
| Exposure surface | **The long-lived SP secret leaves the repository, the state file, the node and both guests entirely.** What lands in paths 2, 3, 6, 7 and 10 is a token that expires in roughly an hour. A snippet recovered from the node next week is worthless |
| Moving parts | Low — one token-mint step in the workflow. The SP still exists; only its *secret* stops travelling |
| Offline boot | Unchanged from today. Arc onboarding already requires Azure reachability at first boot; a token narrows the window in which that boot must happen |
| Rotation | **Structurally solved.** Nothing long-lived is in circulation to rotate |

**Chosen** for the Arc secret. It replaces "hide a permanent credential" with
"stop issuing a permanent credential", which is the only version that survives
a snippet or state file leaking.

Two supporting details from the same reference, both worth taking:

- `azcmagent` accepts service-principal details **via a config file** — *"To
  avoid exposing the secret in console logs, Microsoft recommends providing the
  service principal secret in a configuration file"*. This closes path 10 for
  whatever credential is used, token or not.
- `--service-principal-cert` exists, described as *"a more secure way to
  authenticate using service principals"*. Kept in reserve for the case where
  token lifetime turns out too short for a slow first boot.

### D · Native `cipassword` + a transient Windows credential file

For the two guest passwords, which are not Arc's problem:

- **Linux** — set `initialization.user_account.password` instead of rendering
  `chpasswd` into the snippet. It is `sensitive: true` in the provider and
  Proxmox handles it natively. Combines with SEC-007 (#47), which argues Linux
  password auth should be a deliberate choice rather than the default.
- **Windows** — no `cipassword` equivalent applies. Write the password to a
  transient file the first-boot script consumes and deletes, rather than
  inlining it into the script text, the registry and a process argument.

  > **Withdrawn 2026-08-29.** "No `cipassword` equivalent applies" was asserted
  > with nothing behind it, and SPIKE-002-A1 has since read the real template.
  > Cloudbase-Init there has `inject_user_password=true` and `CreateUserPlugin`
  > enabled — the injection machinery exists. What is actually missing is
  > narrower and is recorded in [ADR 0003 §3](0003-template-provenance.md):
  > Proxmox writes `password:` into the *user-data* document while
  > `CreateUserPlugin` reads `admin_pass` from *meta-data*. That is a testable
  > question rather than an impossibility, and §9 below is updated with it.

| | |
|---|---|
| Exposure surface | Removes the Linux password from paths 2, 3, 6 and 7. Windows shrinks from four sites to one that is deleted at first boot |
| Moving parts | Very low — one provider attribute, one file write plus a delete |
| Offline boot | Works. No network dependency at all |
| Rotation | Ordinary: change the secret, re-apply, the guest picks it up on rebuild |

**Chosen** for both guest passwords.

### E · Upstream `source_raw.data_wo`

Ask bpg/proxmox for a write-only variant of `source_raw.data`.

| | |
|---|---|
| Exposure surface | Closes path 2 completely — write-only arguments never enter state. Does **not** touch path 3: the snippet still lands on the node |
| Moving parts | None locally; an upstream dependency on someone else's release schedule |
| Offline boot | Unaffected |
| Rotation | Unaffected |

**Chosen as a parallel track, not as the plan.** Worth filing given the
precedent in §2, but SEC-001 must not wait on it, and it would only ever be
half a fix.

## 4 · Decision

**Stop circulating long-lived secrets, rather than trying to hide them.**

1. **Arc** — replace `--service-principal-secret` with a short-lived access
   token minted per apply (**C**), passed via a config file rather than a
   command-line argument. The SP secret stops entering Terraform, state, the
   node and the guests.
2. **Linux password** — move to `initialization.user_account.password` (**D**),
   out of the snippet entirely.
3. **Windows password** — deliver as a transient file the first-boot script
   consumes and deletes (**D**); stop writing it to the registry in cleartext
   and stop passing it as a process argument.
4. **Cleanup** — both templates delete what they consume. Today neither does.
5. **In parallel** — file the `source_raw.data_wo` request upstream (**E**).

What this deliberately does *not* claim: after all five, the residual snippet
on the node and the state entry still contain a **token that has already
expired**, plus the Windows password until step 3 lands. That is the accepted
residue, and it is accepted because its value decays to zero — not because it
is hidden.

## 5 · Paths explicitly accepted

- **Path 1** — `TF_VAR_*` in the runner process environment. Inherent to
  running Terraform; bounded by the runner trust boundary
  ([docs/runner-trust-boundary.md](../runner-trust-boundary.md), SEC-004).
- **Path 8, partially** — Windows autologon needs *some* credential in the
  registry to work at all. Step 3 makes it short-lived and step 4 removes it;
  eliminating autologon altogether is SEC-008's (#48) call, not this ADR's.

## 6 · Rotation procedure

| Credential | After this ADR | Procedure |
|---|---|---|
| Arc SP secret | Never leaves Azure and the runner's environment | Rotate in Entra; update `TF_VAR_ARC_SP_SECRET`. No snippet, state entry or guest holds a copy to chase |
| Arc access token | Expires on its own | None. That is the point |
| `linux_vm_password` | In state (provider-`sensitive`), not in the snippet | Update the secret, re-apply. Existing guests need a rebuild or a manual `chpasswd` |
| `windows_admin_password` | In state; transient in the guest | Update the secret, re-apply. Existing guests need a rebuild or a manual reset |
| Proxmox API token, SSH password | Unchanged by this ADR | Runner-scoped; see SEC-006 (#55) |

**Before any of this lands**, all three in-scope secrets must be treated as
already exposed — they are in the current state file, in past job logs and in
snippets on the node. Rotation is a precondition for SEC-001, not a step
within it.

## 7 · SEC-001, re-split and re-estimated (SPIKE-001-A5)

SEC-001 (#46) is currently one **L** issue whose activities do not match this
decision. Proposed replacement — five issues, each independently pullable:

| New issue | Scope | Size |
|---|---|---|
| **SEC-001a** · Arc onboarding via short-lived token | Mint the token in the workflow; both templates take `--access-token` via config file; drop `arc_sp_secret` from `templatefile` | M |
| **SEC-001b** · Linux password via `cipassword` | `initialization.user_account.password`; remove `chpasswd` from the Linux template. Coordinate with SEC-007 | S |
| **SEC-001c** · Windows credential as a transient file | Stop inlining into script text, registry and process arguments; write, consume, delete. Coordinate with SEC-008 and BUG-007 | M |
| **SEC-001d** · Guest and node cleanup | Templates remove what they consume; purge existing snippets from `/var/lib/vz/snippets/`; verify **by inspecting the node filesystem**, not by reading the template | S |
| **SEC-001e** · State migration | The existing state file holds historical cleartext from every apply to date. Re-key or rebuild; the `local` backend itself is FEAT-001's (#56) problem, this is just the purge | M |

**Re-estimate: L → M + S + M + S + M**, four of the five independent of each
other. SEC-001c and SEC-001d are the ones that need a real guest to verify.

**#46 should be reopened.** It is currently closed as *completed* with no PR
having touched it, while the board shows it In Progress. Nothing in §1's rows
2, 3 or 6–10 has changed.

## 8 · What would change this decision

- Access tokens prove too short-lived for a slow Windows first boot → move to
  `--service-principal-cert` (option C's reserve).
- `source_raw.data_wo` ships upstream → path 2 closes on its own, but paths 3
  and 6–10 still need steps 1–4.
- The lab gains a hypervisor-issued machine identity → option B becomes viable
  and is strictly better than all of the above.

## 8b · Confirming path 3 rather than reasoning about it

The snippet on the node is the one storage path a template cannot tell you
about. It is written once and kept for the whole lifetime of the VM, so a
snippet written before SEC-001a still carries a service-principal secret the
template has not contained for months — which is exactly why SEC-001d-A3 says
*"verify by inspecting the node filesystem directly, not by reading the
template."*

[`audit_node_snippets.py`](../../.github/scripts/audit_node_snippets.py) is that
inspection. It runs on the node, takes the credentials to look for from the
environment — including ones since rotated, because §6 requires rotation before
SEC-001 can close and only a person knows what the old values were — and
searches through `assert_no_secrets.variants()`, so a value containing a quote,
a backslash or a newline is found in the form it was actually written
(BUG-021).

The load-bearing variant here is base64. Both templates route every free-form
value across the template boundary encoded (BUG-010), so that is the only form a
snippet holds; a scan for the raw value would report clean.

It deletes nothing and prints no credential. What to remove from the node is
SEC-001d-A2, and that is a person's call.

Path 2 has the same tool pointed at the runner —
[`audit_state_secrets.py`](../../.github/scripts/audit_state_secrets.py), for
SEC-001e-A1 — and *the same* is meant literally: everything that decides an
answer is in
[`credential_audit.py`](../../.github/scripts/credential_audit.py), and the two
front-ends differ only in which files they enumerate and how they word the
result. Written separately they were 76% identical, which is BUG-004's shape in
BUG-004's own subsystem. The failure that duplication invites is specific: a fix
to the scanning rule applied to one and not the other means a credential found
in state and missed on the node, with nothing to say which.

## 8a · Paths 6 and 7, measured on a running guest

Read from VM 100 on 2026-08-30. Both rows in §1 were written from the templates;
this is what the guest shows.

### Path 7 is closed for guests built from now on, and open on the one that exists

```text
/usr/local/sbin/arc-onboard.sh   mode=700  owner=root  size=5287
ARC_ACCESS_TOKEN= assignments:   1
```

SEC-001d-A1 (#149) made the script remove itself. It changed the **template**,
so it takes effect on the next guest built — and VM 100 was built on 2026-08-28,
before that landed. The file is still there, still `0700`, still carrying its
token.

The table in §1 says *"Closed by SEC-001d-A1"*, and that is true of the fix and
not of the lab. **The existing guest keeps the file until it is rebuilt or the
file is removed by hand.**

What is in it has already decayed to nothing — the token was minted for the
apply of 2026-08-28 and expires in about an hour, which is SEC-001a's entire
point. So this is a correctness note about the record rather than a live
exposure, and it is the kind that matters: a path marked closed that is open on
the only guest anyone would look at.

### Path 6 is narrower than §1 says, and wider than it looks

§1 names `/var/lib/cloud/instance/user-data.txt`. The document this repository
writes is not there — it is `vendor-data.txt`, and the permissions are better
than the row implies:

```text
-rw------- root  367   user-data.txt      Proxmox's own: ciuser, sshkeys
-rw------- root  8569  vendor-data.txt    ours
-rw-r--r-- root  53    datasource
```

`0600`, root-owned. Cloud-init is careful with it.

**And the source it was copied from is not.** The cloud-init drive stays
attached for the life of the VM:

```text
sr0  4M  cidata
brw-rw---- 1 root cdrom  11, 0  /dev/sr0
cdrom:x:24:ubuntu
```

`ubuntu` — the login account — is in the `cdrom` group, so it can read the raw
drive and everything on it, including the document cloud-init cached `0600`.

**On this guest that adds nothing**, because `ubuntu` also has passwordless
sudo, so root is reachable anyway. It matters the moment a guest has an account
that is *not* an administrator: group membership here is an Ubuntu image
default, not something this repository sets or checks, and nothing would say so.

So path 6's honest form is: **a root-only cache, and a group-readable block
device holding the same bytes.** Detaching the drive after first boot would
close it, and nothing in this configuration does.

## 8c · Path 3's blast radius, measured

SEC-001d-A4 asks whether *"any backup or replication of the datastore holds
copies"* of the snippets. Nobody had looked. Measured on the node 2026-08-30:

**No.** Three ways it could have, and none of them does:

| | |
|---|---|
| Backup jobs | `/etc/pve/jobs.cfg` is empty |
| vzdump archives | `/var/lib/vz/dump/` is empty |
| ZFS snapshots of the snippets dataset | `zfs list -t snapshot -r rpool/var-lib-vz` → *no datasets available* |

That last one was the case worth checking rather than assuming, because
`/var/lib/vz` **is** a ZFS dataset (`rpool/var-lib-vz`) — so snapshots there
would have held every snippet ever written. There are none. The thirteen
snapshots on this node are guest disks: two template bases and eleven
`__replicate_*` markers.

**Replication does not reach the snippets either.** PVE storage replication is
per-VM over `zfspool` storages; `local`, which holds
`/var/lib/vz/snippets/`, is a `dir` storage and is not replicated. The seven
jobs configured copy guest disks to `pve2`, not snippets.

So path 3's exposure is exactly what SEC-001d-A3 found on the node and no
larger: **two files, one per managed VM, current.** That is a smaller blast
radius than SEC-001d (#119) assumed, and it is now measured rather than feared.

**What replication does copy is worth separating from this.** The guest disks of
seven hand-built VMs go to `pve2`, and a cloud-init disk is a guest disk. None
of those seven is built by this factory, so none carries a snippet this
repository rendered — but if DOC-001 (#59) imports any of them and a later apply
attaches a vendor-data document, that changes, and this paragraph is where to
come back to.

## 8d · What SEC-001d-A2 actually has to purge

A2 says *"purge existing snippets from `/var/lib/vz/snippets/` on the node"*,
written when §1 believed snippets from every apply to date were accumulating
there. A3 and A4 measured it, and the job is much smaller and slightly stranger
than that.

**There is nothing historical to purge.** Two files, one per managed VM, both
from the most recent apply. `overwrite = true` and a per-VM `file_name` mean
each apply replaces rather than appends, and no backup, archive or snapshot
holds an older one (§8c).

**What is there is current, and it is current on purpose.** Those two documents
are how a guest is configured at first boot. Deleting them does not undo a
provisioned guest — cloud-init consumed them once — but it does mean a
`terraform apply` writes them straight back, because they are a managed resource.

So A2 is not a cleanup. It is a question about steady state:

| | |
|---|---|
| Delete them by hand | they return on the next apply. Buys nothing, and the plan then shows a create nobody asked for |
| Delete them at the end of the apply | the guest has already read them; a later `-replace` would need them rewritten, which the apply does anyway |
| Leave them | what happens today. Two documents, `0644`, holding whatever the templates render |

The third is the honest description of the status quo and the second is the only
one that changes anything. **Neither is worth doing while the documents still
carry a credential**, because the exposure is the content, not the file's
lifetime — and SEC-001c (#118) is what decides whether the Windows one still
carries the administrator password at all.

**Recommendation: A2 waits for SEC-001c**, and is then a one-line addition to
the apply — remove the snippet once the VM that consumes it exists. Recorded
here rather than left as an activity that reads like a cleanup nobody has done.

The Linux document already carries only an expired token (SEC-001a), so on that
side this is already close to moot.

## 9 · Option D does not fit the Windows template, and SEC-001c cannot pretend otherwise

Recorded while implementing SEC-001c ([#118](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/issues/118)).

Section 4 step 3 says the Windows password should be delivered "as a transient
file the first-boot script consumes and deletes". **There is no way to write
that file.**

`cloudinit/windows.yaml.tftpl` is not a cloud-config document with a
`write_files` section, the way the Linux template is. It is a bare
`#ps1_sysnative` PowerShell script, and it is the *whole* of the vendor-data.
So the only channel that reaches the guest is the script itself, and a transient
file written by that script must be written from a value the script already
carries. The password would be inlined into the script text in order to be
taken out of it.

That makes SEC-001c's first acceptance criterion — "the password is absent from
script text, process arguments and the rendered snippet" — unreachable by option
D, in the same way section 2 showed SEC-001's `terraform show -json` criterion
was unreachable by any use of `sensitive`. Two of its three clauses are met:

| Clause | Status |
|---|---|
| absent from process arguments | **met** — SEC-008 (#133), `Set-LocalUser` and `Set-ItemProperty` build no command line |
| absent from the rendered snippet | **not met, and not reachable this way** |
| absent from script text | **not met** — the script text *is* the snippet |

### What SEC-001c does instead

Reduce what can be reduced, and say which is which.

- **Path 8 closes by default.** Autologon becomes opt-in and defaults to off, so
  the password is not written to `HKLM\…\Winlogon\DefaultPassword` at all
  unless a VM asks. Section 5 accepted path 8 conditionally — "autologon needs
  *some* credential to function" — which is true and is the wrong lever. The way
  out is not to shorten the credential's life; it is to not ask for autologon.
  RDP is enabled by the same script and is how an operator reaches the machine.
- The password's **length** stops being written to a world-readable log.
- The plaintext is **dropped from the process** as soon as its last consumer has
  run, rather than living through the agent download, the Arc onboarding and the
  reboot.

None of that touches the snippet or state. It is a smaller residue, not a
closed path.

### What would actually close it

Two candidates, neither implementable without evidence this repository does not
have:

1. **`source_raw.data_wo` upstream** — option E. Closes path 2 outright, since
   write-only arguments never enter state. Does nothing for path 3.
2. **`initialization.user_account.password` for Windows.** §3D asserted "no
   `cipassword` equivalent applies". **That assertion is withdrawn** — SPIKE-002-A1
   read the real template on 2026-08-29 and the injection machinery is enabled:
   `inject_user_password=true` with `CreateUserPlugin`.

   The remaining question is one step narrower and no longer needs a guess.
   Verified on the node, in `/usr/share/perl5/PVE/QemuServer/Cloudinit.pm`:
   `cloudinit_userdata()` writes `password:` into the **user-data** document,
   and `configdrive2_gen_metadata()` writes no `admin_pass`. `CreateUserPlugin`
   reads the password from meta-data. So the two halves do not currently meet,
   and what decides it is whether `UserDataPlugin`'s cloud-config handling acts
   on a top-level `password:` key.

   **And OPS-004 (#176) has since made this a choice rather than a test.** The
   Windows first-boot script never ran, because `cicustom: vendor=` lands in
   `/openstack/latest/vendor_data.json` and nothing executes it. The fix is
   `user=` for Windows — which *replaces* Proxmox's generated user-data, and
   `cipassword` travels in exactly that document.

   So on Windows there are three options and only one can be taken:

   | | The script runs | `cipassword` reaches the guest |
   |---|---|---|
   | `cicustom: vendor=` | **no** — the state before OPS-004 | yes, and applied by nothing observable |
   | `cicustom: user=` | yes | **no** — the generated user-data is replaced |
   | `LocalScriptsPlugin` in the template | yes | yes |

   The third is the only one that gets both, and it is a **template** change:
   the real Cloudbase-Init config already enables
   `cloudbaseinit.plugins.common.localscripts.LocalScriptsPlugin`, so a script
   placed in the template's LocalScripts directory would run without occupying
   the user-data slot. That makes it SPIKE-002's territory — build-as-code for
   the templates — rather than something this configuration can do.

   Until then, `user=` is chosen because a first-boot script that does not run
   is a larger problem than a password that travels in the document rather than
   beside it.

   If it works, the Windows password leaves the snippet the same way SEC-001b
   took the Linux one out, and SEC-001c's acceptance criterion becomes
   reachable. If it does not, the criterion should be rewritten rather than left
   standing as something no implementation can satisfy.

**This is the next thing to test on the first Windows guest that gets rebuilt**,
alongside SEC-001c-A5's post-boot inspection — and it is now a one-line
configuration change to try rather than an open research question.

---

[azc]: https://learn.microsoft.com/en-us/azure/azure-arc/servers/azcmagent-connect

## References

- [`azcmagent connect` CLI reference][azc] — authentication options, `--access-token`, `--config`, `--service-principal-cert`
- [Azure Arc security onboarding guidance](https://learn.microsoft.com/en-us/azure/azure-arc/servers/security-onboarding)
- `terraform providers schema -json` against bpg/proxmox 0.111.1
- [docs/plan-output-redaction.md](../plan-output-redaction.md) — SEC-003, path 5
- [docs/runner-trust-boundary.md](../runner-trust-boundary.md) — SEC-004, path 1
