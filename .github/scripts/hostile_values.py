#!/usr/bin/env python3
"""The credential values that break naive string handling, in one place.

BUG-021-A5 asks that this issue and BUG-010 (#52) share one fixture, so the
value lives here rather than in either suite. They are two halves of the same
problem:

- BUG-010 is about these characters reaching a guest script unescaped, where a
  quote closes a shell string and the remainder runs as code.
- BUG-021 is about the same characters being *re-spelled* on the way into a log,
  where the leak guard's substring test then failed to recognise them.

A credential chosen to be strong is more likely to contain them than a weak one,
which is what made both defects worse than they looked.

BUG-010 landed in #136 and did write a second value, and the two drifted exactly
as this warned. Measured before merging them:

    character class          leak guard   injection
    double quote                    yes         yes
    single quote                     no         yes
    backslash                       yes          no
    newline                         yes         yes
    dollar                           no         yes
    backtick                         no         yes
    command substitution             no         yes

So the injection suite never tested a backslash crossing the template boundary,
and the leak guard never tested command substitution being re-spelled into a
log. Neither gap is hypothetical: a backslash is what BUG-021 found the guard
blind to, and `$(...)` is what BUG-010 found executing in a guest.

One value now, and it is the union. Both suites import it.

Nothing here is a real credential. The values are deliberately self-identifying
so that one appearing in a log or an artifact is unambiguously a test fixture.

On the `gitleaks:allow` markers: the repository-wide secret scan (CHORE-002-A5)
flags both lines, because its generic-api-key rule matches a quoted string
assigned to a name containing "password" - which is exactly what these are
called and exactly what they are not. The marker suppresses the finding on the
line it sits on. That is narrower than a .gitleaksignore entry and it stays next
to the value, so the exemption is visible to whoever reads it rather than filed
somewhere else.
"""

# The union of what both defects are about, in one value.
#
# Terraform rewrites the quote, the backslash and the newline on the way into
# plan output, which is what BUG-021's substring guard was blind to. The shell
# and PowerShell act on the dollar, the backtick, `$(...)` and the quotes, which
# is what BUG-010 found executing in a guest. Every one of them is here because
# a fixture missing any is a suite that passes without testing its own defect.
# Both stay on one physical line each, however long. The `gitleaks:allow` marker
# suppresses the finding on the line it sits on - so splitting the value across a
# parenthesised expression would leave the value on one line and the marker on
# another, and the scan would flag it. That is the kind of quiet regression this
# file's own note about the markers exists to prevent.
HOSTILE_PASSWORD = 'bug021-canary-"dq-\'sq-\\backslash-$dollar-`backtick`-$(id -un)-$env:USERNAME\nsecond-line-4d1e8b'  # noqa: E501  # gitleaks:allow

# Single-line variant, for the places where a newline is not survivable at all
# (a command line, an ini-style file). Same characters otherwise.
HOSTILE_PASSWORD_SINGLE_LINE = 'bug021-canary-"dq-\'sq-\\backslash-$dollar-`backtick`-$(id -un)-4d1e8b'  # noqa: E501  # gitleaks:allow
