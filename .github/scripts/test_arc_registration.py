#!/usr/bin/env python3
"""Tests for Arc machine name resolution (BUG-019).

`az resource delete` was given the Proxmox VM name. The Arc resource is only
named after the VM by default - `arc = { enabled = true, resource_name = "..." }`
overrides it, and the override exists so the two need not match. Where they
diverged, the delete targeted a name that does not exist, `|| true` hid it, and
the orphaned Arc machine blocked re-onboarding under the same name.

So the divergent case is the one that matters here, and it is tested through
both extractors rather than against arc_registration.py alone: the defect was in
what the extractors *returned*, and that is what the workflow deletes.

test_arc_extractors.py keeps the marker-free cases, which still resolve by VM
name. This file covers documents that carry markers.

Usage: python3 test_arc_registration.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import arc_registration  # noqa: E402
import extract_arc_names_from_plan as plan_extractor  # noqa: E402
import extract_arc_names_from_state as state_extractor  # noqa: E402


def vm_change(name, actions):
    return {
        "address": f'proxmox_virtual_environment_vm.vm["{name}"]',
        "type": "proxmox_virtual_environment_vm",
        "name": "vm",
        "change": {
            "actions": actions,
            "before": None if actions == ["create"] else {"name": name},
            "after": None if "delete" in actions and "create" not in actions else {"name": name},
        },
    }


def marker_change(vm_name, resource_name, actions=None, after_resource_name=None):
    """One terraform_data.arc_registration entry in a plan's resource_changes."""
    return {
        "address": f'terraform_data.arc_registration["{vm_name}"]',
        "type": "terraform_data",
        "name": "arc_registration",
        "change": {
            "actions": actions or ["delete"],
            "before": {"input": {"vm_name": vm_name, "resource_name": resource_name}},
            "after": {
                "input": {
                    "vm_name": vm_name,
                    "resource_name": after_resource_name or resource_name,
                }
            },
        },
    }


def vm_state(name):
    return {
        "type": "proxmox_virtual_environment_vm",
        "name": "vm",
        "values": {"name": name},
    }


def marker_state(vm_name, resource_name):
    return {
        "type": "terraform_data",
        "name": "arc_registration",
        "values": {"input": {"vm_name": vm_name, "resource_name": resource_name}},
    }


def state_doc(resources, child_resources=None):
    root = {"resources": resources}
    if child_resources is not None:
        root["child_modules"] = [{"resources": child_resources}]
    return {"values": {"root_module": root}}


class PlanTests(unittest.TestCase):
    def test_overridden_resource_name_is_used(self):
        """The case the bug is made of."""
        plan = {
            "resource_changes": [
                vm_change("win-srv-01", ["delete"]),
                marker_change("win-srv-01", "win-srv-01-arc"),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"win-srv-01-arc"})

    def test_default_resource_name_still_equals_the_vm_name(self):
        plan = {
            "resource_changes": [
                vm_change("ubuntu-01", ["delete"]),
                marker_change("ubuntu-01", "ubuntu-01"),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"ubuntu-01"})

    def test_replacement_uses_the_overridden_name(self):
        plan = {
            "resource_changes": [
                vm_change("win-srv-01", ["delete", "create"]),
                marker_change("win-srv-01", "win-srv-01-arc", actions=["no-op"]),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"win-srv-01-arc"})

    def test_a_vm_with_no_marker_is_skipped_once_markers_exist(self):
        """No marker means Arc is disabled for that VM, so its machine is not
        ours to delete. Falling back per-VM here would reintroduce the bug for
        exactly the VMs the marker was added for."""
        plan = {
            "resource_changes": [
                vm_change("win-srv-01", ["delete"]),
                marker_change("win-srv-01", "win-srv-01-arc"),
                vm_change("no-arc-01", ["delete"]),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"win-srv-01-arc"})

    def test_a_marker_for_a_vm_that_stays_is_not_a_target(self):
        plan = {
            "resource_changes": [
                vm_change("win-srv-01", ["delete"]),
                marker_change("win-srv-01", "win-srv-01-arc"),
                vm_change("ubuntu-01", ["no-op"]),
                marker_change("ubuntu-01", "ubuntu-01-arc", actions=["no-op"]),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"win-srv-01-arc"})

    def test_a_renamed_arc_resource_deletes_under_the_old_name(self):
        """Azure knows the machine by what it registered as, not by what the
        configuration now says it should be."""
        plan = {
            "resource_changes": [
                vm_change("win-srv-01", ["delete", "create"]),
                marker_change(
                    "win-srv-01",
                    "old-arc-name",
                    actions=["delete", "create"],
                    after_resource_name="new-arc-name",
                ),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"old-arc-name"})

    def test_a_marker_being_created_carries_only_an_after(self):
        plan = {
            "resource_changes": [
                vm_change("win-srv-01", ["delete"]),
                marker_change("win-srv-01", "win-srv-01-arc"),
                {
                    "type": "terraform_data",
                    "name": "arc_registration",
                    "change": {
                        "actions": ["create"],
                        "before": None,
                        "after": {
                            "input": {"vm_name": "new-01", "resource_name": "new-01-arc"}
                        },
                    },
                },
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"win-srv-01-arc"})

    def test_several_vms_with_mixed_overrides(self):
        plan = {
            "resource_changes": [
                vm_change("win-srv-01", ["delete"]),
                marker_change("win-srv-01", "win-srv-01-arc"),
                vm_change("ubuntu-01", ["delete"]),
                marker_change("ubuntu-01", "ubuntu-01"),
                vm_change("ubuntu-02", ["create"]),
                marker_change("ubuntu-02", "ubuntu-02", actions=["create"]),
            ]
        }
        self.assertEqual(
            plan_extractor.extract_names(plan), {"win-srv-01-arc", "ubuntu-01"}
        )


