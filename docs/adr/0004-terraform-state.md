# ADR 0004 · Where Terraform state lives

- **Status:** Proposed — needs `@iac` + `@sec` sign-off
- **Date:** 2026-08-29
- **Issue:** FEAT-001-A1 (#56) · **Unblocks:** DOC-006-A2 (#74), FEAT-001-A2
- **Deciders:** `@iac`, `@sec`

This is the record [adr/README.md](README.md) calls *"the one that is missing"*.
Its own text says why it did not exist: state being a local file was never
decided, it is what the repository started with, and writing a record for it
would have been inventing deliberation that did not happen. FEAT-001-A3 has
since settled where a *backup* goes. This settles the rest.

---

## 1 · What is actually true today

| | |
|---|---|
| Backend | `local`, at `/opt/terraform-state/proxmox-ubuntu-vm-factory/terraform.tfstate` |
| Host | `gha-runner-01`, one self-hosted runner ([runner-trust-boundary.md](../runner-trust-boundary.md)) |
| Encrypted at rest | **No.** A plaintext file on an ordinary disk |
| Locking | Terraform's local-backend file lock. **Unverified** — an attempt to observe it saw the second apply succeed, on Windows, which is not the runner's platform ([state-recovery.md](../state-recovery.md) §5). Every locking command now passes `-lock-timeout=10m`, so if the lock does work, a second run waits rather than failing |
| Serialised against concurrent runs | **Apply and destroy only**, by the `terraform-lab-state` concurrency group — which serialises *runs*, not writes. `terraform-plan.yml` has never been in that group and plans against the same backend; what keeps it out of an apply's way is that there is one runner. Anyone running `terraform` by hand on the runner is outside all of it (KAN-017-A2) |
| Backup | Last 20 copies in `backups/` beside it, taken before every apply and destroy (FEAT-001-A3) |
| Survives the runner's disk dying | **No.** The backups go with it |

**And it holds cleartext credentials for its whole history.** That is not a
side note; it decides most of what follows. SEC-001e (#120) is the purge, and
until it runs, every copy of this file — including all twenty backups — carries
the Proxmox API token, the node SSH credential and both guest passwords from
every apply ever made.

## 2 · What was verified, and what was only read

Against the pinned toolchain, on 2026-08-29:

```console
$ terraform version
Terraform v1.15.8

$ terraform -help | grep -i encrypt
(nothing)
```

**Terraform has no native state encryption.** There is no subcommand and no
`encryption` block; the feature exists in OpenTofu 1.7+, which this repository
does not use. So "encrypted at rest" has to come from whatever stores the file,
not from Terraform.

Backend types this binary accepts, probed one at a time:

| Accepted | `local` `azurerm` `s3` `gcs` `http` `pg` `consul` `kubernetes` `oss` `cos` |
|---|---|

And what `azurerm` insists on before it will initialise:

```
Error: One of `access_key`, `sas_token`, `use_azuread_auth` and
`resource_group_name` must be specified
```

Everything else below — lease-based locking, DynamoDB tables, retention
policies — is read from documentation, not observed here, and is marked where it
matters.

## 3 · The criteria

Four, in the order they decide the answer for *this* lab:

1. **Does it survive the runner?** The finding in #56 is that losing one disk
   loses the mapping between configuration and the lab. Anything that fails here
   fails.
2. **Is the file encrypted at rest?** It holds credentials, and Terraform will
   not encrypt it for us.
3. **What breaks when the store is unreachable?** Today a plan works with
   everything except the runner down. Every remote option trades some of that.
4. **What does it cost to run and to hold?** A lab with one runner should not
   acquire a database to hold a 40 KB file.

## 4 · Options

### A · Status quo

Local file, same-disk backups.

| | |
|---|---|
| Survives the runner | **No** |
| Encrypted at rest | No |
| Unreachable store | N/A |
| Cost | Nothing |

**Rejected.** It is the finding. Listed so that "we take backups now" is not
mistaken for having addressed it — FEAT-001-A3 says so about itself.

### B · Encrypted copy pushed off-host

Keep the local backend. Extend the existing backup step to encrypt each copy and
push it somewhere else — a GitHub Actions artifact, or another machine.

| | |
|---|---|
| Survives the runner | Yes |
| Encrypted at rest | Yes, by construction — it has to be, or it is SEC-002 with a new filename |
| Unreachable store | **Nothing breaks.** The apply proceeds; the backup fails and is visible |
| Cost | An encryption key, and a place to keep it that is not the runner |
| Locking | **Unchanged — still nothing** |

The key is the whole problem. A key on the runner protects against the disk
being read elsewhere and not against the runner being compromised, which is the
threat [runner-trust-boundary.md](../runner-trust-boundary.md) is about. A key
in GitHub Secrets is available to every workflow run, which is the same set of
things that can already read the state — so it buys protection against the
*artifact store*, not against the runner.

**Not rejected, and not sufficient.** It answers criterion 1 cheaply and
criterion 2 weakly, and does nothing for locking.

### C · `azurerm` remote backend

State in an Azure Storage container. Encrypted at rest by the platform; locking
by blob lease.

| | |
|---|---|
| Survives the runner | Yes |
| Encrypted at rest | Yes, without a key this repository has to hold |
| Unreachable store | **Plan and apply both fail.** Terraform cannot read state |
| Cost | A storage account; effectively nothing at this volume |
| Locking | Blob lease — real, and *documented* rather than observed here |

**Azure is already a dependency, and that is a weaker argument than it looks.**
The Arc path needs Azure at apply time — but it needs it *softly*: if the token
mint fails, `mint_arc_token.py` writes an empty token and guests skip
onboarding. A plan still works with Azure down. Moving state here makes Azure a
**hard** dependency of every plan, including a plan whose only purpose is to
review a change to a comment.

The credential question is sharper. The Arc service principal exists, and could
be granted `Storage Blob Data Contributor`. **It should not be.** That SP's
secret is what SEC-001a took out of circulation precisely because it was too
powerful to travel; giving it write access to the state store makes one
credential the key to both onboarding and the record of what exists. A separate
identity — ideally the runner's managed identity, if the runner ever gets one —
is the version worth having.

### D · `s3` against something in the lab

MinIO or equivalent, self-hosted.

| | |
|---|---|
| Survives the runner | Only if it runs somewhere else — and the obvious somewhere else is Proxmox, which is what the state describes |
| Encrypted at rest | Whatever is configured |
| Unreachable store | Plan and apply fail |
| Cost | **A service to run, patch and back up**, to hold a 40 KB file |

**Rejected.** The circularity is the reason: state describing the lab, stored in
the lab. A Proxmox outage would take out both the VMs and the record of them,
which is the failure mode this whole issue exists to remove.

### E · Terraform Cloud / HCP

| | |
|---|---|
| Survives the runner | Yes |
| Encrypted at rest | Yes |
| Unreachable store | Plan and apply fail |
| Cost | Free tier is adequate; a third party holds the file |

**Rejected for this lab, on the same reasoning SEC-004 applies to the runner:**
the state contains credentials for someone else's hypervisor and an Azure
tenant, and adding a party that holds all of it is a larger change than the
problem needs. Reconsider if the lab ever wants remote operations and policy
enforcement, which is what actually justifies it.

## 5 · Decision

**C, with B as the step that can be taken today.**

1. **Now — B.** Encrypt the FEAT-001-A3 backups and push them off the runner.
   It answers "the disk died" without a hard dependency on anything, and it is a
   change to one script and one step. Its weakness — the key lives where the
   state does — is acceptable *as a stopgap* and is not acceptable as the
   answer.
2. **Then — C**, with a **dedicated** identity, not the Arc service principal.
   This is what closes encryption and locking together, and it is the only
   option here that touches locking at all.
3. **Before either — SEC-001e.** Migrating a file full of historical cleartext
   into a new store copies the problem into it. The purge is a precondition, the
   same way ADR 0001 §6 made rotation a precondition rather than a step.

### What is deliberately accepted

**Every plan gaining a hard dependency on Azure**, once C lands. That is a real
loss: a review of a comment change will fail when Azure is unreachable, where
today it does not. It is accepted because the alternative is a state file whose
only copy is on a machine nobody has a rebuild procedure for.

### What is deliberately not decided

- **Whether locking is a problem worth solving on its own.** The concurrency
  group covers the case that has actually happened. The uncovered case is a
  human running `terraform apply` on the runner by hand, and the honest fix for
  that may be "do not", written down, rather than a backend.
- **Where the encryption key for B lives.** That is the whole of B's weakness,
  and picking a place is the first thing implementing it has to argue.

## 5b · Measured after this was written

Read from the node on 2026-08-30, and one of it corrects §1.

**The runner is a guest of the hypervisor** — VM 1110 on `pve` (OPS-003, #171).
§4D rejected self-hosted S3 for "state describing the lab, stored in the lab"
without knowing that already described the status quo.

**But VM 1110 replicates to `pve2`.** This is a two-node cluster, and every
hand-built guest replicates; §1's "survives the runner's disk dying: **No**" is
therefore too absolute. A replica of the state exists on a second machine.

It does not change the decision, for three reasons worth stating rather than
leaving implied:

| | |
|---|---|
| Replication is asynchronous, and the window is **15 minutes** | every job runs on `*/15`, so the replica can be a quarter of an hour behind the state it copies |
| Nothing in this repository configured or checks it | it can be removed without anything noticing |
| The cluster has no qdevice | expected votes 2, total 2 — either node dying leaves the other inquorate, which is BUG-024's failure |

**And a VM this factory manages does not replicate at all.** VM 101 had no
replication job; 1100, 1101, 1103, 1104, 1105, 1106 and 1110 do. The guest the
factory built was the least protected thing on the node, which is worth knowing
before DOC-001 (#59) imports anything — and it is now the least protected thing
that no longer exists: 101 was destroyed on 2026-08-30, so the factory manages
nothing at all and every copy of state below describes an empty inventory.

Two corrections to that row and that sentence, both measured on 2026-08-30 and
both kept visible rather than edited away, because the first is the reason this
section exists at all:

- **"Nothing here knows the schedule" was a hedge, not a fact.** `pvesr list`
  answers it in one command, and the answer is `*/15` on all seven jobs, each
  run taking 2–6 seconds with `FailCount 0`. What that turns into is a number
  an operator can decide against: an apply that finishes and is followed by a
  node failure inside that window leaves a replica describing a lab that no
  longer matches it.
- **This named VM 100 as well.** That guest was destroyed on 2026-08-30, so
  there is one managed VM rather than two. The asymmetry it points at is
  unchanged and is worth restating: replication here is configured per guest,
  by hand, outside this repository — so a guest the factory builds gets no job,
  and an imported one keeps none.

## 6 · What would change this

- **SEC-001e ships and state stops holding credentials.** Then criterion 2
  weakens sharply, B's key problem mostly evaporates, and C's main advantage
  becomes locking rather than encryption.
- **The runner gets an Azure managed identity.** Then C's credential objection
  disappears and C becomes clearly correct rather than correct-with-conditions.
- **The lab acquires a second runner.** Then the local backend stops being
  viable at all, because two runners cannot share a file, and this decision
  stops being optional. It would also end the serialisation §1 used to credit
  to the concurrency group, silently and with no diff anywhere — registering a
  runner is a settings change, not a commit.
- **The repository moves to OpenTofu.** Native state encryption would make
  encrypted-local a real option, and it is not one today.

---

## References

- `terraform version` and backend-type probes, run 2026-08-29 against the pinned 1.15.x
- [state-recovery.md](../state-recovery.md) — the restore, the drill, and the locking observation
- [runner-trust-boundary.md](../runner-trust-boundary.md) — what else `gha-runner-01` holds
- [ADR 0001 §6](0001-guest-secret-delivery.md) — rotation as a precondition, the pattern §5.3 follows
- FEAT-001 (#56), SEC-001e (#120), DOC-006-A2 (#74)
