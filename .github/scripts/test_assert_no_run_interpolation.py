#!/usr/bin/env python3
"""Canary test for assert_no_run_interpolation.py.

The guard is only worth having if it fires on the defect it was written for, so
CI runs this first. The leaky fixture is the SEC-005 defect verbatim: the
confirmation gate as it stood in terraform-destroy.yml before the fix.

Usage: python3 test_assert_no_run_interpolation.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "assert_no_run_interpolation.py")

# The defect, as it was.
LEAKY = """name: t
on:
  workflow_dispatch:
    inputs:
      confirm:
        required: true
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: Confirm destroy
        run: |
          set -euo pipefail
          if [ "${{ github.event.inputs.confirm }}" != "DESTROY" ]; then
            exit 1
          fi
"""

# The fix.
CLEAN = """name: t
on:
  workflow_dispatch:
    inputs:
      confirm:
        required: true
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: Confirm destroy
        env:
          CONFIRM: ${{ inputs.confirm }}
        run: |
          set -euo pipefail

          if [ "$CONFIRM" != "DESTROY" ]; then
            exit 1
          fi
      - name: After the block scalar ends
        uses: actions/checkout@v5
"""

# `inputs.*` rather than `github.event.inputs.*` - the form actionlint also
# misses, and the one a well-meaning rewrite is most likely to reach for.
LEAKY_SHORT_FORM = CLEAN.replace('if [ "$CONFIRM" !=', 'if [ "${{ inputs.confirm }}" !=')

# Single-line run:, no block scalar.
LEAKY_ONE_LINE = """name: t
on: push
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event.head_commit.message }}"
"""


def run(*paths: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, GUARD, *paths], capture_output=True, text=True)


def check(label: str, got: int, want: int, proc: subprocess.CompletedProcess) -> bool:
    if got == want:
        print(f"ok   - {label} (exit {got})")
        return True
    print(f"FAIL - {label}: expected exit {want}, got {got}")
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    return False


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        def write(name: str, body: str) -> str:
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        leaky = write("leaky.yml", LEAKY)
        clean = write("clean.yml", CLEAN)
        short = write("short.yml", LEAKY_SHORT_FORM)
        oneline = write("oneline.yml", LEAKY_ONE_LINE)

        proc = run(leaky)
        ok &= check("fires on the SEC-005 defect verbatim", proc.returncode, 1, proc)

        proc = run(clean)
        ok &= check("passes the fixed workflow", proc.returncode, 0, proc)

        proc = run(short)
        ok &= check("fires on the inputs.* short form", proc.returncode, 1, proc)

        proc = run(oneline)
        ok &= check("fires on a single-line run:", proc.returncode, 1, proc)

        # env: carries the expression legitimately - that is the fix, not a hit.
        if "::error" in run(clean).stdout:
            print("FAIL - flagged an expression in env:, which is the recommended fix")
            ok = False
        else:
            print("ok   - does not flag an expression in env:")

        proc = run(os.path.join(tmp, "does-not-exist-*.yml"))
        ok &= check("rejects a run that scanned nothing", proc.returncode, 2, proc)

    if not ok:
        print("::error::assert_no_run_interpolation.py does not behave as required")
        return 1
    print("all canary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
