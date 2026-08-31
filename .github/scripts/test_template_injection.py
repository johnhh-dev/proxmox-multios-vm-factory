#!/usr/bin/env python3
"""A hostile value must survive both guest templates unchanged (BUG-010).

`templatefile` substitutes before the guest's shell ever runs. So a value
interpolated into a quoted string arrives as *characters inside that quote*, and
whatever the shell does with those characters, it does. Before BUG-010:

    ARC_TAGS="${arc_tags}"                  # bash: $(...) and `...` execute
    $passwordPlain = @"                     # PowerShell: $name expands,
    ${windows_admin_password}               # a backtick escapes
    "@

Measured, not argued. `arc.tags = "role=$(id -un)"` ran `id -un` as root during
first boot, and a Windows password containing `$env:USERNAME` reached the guest
as the username - so RDP refused the credential the operator thought they set,
with nothing anywhere saying why.

Both templates now carry free-form values base64-encoded and decode them in the
guest. This asserts that the encoding is actually there and that it actually
round-trips, because a fixture that is only checked once proves nothing about
the next edit.

## What is deliberately *not* encoded

Values held to a shape at plan time: `hostname` and `arc_resource_name` by the
BUG-020 regexes, `arc_enabled` and `linux_password_auth` as bools, `dns_servers`
by jsonencode in locals.tf. This asserts that too - a list that quietly grew a
free-form value would otherwise pass by being absent.

Usage: python3 test_template_injection.py
Requires: terraform on PATH (or TERRAFORM_BIN), bash.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TERRAFORM = os.environ.get("TERRAFORM_BIN", "terraform")

# Every class the issue names: a double quote, a single quote, a dollar, a
# backtick, command substitution in both shells' syntax, a comma, an equals and
# a newline. Nothing here has to be a *plausible* tag - the point is that the
# transport cannot care.
# BUG-021-A5 asked for one fixture shared with the leak guard, and this suite
# wrote its own instead - so the two drifted, and neither covered what the other
# did. This one had no backslash; the leak guard's had no command substitution,
# no backtick and no single quote. hostile_values.py carries the union now, and
# the measurement that motivated merging them.
sys.path.insert(0, HERE)
from hostile_values import HOSTILE_PASSWORD as HOSTILE  # noqa: E402

# Values that must cross the boundary encoded, per template.
ENCODED_LINUX = [
    "network_probe_host",
    "arc_tags",
    "arc_subscription_id",
    "arc_tenant_id",
    "arc_resource_group",
    "arc_location",
    "arc_cloud",
    "arc_install_script_url",
    "arc_access_token",
]
ENCODED_WINDOWS = [
    "network_probe_host",
    "windows_admin_password",
    "arc_subscription_id",
    "arc_tenant_id",
    "arc_resource_group",
    "arc_location",
    "arc_cloud",
    "arc_tags",
    "arc_access_token",
]

# Values allowed to stay interpolated, because something else holds their shape.
# The reason is part of the fixture: if one of these stops being validated, the
# note here is what tells the next reader the exemption went with it.
EXEMPT = {
    "hostname": "BUG-020-A1 regex - lowercase DNS label",
    "fqdn": "built from hostname and search_domain",
    "arc_resource_name": "BUG-020-A4 regex - Azure resource name charset",
    "arc_enabled": "bool rendered by Terraform",
    "linux_password_auth": "bool rendered by Terraform",
    "dns_servers": "jsonencode per element in locals.tf",
    "management_source_cidrs": "IPv4 CIDR rule in locals.tf, then jsonencode per element (KAN-011)",
    "windows_enable_winrm": "bool, and a template directive rather than a value",
    "windows_winrm_allow_unencrypted": "bool, and a template directive rather than a value (KAN-015)",
    "windows_autologon": "bool, and a template directive rather than a value (SEC-001c)",
}

# BUG-010-A5. Both were correct before this change and must stay correct: `$$`
# is how a literal `${` reaches the guest, and getting it wrong turns a working
# script into one that expands a Terraform variable that does not exist.
# KAN-011-A6 added a second one to the Windows template, so this became a list
# per template rather than one each. A `$$` that is right when written and wrong
# after the next edit is the whole failure class here, and an escape nothing
# asserts is one nobody will notice renders as `${env:SystemRoot}` no longer.
KNOWN_GOOD_ESCAPES = {
    "linux": ['"$${CONNECT_ARGS[@]}"'],
    "windows": ["$${env:ProgramFiles}", "$${env:SystemRoot}"],
}


def render(tmp: str, template: str, extra: dict) -> str:
    """Render one template through real `templatefile`."""
    work = os.path.join(tmp, "render-" + os.path.basename(template).split(".")[0])
    os.makedirs(work, exist_ok=True)
    shutil.copy2(os.path.join(REPO, "cloudinit", template), work)

    values = {
        "hostname": "guest-01",
        "fqdn": "guest-01.lab.local",
        "linux_password_auth": False,
        "windows_admin_password": HOSTILE,
        "windows_enable_winrm": True,
        # KAN-015. True renders the branch that still contains the two `winrm
        # set` calls, which is the one with something for the scans below to
        # look at. templatefile fails on a missing key whichever branch is
        # taken, so this is not optional either way.
        "windows_winrm_allow_unencrypted": True,
        # SEC-001c. True renders the branch that still writes the password to
        # the registry, which is the one with something for the scans to look
        # at. templatefile fails on a missing key whichever branch is taken.
        "windows_autologon": True,
        "dns_servers": '"192.168.10.2","192.168.10.1"',
        # KAN-011-A3. Non-empty renders the branch that calls
        # Set-NetFirewallRule, which is the one with something for the scans
        # below to look at. Rendered the same way as dns_servers - jsonencode
        # per element in locals.tf, behind a plan-time CIDR rule.
        "management_source_cidrs": '"192.168.10.0/24"',
        # KAN-012. A hostname reaching both guests, so it is encoded like every
        # other free-form value that crosses the template boundary (BUG-010).
        "network_probe_host": HOSTILE,
        "arc_enabled": True,
        "arc_resource_name": "guest-01",
        "arc_tags": HOSTILE,
        "arc_cloud": HOSTILE,
        "arc_install_script_url": HOSTILE,
        "arc_tenant_id": HOSTILE,
        "arc_subscription_id": HOSTILE,
        "arc_resource_group": HOSTILE,
        "arc_location": HOSTILE,
        "arc_access_token": HOSTILE,
    }
    values.update(extra)

    with open(os.path.join(work, "main.tf"), "w", encoding="utf-8") as f:
        f.write(
            'output "rendered" {\n'
            '  value = templatefile("${path.module}/%s", jsondecode(file("${path.module}/vals.json")))\n'
            "}\n" % template
        )
    with open(os.path.join(work, "vals.json"), "w", encoding="utf-8") as f:
        json.dump(values, f)

    for args in (["init", "-backend=false", "-input=false"], ["apply", "-auto-approve", "-input=false"]):
        r = subprocess.run([TERRAFORM, *args], cwd=work, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"terraform {args[0]} failed for {template}:\n{r.stdout}{r.stderr}")

    r = subprocess.run(
        [TERRAFORM, "output", "-raw", "rendered"], cwd=work, capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"terraform output failed for {template}:\n{r.stdout}{r.stderr}")
    return r.stdout


def strip_comments(text: str) -> str:
    """Drop whole-line comments before scanning for interpolation.

    Both templates comment with `#`, and the comments in both *describe* the
    injection they prevent - `role=$(id -un)`, `$env:USERNAME`, a literal
    `${arc_location}` written with the `$$` escape. Scanning those would make
    the guard fire on its own explanation, and the fix for that would be to
    stop explaining, which is the wrong trade. Only code is scanned; a comment
    reaches no shell.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def check_encoded(rendered: str, names: list[str], template: str, failures: list[str]) -> None:
    """The hostile value must appear only base64-encoded, never as itself."""
    encoded = base64.b64encode(HOSTILE.encode("utf-8")).decode("ascii")
    code = strip_comments(rendered)

    if encoded not in rendered:
        failures.append(
            f"{template}: the base64 form of the hostile value is absent - "
            "nothing is being encoded at all"
        )

    # The distinctive fragments, any one of which means a raw interpolation.
    for fragment in ("$(id -un)", "`hostname`", "$env:USERNAME", "s='sq'"):
        if fragment in code:
            failures.append(
                f"{template}: the hostile value reached the rendered output raw "
                f"({fragment!r}) - some site still interpolates it directly"
            )

    for name in names:
        if re.search(r"\$\{%s\}" % re.escape(name), code):
            failures.append(
                f"{template}: ${{{name}}} survived rendering, which means the "
                "template escaped it rather than substituting it"
            )


