#!/usr/bin/env python3
"""Tests for the apply safety guard (BUG-002).

BUG-002-A5 asks for four cases: empty inventory + empty state, empty inventory +
populated state, populated inventory + empty state, populated inventory +
populated state. They are the first four tests in DecisionTests and each one
pins both the verdict and the reason.

The rest exist because the guard this replaces failed in ways a happy-path test
would have missed. It was inverted in practice, its escape hatch disabled it
wholesale, and it read HCL as text. So: TF_BOOTSTRAP is pinned to authorise
first-time creation and nothing else, and every path where the guard cannot
establish its inputs is pinned to block rather than to assume "empty".

Usage: python3 test_inventory_guard.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import inventory_guard as guard  # noqa: E402

SCRIPT = os.path.join(HERE, "inventory_guard.py")

VM = "proxmox_virtual_environment_vm.vm"
FILE = "proxmox_virtual_environment_file.user_data"

POPULATED_STATE = [
    f'{FILE}["ubuntu-01"]',
    f'{VM}["ubuntu-01"]',
    "terraform_data.vm_factory_config",
]


def verdict(desired, state, state_file_exists=True, bootstrap=False):
    code, lines = guard.decide(desired, state, state_file_exists, bootstrap)
    return code, "\n".join(lines)


class DecisionTests(unittest.TestCase):
    # --- the four cases BUG-002-A5 names -------------------------------------

    def test_empty_inventory_empty_state_proceeds(self):
        """Nothing declared, nothing managed. The apply is a no-op.

        The old grep could not match a multi-line `vms = {}`, so this case was
        read as "VMs are desired" and blocked - the inversion the issue found.
        """
        code, out = verdict([], [], state_file_exists=False)
        self.assertEqual(code, 0, out)
        self.assertNotIn("::error::", out)

    def test_empty_inventory_populated_state_proceeds_loudly(self):
        """Emptying the inventory is the deliberate teardown path.

        It proceeds - but an apply that destroys the lab should not read like an
        ordinary one in the log.
        """
        code, out = verdict([], POPULATED_STATE)
        self.assertEqual(code, 0, out)
        self.assertIn("::warning::", out)
        self.assertIn("DESTROY", out)

    def test_populated_inventory_empty_state_blocks(self):
        """The orphan-VM case: applying would create VMs Terraform forgets."""
        code, out = verdict(["ubuntu-01"], [])
        self.assertEqual(code, 1, out)
        self.assertIn("::error::", out)
        self.assertIn("orphan", out)

    def test_populated_inventory_populated_state_proceeds(self):
        code, out = verdict(["ubuntu-01"], POPULATED_STATE)
        self.assertEqual(code, 0, out)
        self.assertNotIn("::error::", out)
        self.assertNotIn("::warning::", out)

    # --- TF_BOOTSTRAP is narrowed to first-time creation (A4) ----------------

    def test_bootstrap_permits_first_time_creation(self):
        code, out = verdict(
            ["ubuntu-01"], [], state_file_exists=False, bootstrap=True
        )
        self.assertEqual(code, 0, out)
        self.assertIn("first-time creation", out)

    def test_bootstrap_does_not_override_a_lost_state_file(self):
        """An existing state file listing nothing is not a first run.

        This is the case the old escape hatch swallowed: TF_BOOTSTRAP=true
        skipped the check entirely, including for a state that had been
        truncated or restored empty.
        """
        code, out = verdict(
            ["ubuntu-01"], [], state_file_exists=True, bootstrap=True
        )
        self.assertEqual(code, 1, out)
        self.assertIn("::error::", out)
        self.assertIn("not a first run", out)

    def test_bootstrap_changes_nothing_in_the_other_three_cases(self):
        """It authorises one thing. It must not silence anything else."""
        for desired, state, exists in (
            ([], [], False),
            ([], POPULATED_STATE, True),
            (["ubuntu-01"], POPULATED_STATE, True),
        ):
            with self.subTest(desired=desired, state=len(state)):
                without = verdict(desired, state, exists, bootstrap=False)
                with_it = verdict(desired, state, exists, bootstrap=True)
                self.assertEqual(without, with_it)

    # --- the messages have to be actionable ---------------------------------

    def test_blocking_message_says_what_to_do_next(self):
        _, out = verdict(["ubuntu-01"], [])
        self.assertIn("/opt/terraform-state/proxmox-ubuntu-vm-factory", out)
        self.assertIn("TF_BOOTSTRAP=true", out)

    def test_report_names_the_desired_vms(self):
        _, out = verdict(["win-01", "ubuntu-01"], POPULATED_STATE)
        self.assertIn("ubuntu-01, win-01", out)

    def test_vm_resources_are_counted_separately_from_the_rest(self):
        _, out = verdict(["ubuntu-01"], POPULATED_STATE)
        self.assertIn("3 resource(s), 1 of them VMs", out)

    def test_a_state_holding_only_non_vm_resources_is_not_empty(self):
        """terraform_data.vm_factory_config exists on every applied state.

        It is not a VM, but its presence still means state was read - so this is
        not the orphan case.
        """
        code, out = verdict(["ubuntu-01"], ["terraform_data.vm_factory_config"])
        self.assertEqual(code, 0, out)

    def test_resource_type_match_is_exact(self):
        """A future `proxmox_virtual_environment_vm_extra` is not a VM."""
        _, out = verdict([], ["proxmox_virtual_environment_vm_extra.thing"])
        self.assertIn("0 of them VMs", out)


class ConsoleParsingTests(unittest.TestCase):
    """`terraform console` output is the guard's view of the inventory."""

    def test_jsonencode_double_encoded_form(self):
        """What console actually prints for a jsonencode() result."""
        self.assertEqual(
            guard.parse_console_output('"[\\"ubuntu-01\\",\\"win-01\\"]"'),
            ["ubuntu-01", "win-01"],
        )

    def test_bare_array_form_is_accepted(self):
        self.assertEqual(
            guard.parse_console_output('["ubuntu-01"]'), ["ubuntu-01"]
        )

    def test_empty_inventory(self):
        self.assertEqual(guard.parse_console_output('"[]"'), [])

    def test_last_non_empty_line_is_used(self):
        self.assertEqual(
            guard.parse_console_output('\n\n"[\\"ubuntu-01\\"]"\n\n'), ["ubuntu-01"]
        )

    def test_empty_output_is_undecidable(self):
        """Not "the inventory is empty" - the guard learned nothing."""
        with self.assertRaises(guard.Undecidable):
            guard.parse_console_output("")
        with self.assertRaises(guard.Undecidable):
            guard.parse_console_output("   \n  \n")

    def test_sensitive_value_is_undecidable(self):
        """If the expression ever picks up a sensitive mark, console redacts it.

        Reading that as an empty inventory would unblock exactly the run this
        guard exists to stop.
        """
        with self.assertRaises(guard.Undecidable):
            guard.parse_console_output("(sensitive value)")

    def test_non_list_result_is_undecidable(self):
        for text in ('"{}"', '{"a": 1}', '"42"', '["ok", 42]'):
            with self.subTest(text=text):
                with self.assertRaises(guard.Undecidable):
                    guard.parse_console_output(text)

    def test_error_text_is_undecidable(self):
        with self.assertRaises(guard.Undecidable):
            guard.parse_console_output("Error: Reference to undeclared local value")


