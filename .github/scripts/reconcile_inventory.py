#!/usr/bin/env python3
"""Compare what is declared, what Terraform manages, and what Proxmox actually has.

DOC-001-A1 (#59) asks for the real Proxmox inventory and the real Terraform state
to be captured and compared against what the repository declares. That comparison
is the whole difficulty of the issue: the README described five VMs, `local.vms`
declared none, and nothing could tell whether those guests existed and were
unmanaged or had never existed at all.

This does the comparison. It does not decide anything - DOC-001-A2 requires a
recorded lifecycle decision per VM, and that is a person's judgement. What this
removes is the part where the person has to build the three-way table by hand
and gets a row wrong.

## The three inputs

    qm list                       what Proxmox has
    terraform show -json          what Terraform manages
    terraform console <<< 'keys(local.vms)'   what the repository declares

Read from files so the tool runs anywhere - on the runner, or on a laptop from
sanitised captures. Nothing here connects to anything.

## Verdicts

Every VM lands in exactly one:

    managed        declared, in state, on the node        nothing to do
    protected      on var.protected_vm_ids                docs/unmanaged-vms.md
    orphan         on the node, not in state              docs/incident-orphan-vm.md
    ghost          in state, not on the node              destroyed outside Terraform
    undeclared     in state and on the node, not in the inventory
                   -> the inventory disagrees with what is managed
    missing        declared, not in state, not on the node
                   -> the next apply will create it
    pending        declared and on the node, not in state
                   -> IMPORT, do not apply (docs/state-recovery.md)

`pending` is the row that matters and the reason this is a tool rather than a
paragraph: applying instead of importing builds a second VM beside the first,
and both then exist with only one of them managed.

Usage:
  python3 reconcile_inventory.py --qm-list qm.txt --state state.json [--declared names.txt]

Exit codes: 0 everything is `managed`, 1 anything else, 2 usage error.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import terraform_defaults  # noqa: E402

# Where the two ID lists live when nobody passes them. Relative to this file, so
# it is found from any working directory - including the runner's state
# directory, which is where the other half of the comparison is produced.
VARIABLES_TF = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "variables.tf")
)

# `qm list` pads its columns, so the split is on whitespace rather than columns.
# The header line and any blank line are skipped. A VMID is the first field and
# is always digits; anything else on the line is ignored, because the columns
# after NAME differ between Proxmox versions and none of them is needed here.
QM_ROW = re.compile(r"^\s*(\d+)\s+(\S+)")


def parse_qm_list(text: str) -> dict[str, int]:
    """Map VM name -> vmid from `qm list` output."""
    found: dict[str, int] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.lstrip().upper().startswith("VMID"):
            continue
        m = QM_ROW.match(line)
        if not m:
            continue
        vmid, name = m.group(1), m.group(2)
        found[name] = int(vmid)
    return found


def walk_modules(mod: dict):
    yield mod
    for child in mod.get("child_modules", []) or []:
        yield from walk_modules(child)


def parse_state(state: dict) -> dict[str, int | None]:
    """Map VM name -> vmid for every proxmox VM Terraform manages.

    Child modules are walked because FEAT-007 (#65) would move the factory into
    one, and a reconciliation tool that silently stopped seeing the VMs would be
    worse than one that never worked.
    """
    found: dict[str, int | None] = {}
    root = (state.get("values") or {}).get("root_module") or {}
    for mod in walk_modules(root):
        for res in mod.get("resources", []) or []:
            if res.get("type") != "proxmox_virtual_environment_vm":
                continue
            values = res.get("values") or {}
            name = values.get("name")
            if isinstance(name, str) and name.strip():
                vmid = values.get("vm_id")
                found[name.strip()] = vmid if isinstance(vmid, int) else None
    return found


def verdict(
    name: str,
    declared: set[str],
    state: dict,
    node: dict,
    protected: set[int] | None = None,
) -> str:
    in_declared = name in declared
    in_state = name in state
    on_node = name in node

    # Before anything else, because getting this wrong is the expensive
    # direction. A VM on var.protected_vm_ids is deliberately unmanaged - the
    # factory needs it in order to run - and calling it an orphan sends a reader
    # to a runbook whose default recovery is `qm destroy --purge`.
    #
    # Two of that runbook's three safety conditions pass for all three of them:
    # they are not in `terraform state list`, and their .conf carries no
    # cicustom or ipconfig0 because they were built by hand. The only guard left
    # is "the name and creation time match the failed run", which asks the
    # reader to notice. That is thin for a command that would terminate the
    # machine running it.
    #
    # Found by running this tool against the real node for the first time, not
    # by reading it.
    if protected and on_node and node.get(name) in protected:
        return "protected"

    if in_declared and in_state and on_node:
        return "managed"
    if in_declared and on_node and not in_state:
        return "pending"
    if in_declared and not on_node and not in_state:
        return "missing"
    if on_node and not in_state:
        return "orphan"
    if in_state and not on_node:
        return "ghost"
    if in_state and on_node and not in_declared:
        return "undeclared"
    # declared and in state but not on the node is already `ghost` above; this
    # is left explicit rather than falling through to a wrong label.
    return "unknown"


ACTIONS = {
    "managed": "nothing to do",
    "pending": "IMPORT - do not apply; see docs/state-recovery.md",
    "missing": "the next apply will create it",
    "protected": "DELIBERATELY unmanaged - the factory needs it; see docs/unmanaged-vms.md",
    "orphan": "unmanaged guest; see docs/incident-orphan-vm.md",
    "ghost": "in state but gone from the node; destroyed outside Terraform",
    "undeclared": "managed but not in local.vms - the inventory disagrees",
    "unknown": "unclassified; report this, the tool is wrong",
}


def reconcile(
    declared: set[str], state: dict, node: dict, protected: set[int] | None = None
) -> list[tuple]:
    rows = []
    for name in sorted(set(declared) | set(state) | set(node)):
        v = verdict(name, declared, state, node, protected)
        rows.append((name, node.get(name), state.get(name), v))
    return rows


def render(rows: list[tuple]) -> str:
    if not rows:
        return "No VMs found in any of the three sources.\n"

    w = max(len(r[0]) for r in rows + [("VM", 0, 0, "")])
    out = [
        f"{'VM'.ljust(w)}  {'PROXMOX':>8}  {'STATE':>8}  VERDICT",
        f"{'-' * w}  {'-' * 8}  {'-' * 8}  {'-' * 40}",
    ]
    for name, node_id, state_id, v in rows:
        n = str(node_id) if node_id is not None else "-"
        s = str(state_id) if state_id is not None else ("-" if v in ("orphan", "missing", "pending", "protected") else "?")
        out.append(f"{name.ljust(w)}  {n:>8}  {s:>8}  {v}")

    out.append("")
    seen = sorted({r[3] for r in rows})
    for v in seen:
        out.append(f"  {v:<11} {ACTIONS[v]}")

    # A vmid that disagrees between the two is not a verdict of its own - the VM
    # is managed - but it is the thing that makes an import or an adoption
    # dangerous, so it is called out rather than left in two adjacent columns.
    drift = [
        (n, node_id, state_id)
        for n, node_id, state_id, v in rows
        if node_id is not None and state_id is not None and node_id != state_id
    ]
    if drift:
        out.append("")
        out.append("  vm_id disagrees between Proxmox and state:")
        for n, node_id, state_id in drift:
            out.append(f"    {n}: node={node_id} state={state_id}")
        out.append("  Setting vm_id from the wrong column rebuilds the guest (FEAT-002-A6).")

    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reconcile declared, managed and actual VM inventories.")
    p.add_argument("--qm-list", required=True, help="file holding `qm list` output")
    p.add_argument("--state", required=True, help="file holding `terraform show -json` output")
    p.add_argument(
        "--declared",
        help="file with one declared VM name per line "
        "(terraform console <<< 'keys(local.vms)'). Omit if the inventory is empty.",
    )
    # `qm list` includes templates, and a template is not an orphan - it is the
    # thing every VM is cloned from. Without this the tool reports one false
    # orphan per template on every run, and a reader who learns to skip rows is
    # a reader who will skip a real one. Found by running it, not by reading it.
    p.add_argument(
        "--template-vmids",
        help="comma-separated VMIDs to exclude as templates, e.g. "
        "the values of var.template_vmid_linux and var.template_vmid_windows",
    )
    # The same shape as --template-vmids and for a sharper reason: a template
    # reported as an orphan wastes a reader's attention, and the runner reported
    # as an orphan points them at `qm destroy --purge`.
    p.add_argument(
        "--protected-vmids",
        help="comma-separated VMIDs that are deliberately unmanaged, i.e. the "
        "value of var.protected_vm_ids. Reported as `protected` rather than "
        "as orphans. Read from variables.tf when omitted; pass an empty string "
        "to use none",
    )
    # Both lists are read from the configuration when the flag is absent,
    # because forgetting one had a cost and no signal. unmanaged-vms.md had to
    # say "Pass both ID lists" in bold, and the failure of not doing so is a
    # false `orphan` row - beside the runner, or beside a template - whose
    # action column points at a runbook that starts with `qm destroy --purge`.
    #
    # A tool that can read the answer should not be relying on a warning in a
    # document for the operator to have read.
    p.add_argument(
        "--variables-file",
        default=VARIABLES_TF,
        help="variables.tf to read the two ID lists from when the flags above "
        f"are omitted (default: {VARIABLES_TF})",
    )
    args = p.parse_args(argv)

    # An explicit empty string is a deliberate "none", and is not the same as
    # omitting the flag. That distinction is the whole reason `is None` is
    # tested rather than truthiness: `--protected-vmids ''` must not silently
    # reload the configuration's list.
    defaults = None
    if args.protected_vmids is None or args.template_vmids is None:
        try:
            defaults = terraform_defaults.read(args.variables_file)
        except OSError as exc:
            print(
                f"error: cannot read {args.variables_file} ({exc.strerror}), and "
                "--protected-vmids / --template-vmids were not both given. "
                "Guessing an empty list here would report the runner and the "
                "templates as orphans - pass the flags explicitly, or point "
                "--variables-file at the file.",
                file=sys.stderr,
            )
            return 2

    def ids(flag_value, variable_name, flag_name):
        """The flag if given, else the variable's default. None on a parse error."""
        if flag_value is not None:
            try:
                return {int(x) for x in flag_value.split(",") if x.strip()}
            except ValueError:
                print(
                    f"error: {flag_name} must be comma-separated integers",
                    file=sys.stderr,
                )
                return None
        found = terraform_defaults.list_default(defaults, variable_name)
        if found is None:
            print(
                f"error: no default for var.{variable_name} in "
                f"{args.variables_file}. Pass {flag_name} explicitly.",
                file=sys.stderr,
            )
            return None
        print(f"{flag_name} not given; read var.{variable_name} from "
              f"{os.path.basename(args.variables_file)}")
        return set(found)

    protected = ids(args.protected_vmids, "protected_vm_ids", "--protected-vmids")
    if protected is None:
        return 2

    if args.template_vmids is not None:
        try:
            templates = {int(x) for x in args.template_vmids.split(",") if x.strip()}
        except ValueError:
            print("error: --template-vmids must be comma-separated integers", file=sys.stderr)
            return 2
    else:
        # Two scalars rather than a list, so this one cannot reuse ids().
        # Missing is not an error here the way an absent protected list is: a
        # lab with one OS declares one template variable, and a null default is
        # a legitimate answer meaning "no template at that ID".
        templates = {
            found
            for name in ("template_vmid_linux", "template_vmid_windows")
            for found in [terraform_defaults.number_default(defaults, name)]
            if found is not None
        }
        print("--template-vmids not given; read var.template_vmid_linux and "
              f"var.template_vmid_windows from {os.path.basename(args.variables_file)}")

    try:
        with open(args.qm_list, encoding="utf-8") as f:
            node = parse_qm_list(f.read())
        with open(args.state, encoding="utf-8") as f:
            state = parse_state(json.load(f))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: --state is not valid JSON: {exc}", file=sys.stderr)
        return 2

    declared: set[str] = set()
    if args.declared:
        try:
            with open(args.declared, encoding="utf-8") as f:
                # Accepts a bare list per line or the bracketed form terraform
                # console prints, so the output can be pasted without editing.
                for line in f:
                    for name in re.findall(r'"([^"]+)"', line) or [line.strip()]:
                        name = name.strip().strip('[],')
                        if name:
                            declared.add(name)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    excluded = sorted(n for n, i in node.items() if i in templates)
    node = {n: i for n, i in node.items() if i not in templates}

    rows = reconcile(declared, state, node, protected)
    print(render(rows), end="")
    if excluded:
        print(f"\n  excluded as templates: {', '.join(excluded)}")
    return 0 if all(r[3] == "managed" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
