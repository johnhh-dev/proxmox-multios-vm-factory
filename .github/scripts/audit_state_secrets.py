#!/usr/bin/env python3
"""Report which credentials appear in the Terraform state, and where.

SEC-001e-A1 (#120): *"confirm what the current state actually contains, and
record which credentials appear."* Nothing could answer that question without
someone on the runner opening the file by hand, which is a poor way to establish
a fact that gates ADR 0004.

This is the same question `assert_no_secrets.py` asks of plan output, pointed at
state instead - and it reuses that module's `variants()` rather than
reimplementing it. BUG-021 is why: a substring test misses a password containing
a quote, a backslash or a newline, so the guard was weakest on exactly the
values worth protecting. Any scanner written fresh here would have been weak in
the same way.

## What it scans

The state file **and its backups**, because there are up to twenty of those
beside it since FEAT-001-A3 (#148) and each holds the same historical cleartext.
`state-recovery.md` and `runner-trust-boundary.md` both record that SEC-001e's
purge has to include them; a scan that did not would report a clean state file
sitting next to twenty copies of what it used to contain.

## What a finding means

Today, findings are expected. What still lands in state on every apply: the
Proxmox API token, the node SSH credential, the Windows administrator password,
and an Arc token that has already expired. What stopped landing there:
the service-principal secret (#122) and the Linux password (#127).

So this is an audit tool first and a gate second. Exit 1 on any finding, because
SEC-001e's acceptance criterion is that a search returns nothing - once the
purge has happened, this is what proves it, and a tool that exits 0 either way
proves nothing.

## What it never does

Print a credential. A finding names the variable, the file and which rendering
matched. That is the discipline assert_no_secrets.py already keeps, and the
reason is the same: this runs where the credentials are.

Usage:
  SECRET_VARS='TF_VAR_proxmox_api_token,TF_VAR_windows_admin_password' \
    python3 audit_state_secrets.py /opt/terraform-state/proxmox-ubuntu-vm-factory

Exit codes: 0 nothing found, 1 at least one credential appears, 2 nothing could
be scanned - which is never reported as "clean".
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import credential_audit  # noqa: E402

STATE_NAME = "terraform.tfstate"
BACKUP_PREFIX = "terraform.tfstate."


def files_to_scan(target: str) -> list:
    """The state file and every backup beside it, or one named file.

    A directory is the ordinary call - it is what the runner holds - and
    scanning only the live file there would be the mistake this exists to avoid.
    """
    if os.path.isfile(target):
        return [target]

    found = []
    live = os.path.join(target, STATE_NAME)
    if os.path.isfile(live):
        found.append(live)

    backups = os.path.join(target, "backups")
    if os.path.isdir(backups):
        for name in sorted(os.listdir(backups)):
            if name.startswith(BACKUP_PREFIX):
                found.append(os.path.join(backups, name))
    return found


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_state_secrets.py <state-dir-or-file>", file=sys.stderr)
        return 2

    return credential_audit.audit(
        files_to_scan(sys.argv[1]),
        unit="file",
        empty_message=(
            f"no state file or backup found under {sys.argv[1]}. "
            "Finding nothing to scan is not the same as finding nothing."
        ),
        found_note=(
            "Expected before SEC-001e; after the purge this returning nothing "
            "is the acceptance criterion."
        ),
        clean_message="No scanned credential appears in any of them.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
