#!/usr/bin/env python3
"""The README's inventory table says what `local.vms` says.

DOC-001 (#59) is the finding that the README described five running VMs while
`local.vms` was empty. #177 replaced that table with one captured from the node,
and #210 rewrote the prose around it. Both were correct when written.

Then commit `9bf6c57` - two characters, `/*` and `*/` around one entry - emptied
`local.vms` again, the apply on that push destroyed VM 101, and every sentence
in the README about what this factory manages became false in the same minute.
Nothing went red. The `checks` workflow runs twenty-two suites and not one of
them read the README.

That is `check_protected_ids.py`'s story with a different column: a copy that
misleads a person breaks no tool, so nothing notices. The list that check guards
changes when someone edits `variables.tf`; this one changes when someone
comments out a VM, which is a thing the lab owner does routinely.

## What is checked

**The managed set.** Every uncommented key of `local.vms` must be a row in the
README inventory table whose *Managed* cell says yes, and every row that says
yes must be an uncommented key. Those are two different failures - the first is
a VM the factory builds and the README does not mention, the second is what
happened here.

**The count sentence.** `**Terraform manages <n> of <m> VMs.**` above the table,
where `<n>` is how many keys `local.vms` has and `<m>` is how many rows the
table has. Written as number words because that is how the README reads; a
sentence nobody can check is how "the current VM inventory shown in Proxmox"
survived above an empty inventory for months.

## What is not checked

**That the table matches the node.** It cannot be: this runs on `ubuntu-latest`
with no route to Proxmox, and the node's side of the comparison is
`reconcile_inventory.py`, run by a person on the runner. This checks that the
README agrees with the *configuration*, which is the half that can go wrong in a
pull request.

**Whether a VM should be managed.** That judgement is DOC-001's, argued per
guest in `docs/unmanaged-vms.md`.

## Parsing HCL with a regex

`inventory_guard.py` refuses to, and says why: reading `locals.tf` as text means
a commented-out declaration can satisfy a guard. It has `terraform console` on
the runner and uses it. This check runs where there is no state and no backend,
so it strips `/* */` and `#` comments first and then reads the `vms = {` block
by brace depth. The commented-out case is therefore the one case it is built to
get right, because it is the case that caused this.

Usage: python3 check_managed_inventory.py [repo-root]
Exit codes: 0 they agree, 1 they do not, 2 neither side could be read - which is
never reported as agreement.
"""

import os
import re
import sys

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"(?m)(^|\s)(#|//).*$")
VMS_OPEN = re.compile(r"(?m)^\s*vms\s*=\s*\{")
ENTRY = re.compile(r"^\s*\"?([A-Za-z0-9_][A-Za-z0-9_.-]*)\"?\s*=\s*\{")
TABLE_HEADER = re.compile(r"^\|\s*VMID\s*\|\s*Name\s*\|\s*Managed\s*\|")
TABLE_ROW = re.compile(r"^\|([^|]*)\|([^|]*)\|([^|]*)\|")
COUNT_SENTENCE = re.compile(
    r"\*\*Terraform manages ([A-Za-z]+) of (?:the )?([A-Za-z]+) VMs?\.\*\*"
)

