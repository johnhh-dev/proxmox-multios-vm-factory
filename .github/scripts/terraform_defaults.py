#!/usr/bin/env python3
"""Read a variable's default out of `variables.tf`.

Two tools need the same three values and neither should be told them by hand:

    check_protected_ids.py    compares var.protected_vm_ids against the three
                              other places it is written
    reconcile_inventory.py    excludes those VMIDs and the two template VMIDs
                              from the orphan verdict

A library rather than a copy in each, for the reason ADR 0001 gives about the
credential audits: written separately they end up mostly identical, and then a
fix to one leaves the other wrong with nothing to say which. Here the shapes
they parse are the same and the consequences of being wrong are not - one goes
red in CI, the other prints `orphan` next to a machine whose runbook says
`qm destroy --purge`.

## Why a regex and not HCL

Because the alternative is a dependency. `terraform console` would answer
exactly, and it needs Terraform, an init, and a provider download - which
reconcile_inventory.py deliberately does not, since it is meant to run from a
laptop against sanitised captures. The parsing is narrow on purpose: a literal
default in a `variable` block, and nothing else. Anything computed, interpolated
or overridden by a `TF_VAR_` is out of scope and returns None rather than a
guess.

**None means "not found", never "empty".** The caller decides what to do with
that, and both callers treat it as a refusal rather than as a default of
nothing - because the failure mode of guessing empty is a tool that reports the
runner as an orphan.

Usage: imported. Not runnable.
"""

import re

# A `variable "name" { ... }` block, ending at a closing brace in column zero.
# Terraform formats the file (`terraform fmt` is a CI gate), so the closing
# brace of a top-level block is reliably unindented - which is what makes this
# safe against a nested `validation { }` or a `default = { ... }` map.
def _block(text: str, name: str):
    pattern = re.compile(
        r'variable\s+"' + re.escape(name) + r'"\s*\{(.*?)^\}',
        re.DOTALL | re.MULTILINE,
    )
    found = pattern.search(text)
    return found.group(1) if found else None


def list_default(text: str, name: str):
    """The integers in a list-typed variable's default, or None.

    An empty list is a real answer and comes back as `[]`, which is why the
    absent case is None: `var.protected_vm_ids = []` and "there is no such
    variable" are different facts and the caller acts differently on each.
    """
    block = _block(text, name)
    if block is None:
        return None
    found = re.search(r"default\s*=\s*\[([^\]]*)\]", block)
    if not found:
        return None
    return [int(n) for n in re.findall(r"\d+", found.group(1))]


def number_default(text: str, name: str):
    """A whole-number default, or None.

    Deliberately does not accept a float. Every number this is asked for is a
    VMID, and Proxmox has no other kind - locals.tf refuses `vm_id = 100.5` for
    the same reason.
    """
    block = _block(text, name)
    if block is None:
        return None
    found = re.search(r"default\s*=\s*(\d+)\s*$", block, re.MULTILINE)
    return int(found.group(1)) if found else None


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()
