#!/usr/bin/env python3
"""Copy the Terraform state aside before a run is allowed to write to it (FEAT-001-A3).

docs/state-recovery.md documents a restore and a drill that proves it works. Its
own §5 then lists what is missing, and the first row is "somewhere to restore
*from* - no automated backup exists". The runbook restores from a backup nothing
was taking. This takes it.

## What this protects against, and what it does not

**It does not meet FEAT-001's outcome, and the PR that introduced it says so.**
The finding in #56 is that losing the runner's disk loses the mapping between
configuration and the lab. A copy on that same disk does not survive the disk.

What it does cover is every failure that has actually happened to this
repository or is one step away from it: a half-applied plan whose state is
consistent but incomplete (BUG-024, run 33074685788), an apply that destroys
guests nobody meant to destroy (BUG-012), a truncated or empty-restored state
(the third row of the inventory guard's table), and an operator running
terraform by hand on the runner outside the concurrency group.

Off-host is FEAT-001-A1's decision - remote backend or an encrypted copy - and
it is deliberately not made here. It cannot be made by adding an upload step:
the state file holds cleartext credentials for its whole history, so an
unencrypted copy anywhere off the runner is the SEC-002 mistake with a different
filename. See the retention note below.

## Retention, and its tension with SEC-001e

Every backup is another copy of a file holding cleartext credentials, and
SEC-001e (#120) exists to purge exactly that. Two things follow, both
deliberate: the count is bounded so the directory cannot grow without limit, and
**SEC-001e's purge must include the backup directory**. A purge that cleans
terraform.tfstate and leaves twenty timestamped copies of it beside the file has
done nothing.

Backups are written 0600 into a directory created 0700, inside the state
directory the runner already owns (docs/runner-trust-boundary.md).

Usage:
  backup_state.py --state-file PATH --backup-dir PATH [--keep N] [--label TEXT]

Exit codes: 0 taken or legitimately skipped, 1 the backup could not be trusted.
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
from datetime import datetime, timezone

# Anything else is stripped from --label. The label reaches a filename, and the
# run id is the only thing put there today, but a value from the workflow
# environment should not be able to write outside the backup directory whatever
# it turns out to contain.
LABEL_SAFE = re.compile(r"[^A-Za-z0-9_.-]")

PREFIX = "terraform.tfstate."


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_label(label: str) -> str:
    cleaned = LABEL_SAFE.sub("-", (label or "").strip())
    return cleaned[:64] or "manual"


def backup_name(label: str, now: datetime) -> str:
    return f"{PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}.{safe_label(label)}"


def existing_backups(backup_dir: str) -> list[str]:
    """Newest first, ordered by name.

    The timestamp leads the suffix and is fixed-width UTC, so lexical order is
    chronological order. Reading mtime instead would order by when the file was
    last *touched*, which a copy or a restore changes.
    """
    try:
        names = os.listdir(backup_dir)
    except FileNotFoundError:
        return []
    return sorted((n for n in names if n.startswith(PREFIX)), reverse=True)


def prune(backup_dir: str, keep: int, protect: str) -> list[str]:
    """Delete all but the newest `keep`, never the one just written.

    `protect` is belt and braces rather than logic: the new backup is the
    newest, so it is inside any keep >= 1. It matters for keep <= 0, which is
    refused by the argument parser, and it is here so that a future caller
    cannot turn this into a function that deletes the backup it just made.
    """
    removed = []
    for name in existing_backups(backup_dir)[max(keep, 1):]:
        if name == protect:
            continue
        os.remove(os.path.join(backup_dir, name))
        removed.append(name)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--keep", type=int, default=20)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    if args.keep < 1:
        print("::error::--keep must be at least 1", file=sys.stderr)
        return 1

    # An absent state file is the first apply, and it is not an error. An empty
    # one is different and is also not this script's problem: the inventory
    # guard decides whether an empty state may proceed, and copying zero bytes
    # aside would only add a useless file to the retention window.
    if not os.path.isfile(args.state_file):
        print(f"No state file at {args.state_file} yet - nothing to back up.")
        return 0
    if os.path.getsize(args.state_file) == 0:
        print(f"State file at {args.state_file} is empty - nothing worth backing up.")
        return 0

    try:
        os.makedirs(args.backup_dir, mode=0o700, exist_ok=True)
    except OSError as exc:
        print(
            f"::error::cannot create {args.backup_dir} ({exc.strerror}). "
            "The state directory is created during runner provisioning - see "
            "docs/runner-trust-boundary.md.",
            file=sys.stderr,
        )
        return 1

    name = backup_name(args.label, datetime.now(timezone.utc))
    target = os.path.join(args.backup_dir, name)

    try:
        shutil.copyfile(args.state_file, target)
        os.chmod(target, 0o600)
    except OSError as exc:
        print(f"::error::could not write {target} ({exc.strerror})", file=sys.stderr)
        return 1

    # A backup that silently truncated is worse than no backup, because the
    # runbook would restore from it and the plan afterwards would propose to
    # rebuild whatever the copy lost. Compare rather than assume.
    source_hash = sha256(args.state_file)
    if sha256(target) != source_hash:
        print(
            f"::error::{target} does not match {args.state_file}. "
            "The copy is not a usable backup and the run should not continue.",
            file=sys.stderr,
        )
        return 1

    size = os.path.getsize(target)
    print(f"Backed up {size} bytes to {target}")
    print(f"sha256 {source_hash}")

    for removed in prune(args.backup_dir, args.keep, protect=name):
        print(f"Pruned {removed}")

    kept = existing_backups(args.backup_dir)
    print(f"{len(kept)} backup(s) retained, keeping {args.keep}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