class StateListParsingTests(unittest.TestCase):
    def test_addresses_are_read_one_per_line(self):
        self.assertEqual(
            guard.parse_state_list(f"{VM}[\"a\"]\n{FILE}[\"a\"]\n"),
            [f'{VM}["a"]', f'{FILE}["a"]'],
        )

    def test_blank_output_is_an_empty_state(self):
        """Unlike the inventory, a blank `state list` genuinely means empty."""
        self.assertEqual(guard.parse_state_list(""), [])
        self.assertEqual(guard.parse_state_list("\n \n"), [])


class CommandLineTests(unittest.TestCase):
    """The workflow reads the exit code, so that is the contract."""

    def run_guard(self, desired_text, state_text, make_state_file, bootstrap=None):
        with tempfile.TemporaryDirectory() as tmp:
            desired = os.path.join(tmp, "desired.json")
            state_list = os.path.join(tmp, "state-list.txt")
            state_file = os.path.join(tmp, "terraform.tfstate")
            if desired_text is not None:
                with open(desired, "w", encoding="utf-8") as f:
                    f.write(desired_text)
            with open(state_list, "w", encoding="utf-8") as f:
                f.write(state_text)
            if make_state_file:
                with open(state_file, "w", encoding="utf-8") as f:
                    f.write("{}")

            env = {k: v for k, v in os.environ.items() if k != "TF_BOOTSTRAP"}
            if bootstrap is not None:
                env["TF_BOOTSTRAP"] = bootstrap

            return subprocess.run(
                [sys.executable, SCRIPT, "--desired", desired,
                 "--state-list", state_list, "--state-file", state_file],
                env=env,
                capture_output=True,
                text=True,
            )

    def test_proceeds_on_a_healthy_apply(self):
        result = self.run_guard(
            json.dumps(json.dumps(["ubuntu-01"])),
            f'{VM}["ubuntu-01"]\n',
            make_state_file=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_blocks_the_orphan_case(self):
        result = self.run_guard(
            json.dumps(json.dumps(["ubuntu-01"])), "", make_state_file=True
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("orphan", result.stdout)

    def test_bootstrap_env_var_is_read(self):
        result = self.run_guard(
            json.dumps(json.dumps(["ubuntu-01"])), "",
            make_state_file=False, bootstrap="true",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bootstrap_must_be_exactly_true(self):
        """`TF_BOOTSTRAP` is a repository variable; a typo must not unblock."""
        for value in ("True ", "yes", "1", "", "false"):
            with self.subTest(value=value):
                result = self.run_guard(
                    json.dumps(json.dumps(["ubuntu-01"])), "",
                    make_state_file=False, bootstrap=value,
                )
                expected = 0 if value.strip().lower() == "true" else 1
                self.assertEqual(result.returncode, expected, value)

    def test_unreadable_inventory_blocks(self):
        """Fail closed. A guard that cannot read the inventory has not
        concluded that the inventory is empty."""
        result = self.run_guard(None, "", make_state_file=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("::error::", result.stderr)

    def test_unparseable_inventory_blocks(self):
        result = self.run_guard("(sensitive value)", "", make_state_file=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("::error::", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
