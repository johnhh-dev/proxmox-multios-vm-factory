#!/usr/bin/env python3
"""Canary test for assert_no_secrets.py.

The guard in the plan workflow is only worth having if it actually fires, so CI
runs this first: it plants a known canary in a fixture, proves the guard fails
on it, and proves the guard passes on the same fixture with the canary removed.
A guard that has silently stopped matching fails the job here, before any real
plan runs.

BUG-021 added the escaping cases. The old guard was `if value in text`, which
misses every rendering Terraform actually produces for a value containing a
quote, a backslash or a newline - so it was weakest on exactly the strong
credentials it most needed to catch. There is now one case per form, and
`test_every_form_is_load_bearing` fails if any branch of `variants()` is removed,
which is the acceptance criterion asking that the suite fail when an escaping
branch goes away.

These fixtures are hand-written on purpose: they pin the *guard's* behaviour
given a rendering. Whether Terraform actually produces that rendering is a
different question, and hand-writing the answer to it would encode the same
assumption the bug was made of - so it is tested separately, against a real
plan, in test_assert_no_secrets_real_plan.py (BUG-021-A4).

Usage: python3 test_assert_no_secrets.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "assert_no_secrets.py")

sys.path.insert(0, HERE)

import assert_no_secrets as guard  # noqa: E402
from hostile_values import HOSTILE_PASSWORD as HOSTILE  # noqa: E402

CANARY = "sec003-canary-9f3a1c7e"


PLAN_BODY = """Terraform will perform the following actions:

  # proxmox_virtual_environment_file.user_data["vm1"] will be created
  + resource "proxmox_virtual_environment_file" "user_data" {
      + source_raw {
          + data      = {DATA}
          + file_name = "vm1-user-data.yaml"
        }
    }

