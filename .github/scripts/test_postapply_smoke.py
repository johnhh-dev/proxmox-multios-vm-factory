#!/usr/bin/env python3
"""Tests for postapply_smoke.py (KAN-017-A5).

Two properties, and the second is the one that matters. A check that blocks
everything is as useless as one that blocks nothing, so every failing case here
has a passing twin: the healthy apply must be silent about the thing the broken
one shouts about.

The Arc cases carry an extra rule of their own. An absent machine must produce a
warning and **not** a failure, for the reason docs/arc-cleanup.md already
settled for the mirror-image case - "not yet" and "never" are indistinguishable
from here, and a red run would invite a re-apply, which cannot retry onboarding.

Usage: python3 test_postapply_smoke.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "postapply_smoke.py")


def vm(ip="192.168.10.20", arc=False, arc_name=None, vmid=101):
    return {
        "name": "x",
        "os": "linux",
        "cores": 2,
        "memory_mb": 4096,
        "ip": "dhcp",
        "ip_observed": ip,
        "arc_enabled": arc,
        "arc_resource_name": arc_name,
        "vm_id_actual": vmid,
    }


def run(inventory, state_names, arc_present=None, arc_omitted=True):
    with tempfile.TemporaryDirectory() as tmp:
        inv = os.path.join(tmp, "inv.json")
        with open(inv, "w", encoding="utf-8") as handle:
            json.dump(inventory, handle)

        state = os.path.join(tmp, "state.txt")
        with open(state, "w", encoding="utf-8") as handle:
            for name in state_names:
                handle.write('proxmox_virtual_environment_vm.vm["%s"]\n' % name)
            # Real output carries other resources too, and they must be ignored
            # rather than mistaken for guests.
            handle.write("terraform_data.vm_factory_config\n")
            handle.write('proxmox_virtual_environment_file.vendor_data["a"]\n')
            handle.write('terraform_data.arc_registration["a"]\n')

        argv = [sys.executable, SCRIPT, "--inventory", inv, "--state-list", state]
        if not arc_omitted:
            present = os.path.join(tmp, "arc.txt")
            with open(present, "w", encoding="utf-8") as handle:
                handle.write("\n".join(arc_present or []))
            argv += ["--arc-present", present]
        return subprocess.run(argv, capture_output=True, text=True)


def check(name, condition, detail, failures):
    if condition:
        print("ok   %s" % name)
    else:
        print("FAIL %s: %s" % (name, detail))
        failures.append(name)


def main() -> int:
    failures = []

    # ---- the healthy apply, which must say nothing alarming -----------------
    r = run({"a": vm(), "b": vm()}, ["a", "b"])
    check(
        "a healthy apply passes",
        r.returncode == 0 and "::error::" not in r.stdout and "::warning::" not in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )
    check(
        "resources that are not VMs are ignored",
        "inventory: 2 VM(s)" in r.stdout,
        "out=%r" % r.stdout,
        failures,
    )

    # Proxmox reuses IDs, and nothing said so until it mattered: VMID 101 was
    # win-srv-01 and then, after a destroy and an apply, ubuntu-dhcp-01.
    r = run({"a": vm(vmid=101), "b": vm(vmid=102)}, ["a", "b"])
    check(
        "the assigned ids are reported",
        "ids: 101=a, 102=b" in r.stdout,
        f"out={r.stdout!r}",
        failures,
    )

    inv = {"a": vm()}
    inv["a"]["vm_id_actual"] = None
    r = run(inv, ["a"])
    check(
        "a VM with no assigned id is omitted rather than printed as None",
        r.returncode == 0 and "None" not in r.stdout,
        f"rc={r.returncode} out={r.stdout!r}",
        failures,
    )

    # ---- inventory ----------------------------------------------------------
    r = run({"a": vm(), "b": vm()}, ["a"])
    check(
        "a declared VM missing from state fails",
        r.returncode == 1 and "b is declared in the inventory" in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )
    check(
        "the missing VM is not also reported as unreachable",
        "b reported no address" not in r.stdout,
        "out=%r" % r.stdout,
        failures,
    )

    r = run({"a": vm()}, ["a", "ghost"])
    check(
        "a VM in state but not in the inventory fails",
        r.returncode == 1 and "ghost has a VM resource in state" in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )

    # ---- guest availability -------------------------------------------------
    for label, value in (("null", None), ("empty", ""), ("blank", "   ")):
        r = run({"a": vm(ip=value)}, ["a"])
        check(
            "an %s observed address fails" % label,
            r.returncode == 1 and "a reported no address" in r.stdout,
            "rc=%s out=%r" % (r.returncode, r.stdout),
            failures,
        )

    # ---- arc ----------------------------------------------------------------
    r = run({"a": vm(arc=True, arc_name="a-arc")}, ["a"], arc_present=["a-arc"], arc_omitted=False)
    check(
        "an onboarded machine passes quietly",
        r.returncode == 0 and "1 of 1 machine(s) present" in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )

    r = run({"a": vm(arc=True, arc_name="a-arc")}, ["a"], arc_present=[], arc_omitted=False)
    check(
        "an absent machine warns and does NOT fail",
        r.returncode == 0 and "::warning::arc: a (as a-arc) is not in Azure" in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )
    check(
        "the warning says a re-apply will not help",
        "will NOT retry onboarding" in r.stdout
        and "incident-arc-onboarding.md" in r.stdout,
        "out=%r" % r.stdout,
        failures,
    )

    # BUG-019 is the whole reason arc_resource_name exists as a separate field:
    # the machine is named for the override, not for the VM.
    r = run(
        {"a": vm(arc=True, arc_name="different-in-azure")},
        ["a"],
        arc_present=["different-in-azure"],
        arc_omitted=False,
    )
    check(
        "the Azure name is matched, not the VM name",
        r.returncode == 0 and "::warning::" not in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )

    # The other direction, which nothing looked at until a destroy removed two
    # VMs and nobody could say whether their registrations went with them.
    r = run(
        {"a": vm(arc=True, arc_name="a-arc")},
        ["a"],
        arc_present=["a-arc", "long-gone-01"],
        arc_omitted=False,
    )
    check(
        "a machine no VM claims is reported",
        r.returncode == 0 and "'long-gone-01' is in the resource group" in r.stdout,
        f"rc={r.returncode} out={r.stdout!r}",
        failures,
    )
    check(
        "an orphan warns rather than failing, like an absent one",
        r.returncode == 0,
        f"rc={r.returncode}",
        failures,
    )
    check(
        "the machine that is claimed is not reported as an orphan",
        "'a-arc' is in the resource group" not in r.stdout,
        f"out={r.stdout!r}",
        failures,
    )

    # A lab with no Arc VM at all must not report every machine in the group as
    # an orphan - the resource group holds what it holds, and this factory is
    # not the only thing that may put something in it.
    r = run({"a": vm()}, ["a"], arc_present=["someone-elses-01"], arc_omitted=False)
    check(
        "with no Arc VM declared, nothing is called an orphan",
        r.returncode == 0 and "orphan" not in r.stdout and "is in the resource group" not in r.stdout,
        f"rc={r.returncode} out={r.stdout!r}",
        failures,
    )

    r = run({"a": vm(arc=True, arc_name="a-arc")}, ["a"])
    check(
        "no Azure query is a notice, not an absence",
        r.returncode == 0
        and "::notice::arc: no Azure credentials" in r.stdout
        and "::warning::" not in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )

    r = run({"a": vm()}, ["a"], arc_present=[], arc_omitted=False)
    check(
        "a lab with no Arc VM says so rather than reporting zero of zero",
        r.returncode == 0 and "arc: no VM asks for onboarding" in r.stdout,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )

    # ---- the contract with outputs.tf ---------------------------------------
    # Each key dropped in turn. The arc_* pair is the reason this exists: read
    # with .get(), a missing one made the expected set empty, so this reported
    # "no VM asks for onboarding" and the poll never waited - Arc verification
    # switched off with no error anywhere.
    for key in ("ip_observed", "arc_enabled", "arc_resource_name"):
        entry = vm(arc=True, arc_name="a-arc")
        del entry[key]
        result = run({"a": entry}, ["a"])
        check(
            f"a vm_inventory entry missing {key} exits 2",
            result.returncode == 2 and key in result.stderr,
            f"rc={result.returncode} err={result.stderr!r}",
            failures,
        )

    # And the shape that must still pass: arc off, so arc_resource_name is null
    # rather than absent. outputs.tf emits exactly that.
    entry = vm(arc=False, arc_name=None)
    result = run({"a": entry}, ["a"])
    check(
        "a null arc_resource_name is a value, not a missing key",
        result.returncode == 0,
        f"rc={result.returncode} out={result.stdout!r} err={result.stderr!r}",
        failures,
    )

    # ---- unreadable input ---------------------------------------------------
    # The one case where staying quiet would turn the whole check into
    # decoration: an input that could not be read is not "nothing to check".
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state.txt")
        open(state, "w").close()
        r = subprocess.run(
            [
                sys.executable, SCRIPT,
                "--inventory", os.path.join(tmp, "absent.json"),
                "--state-list", state,
            ],
            capture_output=True, text=True,
        )
    check(
        "an unreadable inventory exits 2 rather than passing",
        r.returncode == 2 and "::error::post-apply smoke:" in r.stderr,
        "rc=%s err=%r" % (r.returncode, r.stderr),
        failures,
    )

    with tempfile.TemporaryDirectory() as tmp:
        inv = os.path.join(tmp, "inv.json")
        with open(inv, "w", encoding="utf-8") as handle:
            handle.write("not json at all")
        state = os.path.join(tmp, "state.txt")
        open(state, "w").close()
        r = subprocess.run(
            [sys.executable, SCRIPT, "--inventory", inv, "--state-list", state],
            capture_output=True, text=True,
        )
    check(
        "an unparseable inventory exits 2 rather than passing",
        r.returncode == 2,
        "rc=%s err=%r" % (r.returncode, r.stderr),
        failures,
    )

    # An empty lab is a legitimate answer, not a failure.
    r = run({}, [])
    check(
        "an empty inventory with empty state passes",
        r.returncode == 0,
        "rc=%s out=%r" % (r.returncode, r.stdout),
        failures,
    )

    if failures:
        print("\n%d case(s) failed: %s" % (len(failures), ", ".join(failures)))
        return 1
    print("\nAll cases pass: the two decidable checks block, and Arc warns without blocking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
