#!/usr/bin/env python3
"""Did the guest's first-boot configuration actually run? (OPS-004, #176)

The check that was missing. VM 101 ran for days with no administrator password
set by us, no RDP, no DNS and no Arc, because `cicustom: vendor=` lands in
`/openstack/latest/vendor_data.json` and Cloudbase-Init executes nothing there.
Five merged pull requests were believed to have taken effect and had not.

Nothing would have noticed. The post-apply smoke test asks whether the VM exists
and answers through the guest agent - both true of a guest whose first-boot
document never ran. Terraform is equally satisfied: it wrote a snippet and
attached it, and what the guest does with it is not a resource attribute.

So this asks the guest instead, through the one API that can:

    GET /nodes/{node}/qemu/{vmid}/agent/file-read?file=<marker>

One synchronous call per VM. `agent/exec` would also work and needs a pid and a
second call to poll it; reading a file is the whole question here.

## The markers

  linux    /var/lib/cloud/instance/boot-finished
  windows  C:\\ProgramData\\vm-factory-firstboot.done

The Linux one is cloud-init's own, written after every module including
`runcmd`. The Windows one is written by `windows.yaml.tftpl` itself, near the
top, which is why its absence on VM 101 was conclusive.

## Why a missing marker warns rather than fails

The same reason an absent Arc machine does. A guest created by *this* apply may
still be working: the Linux template updates packages and the onboarding script
retries for up to ten minutes, so `boot-finished` legitimately arrives long
after Terraform is done. "Not yet" and "never" are indistinguishable inside any
window worth holding the terraform-lab-state concurrency group for.

That is weaker than it sounds in the case that matters. A guest whose first-boot
document *cannot* run warns on **every** apply, forever - not intermittently -
and OPS-004 went unnoticed for months with no signal at all.

Usage:
  PROXMOX_API_TOKEN=... verify_first_boot.py --inventory vm-inventory.json \
    --endpoint https://host:8006 --node pve [--insecure]

Exit codes: 0 checked, 2 an input or the API could not be used - which is never
reported as "first boot ran".
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

# Raw strings, and that is not style. Written as an ordinary literal, the
# Windows path contains `\v` - which Python reads as a vertical tab, silently
# producing `C:\ProgramData<VT>m-factory-firstboot.done`. The check would then
# have warned about every Windows guest forever, which looks exactly like the
# defect it exists to find. Caught by asserting the value rather than reading it.
MARKERS = {
    "linux": "/var/lib/cloud/instance/boot-finished",
    "windows": r"C:\ProgramData\vm-factory-firstboot.done",
}

TIMEOUT = 20


class Unusable(Exception):
    """The check could not be made. Never the same as the check passing."""


def marker_present(endpoint, node, vmid, path, token, insecure):
    """True if the guest has the file, False if it does not.

    A 500 from this endpoint is the ordinary "no such file" answer as well as a
    real fault, so it is read as absent rather than raised - the caller reports
    absence as a warning either way, and turning a missing marker into a failed
    run would make the common case the noisy one.
    """
    query = urllib.parse.urlencode({"file": path})
    url = f"{endpoint.rstrip('/')}/api2/json/nodes/{node}/qemu/{vmid}/agent/file-read?{query}"
    request = urllib.request.Request(url, headers={"Authorization": f"PVEAPIToken={token}"})

    context = None
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
            json.loads(response.read().decode("utf-8"))
        return True
    except urllib.error.HTTPError as exc:
        if exc.code in (500, 400):
            return False
        if exc.code in (401, 403):
            raise Unusable(
                f"the API refused the token ({exc.code}). This check proves "
                "nothing about the guests - see docs/proxmox-api-token.md."
            ) from exc
        raise Unusable(f"unexpected HTTP {exc.code} from the guest agent API") from exc
    except (urllib.error.URLError, ssl.SSLError, TimeoutError) as exc:
        raise Unusable(f"could not reach {endpoint} ({exc})") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("PROXMOX_API_TOKEN", "").strip()
    if not token:
        print("::error::PROXMOX_API_TOKEN is empty. Nothing was checked.", file=sys.stderr)
        return 2

    try:
        with open(args.inventory, "r", encoding="utf-8") as handle:
            inventory = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::could not read {args.inventory} ({exc})", file=sys.stderr)
        return 2

    if not inventory:
        print("No VMs in the inventory; nothing to check.")
        return 0

    missing, checked = [], 0
    for name, vm in sorted(inventory.items()):
        vmid = vm.get("vm_id_actual")
        os_name = vm.get("os")
        marker = MARKERS.get(os_name)
        if vmid is None or marker is None:
            print(f"::warning::{name}: no VM id or unknown os '{os_name}'; not checked.")
            continue
        try:
            present = marker_present(args.endpoint, args.node, vmid, marker, token, args.insecure)
        except Unusable as exc:
            print(f"::error::first-boot check: {exc}", file=sys.stderr)
            return 2
        checked += 1
        if not present:
            missing.append((name, os_name, marker))

    print(f"Checked first-boot completion on {checked} guest(s).")
    for name, os_name, marker in missing:
        print(
            f"::warning::{name}: {marker} is absent. Either first boot has not "
            f"finished yet, or the {os_name} first-boot document never ran - "
            "which is OPS-004 (#176), and looked exactly like this."
        )
    if not missing and checked:
        print("Every guest reports its first-boot configuration completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
