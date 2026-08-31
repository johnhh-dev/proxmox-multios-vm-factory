#!/usr/bin/env python3
"""Decide whether an apply may proceed, from the inventory and the state (BUG-002).

The guard this replaces was a grep:

    if grep -qE 'vms\\s*=\\s*\\{\\s*\\}' locals.tf; then

`grep` matches within a single line and `\\s` does not cross line boundaries,
but `local.vms` spans dozens of lines. The pattern could not match, so an empty
inventory was read as "VMs are desired", the step then required a non-empty
state, and a legitimate apply was blocked. The only escape was TF_BOOTSTRAP=true,
which disabled the guard wholesale - including for the orphan-VM case it exists
to catch. It was inverted in practice.

The second defect was the approach, not the pattern: matching HCL source text
means a commented-out `# vms = {}` satisfies the guard. So nothing here reads
locals.tf. The desired inventory arrives as the evaluated value of `local.vms`
and the state arrives from `terraform state list`.

## The two conditions, kept apart (A3)

The old step conflated "nothing is declared" with "state is missing". They are
different questions with different answers:

| Desired | State    | Verdict                                              |
|---------|----------|------------------------------------------------------|
| empty   | empty    | proceed - nothing declared, nothing managed          |
| empty   | non-empty| proceed, loudly - this apply tears the lab down      |
| non-empty | empty  | BLOCK - applying would create VMs Terraform forgets  |
| non-empty | non-empty | proceed - the ordinary case                       |

## TF_BOOTSTRAP (A4)

It used to skip the whole check. Here it authorises exactly one thing: the third
row, and only when there is genuinely no state file yet. An *existing* state file
that lists nothing is not a first run - it is a state that was truncated or
restored empty, which is precisely the orphan-VM case. Bootstrap does not
override that.

## Why the exposure argument matters

Established while fixing SEC-002 (#34): `terraform show -json tfplan` emits a
`variables` block holding every input variable in cleartext, `sensitive = true`
does not redact it, and it is populated even when the plan changes nothing -
which is exactly the empty-inventory run this guard exists for. Reading plan JSON
here would write the full credential set to the runner on the very runs the
guard is protecting. `terraform console` evaluating `keys(local.vms)` returns a
list of names and nothing else.

`local.vms` rather than `local.vms_final` for the same reason, one step further:
`vms_final` merges `var.windows_admin_password` into every VM, so its value
carries a sensitive mark that `terraform console` would print as
"(sensitive value)". The keys are identical either way.

Usage:
  printf '%s\\n' 'jsonencode(keys(local.vms))' | terraform console -no-color > desired.json
  terraform state list > state-list.txt 2>/dev/null || true
  python3 inventory_guard.py --desired desired.json --state-list state-list.txt \\
      --state-file /opt/terraform-state/proxmox-ubuntu-vm-factory/terraform.tfstate

Exit codes: 0 proceed, 1 blocked, 2 the guard could not decide (also blocking).
"""

import argparse
import json
import os
import sys

VM_TYPE = "proxmox_virtual_environment_vm"


class Undecidable(Exception):
    """The guard could not establish one of its two inputs.

    Always fatal. A guard that cannot read the inventory has not concluded that
    the inventory is empty.
    """


def parse_console_output(text: str) -> list[str]:
    """Read the list of VM names out of `terraform console` output.

    `jsonencode(...)` makes the result a JSON *string*, which console then
    prints as a quoted Go-style literal - so the document is decoded twice. The
    bare-array form is accepted too, so a future Terraform that stops quoting
    scalar results does not turn this into a silent block.
    """
    stripped = text.strip()
    if not stripped:
        raise Undecidable("terraform console produced no output")

    # Console echoes diagnostics to stderr, so stdout should be one value. Take
    # the last non-empty line rather than the whole buffer in case a banner ever
    # appears ahead of it.
    line = [ln for ln in stripped.splitlines() if ln.strip()][-1].strip()

    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as exc:
        raise Undecidable(
            f"could not parse the inventory from terraform console ({exc.msg}). "
            f"Expected a JSON document, got {line[:80]!r}"
        ) from exc

    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise Undecidable(
                f"terraform console returned a string that is not JSON ({exc.msg})"
            ) from exc

    if not isinstance(decoded, list) or not all(isinstance(n, str) for n in decoded):
        raise Undecidable(
            "the inventory did not evaluate to a list of names. If `local.vms` "
            "now carries a sensitive value, console prints '(sensitive value)' "
            "and this guard cannot read it."
        )

    return decoded


