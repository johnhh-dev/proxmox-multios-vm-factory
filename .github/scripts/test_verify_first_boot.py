#!/usr/bin/env python3
"""Tests for verify_first_boot.py (OPS-004, #176).

The first case is not about the API. It asserts the two marker paths are the
literal strings intended — because the Windows one was wrong when this script
was first written, and wrong in the way that hides:

    "C:\\ProgramData\\vm-factory-firstboot.done"   as an ordinary literal
    -> 'C:\\ProgramData\\x0bm-factory-firstboot.done'

`\\v` is a vertical tab. The check would have reported every Windows guest as
missing its marker, forever — which looks exactly like OPS-004, the defect it
exists to find. A check whose failure mode is indistinguishable from its finding
is worse than no check.

Nothing here makes a network call. The API layer is stubbed; what is tested is
the decision and the reporting, which is the part that can be wrong without
anyone noticing.

Usage: python3 test_verify_first_boot.py
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verify_first_boot as checker  # noqa: E402


def inventory(**vms):
    return {
        name: {
            "os": spec.get("os", "linux"),
            "vm_id_actual": spec.get("vmid", 100),
            "ip_observed": "10.0.0.1",
            "arc_enabled": False,
            "arc_resource_name": None,
        }
        for name, spec in vms.items()
    }


def run(inv, present_for, token="root@pam!t=uuid"):
    """Run main() with the API stubbed to say which VMIDs have their marker."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "inv.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(inv, handle)

        calls = []

        def stub(endpoint, node, vmid, marker, tok, insecure):
            calls.append((vmid, marker))
            if isinstance(present_for, Exception):
                raise present_for
            return vmid in present_for

        real, checker.marker_present = checker.marker_present, stub
        real_env = os.environ.get("PROXMOX_API_TOKEN")
        if token is None:
            os.environ.pop("PROXMOX_API_TOKEN", None)
        else:
            os.environ["PROXMOX_API_TOKEN"] = token

        out, err = io.StringIO(), io.StringIO()
        try:
            argv = sys.argv
            sys.argv = [
                "verify_first_boot.py", "--inventory", path,
                "--endpoint", "https://node:8006", "--node", "pve",
            ]
            with redirect_stdout(out), redirect_stderr(err):
                code = checker.main()
        finally:
            sys.argv = argv
            checker.marker_present = real
            if real_env is None:
                os.environ.pop("PROXMOX_API_TOKEN", None)
            else:
                os.environ["PROXMOX_API_TOKEN"] = real_env
        return code, out.getvalue(), err.getvalue(), calls


class MarkerPathTests(unittest.TestCase):
    def test_the_windows_marker_is_the_path_we_mean(self):
        """The bug this file opens with. Asserted as a literal, not derived."""
        self.assertEqual(
            checker.MARKERS["windows"],
            "C:" + chr(92) + "ProgramData" + chr(92) + "vm-factory-firstboot.done",
        )

    def test_no_marker_contains_a_control_character(self):
        """The general form of it, so the next escape mistake fails here."""
        for os_name, marker in checker.MARKERS.items():
            with self.subTest(os=os_name):
                self.assertTrue(
                    all(ord(c) >= 32 for c in marker),
                    f"{os_name} marker holds a control character: {marker!r}",
                )

    def test_the_linux_marker_is_cloud_inits_own(self):
        self.assertEqual(
            checker.MARKERS["linux"], "/var/lib/cloud/instance/boot-finished"
        )


class ReportingTests(unittest.TestCase):
    def test_every_marker_present_is_quiet(self):
        code, out, _, _ = run(inventory(a={"vmid": 100}, b={"vmid": 101}), {100, 101})
        self.assertEqual(code, 0)
        self.assertNotIn("::warning::", out)
        self.assertIn("completed", out)

    def test_a_missing_marker_warns_and_does_not_fail(self):
        """Same doctrine as the Arc check: a guest created by this apply may
        still be working, and 'not yet' is indistinguishable from 'never'."""
        code, out, _, _ = run(inventory(a={"vmid": 100}, b={"vmid": 101}), {100})
        self.assertEqual(code, 0)
        self.assertIn("::warning::b:", out)
        self.assertNotIn("::warning::a:", out)

    def test_the_warning_names_the_defect_it_looks_like(self):
        _, out, _, _ = run(inventory(a={"vmid": 100}), set())
        self.assertIn("OPS-004", out)
        self.assertIn("#176", out)

    def test_each_os_is_asked_for_its_own_marker(self):
        _, _, _, calls = run(
            inventory(lin={"vmid": 100, "os": "linux"}, win={"vmid": 101, "os": "windows"}),
            {100, 101},
        )
        asked = dict(calls)
        self.assertEqual(asked[100], checker.MARKERS["linux"])
        self.assertEqual(asked[101], checker.MARKERS["windows"])

    def test_a_vm_with_no_id_is_reported_not_skipped_silently(self):
        inv = inventory(a={"vmid": 100})
        inv["a"]["vm_id_actual"] = None
        code, out, _, calls = run(inv, set())
        self.assertEqual(code, 0)
        self.assertIn("::warning::a: no VM id", out)
        self.assertEqual(calls, [])

    def test_an_unknown_os_is_reported_rather_than_assumed_linux(self):
        inv = inventory(a={"vmid": 100})
        inv["a"]["os"] = "plan9"
        _, out, _, calls = run(inv, set())
        self.assertIn("unknown os", out)
        self.assertEqual(calls, [])


class UnusableTests(unittest.TestCase):
    """Every way of not being able to check must be distinguishable from
    checking and finding everything fine."""

    def test_an_empty_token_exits_2(self):
        code, _, err, _ = run(inventory(a={"vmid": 100}), {100}, token=None)
        self.assertEqual(code, 2)
        self.assertIn("::error::", err)

    def test_an_api_that_cannot_be_used_exits_2(self):
        code, out, err, _ = run(
            inventory(a={"vmid": 100}), checker.Unusable("the API refused the token (401)")
        )
        self.assertEqual(code, 2)
        # On stderr, and carrying what went wrong - so a run that could not
        # check says so rather than printing a reassuring summary.
        self.assertIn("::error::first-boot check:", err)
        self.assertIn("the API refused the token", err)
        self.assertNotIn("completed", out)

    def test_an_unreadable_inventory_exits_2(self):
        argv = sys.argv
        os.environ["PROXMOX_API_TOKEN"] = "t"
        out, err = io.StringIO(), io.StringIO()
        try:
            sys.argv = [
                "verify_first_boot.py", "--inventory", "/definitely/not/here.json",
                "--endpoint", "https://node:8006", "--node", "pve",
            ]
            with redirect_stdout(out), redirect_stderr(err):
                code = checker.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 2)

    def test_an_empty_inventory_is_not_a_failure(self):
        code, out, _, _ = run({}, set())
        self.assertEqual(code, 0)
        self.assertIn("nothing to check", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
