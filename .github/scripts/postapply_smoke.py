#!/usr/bin/env python3
"""Decide whether an apply actually delivered what it claimed (KAN-017-A5).

`terraform apply` reporting success means the provider's API calls returned. It
does not mean a guest booted, and it says nothing at all about Azure - Arc
onboarding happens inside the guest, minutes after Terraform has finished and
gone home. docs/incident-arc-onboarding.md is the runbook for the failure that
leaves behind, and its first line is that the apply is green and nothing is red.

This is the check that was missing. Three questions, in the order they can be
answered:

| Check | Question | Verdict on failure |
|-------|----------|--------------------|
| inventory | is every declared VM in state, and nothing else? | **fail** |
| guest | did the guest boot far enough to report an address? | **fail** |
| arc | is the machine in Azure, and is anything there that should not be? | **warn** - see below |

## Why Arc is a warning and the other two are not

Not squeamishness. "Not yet" and "never" are indistinguishable from here, and
docs/arc-cleanup.md already settled that exact ambiguity for the mirror-image
case: an absent Arc resource is a `::warning::` there for the same reason.

The caller polls before giving up, so a machine which is merely slow is present
by the time this runs. What survives that poll stays undecidable: a Windows
first boot that installs an agent and reboots can outlast any deadline worth
holding the terraform-lab-state concurrency group for.

And a failure here would be actively misleading. Re-running the apply does not
retry onboarding - a vendor-data change replaces the snippet and leaves the VM
alone (BUG-012-A2) - so a red workflow would invite the one action that cannot
help. The warning says so and names the runbook instead.

## Where --arc-present comes from

The apply workflow queries Azure with one `az resource list` for the whole
resource group and passes the result here. It polls first, so a machine that is
merely slow is usually present by the time this runs; the loop exits as soon as
every expected name appears, so a steady apply pays one call and no wait.

The session is `.github/scripts/az_session.sh`, shared with arc-cleanup rather
than copied - BUG-004 exists because an Arc block was duplicated between two
workflows and one copy carried a defect for months. Its exit 2 means Arc is not
configured for this lab, and the workflow then passes no --arc-present at all,
which is how this script is told the question was not asked. That is a different
answer from asking and finding nothing.

## Why the other two are hard failures

**Inventory** is decidable with no API call at all: either the resource is in
state or it is not.

**Guest availability** looks softer than it is. main.tf enables the guest agent
and the provider *waits for it during create* (FEAT-002-A3), failing the apply
after a timeout if it never answers. So a VM sitting in state with no observed
address, after an apply that succeeded, is genuinely anomalous rather than
merely early.

The one case it could be wrong about: a guest whose agent answered during create
with nothing but a loopback address, so `ip_observed` resolved to null while the
guest was in fact fine. That is worth a red run and a look, which is the whole
point.

Usage:
  postapply_smoke.py --inventory vm_inventory.json --state-list state-list.txt
                     [--arc-present arc_present.txt]

Exit codes: 0 everything checked passed, 1 a check failed, 2 an input could not
be read - which is never read as "nothing to check".
"""

import argparse
import json
import sys

VM_RESOURCE = "proxmox_virtual_environment_vm.vm"

# The contract with outputs.tf. Every one of these is read below, and every one
# used to be read with .get() - which degrades silently, and one of them
# degrades silently into doing nothing at all.
#
# Drop or rename `arc_resource_name` in outputs.tf and the expected set becomes
# empty, so this reports "no VM asks for onboarding", the poll in
# arc_missing.py never waits, and Arc verification is switched off with no
# error anywhere. `arc_enabled` has the same shape. Only `ip_observed` fails
# loudly on its own, by reporting every VM as unreachable.
#
# Checked at the boundary rather than asserted in a terraform test, because the
# values that matter are computed - ip_observed reads the guest agent - so a
# plan-time assertion could only see them as unknown. This sees the real
# document, on every run.
REQUIRED_KEYS = ("ip_observed", "arc_enabled", "arc_resource_name")


class Undecidable(Exception):
    """An input could not be turned into an answer."""