class StateTests(unittest.TestCase):
    def test_overridden_resource_name_is_used(self):
        state = state_doc(
            [vm_state("win-srv-01"), marker_state("win-srv-01", "win-srv-01-arc")]
        )
        self.assertEqual(state_extractor.extract_names(state), {"win-srv-01-arc"})

    def test_markers_are_found_in_child_modules(self):
        """FEAT-007 will move the factory into a module; the marker goes with it."""
        state = state_doc(
            [],
            child_resources=[
                vm_state("win-srv-01"),
                marker_state("win-srv-01", "win-srv-01-arc"),
            ],
        )
        self.assertEqual(state_extractor.extract_names(state), {"win-srv-01-arc"})

    def test_a_marker_in_a_sibling_module_still_names_the_vm(self):
        state = state_doc(
            [vm_state("win-srv-01")],
            child_resources=[marker_state("win-srv-01", "win-srv-01-arc")],
        )
        self.assertEqual(state_extractor.extract_names(state), {"win-srv-01-arc"})

    def test_a_vm_with_arc_disabled_is_skipped(self):
        state = state_doc(
            [
                vm_state("win-srv-01"),
                marker_state("win-srv-01", "win-srv-01-arc"),
                vm_state("no-arc-01"),
            ]
        )
        self.assertEqual(state_extractor.extract_names(state), {"win-srv-01-arc"})

    def test_a_marker_without_its_vm_is_not_a_target(self):
        """Nothing to destroy means nothing to de-register."""
        state = state_doc([marker_state("win-srv-01", "win-srv-01-arc")])
        self.assertEqual(state_extractor.extract_names(state), set())


class MarkerIdentityTests(unittest.TestCase):
    def test_the_validation_marker_is_not_an_arc_marker(self):
        """checks.tf uses terraform_data too, so the local name has to match."""
        self.assertFalse(
            arc_registration.is_marker(
                {"type": "terraform_data", "name": "vm_factory_config"}
            )
        )
        self.assertTrue(
            arc_registration.is_marker(
                {"type": "terraform_data", "name": "arc_registration"}
            )
        )

    def test_a_foreign_resource_of_the_right_name_is_not_a_marker(self):
        self.assertFalse(
            arc_registration.is_marker(
                {"type": "null_resource", "name": "arc_registration"}
            )
        )

    def test_malformed_markers_yield_no_mapping(self):
        """A destructive Azure call is downstream, so a name is only accepted
        when the document actually carries one as a non-empty string."""
        for values in (
            None,
            {},
            {"input": None},
            {"input": {}},
            {"input": {"vm_name": "a"}},
            {"input": {"resource_name": "b"}},
            {"input": {"vm_name": "a", "resource_name": ""}},
            {"input": {"vm_name": "a", "resource_name": "   "}},
            {"input": {"vm_name": "  ", "resource_name": "b"}},
            {"input": {"vm_name": 42, "resource_name": "b"}},
            {"input": {"vm_name": "a", "resource_name": 42}},
        ):
            with self.subTest(values=values):
                marker = {
                    "type": "terraform_data",
                    "name": "arc_registration",
                    "values": values,
                }
                self.assertEqual(arc_registration.from_state_resources([marker]), {})

    def test_names_are_stripped(self):
        marker = marker_state(" win-srv-01 ", " win-srv-01-arc ")
        self.assertEqual(
            arc_registration.from_state_resources([marker]),
            {"win-srv-01": "win-srv-01-arc"},
        )


class FallbackTests(unittest.TestCase):
    """A plan or state written before arc.tf existed carries no markers.

    Returning nothing there would silently stop cleaning anything up, so the
    previous behaviour applies until the first apply writes the markers.
    """

    def test_plan_without_markers_falls_back_to_vm_names(self):
        plan = {"resource_changes": [vm_change("ubuntu-01", ["delete"])]}
        self.assertEqual(plan_extractor.extract_names(plan), {"ubuntu-01"})

    def test_state_without_markers_falls_back_to_vm_names(self):
        self.assertEqual(
            state_extractor.extract_names(state_doc([vm_state("ubuntu-01")])),
            {"ubuntu-01"},
        )

    def test_the_fallback_is_per_document_not_per_vm(self):
        """One marker anywhere means the document is post-arc.tf, so a VM
        without one has Arc disabled rather than an unknown name."""
        plan = {
            "resource_changes": [
                vm_change("ubuntu-01", ["delete"]),
                vm_change("win-srv-01", ["delete"]),
                marker_change("win-srv-01", "win-srv-01-arc"),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"win-srv-01-arc"})

    def test_resolve_is_the_only_place_the_fallback_lives(self):
        self.assertEqual(arc_registration.resolve({"a", "b"}, {}), {"a", "b"})
        self.assertEqual(arc_registration.resolve({"a", "b"}, {"a": "a-arc"}), {"a-arc"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
