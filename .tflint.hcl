# tflint configuration (CHORE-002).
#
# The `terraform` ruleset ships inside the tflint binary, so no `tflint --init`
# and no plugin download at run time: the pinned binary is the whole linter.
# That keeps the tool pin honest - see docs/version-pinning.md - and keeps the
# checks job from reaching out to a second release feed on every run.
#
# `recommended` is the preset tflint maintains; it is a superset of the default
# rules and includes the naming and documentation rules the default set leaves
# off. It is enabled here as a whole rather than rule by rule, so a tflint
# upgrade arrives as findings in the PR that bumps the pin.
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

config {
  # There is one module and it is the root module. Nothing to recurse into.
  call_module_type = "none"
}
