#!/usr/bin/env python3
"""Tests for audit_state_secrets.py (SEC-001e-A1).

Three properties, and the second is why this tool exists rather than a grep.

**It scans the backups.** There are up to twenty beside the state file since
FEAT-001-A3, each holding the same historical cleartext. A scan reporting a
clean state file next to twenty copies of what it used to contain would be
worse than no scan, because someone would believe it.

**It finds escaped renderings.** BUG-021 found that a substring test misses a
password containing a quote, a backslash or a newline — so the guard was weakest
on exactly the values worth protecting. This reuses assert_no_secrets.variants()
rather than reimplementing it, and these cases prove the reuse is live.

**It never prints the credential.** It runs where the credentials are.

Usage: python3 test_audit_state_secrets.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "audit_state_secrets.py")

TOKEN = "root@pam!gha=0000-1111-2222"


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


def lab(tmp, live="clean", backups=()):
    os.makedirs(os.path.join(tmp, "backups"), exist_ok=True)
    with open(os.path.join(tmp, "terraform.tfstate"), "w", encoding="utf-8") as h:
        h.write(json.dumps({"version": 4, "attr": live}))
    for i, content in enumerate(backups):
        name = f"terraform.tfstate.2026010{i + 1}T000000Z.run{i}"
        with open(os.path.join(tmp, "backups", name), "w", encoding="utf-8") as h:
            h.write(json.dumps({"version": 4, "attr": content}))
    return tmp


def check(name, condition, detail, failures):
    if condition:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: {detail}")
        failures.append(name)


def main() -> int:
    failures = []

    # THE case. state-recovery.md and runner-trust-boundary.md both record that
    # SEC-001e's purge must include backups/; this is what would prove it did.
    with tempfile.TemporaryDirectory() as tmp:
        lab(tmp, live="nothing here", backups=[TOKEN])
        result = run(tmp, {"TF_VAR_proxmox_api_token": TOKEN}, "TF_VAR_proxmox_api_token")
        check(
            "a clean state file with a dirty backup is not clean",
            result.returncode == 1 and "run0" in result.stdout,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )
        check(
            "the finding names the file and the variable, not the value",
            TOKEN not in result.stdout and TOKEN not in result.stderr,
            "the credential appeared in the output",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        lab(tmp, live="nothing here", backups=["also nothing"])
        result = run(tmp, {"TF_VAR_proxmox_api_token": TOKEN}, "TF_VAR_proxmox_api_token")
        check(
            "nothing anywhere exits 0",
            result.returncode == 0 and "No scanned credential" in result.stdout,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )

    # BUG-021's finding, which is the reason variants() is reused rather than
    # rewritten: a raw substring test misses every one of these.
    for label, value, stored in (
        ("a quote", 'pa"ssword-x', json.dumps('pa"ssword-x')[1:-1]),
        ("a backslash", "pa\\ssword-x", json.dumps("pa\\ssword-x")[1:-1]),
        ("a newline", "pass\nword-x", json.dumps("pass\nword-x")[1:-1]),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "backups"))
            with open(os.path.join(tmp, "terraform.tfstate"), "w", encoding="utf-8") as h:
                h.write('{"version":4,"attr":"' + stored + '"}')
            result = run(
                tmp,
                {"TF_VAR_windows_admin_password": value},
                "TF_VAR_windows_admin_password",
            )
            check(
                f"a password containing {label} is found in its escaped form",
                result.returncode == 1,
                f"rc={result.returncode} out={result.stdout!r}",
                failures,
            )

    # Every way of finding nothing to say must be distinguishable from finding
    # nothing. Reporting "clean" is the one result this must never invent.
    with tempfile.TemporaryDirectory() as tmp:
        lab(tmp)
        result = run(tmp, {"TF_VAR_proxmox_api_token": TOKEN}, "")
        check(
            "an empty SECRET_VARS exits 2 rather than reporting clean",
            result.returncode == 2 and "::error::" in result.stderr,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    with tempfile.TemporaryDirectory() as tmp:
        result = run(
            os.path.join(tmp, "nothing-here"),
            {"TF_VAR_proxmox_api_token": TOKEN},
            "TF_VAR_proxmox_api_token",
        )
        check(
            "no state file at all exits 2 rather than reporting clean",
            result.returncode == 2 and "not the same as finding nothing" in result.stderr,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    # BUG-021-A3: a value too short to search for cannot be certified, and
    # saying otherwise reports a scan that proved nothing about it.
    with tempfile.TemporaryDirectory() as tmp:
        lab(tmp)
        result = run(tmp, {"TF_VAR_proxmox_api_token": "abc"}, "TF_VAR_proxmox_api_token")
        check(
            "a value too short to search for is refused, not skipped",
            result.returncode == 2 and "NOT scanned" in result.stderr,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    # An unset optional credential cannot leak, and is a different answer again.
    with tempfile.TemporaryDirectory() as tmp:
        lab(tmp)
        result = run(tmp, {}, "TF_VAR_arc_sp_secret")
        check(
            "an unset credential is reported as unset, not as clean",
            result.returncode == 0 and "not set" in result.stdout,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )

    # A single file is the other legitimate call - scanning one backup by hand.
    with tempfile.TemporaryDirectory() as tmp:
        lab(tmp, live=TOKEN)
        result = run(
            os.path.join(tmp, "terraform.tfstate"),
            {"TF_VAR_proxmox_api_token": TOKEN},
            "TF_VAR_proxmox_api_token",
        )
        check(
            "a single file can be scanned on its own",
            result.returncode == 1 and "Scanned 1 file" in result.stdout,
            f"rc={result.returncode} out={result.stdout!r}",
            failures,
        )

    if failures:
        print(f"\n{len(failures)} case(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll cases pass: backups are scanned, escaped forms are found, and nothing prints a credential.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