def check_linux_roundtrip(rendered: str, failures: list[str]) -> None:
    """Execute the decode in real bash and compare byte for byte."""
    doc = None
    try:
        import yaml  # noqa: PLC0415

        doc = yaml.safe_load(rendered)
    except ImportError:
        failures.append("linux: pyyaml is required to extract arc-onboard.sh")
        return
    except Exception as exc:
        failures.append(f"linux: rendered template is not valid YAML: {exc}")
        return

    script = None
    for entry in doc.get("write_files", []):
        if str(entry.get("path", "")).endswith("arc-onboard.sh"):
            script = entry.get("content")
    if script is None:
        failures.append("linux: arc-onboard.sh not found in write_files")
        return

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "arc-onboard.sh")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(script)

        syntax = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        if syntax.returncode != 0:
            failures.append(f"linux: rendered script is not valid bash:\n{syntax.stderr}")
            return

        # Source only up to the last decode, then print what the shell holds.
        # Running the whole script would try to onboard a machine to Azure.
        lines = script.splitlines()
        end = max(
            i for i, line in enumerate(lines) if line.strip().startswith("ARC_INSTALL_SCRIPT_URL=")
        )
        head = os.path.join(tmp, "head.sh")
        with open(head, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines[: end + 1]) + "\n")

        probe = f'set -u; . "{head}"; printf "%s" "$ARC_TAGS"'
        r = subprocess.run(["bash", "-c", probe], capture_output=True, text=True)
        if r.returncode != 0:
            failures.append(f"linux: sourcing the decode block failed:\n{r.stderr}")
            return
        if r.stdout != HOSTILE:
            failures.append(
                "linux: ARC_TAGS did not round-trip.\n"
                f"  expected: {HOSTILE!r}\n"
                f"  got     : {r.stdout!r}"
            )