def parse_state_list(text: str) -> list[str]:
    """Resource addresses from `terraform state list`, one per line."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def decide(
    desired: list[str],
    state_resources: list[str],
    state_file_exists: bool,
    bootstrap: bool,
) -> tuple[int, list[str]]:
    """Return (exit code, lines to print). Pure, so the four cases are testable."""
    vm_addresses = [a for a in state_resources if a.split(".")[0] == VM_TYPE]
    lines = [
        f"desired inventory: {len(desired)} VM(s)"
        + (f" - {', '.join(sorted(desired))}" if desired else ""),
        f"state: {len(state_resources)} resource(s), {len(vm_addresses)} of them VMs"
        + ("" if state_file_exists else " (no state file on disk)"),
    ]

    if not desired and not state_resources:
        lines.append(
            "Nothing is declared and nothing is managed. The apply is a no-op."
        )
        return 0, lines

    if not desired and state_resources:
        lines.append(
            f"::warning::The inventory is empty but state manages "
            f"{len(state_resources)} resource(s). This apply will DESTROY them, "
            f"including {len(vm_addresses)} VM(s). If that is not what you "
            f"intended, stop the run and restore the inventory."
        )
        return 0, lines

    if desired and state_resources:
        lines.append("Inventory and state are both populated. Proceeding.")
        return 0, lines

    # desired and not state_resources - the orphan-VM case.
    if bootstrap and not state_file_exists:
        lines.append(
            "TF_BOOTSTRAP=true and there is no state file yet, so this is a "
            "first-time creation. Proceeding. Unset TF_BOOTSTRAP once the apply "
            "has written state."
        )
        return 0, lines

    if bootstrap and state_file_exists:
        lines.append(
            "::error::TF_BOOTSTRAP=true, but a state file already exists and "
            "lists no resources. That is not a first run - it is a state that "
            "was truncated, restored empty, or pointed at the wrong path. "
            "Applying now would create VMs that Terraform does not know it "
            "owns. Bootstrap does not override this. Restore the correct state "
            "file, or confirm in Proxmox that no VM from this inventory exists "
            "and then delete the empty state file."
        )
        return 1, lines

    lines.append(
        "::error::The inventory declares "
        f"{len(desired)} VM(s) but Terraform state is empty. Applying now would "
        "create VMs that Terraform does not know it owns - the orphan-VM case. "
        "Restore the persisted state from "
        "/opt/terraform-state/proxmox-ubuntu-vm-factory, or, if this really is "
        "the first apply against an empty hypervisor, set the repository "
        "variable TF_BOOTSTRAP=true and re-run."
    )
    return 1, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desired", required=True,
                        help="file holding `terraform console` output for the inventory")
    parser.add_argument("--state-list", required=True,
                        help="file holding `terraform state list` output")
    parser.add_argument("--state-file", required=True,
                        help="path the backend persists state to")
    args = parser.parse_args()

    bootstrap = os.environ.get("TF_BOOTSTRAP", "").strip().lower() == "true"

    try:
        with open(args.desired, "r", encoding="utf-8") as handle:
            desired = parse_console_output(handle.read())
    except Undecidable as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"::error::could not read the inventory from {args.desired} ({exc.strerror}). "
            "The guard blocks rather than assuming an empty inventory.",
            file=sys.stderr,
        )
        return 2

    try:
        with open(args.state_list, "r", encoding="utf-8") as handle:
            state_resources = parse_state_list(handle.read())
    except OSError as exc:
        print(
            f"::error::could not read {args.state_list} ({exc.strerror})",
            file=sys.stderr,
        )
        return 2

    code, lines = decide(
        desired=desired,
        state_resources=state_resources,
        state_file_exists=os.path.isfile(args.state_file),
        bootstrap=bootstrap,
    )
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
