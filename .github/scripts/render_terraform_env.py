#!/usr/bin/env python3
"""Map GitHub secrets onto TF_VAR_* environment entries, in one place (BUG-003).

Every workflow that runs Terraform needs the same set of input variables. They
used to be written out as a `env:` block on each step that needed them, which is
how BUG-003 happened: `TF_VAR_windows_admin_password` was set on `terraform
init`, where it does nothing, and omitted from the step that actually evaluates
the configuration. The PR plan and the merge plan then rendered different
user-data for the same commit - the reviewed diff was not the applied diff.

The missing line was the symptom. Nine hand-maintained copies of the same block
across three workflows was the cause, so the block lives here now and the
workflows carry one step each.

## What this does

Reads `toJSON(secrets)` from GITHUB_SECRETS_JSON and appends `TF_VAR_*` entries
to the file named on the command line - `$GITHUB_ENV` in a workflow, so every
later step in the job sees an identical set. Nothing is printed but names.

## Canonical names

One secret name per variable, and exactly one: the Terraform variable name,
uppercased. No aliases, no fallback chain.

BUG-003 landed with the old names still readable so that renaming the repository
secrets could be a separate, reversible step; every run since has logged a
warning naming the replacement. BUG-003-A4 is the other half - the repository
secrets have been renamed, and the fallbacks are gone.

They are worth not reintroducing. A chain of names for one value is what let
`TF_VAR_VM_PASSWORD` and `VM_PASSWORD` mean the same thing in some workflows and
not in others, and a variable resolved from a different secret depending on which
workflow asked is the shape of BUG-003 itself. Renaming a secret is now a rename,
not an accumulation.

## Required vs optional

Required here means Terraform has no default and the run cannot work without it.
An optional variable that is unset is reported, not defaulted: `main.tf`
coalesces a null password to "", and a Windows VM built with a blank
Administrator password is worth a line in the log.

Usage:
  GITHUB_SECRETS_JSON="$(cat secrets.json)" \\
    python3 render_terraform_env.py "$GITHUB_ENV"

Exit codes: 0 written, 1 a required secret is missing, 2 usage error.
"""

import json
import os
import secrets as secrets_module
import sys

# (terraform variable, secret name, required)
#
# Order is the order of the summary line, so keep related variables together.
#
# SEC-001a note on arc_sp_id / arc_sp_secret: the root module no longer declares
# either, because the service principal stopped being a Terraform input when the
# guest started receiving a minted token instead (ADR 0001 option C). They are
# still rendered here because two runner-side consumers read them out of the job
# environment - .github/actions/arc-cleanup, which calls `az`, and
# .github/actions/arc-token, which mints the token. Terraform ignores a TF_VAR_
# environment variable it has no declaration for, silently and by design, so the
# name is kept rather than introducing a second naming scheme for two values.
VARIABLES: list[tuple[str, str, bool]] = [
    ("proxmox_api_token", "TF_VAR_PROXMOX_API_TOKEN", True),
    ("proxmox_ssh_username", "TF_VAR_PROXMOX_SSH_USERNAME", False),
    # SEC-006-A3. Optional since the node SSH identity stopped being a password
    # or nothing. This table can say "this one secret must exist"; it cannot say
    # "one of these three must", because it resolves each name independently and
    # has no view of the others.
    #
    # So the requirement moved rather than disappeared: locals.tf refuses a
    # configuration with no password, no private key and no agent, at plan time,
    # with a message naming all three ways to fix it. Leaving this `True` would
    # instead force a lab that has moved to a key to keep a root password in its
    # secrets purely to satisfy this line.
    ("proxmox_ssh_password", "TF_VAR_PROXMOX_SSH_PASSWORD", False),
    ("proxmox_ssh_private_key", "TF_VAR_PROXMOX_SSH_PRIVATE_KEY", False),
    ("ssh_public_key", "TF_VAR_SSH_PUBLIC_KEY", True),
    ("linux_vm_password", "TF_VAR_LINUX_VM_PASSWORD", False),
    ("windows_admin_password", "TF_VAR_WINDOWS_ADMIN_PASSWORD", False),
    ("arc_tenant_id", "TF_VAR_ARC_TENANT_ID", False),
    ("arc_subscription_id", "TF_VAR_ARC_SUBSCRIPTION_ID", False),
    ("arc_resource_group", "TF_VAR_ARC_RESOURCE_GROUP", False),
    ("arc_location", "TF_VAR_ARC_LOCATION", False),
    ("arc_cloud", "TF_VAR_ARC_CLOUD", False),
    ("arc_sp_id", "TF_VAR_ARC_SP_ID", False),
    ("arc_sp_secret", "TF_VAR_ARC_SP_SECRET", False),
]


