#!/usr/bin/env python3
"""Extract candidate Azure Arc machine resource names from a Terraform plan JSON.

We look for Proxmox VM resources that are being deleted (or replaced), and return
their *previous* name (change.before.name).

Usage:
  python3 extract_arc_names_from_plan.py tfplan.json
"""

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_arc_names_from_plan.py <tfplan.json>", file=sys.stderr)
        return 2

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    names: set[str] = set()
    for rc in plan.get("resource_changes", []) or []:
        if rc.get("type") != "proxmox_virtual_environment_vm":
            continue
        change = rc.get("change") or {}
        actions = change.get("actions") or []
        if ("delete" not in actions) and ("delete_create" not in actions):
            continue
        before = change.get("before") or {}
        name = before.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())

    for n in sorted(names):
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
