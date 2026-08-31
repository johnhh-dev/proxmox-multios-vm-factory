#!/usr/bin/env python3
"""Unit tests for render_terraform_env.py (BUG-003).

This script decides which value every Terraform run gets for every input
variable. A defect here is not a failed build - it is a plan that renders
different user-data than the apply of the same commit, which is the whole of
BUG-003. So the mapping is pinned: one secret name per variable, a name that is
not that one supplies nothing (BUG-003-A4), and a required secret that is absent
stops the job rather than reaching Terraform as an empty string.

Usage: python3 test_render_terraform_env.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_terraform_env as renderer  # noqa: E402

SCRIPT = os.path.join(HERE, "render_terraform_env.py")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

# Enough to satisfy every `required` entry, so a test can add one more secret
# and assert about that one without also triggering the missing-secret path.
#
# SEC-006-A3 removed TF_VAR_PROXMOX_SSH_PASSWORD from this set. It is optional
# now, because a lab authenticating to the node with a key or an agent has no
# root password to supply - and this table cannot express "one of these three".
# That requirement lives in locals.tf, where a plan can see all three at once.
REQUIRED = {
    "TF_VAR_PROXMOX_API_TOKEN": "root@pam!ci=00000000-0000-0000-0000-000000000000",
    "TF_VAR_SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest test@example",
}


def parse_env_file(text: str) -> dict[str, str]:
    """Read back the heredoc form that GitHub Actions writes to $GITHUB_ENV."""
    values: dict[str, str] = {}
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line:
            index += 1
            continue
        if "<<" not in line:
            raise AssertionError(f"not a heredoc entry: {line!r}")
        name, delimiter = line.split("<<", 1)
        index += 1
        body: list[str] = []
        while lines[index] != delimiter:
            body.append(lines[index])
            index += 1
        values[name] = "\n".join(body)
        index += 1
    return values


class ResolveTests(unittest.TestCase):
    def test_canonical_names_are_mapped_to_variables(self):
        # SEC-006-A3: supplied explicitly rather than read out of REQUIRED,
        # because the node SSH identity is optional now and REQUIRED holds only
        # what a run cannot proceed without.
        available = dict(REQUIRED)
        available["TF_VAR_PROXMOX_SSH_PASSWORD"] = "not-a-real-password"
        available["TF_VAR_PROXMOX_SSH_PRIVATE_KEY"] = "-----BEGIN FIXTURE-----"
        resolved, _ = renderer.resolve(available)
        self.assertEqual(
            resolved["TF_VAR_proxmox_api_token"], REQUIRED["TF_VAR_PROXMOX_API_TOKEN"]
        )
        self.assertEqual(
            resolved["TF_VAR_proxmox_ssh_password"],
            available["TF_VAR_PROXMOX_SSH_PASSWORD"],
        )
        self.assertEqual(
            resolved["TF_VAR_proxmox_ssh_private_key"],
            available["TF_VAR_PROXMOX_SSH_PRIVATE_KEY"],
        )

    def test_every_variable_has_a_distinct_canonical_secret(self):
        """A duplicated canonical name would make one variable shadow another."""
        canonical = [entry[1] for entry in renderer.VARIABLES]
        self.assertEqual(len(canonical), len(set(canonical)))
        variables = [entry[0] for entry in renderer.VARIABLES]
        self.assertEqual(len(variables), len(set(variables)))

    def test_canonical_name_is_the_variable_uppercased(self):
        """The naming rule is the point of BUG-003-A4; assert it, don't trust it."""
        for variable, canonical, _ in renderer.VARIABLES:
            with self.subTest(variable=variable):
                self.assertEqual(canonical, f"TF_VAR_{variable}".upper())

    def test_a_retired_name_supplies_nothing(self):
        """BUG-003-A4. Every one of these used to resolve. The point of removing
        the fallbacks is that a secret under an old name is now a secret nothing
        reads - so a rename that was never finished has to fail loudly here
        rather than quietly work until the day someone deletes the old secret."""
        # SEC-006-A3 moved PX_SSH_PASS out of this list and into the silent
        # case below. Not because the alias started working - it did not - but
        # because its canonical name is optional now, so the symptom of using
        # the retired name changed from a loud failure to an absent variable.
        for retired, canonical in (
            ("PX_API_TOKEN", "TF_VAR_PROXMOX_API_TOKEN"),
            ("SSH_PUBKEY", "TF_VAR_SSH_PUBLIC_KEY"),
        ):
            with self.subTest(retired=retired):
                available = {k: v for k, v in REQUIRED.items() if k != canonical}
                available[retired] = "value-under-the-old-name"
                with self.assertRaises(renderer.MissingSecret) as caught:
                    renderer.resolve(available)
                self.assertIn(canonical, str(caught.exception))

    def test_a_retired_optional_name_supplies_nothing(self):
        """The optional ones fail silently rather than loudly, which is worse:
        a Windows guest built with a blank Administrator password. It shows up
        in the `unset:` line instead."""
        available = dict(REQUIRED)
        available["WINDOWS_ADMIN_PASSWORD"] = "value-under-the-old-name"
        available["TF_VAR_VM_PASSWORD"] = "value-under-the-old-name"
        available["PX_SSH_PASS"] = "value-under-the-old-name"
        resolved, unset = renderer.resolve(available)
        self.assertNotIn("TF_VAR_windows_admin_password", resolved)
        self.assertNotIn("TF_VAR_linux_vm_password", resolved)
        self.assertNotIn("TF_VAR_proxmox_ssh_password", resolved)
        self.assertIn("TF_VAR_windows_admin_password", unset)
        self.assertIn("TF_VAR_linux_vm_password", unset)
        self.assertIn("TF_VAR_proxmox_ssh_password", unset)

    def test_the_node_ssh_identity_has_three_optional_sources(self):
        """SEC-006-A3. None of the three may be required on its own, or a lab
        using one of the others is forced to keep a credential it does not use.
        The "at least one" rule is locals.tf's, and cannot be expressed here."""
        by_name = {v: (secret, required) for v, secret, required in renderer.VARIABLES}
        for variable in (
            "proxmox_ssh_password",
            "proxmox_ssh_private_key",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, by_name)
                self.assertFalse(by_name[variable][1], f"{variable} must be optional")

    def test_no_variable_declares_an_alias(self):
        """The mechanism is gone, not merely unused. A three-field entry cannot
        carry a fallback chain, and this fails if a fourth field comes back."""
        for entry in renderer.VARIABLES:
            self.assertEqual(len(entry), 3, entry)

    def test_missing_required_secret_raises(self):
        for name in REQUIRED:
            with self.subTest(missing=name):
                available = {k: v for k, v in REQUIRED.items() if k != name}
                with self.assertRaises(renderer.MissingSecret) as caught:
                    renderer.resolve(available)
                self.assertIn(name, str(caught.exception))

    def test_empty_string_counts_as_missing(self):
        """An undeclared secret arrives from toJSON as an absent key; a declared
        but empty one arrives as "". Both mean the same thing here."""
        available = dict(REQUIRED)
        available["TF_VAR_PROXMOX_API_TOKEN"] = ""
        with self.assertRaises(renderer.MissingSecret):
            renderer.resolve(available)

    def test_optional_secrets_are_reported_not_defaulted(self):
        _, unset = renderer.resolve(REQUIRED)
        self.assertIn("TF_VAR_windows_admin_password", unset)
        self.assertIn("TF_VAR_arc_sp_secret", unset)
        self.assertNotIn("TF_VAR_proxmox_api_token", unset)

    def test_unrelated_secrets_are_ignored(self):
        available = dict(REQUIRED)
        available["SOME_OTHER_TOKEN"] = "not ours"
        resolved, _ = renderer.resolve(available)
        self.assertNotIn("TF_VAR_some_other_token", resolved)
        self.assertNotIn("some other", " ".join(resolved.values()))


