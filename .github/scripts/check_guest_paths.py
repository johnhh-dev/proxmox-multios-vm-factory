#!/usr/bin/env python3
r"""Every Windows path the documentation names is one a guest actually gets.

`verify_first_boot.py` carries this comment, and it is the reason that file
uses raw strings:

    Written as an ordinary literal, the Windows path contains `\v` - which
    Python reads as a vertical tab, silently producing
    `C:\ProgramData<VT>m-factory-firstboot.done`.

It was caught there by asserting the value rather than reading it. It was not
caught in `docs/lab-access-required.md`, which carried a literal U+000B inside
the one command that decides whether five merged Windows changes ever took
effect - so an operator who followed the instruction got `False` and the
conclusion "first boot still has not run", which is indistinguishable from the
defect the instruction exists to detect.

Nothing renders a vertical tab. The path *looks* right in a browser, in a
terminal and in a diff.

## What counts as a known path

Anything a file under `cloudinit/` names. Those documents are what create the
files on the guest - the first-boot script writes the logs and the marker, and
the captured Cloudbase-Init configuration names its own install location - so
they are the only place a path a reader is told to type can legitimately come
from.

That was the whole rule when this was written, on the reasoning that a document
naming a path no guest document creates is "either wrong or describing something
this repository does not control". `NOT_OURS` below is the second half of that
sentence, made explicit rather than left as a category with no members.

ADR 0003 §2 draws the line these sit on, and drew it before this guard existed:
a template's **build** inputs are not a guest's provision-time files.
`template-build.md` is about the build, so it names Windows' own sysprep - a
path no guest document creates, and none should.

Every entry carries its reason, because an allowlist without reasons is where
findings go to be silenced.

## What this does not check

**Linux paths.** `/var/log/arc-onboard.log` cannot be mangled by a string
escape, and the documentation names runner paths (`/opt/terraform-state/...`)
and node paths (`/var/lib/vz/snippets/`) that no guest document mentions - so
the same rule there would be false positives rather than findings. The failure
class this guard is aimed at is Windows-shaped.

**That the path is the right one for the sentence around it.** Naming
`C:\arc-onboard.log` where the marker was meant passes here. This finds paths
that cannot exist, not paths that are beside the point.

Usage: python3 check_guest_paths.py [repo-root]
Exit codes: 0 every path is known, 1 at least one is not, 2 nothing could be
scanned - which is never reported as clean.
"""

import glob
import os
import sys

# Stops at ordinary whitespace and deliberately not at a vertical tab or a
# backspace: those are exactly the bytes a mangled path contains, and a pattern
# that treated them as a boundary would silently truncate the finding to a
# prefix and then report the prefix as the problem.
STOP = " \t\n\r\f"

# Trailing characters a sentence or a table cell puts after a path. Stripped
# from the right, repeatedly, because `` `C:\arc-onboard.log`, `` ends with two
# of them. A path in this repository never ends in one.
TRAILING = "`*_,;:!?)]}\"'<>|" + "."

DOC_GLOBS = ("*.md", "docs/*.md", "docs/**/*.md")
SOURCE_DIR = "cloudinit"

# Paths belonging to Windows or to third-party software - things this
# repository runs and does not create. See the header for why this exists and
# what keeps it honest.
NOT_OURS = {
    r"C:\Windows\System32\Sysprep\sysprep.exe":
        "Windows' own sysprep, run when building the template (DOC-002-A3)",
    r"C:\Windows\System32\Sysprep\Unattend.xml":
        "where sysprep reads the answer file from at build time; the file "
        "itself is cloudinit/Unattend.xml",
    r"C:\Windows\System32\LogFiles\Firewall\pfirewall.log":
        "Windows' own firewall log. KAN-011-A6 turns writing to it on and "
        "does not choose the location, so the first-boot script names it "
        "with $env:SystemRoot rather than as a literal - which is "
        "correct, and is why the literal appears in no "
        "cloudinit/ document",
}


def paths_in(text: str) -> list:
    """Every `C:\\...` run in this text, one entry per occurrence."""
    found = []
    i = text.find("C:")
    while i != -1:
        rest = text[i + 2:]
        if rest[:1] == "\\":
            end = 2
            while i + end < len(text) and text[i + end] not in STOP:
                end += 1
            token = text[i:i + end].rstrip(TRAILING)
            if len(token) > 3:
                found.append(token)
        i = text.find("C:", i + 2)
    return found


def control_characters(path: str) -> list:
    """The C0 controls inside a path, named as code points.

    A tab is not included: `STOP` already ended the token there, so anything
    reaching this function held a control character that no editor shows.
    """
    return [f"U+{ord(ch):04X}" for ch in path if ord(ch) < 0x20]


def known_paths(root: str) -> set:
    """Every Windows path named by a guest document under cloudinit/."""
    known = set()
    source = os.path.join(root, SOURCE_DIR)
    for path in sorted(glob.glob(os.path.join(source, "*"))):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                body = handle.read()
        except OSError:
            continue
        known.update(paths_in(body))
    return known


def documents(root: str) -> list:
    found = []
    for pattern in DOC_GLOBS:
        found.extend(glob.glob(os.path.join(root, pattern), recursive=True))
    return sorted(set(os.path.normpath(p) for p in found))


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    )

    known = known_paths(root)
    if not known:
        print(
            f"::error::no Windows path found under {SOURCE_DIR}/. Finding no "
            "source of truth is not the same as finding every document correct.",
            file=sys.stderr,
        )
        return 2

    docs = documents(root)
    if not docs:
        print(
            f"::error::no markdown found under {root}.",
            file=sys.stderr,
        )
        return 2

    findings = []
    checked = 0
    not_ours = 0
    for doc in docs:
        try:
            with open(doc, "r", encoding="utf-8", errors="replace") as handle:
                body = handle.read()
        except OSError as exc:
            print(f"::error::cannot read {doc} ({exc.strerror}).", file=sys.stderr)
            return 2
        rel = os.path.relpath(doc, root).replace(os.sep, "/")
        for path in paths_in(body):
            checked += 1
            if path in known:
                continue
            if path in NOT_OURS:
                not_ours += 1
                continue
            controls = control_characters(path)
            if controls:
                findings.append(
                    f"{rel}: a path holds {', '.join(controls)} - an escape "
                    "sequence was expanded into the document. See the comment "
                    "on MARKERS in verify_first_boot.py."
                )
            else:
                findings.append(
                    f"{rel}: {path} is named by no document under {SOURCE_DIR}/."
                )

    print(f"Checked {checked} Windows path(s) across {len(docs)} document(s) "
          f"against {len(known)} named by {SOURCE_DIR}/.")
    if not_ours:
        print(f"{not_ours} of them belong to Windows or to third-party "
              "software - see NOT_OURS, which names a reason for each.")

    if findings:
        print()
        for finding in findings:
            print(f"::error::{finding}", file=sys.stderr)
        print(f"\n{len(findings)} document path(s) no guest creates.", file=sys.stderr)
        return 1

    print("Every one of them is accounted for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
