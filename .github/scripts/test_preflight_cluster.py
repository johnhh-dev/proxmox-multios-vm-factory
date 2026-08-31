#!/usr/bin/env python3
"""Tests for the apply preflight check (BUG-024).

The decision this script makes is whether an apply is allowed to start, so
every case where it cannot establish an answer is pinned to block rather than
to assume the cluster is healthy. That is the same rule the inventory guard
follows for the same reason: a guard that cannot read its input has not
concluded that the input is fine.

`evaluate` is pure, so the interesting cases need no hypervisor. The fixtures
are the shapes /cluster/status actually returns: one `cluster` entry plus one
`node` entry per member, with booleans rendered as 0/1, and no `cluster` entry
at all on a node that was never clustered.

Usage: python3 test_preflight_cluster.py
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import preflight_cluster as preflight  # noqa: E402


def status(*entries):
    return {"data": list(entries)}


def cluster(name="lab", quorate=1, nodes=2):
    return {"type": "cluster", "name": name, "quorate": quorate, "nodes": nodes}


def node(name, online=1):
    return {"type": "node", "name": name, "online": online, "local": 0}


class EvaluateTests(unittest.TestCase):
    def test_quorate_cluster_passes(self):
        ok, message = preflight.evaluate(
            status(cluster(quorate=1), node("pve"), node("pve2"))
        )
        self.assertTrue(ok)
        self.assertIn("quorate", message)

    def test_inquorate_cluster_blocks(self):
        """The case that produced run 33074685788: pve2 down, pve alone with
        one vote out of two, /etc/pve read-only, the clone rejected."""
        ok, message = preflight.evaluate(
            status(cluster(quorate=0), node("pve"), node("pve2", online=0))
        )
        self.assertFalse(ok)
        self.assertIn("no quorum", message)

    def test_inquorate_message_names_the_missing_node(self):
        """An operator reading the failed step should not have to go and ask
        the hypervisor which node it was."""
        _, message = preflight.evaluate(
            status(cluster(quorate=0), node("pve"), node("pve2", online=0))
        )
        self.assertIn("Offline: pve2", message)
        self.assertIn("Online: pve", message)
        self.assertIn("pvecm expected 1", message)

    def test_standalone_node_passes(self):
        """A node that was never clustered reports itself and no cluster entry.
        There is no quorum to lose, so the check has nothing to say."""
        ok, message = preflight.evaluate(status(node("pve")))
        self.assertTrue(ok)
        self.assertIn("not part of a cluster", message)

    def test_empty_payload_blocks(self):
        """Even a standalone node reports itself, so nothing at all is a shape
        this script does not recognise - not a healthy cluster."""
        ok, _ = preflight.evaluate(status())
        self.assertFalse(ok)

    def test_missing_data_key_blocks(self):
        ok, _ = preflight.evaluate({})
        self.assertFalse(ok)

    def test_unreadable_quorate_value_blocks(self):
        """Fail closed on a value that is neither the integer nor the boolean
        form. A truthy string would otherwise sail through any `if quorate:`
        written here later."""
        for value in (None, "1", "yes", "", 2, -1, []):
            with self.subTest(value=value):
                ok, _ = preflight.evaluate(status(cluster(quorate=value), node("pve")))
                self.assertFalse(ok, "quorate=%r must not pass" % (value,))

    def test_json_boolean_forms_are_accepted(self):
        """Proxmox renders these as 0/1 today. If that ever becomes a real JSON
        boolean, `true` means quorate and blocking on it would stop applies for
        no reason - so both forms are handled on purpose, not by Python's
        True == 1 accident."""
        ok, _ = preflight.evaluate(status(cluster(quorate=True), node("pve")))
        self.assertTrue(ok)
        ok, _ = preflight.evaluate(status(cluster(quorate=False), node("pve")))
        self.assertFalse(ok)

    def test_quorate_one_is_the_only_integer_that_passes(self):
        ok, _ = preflight.evaluate(status(cluster(quorate=1), node("pve")))
        self.assertTrue(ok)


class EndpointReadingTests(unittest.TestCase):
    """`terraform console` prints a string as a quoted JSON document. The step
    pipes that straight into a file, so both forms have to work."""

    class Args:
        def __init__(self, endpoint=None, endpoint_file=None):
            self.endpoint = endpoint
            self.endpoint_file = endpoint_file

    def read(self, contents):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "endpoint.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(contents)
            return preflight.read_endpoint(self.Args(endpoint_file=path))

    def test_terraform_console_output(self):
        self.assertEqual(
            self.read(json.dumps("https://192.168.10.25:8006") + "\n"),
            "https://192.168.10.25:8006",
        )

    def test_bare_url(self):
        self.assertEqual(
            self.read("https://192.168.10.25:8006\n"),
            "https://192.168.10.25:8006",
        )

    def test_explicit_flag_wins(self):
        args = self.Args(endpoint="https://elsewhere:8006")
        self.assertEqual(preflight.read_endpoint(args), "https://elsewhere:8006")


class MainTests(unittest.TestCase):
    def test_missing_token_is_a_configuration_error(self):
        """Exit 2, not 1: the cluster was never asked, so this is not a verdict
        about the cluster."""
        saved = os.environ.pop("TF_VAR_proxmox_api_token", None)
        try:
            self.assertEqual(main_rc(["--endpoint", "https://example:8006"]), 2)
        finally:
            if saved is not None:
                os.environ["TF_VAR_proxmox_api_token"] = saved


def main_rc(argv):
    import io
    from contextlib import redirect_stderr

    with redirect_stderr(io.StringIO()):
        return preflight.main(argv)



class ShapeTests(unittest.TestCase):
    """What the function is handed, before what it says about it.

    The real cluster was read on 2026-08-30 and evaluate() was given the output
    of `pvesh get /cluster/status --output-format json`, which strips the API's
    envelope. It raised AttributeError - an unhandled traceback from the one
    function in this repository whose entire purpose is turning an opaque
    failure into a sentence.
    """

    def test_a_bare_list_is_refused_legibly(self):
        ok, msg = preflight.evaluate(
            [{"type": "cluster", "name": "homelab", "quorate": 1}]
        )
        self.assertFalse(ok)
        self.assertIn("envelope", msg)
        self.assertIn("pvesh", msg)

    def test_a_non_object_is_refused_by_type(self):
        ok, msg = preflight.evaluate("not json at all")
        self.assertFalse(ok)
        self.assertIn("str", msg)

    def test_the_real_clusters_shape_still_passes(self):
        """The payload read from `homelab` on 2026-08-30, wrapped as the HTTP
        API returns it. Two nodes, both online, quorate."""
        ok, msg = preflight.evaluate({"data": [
            {"type": "cluster", "name": "homelab", "quorate": 1, "nodes": 2},
            {"type": "node", "name": "pve", "online": 1},
            {"type": "node", "name": "pve2", "online": 1},
        ]})
        self.assertTrue(ok)
        self.assertIn("homelab", msg)

    def test_the_failure_it_exists_for(self):
        """The same payload with pve2 down - which is what BUG-024 hit, and
        what this cluster does whenever either node goes away, because there is
        no qdevice and a majority of two is two."""
        ok, msg = preflight.evaluate({"data": [
            {"type": "cluster", "name": "homelab", "quorate": 0, "nodes": 2},
            {"type": "node", "name": "pve", "online": 1},
            {"type": "node", "name": "pve2", "online": 0},
        ]})
        self.assertFalse(ok)
        self.assertIn("pve2", msg)
        self.assertIn("read-only", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
