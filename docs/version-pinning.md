# Version pinning — actions and Terraform

Why this file exists: these workflows run on a self-hosted runner that holds the
Proxmox API token, the Proxmox root SSH password and the Arc service principal.
A mutable action tag means the code executing next to those credentials can
change without a commit in this repository. Everything here is pinned, and
everything pinned needs a way to be updated — otherwise pinning just becomes a
different kind of staleness.

## What is pinned

| Thing | Pinned in | Current |
|---|---|---|
| `actions/checkout` | all four workflows | `3d3c42e` · v7.0.1 |
| `hashicorp/setup-terraform` | `terraform-*.yml` | `dfe3c3f` · v4.0.1 |
| `actions/upload-artifact` | `terraform-apply.yml` | `043fb46` · v7.0.1 |
| `raven-actions/actionlint` | `checks.yml` | `3d39aea` · v2.2.0 |
| actionlint **tool** | `version:` input in `checks.yml` | 1.7.12 |
| `terraform-linters/setup-tflint` | `checks.yml` | `6e1e064` · v6.3.0 |
| tflint **tool** | `tflint_version:` input in `checks.yml` | v0.64.0 |
| gitleaks **tool** | `GITLEAKS_VERSION` + `GITLEAKS_SHA256` in `checks.yml` | 8.30.1 |
| Terraform CLI | `terraform_version:` in the three `terraform-*.yml` workflows | 1.15.9 |
| Terraform CLI | `required_version` in `providers.tf` | `~> 1.15.0` |
| bpg/proxmox provider | `providers.tf` + `.terraform.lock.hcl` | `~> 0.111.0`, locked to 0.111.1 |

Two rows each for actionlint and tflint, because pinning the action is not the
same as pinning the tool. `raven-actions/actionlint` defaults its `version` input to `latest`,
which it resolves by following the `releases/latest` redirect **at run time** and
downloading the tarball with no checksum check. Leave it at the default and the
action SHA is pinned while the binary it fetches is not - a new actionlint
release can redden every open pull request with no commit in this repository.
`terraform-linters/setup-tflint` defaults the same way, so `tflint_version` is
pinned beside it.

gitleaks has no row for an action because it is not installed by one. The
checks job downloads the release tarball and verifies it against
`GITLEAKS_SHA256` before running it - see the comment in `checks.yml` for why
the official action is not used. Updating it means changing both the version
and the checksum, which comes from the release's `gitleaks_<version>_checksums.txt`.

The two Terraform CLI rows must always agree. `~> 1.15.0` allows patch releases within 1.15,
so a local `terraform` on 1.15.x behaves like CI; a 1.16 release will fail fast
instead of silently diverging.

## Updating an action pin

1. Find the newest release and resolve its tag to a commit SHA:

   ```bash
   gh api repos/actions/checkout/releases/latest --jq '.tag_name'
   gh api repos/actions/checkout/commits/<tag> --jq '.sha'
   ```

2. Replace the SHA and update the trailing `# <version>` comment. The comment is
   the only human-readable record of what the SHA is — keep the two in sync.
3. Read the action's release notes for input or behaviour changes.
4. Merge through a PR so `terraform-plan` exercises the new pin before
   `terraform-apply` runs it against real infrastructure.

## Updating the Terraform version

Terraform will not downgrade a state file once a newer version has written it,
so the version bump is one-way in practice. Before changing `terraform_version`:

1. Read the upgrade notes for every minor release being crossed.
2. Copy the state file to a scratch path and run `terraform plan` against the
   copy with the new version. **The plan must be empty.** A non-empty plan means
   the new version reads the state differently and the bump needs investigation,
   not an apply.
3. Raise `terraform_version` in all three workflows and `required_version` in
   `providers.tf` in the same commit.
4. Attach the scratch-state plan output to the PR.

Rollback is reverting the pins — but only works if no apply has run on the new
version, for the state-file reason above.

## Updating the actionlint tool version

`gh api repos/rhysd/actionlint/releases/latest --jq '.tag_name'` gives the newest
release. Raise the `version:` input in `checks.yml`, then read the release notes:
actionlint adds *rules*, so a bump can turn a previously green workflow red for
reasons unrelated to the change under review. That is the point of pinning it -
the new findings arrive in a PR that is about the bump, not somebody else's.

## Automated bumps

`.github/dependabot.yml` (CHORE-006) opens weekly pull requests for two of the
rows above: the action SHAs in `.github/workflows/`, and the `bpg/proxmox`
constraint in `providers.tf`. Dependabot rewrites the SHA and the trailing
`# v7.0.1` comment together, so the two stay in sync on its PRs the way step 2
of *Updating an action pin* requires on a manual one.

### What this does not cover

Most of the table. Dependabot understands dependency manifests, and the
remaining pins are workflow *inputs* or shell variables that no ecosystem
parser reads:

| Not covered | Why | Updated by |
|---|---|---|
| actionlint **tool** (`version:`) | an input to the action, not a dependency | hand · see above |
| tflint **tool** (`tflint_version:`) | same | hand |
| gitleaks (`GITLEAKS_VERSION` + `GITLEAKS_SHA256`) | downloaded in a `run:` body; the checksum has to move with it | hand |
| Terraform CLI (`terraform_version:` ×3, `required_version`) | four sites that must agree; see *Updating the Terraform version* | hand |
| `.terraform.lock.hcl` | see below | usually hand |

So the sprint check does not go away — it shrinks to the rows in this table.

### Reviewing a provider bump

Not a rubber stamp. A provider release can change how a resource is read or
written, and this repository applies to real infrastructure on merge to `main`.

1. **Check the lock file.** The constraint in `providers.tf` and the checksums
   in `.terraform.lock.hcl` are separate; if Dependabot moved only the first,
   `terraform init` fails on the mismatch. Regenerate with
   `terraform providers lock -platform=linux_amd64` and commit the result to the
   same PR.
2. **Read the release notes** for every version being crossed, not just the
   newest. Look for resource-schema changes to `proxmox_virtual_environment_vm`
   and `proxmox_virtual_environment_file` in particular — those are the two this
   configuration uses.
3. **Get a plan against the real state.** Dependabot's PRs deliberately do not
   run `terraform-plan` on the lab runner (CHORE-006-A2, and the comment in
   `terraform-plan.yml` explains why), so this step is manual: check the branch
   out and plan it yourself. **The plan must be empty.** A non-empty plan means
   the new provider reads existing resources differently, and that is a finding
   to investigate rather than an apply to approve.
4. Merge only after 1–3. The apply happens on merge.

### Reviewing an action bump

Read the release notes for input or behaviour changes, and confirm the SHA
really is the tag Dependabot claims:

```bash
gh api repos/actions/checkout/commits/<tag> --jq '.sha'
```

The trailing comment is the only human-readable record of what the SHA is, and
a comment that disagrees with its SHA is worse than no comment.

## Ownership

The hand-updated rows above get checked when touching a workflow for any other
reason, and at minimum once per sprint.

tflint, actionlint and gitleaks all add *rules* between releases, so a bump can
turn a green tree red for reasons unrelated to the change under review. Bump
them in their own pull request — that is the point of pinning them.