def check_windows_roundtrip(rendered: str, failures: list[str]) -> None:
    """No PowerShell on the CI runner, so decode the payloads directly.

    This proves the value Terraform embedded is the value the guest will decode.
    That the decode expression itself is valid PowerShell is covered by the
    parse in SEC-008's verification and by the guest that runs it.
    """
    payloads = re.findall(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", rendered)
    payloads += re.findall(r"ConvertFrom-Base64Utf8 '([A-Za-z0-9+/=]*)'", rendered)
    if not payloads:
        failures.append("windows: no base64 payload found - nothing is encoded")
        return

    decoded = set()
    for p in payloads:
        try:
            decoded.add(base64.b64decode(p).decode("utf-8"))
        except Exception as exc:
            failures.append(f"windows: payload {p[:16]}... does not decode: {exc}")
            return

    if HOSTILE not in decoded:
        failures.append(
            "windows: no payload decodes to the hostile value - "
            f"decoded {sorted(repr(d)[:40] for d in decoded)}"
        )


def check_all_accounted(template_file: str, encoded: list[str], failures: list[str]) -> None:
    """Every value the template consumes is either encoded or exempt.

    This is the check that survives the next edit. The scans above prove the
    values named today are safe; this one fails when a *new* free-form value is
    interpolated and nobody thought about it, which is how BUG-010 happened in
    the first place.
    """
    with open(os.path.join(REPO, "cloudinit", template_file), encoding="utf-8") as f:
        source = f.read()

    used = set(re.findall(r"(?<!\$)\$\{([a-z_][a-z0-9_]*)\}", strip_comments(source)))
    used |= set(re.findall(r"base64encode\(([a-z_][a-z0-9_]*)\)", source))

    for name in sorted(used):
        if name in encoded or name in EXEMPT:
            continue
        failures.append(
            f"{template_file}: '{name}' is interpolated but is neither encoded "
            "nor listed as exempt. If something validates it at plan time, add "
            "it to EXEMPT with the reason; otherwise route it through base64."
        )


def check_known_good_escapes(rendered: str, template: str, failures: list[str]) -> None:
    """BUG-010-A5: the `$$` escapes that were already right."""
    for want in KNOWN_GOOD_ESCAPES[template]:
        expected = want.replace("$$", "$")
        if expected not in rendered:
            failures.append(
                f"{template}: the known-good escape {want} no longer renders as "
                f"{expected} - it was correct when written and must stay correct"
            )


def main() -> int:
    if shutil.which(TERRAFORM) is None and not os.path.isfile(TERRAFORM):
        print(f"FAIL: terraform not found ({TERRAFORM})", file=sys.stderr)
        return 2

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            linux = render(tmp, "linux.yaml.tftpl", {})
            windows = render(tmp, "windows.yaml.tftpl", {})
        except RuntimeError as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 1

        check_encoded(linux, ENCODED_LINUX, "linux", failures)
        check_encoded(windows, ENCODED_WINDOWS, "windows", failures)
        check_linux_roundtrip(linux, failures)
        check_windows_roundtrip(windows, failures)
        check_known_good_escapes(linux, "linux", failures)
        check_known_good_escapes(windows, "windows", failures)
        check_all_accounted("linux.yaml.tftpl", ENCODED_LINUX, failures)
        check_all_accounted("windows.yaml.tftpl", ENCODED_WINDOWS, failures)

    if failures:
        print("", file=sys.stderr)
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        return 1

    print(
        "ok   linux:   hostile value is encoded, decodes in real bash byte for byte\n"
        "ok   windows: hostile value is encoded, every payload decodes\n"
        "ok   both:    no raw interpolation of a free-form value survives\n"
        "ok   both:    the known-good $$ escapes still render correctly\n"
        f"\nA value carrying {len(set(HOSTILE)) } distinct characters - quotes, $, "
        "backtick, comma, equals and a newline - crosses both templates unchanged."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
