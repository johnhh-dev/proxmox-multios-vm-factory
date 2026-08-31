# Proxmox cluster quorum — why an apply fails when a node is down

Why this file exists: on 2026-08-27, run
[33074685788](https://github.com/johnhh-dev/proxmox-multios-vm-factory-v2/actions/runs/33074685788)
planned three resources, created two, and failed the third with

```
Error: VM clone
All attempts fail:
#1: error cloning VM: received an HTTP 500 response - Reason: cluster not ready - no quorum?
```

`pve2` was down. Nothing in the configuration was wrong, nothing in the plan was
wrong, and the message does not say which node was missing or what to do about
it. This is that explanation, and the reasoning behind the preflight check that
now runs before every apply and destroy.

## Why a dead peer blocks a clone on the surviving node

The instinct is that `pve` should be able to clone a VM by itself — the VM was
destined for `pve`, and `pve` was up. That is not where the block comes from.

Every VM configuration in Proxmox lives in `/etc/pve`. That is not an ordinary
directory: it is **pmxcfs**, a FUSE filesystem that corosync replicates between
all nodes. pmxcfs has one hard rule — *without quorum it mounts read-only.*

Cloning a VM means writing `/etc/pve/nodes/pve/qemu-server/<vmid>.conf`. That
write is refused in an inquorate partition regardless of which node the VM would
have run on. So is creating, starting, or destroying anything.

For a two-node cluster the arithmetic leaves no slack:

```
expected votes: 2      (pve + pve2)
quorum:         2      (a majority of 2 is 2, not 1)
pve2 down    →  1 of 2 → inquorate → /etc/pve read-only
```

**What still works:** VMs that are already running keep running — the qemu
processes do not consult corosync. Reads work, configuration is visible, the
console works. Everything that *writes* is refused.

A useful confirmation from the failed run: the `user_data` snippet uploaded
fine, because it lands in `/var/lib/vz/snippets` — ordinary disk, not cluster
filesystem. Only the VM resource failed. That is exactly the signature of a
read-only `/etc/pve`.

## The failover this does not currently give you

`pve2` exists as a replication target so that a failure of `pve` costs little
time. As the cluster stands, that does not work: if `pve` dies, `pve2` is left
with one vote out of two and is inquorate for the same reason. The replicated
data is intact and **no VM on it can be started** without intervening by hand.

Whichever fix below is chosen, this is the reason to choose one.


### What the missing qdevice actually costs, measured 2026-08-30

The arithmetic above is general. These are this cluster's numbers, and the first
row is the one worth stopping on.

| | |
|---|---|
| Guests defined on `pve` | **10** — 7 running, 1 stopped (`macos-ventura`), 2 templates |
| Guests defined on `pve2` | **0** |
| VMs replicating to `pve2` | 7, including `gha-runner-01` |
| Replication schedule | `*/15` on every job — so up to **15 minutes** of loss |
| Replication health, 2026-08-30 | all 7 `State OK`, `FailCount 0`, last sync 10:45, each run 2-6s |

The first row said "VMs running" and counted guests that are *defined*, which is
not the same number. Both are worth knowing and they answer different questions:
ten guests are lost with the node, seven of them are doing something at the time.

`pve2` hosts nothing. It exists to receive replicas. And because there is no
qdevice:

**If `pve2` goes down** — the node that runs nothing — `pve` drops to one vote
of two and `/etc/pve` goes read-only. All nine VMs keep running, and **nothing
can be created, started, stopped or destroyed** until someone runs `pvecm
expected 1`. Losing the node that hosts nothing takes out the ability to manage
the node that hosts everything.

**If `pve` goes down**, everything is down. `pve2` holds a replica of seven of
them and is itself inquorate, so it cannot start any of them either — which is
the failover this document's own section above says does not currently work,
now with a count attached.

Both cases are one command away from recoverable, and the command has to be run
by a person who knows to run it. That is the argument for the QDevice below, and
it is stronger than the arithmetic alone suggests: the loss that paralyses the
lab is the loss of the machine nobody would notice.

### Current state, measured 2026-08-30

**No qdevice is configured.** From `pvecm status` on `pve`:

```text
Expected votes:   2
Total votes:      2
```

and `/etc/pve/corosync.conf`'s `quorum { }` block holds only
`provider: corosync_votequorum` — no `device { }`. So the arithmetic above is
still exactly the arithmetic in force: either node down leaves the survivor
inquorate and `/etc/pve` read-only.

What has changed since this was written is that `pve2` is doing real work.
Seven guests replicate to it — 1100, 1101, 1103, 1104, 1105, 1106 and **1110,
the runner**. So the "replication target so that a failure of `pve` costs little
time" in the paragraph above is live, and the gap this document identifies is
what stops it being a recovery: a replica on a node that cannot form a quorum is
a copy, not a failover.

**The VM this factory manages does not replicate.** 101 has no job. Every
hand-built guest has one and the factory's guest does not, which is a real
asymmetry rather than an oversight in the reading: replication here is
configured per guest, by hand, outside this repository, and nothing creates a
job for a VM the factory builds.

(This sentence named VM 100 as well until 2026-08-30. That guest was destroyed;
only 101 is managed now.)

## Options

| | Keeps `pve2` in the cluster | Survives a reboot | Split-brain risk |
|---|---|---|---|
| Start the missing node | yes | n/a | none |
| `pvecm expected 1` | yes | **no** | while it is set |
| `two_node: 1` in `corosync.conf` | yes | yes | yes, permanently |
| QDevice on a third always-on host | yes | yes | none |
| `pvecm delnode pve2` | **no** | yes | none |

### Immediate: `pvecm expected 1`

```bash
pvecm expected 1
```

Lowers the expected vote count so the surviving node is quorate alone. **Runtime
only** — it is lost on reboot and on `systemctl restart corosync`. Safe while the
other node is genuinely powered off; if it is alive but network-isolated, both
nodes can believe they are quorate and `/etc/pve` diverges.

This is also the step that unblocks editing `corosync.conf`, because that file
lives in `/etc/pve` and cannot be written while inquorate.

### Permanent, no extra hardware: `two_node: 1`

Edit `/etc/pve/corosync.conf` — **not** `/etc/corosync/corosync.conf`, which is a
copy pmxcfs overwrites — and bump `config_version` in `totem`, or nothing
propagates:

```
quorum {
  provider: corosync_votequorum
  two_node: 1
  wait_for_all: 0
}
```

`wait_for_all: 0` is not optional here. `two_node: 1` enables `wait_for_all`
automatically, which means a node is not quorate after boot until it has seen its
peer at least once — precisely the disaster case. It has to be cleared
explicitly.

The cost: two nodes that lose contact with each other while both are alive will
both consider themselves quorate. Without HA and without shared storage the
damage is limited to a divergent `/etc/pve`, where one side's changes are lost
when the partition heals. **Do not use this with HA enabled** — that produces a
fencing race.

Practical rule for a warm standby: confirm the other node is actually dead, not
merely unreachable, before starting VMs on the survivor.

### Permanent, no split-brain: a QDevice

`corosync-qnetd` on any always-on Linux host that is not a Proxmox node gives a
third, tie-breaking vote — 3 votes, quorum 2, so one node plus the QDevice stays
quorate:

```bash
apt install corosync-qnetd        # on the third host
apt install corosync-qdevice      # on both PVE nodes
pvecm qdevice setup <qnetd-ip>    # on one PVE node
```

This is the supported answer for a two-node cluster. It does not need a new
machine, but it must not be a VM hosted on either node: starting that VM would
itself require a writable `/etc/pve`.

## What the preflight check does

[`.github/scripts/preflight_cluster.py`](../.github/scripts/preflight_cluster.py)
reads `/cluster/status` before the plan in `terraform-apply` and
`terraform-destroy`, and fails the job with a message naming the offline node if
the cluster is not quorate.

It does **not** make an apply survive a node dying halfway through — nothing at
this layer can. It removes the case where the cluster was already unwritable
when the run started, which is the case that happened, and it turns a
provider-level HTTP 500 into a sentence saying which node is missing.

It fails closed: a status it cannot parse, an endpoint that will not answer, or
a `quorate` value that is neither the integer nor the boolean form all block the
run. A guard that cannot read its input has not concluded that the input is
fine.

## Recovering from a partial apply

The failed run leaves state consistent but incomplete — in 33074685788, two of
three resources existed. No cleanup is needed: once quorum is back, re-running
the workflow plans the remainder (`1 to add`) and converges. The inventory guard
in the apply workflow is what catches the case where state and inventory have
genuinely diverged.
