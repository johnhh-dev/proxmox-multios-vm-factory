#!/usr/bin/env python3
"""Extract Azure Arc machine resource names to delete, from a Terraform state JSON.

We look for Proxmox VM resources in the current state and return the name of the
*Arc machine* each one registered under - which is not necessarily the VM's own
name. See arc_registration.py for why (BUG-019).

Usage:
  terraform show -json > state.json
  python3 extract_arc_names_from_state.py state.json
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arc_registration  # noqa: E402


def walk_modules(mod):
    # yields resources from this module and children
    for r in (mod.get("resources") or []):
        yield r
    for c in (mod.get("child_modules") or []):
        yield from walk_modules(c)


def state_resources(state: dict) -> list[dict]:
    values = state.get("values") or {}
    root = values.get("root_module") or {}
    return list(walk_modules(root))


def extract_vm_names(state: dict) -> set[str]:
    """Names of every Proxmox VM in the state, at any module depth.

    Deliberately narrow - a name is only returned when the state actually
    carries one as a string.
    """
    names: set[str] = set()
    for r in state_resources(state):
        if r.get("type") != "proxmox_virtual_environment_vm":
            continue
        vals = r.get("values") or {}
        name = vals.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def extract_names(state: dict) -> set[str]:
    """Arc machine resource names to delete.

    Which VMs exist comes from the VM resources; what each is called in Azure
    comes from the arc_registration markers alongside them.
    """
    resources = state_resources(state)
    return arc_registration.resolve(
        extract_vm_names(state), arc_registration.from_state_resources(resources)
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_arc_names_from_state.py <state.json>", file=sys.stderr)
        return 2

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        st = json.load(f)

    for n in sorted(extract_names(st)):
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
