#!/usr/bin/env python3
"""Extract candidate Azure Arc machine resource names from a Terraform state JSON.

We look for Proxmox VM resources in the current state and return their name.

Usage:
  terraform show -json > state.json
  python3 extract_arc_names_from_state.py state.json
"""

import json
import sys


def walk_modules(mod):
    # yields resources from this module and children
    for r in (mod.get("resources") or []):
        yield r
    for c in (mod.get("child_modules") or []):
        yield from walk_modules(c)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_arc_names_from_state.py <state.json>", file=sys.stderr)
        return 2

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        st = json.load(f)

    values = (st.get("values") or {})
    root = values.get("root_module") or {}

    names: set[str] = set()
    for r in walk_modules(root):
        if r.get("type") != "proxmox_virtual_environment_vm":
            continue
        vals = r.get("values") or {}
        name = vals.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())

    for n in sorted(names):
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