# ---------------------------------------------------------------------------
# Non-secret settings, from repository variables (KAN-012)
# ---------------------------------------------------------------------------
# The lab's topology is not secret - endpoint, node, bridge, resolvers,
# datastores, template IDs - but it was only settable by editing variables.tf
# and merging it, which makes pointing this factory at a different Proxmox host
# a code change. That is KAN-012.
#
# It also fixes something documented and broken. docs/proxmox-api-token.md tells
# an operator to turn certificate validation on by "setting the repository
# variable TF_VAR_proxmox_tls_insecure to false. No code change and no PR", and
# operator-setup.md says the same for TF_VAR_proxmox_ssh_agent. **Neither
# worked.** GitHub repository variables are only available through the `vars`
# expression context; they are not environment variables, and nothing in these
# workflows referenced `vars` except TF_BOOTSTRAP - which needs its own explicit
# `env:` mapping, and is the proof.
#
# So an operator following that procedure would have set the variable, seen a
# green apply, and believed certificate validation was on while it was still
# off. A silent no-op on a security control is worse than the control being
# missing, because the missing one is at least visible.
#
# ## Why an allowlist rather than passing every TF_VAR_* through
#
# Because `vars` is writable by anyone with repository admin and is not audited
# the way code is. An unfiltered pass-through would make every Terraform input a
# remotely settable value, including the ones that decide whether guests get
# password SSH or an unencrypted WinRM transport. Those are here on purpose and
# each is a deliberate entry; what is *not* here is anything that carries a
# credential. Secrets travel through the table above, which reads the `secrets`
# context, and a secret must never be settable from `vars`.
#
# ## Names
#
# The repository variable name is the environment variable name, exactly as the
# docs already promised - `TF_VAR_proxmox_tls_insecure`, not a separate
# uppercase alias. Secrets use the uppercase canonical form because they predate
# this and renaming them is CHORE-007's finished work; settings have no such
# history and a second naming scheme would be one more thing to get wrong.
#
# (name, kind). "bool" is validated; "string" is passed through.
SETTINGS: list[tuple[str, str]] = [
    # Connection to the hypervisor
    ("TF_VAR_proxmox_endpoint", "string"),
    ("TF_VAR_proxmox_node_name", "string"),
    ("TF_VAR_proxmox_ssh_nodes", "string"),
    ("TF_VAR_proxmox_ssh_port", "string"),
    # SEC-006. The two that were documented as working and were not.
    ("TF_VAR_proxmox_tls_insecure", "bool"),
    ("TF_VAR_proxmox_ssh_agent", "bool"),
    ("TF_VAR_proxmox_ssh_agent_socket", "string"),
    # Where things live on the node
    ("TF_VAR_snippets_datastore", "string"),
    ("TF_VAR_vm_datastore_id", "string"),
    ("TF_VAR_protected_vm_ids", "string"),
    ("TF_VAR_template_vmid_linux", "string"),
    ("TF_VAR_template_vmid_windows", "string"),
    ("TF_VAR_template_disk_gb_linux", "string"),
    ("TF_VAR_template_disk_gb_windows", "string"),
    # Network and DNS - the half KAN-012 names in its title
    ("TF_VAR_bridge", "string"),
    ("TF_VAR_dns_server", "string"),
    ("TF_VAR_dns_servers_fallback", "string"),
    ("TF_VAR_search_domain", "string"),
    ("TF_VAR_network_probe_host", "string"),
    # Guest-facing defaults that are decisions, not secrets
    ("TF_VAR_linux_password_auth", "bool"),
    ("TF_VAR_windows_enable_winrm_default", "bool"),
    ("TF_VAR_windows_winrm_allow_unencrypted_default", "bool"),
    ("TF_VAR_windows_autologon_default", "bool"),
    # Arc placement. The credentials are secrets and are not here.
    #
    # arc_location, arc_cloud and proxmox_ssh_username are deliberately absent
    # even though none of the three is a secret: all three are already resolved
    # from the `secrets` context by the table above, and listing them here would
    # give one Terraform variable two sources that could disagree. The
    # disjointness test found this, not the author.
    #
    # That they are declared as secrets at all is worth fixing - a value nobody
    # needs to hide costs a repository secret and a rotation procedure it does
    # not need - but moving them would break every lab that has set them, so it
    # is its own change rather than a side effect of this one.
    ("TF_VAR_arc_enabled_default", "bool"),
    ("TF_VAR_arc_install_script_url", "string"),
]

# Terraform accepts these for a bool-typed variable. Anything else is a
# configuration error worth naming here rather than letting Terraform report it
# as a type conversion failure on a line the operator did not write.
BOOLS = {"true", "false", "1", "0"}


class MissingSecret(Exception):
    """A required secret is absent or empty."""


class BadSetting(Exception):
    """A repository variable holds something Terraform cannot use."""


