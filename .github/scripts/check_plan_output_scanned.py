#!/usr/bin/env python3
"""A plan document is scanned for credentials before a workflow prints it.

`terraform plan` output renders every input the configuration reads. SEC-003
marks the rendered snippet `sensitive()` so it prints as `(sensitive value)`,
and BUG-021 then found that a mark is not a guard: Terraform re-spells quotes,
backslashes and newlines on the way into plan output, and the leak check that
only looked for the literal value did not recognise them. `assert_no_secrets.py`
exists because of that - it checks what the text actually says.

terraform-plan.yml has always used it. It captures its plan to a file, scans the
file, and prints it only once the scan comes back clean.

terraform-apply.yml did not. It ran `terraform plan -out=tfplan` and let the
output go straight to a job log that anyone with read access to the repository
can open, and nothing looked at what was in it. So the protection covered the
pull-request path and not the path that runs on `main`. The convergence check
added later printed a second unscanned plan on its failure path - the one path
where a reader most wants to read it.

Neither was a decision. Both are what happens when a rule lives in one file.

## The rule

If a workflow redirects `terraform plan` or `terraform show` output into a file,
and later prints that file, then `assert_no_secrets.py` must be run against the
same file first.

"First" is textual order within the workflow. Steps run top to bottom, so a scan
written above a print runs above it. That is coarser than a real dependency
graph and it is the right coarseness: it catches the whole failure mode - print
without scan - without needing to interpret `if:` conditions.

## What this does not check

**That the scan's SECRET_VARS names every credential.** A scan configured with
an empty list passes here and finds nothing. That list is checked by
test_assert_no_secrets.py and by reading it; this guard is about whether a scan
happens at all, which is the part that was silently absent.

**Binary plan files.** `-out=tfplan` writes a file no `cat` will render usefully
and no reader will paste anywhere. Only redirected text is in scope.

Usage: python3 check_plan_output_scanned.py [workflow-dir]
Exit codes: 0 every printed plan is scanned first, 1 at least one is not.
"""

import glob
import os
import re
import sys

# `terraform plan ... > FILE` / `terraform show ... > FILE`, on one line. The
# excluded '|' and '>' keep this from running past a pipe or matching '>>'.
#
# Anchored to the start of a line, because the first version of this was not
# and reported four defects that were all prose: these workflows echo the
# words `terraform plan` into their own error messages, and one names a step
# `Fail the job if terraform plan failed`. A real invocation begins a line in
# a `run:` block, or follows `run:` on one line - which is how the original
# defect was written, so leaving that form out would have made this guard
# blind to the thing it exists for. One invocation written after a `;` or a
# `&&` on the same line is still missed - that is the trade for not reporting
# sentences as findings.
PLAN_WRITE = re.compile(
    r"^\s*(?:run:\s*)?terraform\s+(?:plan|show)\b[^\n|>]*>\s*([A-Za-z0-9_./-]+)", re.M
)

# The same commands with nothing capturing them. An unredirected `terraform
# plan` prints to the job log by construction, so there is no file for the
# rule above to be about - which is exactly how the original defect hid from
# the first version of this guard. The absence of a file *is* the finding.
PLAN_RUN = re.compile(r"^\s*(?:run:\s*)?terraform\s+(?:plan|show)\b[^\n]*", re.M)
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, "..", "workflows")


def prints_of(text: str, name: str) -> list:
    """Offsets where this workflow renders the named file into its log."""
    found = []
    for verb in ("cat ", "type "):
        start = 0
        while True:
            i = text.find(verb + name, start)
            if i == -1:
                break
            found.append(i)
            start = i + 1
    return found


def first_scan_of(text: str, name: str) -> int:
    """Offset of the first assert_no_secrets run against this file, or -1."""
    return text.find("assert_no_secrets.py " + name)


def strip_comments(text: str) -> str:
    """Blank out comment lines, keeping every offset and line number intact.

    These workflows explain themselves at length, and several of those comments
    discuss `terraform plan` in prose. Matching one would report a defect on a
    sentence. Replacing rather than removing keeps the reported line numbers
    pointing at the real file.
    """
    out = []
    for line in text.split("\n"):
        out.append("" if line.lstrip().startswith("#") else line)
    return "\n".join(out)


def check(path: str) -> list:
    with open(path, "r", encoding="utf-8") as handle:
        text = strip_comments(handle.read())

    rel = os.path.basename(path)
    problems = []

    for name in sorted(set(PLAN_WRITE.findall(text))):
        printed = prints_of(text, name)
        if not printed:
            continue

        scanned = first_scan_of(text, name)
        line_of = lambda off: text.count("\n", 0, off) + 1

        if scanned == -1:
            problems.append(
                f"{rel}:{line_of(printed[0])}: '{name}' holds terraform plan "
                "output and is printed to the job log, and nothing scans it. "
                f"Run assert_no_secrets.py against {name} before printing it."
            )
            continue

        late = [off for off in printed if off < scanned]
        for off in late:
            problems.append(
                f"{rel}:{line_of(off)}: '{name}' is printed here, above the "
                f"scan on line {line_of(scanned)}. Steps run in order, so this "
                "one prints an unscanned plan."
            )

    for match in PLAN_RUN.finditer(text):
        if ">" in match.group(0):
            continue
        line = text.count("\n", 0, match.start()) + 1
        problems.append(
            f"{rel}:{line}: this plan is not captured, so its output goes "
            "straight to the job log and no scan can run against it. Redirect "
            "it to a file, scan the file, and print it after."
        )

    return problems


def main() -> int:
    where = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    files = sorted(glob.glob(os.path.join(where, "*.yml"))
                   + glob.glob(os.path.join(where, "*.yaml")))

    if not files:
        print(
            f"::error::no workflows found in {where}. This check passing on an "
            "empty set would be worse than useless.",
            file=sys.stderr,
        )
        return 1

    problems = []
    for path in files:
        problems.extend(check(path))

    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)

    if problems:
        print(
            f"\n{len(problems)} unscanned plan print(s) across "
            f"{len(files)} workflow(s).",
            file=sys.stderr,
        )
        return 1

    print(
        f"Every printed plan document is scanned first, across "
        f"{len(files)} workflow(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