def parse_inventory(text: str) -> dict:
    """The `vm_inventory` output, as `terraform output -json` renders it.

    `terraform output -json` and not `terraform show -json`: plan and state JSON
    both carry a `variables` block holding every input in cleartext (SEC-002),
    and this check has no business producing that file. Outputs are declared, and
    none of vm_inventory's fields is a secret.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Undecidable("vm_inventory is not valid JSON (%s)" % exc.msg) from exc

    if not isinstance(parsed, dict):
        raise Undecidable("vm_inventory is not a JSON object")

    for name, vm in parsed.items():
        if not isinstance(vm, dict):
            raise Undecidable("vm_inventory entry '%s' is not an object" % name)
        missing = [key for key in REQUIRED_KEYS if key not in vm]
        if missing:
            raise Undecidable(
                "vm_inventory entry '%s' is missing %s. outputs.tf and this "
                "check have to agree: a missing key would otherwise be read as "
                "an absent value, and for the arc_* pair that silently means "
                "'no VM asks for onboarding'."
                % (name, ", ".join(missing))
            )
    return parsed


def parse_state_list(text: str) -> set:
    """VM names from `terraform state list`.

    Only the VM resources matter here. The snippet files and the Arc markers are
    in state too and say nothing about whether a guest exists.
    """
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(VM_RESOURCE + "["):
            continue
        inner = line[len(VM_RESOURCE) + 1: -1]
        names.add(inner.strip('"'))
    return names


def parse_present(text: str) -> set:
    return {line.strip() for line in text.splitlines() if line.strip()}


def decide(inventory: dict, in_state: set, arc_present) -> tuple:
    """Return (exit code, [lines to print]).

    `arc_present` is None when Azure was not queried - no credentials
    configured, which is a legitimate lab and not a failure. That is the same
    policy the cleanup path takes, and it is a `::notice::` there too.
    """
    lines = []
    failed = False

    declared = set(inventory)

    # ---- inventory ----------------------------------------------------------
    missing = sorted(declared - in_state)
    for name in missing:
        failed = True
        lines.append(
            "::error::%s is declared in the inventory but has no VM resource in "
            "state after the apply. The apply reported success without creating "
            "it." % name
        )

    extra = sorted(in_state - declared)
    for name in extra:
        failed = True
        lines.append(
            "::error::%s has a VM resource in state but is not in the "
            "inventory. The apply reported success without destroying it." % name
        )

    if not missing and not extra:
        lines.append("inventory: %d VM(s), all present in state." % len(declared))

    # The assigned IDs, in the log, because Proxmox reuses them and nothing said
    # so. On 2026-08-30 VMID 101 was win-srv-01; after a destroy and an apply it
    # was ubuntu-dhcp-01. Terraform is right either way - vm_id is unset, so
    # Proxmox picks the lowest free one - and anything outside Terraform that
    # remembered "101" now points at a different machine.
    #
    # This cannot detect the reuse: the check has no memory, and a VM with no
    # declared vm_id has nothing to be compared against. What it can do is put
    # the number where a human comparing two runs will see it. Declaring vm_id
    # is the actual fix (FEAT-002).
    assigned = sorted(
        (vm.get("vm_id_actual"), name)
        for name, vm in inventory.items()
        if name in in_state and vm.get("vm_id_actual") is not None
    )
    if assigned:
        lines.append(
            "  ids: " + ", ".join("%s=%s" % (vmid, name) for vmid, name in assigned)
        )

    # ---- guest availability -------------------------------------------------
    # Only for VMs that are actually in state. A VM the inventory check already
    # failed on has no address to report, and a second error about the same VM
    # tells the reader nothing new.
    unreachable = []
    for name in sorted(declared & in_state):
        observed = inventory[name].get("ip_observed")
        # null is the shape outputs.tf produces when the agent reported nothing
        # usable. Anything that is not a non-blank string is the same answer.
        if not isinstance(observed, str) or not observed.strip():
            unreachable.append(name)
    for name in unreachable:
        failed = True
        lines.append(
            "::error::%s reported no address through the guest agent. The "
            "provider waits for the agent during create, so a VM in state "
            "without one has either failed to boot or lost its agent." % name
        )
    reachable = len(declared & in_state) - len(unreachable)
    if reachable:
        lines.append("guest: %d VM(s) reporting an address." % reachable)

    # ---- arc ----------------------------------------------------------------
    expected = {
        name: vm["arc_resource_name"]
        for name, vm in inventory.items()
        if vm.get("arc_enabled") and vm.get("arc_resource_name")
    }

    if not expected:
        lines.append("arc: no VM asks for onboarding.")
    elif arc_present is None:
        lines.append(
            "::notice::arc: no Azure credentials configured, so onboarding was "
            "not verified for %d VM(s)." % len(expected)
        )
    else:
        absent = sorted(
            "%s (as %s)" % (name, resource)
            for name, resource in expected.items()
            if resource not in arc_present
        )
        for entry in absent:
            lines.append(
                "::warning::arc: %s is not in Azure. It may still be onboarding, "
                "or it may have failed - the two are indistinguishable from "
                "here. Re-running the apply will NOT retry onboarding; see "
                "docs/incident-arc-onboarding.md." % entry
            )
        found = len(expected) - len(absent)
        if found:
            lines.append("arc: %d of %d machine(s) present in Azure." % (found, len(expected)))

        # The other direction, which nothing looked at. A machine in the
        # resource group that no VM in the inventory claims is an orphan: the
        # guest is gone and its Arc registration is not, which is the whole
        # subject of docs/arc-cleanup.md.
        #
        # It matters because cleanup runs *before* the destroy and tolerates an
        # absent resource with a warning (BUG-019-A3). So a cleanup that aimed
        # at the wrong name reports a warning nobody reads, the destroy
        # proceeds, and the registration outlives the VM - blocking the next
        # onboarding under that name with an error that names neither.
        #
        # Reported rather than acted on. This does not know whether a machine
        # belongs to a VM this factory never built - the resource group holds
        # what it holds - so it names them and stops.
        orphans = sorted(arc_present - set(expected.values()))
        for name in orphans:
            lines.append(
                "::warning::arc: '%s' is in the resource group and no VM in the "
                "inventory claims it. Either it belongs to something this "
                "factory did not build, or a destroy left it behind - see "
                "docs/arc-cleanup.md." % name
            )

    return (1 if failed else 0), lines


def read(path: str, what: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise Undecidable("could not read %s from %s (%s)" % (what, path, exc.strerror)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--state-list", required=True)
    parser.add_argument(
        "--arc-present",
        default=None,
        help="Arc machine names found in Azure, one per line. Omit when Azure "
             "was not queried - that is reported, not treated as absence.",
    )
    args = parser.parse_args()

    try:
        inventory = parse_inventory(read(args.inventory, "the VM inventory"))
        in_state = parse_state_list(read(args.state_list, "the state list"))
        present = None
        if args.arc_present is not None:
            present = parse_present(read(args.arc_present, "the Arc machine list"))
    except Undecidable as exc:
        # Never "nothing to check". An unreadable input is the one case where
        # staying silent would turn this check into decoration.
        print("::error::post-apply smoke: %s" % exc, file=sys.stderr)
        return 2

    code, lines = decide(inventory, in_state, present)
    for line in lines:
        print(line)
    if code:
        print(
            "::error::post-apply smoke tests failed. The apply already happened "
            "- this reports what it left behind, it does not roll anything back."
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
