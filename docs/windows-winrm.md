# Windows remote management

KAN-015 (#19). SEC-008-A5 measured this and deliberately left it; this is the
record of what changed and what it costs.

## What was wrong

Every Windows guest this factory built ran these two lines during first boot:

```powershell
winrm set winrm/config/service/auth '@{Basic="true"}'
winrm set winrm/config/service '@{AllowUnencrypted="true"}'
```

Basic authentication sends `username:password` base64-encoded. That is an
encoding, not encryption — anyone who can read the bytes can read the
credential. `AllowUnencrypted="true"` removes the transport protection that
would otherwise have covered it.

Together, on port 5985 over HTTP, they put the **local Administrator password
on the lab network in recoverable form**, on every Windows guest, by default.

It is worth being precise about how this compares to the two exposures SEC-008
closed. A process command line and a registry value are readable by accounts
that are already on the machine. This one is readable by anything that can
observe the wire, which is a strictly larger set.

## What changed

Neither line runs any more unless a VM asks for it.

Nothing replaces them. That is the entire mechanism: with no `winrm set` at
all, the service keeps the configuration `winrm quickconfig` and
`Enable-PSRemoting` leave behind —

| Setting | Value after this change |
|---|---|
| `Negotiate` | `true` |
| `Kerberos` | `true` |
| `Basic` | `false` |
| `AllowUnencrypted` | `false` |

Negotiate encrypts the payload at the message layer. The transport is still
HTTP on 5985, and the credential is still not readable from it.

## What you have to do differently

**This is the part that will look like a broken change if you have not read
it.** Negotiate against a *workgroup* machine — which every guest from this
factory is — requires the **connecting** host to trust it first. On your own
machine, once per target:

```powershell
Set-Item WSMan:\localhost\Client\TrustedHosts -Value 192.168.10.42 -Concatenate -Force
Enter-PSSession -ComputerName 192.168.10.42 -Credential (Get-Credential)
```

`-Concatenate` matters — without it the assignment **replaces** the list, and
every host you had added before silently stops working.

The failure mode when you skip this is not subtle and not a hang:

```
Connecting to remote server 192.168.10.42 failed with the following error
message : The WinRM client cannot process the request. If the authentication
scheme is different from Kerberos, or if the client computer is not joined to
a domain, then HTTPS transport must be used or the destination machine must be
added to the TrustedHosts configuration setting.
```

That error means the client is refusing to send the credential to a host it
cannot authenticate. It is the change working, not failing.

The guest records which transport it was given, so a guest built before this
change is distinguishable from one built after it without guessing —
`C:\cloudbase-firstboot-test.log`:

```
WinRM: Negotiate only - Basic and unencrypted transport left off (KAN-015). ...
```

## Turning the old behaviour back on

Per VM, in `locals.tf`:

```hcl
win-legacy-01 = {
  os      = "windows"
  network = { type = "dhcp" }
  windows = {
    winrm_allow_unencrypted = true
  }
}
```

or repository-wide with `TF_VAR_windows_winrm_allow_unencrypted_default=true`,
though there is no good reason to make it the default again.

Two things to know before doing it:

- **It is an S2 exposure for as long as it is on**, and the guest says so in
  its own first-boot log rather than leaving you to infer it.
- **It does not reach a running guest.** A template change replaces the
  snippet and leaves the VM alone — see
  [guest-config-changes.md](guest-config-changes.md). Getting it into an
  existing guest is `terraform apply -replace`, or one `winrm set` by hand.

Setting it with `enable_winrm = false` is refused at plan time. The template
emits no WinRM block at all in that case, so the value would do nothing while
the inventory claimed a decision the guest never made.

**Setting it with no `var.management_source_cidrs` is also refused** (KAN-011-A3).
This flag decides whether the credential is recoverable from the wire; that
list decides who can be on the wire. Each was reviewed on its own and accepted
— the exposure here, the unrestricted firewall rule in `windows.yaml.tftpl` —
and the combination never was. Naming one CIDR satisfies it:

```
TF_VAR_management_source_cidrs='["192.168.10.0/24"]'
```

The refusal is not a claim that the transport is safe once the sources are
named. It is that an S2 exposure with a bounded audience is a decision, and one
with an unbounded audience is an accident.

## The part that is still not fixed

**An HTTPS listener on 5986 is the better answer, and it is not here.**

What it would take, in order:

1. **A certificate the client trusts**, with the guest's name or address in it.
   This is the same prerequisite as SEC-006 (#55) — a CA the lab trusts — and
   it is why this is a documented gap rather than a code change today.
2. A listener, in first boot:
   ```powershell
   winrm create winrm/config/Listener?Address=*+Transport=HTTPS `
     "@{Hostname=`"$name`";CertificateThumbprint=`"$thumbprint`"}"
   ```
3. The firewall rule for 5986, and the decision whether 5985 stays open.
4. Certificate renewal, which is a lifecycle this repository does not have one
   of yet for anything.

**A self-signed certificate is not a shortcut.** It encrypts the transport, but
the client then has to connect with `-SkipCACheck -SkipCNCheck`, which accepts
any certificate at all — so it defends against passive observation and not
against a host on the lab network answering for the address. Negotiate over
HTTP already defends against passive observation, with none of the moving
parts, which is why this change does that instead of shipping a self-signed
listener.

## Not verified

**No Windows guest has been built with this change.** What is verified is that
the configuration renders both branches and that the inventory rules behave —
`terraform validate`, `terraform test`, and the plan-per-rule suite in
`.github/scripts/test_config_validation.py`.

What has *not* been observed is a real guest coming up with `Basic=false` and a
`Enter-PSSession` succeeding against it over Negotiate. The claim about
`winrm quickconfig`'s defaults comes from Microsoft's documented defaults for
the WinRM service, not from reading `winrm get winrm/config/service` on a guest
from this factory. First Windows build after this lands is the test; check that
file before assuming it passed.
