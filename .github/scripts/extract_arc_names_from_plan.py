#!/usr/bin/env python3
"""Extract Azure Arc machine resource names to delete, from a Terraform plan JSON.

We look for Proxmox VM resources that are being deleted (or replaced), and return
the name of the *Arc machine* each one registered under - which is not
necessarily the VM's own name. See arc_registration.py for why (BUG-019).

Usage:
  python3 extract_arc_names_from_plan.py tfplan.json
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arc_registration  # noqa: E402


def extract_vm_names(plan: dict) -> set[str]:
    """Names of VMs whose current instance the plan removes.

    A replacement counts: Terraform writes it as ["delete", "create"] or
    ["create", "delete"] depending on create_before_destroy, and either way the
    machine that is registered in Arc today goes away. Both forms contain
    "delete", so testing for that one member covers the replacement cases too.

    CHORE-004-A1. This used to also test for the string "delete_create", which
    is not an action Terraform emits - the plan JSON action strings are
    "no-op", "create", "read", "update" and "delete", and a replacement is the
    two-element list above rather than a fused name. The test could never be
    true. It was harmless, because the "delete" test already caught every
    replacement, but it advertised a case that was never exercised and would
    have misled the next person editing this filter.

    This is deliberately narrow - a name is only returned when the plan actually
    carries one as a string.
    """
    names: set[str] = set()
    for rc in plan.get("resource_changes", []) or []:
        if rc.get("type") != "proxmox_virtual_environment_vm":
            continue
        change = rc.get("change") or {}
        actions = change.get("actions") or []
        if "delete" not in actions:
            continue
        before = change.get("before") or {}
        name = before.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def extract_names(plan: dict) -> set[str]:
    """Arc machine resource names to delete.

    Two steps, kept apart on purpose: which VMs are going away is decided from
    the VM resources, and what each one is called in Azure is looked up from the
    arc_registration markers. Deciding both from the markers would mean
    disabling Arc on a running VM deleted its machine resource, which is a
    different change than this one.
    """
    return arc_registration.resolve(
        extract_vm_names(plan), arc_registration.from_plan(plan)
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_arc_names_from_plan.py <tfplan.json>", file=sys.stderr)
        return 2

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    for n in sorted(extract_names(plan)):
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
