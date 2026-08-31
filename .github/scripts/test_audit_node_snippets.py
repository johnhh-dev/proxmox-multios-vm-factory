#!/usr/bin/env python3
"""Tests for audit_node_snippets.py (SEC-001d-A3).

The case that carries the issue is the base64 one. Both templates route every
free-form value across the template boundary base64-encoded (BUG-010), so a
snippet on the node carries the *encoded* form — a scan looking for the raw
value would report clean while the credential sits there in full.

The second is history. ADR 0001 path 3 is a file written once and kept for the
whole lifetime of the VM, so a snippet from before SEC-001a still carries a
service-principal secret the template has not contained for months. That is the
value of scanning the node rather than reading the template, and it is why
credentials come from the environment: only a person knows what the old ones
were.

Usage: python3 test_audit_node_snippets.py
"""

import base64
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "audit_node_snippets.py")

SECRET = "sp-secret-value-0000"


def run(target, env_extra, secret_vars):
    env = dict(os.environ)
    env["SECRET_VARS"] = secret_vars
    for key in list(env):
        if key.startswith("TF_VAR_"):
            del env[key]
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, SCRIPT, target], capture_output=True, text=True, env=env
    )


def snippets(tmp, **files):
    for name, content in files.items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
            handle.write(content)
    return tmp


def check(name, condition, detail, failures):
    if condition:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: {detail}")
        failures.append(name)


def main() -> int:
    failures = []

    # THE case. BUG-010 routes every free-form value through base64encode(), so
    # this is the form a snippet actually holds. A scan for the raw value would
    # report clean.
    encoded = base64.b64encode(SECRET.encode()).decode()
    with tempfile.TemporaryDirectory() as tmp:
        snippets(
            tmp,
            **{
                "old-vm-vendor-data.yaml": f"ARC_SP_SECRET=\"$(b64 '{encoded}')\"\n",
                "new-vm-vendor-data.yaml": "nothing of interest\n",
            },
        )
        result = run(tmp, {"TF_VAR_arc_sp_secret": SECRET}, "TF_VAR_arc_sp_secret")
        check(
            "a base64-encoded credential in a snippet is found",
            result.returncode == 1 and "old-vm-vendor-data.yaml" in result.stdout,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )
        check(
            "the finding says which rendering matched",
            "base64" in result.stdout,
            f"out={result.stdout!r}",
            failures,
        )
        check(
            "the credential is never printed",
            SECRET not in result.stdout and SECRET not in result.stderr,
            "the credential appeared in the output",
            failures,
        )
        check(
            "the clean snippet is not reported",
            "new-vm-vendor-data.yaml" not in result.stdout,
            f"out={result.stdout!r}",
            failures,
        )

    # A rotated credential is exactly what this is for: the template stopped
    # carrying it, and the node did not.
    with tempfile.TemporaryDirectory() as tmp:
        snippets(tmp, **{"stale-vendor-data.yaml": f"password: {SECRET}\n"})
        result = run(tmp, {"TF_VAR_windows_admin_password": SECRET}, "TF_VAR_windows_admin_password")
        check(
            "a raw credential left by an older apply is found",
            result.returncode == 1,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )

    # BUG-021's escaping, through the shared variants().
    quoted = 'pa"ssword-value'
    with tempfile.TemporaryDirectory() as tmp:
        snippets(tmp, **{"a.yaml": '{"x":"' + json.dumps(quoted)[1:-1] + '"}'})
        result = run(tmp, {"TF_VAR_windows_admin_password": quoted}, "TF_VAR_windows_admin_password")
        check(
            "a value containing a quote is found in its escaped form",
            result.returncode == 1,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        snippets(tmp, **{"a.yaml": "clean\n", "b.yaml": "also clean\n"})
        result = run(tmp, {"TF_VAR_arc_sp_secret": SECRET}, "TF_VAR_arc_sp_secret")
        check(
            "a node with nothing on it exits 0",
            result.returncode == 0 and "No scanned credential" in result.stdout,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )

    # An empty snippets directory is the answer that must not be read as clean:
    # it is also what running this on the wrong host looks like.
    with tempfile.TemporaryDirectory() as tmp:
        result = run(tmp, {"TF_VAR_arc_sp_secret": SECRET}, "TF_VAR_arc_sp_secret")
        check(
            "an empty directory exits 2 rather than reporting clean",
            result.returncode == 2 and "not the same as a clean one" in result.stderr,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        snippets(tmp, **{"a.yaml": "clean\n"})
        result = run(tmp, {"TF_VAR_arc_sp_secret": SECRET}, "")
        check(
            "an empty SECRET_VARS exits 2",
            result.returncode == 2,
            f"rc={result.returncode}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        snippets(tmp, **{"a.yaml": "clean\n"})
        result = run(tmp, {"TF_VAR_arc_sp_secret": "abc"}, "TF_VAR_arc_sp_secret")
        check(
            "a value too short to search for is refused, not skipped",
            result.returncode == 2 and "NOT scanned" in result.stderr,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    # Nothing is deleted, ever. SEC-001d-A2 is a person's call.
    with tempfile.TemporaryDirectory() as tmp:
        snippets(tmp, **{"a.yaml": f"password: {SECRET}\n"})
        run(tmp, {"TF_VAR_arc_sp_secret": SECRET}, "TF_VAR_arc_sp_secret")
        check(
            "a snippet with a finding is left on disk",
            os.path.exists(os.path.join(tmp, "a.yaml")),
            "the snippet was removed",
            failures,
        )

    if failures:
        print(f"\n{len(failures)} case(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll cases pass: the encoded form is found, nothing is printed, nothing is deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
