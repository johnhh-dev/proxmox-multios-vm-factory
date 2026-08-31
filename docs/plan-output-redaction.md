# Keeping secrets out of the plan log

Why this file exists: `terraform plan` prints the full diff of every attribute
it is about to change. One of those attributes is `source_raw.data` on the
cloud-init snippet — the rendered user-data, which contains the Linux user
password, the Windows Administrator password and the Azure Arc service-principal
secret. The Actions job log is readable by anyone with read access to this
repository, and a pull request from a fork can produce that log without anyone
approving a merge.

This is SEC-003. The related leak through uploaded artifacts is SEC-002 and is
already closed; the two share nothing but a cause.

## Why GitHub's masking is not the answer

GitHub redacts literal `secrets.*` values it recognises in a log line. Three
things defeat that here:

1. The rendered template is a derived, multi-line string. Masking matches within
   a line, so a value that Terraform wraps or indents across a line break can
   emerge unmasked.
2. Values sourced from `vars.*` are **never** masked. Repository variables are
   not secrets as far as the runner is concerned.
3. Masking is a display filter on a log that has already been written. It is a
   courtesy, not a control.

## The two mechanisms in place

### 1. Terraform redacts the snippet itself

[`main.tf`](../main.tf) wraps the `templatefile()` call in `sensitive()`, so the
plan renders `(sensitive value)` in place of the body:

```
+ source_raw {
    + data      = (sensitive value)
    + file_name = "vm1-user-data.yaml"
  }
```

The mark is applied explicitly rather than left to propagate from the input
variables. `coalesce(var.linux_vm_password, "")` returns the `""` literal
whenever the variable is null, and a literal carries no sensitivity mark — so
relying on propagation means an unset password silently un-redacts the entire
snippet, which is exactly the configuration a new operator starts from.

This covers `terraform-apply.yml` as well. Its `terraform plan -out=tfplan` step
prints the same diff to the same kind of log.

### 2. CI refuses to publish output it has not checked

[`terraform-plan.yml`](../.github/workflows/terraform-plan.yml) no longer lets
Terraform write to the log at all. The plan is captured to `plan.txt` (stdout and
stderr both — a Terraform error can quote the value that caused it), then:

| Step | What it does |
|---|---|
| Leak guard canary self-test | Proves the scanner still fires, before it is trusted |
| Terraform plan (captured) | Writes to `plan.txt`, prints only the exit code |
| Scan captured plan | [`assert_no_secrets.py`](../.github/scripts/assert_no_secrets.py) — fails the job on a hit |
| Publish plan output | `cat plan.txt`, reached only if the scan passed |
| Remove the captured plan | `always()` — nothing is left on the self-hosted runner |

If the scan finds something, the job fails and the file is never printed. The
leak is contained on the runner instead of published.

The scanner reads the values to look for from the environment and prints only
the *name* of the variable that leaked. It never echoes a value, and it fails
when it had nothing scannable — a guard that checked nothing must not report
success.

## Running the guard locally

```bash
python3 .github/scripts/test_assert_no_secrets.py     # canary suite
SECRET_VARS='MY_SECRET' MY_SECRET='hunter2xyz' \
  python3 .github/scripts/assert_no_secrets.py some-file.txt
```

Exit codes: `0` clean, `1` secret found (or nothing scannable), `2` usage error.

Values shorter than `SECRET_MIN_LEN` (default 6) are skipped and reported,
because scanning a plan for a three-character string matches ordinary English
and would block every run. If a real credential is that short, the fix is to
lengthen the credential, not the threshold.

## Arc values are secrets

Every `TF_VAR_arc_*` input is now read from `secrets.*` only. The former
`secrets.X || secrets.Y || vars.Z` chains are gone from all three workflows.

The first two links were always the same secret — GitHub secret names are
case-insensitive, so `secrets.TF_VAR_arc_tenant_id` and
`secrets.TF_VAR_ARC_TENANT_ID` resolve identically. Only the `vars.*` tail
changed behaviour, and it changed it for the worse.

Required secrets, canonical names:

| Secret | Sensitive |
|---|---|
| `TF_VAR_ARC_TENANT_ID` | identifier |
| `TF_VAR_ARC_SUBSCRIPTION_ID` | identifier |
| `TF_VAR_ARC_RESOURCE_GROUP` | identifier |
| `TF_VAR_ARC_LOCATION` | identifier |
| `TF_VAR_ARC_CLOUD` | identifier |
| `TF_VAR_ARC_SP_ID` | identifier |
| `TF_VAR_ARC_SP_SECRET` | **yes — rotate if it ever appeared in a log** |

SEC-001a added one more value to the scan that is not in the table above,
because no one stores it: `TF_VAR_arc_access_token` is minted per run by
[`.github/actions/arc-token`](../.github/actions/arc-token/action.yml). It is
unlike every other name in the list. The others are values the configuration
must never render; this one it renders into the snippet on purpose, so the scan
is there to prove `sensitive()` still redacts it before the plan is published.
A leaked token expires on its own, which shortens the exposure without removing
it — treat one that reached a public comment as live and let it lapse before
assuming otherwise.

The identifiers are not credentials on their own, but they name the tenant and
subscription this lab attaches to, and there is no reason to publish them. A
full secrets reference belongs in DOC-002.

**Migration:** if any Arc value is currently set as a repository *variable*,
move it to a repository *secret* under the name above before merging, or Arc
onboarding silently falls back to the empty default.

## Adding a new secret input

1. Add the `TF_VAR_*` name to the `SECRET_VARS` list in the scan step, and pass
   its value in that step's `env:`. A secret the scanner does not know about is
   a secret it cannot catch.
2. If it is interpolated into a guest template, confirm the plan still shows
   `(sensitive value)` for `source_raw.data`.
3. Never source it from `vars.*`.
