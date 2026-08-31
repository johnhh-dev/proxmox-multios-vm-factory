#!/usr/bin/env python3
"""Fail if a workflow interpolates ${{ }} inside a `run:` script body.

This is the guard for SEC-005. The Actions expression engine substitutes ${{ }}
into the script *text* before the shell parses it, so a value containing a quote
closes the surrounding quoting and the rest runs as code. The fix is always the
same: pass the value through `env:` and read it as a variable.

actionlint does not catch this. Its `expression` rule fires only for a hardcoded
list of attacker-writable event fields - `github.event.issue.title` and friends -
so `inputs.*` and `github.event.inputs.*` pass it clean, and shellcheck never
sees the expression because actionlint replaces it with a placeholder before
handing the script over. Verified against actionlint 1.7.12: it reports nothing
on the workflow that carried SEC-005.

The rule here is deliberately blunt: no ${{ }} inside a run: body, whatever the
expression. A per-expression allowlist would need a judgement call at every new
site, which is how the original defect survived review.

Usage:
  python3 assert_no_run_interpolation.py .github/workflows/*.yml

Exit codes: 0 clean, 1 interpolation found, 2 usage error.
"""

import glob
import re
import sys

RUN_KEY = re.compile(r"^(\s*)-?\s*run:\s*(.*)$")
BLOCK_SCALAR = re.compile(r"^[|>][-+]?\d*$")


def findings(path: str) -> list[tuple[int, str]]:
    """Return (line number, line) for every ${{ }} inside a run: body."""
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().split("\n")

    hits: list[tuple[int, str]] = []
    body_indent: int | None = None

    for number, line in enumerate(lines, start=1):
        if body_indent is not None:
            # A blank line does not end a block scalar.
            if line.strip() == "":
                continue
            indent = len(line) - len(line.lstrip())
            if indent > body_indent:
                if "${{" in line:
                    hits.append((number, line.strip()))
                continue
            body_indent = None

        match = RUN_KEY.match(line)
        if not match:
            continue
        indent, value = len(match.group(1)), match.group(2).strip()
        if BLOCK_SCALAR.match(value):
            body_indent = indent
        elif "${{" in value:
            # Single-line `run: something ${{ ... }}` - same defect, one line.
            hits.append((number, line.strip()))

    return hits


def main() -> int:
    patterns = sys.argv[1:]
    if not patterns:
        print("usage: assert_no_run_interpolation.py <workflow.yml> [...]", file=sys.stderr)
        return 2

    paths: list[str] = []
    for pattern in patterns:
        paths.extend(sorted(glob.glob(pattern)))
    if not paths:
        print("::error::no workflow files matched - nothing was scanned", file=sys.stderr)
        return 2

    total = 0
    for path in paths:
        for number, text in findings(path):
            total += 1
            print(f"::error file={path},line={number}::expression interpolated into a run: body - "
                  f"pass it through env: and read it as a variable (SEC-005): {text}")

    print(f"scanned {len(paths)} workflow file(s)")
    if total:
        print(f"::error::{total} interpolation(s) inside run: bodies")
        return 1

    print("clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
