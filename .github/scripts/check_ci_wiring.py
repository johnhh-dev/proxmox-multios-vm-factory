#!/usr/bin/env python3
"""Every test suite in this directory is actually run by CI.

There are fifteen `test_*.py` files here and `checks.yml` names all fifteen, one
step each. That is true today by discipline: nothing enforces it, and the
failure mode of forgetting is the quiet one — a suite that exists, passes
locally, and never runs on a pull request, so it goes green forever and stops
being evidence of anything.

The repository has already been bitten by the general shape of this. #157 was
merged with a red `checks` run because a suite behaved differently on CI than on
a workstation; a suite that runs *nowhere* is the same problem with the symptom
removed.

## What counts as wired

The file's basename appearing anywhere in `checks.yml`. That is deliberately
loose: matching the exact `run:` line would break the moment someone groups two
suites into one step or changes the invocation, and this check should notice a
missing suite rather than police how it is invoked.

## What this does not check

**That the step is in a job that runs.** A suite named inside a job with an `if:`
that is never true would pass here. Checking that means interpreting workflow
conditions, which is a job for `act` or for reading it, not for a twenty-line
guard.

Usage: python3 check_ci_wiring.py [scripts-dir] [workflow]
Exit codes: 0 every suite is named, 1 at least one is not.
"""

import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WORKFLOW = os.path.join(HERE, "..", "workflows", "checks.yml")


def reachable(name: str, haystacks: list) -> bool:
    """Is this script named anywhere, other than by its own test file?

    A plain substring test says yes for every script that has a suite, because
    `test_reconcile_inventory.py` contains `reconcile_inventory.py`. That is how
    an unreachable script passed a check written to find unreachable scripts -
    mine, on the first attempt.

    So a match is only a match when it is not immediately preceded by `test_`.
    """
    for text in haystacks:
        start = 0
        while True:
            i = text.find(name, start)
            if i == -1:
                break
            if not text[max(0, i - 5):i].endswith("test_"):
                return True
            start = i + 1
    return False


def is_runnable(path: str) -> bool:
    """Does this script have a command-line interface, or is it a library?

    The distinction decides what "reachable" has to mean. A library is reachable
    by being imported - hostile_values.py exists to be imported by two test
    suites and that is the whole of its job. A script with an argparse and a
    `__main__` is meant to be *run*, and a runnable thing nobody names anywhere
    is a thing nobody can find.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            body = handle.read()
    except OSError:
        return False
    if path.endswith(".sh"):
        return True
    return "__main__" in body and ("argparse" in body or "sys.argv" in body)


def check_scripts_have_callers(scripts_dir: str, haystacks: list) -> list:
    """Every runnable script is invoked or documented somewhere.

    The three-way inventory comparison tool was neither. #144 built it for
    DOC-001, checks.yml runs its suite, and nothing anywhere told a reader it
    existed - so the tool for the comparison DOC-001 is *about* was findable
    only by listing this directory. A passing test suite made it look fine.

    Named without its filename on purpose: this file is not allowed to be the
    thing that makes a script findable.

    Documented counts, because not everything here belongs in a workflow. That
    was written of both credential audits, on the grounds that supplying their
    inputs over SSH would put a credential on a command line.

    It is now true of one. audit_state_secrets.py runs in terraform-apply,
    where the reasoning does not apply: the credentials are already in the job
    environment and the state file is on the same machine, so nothing is typed.
    audit_node_snippets.py stays operator-run - its target is the node, which
    is the far side of an SSH connection this repository does not open.
    """
    unreachable = []
    for path in sorted(glob.glob(os.path.join(scripts_dir, "*.py"))
                       + glob.glob(os.path.join(scripts_dir, "*.sh"))):
        name = os.path.basename(path)
        if name.startswith("test_") or not is_runnable(path):
            continue
        if not reachable(name, haystacks):
            unreachable.append(name)
    return unreachable


def main() -> int:
    scripts_dir = sys.argv[1] if len(sys.argv) > 1 else HERE
    workflow = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_WORKFLOW

    try:
        with open(workflow, "r", encoding="utf-8") as handle:
            wiring = handle.read()
    except OSError as exc:
        print(
            f"::error::cannot read {workflow} ({exc.strerror}). "
            "Not finding the workflow is not the same as finding it complete.",
            file=sys.stderr,
        )
        return 1

    suites = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(scripts_dir, "test_*.py"))
    )
    if not suites:
        print(
            f"::error::no test_*.py found in {scripts_dir}. "
            "This check passing on an empty set would be worse than useless.",
            file=sys.stderr,
        )
        return 1

    missing = [s for s in suites if s not in wiring]
    for name in missing:
        print(
            f"::error::{name} is never run by CI. Add a step to "
            f"{os.path.basename(workflow)}, or delete the suite - a test that "
            "does not run is not evidence of anything.",
            file=sys.stderr,
        )

    if missing:
        print(
            f"\n{len(missing)} of {len(suites)} suite(s) are not wired.",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(suites)} test suite(s) are run by {os.path.basename(workflow)}.")

    # The other direction. A suite that never runs is one failure mode; a script
    # nobody can find is the other, and it is quieter - the tests pass, so
    # nothing looks wrong.
    repo = os.path.abspath(os.path.join(scripts_dir, "..", ".."))
    haystacks = []
    for pattern in ("workflows/*.yml", "actions/*/action.yml"):
        for path in glob.glob(os.path.join(scripts_dir, "..", pattern)):
            haystacks.append(open(path, encoding="utf-8").read())
    for pattern in ("*.md", "docs/*.md", "docs/adr/*.md"):
        for path in glob.glob(os.path.join(repo, pattern)):
            haystacks.append(open(path, encoding="utf-8").read())
    # Deliberately not the Python sources. Every script names itself in its own
    # usage docstring, so including them made each one reachable by existing -
    # and this file naming another script in a comment made *that* one reachable
    # forever. A runnable script has to be named where a reader would look.

    unreachable = check_scripts_have_callers(scripts_dir, haystacks)
    for name in unreachable:
        print(
            f"::error::{name} has a command line and is named in no workflow, "
            "action or document. Nobody can find it - either invoke it, write "
            "down how to run it, or delete it.",
            file=sys.stderr,
        )
    if unreachable:
        return 1

    print("Every runnable script is named in a workflow, an action or a document.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
