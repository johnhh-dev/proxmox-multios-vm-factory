#!/usr/bin/env python3
"""Read the Arc resource name of each VM out of Terraform JSON (BUG-019).

Shared by both extractors, because both had the same defect and a second copy of
the fix is a second thing to get wrong.

## Why this exists

`az resource delete` was given the Proxmox VM name. The Arc resource is only
named after the VM by default - `arc = { enabled = true, resource_name = "..." }`
overrides it, and the override exists so the two need not match. Where they
diverged the delete targeted a name that does not exist, `|| true` hid it, and
the orphaned Arc machine then blocked re-onboarding under the same name.

`terraform_data.arc_registration` in arc.tf carries the pair. This reads it back
out of either representation of that resource.

## The fallback, and its limit

A plan or state written before arc.tf existed carries no markers at all. Rather
than return nothing - which would silently stop cleaning up anything - the
callers fall back to the previous behaviour when the document has no markers
whatsoever, which is what `markers_present` is for.

Once markers *are* present the fallback stops: a VM without one is a VM with Arc
disabled, and its machine is not ours to delete. Falling back per-VM instead
would resurrect the original bug for exactly the VMs the marker was added for.
"""

MARKER_TYPE = "terraform_data"
MARKER_NAME = "arc_registration"


def _pair(values) -> tuple[str, str] | None:
    """(vm name, Arc resource name) from one marker's attribute values.

    Deliberately narrow: this feeds a destructive Azure call, so a name is only
    returned when the document actually carries one as a non-empty string.
    """
    if not isinstance(values, dict):
        return None
    data = values.get("input")
    if not isinstance(data, dict):
        return None
    vm_name = data.get("vm_name")
    resource_name = data.get("resource_name")
    if not isinstance(vm_name, str) or not vm_name.strip():
        return None
    if not isinstance(resource_name, str) or not resource_name.strip():
        return None
    return vm_name.strip(), resource_name.strip()


def is_marker(resource: dict) -> bool:
    """terraform_data is the built-in no-op resource and checks.tf uses it too,
    so the local name has to match as well as the type."""
    return (
        resource.get("type") == MARKER_TYPE
        and resource.get("name") == MARKER_NAME
    )


def from_plan(plan: dict) -> dict[str, str]:
    """Map VM name -> Arc resource name, from a plan's resource_changes.

    `before` wins over `after`: the machine registered in Azure right now is the
    one the cleanup has to delete, so a plan that changes the resource name must
    still delete under the old one.
    """
    mapping: dict[str, str] = {}
    for rc in plan.get("resource_changes", []) or []:
        if not is_marker(rc):
            continue
        change = rc.get("change") or {}
        for side in ("before", "after"):
            pair = _pair(change.get(side))
            if pair:
                mapping.setdefault(*pair)
                break
    return mapping


def from_state_resources(resources) -> dict[str, str]:
    """Map VM name -> Arc resource name, from state resources at any depth."""
    mapping: dict[str, str] = {}
    for resource in resources:
        if not is_marker(resource):
            continue
        pair = _pair(resource.get("values"))
        if pair:
            mapping.setdefault(*pair)
    return mapping


def resolve(vm_names: set[str], mapping: dict[str, str]) -> set[str]:
    """Arc resource names to delete, for the VMs that are going away.

    With no markers in the document at all, this is a pre-arc.tf plan or state
    and the previous name-based behaviour applies. With markers present, a VM
    that has none has Arc disabled and is skipped.
    """
    if not mapping:
        return set(vm_names)
    return {mapping[name] for name in vm_names if name in mapping}
