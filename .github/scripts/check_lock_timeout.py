#!/usr/bin/env python3
"""Every Terraform command that takes the state lock waits for it (KAN-017-A2).

`plan`, `apply` and `destroy` all acquire a lock on the state file. Without
`-lock-timeout` the default is zero: the second one does not wait, it fails
immediately with *Error acquiring the state lock*, and on the apply path that is
a run that stops between the backup and the plan for a reason that will be gone
by the time anyone looks.

Nothing in this repository had ever set it, and the reason nothing had noticed
is the interesting part. Two documents - `docs/release-process.md`'s gate table
and `docs/adr/0004-terraform-state.md` section 1 - credited the
`terraform-lab-state` concurrency group with keeping runs off each other's
state. That group holds `terraform-apply` and `terraform-destroy`. It has never
held `terraform-plan`, which plans against the same local backend on the same
runner.

So what actually keeps a plan out of an apply's way is that `gha-runner-01` is
one runner and runs one job at a time - a fact about the lab, not about anything
in this repository, and one that a second runner would end without a diff.
ADR 0004 section 6 already lists "the lab acquires a second runner" as a change
that would matter; this is one of the things it would change.

`-lock-timeout` is the half that does not depend on runner cardinality.

## The rule

A workflow line that *invokes* `terraform plan`, `terraform apply` or
`terraform destroy` must pass `-lock-timeout=`. Lines that merely mention one -
an `echo "::error::terraform plan exited $rc"`, a comment - are not invocations
and are ignored, which is why this matches on the command position rather than
on the substring.

## What is not covered

`terraform console`, `state list` and `output` read state without taking the
write lock, so they are not required to wait for one. `init -backend=false` in
`checks.yml` and in the fork-validate job never touches the state file at all -
that job exists precisely so a fork's pull request reaches no backend.

And the timeout is a wait, not a fix. A lock left behind by a killed run is
still stuck after it expires; `terraform force-unlock` and
`docs/state-recovery.md` are that path.

Usage: python3 check_lock_timeout.py [workflow-glob ...]
Exit codes: 0 every invocation waits, 1 at least one does not.
"""

import glob
import os
import re
import sys

LOCKING = ("plan", "apply", "destroy")

# A command position: the start of the line, or after a shell separator, with an
# optional `- run:`/`run:` prefix and an optional `timeout <n>` wrapper.
INVOCATION = re.compile(
    r"^(?:-\s*)?(?:run:\s*)?(?:timeout\s+\d+\s+)?terraform\s+(\w+)\b"
)


def invocations(text):
    """(line number, line) for every line that runs a locking subcommand."""
    found = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("#"):
            continue
        match = INVOCATION.match(line)
        if match and match.group(1) in LOCKING:
            found.append((number, line))
    return found


def main():
    patterns = sys.argv[1:] or [
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "workflows", "*.yml")
    ]

    paths = sorted({os.path.normpath(p)
                    for pattern in patterns
                    for p in glob.glob(pattern)})
    if not paths:
        print("::error::no workflow files matched %s. Finding nothing to check "
              "is not the same as finding everything correct." % patterns,
              file=sys.stderr)
        return 1

    findings = []
    checked = 0

    for path in paths:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        name = os.path.basename(path)
        for number, line in invocations(text):
            checked += 1
            if "-lock-timeout=" not in line:
                findings.append(
                    "%s:%d: `%s` takes the state lock and does not wait for "
                    "it. Add -lock-timeout=; without it a second run fails "
                    "instantly instead of queueing." % (name, number, line)
                )

    print("Checked %d locking Terraform invocation(s) across %d workflow(s)."
          % (checked, len(paths)))

    if not checked:
        print("::error::no `terraform plan|apply|destroy` invocation found at "
              "all. This check has stopped matching the workflows it guards.",
              file=sys.stderr)
        return 1

    if findings:
        print()
        for finding in findings:
            print("::error::%s" % finding, file=sys.stderr)
        return 1

    print("Every one of them waits for the lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
