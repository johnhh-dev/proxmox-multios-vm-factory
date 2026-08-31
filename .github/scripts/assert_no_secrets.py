#!/usr/bin/env python3
"""Fail if a secret value appears in a file that is about to be printed.

The workflow captures Terraform output to a file, runs this, and only prints the
file once it comes back clean. Anything this catches never reaches the job log.

Which variables to scan for comes in through SECRET_VARS - one name per line,
commas also accepted. Values are read from the environment and are never printed
or written anywhere: a finding reports the variable name, the file and which
rendering matched, nothing more.

## BUG-021: why a substring test was not enough

The guard used to be `if value in text`. But Terraform does not print a string
raw - it escapes it. A password of `pa"ss` appears in plan output as `pa\\"ss`;
one containing a newline appears with a literal `\\n`; a backslash is doubled.
None of those contains the raw value, so the guard reported clean and the
workflow published the log.

That made the guard weakest on exactly the values BUG-010 is about - the ones
with quotes, backslashes and newlines in them. A credential chosen to be strong
was more likely to slip past it than a weak one.

So every value is now scanned in each rendering it could plausibly take - see
`variants()` - and multi-line values are additionally compared with indentation
removed, because Terraform prints those as a heredoc block whose continuation
lines are indented and which therefore contains no contiguous copy of the value
at all. See `unindent()`; that one was found by measuring real plan output
rather than by reasoning about it.

## Refusing to certify (BUG-021-A3)

A value that is set but too short to search for is not "skipped" - the guard
cannot form a reliable pattern for it, and saying otherwise would report a scan
that proved nothing about that credential. Those fail the run. A value that is
simply unset is a different thing and is reported as such: Arc is optional here,
and an absent optional credential cannot leak.

Usage:
  SECRET_VARS='TF_VAR_arc_sp_secret' python3 assert_no_secrets.py plan.txt

Exit codes: 0 clean, 1 secret found or not certifiable, 2 usage error.
"""

import base64
import json
import os
import re
import sys

DEFAULT_MIN_LEN = 6


def secret_var_names(raw: str) -> list[str]:
    names: list[str] = []
    for token in re.split(r"[\s,]+", raw):
        token = token.strip()
        if token and token not in names:
            names.append(token)
    return names


def variants(value: str) -> dict[str, str]:
    """Every rendering the value could take on its way into a log.

    Keyed by the name of the form, so a finding can say which one matched -
    that tells whoever reads it where the leak is, not merely that there is one.

    The forms:

    - `raw`: the value itself. Terraform prints multi-line strings as a heredoc
      with real newlines, so this still matters for those.
    - `terraform-escaped`: what a Terraform string literal looks like. This is
      the form the old substring test missed.
    - `json-escaped`: `terraform show -json`, and any diagnostic that embeds the
      value in a JSON document. Differs from the above on control characters,
      which JSON writes as \\uXXXX.
    - `newline-escaped`: newlines only, everything else untouched. Some
      renderers collapse just the line breaks.
    - `base64`: both templates now render every free-form value this way.
      BUG-010 routed them through `base64encode()` in Terraform and decodes
      them in the guest - `b64` in linux.yaml.tftpl, `ConvertFrom-Base64Utf8`
      in windows.yaml.tftpl - so that a value containing a quote, a dollar or a
      backtick cannot be executed by the shell it lands in.

      This variant predates that change and was written when no template
      encoded anything, on the reasoning that a guard covering only the
      encodings that exist today stops covering the code the moment someone
      adds one. That is now the load-bearing case rather than the hypothetical
      one: without it, `TF_VAR_windows_admin_password` and
      `TF_VAR_arc_access_token` would now appear in a plan in a form this
      scanner did not recognise.

    Identical renderings collapse, so a value with none of these characters is
    scanned once rather than five times.
    """
    forms = {
        "raw": value,
        "terraform-escaped": (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        ),
        "json-escaped": json.dumps(value)[1:-1],
        "newline-escaped": value.replace("\r\n", "\\n").replace("\n", "\\n"),
        "base64": base64.b64encode(value.encode("utf-8")).decode("ascii"),
    }

    # Keep the first name for each distinct rendering, so `raw` wins for a value
    # that needs no escaping and the report stays readable.
    seen: dict[str, str] = {}
    for name, rendered in forms.items():
        if rendered not in seen.values():
            seen[name] = rendered
    return seen


