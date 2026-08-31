#!/usr/bin/env python3
"""Tests for check_plan_output_scanned.py.

The fixtures below are the two shapes the defect actually took in this
repository, plus the four shapes that made the first version of the guard report
prose as a finding. Both halves matter: a guard that misses the defect is
useless, and a guard that cries wolf gets deleted.
"""

import os
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "check_plan_output_scanned.py")


def run(workflow_body: str):
    """Run the guard over a directory holding one workflow. Returns (rc, err)."""
    with tempfile.TemporaryDirectory() as where:
        path = os.path.join(where, "w.yml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(textwrap.dedent(workflow_body))
        proc = subprocess.run(
            [sys.executable, GUARD, where],
            capture_output=True, text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr


FAILURES = []


def expect(name: str, condition: bool, detail: str = ""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def main() -> int:
    print("the two shapes the defect took here")

    # terraform-apply.yml before the fix: the plan was never captured at all.
    rc, out = run("""
        jobs:
          apply:
            steps:
              - name: Terraform plan
                run: terraform plan -out=tfplan
    """)
    expect("an uncaptured inline plan is a finding", rc == 1)
    expect("...and says the output is not captured", "not captured" in out)

    # The convergence check: captured, printed on the failure path, unscanned.
    rc, out = run("""
        jobs:
          apply:
            steps:
              - run: |
                  terraform plan -detailed-exitcode > post-apply-plan.txt
                  cat post-apply-plan.txt
    """)
    expect("a captured plan printed unscanned is a finding", rc == 1)
    expect("...and names the file", "post-apply-plan.txt" in out)

    print("the shape that is correct")

    rc, out = run("""
        jobs:
          plan:
            steps:
              - run: |
                  terraform plan -no-color > plan.txt 2>&1
              - run: python3 .github/scripts/assert_no_secrets.py plan.txt
              - run: cat plan.txt
    """)
    expect("capture, scan, then print passes", rc == 0, out)

    print("order matters, because steps run in order")

    rc, out = run("""
        jobs:
          plan:
            steps:
              - run: |
                  terraform plan -no-color > plan.txt 2>&1
              - run: cat plan.txt
              - run: python3 .github/scripts/assert_no_secrets.py plan.txt
    """)
    expect("printing above the scan is a finding", rc == 1)
    expect("...and says so", "above the scan" in out)

    print("prose is not an invocation")

    # Every one of these was reported as a defect by the first version.
    rc, out = run("""
        jobs:
          plan:
            steps:
              # This comment discusses terraform plan at length.
              - name: Fail the job if terraform plan failed
                run: |
                  echo "::error::terraform plan exited $PLAN_RC"
                  echo "terraform plan output withheld pending the scan."
    """)
    expect("echoes, step names and comments are not findings", rc == 0, out)

    print("an empty run is not a pass")

    with tempfile.TemporaryDirectory() as empty:
        proc = subprocess.run(
            [sys.executable, GUARD, empty], capture_output=True, text=True
        )
        expect("no workflows found is a failure", proc.returncode == 1)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
