#!/usr/bin/env python3
"""Every relative link in the documentation must resolve to a file.

Written after finding seventeen dead links in one document - and not a document
where it did not matter. `docs/backlog-reconciliation.md` exists to map two
backlogs onto each other by linking to the issues in them, and every one of
those links was broken:

    [KAN-003](../../issues/16)

That form works from a file at the repository root. From `docs/` it does not.
GitHub leaves the href relative and the browser resolves it against the page:

    .../blob/main/docs/backlog-reconciliation.md
      ..  -> .../blob/main/
      ..  -> .../blob/
      issues/16 -> .../blob/issues/16      <- 404

Confirmed by asking GitHub what it renders, rather than by reasoning about it:

    gh api repos/OWNER/REPO/contents/docs/backlog-reconciliation.md \\
      -H "Accept: application/vnd.github.html"
    href="../../issues/16"

## The convention this enforces

**A relative link points at a file in this repository. Anything else is an
absolute URL.** That is already what most of these documents do - the incident
runbooks link issues as full URLs - and it makes the rule checkable without
modelling how GitHub resolves a path, which is the part that went wrong.

## What this does not check

**Anchors.** `file.md#some-heading` is checked as far as `file.md` and no
further. GitHub's heading-to-anchor slugification has enough edge cases -
punctuation, duplicates, inline code, emoji - that a checker would produce false
positives, and a check nobody trusts gets disabled rather than fixed.

**External URLs.** Nothing here makes a network call. A link rotting on someone
else's server is not something a pull request should fail on, and a checker that
needs the internet fails on the day the internet is what is broken.

Usage: python3 check_doc_links.py [root]
Exit codes: 0 every relative link resolves, 1 at least one does not.
"""

import os
import re
import sys

# Markdown inline links. The lazy body and the excluded ')' keep this from
# swallowing a following link, which matters in the tables these documents are
# mostly made of.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

SKIP_DIRS = {".git", ".terraform", "__pycache__", "node_modules"}

# A link that names a scheme, a fragment on the same page, or a bare mailto is
# not this checker's business.
EXTERNAL = ("http://", "https://", "mailto:", "//", "#")


def markdown_files(root: str) -> list:
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.lower().endswith(".md"):
                found.append(os.path.join(base, name))
    return sorted(found)


def check(path: str, root: str) -> list:
    """Return one message per link in this file that does not resolve."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    problems = []
    here = os.path.dirname(path) or "."

    for match in LINK.finditer(text):
        target = match.group(1)
        if target.startswith(EXTERNAL):
            continue

        # Strip the anchor. See the docstring for why it is not checked.
        filename = target.split("#", 1)[0]
        if not filename:
            continue

        resolved = os.path.normpath(os.path.join(here, filename))
        line = text.count("\n", 0, match.start()) + 1
        rel = os.path.relpath(path, root).replace(os.sep, "/")

        if os.path.exists(resolved):
            # A relative link that escapes the repository resolves on disk only
            # by accident, and means something different to a browser than it
            # does here. `../../issues/16` was exactly this shape.
            if os.path.relpath(resolved, root).startswith(".."):
                problems.append(
                    f"{rel}:{line}: '{target}' points outside the repository. "
                    "Link to a file in this repository, or use an absolute URL."
                )
            continue

        hint = ""
        if re.match(r"(\.\./)+((issues|pull)/\d+|issues|pulls?)/?$", filename):
            hint = (
                " This looks like a GitHub issue or pull request link. Those "
                "resolve against the blob URL, not the repository root - use "
                "the full https:// URL instead."
            )
        problems.append(f"{rel}:{line}: '{target}' does not resolve.{hint}")

    return problems


def main() -> int:
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = markdown_files(root)

    problems = []
    for path in files:
        problems.extend(check(path, root))

    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)

    if problems:
        print(
            f"\n{len(problems)} broken link(s) across {len(files)} markdown file(s).",
            file=sys.stderr,
        )
        return 1

    print(f"All relative links resolve, across {len(files)} markdown file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
