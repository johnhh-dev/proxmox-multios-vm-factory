#!/usr/bin/env python3
"""Unit tests for the Azure Arc cleanup target extractors (CHORE-002).

These two scripts decide which machines get deleted from Azure. A parsing
regression here is a silent destructive action, not a failed build, so both
directions are tested: every VM that should be a target is returned, and
nothing else ever is.

BUG-019 has landed and the expectations changed with it, which is what these
tests were for. The history is worth keeping: CHORE-002-A3 could not restore
`git show a7bab38^:.github/scripts/test_arc_extractors.py` because it tested an
`extract_targets()` function reading `arc.resource_name` off a
`terraform_data.arc_registration` marker the configuration did not have. That
marker exists now - arc.tf - so the deleted suite had been written against the
right design and the wrong tree.

Both extractors now return the *Arc machine name*, which equals the VM name only
where `arc.resource_name` is not overridden. The tests are split accordingly:
the classes below cover a document with no markers at all, where the previous
name-based behaviour still applies, and the BUG-019 classes at the bottom cover
documents that have them.

Usage: python3 test_arc_extractors.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extract_arc_names_from_plan as plan_extractor  # noqa: E402
import extract_arc_names_from_state as state_extractor  # noqa: E402


def vm_change(name, actions, resource_type="proxmox_virtual_environment_vm"):
    """One entry of a plan's resource_changes."""
    return {
        "address": f'{resource_type}.vm["{name}"]',
        "type": resource_type,
        "name": "vm",
        "change": {
            "actions": actions,
            "before": None if actions == ["create"] else {"name": name},
            "after": None if "delete" in actions and "create" not in actions else {"name": name},
        },
    }


class PlanExtractorTests(unittest.TestCase):
    def test_delete_only_plan_is_a_target(self):
        plan = {"resource_changes": [vm_change("ubuntu-01", ["delete"])]}
        self.assertEqual(plan_extractor.extract_names(plan), {"ubuntu-01"})

    def test_create_only_plan_has_no_targets(self):
        """A new VM has nothing registered in Arc to clean up."""
        plan = {"resource_changes": [vm_change("ubuntu-01", ["create"])]}
        self.assertEqual(plan_extractor.extract_names(plan), set())

    def test_no_op_and_update_plans_have_no_targets(self):
        plan = {
            "resource_changes": [
                vm_change("ubuntu-01", ["no-op"]),
                vm_change("ubuntu-02", ["update"]),
                vm_change("ubuntu-03", ["read"]),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), set())

    def test_replacement_destroy_before_create(self):
        """The default replacement ordering."""
        plan = {"resource_changes": [vm_change("ubuntu-01", ["delete", "create"])]}
        self.assertEqual(plan_extractor.extract_names(plan), {"ubuntu-01"})

    def test_replacement_create_before_destroy(self):
        """The create_before_destroy ordering. The old machine still goes away."""
        plan = {"resource_changes": [vm_change("ubuntu-01", ["create", "delete"])]}
        self.assertEqual(plan_extractor.extract_names(plan), {"ubuntu-01"})

    def test_a_fused_delete_create_action_is_not_a_target(self):
        """CHORE-004-A1. "delete_create" is not a Terraform action.

        The filter used to test for it alongside "delete". The test could never
        be true - a replacement is ["delete", "create"], two members, and the
        action vocabulary in plan JSON is "no-op", "create", "read", "update"
        and "delete". Removing it changed no behaviour, which is exactly why it
        needs a test: this pins the vocabulary, so reintroducing the fused
        string as a *positive* match would turn a plan that deletes nothing into
        an Arc deletion.
        """
        plan = {"resource_changes": [vm_change("ubuntu-01", ["delete_create"])]}
        self.assertEqual(plan_extractor.extract_names(plan), set())

    def test_uses_the_previous_name_not_the_new_one(self):
        """A rename is a replacement; Arc knows the machine by the old name."""
        plan = {
            "resource_changes": [
                {
                    "type": "proxmox_virtual_environment_vm",
                    "change": {
                        "actions": ["delete", "create"],
                        "before": {"name": "old-name"},
                        "after": {"name": "new-name"},
                    },
                }
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"old-name"})

    def test_other_resource_types_are_never_targets(self):
        """The snippet file is destroyed on every change. It is not a machine."""
        plan = {
            "resource_changes": [
                vm_change("user-data", ["delete"], "proxmox_virtual_environment_file"),
                vm_change("cfg", ["delete"], "terraform_data"),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), set())

    def test_missing_and_malformed_keys_are_survivable(self):
        plan = {
            "resource_changes": [
                {},
                {"type": "proxmox_virtual_environment_vm"},
                {"type": "proxmox_virtual_environment_vm", "change": None},
                {"type": "proxmox_virtual_environment_vm", "change": {"actions": None}},
                {
                    "type": "proxmox_virtual_environment_vm",
                    "change": {"actions": ["delete"], "before": None},
                },
                {
                    "type": "proxmox_virtual_environment_vm",
                    "change": {"actions": ["delete"], "before": {}},
                },
                {
                    "type": "proxmox_virtual_environment_vm",
                    "change": {"actions": ["delete"], "before": {"name": None}},
                },
                {
                    "type": "proxmox_virtual_environment_vm",
                    "change": {"actions": ["delete"], "before": {"name": 42}},
                },
                {
                    "type": "proxmox_virtual_environment_vm",
                    "change": {"actions": ["delete"], "before": {"name": "   "}},
                },
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), set())

    def test_empty_plan(self):
        self.assertEqual(plan_extractor.extract_names({}), set())
        self.assertEqual(plan_extractor.extract_names({"resource_changes": []}), set())
        self.assertEqual(plan_extractor.extract_names({"resource_changes": None}), set())

    def test_several_vms_in_one_plan(self):
        plan = {
            "resource_changes": [
                vm_change("ubuntu-01", ["delete"]),
                vm_change("ubuntu-02", ["create"]),
                vm_change("win-01", ["delete", "create"]),
                vm_change("ubuntu-03", ["no-op"]),
            ]
        }
        self.assertEqual(plan_extractor.extract_names(plan), {"ubuntu-01", "win-01"})


