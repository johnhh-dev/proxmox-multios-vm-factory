#!/usr/bin/env python3
"""The shared half of the two credential audits.

`audit_state_secrets.py` (SEC-001e-A1) and `audit_node_snippets.py`
(SEC-001d-A3) ask the same question of two different places: which of these
credentials appears in these files, and in what rendering. Written separately
they were 76% identical — everything except which files to enumerate and how to
word the answer.

That is BUG-004's shape exactly, and BUG-004 is in this subsystem: an Arc block
duplicated between two callers, where one copy carried a defect for months. The
duplication here would have been worse in one specific way — a fix to the
scanning rule applied to one auditor and not the other means a credential found
in state and missed on the node, or the reverse, with nothing to say which.

So the enumerating and the wording stay in the front-ends, and everything that
decides an answer lives here.

## What lives here, and why each piece is not negotiable

- **The search** is `assert_no_secrets.variants()`, not a substring test.
  BUG-021: a value containing a quote, a backslash or a newline does not appear
  raw in what Terraform writes, so a substring guard is weakest on exactly the
  credentials worth protecting.
- **A value too short to search for is refused, not skipped** (BUG-021-A3).
  Reporting a scan that proved nothing about a credential is worse than not
  scanning it.
- **Nothing to scan is never "clean".** An empty `SECRET_VARS`, an absent
  directory, an unreadable file — each exits 2. Inventing "clean" is the one
  result these tools must never produce, because someone will act on it.
- **No credential is ever printed.** A finding names the variable, the file and
  which rendering matched. Both auditors run where the credentials are.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import assert_no_secrets as scanner  # noqa: E402


def resolve_values(names: list) -> tuple:
    """Split the requested names into scannable, unset, and unusable.

    Unset and unusable are different answers and are reported differently: Arc
    is optional here, so an absent optional credential cannot leak, while a
    value that is set and too short is a credential this cannot certify.
    """
    values, unset, unusable = {}, [], []
    for name in names:
        value = os.environ.get(name, "")
        if not value:
            unset.append(name)
        elif len(value) < scanner.DEFAULT_MIN_LEN:
            unusable.append(name)
        else:
            values[name] = value
    return values, unset, unusable


def scan_file(path: str, values: dict) -> list:
    """Which named values appear in this file, and in which rendering.

    The flattened comparison is BUG-021's other half: a multi-line value is
    written as an indented block, so the file contains no contiguous copy of it
    at all. That one was found by measuring real output rather than reasoning.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise RuntimeError(f"could not read {path} ({exc.strerror})") from exc

    flattened = scanner.unindent(text)
    findings = []
    for name, value in values.items():
        for form, rendering in scanner.variants(value).items():
            if rendering in text or rendering in flattened:
                findings.append((name, form))
                break
    return findings


def audit(targets: list, unit: str, empty_message: str, found_note: str, clean_message: str) -> int:
    """Run the audit and report it. Returns the process exit code.

    `unit` is what to call a scanned file in the summary line - "file" or
    "snippet". The two notes are the only other thing that differs between the
    callers, and they differ because what a finding *means* differs: one is
    measured against SEC-001e's acceptance criterion, the other is a list for a
    person to act on.
    """
    names = scanner.secret_var_names(os.environ.get("SECRET_VARS", ""))
    if not names:
        print(
            "::error::SECRET_VARS is empty. Scanning for nothing and reporting "
            "clean is the one result this must never produce.",
            file=sys.stderr,
        )
        return 2

    values, unset, unusable = resolve_values(names)

    if not targets:
        print(f"::error::{empty_message}", file=sys.stderr)
        return 2

    if unusable:
        print(
            "::error::cannot form a reliable pattern for: "
            + ", ".join(unusable)
            + ". These were NOT scanned.",
            file=sys.stderr,
        )

    findings = []
    for path in targets:
        try:
            for name, form in scan_file(path, values):
                findings.append((path, name, form))
        except RuntimeError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 2

    print(f"Scanned {len(targets)} {unit}(s) for {len(values)} credential(s).")
    if unset:
        print("  not set, so nothing to find: " + ", ".join(sorted(unset)))

    if findings:
        print()
        for path, name, form in findings:
            print(f"  {os.path.basename(path)}: {name} (as {form})")
        print(f"\n{len(findings)} occurrence(s). {found_note}")
        return 1

    if unusable:
        return 2

    print(clean_message)
    return 0
