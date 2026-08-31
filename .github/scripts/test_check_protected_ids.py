#!/usr/bin/env python3
"""Tests for check_protected_ids.py.

Each case is one of the four places the list is written, made to disagree on its
own. The one that carries the issue is the README case: that is the copy #198
left behind, and it is the copy nothing else would have caught, because a wrong
row in a table breaks no tool - it misleads a person and then goes on looking
fine.

The fixture tree is deliberately minimal. It is not this repository, so the
suite keeps saying the same thing when the real list changes - which it will,
since deciding what belongs on it is the open half of DOC-001.

Usage: python3 test_check_protected_ids.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_protected_ids.py")

VARIABLES = """variable "protected_vm_ids" {
  type    = list(number)
  default = [1110, 1103]
}
"""

LOCALS = """  protected = [
    for k, v in local.vms : format(
      "VM '%s': vm_id %d is on the protected list. %s",
      k,
      v.vm_id,
      v.vm_id == 1110 ? "That is the runner." :
      v.vm_id == 1103 ? "That is dns-01." :
      "See var.protected_vm_ids."
    )
  ]
"""

README = """# Lab

| VMID | Name | Managed | Refused | Note |
|---|---|---|---|---|
| 101 | `ubuntu-dhcp-01` | yes | no | built by the factory |
| 1103 | `dns-01` | no | **protected** | every first boot waits on it |
| 1110 | `gha-runner-01` | no | **protected** | the runner |
"""

DOC = """# Unmanaged

## The two on `var.protected_vm_ids`

```bash
python3 reconcile_inventory.py --protected-vmids 1110,1103
```
"""


def build(tmp, variables=VARIABLES, locals_tf=LOCALS, readme=README, doc=DOC):
    root = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    for name, body in (
        ("variables.tf", variables),
        ("locals.tf", locals_tf),
        ("README.md", readme),
    ):
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write(body)
    with open(os.path.join(root, "docs", "unmanaged-vms.md"), "w",
              encoding="utf-8") as handle:
        handle.write(doc)
    return root


def run(root):
    return subprocess.run(
        [sys.executable, SCRIPT, root], capture_output=True, text=True
    )


def check(name, condition, detail, failures):
    if condition:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: {detail}")
        failures.append(name)


def main() -> int:
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        result = run(build(tmp))
        check(
            "a tree where all four agree passes",
            result.returncode == 0,
            f"exit {result.returncode}\n{result.stdout}{result.stderr}",
            failures,
        )

    # The #198 case, and the reason this script exists: the ID is added
    # everywhere a tool would notice, and the table a person reads is not.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(
            tmp,
            variables=VARIABLES.replace("[1110, 1103]", "[1110, 1103, 1105]"),
            locals_tf=LOCALS.replace(
                '      "See var.protected_vm_ids."',
                '      v.vm_id == 1105 ? "That is elastic-01." :\n'
                '      "See var.protected_vm_ids."',
            ),
            doc=DOC.replace("two on", "three on").replace("1110,1103", "1110,1103,1105"),
            readme=README + "| 1105 | `elastic-01` | no | no | referenced nowhere else |\n",
        )
        result = run(root)
        check(
            "a README row that does not say protected is a finding",
            result.returncode == 1,
            f"exit {result.returncode}\n{result.stdout}{result.stderr}",
            failures,
        )
        check(
            "and the finding names the VMID",
            "1105" in result.stderr,
            f"stderr was:\n{result.stderr}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, variables=VARIABLES.replace("[1110, 1103]", "[1110, 1103, 1104]"))
        result = run(root)
        check(
            "an ID with no locals.tf branch is a finding",
            result.returncode == 1 and "1104" in result.stderr,
            f"exit {result.returncode}\n{result.stderr}",
            failures,
        )
        check(
            "and it says the refusal would give no reason",
            "no reason" in result.stderr,
            f"stderr was:\n{result.stderr}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, doc=DOC.replace("1110,1103", "1110"))
        result = run(root)
        check(
            "a stale --protected-vmids is a finding",
            result.returncode == 1,
            f"exit {result.returncode}\n{result.stderr}",
            failures,
        )
        check(
            "and it says an omitted ID is reported as an orphan",
            "orphan" in result.stderr,
            f"stderr was:\n{result.stderr}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, doc=DOC.replace("The two on", "The three on"))
        result = run(root)
        check(
            "a count word that disagrees with the list is a finding",
            result.returncode == 1 and "counts 3" in result.stderr,
            f"exit {result.returncode}\n{result.stderr}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, locals_tf=LOCALS.replace("v.vm_id == 1103 ?", "v.vm_id == 1109 ?"))
        result = run(root)
        check(
            "a branch for an ID that is not on the list is a finding",
            result.returncode == 1 and "1109" in result.stderr,
            f"exit {result.returncode}\n{result.stderr}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # An unreadable list must never be reported as four copies agreeing -
        # the same rule the credential audits keep about an empty scan.
        root = build(tmp, variables='variable "something_else" {\n  default = []\n}\n')
        check(
            "no list is reported as unusable, not as agreement",
            run(root).returncode == 2,
            "a tree with no protected_vm_ids passed",
            failures,
        )

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
