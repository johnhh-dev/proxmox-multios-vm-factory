#!/usr/bin/env python3
r"""Tests for check_guest_paths.py.

The case that carries the issue is the first one, and it is not hypothetical:
`docs/lab-access-required.md` held `C:\ProgramData<VT>m-factory-firstboot.done`
for as long as that document has existed. So the test fixture is the real bug,
byte for byte, built the way the bug was built - by letting Python expand `\v`.

The second case is the one that makes the guard worth having rather than
merely correct: the same path written properly must pass, or the guard is a
prohibition on documenting the marker at all.

Everything runs against a temporary tree rather than the repository, so the
suite says the same thing on a checkout where someone is mid-edit.

Usage: python3 test_check_guest_paths.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_guest_paths.py")

BACKSLASH = chr(92)
VT = chr(11)

MARKER = "C:" + BACKSLASH + "ProgramData" + BACKSLASH + "vm-factory-firstboot.done"
MANGLED = "C:" + BACKSLASH + "ProgramData" + VT + "m-factory-firstboot.done"
LOG = "C:" + BACKSLASH + "cloudbase-firstboot-test.log"

# Stands in for cloudinit/windows.yaml.tftpl: the guest document that creates
# the files, and therefore the only thing that makes a path in a document real.
GUEST_DOC = f"""#ps1_sysnative
$LogPath = "{LOG}"
$RunOnceMarker = "{MARKER}"
"""


def build(tmp: str, doc_body: str, guest: str = GUEST_DOC) -> str:
    root = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(root, "cloudinit"), exist_ok=True)
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    with open(os.path.join(root, "cloudinit", "windows.yaml.tftpl"), "w",
              encoding="utf-8") as handle:
        handle.write(guest)
    with open(os.path.join(root, "docs", "runbook.md"), "w",
              encoding="utf-8") as handle:
        handle.write(doc_body)
    return root


def run(root: str):
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
        root = build(tmp, f"Run:\n\n```powershell\nTest-Path {MANGLED}\n```\n")
        result = run(root)
        check(
            "an expanded escape in a documented path is a finding",
            result.returncode == 1,
            f"exit {result.returncode}\n{result.stdout}{result.stderr}",
            failures,
        )
        check(
            "and the message names the code point rather than showing nothing",
            "U+000B" in result.stderr,
            f"stderr was:\n{result.stderr}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, f"Run:\n\n```powershell\nTest-Path {MARKER}\n```\n")
        check(
            "the same path written correctly passes",
            run(root).returncode == 0,
            "the guard would forbid documenting the marker at all",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, f"See `{LOG}`, and `{LOG}`.\n")
        check(
            "trailing markdown punctuation is not part of the path",
            run(root).returncode == 0,
            "a backtick or a full stop was read as a path character",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        bad = "C:" + BACKSLASH + "Windows" + BACKSLASH + "Temp" + BACKSLASH + "invented.log"
        root = build(tmp, f"Read `{bad}` afterwards.\n")
        result = run(root)
        check(
            "a path no guest document creates is a finding",
            result.returncode == 1,
            f"exit {result.returncode}",
            failures,
        )
        check(
            "and the finding names the document it is in",
            "docs/runbook.md" in result.stderr,
            f"stderr was:\n{result.stderr}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # A guest document naming no Windows path at all leaves the guard with
        # no source of truth. Reporting that tree clean would be the same error
        # as an empty scan reporting a clean state file.
        root = build(tmp, f"Test-Path {MARKER}\n", guest="#cloud-config\n")
        check(
            "no source of truth is reported as unusable, not as clean",
            run(root).returncode == 2,
            "an empty cloudinit/ passed every document",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # NOT_OURS. A build-time path belonging to Windows passes without being
        # named in cloudinit/ - ADR 0003 section 2's distinction between what
        # builds a template and what provisions a guest.
        sysprep = ("C:" + BACKSLASH + "Windows" + BACKSLASH + "System32"
                   + BACKSLASH + "Sysprep" + BACKSLASH + "sysprep.exe")
        root = build(tmp, f"Run `{sysprep}` to generalize.\n")
        check(
            "an allowlisted third-party path passes",
            run(root).returncode == 0,
            "NOT_OURS did not admit the path it names",
            failures,
        )
        # The allowlist admits the path, not the neighbourhood.
        root = build(tmp, f"Run `{sysprep}.bak` to generalize.\n")
        check(
            "and only that exact path, not one near it",
            run(root).returncode == 1,
            "a path that merely starts with an allowlisted one was admitted",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, "Nothing here names a Windows path.\n")
        check(
            "a document naming no path is not a finding",
            run(root).returncode == 0,
            "the guard requires every document to mention a path",
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
