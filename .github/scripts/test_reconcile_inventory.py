#!/usr/bin/env python3
"""Tests for the three-way inventory reconciliation (DOC-001-A1).

The tool's whole value is the verdict column, and a wrong verdict is worse than
no tool: `pending` told to apply instead of import builds a second VM beside the
first. So every verdict has a case, including the ones that look alike.

The `qm list` fixtures are real column layouts rather than tidied ones, because
the parser's job is to survive Proxmox's padding.

Usage: python3 test_reconcile_inventory.py
"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import reconcile_inventory as r  # noqa: E402

QM_LIST = """      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID
       100 ubuntu-static-01     running    4096              50.00 1234
       101 orphan-01            stopped    2048              32.00 0
      9900 ubuntu-2404-template stopped    2048              50.00 0
"""


def state_with(*vms):
    return {
        "values": {
            "root_module": {
                "resources": [
                    {
                        "type": "proxmox_virtual_environment_vm",
                        "name": "vm",
                        "values": {"name": n, "vm_id": i},
                    }
                    for n, i in vms
                ]
            }
        }
    }


class QmListTests(unittest.TestCase):
    def test_columns_and_header(self):
        got = r.parse_qm_list(QM_LIST)
        self.assertEqual(
            got,
            {"ubuntu-static-01": 100, "orphan-01": 101, "ubuntu-2404-template": 9900},
        )

    def test_header_is_not_a_vm(self):
        self.assertNotIn("NAME", r.parse_qm_list(QM_LIST))

    def test_blank_and_malformed_lines_are_survivable(self):
        text = QM_LIST + "\n   \nnot a row at all\n"
        self.assertEqual(len(r.parse_qm_list(text)), 3)

    def test_empty_output(self):
        self.assertEqual(r.parse_qm_list(""), {})


class StateTests(unittest.TestCase):
    def test_names_and_ids(self):
        self.assertEqual(
            r.parse_state(state_with(("ubuntu-static-01", 100))),
            {"ubuntu-static-01": 100},
        )

    def test_other_resource_types_are_ignored(self):
        st = state_with(("a", 1))
        st["values"]["root_module"]["resources"].append(
            {"type": "proxmox_virtual_environment_file", "values": {"name": "snippet"}}
        )
        self.assertEqual(r.parse_state(st), {"a": 1})

    def test_child_modules_are_walked(self):
        """FEAT-007 would move the factory into a module. The walk must follow."""
        st = state_with(("root-vm", 1))
        st["values"]["root_module"]["child_modules"] = [
            {"resources": [
                {"type": "proxmox_virtual_environment_vm", "values": {"name": "child-vm", "vm_id": 2}}
            ]}
        ]
        self.assertEqual(r.parse_state(st), {"root-vm": 1, "child-vm": 2})

    def test_missing_and_malformed_keys(self):
        st = {"values": {"root_module": {"resources": [
            {"type": "proxmox_virtual_environment_vm"},
            {"type": "proxmox_virtual_environment_vm", "values": {}},
            {"type": "proxmox_virtual_environment_vm", "values": {"name": None}},
            {"type": "proxmox_virtual_environment_vm", "values": {"name": "  "}},
            {"type": "proxmox_virtual_environment_vm", "values": {"name": "ok", "vm_id": "not-int"}},
        ]}}}
        self.assertEqual(r.parse_state(st), {"ok": None})

    def test_empty_state(self):
        self.assertEqual(r.parse_state({}), {})


class VerdictTests(unittest.TestCase):
    """One case per verdict. These are what the tool is for."""

    def v(self, declared, state, node, name="vm-01"):
        return r.verdict(name, set(declared), dict(state), dict(node))

    def v_protected(self, name, declared=(), state=(), node=(), protected=()):
        return r.verdict(
            name, set(declared), dict(state), dict(node), set(protected)
        )

    def test_a_protected_vm_is_not_an_orphan(self):
        """The finding this came from. Before it, reconcile_inventory reported
        gha-runner-01 as an orphan and pointed at a runbook whose default
        recovery is `qm destroy --purge` - two of whose three safety checks pass
        for the runner."""
        self.assertEqual(
            self.v_protected(
                "gha-runner-01", node={"gha-runner-01": 1110}, protected={1110}
            ),
            "protected",
        )

    def test_an_ordinary_unmanaged_vm_is_still_an_orphan(self):
        """The rule must not swallow the verdict it was carved out of."""
        self.assertEqual(
            self.v_protected(
                "elastic-01", node={"elastic-01": 1105}, protected={1110}
            ),
            "orphan",
        )

    def test_protected_wins_over_every_other_verdict(self):
        """Deliberately, and it is the safe direction: a protected VM that
        someone has also declared and imported is a configuration to stop and
        look at, not one to report as `managed` and move past."""
        self.assertEqual(
            self.v_protected(
                "gha-runner-01",
                declared=["gha-runner-01"],
                state={"gha-runner-01": 1110},
                node={"gha-runner-01": 1110},
                protected={1110},
            ),
            "protected",
        )

    def test_without_the_flag_nothing_changes(self):
        """Callers that do not pass a protected set keep the old behaviour."""
        self.assertEqual(
            self.v_protected("gha-runner-01", node={"gha-runner-01": 1110}),
            "orphan",
        )

    def test_managed(self):
        self.assertEqual(self.v(["vm-01"], {"vm-01": 100}, {"vm-01": 100}), "managed")

    def test_pending_is_an_import_not_an_apply(self):
        """Declared and on the node but not in state.

        Applying here builds a second VM beside the first. This is the verdict
        the tool exists to get right.
        """
        self.assertEqual(self.v(["vm-01"], {}, {"vm-01": 100}), "pending")

    def test_missing_will_be_created(self):
        self.assertEqual(self.v(["vm-01"], {}, {}), "missing")

    def test_orphan(self):
        self.assertEqual(self.v([], {}, {"vm-01": 100}), "orphan")

    def test_ghost(self):
        self.assertEqual(self.v(["vm-01"], {"vm-01": 100}, {}), "ghost")

    def test_undeclared(self):
        self.assertEqual(self.v([], {"vm-01": 100}, {"vm-01": 100}), "undeclared")

    def test_pending_and_orphan_differ_only_by_declaration(self):
        """Same physical situation, opposite actions - so the inventory is what
        decides, and the tool must not collapse the two."""
        self.assertEqual(self.v(["vm-01"], {}, {"vm-01": 100}), "pending")
        self.assertEqual(self.v([], {}, {"vm-01": 100}), "orphan")


class ReconcileTests(unittest.TestCase):
    def test_the_repository_situation_doc_001_describes(self):
        """Five guests on the node, an empty inventory, an empty state."""
        node = {f"vm-{i}": 100 + i for i in range(5)}
        rows = r.reconcile(set(), {}, node)
        self.assertEqual({row[3] for row in rows}, {"orphan"})
        self.assertEqual(len(rows), 5)

    def test_rows_cover_the_union_of_all_three(self):
        rows = r.reconcile({"declared-only"}, {"state-only": 1}, {"node-only": 2})
        self.assertEqual([row[0] for row in rows], ["declared-only", "node-only", "state-only"])

    def test_rows_are_sorted(self):
        rows = r.reconcile({"c", "a", "b"}, {}, {})
        self.assertEqual([row[0] for row in rows], ["a", "b", "c"])


class RenderTests(unittest.TestCase):
    def test_vmid_drift_is_called_out(self):
        """A VM whose id differs between node and state is still `managed`, but
        adopting the wrong number rebuilds the guest (FEAT-002-A6)."""
        rows = r.reconcile({"vm-01"}, {"vm-01": 101}, {"vm-01": 100})
        out = r.render(rows)
        self.assertIn("vm_id disagrees", out)
        self.assertIn("node=100 state=101", out)

    def test_no_drift_section_when_ids_agree(self):
        out = r.render(r.reconcile({"vm-01"}, {"vm-01": 100}, {"vm-01": 100}))
        self.assertNotIn("vm_id disagrees", out)

    def test_every_verdict_has_an_action_line(self):
        for v in r.ACTIONS:
            self.assertTrue(r.ACTIONS[v], f"{v} has no action text")

    def test_empty_everything(self):
        self.assertIn("No VMs found", r.render(r.reconcile(set(), {}, {})))


class MainTests(unittest.TestCase):
    def _files(self, tmp, qm=QM_LIST, state=None, declared=None):
        paths = {}
        p = os.path.join(tmp, "qm.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(qm)
        paths["qm"] = p
        p = os.path.join(tmp, "state.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(state if state is not None else state_with(), f)
        paths["state"] = p
        if declared is not None:
            p = os.path.join(tmp, "declared.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(declared)
            paths["declared"] = p
        return paths

    def test_exit_1_when_anything_needs_attention(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = self._files(tmp)
            with redirect_stdout(io.StringIO()):
                rc = r.main(["--qm-list", p["qm"], "--state", p["state"]])
            self.assertEqual(rc, 1)

    def test_exit_0_when_everything_is_managed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            qm = "      VMID NAME             STATUS\n       100 only-vm          running\n"
            p = self._files(
                tmp, qm=qm, state=state_with(("only-vm", 100)), declared='["only-vm"]'
            )
            with redirect_stdout(io.StringIO()):
                rc = r.main(
                    ["--qm-list", p["qm"], "--state", p["state"], "--declared", p["declared"]]
                )
            self.assertEqual(rc, 0)

    def test_declared_accepts_the_bracketed_console_form(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = self._files(tmp, declared='[\n  "ubuntu-static-01",\n  "orphan-01",\n]\n')
            buf = io.StringIO()
            with redirect_stdout(buf):
                r.main(["--qm-list", p["qm"], "--state", p["state"], "--declared", p["declared"]])
            out = buf.getvalue()
            # Both were declared and are on the node but not in state.
            self.assertIn("pending", out)

    def test_templates_are_not_orphans(self):
        """`qm list` includes templates. Without --template-vmids the tool
        reports one false orphan per template on every run, and a reader who
        learns to skip rows will skip a real one."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = self._files(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                r.main([
                    "--qm-list", p["qm"], "--state", p["state"],
                    "--template-vmids", "9900,9917",
                ])
            out = buf.getvalue()
            self.assertNotIn("9900", out.split("excluded as templates")[0])
            self.assertIn("excluded as templates: ubuntu-2404-template", out)

    def test_bad_template_vmids_is_a_usage_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = self._files(tmp)
            with redirect_stdout(io.StringIO()):
                rc = r.main([
                    "--qm-list", p["qm"], "--state", p["state"],
                    "--template-vmids", "9900,not-a-number",
                ])
            self.assertEqual(rc, 2)

    def test_missing_file_is_a_usage_error_not_a_traceback(self):
        with redirect_stdout(io.StringIO()):
            rc = r.main(["--qm-list", "/nope", "--state", "/nope"])
        self.assertEqual(rc, 2)