def state_resource(name, resource_type="proxmox_virtual_environment_vm"):
    return {"type": resource_type, "name": "vm", "values": {"name": name}}


class StateExtractorTests(unittest.TestCase):
    def test_root_module_vms(self):
        state = {
            "values": {
                "root_module": {
                    "resources": [state_resource("ubuntu-01"), state_resource("win-01")]
                }
            }
        }
        self.assertEqual(
            state_extractor.extract_names(state), {"ubuntu-01", "win-01"}
        )

    def test_nested_child_modules(self):
        """FEAT-007 will move the factory into a module. The walk must follow."""
        state = {
            "values": {
                "root_module": {
                    "resources": [state_resource("root-01")],
                    "child_modules": [
                        {
                            "resources": [state_resource("child-01")],
                            "child_modules": [
                                {"resources": [state_resource("grandchild-01")]}
                            ],
                        },
                        {"resources": [state_resource("child-02")]},
                    ],
                }
            }
        }
        self.assertEqual(
            state_extractor.extract_names(state),
            {"root-01", "child-01", "grandchild-01", "child-02"},
        )

    def test_other_resource_types_are_never_targets(self):
        state = {
            "values": {
                "root_module": {
                    "resources": [
                        state_resource("user-data", "proxmox_virtual_environment_file"),
                        state_resource("cfg", "terraform_data"),
                    ]
                }
            }
        }
        self.assertEqual(state_extractor.extract_names(state), set())

    def test_empty_state(self):
        """`terraform show -json` on an empty state has no values at all."""
        self.assertEqual(state_extractor.extract_names({}), set())
        self.assertEqual(state_extractor.extract_names({"values": None}), set())
        self.assertEqual(
            state_extractor.extract_names({"values": {"root_module": None}}), set()
        )
        self.assertEqual(
            state_extractor.extract_names({"values": {"root_module": {}}}), set()
        )

    def test_missing_and_malformed_keys_are_survivable(self):
        state = {
            "values": {
                "root_module": {
                    "resources": [
                        {},
                        {"type": "proxmox_virtual_environment_vm"},
                        {"type": "proxmox_virtual_environment_vm", "values": None},
                        {"type": "proxmox_virtual_environment_vm", "values": {}},
                        {
                            "type": "proxmox_virtual_environment_vm",
                            "values": {"name": None},
                        },
                        {
                            "type": "proxmox_virtual_environment_vm",
                            "values": {"name": 42},
                        },
                        {
                            "type": "proxmox_virtual_environment_vm",
                            "values": {"name": "  "},
                        },
                    ],
                    "child_modules": None,
                }
            }
        }
        self.assertEqual(state_extractor.extract_names(state), set())

    def test_duplicate_names_collapse(self):
        state = {
            "values": {
                "root_module": {
                    "resources": [state_resource("ubuntu-01")],
                    "child_modules": [{"resources": [state_resource("ubuntu-01")]}],
                }
            }
        }
        self.assertEqual(state_extractor.extract_names(state), {"ubuntu-01"})


class CommandLineTests(unittest.TestCase):
    """The scripts are called from a workflow, so the exit code is the contract.

    A malformed document must not look like "no machines to clean up": that is
    the difference between a failed job and a silent skip of a destructive step.
    """

    scripts = ("extract_arc_names_from_plan.py", "extract_arc_names_from_state.py")

    def run_script(self, script, *args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, script), *args],
            capture_output=True,
            text=True,
        )

    def test_wrong_argument_count_exits_2(self):
        for script in self.scripts:
            with self.subTest(script=script):
                result = self.run_script(script)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("usage:", result.stderr)

    def test_malformed_json_fails_loudly(self):
        for script in self.scripts:
            with self.subTest(script=script):
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "broken.json")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("{not json")
                    result = self.run_script(script, path)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")

    def test_missing_file_fails_loudly(self):
        for script in self.scripts:
            with self.subTest(script=script):
                result = self.run_script(script, os.path.join(HERE, "no-such-file.json"))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_names_are_printed_one_per_line_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plan.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "resource_changes": [
                            vm_change("win-01", ["delete"]),
                            vm_change("ubuntu-01", ["delete"]),
                        ]
                    },
                    f,
                )
            result = self.run_script("extract_arc_names_from_plan.py", path)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.split(), ["ubuntu-01", "win-01"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
