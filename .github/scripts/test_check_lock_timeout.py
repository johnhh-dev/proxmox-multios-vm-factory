#!/usr/bin/env python3
"""Tests for check_lock_timeout.py.

Two cases carry this suite, and neither is "a missing flag is caught".

`a_mention_is_not_an_invocation` is the false positive that would make the check
unusable: `terraform-plan.yml` and `terraform-apply.yml` both echo the string
`terraform plan exited $rc` on their failure paths, and a substring match would
demand a lock timeout on an error message.

`no_invocations_is_a_failure` is the false negative, which is the same shape as
every other guard in this directory: a matcher that has stopped matching reports
that everything it checks is fine, and goes green forever.

Usage: python3 test_check_lock_timeout.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_lock_timeout.py")

GOOD = """name: terraform-apply
jobs:
  apply:
    steps:
      - run: terraform init -reconfigure
      - name: Plan
        run: |
          terraform plan -no-color -input=false -lock-timeout=10m -out=tfplan > apply-plan.txt 2>&1 || rc=$?
          if [ "$rc" -ne 0 ]; then
            echo "::error::terraform plan exited $rc. Its output is NOT printed."
          fi
      - run: terraform apply -auto-approve -lock-timeout=10m tfplan
      - name: Read-only
        run: |
          terraform state list > state-list.txt
          terraform console -no-color <<<'var.proxmox_endpoint'
          terraform output -json
"""

FAILURES = []


def run(*files):
    with tempfile.TemporaryDirectory() as root:
        paths = []
        for i, body in enumerate(files):
            path = os.path.join(root, "w%d.yml" % i)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            paths.append(path)
        done = subprocess.run(
            [sys.executable, SCRIPT] + paths,
            capture_output=True, text=True,
        )
        return done.returncode, done.stdout + done.stderr


def case(name, code, output, expected, must_say=None, must_not_say=None):
    if code != expected:
        FAILURES.append("%s: expected exit %d, got %d\n%s"
                        % (name, expected, code, output))
        return
    if must_say and must_say not in output:
        FAILURES.append("%s: output does not mention %r\n%s"
                        % (name, must_say, output))
        return
    if must_not_say and must_not_say in output:
        FAILURES.append("%s: output should not mention %r\n%s"
                        % (name, must_not_say, output))
        return
    print("ok   %s" % name)


code, out = run(GOOD)
case("all_invocations_wait", code, out, 0, "Checked 2 locking")

# The state of the repository before this check existed.
code, out = run(GOOD.replace(" -lock-timeout=10m -out=tfplan", " -out=tfplan"))
case("plan_without_timeout_is_caught", code, out, 1, "takes the state lock")

code, out = run(GOOD.replace("terraform apply -auto-approve -lock-timeout=10m",
                             "terraform apply -auto-approve"))
case("apply_without_timeout_is_caught", code, out, 1, "terraform apply")

code, out = run("""jobs:
  destroy:
    steps:
      - run: terraform destroy -auto-approve
""")
case("destroy_without_timeout_is_caught", code, out, 1, "terraform destroy")

# An error message is not a command. Both real workflows contain this line.
code, out = run("""jobs:
  plan:
    steps:
      - run: |
          echo "::error::terraform plan exited $rc"
          echo "terraform apply would follow"
      - run: terraform plan -lock-timeout=10m
""")
case("a_mention_is_not_an_invocation", code, out, 0, "Checked 1 locking")

# Nor is a comment. The workflows are more comment than command.
code, out = run("""jobs:
  plan:
    steps:
      # `terraform plan` is not supposed to persist a refreshed state
      - run: terraform plan -lock-timeout=10m
""")
case("a_comment_is_not_an_invocation", code, out, 0, "Checked 1 locking")

# Read-only subcommands take no write lock and are not required to wait.
code, out = run("""jobs:
  x:
    steps:
      - run: terraform init -backend=false
      - run: terraform validate
      - run: terraform state list
      - run: timeout 60 terraform console -no-color
      - run: terraform plan -lock-timeout=10m
""")
case("read_only_subcommands_are_exempt", code, out, 0, "Checked 1 locking")

# A wrapped invocation is still an invocation.
code, out = run("""jobs:
  x:
    steps:
      - run: timeout 600 terraform apply -auto-approve tfplan
""")
case("timeout_wrapper_still_counts", code, out, 1, "terraform apply")

# The guard that has stopped matching must not pass.
code, out = run("""jobs:
  x:
    steps:
      - run: echo nothing to see
""")
case("no_invocations_is_a_failure", code, out, 1, "stopped matching")

# A glob that matches nothing is not a repository with nothing to check. With
# no arguments at all the script falls back to the real workflows on purpose,
# so this passes a pattern instead.
done = subprocess.run(
    [sys.executable, SCRIPT, os.path.join(HERE, "no-such-directory", "*.yml")],
    capture_output=True, text=True,
)
case("no_files_is_a_failure", done.returncode, done.stdout + done.stderr, 1,
     "no workflow files matched")

if FAILURES:
    print()
    for failure in FAILURES:
        print("FAIL %s" % failure, file=sys.stderr)
    raise SystemExit(1)

print("\nall cases pass")
