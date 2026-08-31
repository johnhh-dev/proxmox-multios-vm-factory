#!/usr/bin/env python3
"""How many expected Arc machines are not in Azure yet.

The post-apply smoke step polls before it gives up, so that a machine which is
merely slow is present by the time the check runs. That poll used to compare
*counts*:

    EXPECTED=$(... number of arc_enabled VMs ...)
    FOUND=$(grep -c . arc-present.txt)
    if [ "$FOUND" -ge "$EXPECTED" ]; then break; fi

`az resource list` returns every Microsoft.HybridCompute machine in the resource
group, not only the ones this apply expects. So a resource group holding three
machines from elsewhere satisfied `FOUND >= EXPECTED` on the first iteration
while the one machine that had just been built was absent - the loop exited
immediately and the warning that followed was exactly the noise the poll exists
to prevent.

Demonstrated before fixing:

    EXPECTED (what the loop computed): 1
    FOUND    (what the loop counted) : 3
    loop breaks immediately?           True
    ...and the script then warns that new-vm is not in Azure.

Names, then. And read through postapply_smoke's own parsers rather than
reimplemented here, because a poll that waits for a different set than the check
reports on is the same defect wearing a different shape.

Usage: arc_missing.py --inventory vm_inventory.json --present arc_present.txt
Prints one number: how many expected machines are absent. Exit 2 if an input
cannot be read, because "0 missing" is the answer that stops the loop.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import postapply_smoke  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--present", required=True)
    args = parser.parse_args()

    try:
        inventory = postapply_smoke.parse_inventory(
            postapply_smoke.read(args.inventory, "the VM inventory")
        )
        present = postapply_smoke.parse_present(
            postapply_smoke.read(args.present, "the Arc machine list")
        )
    except postapply_smoke.Undecidable as exc:
        print(f"::error::arc poll: {exc}", file=sys.stderr)
        return 2

    # The same rule postapply_smoke.decide() applies: a VM counts as expecting a
    # machine only when Arc is on *and* it has a name to be onboarded under. A
    # count of arc_enabled alone would wait for a machine nothing will create.
    expected = {
        vm["arc_resource_name"]
        for vm in inventory.values()
        if vm.get("arc_enabled") and vm.get("arc_resource_name")
    }
    print(len(expected - present))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