def unindent(text: str) -> str:
    """Every line with its leading whitespace removed.

    Terraform renders a multi-line string as an indented heredoc:

          + input  = <<-EOT
                bug021-canary-"quote-\backslash
                second-line-4d1e8b
            EOT

    The quote is not escaped and the backslash is not doubled - but every line
    after the first carries the block's indentation, so *no* contiguous
    rendering of the value appears in the output at all. Comparing both sides
    with leading whitespace removed is what makes a multi-line secret findable.

    Discovered by test_assert_no_secrets_real_plan.py on its first run against
    real plan output, which is exactly the assumption that suite exists to
    replace: every hand-written fixture here had the value contiguous, because
    that is what a person writing a fixture assumes.
    """
    return "\n".join(line.lstrip() for line in text.splitlines())


def main() -> int:
    paths = sys.argv[1:]
    if not paths:
        print("usage: assert_no_secrets.py <file> [file...]", file=sys.stderr)
        return 2

    raw = os.environ.get("SECRET_VARS", "")
    names = secret_var_names(raw)
    if not names:
        print("::error::SECRET_VARS is empty - nothing would be scanned", file=sys.stderr)
        return 2

    try:
        min_len = int(os.environ.get("SECRET_MIN_LEN", DEFAULT_MIN_LEN))
    except ValueError:
        print("::error::SECRET_MIN_LEN is not an integer", file=sys.stderr)
        return 2

    contents: dict[str, str] = {}
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                contents[path] = f.read()
        except FileNotFoundError:
            print(f"note: {path} does not exist, skipping")

    if not contents:
        print("::error::none of the given files exist - nothing was scanned", file=sys.stderr)
        return 2

    unindented = {path: unindent(text) for path, text in contents.items()}

    scanned: list[str] = []
    unset: list[str] = []
    uncertifiable: list[str] = []
    found: list[str] = []

    for name in names:
        value = os.environ.get(name, "")
        if not value:
            unset.append(name)
            continue
        if len(value) < min_len:
            # Not a skip. Searching a plan for a three-character string matches
            # ordinary words, so there is no pattern that would mean anything -
            # and reporting that as "scanned" is the failure mode BUG-021-A3
            # names.
            uncertifiable.append(f"{name} (shorter than {min_len} characters)")
            continue

        rendered = variants(value)
        scanned.append(f"{name} ({len(rendered)} form(s))")
        for path, text in contents.items():
            for form, needle in rendered.items():
                if needle in text:
                    found.append(f"{name} as {form} -> {path}")
                    break
                # A multi-line value is never contiguous in a heredoc block.
                if "\n" in needle and unindent(needle) in unindented[path]:
                    found.append(f"{name} as {form}, heredoc-indented -> {path}")
                    break

    print(
        f"scanned {len(contents)} file(s) for {len(scanned)} of {len(names)} value(s)"
    )
    if scanned:
        print("  scanned:         " + ", ".join(scanned))
    if unset:
        print("  not set:         " + ", ".join(unset))
    if uncertifiable:
        print("  NOT CERTIFIABLE: " + ", ".join(uncertifiable))

    if found:
        for hit in found:
            print(f"::error::secret material in output: {hit}")
        print(
            "::error::refusing to publish this output. Fix the leak, then purge "
            "this run's logs and rotate the affected credential."
        )
        return 1

    if uncertifiable:
        print(
            "::error::one or more values are set but too short to search for, so "
            "this output cannot be certified clean. Lengthen the credential, or "
            "remove it from SECRET_VARS if it is not secret."
        )
        return 1

    if not scanned:
        print(
            "::error::no value was set or long enough to scan for - the guard "
            "proved nothing"
        )
        return 1

    print("clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
