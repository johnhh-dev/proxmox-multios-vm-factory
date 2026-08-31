#!/usr/bin/env python3
"""Tests for backup_state.py (FEAT-001-A3).

The property that matters is not "a file appeared". It is that the file is a
*usable* backup: byte-identical to what it copied, readable only by the runner
account, and still present after the pruner has run. docs/state-recovery.md's
drill restores from one of these, and a truncated copy would restore cleanly and
then propose to rebuild the lab.

Usage: python3 test_backup_state.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "backup_state.py")

STATE = '{"version": 4, "resources": [{"name": "vm_a"}]}\n'


def run(state_file: str, backup_dir: str, keep: int = 20, label: str = "run-1"):
    return subprocess.run(
        [
            sys.executable,
            SCRIPT,
            "--state-file", state_file,
            "--backup-dir", backup_dir,
            "--keep", str(keep),
            "--label", label,
        ],
        capture_output=True,
        text=True,
    )


def backups(backup_dir: str) -> list[str]:
    if not os.path.isdir(backup_dir):
        return []
    return sorted(n for n in os.listdir(backup_dir) if n.startswith("terraform.tfstate."))


def check(name: str, condition: bool, detail: str, failures: list[str]) -> None:
    if condition:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: {detail}")
        failures.append(name)


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main() -> int:
    failures: list[str] = []

    # An absent state file is the first apply. It must not fail the run - the
    # inventory guard is what decides whether an empty state may proceed, and a
    # backup step that blocked bootstrap would be making that decision for it.
    with tempfile.TemporaryDirectory() as tmp:
        result = run(os.path.join(tmp, "absent.tfstate"), os.path.join(tmp, "backups"))
        check(
            "no state file is a no-op, not a failure",
            result.returncode == 0 and "nothing to back up" in result.stdout,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )

    # An empty state file is a different answer, and is also not a failure.
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "empty.tfstate")
        open(state, "w").close()
        bdir = os.path.join(tmp, "backups")
        result = run(state, bdir)
        check(
            "an empty state file is skipped rather than copied",
            result.returncode == 0 and backups(bdir) == [],
            f"rc={result.returncode} backups={backups(bdir)}",
            failures,
        )

    # The whole point: byte-identical, and verified as such by the script rather
    # than assumed by this test.
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "terraform.tfstate")
        write(state, STATE)
        bdir = os.path.join(tmp, "backups")
        result = run(state, bdir)
        names = backups(bdir)
        identical = False
        if len(names) == 1:
            with open(os.path.join(bdir, names[0]), encoding="utf-8") as handle:
                identical = handle.read() == STATE
        check(
            "the backup is byte-identical to the state it copied",
            result.returncode == 0 and identical,
            f"rc={result.returncode} names={names} err={result.stderr!r}",
            failures,
        )
        check(
            "the run reports the sha256 it verified",
            "sha256 " in result.stdout,
            f"out={result.stdout!r}",
            failures,
        )

        # The state file holds cleartext credentials for its whole history
        # (SEC-001e). A world-readable copy of it is a new finding, not a
        # backup. Skipped on Windows, where these bits do not mean this.
        if os.name != "nt":
            mode = os.stat(os.path.join(bdir, names[0])).st_mode & 0o777
            check("the backup is 0600", mode == 0o600, f"mode={oct(mode)}", failures)
            dmode = os.stat(bdir).st_mode & 0o777
            check(
                "the backup directory is 0700",
                dmode == 0o700,
                f"mode={oct(dmode)}",
                failures,
            )
        else:
            print("skip the two permission cases - not meaningful on this platform")

    # Retention. Names carry a fixed-width UTC timestamp, so lexical order is
    # chronological order; the pruner must keep the newest and drop the rest.
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "terraform.tfstate")
        write(state, STATE)
        bdir = os.path.join(tmp, "backups")
        os.makedirs(bdir)
        for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            write(os.path.join(bdir, f"terraform.tfstate.{stamp}.old"), "older")

        result = run(state, bdir, keep=2, label="run-9")
        names = backups(bdir)
        check(
            "pruning keeps exactly --keep backups",
            result.returncode == 0 and len(names) == 2,
            f"rc={result.returncode} names={names}",
            failures,
        )
        check(
            "pruning never removes the backup just taken",
            any(n.endswith(".run-9") for n in names),
            f"names={names}",
            failures,
        )
        check(
            "pruning removes the oldest first",
            "terraform.tfstate.20260101T000000Z.old" not in names,
            f"names={names}",
            failures,
        )
        check(
            "each deletion is reported",
            result.stdout.count("Pruned ") == 2,
            f"out={result.stdout!r}",
            failures,
        )

    # A label reaches a filename and comes from the workflow environment, so it
    # must not be able to name a path.
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "terraform.tfstate")
        write(state, STATE)
        bdir = os.path.join(tmp, "backups")
        result = run(state, bdir, label="../../etc/passwd")
        names = backups(bdir)
        # A separator is the property, not the literal `..`. The sanitiser turns
        # `../../etc/passwd` into `..-..-etc-passwd`, which reads alarmingly and
        # is an ordinary filename: with no separator in it there is no traversal
        # left, and os.path.join can only produce a path inside bdir.
        inside = False
        if len(names) == 1:
            separators = "/" in names[0] or "\\" in names[0]
            resolved = os.path.realpath(os.path.join(bdir, names[0]))
            inside = not separators and os.path.dirname(resolved) == os.path.realpath(bdir)
        check(
            "a label cannot escape the backup directory",
            result.returncode == 0 and inside,
            f"rc={result.returncode} names={names}",
            failures,
        )

    # An unusable destination must stop the run. A backup step that failed
    # quietly would leave the runbook restoring from nothing.
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "terraform.tfstate")
        write(state, STATE)
        blocker = os.path.join(tmp, "backups")
        write(blocker, "not a directory")
        result = run(state, blocker)
        check(
            "an unusable backup directory fails the step",
            result.returncode == 1 and "::error::" in result.stderr,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    # --keep 0 would mean "retain nothing", which is a backup step that deletes
    # its own output.
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "terraform.tfstate")
        write(state, STATE)
        result = run(state, os.path.join(tmp, "backups"), keep=0)
        check(
            "--keep below 1 is refused",
            result.returncode == 1,
            f"rc={result.returncode}",
            failures,
        )

    if failures:
        print(f"\n{len(failures)} case(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll cases pass: the backup is identical, private, retained and pruned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