class DefaultIdListTests(unittest.TestCase):
    """Both ID lists come from variables.tf when the flags are absent.

    Forgetting one had a cost and no signal: `unmanaged-vms.md` had to say
    "Pass both ID lists" in bold, and the failure of not doing so is a false
    `orphan` beside the runner, whose action column points at a runbook that
    starts with `qm destroy --purge`.
    """

    VARIABLES = '''variable "protected_vm_ids" {
  type    = list(number)
  default = [1110, 1103]
}

variable "template_vmid_linux" {
  type    = number
  default = 9900
}

variable "template_vmid_windows" {
  type    = number
  default = 9917
}
'''

    QM = (
        "      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID\n"
        "       101 ubuntu-dhcp-01       running    4096              50.00 1\n"
        "      1110 gha-runner-01        running    4096              50.00 2\n"
        "      9900 ubuntu-template      stopped    2048              50.00 0\n"
    )

    def _files(self, tmp, variables=None):
        paths = {}
        for name, body in (
            ("qm.txt", self.QM),
            ("state.json", json.dumps({"values": {"root_module": {"resources": []}}})),
            ("variables.tf", self.VARIABLES if variables is None else variables),
        ):
            paths[name] = os.path.join(tmp, name)
            with open(paths[name], "w", encoding="utf-8") as handle:
                handle.write(body)
        return paths

    def _run(self, tmp, *extra, variables=None):
        p = self._files(tmp, variables)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r.main([
                "--qm-list", p["qm.txt"], "--state", p["state.json"],
                "--variables-file", p["variables.tf"], *extra,
            ])
        return rc, buf.getvalue()

    def test_the_runner_is_protected_with_no_flags(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _, out = self._run(tmp)
            self.assertRegex(out, r"gha-runner-01\s+1110\s+-\s+protected")
            self.assertIn("read var.protected_vm_ids", out)

    def test_templates_are_excluded_with_no_flags(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _, out = self._run(tmp)
            self.assertIn("excluded as templates: ubuntu-template", out)

    def test_an_explicit_empty_list_is_not_the_same_as_omitting(self):
        """`--protected-vmids ''` means none, deliberately. Reloading the
        configuration's list there would make the flag impossible to turn off,
        and the operator asking for it is the one comparing against a lab that
        does not have those IDs."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _, out = self._run(tmp, "--protected-vmids", "")
            self.assertRegex(out, r"gha-runner-01\s+1110\s+-\s+orphan")

    def test_an_unreadable_variables_file_refuses(self):
        """Rather than proceeding with an empty list, which is the answer that
        prints `orphan` next to the runner."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = self._files(tmp)
            with redirect_stdout(io.StringIO()):
                rc = r.main([
                    "--qm-list", p["qm.txt"], "--state", p["state.json"],
                    "--variables-file", os.path.join(tmp, "absent.tf"),
                ])
            self.assertEqual(rc, 2)

    def test_a_variables_file_without_the_list_refuses(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                rc, _ = self._run(tmp, variables='variable "other" {\n  default = 1\n}\n')
            self.assertEqual(rc, 2)

    def test_a_flag_still_wins_over_the_configuration(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _, out = self._run(tmp, "--protected-vmids", "101")
            self.assertRegex(out, r"ubuntu-dhcp-01\s+101\s+-\s+protected")
            self.assertRegex(out, r"gha-runner-01\s+1110\s+-\s+orphan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