def resolve_settings(available: dict) -> tuple[dict, list]:
    """Pick up the allowlisted repository variables that are actually set.

    An unset or blank variable is **omitted entirely**, not emitted as "". That
    distinction is the whole contract: variables.tf holds the default, and an
    empty TF_VAR_ would override that default with an empty string rather than
    fall back to it. SEC-006-A3 found the same trap from the provider's side -
    an empty ssh password is rejected outright where a null is fine.
    """
    resolved: dict = {}
    bad: list = []

    for name, kind in SETTINGS:
        value = available.get(name, "")
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()

        if kind == "bool" and value.lower() not in BOOLS:
            bad.append(
                "%s is %r; a bool setting must be one of %s"
                % (name, value, ", ".join(sorted(BOOLS)))
            )
            continue

        resolved[name] = value

    return resolved, bad


def resolve(
    available: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Pick a value for each variable from the secrets that were supplied.

    Returns the TF_VAR_* mapping and the names of the optional variables that no
    secret supplied. One name is consulted per variable (BUG-003-A4): a secret
    under any other name is not a source, it is a secret nothing reads.
    """
    resolved: dict[str, str] = {}
    unset: list[str] = []
    missing: list[str] = []

    for variable, canonical, required in VARIABLES:
        value = available.get(canonical, "")

        if not value:
            if required:
                missing.append(canonical)
            else:
                unset.append(f"TF_VAR_{variable}")
            continue

        resolved[f"TF_VAR_{variable}"] = value

    if missing:
        raise MissingSecret(
            "no value for required secret(s): "
            + ", ".join(missing)
            + ". Set them under Settings -> Secrets and variables -> Actions."
        )

    return resolved, unset


def render(name: str, value: str) -> str:
    """One `$GITHUB_ENV` entry, in the heredoc form that survives a newline.

    The delimiter is random per value and checked against the value itself. A
    password containing the delimiter would otherwise end the entry early and
    leave the remainder of the secret being parsed as further assignments.
    """
    for _ in range(8):
        delimiter = f"ghenv_{secrets_module.token_hex(16)}"
        if delimiter not in value:
            return f"{name}<<{delimiter}\n{value}\n{delimiter}\n"
    # Unreachable short of a value built to collide with 128 random bits, but
    # failing is the only safe outcome if it ever happens.
    raise MissingSecret(f"could not find a safe heredoc delimiter for {name}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render_terraform_env.py <env-file>", file=sys.stderr)
        return 2

    raw = os.environ.get("GITHUB_SECRETS_JSON", "")
    if not raw.strip():
        print(
            "::error::GITHUB_SECRETS_JSON is empty. The action must be called "
            "with `secrets: ${{ toJSON(secrets) }}`.",
            file=sys.stderr,
        )
        return 2

    # KAN-012. A repository with no variables set produces "{}" here, so an
    # empty document is the ordinary case rather than a caller error - unlike
    # the secrets document above, which is empty only when the action was called
    # wrong. Absent entirely means the same thing: no settings.
    settings_raw = os.environ.get("GITHUB_VARS_JSON", "").strip() or "{}"
    try:
        vars_available = json.loads(settings_raw)
    except json.JSONDecodeError as exc:
        print(f"::error::GITHUB_VARS_JSON is not valid JSON ({exc.msg})", file=sys.stderr)
        return 2
    if not isinstance(vars_available, dict):
        print("::error::GITHUB_VARS_JSON is not a JSON object", file=sys.stderr)
        return 2

    try:
        available = json.loads(raw)
    except json.JSONDecodeError as exc:
        # The message never quotes the document: it is the secret set.
        print(f"::error::GITHUB_SECRETS_JSON is not valid JSON ({exc.msg})", file=sys.stderr)
        return 2

    if not isinstance(available, dict):
        print("::error::GITHUB_SECRETS_JSON is not a JSON object", file=sys.stderr)
        return 2

    available = {
        key: value for key, value in available.items() if isinstance(value, str)
    }

    try:
        resolved, unset = resolve(available)
    except MissingSecret as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    settings, bad = resolve_settings(vars_available)
    if bad:
        for line in bad:
            print(f"::error::{line}", file=sys.stderr)
        return 1

    # A repository variable must never be able to supply a secret. The tables
    # are disjoint by construction - SETTINGS carries no credential - and this
    # asserts it at runtime, because the cost of getting it wrong later is a
    # credential settable by anyone with repository admin and no audit trail.
    collisions = sorted(set(settings) & set(resolved))
    if collisions:
        print(
            "::error::repository variable(s) %s would override a secret. "
            "SETTINGS and VARIABLES must stay disjoint." % ", ".join(collisions),
            file=sys.stderr,
        )
        return 1
    resolved.update(settings)

    try:
        with open(sys.argv[1], "a", encoding="utf-8") as handle:
            for name, value in resolved.items():
                handle.write(render(name, value))
    except MissingSecret as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print(f"terraform env: {len(resolved)} variable(s) set for this job")
    print("  set:   " + ", ".join(sorted(resolved)))
    if settings:
        print("  from repository variables: " + ", ".join(sorted(settings)))
    if unset:
        print("  unset: " + ", ".join(sorted(unset)))
        print(
            "  an unset optional variable is not an error, but a guest built "
            "with a blank password is a real outcome - check the list above is "
            "what you expect."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