class RenderTests(unittest.TestCase):
    def test_value_round_trips(self):
        entry = renderer.render("TF_VAR_x", "plain")
        self.assertEqual(parse_env_file(entry), {"TF_VAR_x": "plain"})

    def test_multiline_value_round_trips(self):
        """An SSH private key or a generated password can contain a newline.
        The `NAME=value` form silently truncates one; the heredoc form does not."""
        value = "line-one\nline-two\nline-three"
        self.assertEqual(parse_env_file(renderer.render("TF_VAR_x", value)),
                         {"TF_VAR_x": value})

    def test_value_containing_an_equals_sign_and_quotes(self):
        value = 'pa"ss=wo\\rd'
        self.assertEqual(parse_env_file(renderer.render("TF_VAR_x", value)),
                         {"TF_VAR_x": value})

    def test_delimiter_is_not_present_in_the_value(self):
        """A value carrying the delimiter would end the entry early and leave
        the rest of the secret parsed as further assignments."""
        for _ in range(64):
            entry = renderer.render("TF_VAR_x", "value")
            delimiter = entry.split("\n")[0].split("<<")[1]
            self.assertNotIn(delimiter, "value")


class CommandLineTests(unittest.TestCase):
    """The workflow reads the exit code, so that is the contract."""

    def run_script(self, env_extra, argv=None):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = os.path.join(tmp, "env")
            open(env_file, "w", encoding="utf-8").close()
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_SECRETS_JSON"}
            env.update(env_extra)
            result = subprocess.run(
                [sys.executable, SCRIPT, *(argv if argv is not None else [env_file])],
                env=env,
                capture_output=True,
                text=True,
            )
            with open(env_file, "r", encoding="utf-8") as handle:
                written = handle.read()
        return result, written

    def test_writes_the_variables_and_exits_zero(self):
        result, written = self.run_script(
            {"GITHUB_SECRETS_JSON": json.dumps(REQUIRED)}
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        values = parse_env_file(written)
        self.assertEqual(
            values["TF_VAR_proxmox_api_token"], REQUIRED["TF_VAR_PROXMOX_API_TOKEN"]
        )

    def test_no_secret_value_is_ever_printed(self):
        secret = "canary-8b21f0d4-do-not-print"
        available = dict(REQUIRED)
        available["TF_VAR_ARC_SP_SECRET"] = secret
        result, _ = self.run_script({"GITHUB_SECRETS_JSON": json.dumps(available)})
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)
        for value in REQUIRED.values():
            self.assertNotIn(value, result.stdout)
            self.assertNotIn(value, result.stderr)

    def test_missing_required_secret_exits_one_and_names_it(self):
        available = {k: v for k, v in REQUIRED.items() if k != "TF_VAR_SSH_PUBLIC_KEY"}
        result, written = self.run_script(
            {"GITHUB_SECRETS_JSON": json.dumps(available)}
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("TF_VAR_SSH_PUBLIC_KEY", result.stderr)
        self.assertEqual(written, "", "nothing should be written on the failing path")

    def test_empty_secrets_json_exits_two(self):
        result, _ = self.run_script({"GITHUB_SECRETS_JSON": ""})
        self.assertEqual(result.returncode, 2)
        self.assertIn("toJSON(secrets)", result.stderr)

    def test_malformed_secrets_json_exits_two(self):
        result, _ = self.run_script({"GITHUB_SECRETS_JSON": "{not json"})
        self.assertEqual(result.returncode, 2)

    def test_non_object_secrets_json_exits_two(self):
        result, _ = self.run_script({"GITHUB_SECRETS_JSON": '["a"]'})
        self.assertEqual(result.returncode, 2)

    def test_wrong_argument_count_exits_two(self):
        result, _ = self.run_script(
            {"GITHUB_SECRETS_JSON": json.dumps(REQUIRED)}, argv=[]
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


class SettingsTests(unittest.TestCase):
    """KAN-012. Repository variables reaching Terraform, and the two rules that
    make that safe: an unset variable must not override a default, and a
    variable must never be able to supply a secret."""

    def test_an_unset_setting_is_omitted_not_blanked(self):
        """The contract. variables.tf holds the default, and emitting "" would
        override that default with an empty string rather than fall back to it -
        which is the trap SEC-006-A3 found from the provider's side, where an
        empty ssh password is rejected outright and a null is fine."""
        resolved, bad = renderer.resolve_settings({})
        self.assertEqual(resolved, {})
        self.assertEqual(bad, [])

        for blank in ("", "   ", "	"):
            with self.subTest(blank=repr(blank)):
                resolved, _ = renderer.resolve_settings(
                    {"TF_VAR_proxmox_endpoint": blank}
                )
                self.assertNotIn("TF_VAR_proxmox_endpoint", resolved)

    def test_an_allowlisted_setting_is_passed_through(self):
        resolved, bad = renderer.resolve_settings(
            {"TF_VAR_proxmox_endpoint": "https://10.0.0.9:8006"}
        )
        self.assertEqual(resolved["TF_VAR_proxmox_endpoint"], "https://10.0.0.9:8006")
        self.assertEqual(bad, [])

    def test_a_variable_outside_the_allowlist_is_ignored(self):
        """`vars` is writable by anyone with repository admin and is not audited
        the way code is. Passing everything through would make every Terraform
        input remotely settable."""
        resolved, _ = renderer.resolve_settings(
            {
                "TF_VAR_something_invented": "value",
                "SOME_OTHER_VARIABLE": "value",
            }
        )
        self.assertEqual(resolved, {})

    def test_no_setting_can_supply_a_secret(self):
        """The two tables must stay disjoint. A credential settable from `vars`
        would be settable by repository admin with no audit trail."""
        secret_names = {f"TF_VAR_{v}" for v, _, _ in renderer.VARIABLES}
        setting_names = {name for name, _ in renderer.SETTINGS}
        self.assertEqual(secret_names & setting_names, set())

    def test_no_sensitive_variable_is_settable_from_vars(self):
        """The invariant test_no_setting_can_supply_a_secret only half covers.

        That one compares SETTINGS against the *secrets table*. A variable
        declared `sensitive = true` in variables.tf and put in SETTINGS without
        also being in that table would pass it - and become settable by anyone
        with repository admin, from a context that is not reviewed the way code
        is and leaves no audit trail.

        This reads the declarations instead, so the rule is "nothing sensitive",
        not "nothing already known to be a secret".
        """
        source = open(
            os.path.join(REPO, "variables.tf"), encoding="utf-8"
        ).read()
        # Split into blocks on the declaration itself rather than matched with
        # one expression: a lookahead that has to say "not the closing brace of
        # a top-level block" is the kind of regex that rots quietly, and this
        # test exists to notice things rather than to be clever.
        sensitive = set()
        for block in re.split(r'^variable "', source, flags=re.M)[1:]:
            name = block.split('"', 1)[0]
            body = block.split("\n}", 1)[0]
            if re.search(r"^\s*sensitive\s*=\s*true", body, re.M):
                sensitive.add(name)
        self.assertTrue(sensitive, "found no sensitive variables - the regex has rotted")

        settable = {name[len("TF_VAR_"):] for name, _ in renderer.SETTINGS}
        self.assertEqual(
            sensitive & settable,
            set(),
            "a sensitive variable must not be settable from a repository variable",
        )

    def test_the_documented_escape_hatches_are_in_the_allowlist(self):
        """These two are the reason this exists. docs/proxmox-api-token.md and
        docs/operator-setup.md both tell an operator to set them as repository
        variables, and before KAN-012 neither reached Terraform at all - so the
        TLS one would have been set, reported green, and still off."""
        setting_names = {name for name, _ in renderer.SETTINGS}
        self.assertIn("TF_VAR_proxmox_tls_insecure", setting_names)
        self.assertIn("TF_VAR_proxmox_ssh_agent", setting_names)

    def test_a_bool_setting_rejects_a_value_terraform_cannot_use(self):
        """Naming it here beats letting Terraform report a type conversion
        failure on a line the operator did not write."""
        for value in ("yes", "on", "maybe", "1.0"):
            with self.subTest(value=value):
                resolved, bad = renderer.resolve_settings(
                    {"TF_VAR_proxmox_tls_insecure": value}
                )
                self.assertNotIn("TF_VAR_proxmox_tls_insecure", resolved)
                self.assertEqual(len(bad), 1)
                self.assertIn("TF_VAR_proxmox_tls_insecure", bad[0])

    def test_a_bool_setting_accepts_what_terraform_accepts(self):
        # "True " is here rather than in the rejected list above: the value is
        # trimmed before it is judged, which is right for a value typed into a
        # web form, and Terraform takes "True" for a bool.
        for value in ("true", "false", "TRUE", "False", "1", "0", "True "):
            with self.subTest(value=value):
                resolved, bad = renderer.resolve_settings(
                    {"TF_VAR_proxmox_tls_insecure": value}
                )
                self.assertEqual(bad, [])
                self.assertEqual(resolved["TF_VAR_proxmox_tls_insecure"], value.strip())

    def test_every_setting_names_a_real_variable(self):
        """A setting for a variable that does not exist would be silently
        ignored by Terraform, which is the same silence KAN-012 is fixing."""
        declared = set()
        with open(os.path.join(REPO, "variables.tf"), encoding="utf-8") as handle:
            for line in handle:
                if line.startswith('variable "'):
                    declared.add("TF_VAR_" + line.split('"')[1])
        for name, _ in renderer.SETTINGS:
            with self.subTest(setting=name):
                self.assertIn(name, declared)


if __name__ == "__main__":
    unittest.main(verbosity=2)
