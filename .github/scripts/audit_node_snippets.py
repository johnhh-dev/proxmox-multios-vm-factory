#!/usr/bin/env python3
"""Report which credentials appear in the snippets left on the Proxmox node.

SEC-001d-A3 (#119) asks for this in the strongest terms an activity in this
repository uses: *"verify by inspecting the node filesystem directly - not by
reading the template. This is the whole point of the activity."*

Reading the template proves what the current template renders. It proves nothing
about the file sitting in /var/lib/vz/snippets/ that a *previous* apply wrote,
and ADR 0001 path 3 is precisely that file: written once, kept for the whole
lifetime of the VM, and never revisited. A snippet from before SEC-001a still
carries a service-principal secret that the template has not contained for
months.

Run on the node, where those files are:

    SECRET_VARS='TF_VAR_windows_admin_password,TF_VAR_arc_sp_secret' \
      python3 audit_node_snippets.py /var/lib/vz/snippets

## Why it takes credentials from the environment

Because it must find *historical* values, and only a person knows what those
were. The obvious alternative - scan for anything password-shaped - answers a
different question and answers it badly: it cannot tell a rotated credential
from a current one, and it would report the base64 of an expired Arc token as
confidently as a live administrator password.

So the operator supplies what to look for, including credentials that have since
been rotated. ADR 0001 §6 already says every in-scope credential must be treated
as exposed and rotated before SEC-001 can close; this is how the old ones get
found afterwards.

## What it shares with audit_state_secrets.py

The search itself - assert_no_secrets.variants(), for BUG-021's reason: a
substring test misses a value containing a quote, a backslash or a newline, so
it is weakest on the credentials worth protecting. Both templates additionally
base64 every free-form value across the template boundary (BUG-010), and that
variant is the load-bearing one here rather than a hypothetical: a snippet
carries the encoded form, not the raw one.

## What it never does

Print a credential, or delete anything. A finding names the file, the variable
and which rendering matched. Deciding what to remove from a hypervisor is not a
script's call - the runbook is docs/arc-cleanup.md's sibling reasoning, and
SEC-001d-A2 is the activity.

Exit codes: 0 nothing found, 1 at least one credential appears, 2 nothing could
be scanned - which is never reported as clean.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import credential_audit  # noqa: E402

# Proxmox writes snippets as plain files in one flat directory. Anything else in
# there is not this tool's business, but a stray .bak or an editor's swap file
# holding the same content very much is - so the filter is only on being a file.
SKIP_NAMES = {".", ".."}


def files_to_scan(target: str) -> list:
    if os.path.isfile(target):
        return [target]
    if not os.path.isdir(target):
        return []
    return sorted(
        os.path.join(target, name)
        for name in os.listdir(target)
        if name not in SKIP_NAMES and os.path.isfile(os.path.join(target, name))
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_node_snippets.py <snippets-dir-or-file>", file=sys.stderr)
        return 2

    return credential_audit.audit(
        files_to_scan(sys.argv[1]),
        unit="snippet",
        empty_message=(
            f"nothing to scan under {sys.argv[1]}. An empty or absent snippets "
            "directory is not the same as a clean one - check the path and that "
            "this is running on the node."
        ),
        found_note=(
            "Nothing is deleted here - what to remove from the node is "
            "SEC-001d-A2, and it is a person's call."
        ),
        clean_message="No scanned credential appears in any snippet on this node.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