# "none" is here because zero is a number the README has to be able to say, and
# has had to say since 2026-08-30.
NUMBER_WORDS = {
    "none": 0, "no": 0, "zero": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
WORD_FOR = {
    0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
    12: "twelve",
}


class Undecidable(Exception):
    """One of the two sides could not be established.

    Always fatal, for inventory_guard.py's reason: a check that cannot read the
    inventory has not concluded that the inventory is empty.
    """


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def declared_vms(locals_tf):
    """The uncommented keys of `local.vms`, in file order.

    Comments go first so that a `/*` wrapped around an entry cannot be read as a
    declaration - which is the entire point of this function.
    """
    text = LINE_COMMENT.sub(lambda m: m.group(1), BLOCK_COMMENT.sub("", locals_tf))

    opening = VMS_OPEN.search(text)
    if opening is None:
        raise Undecidable(
            "no `vms = {` block in locals.tf. Not finding the inventory is not "
            "the same as finding it empty."
        )

    depth = 0
    start = opening.end() - 1
    end = None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        raise Undecidable("the `vms = {` block in locals.tf is not closed.")

    body = text[start + 1:end]

    # Only entries at the top level of the block: a `network = {` nested inside
    # a VM is not a VM.
    names = []
    depth = 0
    for line in body.splitlines():
        if depth == 0:
            match = ENTRY.match(line)
            if match:
                names.append(match.group(1))
        depth += line.count("{") - line.count("}")
    return names


def table(readme):
    """The inventory table's rows as (vmid, name, managed-cell) triples."""
    header = None
    lines = readme.splitlines()
    for i, line in enumerate(lines):
        if TABLE_HEADER.match(line):
            header = i
            break
    if header is None:
        raise Undecidable(
            "no `| VMID | Name | Managed |` table in README.md. The check "
            "cannot confirm a table it cannot find."
        )

    rows = []
    for line in lines[header + 2:]:
        if not line.startswith("|"):
            break
        cells = TABLE_ROW.match(line)
        if cells:
            rows.append(tuple(c.strip() for c in cells.groups()))
    if not rows:
        raise Undecidable("the README inventory table has no rows.")
    return rows


def says_yes(cell):
    return cell.replace("*", "").strip().lower() == "yes"


def name_of(cell):
    return cell.replace("`", "").strip()


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    )

    try:
        readme = read(os.path.join(root, "README.md"))
        declared = declared_vms(read(os.path.join(root, "locals.tf")))
        rows = table(readme)
    except Undecidable as exc:
        print("::error::%s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("::error::cannot read %s (%s)." % (exc.filename, exc.strerror),
              file=sys.stderr)
        return 2

    findings = []

    declared_set = set(declared)
    marked = {name_of(name) for _, name, managed in rows if says_yes(managed)}

    for missing in sorted(declared_set - marked):
        findings.append(
            "README.md: `local.vms` declares '%s' and no row of the inventory "
            "table marks it managed. The factory builds a VM the table does "
            "not admit to." % missing
        )
    for extra in sorted(marked - declared_set):
        findings.append(
            "README.md: the table marks '%s' as managed and `local.vms` does "
            "not declare it. That is DOC-001 (#59) exactly - and after an "
            "apply, that VM no longer exists." % extra
        )

    sentence = COUNT_SENTENCE.search(readme)
    if sentence is None:
        findings.append(
            'README.md: no "**Terraform manages <n> of <m> VMs.**" sentence '
            "above the table. It is the one line a reader takes for the "
            "summary, so it is the one line that has to be checkable."
        )
    else:
        for word, actual, what in (
            (sentence.group(1), len(declared), "VMs `local.vms` declares"),
            (sentence.group(2), len(rows), "rows in the inventory table"),
        ):
            counted = NUMBER_WORDS.get(word.lower())
            if counted is None:
                findings.append(
                    "README.md: '%s' in the count sentence is not a number "
                    "word this can check." % word
                )
            elif counted != actual:
                findings.append(
                    "README.md: the count sentence says '%s' where there are "
                    "%d %s (%s)." % (word, actual, what,
                                     WORD_FOR.get(actual, actual))
                )

    print("local.vms declares %d: %s"
          % (len(declared), ", ".join(declared) or "(nothing)"))
    print("README.md's inventory table has %d row(s), %d marked managed."
          % (len(rows), len(marked)))

    if findings:
        print()
        for finding in findings:
            print("::error::%s" % finding, file=sys.stderr)
        print("\n%d disagreement(s)." % len(findings), file=sys.stderr)
        return 1

    print("The configuration and the README describe the same inventory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
