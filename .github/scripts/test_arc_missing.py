#!/usr/bin/env python3
"""Tests for arc_missing.py — the Arc poll's exit condition.

The first case is the defect this script was written for, and it is the one to
keep if the rest are ever trimmed: a resource group holding machines from
elsewhere must not satisfy the poll. Counting satisfied it; names do not.

Usage: python3 test_arc_missing.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "arc_missing.py")


def vm(arc=True, name="a-arc", ip="10.0.0.5"):
    return {
        "name": "a",
        "os": "linux",
        "cores": 2,
        "memory_mb": 4096,
        "ip": "dhcp",
        "ip_observed": ip,
        "arc_enabled": arc,
        "arc_resource_name": name,
    }


def run(inventory, present_names, write_present=True):
    with tempfile.TemporaryDirectory() as tmp:
        inv = os.path.join(tmp, "inv.json")
        with open(inv, "w", encoding="utf-8") as handle:
            json.dump(inventory, handle)

        present = os.path.join(tmp, "present.txt")
        if write_present:
            with open(present, "w", encoding="utf-8") as handle:
                handle.write("\n".join(present_names))
                if present_names:
                    handle.write("\n")

        return subprocess.run(
            [sys.executable, SCRIPT, "--inventory", inv, "--present", present],
            capture_output=True,
            text=True,
        )


def check(name, condition, detail, failures):
    if condition:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: {detail}")
        failures.append(name)


def main() -> int:
    failures = []

    # THE case. Three machines in the resource group, none of them ours. The
    # count comparison this replaced saw 3 >= 1 and stopped waiting.
    result = run({"new-vm": vm(name="new-vm")}, ["old-a", "old-b", "old-c"])
    check(
        "machines from elsewhere do not satisfy the poll",
        result.returncode == 0 and result.stdout.strip() == "1",
        f"rc={result.returncode} out={result.stdout!r}",
        failures,
    )

    result = run({"a": vm(name="a-arc")}, ["a-arc"])
    check(
        "the expected machine present means nothing is missing",
        result.stdout.strip() == "0",
        f"out={result.stdout!r}",
        failures,
    )

    result = run({"a": vm(name="a-arc"), "b": vm(name="b-arc")}, ["a-arc", "unrelated"])
    check(
        "only the expected names are counted, in both directions",
        result.stdout.strip() == "1",
        f"out={result.stdout!r}",
        failures,
    )

    # BUG-019: the machine is named for arc.resource_name, not for the VM.
    result = run({"a": vm(name="different-in-azure")}, ["different-in-azure"])
    check(
        "the Azure name is matched, not the VM name",
        result.stdout.strip() == "0",
        f"out={result.stdout!r}",
        failures,
    )
    result = run({"a": vm(name="different-in-azure")}, ["a"])
    check(
        "the VM name alone does not satisfy the poll",
        result.stdout.strip() == "1",
        f"out={result.stdout!r}",
        failures,
    )

    # A VM with Arc on but no resource name expects nothing - the check itself
    # applies the same rule, and a poll waiting for a machine nothing will
    # create would run to the deadline on every apply.
    result = run({"a": vm(name=None)}, [])
    check(
        "arc_enabled without a name expects no machine",
        result.stdout.strip() == "0",
        f"out={result.stdout!r}",
        failures,
    )

    result = run({"a": vm(arc=False, name=None)}, [])
    check(
        "a lab with no Arc VM never waits",
        result.stdout.strip() == "0",
        f"out={result.stdout!r}",
        failures,
    )

    result = run({"a": vm(name="a-arc")}, [])
    check(
        "an empty Azure listing means everything is missing",
        result.stdout.strip() == "1",
        f"out={result.stdout!r}",
        failures,
    )

    # An unreadable input must not print "0". That is the value that stops the
    # loop, so a failure here would look exactly like success.
    result = run({"a": vm()}, [], write_present=False)
    check(
        "an unreadable input exits 2 rather than printing 0",
        result.returncode == 2 and result.stdout.strip() != "0",
        f"rc={result.returncode} out={result.stdout!r}",
        failures,
    )

    if failures:
        print(f"\n{len(failures)} case(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll cases pass: the poll waits for names, not for a count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
