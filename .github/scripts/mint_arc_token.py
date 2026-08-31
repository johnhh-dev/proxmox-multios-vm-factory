#!/usr/bin/env python3
"""Mint a short-lived Entra access token for Arc onboarding (SEC-001a).

## Why this exists

`azcmagent connect` used to be handed `--service-principal-secret`, which meant
the secret was interpolated into the cloud-init template, written to a snippet
on the Proxmox node, and stored in `source_raw.data` in Terraform state. Three
copies of a long-lived credential, two of them outliving the boot that used
them. ADR 0001 chose option C: stop circulating the secret and hand the guest a
token that expires on its own instead.

The service principal does not disappear - it is what mints the token, here, on
the runner. That is ADR 0001's accepted Path 1: a credential in the runner's
process environment, bounded by the runner trust boundary. What changes is that
it stops travelling any further than this process.

## Why not `az account get-access-token`

The Azure CLI is a large dependency to require on the runner for one HTTP POST,
and `arc-cleanup` already pays for it only because it needs `az resource`. This
is the standard OAuth 2.0 client-credentials exchange and the standard library
covers it, so a runner without `az` can still plan and apply.

## Missing inputs are not an error

Arc is optional per VM and the whole Arc block is optional for the lab. When the
service principal is absent this writes an empty token and says so: both guest
templates already skip onboarding when their required values are empty, and
failing here would break an apply for a lab that never asked for Arc.

Usage:
  GITHUB_SECRETS_JSON="$(cat secrets.json)" \\
    python3 mint_arc_token.py "$GITHUB_ENV"

Exit codes: 0 written (token or deliberate blank), 1 the exchange failed,
2 usage error.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 30

# Login host and ARM scope per cloud. `azcmagent --cloud` accepts exactly these
# three names, so the table is closed rather than a default-and-hope lookup: an
# unknown cloud is a configuration error worth failing on, not a reason to mint
# a public-cloud token for a government subscription.
CLOUDS = {
    "AzureCloud": (
        "https://login.microsoftonline.com",
        "https://management.azure.com/.default",
    ),
    "AzureUSGovernment": (
        "https://login.microsoftonline.us",
        "https://management.usgovcloudapi.net/.default",
    ),
    "AzureChinaCloud": (
        "https://login.chinacloudapi.cn",
        "https://management.chinacloudapi.cn/.default",
    ),
}

VARIABLE = "TF_VAR_arc_access_token"


class MintError(Exception):
    """The token exchange did not produce a token."""


def endpoints(cloud):
    """Login host and scope for a cloud name, defaulting to the public cloud."""
    if not cloud:
        cloud = "AzureCloud"
    if cloud not in CLOUDS:
        raise MintError(
            "unknown cloud '%s'. Supported: %s"
            % (cloud, ", ".join(sorted(CLOUDS)))
        )
    return CLOUDS[cloud]


def mint(sp_id, sp_secret, tenant_id, cloud, opener=None):
    """Exchange client credentials for an ARM access token.

    `opener` exists for the tests: nothing in this repository should make a
    network call to prove it parses a JSON document correctly.
    """
    login_host, scope = endpoints(cloud)
    # safe="" so a tenant id carrying a slash cannot walk the path. quote()
    # leaves "/" alone by default, which is the wrong default here.
    url = "%s/%s/oauth2/v2.0/token" % (
        login_host,
        urllib.parse.quote(tenant_id, safe=""),
    )

    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": sp_id,
            "client_secret": sp_secret,
            "scope": scope,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    send = opener or urllib.request.urlopen

    try:
        with send(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The error body carries an `error_description` naming the actual
        # problem - expired secret, wrong tenant, missing consent - and it is
        # not itself a credential. Losing it would leave a bare 401 to debug,
        # which is the failure mode the 2026-08-28 Proxmox incident was.
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
            detail = parsed.get("error_description") or parsed.get("error") or ""
            detail = detail.splitlines()[0] if detail else ""
        except Exception:  # noqa: BLE001 - a body we cannot parse is not fatal
            detail = ""
        raise MintError(
            "token endpoint returned HTTP %s%s"
            % (exc.code, ": " + detail if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise MintError("could not reach %s: %s" % (login_host, exc.reason)) from exc

    token = payload.get("access_token")
    if not token:
        raise MintError("token endpoint returned no access_token")

    return token


def render(name, value):
    """One `$GITHUB_ENV` entry.

    A JWT is base64url plus dots, so it cannot contain a newline and the plain
    `name=value` form is safe. The blank case has to survive too, and
    `name=` with nothing after it is a valid empty assignment.
    """
    return "%s=%s\n" % (name, value)


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) != 2:
        print("usage: mint_arc_token.py <env-file>", file=sys.stderr)
        return 2

    raw = os.environ.get("GITHUB_SECRETS_JSON", "")
    if not raw.strip():
        print(
            "::error::GITHUB_SECRETS_JSON is empty. The action must be called "
            "with `secrets: ${{ toJSON(secrets) }}`.",
            file=sys.stderr,
        )
        return 2

    try:
        available = json.loads(raw)
    except json.JSONDecodeError as exc:
        # The message never quotes the document: it is the secret set.
        print(
            "::error::GITHUB_SECRETS_JSON is not valid JSON (%s)" % exc.msg,
            file=sys.stderr,
        )
        return 2

    if not isinstance(available, dict):
        print("::error::GITHUB_SECRETS_JSON is not a JSON object", file=sys.stderr)
        return 2

    def secret(name):
        value = available.get(name, "")
        return value.strip() if isinstance(value, str) else ""

    # The canonical names render_terraform_env.py uses. One naming scheme for
    # the whole repository, so a rename is a rename (BUG-003-A4).
    sp_id = secret("TF_VAR_ARC_SP_ID")
    sp_secret = secret("TF_VAR_ARC_SP_SECRET")
    tenant_id = secret("TF_VAR_ARC_TENANT_ID")
    cloud = secret("TF_VAR_ARC_CLOUD")

    if not (sp_id and sp_secret and tenant_id):
        print(
            "arc token: no service principal configured, so no token is minted. "
            "Guests will skip Arc onboarding."
        )
        with open(argv[1], "a", encoding="utf-8") as handle:
            handle.write(render(VARIABLE, ""))
        return 0

    try:
        token = mint(sp_id, sp_secret, tenant_id, cloud)
    except MintError as exc:
        print("::error::arc token: %s" % exc, file=sys.stderr)
        return 1

    # Masked before it is written anywhere. A token is not a password but it
    # authenticates as one until it expires, and the guest templates echo their
    # own configuration into logs.
    print("::add-mask::%s" % token)

    with open(argv[1], "a", encoding="utf-8") as handle:
        handle.write(render(VARIABLE, token))

    print("arc token: minted for %s, %s set" % (cloud or "AzureCloud", VARIABLE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
