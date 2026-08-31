#!/usr/bin/env python3
"""Tests for mint_arc_token.py (SEC-001a).

Nothing here makes a network call. The token exchange is a documented HTTP
contract, so the tests drive it through an injected opener and assert on the
request that would have been sent and on how each answer is handled.
"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mint_arc_token", Path(__file__).with_name("mint_arc_token.py")
)
minter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(minter)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def opener_returning(payload):
    """An opener that records the request and answers with `payload`."""
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["body"] = dict(urllib.parse.parse_qsl(request.data.decode("utf-8")))
        seen["timeout"] = timeout
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    return opener, seen


def opener_raising(exc):
    def opener(request, timeout=None):
        raise exc

    return opener


class TestEndpoints(unittest.TestCase):
    def test_public_cloud_is_the_default(self):
        host, scope = minter.endpoints("")
        self.assertEqual(host, "https://login.microsoftonline.com")
        self.assertEqual(scope, "https://management.azure.com/.default")

    def test_each_supported_cloud_has_a_distinct_host_and_scope(self):
        """A government subscription must never be handed a public-cloud token."""
        hosts = {minter.endpoints(c)[0] for c in minter.CLOUDS}
        scopes = {minter.endpoints(c)[1] for c in minter.CLOUDS}
        self.assertEqual(len(hosts), len(minter.CLOUDS))
        self.assertEqual(len(scopes), len(minter.CLOUDS))

    def test_the_cloud_table_matches_what_azcmagent_accepts(self):
        self.assertEqual(
            sorted(minter.CLOUDS),
            ["AzureChinaCloud", "AzureCloud", "AzureUSGovernment"],
        )

    def test_an_unknown_cloud_is_an_error_not_a_fallback(self):
        with self.assertRaises(minter.MintError) as caught:
            minter.endpoints("AzureNorwayCloud")
        self.assertIn("AzureNorwayCloud", str(caught.exception))


class TestMint(unittest.TestCase):
    def test_sends_the_client_credentials_grant(self):
        opener, seen = opener_returning({"access_token": "jwt-value"})
        token = minter.mint("app-id", "app-secret", "tenant", "AzureCloud", opener)

        self.assertEqual(token, "jwt-value")
        self.assertEqual(
            seen["url"], "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"
        )
        self.assertEqual(seen["body"]["grant_type"], "client_credentials")
        self.assertEqual(seen["body"]["client_id"], "app-id")
        self.assertEqual(seen["body"]["client_secret"], "app-secret")
        self.assertEqual(
            seen["body"]["scope"], "https://management.azure.com/.default"
        )

    def test_the_request_carries_a_timeout(self):
        """A hung login host must fail the step, not hold the lab-state group."""
        opener, seen = opener_returning({"access_token": "jwt-value"})
        minter.mint("app-id", "app-secret", "tenant", "AzureCloud", opener)
        self.assertEqual(seen["timeout"], minter.TIMEOUT_SECONDS)

    def test_a_tenant_id_is_url_quoted(self):
        opener, seen = opener_returning({"access_token": "jwt-value"})
        minter.mint("app-id", "app-secret", "a/b", "AzureCloud", opener)
        self.assertIn("a%2Fb", seen["url"])

    def test_a_response_without_a_token_is_an_error(self):
        opener, _ = opener_returning({"token_type": "Bearer"})
        with self.assertRaises(minter.MintError):
            minter.mint("app-id", "app-secret", "tenant", "AzureCloud", opener)

    def test_an_http_error_reports_the_description(self):
        body = json.dumps(
            {"error": "invalid_client", "error_description": "AADSTS7000215: bad secret"}
        ).encode("utf-8")
        exc = urllib.error.HTTPError(
            "https://login", 401, "Unauthorized", {}, io.BytesIO(body)
        )
        with self.assertRaises(minter.MintError) as caught:
            minter.mint("id", "secret", "tenant", "AzureCloud", opener_raising(exc))
        message = str(caught.exception)
        self.assertIn("401", message)
        self.assertIn("AADSTS7000215", message)

    def test_an_unparseable_error_body_still_raises_cleanly(self):
        exc = urllib.error.HTTPError(
            "https://login", 500, "Server Error", {}, io.BytesIO(b"<html>")
        )
        with self.assertRaises(minter.MintError) as caught:
            minter.mint("id", "secret", "tenant", "AzureCloud", opener_raising(exc))
        self.assertIn("500", str(caught.exception))

    def test_an_unreachable_host_raises(self):
        exc = urllib.error.URLError("no route to host")
        with self.assertRaises(minter.MintError):
            minter.mint("id", "secret", "tenant", "AzureCloud", opener_raising(exc))

    def test_the_secret_is_never_in_the_error_message(self):
        exc = urllib.error.HTTPError(
            "https://login", 401, "Unauthorized", {}, io.BytesIO(b"{}")
        )
        with self.assertRaises(minter.MintError) as caught:
            minter.mint(
                "id", "super-secret-value", "tenant", "AzureCloud", opener_raising(exc)
            )
        self.assertNotIn("super-secret-value", str(caught.exception))


class TestMain(unittest.TestCase):
    def setUp(self):
        os.environ.pop("GITHUB_SECRETS_JSON", None)
        self.addCleanup(os.environ.pop, "GITHUB_SECRETS_JSON", None)

    def env_file(self):
        handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".env")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def set_secrets(self, **values):
        os.environ["GITHUB_SECRETS_JSON"] = json.dumps(values)

    def test_no_service_principal_writes_a_blank_and_succeeds(self):
        """Arc is optional. A lab that never asked for it must still apply."""
        self.set_secrets()
        path = self.env_file()
        self.assertEqual(minter.main(["mint_arc_token.py", path]), 0)
        self.assertEqual(
            Path(path).read_text(encoding="utf-8"), "TF_VAR_arc_access_token=\n"
        )

    def test_a_partial_service_principal_also_writes_a_blank(self):
        self.set_secrets(TF_VAR_ARC_SP_ID="app-id")
        path = self.env_file()
        self.assertEqual(minter.main(["mint_arc_token.py", path]), 0)
        self.assertIn("TF_VAR_arc_access_token=", Path(path).read_text(encoding="utf-8"))

    def test_a_non_string_secret_is_ignored_rather_than_crashing(self):
        self.set_secrets(TF_VAR_ARC_SP_ID=None, TF_VAR_ARC_SP_SECRET="s")
        path = self.env_file()
        self.assertEqual(minter.main(["mint_arc_token.py", path]), 0)

    def test_an_empty_secrets_document_is_a_usage_error(self):
        os.environ["GITHUB_SECRETS_JSON"] = "   "
        self.assertEqual(minter.main(["mint_arc_token.py", self.env_file()]), 2)

    def test_invalid_json_is_a_usage_error(self):
        os.environ["GITHUB_SECRETS_JSON"] = "{not json"
        self.assertEqual(minter.main(["mint_arc_token.py", self.env_file()]), 2)

    def test_a_json_array_is_a_usage_error(self):
        os.environ["GITHUB_SECRETS_JSON"] = "[]"
        self.assertEqual(minter.main(["mint_arc_token.py", self.env_file()]), 2)

    def test_usage_error_without_an_env_file(self):
        self.assertEqual(minter.main(["mint_arc_token.py"]), 2)

    def test_render_produces_one_assignment(self):
        self.assertEqual(minter.render("NAME", "value"), "NAME=value\n")

    def test_render_survives_an_empty_value(self):
        self.assertEqual(minter.render("NAME", ""), "NAME=\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
