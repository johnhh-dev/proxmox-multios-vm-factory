#!/usr/bin/env python3
"""BUG-024. Refuse to start an apply against a Proxmox cluster that cannot
accept writes.

Every VM configuration in Proxmox lives in /etc/pve, which is pmxcfs - a
filesystem replicated between nodes by corosync, and read-only in any partition
that is not quorate. Cloning a VM writes
/etc/pve/nodes/<node>/qemu-server/<vmid>.conf, so an inquorate cluster fails the
clone with `HTTP 500 - cluster not ready - no quorum?` no matter which node the
VM was destined for.

Terraform has no idea. It plans, starts creating, and stops partway through:
run 33074685788 planned three resources, created the Arc marker and uploaded the
user-data snippet - neither of which touches /etc/pve - and failed on the VM. The
state left behind is consistent but incomplete, and the operator gets a
provider-level HTTP 500 rather than a sentence naming the problem.

A read of /cluster/status before the plan turns that into a fast, legible
failure. It cannot prevent a node dying mid-apply; it removes the case where the
cluster was already unwritable when the run started, which is the case that
actually happened.

Two votes is the whole story for a two-node cluster: quorum is a majority, a
majority of two is two, and one node down means no writes anywhere. See
docs/proxmox-cluster-quorum.md.

Usage:
  preflight_cluster.py --endpoint-file <file>   # a terraform console string
  preflight_cluster.py --endpoint https://host:8006

Reads the API token from TF_VAR_proxmox_api_token.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 20


def evaluate(payload):
    """Decide whether the cluster will accept a write. Returns (ok, message).

    Pure, so the cases below are testable without a hypervisor. The API returns
    a list mixing one `cluster` entry with one `node` entry per member; a
    standalone node has no `cluster` entry at all.
    """
    # The API envelope, not the list inside it. `pvesh get /cluster/status
    # --output-format json` strips it, so an operator reproducing this by hand
    # hands a bare list to a function whose whole purpose is a legible failure -
    # and gets `AttributeError: 'list' object has no attribute 'get'`. Which is
    # what happened the first time this was checked against the real cluster.
    if isinstance(payload, list):
        return False, (
            "Got a bare list rather than the API's {\"data\": [...]} envelope. "
            "`pvesh` strips it; the HTTP API does not. Wrap it, or read from "
            "/api2/json/cluster/status."
        )
    if not isinstance(payload, dict):
        return False, "Got %s rather than a JSON object from /cluster/status." % type(payload).__name__

    entries = payload.get("data") or []
    if not entries:
        # Even a standalone node reports itself here. Nothing at all is a shape
        # this script does not recognise, and "probably fine" is the wrong
        # direction to be wrong in when the answer gates an apply.
        return False, (
            "/cluster/status returned no entries. A standalone node reports "
            "itself and a cluster reports every member, so this is neither."
        )

    clusters = [e for e in entries if e.get("type") == "cluster"]
    if not clusters:
        # Not clustered. There is no quorum to lose, and /etc/pve is writable
        # whenever the node is up - so this check has nothing to say.
        return True, "Node is not part of a cluster; quorum does not apply."

    cluster = clusters[0]
    name = cluster.get("name") or "<unnamed>"

    # The API renders booleans as 0/1. The JSON boolean forms are accepted
    # alongside them deliberately rather than by Python's True == 1 accident: if
    # Proxmox ever returns a real boolean, `true` means quorate and blocking on
    # it would stop applies for no reason. Everything else - a string, a null, a
    # 2 - is a shape this script does not understand, and "probably fine" is the
    # wrong direction to be wrong in when the answer gates an apply.
    quorate = cluster.get("quorate")
    if quorate in (1, True):
        return True, "Cluster '%s' is quorate." % name
    if quorate not in (0, False):
        return False, (
            "Cluster '%s' reported quorate=%r, which is neither 0 nor 1. "
            "Refusing to apply against a status this script cannot read."
            % (name, quorate)
        )

    nodes = [e for e in entries if e.get("type") == "node"]
    offline = sorted(e.get("name") or "<unnamed>" for e in nodes if e.get("online") != 1)
    online = sorted(e.get("name") or "<unnamed>" for e in nodes if e.get("online") == 1)

    detail = ""
    if offline:
        detail = " Offline: %s. Online: %s." % (
            ", ".join(offline),
            ", ".join(online) or "none",
        )

    return False, (
        "Cluster '%s' has no quorum, so /etc/pve is read-only and any clone, "
        "create, start or destroy will fail with HTTP 500.%s Bring the missing "
        "node back, or - if it is genuinely down rather than unreachable - run "
        "`pvecm expected 1` on a surviving node. See "
        "docs/proxmox-cluster-quorum.md." % (name, detail)
    )


def fetch(endpoint, token):
    url = endpoint.rstrip("/") + "/api2/json/cluster/status"
    request = urllib.request.Request(url, headers={"Authorization": "PVEAPIToken=" + token})

    # The provider is configured with `insecure = true` against the same
    # endpoint (providers.tf), so verifying here would only make this step fail
    # where the apply behind it succeeds. SEC-006 (#55) is where that changes,
    # and it changes in both places at once.
    context = ssl._create_unverified_context()

    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def read_endpoint(args):
    if args.endpoint:
        return args.endpoint

    with open(args.endpoint_file, "r", encoding="utf-8") as handle:
        raw = handle.read().strip()

    # `terraform console` prints a string as a quoted, escaped JSON document.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--endpoint", help="Proxmox API base URL")
    group.add_argument("--endpoint-file", help="file holding it, as terraform console prints it")
    args = parser.parse_args(argv)

    token = os.environ.get("TF_VAR_proxmox_api_token")
    if not token:
        print("::error::TF_VAR_proxmox_api_token is not set.", file=sys.stderr)
        return 2

    endpoint = read_endpoint(args)

    try:
        payload = fetch(endpoint, token)
    except urllib.error.HTTPError as exc:
        # Deliberately not a pass. An endpoint that will not answer a read is
        # not an endpoint that should be handed an apply.
        print(
            "::error::%s returned HTTP %s for /cluster/status. The apply would "
            "talk to the same endpoint with the same token." % (endpoint, exc.code),
            file=sys.stderr,
        )
        return 1
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(
            "::error::could not read /cluster/status from %s: %s" % (endpoint, exc),
            file=sys.stderr,
        )
        return 1

    ok, message = evaluate(payload)
    if not ok:
        print("::error::" + message, file=sys.stderr)
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
