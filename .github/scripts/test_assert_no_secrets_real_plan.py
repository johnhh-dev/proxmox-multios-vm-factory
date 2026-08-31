#!/usr/bin/env python3
"""The leak guard, against output Terraform actually produced (BUG-021-A4).

test_assert_no_secrets.py pins what the guard does when it is handed a given
rendering. It cannot answer the question underneath BUG-021 - *which* rendering
Terraform produces - because a hand-written fixture encodes the same assumption
the bug was made of. The original suite had a multi-line case that planted the
raw value in the fixture, which tested that Python's `in` spans newlines, not
that the guard survives Terraform's escaping of them.

So this renders a real plan carrying the hostile value and runs the guard on the
result. Two cases, which together are the "before and after verdict" the issue
asks for as evidence:

  leaky    - the value reaches plan output. The guard must refuse to publish.
  redacted - sensitive() marks it, so plan output says "(sensitive value)".
             The guard must pass, or it would block every legitimate run.

## Why a throwaway configuration rather than the root module

The root module already marks the rendered snippet with sensitive() (main.tf,
SEC-003), so a plan of it never carries the password and could not produce the
leaky case at all. The unknown being tested is narrower than the factory: it is
how Terraform spells a string containing a quote, a backslash and a newline when
it prints one. A two-resource configuration answers exactly that, with no
provider, no network, and no reach anywhere near the lab.

Usage: python3 test_assert_no_secrets_real_plan.py
Requires: terraform on PATH (or TERRAFORM_BIN).
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import assert_no_secrets as guard  # noqa: E402
from hostile_values import HOSTILE_PASSWORD  # noqa: E402

GUARD = os.path.join(HERE, "assert_no_secrets.py")
TERRAFORM = os.environ.get("TERRAFORM_BIN", "terraform")

CONFIG = """
terraform {
  required_version = "~> 1.15.0"
}

variable "hostile" {
  type      = string
  sensitive = %s
}

resource "terraform_data" "subject" {
  input = var.hostile
}
"""


def render_plan(tmp: str, sensitive: bool) -> str:
    """Plan a one-resource configuration and return the plan text.

    `sensitive` on the variable is what decides whether the value reaches the
    output - which makes the two cases differ by one word, so a difference in
    the verdict can only come from the rendering.
    """
    work = os.path.join(tmp, "sensitive" if sensitive else "leaky")
    os.makedirs(work)
    with open(os.path.join(work, "main.tf"), "w", encoding="utf-8") as f:
        f.write(CONFIG % ("true" if sensitive else "false"))

    env = dict(os.environ)
    env["TF_VAR_hostile"] = HOSTILE_PASSWORD
    env["TF_IN_AUTOMATION"] = "1"

    init = subprocess.run(
        [TERRAFORM, "init", "-input=false", "-no-color"],
        cwd=work, env=env, capture_output=True, text=True,
    )
    if init.returncode != 0:
        raise SystemExit(
            "FAIL: terraform init failed in the fixture\n" + init.stdout + init.stderr
        )

    plan = subprocess.run(
        [TERRAFORM, "plan", "-input=false", "-no-color", "-lock=false"],
        cwd=work, env=env, capture_output=True, text=True,
    )
    if plan.returncode != 0:
        raise SystemExit(
            "FAIL: terraform plan failed in the fixture\n" + plan.stdout + plan.stderr
        )

    path = os.path.join(tmp, ("plan-redacted" if sensitive else "plan-leaky") + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(plan.stdout + plan.stderr)
    return path


def run_guard(path: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SECRET_VARS"] = "TF_VAR_hostile"
    env["TF_VAR_hostile"] = HOSTILE_PASSWORD
    return subprocess.run(
        [sys.executable, GUARD, path], env=env, capture_output=True, text=True
    )


def main() -> int:
    if shutil.which(TERRAFORM) is None and not os.path.isfile(TERRAFORM):
        print(f"FAIL: terraform not found ({TERRAFORM})", file=sys.stderr)
        return 2

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        leaky = render_plan(tmp, sensitive=False)
        with open(leaky, "r", encoding="utf-8") as f:
            text = f.read()

        # Record what Terraform actually did, because that is the fact this
        # whole issue turned on. Which form matched is reported below; the value
        # itself is a published test fixture, not a credential.
        if HOSTILE_PASSWORD in text:
            print("note: Terraform printed the value raw")
        # Same matching the guard does, including the heredoc path - a
        # diagnostic that used a different rule than the code it describes
        # would report "none" for output the guard correctly refuses.
        unindented = guard.unindent(text)
        matched = [
            form if rendered in text else f"{form} (heredoc-indented)"
            for form, rendered in guard.variants(HOSTILE_PASSWORD).items()
            if rendered in text
            or ("\n" in rendered and guard.unindent(rendered) in unindented)
        ]
        print(f"note: renderings present in real plan output: {matched or 'none'}")

        if not matched:
            failures.append(
                "the hostile value does not appear in the leaky plan in ANY known "
                "rendering. Either Terraform now redacts an unmarked variable - in "
                "which case this test needs rewriting - or variants() has lost a "
                "form it needs. Do not weaken the guard to make this pass.\n"
                + text
            )

        result = run_guard(leaky)
        if result.returncode != 1:
            failures.append(
                "guard did not refuse a real plan carrying the hostile value "
                f"(exit {result.returncode})\n{result.stdout}{result.stderr}"
            )
        else:
            print("ok   - guard refuses a real plan carrying the hostile value")

        redacted = render_plan(tmp, sensitive=True)
        result = run_guard(redacted)
        if result.returncode != 0:
            failures.append(
                "guard blocked a plan where the value was properly redacted "
                f"(exit {result.returncode}) - this would block every real run\n"
                f"{result.stdout}{result.stderr}"
            )
        else:
            print("ok   - guard passes a real plan where sensitive() redacted it")

    if failures:
        print("", file=sys.stderr)
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    print("\nThe guard's verdict is correct on real Terraform output, both ways.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
