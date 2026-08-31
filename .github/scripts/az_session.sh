#!/usr/bin/env bash
# Authenticate the job to Azure for Arc operations (KAN-017-A5, BUG-004).
#
# Extracted from .github/actions/arc-cleanup, which was the only caller until
# the post-apply smoke test needed the same session. Copying six lines of
# `az login` would have been the mistake BUG-004 exists to record: an Arc block
# duplicated between two callers, where one copy carried a defect for months and
# the other had no implementation at all.
#
# A script rather than a composite action, deliberately. arc-cleanup is one step
# with early `exit 0` guards, and in a composite action `exit 0` ends the step
# rather than the action - so splitting it into "log in" and "delete" steps would
# have turned those guards into `if:` conditions across a security-relevant
# action. A script keeps the control flow exactly where it was.
#
# Executed, not sourced: `az login` writes the account and token cache under
# $HOME/.azure, so a session established by a child process is available to
# every later `az` call in the same job.
#
# Credentials are read from the job environment - TF_VAR_arc_* - which the
# terraform-env action populates once per job (BUG-003).
#
# Exit codes, and the middle one is the point:
#   0  logged in, subscription selected, resource provider registered
#   2  Arc is not configured for this lab. Not an error - the caller decides
#      whether that is a skip or a failure, because those differ per caller
#   1  configured, and something went wrong
set -euo pipefail

if [ -z "${TF_VAR_arc_tenant_id:-}" ] \
  || [ -z "${TF_VAR_arc_subscription_id:-}" ] \
  || [ -z "${TF_VAR_arc_resource_group:-}" ] \
  || [ -z "${TF_VAR_arc_sp_id:-}" ] \
  || [ -z "${TF_VAR_arc_sp_secret:-}" ]; then
  exit 2
fi

# SEC-004-A4: this used to be a remote script piped into `sudo bash` on the host
# holding the Proxmox and Arc credentials, in a job that had those credentials in
# its environment. The Azure CLI is a runner prerequisite now - installed once at
# provisioning time, under review - and this only asserts it is present.
if ! command -v az >/dev/null 2>&1; then
  echo "::error::Azure CLI (az) is not installed on this runner. Install it during runner provisioning. See docs/runner-trust-boundary.md."
  exit 1
fi

# The secret is on az's command line here, and moving it off is not something
# this extraction can do: `az login --service-principal` has no file or stdin
# form for the password. What the extraction does buy is that there is now one
# site to fix rather than two. It is bounded by the runner trust boundary
# (SEC-004) and is ADR 0001 section 5's accepted Path 1.
az login --service-principal \
  -u "$TF_VAR_arc_sp_id" \
  -p "$TF_VAR_arc_sp_secret" \
  --tenant "$TF_VAR_arc_tenant_id" >/dev/null
az account set --subscription "$TF_VAR_arc_subscription_id"

RP_STATE="$(az provider show -n Microsoft.HybridCompute --query registrationState -o tsv 2>/dev/null || true)"
if [ "$RP_STATE" != "Registered" ]; then
  echo "Registering resource provider Microsoft.HybridCompute (current state: ${RP_STATE:-unknown})"
  az provider register -n Microsoft.HybridCompute --wait >/dev/null
fi
