#!/usr/bin/env python3
"""Tests for check_managed_inventory.py.

The case that carries this suite is `commented_out_is_not_declared`. It is the
one that happened: `/*` and `*/` around one entry of `local.vms`, an apply that
destroyed the guest, and a README that went on saying the factory managed it.
Everything else here is a variation on that failing in the other direction.

`nested_block_is_not_a_vm` is the defect this parser would most plausibly have.
A VM entry and the `network = {` inside it look identical to a regex; counting
the inner one would report a VM nobody declared and fail every honest README.

The fixtures are minimal and are not this repository, so the suite keeps saying
the same thing when the real inventory changes - which it does routinely, and
which is the reason the check exists.

Usage: python3 test_check_managed_inventory.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_managed_inventory.py")

LOCALS_ONE = """locals {
  vms = {

    /*ubuntu-static-01 = {
      os = "linux"
    }*/

    ubuntu-dhcp-01 = {
      os        = "linux"
      cores     = 2
      network = {
        type = "dhcp"
      }
    }

  }
}
"""

LOCALS_NONE = LOCALS_ONE.replace(
    "    ubuntu-dhcp-01 = {", "    /*ubuntu-dhcp-01 = {"
).replace("    }\n\n  }\n}", "    }*/\n\n  }\n}")

README_ONE = """# Lab

**Terraform manages one of three VMs.**

| VMID | Name | Managed | Refused | Note |
|---|---|---|---|---|
| 101 | `ubuntu-dhcp-01` | **yes** | no | built by the factory |
| 1103 | `dns-01` | no | **protected** | every first boot waits on it |
| 1110 | `gha-runner-01` | no | **protected** | the runner |
"""

README_NONE = """# Lab

**Terraform manages none of the two VMs.**

| VMID | Name | Managed | Refused | Note |
|---|---|---|---|---|
| 1103 | `dns-01` | no | **protected** | every first boot waits on it |
| 1110 | `gha-runner-01` | no | **protected** | the runner |
"""


def run(locals_tf, readme):
    with tempfile.TemporaryDirectory() as root:
        for name, body in (("locals.tf", locals_tf), ("README.md", readme)):
            with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                handle.write(body)
        done = subprocess.run(
            [sys.executable, SCRIPT, root],
            capture_output=True, text=True,
        )
        return done.returncode, done.stdout + done.stderr


FAILURES = []


def case(name, code, output, expected, must_say=None):
    if code != expected:
        FAILURES.append(
            "%s: expected exit %d, got %d\n%s" % (name, expected, code, output)
        )
        return
    if must_say and must_say not in output:
        FAILURES.append("%s: output does not mention %r\n%s" % (name, must_say, output))
        return
    print("ok   %s" % name)


# They agree, with one VM declared and marked.
code, out = run(LOCALS_ONE, README_ONE)
case("agreement", code, out, 0)

# They agree, with none declared and none marked - which is the state this
# repository is in, and the state the old count sentence could not express.
code, out = run(LOCALS_NONE, README_NONE)
case("agreement_when_empty", code, out, 0)

# What happened on 2026-08-30: the entry is commented out, the README is not.
code, out = run(LOCALS_NONE, README_ONE)
case("commented_out_is_not_declared", code, out, 1, "ubuntu-dhcp-01")

# The other direction: the factory builds a VM the table does not admit to.
code, out = run(LOCALS_ONE, README_NONE)
case("declared_but_not_in_the_table", code, out, 1, "ubuntu-dhcp-01")

# The row is there and says no. A table can be wrong about the column as well
# as about the row.
code, out = run(LOCALS_ONE, README_ONE.replace("| **yes** |", "| no |"))
case("row_present_but_says_no", code, out, 1, "does not admit to")

# The sentence and the table disagree about the number of rows.
code, out = run(LOCALS_ONE, README_ONE.replace("one of three", "one of nine"))
case("total_count_wrong", code, out, 1, "nine")

# The sentence and the configuration disagree about the number managed.
code, out = run(LOCALS_ONE, README_ONE.replace("one of three", "two of three"))
case("managed_count_wrong", code, out, 1, "two")

# A number word nothing can read is not an agreement.
code, out = run(LOCALS_ONE, README_ONE.replace("one of three", "several of three"))
case("unreadable_number_word", code, out, 1, "several")

# `network = {` inside a VM is not a VM.
code, out = run(LOCALS_ONE, README_ONE)
case("nested_block_is_not_a_vm", code, out, 0, "declares 1")

# Neither side missing is ever reported as agreement.
code, out = run("locals {\n}\n", README_NONE)
case("no_vms_block_is_undecidable", code, out, 2, "not the same as finding it empty")

code, out = run(LOCALS_NONE, "# Lab\n\nNo table here.\n")
case("no_table_is_undecidable", code, out, 2, "cannot confirm a table")

code, out = run(LOCALS_NONE, README_NONE.replace(
    "**Terraform manages none of the two VMs.**", "It manages nothing."))
case("no_count_sentence", code, out, 1, "has to be checkable")

if FAILURES:
    print()
    for failure in FAILURES:
        print("FAIL %s" % failure, file=sys.stderr)
    raise SystemExit(1)

print("\nall cases pass")