Plan: 1 to add, 0 to change, 0 to destroy.
"""


def run(fixture: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in ("SECRET_VARS", "SECRET_MIN_LEN", "CANARY_VALUE"):
        env.pop(key, None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, GUARD, fixture],
        env=env,
        capture_output=True,
        text=True,
    )


def check(label: str, got: int, want: int, proc: subprocess.CompletedProcess) -> bool:
    if got == want:
        print(f"ok   - {label} (exit {got})")
        return True
    print(f"FAIL - {label}: expected exit {want}, got {got}")
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    return False


def write(path: str, data: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(PLAN_BODY.replace("{DATA}", data))
    return path


def escaping_cases(tmp: str) -> bool:
    """One fixture per rendering in variants(). Each must be caught.

    The guard is handed the *raw* value through the environment every time, as
    the workflow does - it is the fixture that carries the escaped form. That is
    the whole defect: the environment and the log never agree on the spelling.
    """
    ok = True
    env = {"SECRET_VARS": "CANARY_VALUE", "CANARY_VALUE": HOSTILE}

    for form, rendered in guard.variants(HOSTILE).items():
        fixture = write(os.path.join(tmp, f"plan-{form}.txt"), rendered)
        proc = run(fixture, env)
        ok &= check(f"guard catches the {form} rendering", proc.returncode, 1, proc)
        if proc.returncode == 1:
            # The report has to name the form, or a leak tells you nothing about
            # where it came from.
            if form not in proc.stdout:
                print(f"FAIL - the finding does not name the '{form}' form")
                print(proc.stdout)
                ok = False

    return ok


def test_every_form_is_load_bearing(tmp: str) -> bool:
    """Removing any branch of variants() must break at least one fixture.

    Without this the suite would go green on a guard that had quietly stopped
    covering a rendering - which is precisely how BUG-021 survived review.
    """
    ok = True
    forms = guard.variants(HOSTILE)
    for form, rendered in forms.items():
        others = {v for k, v in forms.items() if k != form}
        if rendered in others:
            print(f"FAIL - the '{form}' rendering is a duplicate and proves nothing")
            ok = False
    if len(forms) < 4:
        print(f"FAIL - the hostile value only produced {len(forms)} distinct forms")
        ok = False
    else:
        print(f"ok   - the hostile value produces {len(forms)} distinct renderings")
    return ok


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        leaky = write(
            os.path.join(tmp, "plan-leaky.txt"),
            f"<<-EOT\n            password: {CANARY}\n        EOT",
        )
        clean = write(os.path.join(tmp, "plan-clean.txt"), "(sensitive value)")

        env = {"SECRET_VARS": "CANARY_VALUE", "CANARY_VALUE": CANARY}

        proc = run(leaky, env)
        ok &= check("guard fails on a plan carrying the canary", proc.returncode, 1, proc)

        proc = run(clean, env)
        ok &= check("guard passes on a redacted plan", proc.returncode, 0, proc)

        # A scan that checked nothing must not report success: an unset value
        # would otherwise make every run look clean.
        proc = run(clean, {"SECRET_VARS": "CANARY_VALUE"})
        ok &= check("guard fails when no value was scannable", proc.returncode, 1, proc)

        proc = run(clean, {"SECRET_VARS": "", "CANARY_VALUE": CANARY})
        ok &= check("guard rejects an empty SECRET_VARS", proc.returncode, 2, proc)

        # Multi-line values must match as a whole, not line by line.
        multi = write(os.path.join(tmp, "plan-multiline.txt"),
                      f"line-one\n          {CANARY}")
        proc = run(multi, {"SECRET_VARS": "CANARY_VALUE",
                           "CANARY_VALUE": f"line-one\n          {CANARY}"})
        ok &= check("guard matches a multi-line value", proc.returncode, 1, proc)

        # The rendering Terraform actually produces for a multi-line value,
        # reproduced from real plan output. Every line after the first carries
        # the heredoc block's indentation, so the value is not contiguous in the
        # file in any form - which is why `raw` alone is not enough.
        indented = "\n".join("            " + ln for ln in HOSTILE.splitlines())
        heredoc = write(os.path.join(tmp, "plan-heredoc.txt"),
                        f"<<-EOT\n{indented}\n        EOT")
        proc = run(heredoc, {"SECRET_VARS": "CANARY_VALUE", "CANARY_VALUE": HOSTILE})
        ok &= check("guard catches an indented heredoc rendering",
                    proc.returncode, 1, proc)
        if proc.returncode == 1 and "heredoc-indented" not in proc.stdout:
            print("FAIL - the finding does not say the match was heredoc-indented")
            ok = False

        # BUG-021-A1: the escaped renderings, one fixture each.
        ok &= escaping_cases(tmp)
        ok &= test_every_form_is_load_bearing(tmp)

        # BUG-021-A3: set but unsearchable is not the same as absent.
        proc = run(clean, {"SECRET_VARS": "CANARY_VALUE", "CANARY_VALUE": "abc"})
        ok &= check("guard refuses to certify a too-short value", proc.returncode, 1, proc)
        if "NOT CERTIFIABLE" not in proc.stdout:
            print("FAIL - the summary does not distinguish 'could not certify'")
            ok = False

        # ...and an absent optional credential is reported without failing,
        # because Arc is optional and what is not set cannot leak.
        # The name must be one nothing could plausibly set: this ran green in
        # CI and red on the lab runner, where TF_VAR_arc_sp_secret is a real
        # populated variable and was scanned rather than reported unset.
        proc = run(clean, {"SECRET_VARS": "CANARY_VALUE NEVER_SET_2b7c41ff",
                           "CANARY_VALUE": CANARY})
        ok &= check("guard tolerates an unset optional value", proc.returncode, 0, proc)
        if "not set:" not in proc.stdout:
            print("FAIL - the summary does not report the unset value")
            ok = False

        # A value needing no escaping must not be scanned five times over.
        plain = guard.variants("plain-value-with-no-metacharacters")
        if list(plain) != ["raw", "base64"]:
            print(f"FAIL - a plain value produced unexpected forms: {list(plain)}")
            ok = False
        else:
            print("ok   - identical renderings collapse")

    if not ok:
        print("::error::assert_no_secrets.py does not behave as required")
        return 1
    print("all canary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
