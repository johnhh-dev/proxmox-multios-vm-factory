#!/usr/bin/env python3
"""Tests for az_session.sh (KAN-017-A5).

Nothing here reaches Azure. What is tested is the part both callers depend on
and neither can see: which exit code means what.

The three-way distinction is the whole design. `arc-cleanup` treats "not
configured" as a loud skip because a destroy that silently left Arc machines
behind is BUG-004; the post-apply smoke test treats the same answer as "there
was nothing to verify". They cannot both be right if the script decides, so the
script reports and the callers decide - and that only works if 2 never means
anything else.

Usage: python3 test_az_session.py
Requires: bash.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "az_session.sh")

# Resolved to an absolute path *here*, using this process's PATH, because run()
# below hands the child a PATH containing only the stub directory - that is the
# whole point of it, so `az` resolves to the stub and not to a real Azure CLI.
# A bare "bash" was therefore looked up in that stripped PATH and not found:
#
#   FileNotFoundError: [Errno 2] No such file or directory: 'bash'
#
# It passed locally only because BASH_BIN was set to an absolute path, which is
# exactly the kind of difference between a workstation and CI that makes a suite
# look green and land red.
#
# BASH_BIN stays for the other reason it exists: on a Windows workstation `bash`
# resolves to the WSL launcher, which cannot execute a script on a Windows path,
# and an unrunnable suite gets skipped rather than fixed.
BASH = os.environ.get("BASH_BIN") or shutil.which("bash") or "bash"

CONFIGURED = {
    "TF_VAR_arc_tenant_id": "11111111-1111-1111-1111-111111111111",
    "TF_VAR_arc_subscription_id": "22222222-2222-2222-2222-222222222222",
    "TF_VAR_arc_resource_group": "rg-arc-home-lab",
    "TF_VAR_arc_sp_id": "33333333-3333-3333-3333-333333333333",
    "TF_VAR_arc_sp_secret": "not-a-real-secret",
}


def run(env_overrides, az_body=None, on_path=True):
    """Run the script with a stubbed `az`, or with none on PATH at all."""
    with tempfile.TemporaryDirectory() as tmp:
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        if on_path:
            stub = os.path.join(bindir, "az")
            with open(stub, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(az_body or "#!/bin/sh\necho Registered\nexit 0\n")
            os.chmod(stub, 0o755)

        env = {
            "PATH": bindir,
            "HOME": tmp,
            # bash needs these on some platforms; nothing here reads them.
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        }
        env.update(env_overrides)
        return subprocess.run(
            [BASH, SCRIPT], capture_output=True, text=True, env=env
        )


def check(name, condition, detail, failures):
    if condition:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: {detail}")
        failures.append(name)


def main() -> int:
    failures = []

    # Exit 2 - the answer both callers interpret differently, one per missing
    # value so that a partially configured lab is not read as an unconfigured
    # one by accident.
    for missing in sorted(CONFIGURED):
        env = {k: v for k, v in CONFIGURED.items() if k != missing}
        result = run(env)
        check(
            f"exit 2 when {missing} is absent",
            result.returncode == 2,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    env = dict(CONFIGURED)
    env["TF_VAR_arc_sp_secret"] = ""
    result = run(env)
    check(
        "an empty value counts as absent, not as a credential",
        result.returncode == 2,
        f"rc={result.returncode}",
        failures,
    )

    result = run({})
    check(
        "exit 2 says nothing - the caller writes the message",
        result.returncode == 2 and result.stdout.strip() == "",
        f"rc={result.returncode} out={result.stdout!r}",
        failures,
    )

    # Exit 1 - configured, and something is wrong. Distinct from 2, because
    # arc-cleanup skips on 2 and must not skip on this.
    result = run(CONFIGURED, on_path=False)
    check(
        "a missing az is exit 1, not exit 2",
        result.returncode == 1 and "::error::" in result.stdout,
        f"rc={result.returncode} out={result.stdout!r}",
        failures,
    )
    check(
        "the missing-az message points at the runner runbook",
        "runner-trust-boundary.md" in result.stdout,
        f"out={result.stdout!r}",
        failures,
    )

    result = run(CONFIGURED, az_body="#!/bin/sh\nexit 1\n")
    check(
        "a failed login is exit 1, not a silent success",
        result.returncode == 1,
        f"rc={result.returncode}",
        failures,
    )

    # Exit 0 - and the provider registration is conditional, so a lab where it
    # is already registered does not pay for `az provider register --wait`.
    calls = os.path.join(tempfile.gettempdir(), "az_session_calls.txt")
    if os.path.exists(calls):
        os.remove(calls)
    az = (
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        'if [ "$1" = "provider" ] && [ "$2" = "show" ]; then echo Registered; fi\n'
        "exit 0\n"
    )
    result = run(CONFIGURED, az_body=az)
    recorded = open(calls, encoding="utf-8").read() if os.path.exists(calls) else ""
    check(
        "a fully configured lab exits 0",
        result.returncode == 0,
        f"rc={result.returncode} err={result.stderr!r}",
        failures,
    )
    check(
        "an already-registered provider is not registered again",
        "provider register" not in recorded,
        f"calls={recorded!r}",
        failures,
    )
    check(
        "the subscription is selected, not just logged in to",
        "account set" in recorded,
        f"calls={recorded!r}",
        failures,
    )

    if os.path.exists(calls):
        os.remove(calls)
    az = (
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        'if [ "$1" = "provider" ] && [ "$2" = "show" ]; then echo NotRegistered; fi\n'
        "exit 0\n"
    )
    result = run(CONFIGURED, az_body=az)
    recorded = open(calls, encoding="utf-8").read() if os.path.exists(calls) else ""
    check(
        "an unregistered provider is registered",
        result.returncode == 0 and "provider register" in recorded,
        f"rc={result.returncode} calls={recorded!r}",
        failures,
    )
    if os.path.exists(calls):
        os.remove(calls)

    if failures:
        print(f"\n{len(failures)} case(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll cases pass: 2 means unconfigured, 1 means broken, and only 0 means ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
