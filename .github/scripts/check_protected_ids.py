#!/usr/bin/env python3
"""The protected VM IDs say the same thing in all four places they are written.

`var.protected_vm_ids` is the list this factory refuses to manage (OPS-003,
#171). It is not written once. It appears as:

    variables.tf      the default, which is the value
    locals.tf         one `v.vm_id == N ?` branch per ID, giving the reason a
                      refused plan prints
    README.md         the inventory table a reader looks at first
    docs/             any `--protected-vmids` argument written out in full

**The fourth of those is now usually empty, and that is the better outcome.**
reconcile_inventory.py reads `variables.tf` itself when the flag is omitted, so
the documented command no longer spells the list out - a copy that cannot drift
because it does not exist. The check is kept because a document may still write
the list out to illustrate something, and a stale illustration is how the
count in `unmanaged-vms.md`'s heading would go wrong.

Adding an ID means editing four files, and #198 edited three. The README row for
`elastic-01` still said *"referenced nowhere else in this repository"* after that
VMID had been added to the deny-list, given a bespoke refusal message and given
its own section in `unmanaged-vms.md`.

That is the failure #201 documented in three other documents at once, and its
own summary of why it happens: a count written as prose *"does not silently
become wrong again"* only if something checks it.

## What each check costs if it is missing

**A missing `locals.tf` branch** is the quiet one. The ternary falls through to
`"See var.protected_vm_ids."`, so the plan is still refused and the operator is
told nothing about which machine they nearly managed, or why.

**A stale `--protected-vmids`** is the loud one, in the wrong direction: the ID
omitted is reported as an `orphan`, and
[incident-orphan-vm.md](../../docs/incident-orphan-vm.md)'s recovery is
`qm destroy --purge`.

**A README row that does not say `protected`** is the one that misleads a person
rather than a tool, which is what happened.

## What this does not check

**That the list is right.** Whether a VM belongs on it is a judgement recorded
in `docs/unmanaged-vms.md`, and no script can hold an opinion about it. This
checks that the four copies agree, not that they agree about the correct thing.

Usage: python3 check_protected_ids.py [repo-root]
Exit codes: 0 they agree, 1 they do not, 2 the list itself could not be read -
which is never reported as agreement.
"""

import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import terraform_defaults  # noqa: E402

LOCALS_BRANCH = re.compile(r"v\.vm_id\s*==\s*(\d+)\s*\?")
# Digits required, so reconcile_inventory.py's own argparse declaration of the
# flag is not mistaken for a use of it.
DOC_ARGUMENT = re.compile(r"--protected-vmids\s+(\d+(?:\s*,\s*\d+)*)")
COUNT_PHRASE = re.compile(r"The\s+([A-Za-z]+)\s+on\s+`var\.protected_vm_ids`")
TABLE_ROW = re.compile(r"^\|\s*(\d+)\s*\|(.*)\|\s*$")

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def documents(root: str) -> list:
    found = glob.glob(os.path.join(root, "*.md"))
    found += glob.glob(os.path.join(root, "docs", "**", "*.md"), recursive=True)
    return sorted(set(os.path.normpath(p) for p in found))


def declared(root: str) -> set:
    """The value: var.protected_vm_ids' default in variables.tf.

    Parsed by terraform_defaults, which reconcile_inventory.py now reads the
    same list with. One parser, because a fix applied to one copy and not the
    other means this guard passing while the tool that acts on the list is
    wrong - and that tool's wrong answer is `orphan` beside a machine whose
    runbook says `qm destroy --purge`.
    """
    listed = terraform_defaults.list_default(
        read(os.path.join(root, "variables.tf")), "protected_vm_ids"
    )
    return set(listed or [])


def rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    )

    try:
        ids = declared(root)
    except OSError as exc:
        print(f"::error::cannot read variables.tf ({exc.strerror}).", file=sys.stderr)
        return 2

    if not ids:
        print(
            "::error::could not read var.protected_vm_ids' default from "
            "variables.tf. Not finding the list is not the same as finding "
            "every copy of it correct.",
            file=sys.stderr,
        )
        return 2

    findings = []
    expected = ", ".join(str(n) for n in sorted(ids))

    branches = set(int(n) for n in LOCALS_BRANCH.findall(read(os.path.join(root, "locals.tf"))))
    for missing in sorted(ids - branches):
        findings.append(
            f"locals.tf: vm_id {missing} is on var.protected_vm_ids and has no "
            "`v.vm_id == ...` branch, so a plan that declares it is refused "
            "with no reason given."
        )
    for extra in sorted(branches - ids):
        findings.append(
            f"locals.tf: a branch explains vm_id {extra}, which is not on "
            "var.protected_vm_ids. Nothing refuses that plan."
        )

    for doc in documents(root):
        body = read(doc)
        name = rel(root, doc)

        for argument in DOC_ARGUMENT.findall(body):
            used = {int(n) for n in re.findall(r"\d+", argument)}
            if used != ids:
                findings.append(
                    f"{name}: --protected-vmids {argument.strip()} does not "
                    f"match var.protected_vm_ids ({expected}). An ID left out "
                    "is reported as an orphan, and that runbook destroys."
                )

        for word in COUNT_PHRASE.findall(body):
            counted = NUMBER_WORDS.get(word.lower())
            if counted is None:
                findings.append(
                    f"{name}: \"The {word} on var.protected_vm_ids\" - not a "
                    "number this can check. Use a number word."
                )
            elif counted != len(ids):
                findings.append(
                    f"{name}: \"The {word} on var.protected_vm_ids\" counts "
                    f"{counted}; there are {len(ids)}."
                )

    readme = read(os.path.join(root, "README.md"))
    rows = {}
    for line in readme.splitlines():
        row = TABLE_ROW.match(line)
        if row:
            rows[int(row.group(1))] = row.group(2)

    for vmid in sorted(ids):
        if vmid not in rows:
            findings.append(
                f"README.md: vm_id {vmid} is on var.protected_vm_ids and is not "
                "a row in the inventory table."
            )
        elif "protected" not in rows[vmid].lower():
            findings.append(
                f"README.md: the row for vm_id {vmid} does not say it is "
                "protected. A reader takes the table at face value - that is "
                "how elastic-01 read as an ordinary unmanaged guest after #198."
            )

    print(f"var.protected_vm_ids is {expected}.")

    if findings:
        print()
        for finding in findings:
            print(f"::error::{finding}", file=sys.stderr)
        print(f"\n{len(findings)} place(s) disagree.", file=sys.stderr)
        return 1

    print("locals.tf, the documentation and the README inventory all agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
