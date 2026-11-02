# Signing in with Entra ID

Pinax can sign people in with Microsoft Entra ID itself (`PINAX_AUTH_MODE=oidc`), or with any other identity
provider that speaks OpenID Connect, the standard sign-in protocol Entra ID uses. It is optional: out of the box,
Pinax signs people in with passwords it keeps, and a site without Entra ID never needs this page. With it, no
sign-on proxy is needed; nginx, Caddy, or a load balancer only provides HTTPS. This page explains how it works, what
to ask IT for, how to set Pinax up, and how to try it on a laptop before Entra ID is ready.

## How it works

1. Someone opens Pinax without a session, and *Sign in* sends their browser to Entra ID's sign-in page.
2. They sign in there, with their password and any multi-factor step Entra ID asks for; Pinax never sees either.
3. Entra ID sends the browser back to Pinax (`/auth/callback`) with a one-time code.
4. Pinax exchanges the code, with its client secret, for a signed ID token, checks the token, and matches the person.
5. Pinax gives them a session, as local sign-in does, and returns them to the page they were opening.

What Pinax checks before anyone is signed in:
- **The token is genuine:** signed with one of the provider's published keys, issued by the site's tenant, meant
  for Pinax (its audience), and not expired. It answers this sign-in only: a nonce, and a state value kept for the
  minutes a sign-in takes, and the code exchange is protected with PKCE, so a stolen code is no use elsewhere.
- **The person is one of Pinax's people:** the sign-in name (`preferred_username`, Entra ID's user principal name,
  such as `jdoe@example.org`) is matched to an active person under *People and access*, never made into a new one.
  With `PINAX_OIDC_USERNAME_DOMAINS`, the name must be in one of those domains, and matches a person named either
  `jdoe@example.org` or `jdoe`; a guest account such as `jdoe@example.com` is refused even when a staff member is
  called `jdoe`. Without it, the name must be a person's username exactly.

Anyone refused sees why, with a way to sign in as someone else, and the attempt is in the audit log
(`auth.sign_in_refused`, with the reason), as is every sign-in (`auth.signed_in`) and sign-out. A session lasts
`PINAX_SESSION_HOURS` (12) from sign-in, and is kept by Pinax, so deactivating someone takes effect on their next
click. Signing out ends Pinax's session and Entra ID's. Roles and lab memberships are given in Pinax, under *People
and access*, as with every way of signing in, unless the site maps roles from Entra ID ([Roles from Entra
ID](#roles-from-entra-id)); API tokens work as before.

## The request for IT

Ask the Entra ID team to register Pinax as an application:

| Setting | Value |
|---|---|
| Name | Pinax |
| Supported account types | Accounts in this organizational directory only (single tenant) |
| Platform | Web |
| Redirect URIs | `https://<pinax host>/auth/callback`, and `https://<pinax host>/auth/signed-out` (where Entra ID returns people after signing out) |
| Implicit grant and hybrid flows | Off: Pinax uses the authorization code flow |
| Client secret | One secret; tell the Pinax administrators its expiry date, and send its value securely (not by email) |
| API permissions | Microsoft Graph, delegated: `openid`, `profile`, `email`, with admin consent so people are not asked |
| Optional claims | None needed: `preferred_username` (the user principal name) is in the ID token by default |
| App roles (optional) | Only for [roles from Entra ID](#roles-from-entra-id): an app role for each Pinax role to map, with groups assigned to them |
| Who can sign in | On the enterprise application, set **Assignment required** to Yes and assign the groups of people who use Pinax. Entra ID then refuses everyone else before they reach Pinax. |

Ask them to send back:
- the **Directory (tenant) ID**, which makes the issuer `https://login.microsoftonline.com/<tenant ID>/v2.0`;
- the **Application (client) ID**;
- the **client secret** and its expiry date;
- the domain of staff user principal names (`<username>@example.org`).

The server Pinax runs on needs outbound HTTPS to `login.microsoftonline.com`. Multi-factor sign-in and other
Conditional Access policies apply as usual; Pinax needs nothing for them.

## Setting up Pinax

In `/etc/pinax/pinax.env` (each setting is in [CONFIGURATION.md](CONFIGURATION.md#identity)):

```ini
PINAX_AUTH_MODE=oidc
PINAX_OIDC_ISSUER=https://login.microsoftonline.com/<tenant ID>/v2.0
PINAX_OIDC_CLIENT_ID=<application ID>
PINAX_OIDC_CLIENT_SECRET=<client secret>
PINAX_OIDC_USERNAME_DOMAINS=example.org
PINAX_PUBLIC_URL=https://<pinax host>
PINAX_SESSION_SECRET=<output of: openssl rand -hex 32>
```

Then restart `pinax-web`. Behind nginx or another HTTPS proxy, as for local sign-in
([Signing in](DEPLOYMENT.md#6-signing-in)), with its address in `PINAX_TRUSTED_PROXIES`.

- `PINAX_PUBLIC_URL` must be exactly the address in people's browsers: Entra ID returns them to it, and the
  sign-in's cookie belongs to it.
- Pinax refuses to start, and says what is missing, until the settings are complete.
- People must be in Pinax before they sign in. Usernames are best as user principal names (`jdoe@example.org`)
  from the start, which is what the guides suggest for local sign-in too; with `PINAX_OIDC_USERNAME_DOMAINS`,
  people named `jdoe` match as well. On a new installation, add the first administrator on the server with
  `pinax user add USERNAME --admin`; they add everyone else.
- A site moving from local sign-in keeps its people, labs, roles, and tokens; passwords are no longer used.

**Secrets:**
- The client secret expires (Entra ID allows up to two years). Put its expiry in the operations calendar. To replace
  it, have IT add a new secret, set `PINAX_OIDC_CLIENT_SECRET`, restart `pinax-web`, and then have IT remove the
  old one.
- `PINAX_SESSION_SECRET` signs only the sign-in's own state. Change it if it may have leaked: sign-ins under way
  must start again, and nobody is signed out.

## Roles from Entra ID

Optionally, Pinax's roles (storage administrator, auditor, finance, and division head) come from Entra ID instead of
*People and access*. There are two ways to run:
- **Entra ID for signing in only** (the default): people sign in with Entra ID, and their roles are given in Pinax,
  as with local sign-in.
- **Roles from Entra ID:** a role map names which Entra ID app roles (or groups) give which of Pinax's roles. Each
  role the map names then belongs to Entra ID: Pinax gives or takes it away each time the person signs in, audited
  as the identity provider's change, and the person's page shows it as *From Entra ID*, without a checkbox. Roles
  the map does not name are still given in Pinax. A site can map some roles and not others, such as administrators
  and auditors from Entra ID, and finance and division heads in Pinax.

Lab memberships stay in Pinax either way. A change in Entra ID takes effect at the person's next sign-in (a session
lasts up to `PINAX_SESSION_HOURS`), and turning the map on changes nobody's roles until they sign in. Emergency
accounts never sign in through Entra ID, so the map never changes theirs: a map that leaves no storage administrator
is one of the failures they are for. `pinax user add --admin` on the server still makes an administrator.

**App roles** (preferred). Ask IT to add app roles to Pinax's app registration, one for each role to map, such as
`Pinax.Admin`, `Pinax.Auditor`, `Pinax.Finance`, and `Pinax.Head.BasicSciences`, and to assign groups to them on
the enterprise application. Entra ID then names a person's app roles in the ID token's `roles` claim. In
`/etc/pinax/pinax.env`:

```ini
PINAX_OIDC_ROLE_MAP={"admin": ["Pinax.Admin"], "auditor": ["Pinax.Auditor"], "division:Basic Sciences": ["Pinax.Head.BasicSciences"]}
```

A key is `admin`, `auditor`, `finance`, or `division:` and a division's name as labs have it; its values are the app
roles that give it (any one is enough).

**Groups** instead: with `PINAX_OIDC_ROLES_CLAIM=groups`, the values are group object IDs, which Entra ID sends in
the `groups` claim once IT turns on group claims for the application. Past about 200 groups, Entra ID sends a link
in place of the list; Pinax does not guess, and takes every mapped role away from that person until it can read
them (audited as `auth.roles_unknown`, and logged as a warning). App roles avoid this.

## When Entra ID fails: the emergency sign-in

If Entra ID, or Pinax's setup for it, stops working (an expired client secret, a wrong setting, an outage, someone
removed from the group that is assigned), nobody can sign in. Someone with a shell on the server can always fix the
settings, or switch to local sign-in for a while (`PINAX_AUTH_MODE=local` and a restart, then `pinax user
password`). The **emergency sign-in** is for an administrator who needs Pinax itself meanwhile: a password form at
`https://<pinax host>/auth/emergency`, for **emergency accounts** only, as Microsoft advises emergency access
accounts for Entra ID itself.

It is a way into Pinax with a password alone, past the organization's sign-in and its multi-factor step, so it is
kept small:
- **Off unless an emergency account exists.** Without one, `/auth/emergency` answers *Not Found*, and no one signs in
  with a password at all. A site whose policy forbids it simply makes none.
- **Only storage administrators marked as emergency accounts**, marked only on the server, never in the app, so an
  administrator's Entra ID account, if taken over, cannot make one:

  ```bash
  pinax user emergency emergency-1
  ```

  It prints a new password of 32 characters, once. The account must be an active storage administrator; making it
  one again replaces its password, and `pinax user emergency emergency-1 --off` ends it, with its password.
- **Short and locked like local sign-in:** an hour's session, and the same lockout after five failures in a row.
- **Loud:** every emergency sign-in and failure is in the audit log (`auth.emergency_signed_in`,
  `auth.emergency_sign_in_failed`), logged as a warning, and counted in `/metrics`; the alert rule
  `PinaxEmergencySignIn` (`deploy/prometheus/pinax-alerts.yml`) fires on any. *Access review* lists each emergency
  account under *Worth a look*, with when it was last used, and a person's page says they are one.
- Behind oauth2-proxy (header mode) there is no emergency sign-in: every request passes the proxy, and the way back
  is on the server.

Advice: make **two** accounts for the purpose (such as `emergency-1` and `emergency-2`), not anyone's own, as
storage administrators with no lab, so their use is plainly an emergency. Keep their passwords in the
organization's password vault, with who may open them. Sign in with each now and then to check it still works,
which the audit log records, and say so in the access review's note.

## Trying it before Entra ID is ready

[`scripts/dex/dex.sh`](../scripts/dex/dex.sh) starts Dex, an OpenID Connect provider, in Podman, standing in for
Entra ID, with four test accounts (each with the password `password`): a storage administrator, a PI, someone with
no Pinax account, and a guest whose name matches a staff member's.

```bash
scripts/dex/dex.sh up
```

It prints the settings for running the demo against it on <http://localhost:8001>; seed the demo first
(`uv run pinax seed`). `pnatarajan@example.org` signs in as Priya Natarajan, a storage administrator, and
`afujimoto@example.org` as Aiko Fujimoto, a PI who sees her lab's folders; the other two are refused, each with a
message saying why. To try the emergency sign-in too, make an account for it, with the same settings in the shell:
`uv run pinax user add emergency-1 --name "Emergency 1" --email it@example.org --admin`, then
`uv run pinax user emergency emergency-1`, and sign in at <http://localhost:8001/auth/emergency>. `dex.sh down`
removes Dex. The test suite also runs the whole sign-in against a stand-in provider, including forged and expired
tokens, replayed sign-ins, and guest accounts (`tests/test_oidc.py`).

Two differences from Entra ID: Dex has no single sign-out, so signing out of Pinax goes straight to its *signed
out* page; and the trial matches people by the `email` claim (`PINAX_OIDC_USERNAME_CLAIM=email`), because Dex's
test accounts have no user principal name.

## Troubleshooting

| What you see | Usually means | What to do |
|---|---|---|
| *The sign-in service said: AADSTS50011 … redirect URI* | The address Pinax sent does not match a registered redirect URI | Make `PINAX_PUBLIC_URL` match the address IT registered, exactly |
| *AADSTS50105 … not assigned* | Assignment is required, and the person is not in an assigned group | Ask IT to add them to one |
| *AADSTS7000215 … invalid client secret* | The secret is wrong or has expired | Replace the client secret (above) |
| *You can't use Pinax yet … no account for you yet* | Signed in, but not under *People and access* | Add the person; then they try again |
| *Your account is not one Pinax accepts here* | A guest, or another domain than `PINAX_OIDC_USERNAME_DOMAINS` | They sign in with their organization's account |
| *Pinax could not confirm the sign-in (CSRF Warning! State not equal in request and response.)* | The browser's address differs from `PINAX_PUBLIC_URL`, so the sign-in's cookie was not kept, or the sign-in took more than ten minutes | Correct `PINAX_PUBLIC_URL` and restart; or try again |
| *Pinax could not reach the sign-in service* | No outbound HTTPS to `login.microsoftonline.com` | Open the network path from Pinax's server |
| Pinax does not start: *… needs its settings: PINAX_… is not set* | A setting is missing or too short | Set it, as above |
| Nobody can sign in, because Entra ID is failing | The provider, or Pinax's settings for it | An emergency account signs in at `/auth/emergency`; or, on the server, fix the settings, or switch to local sign-in for a while ([When Entra ID fails](#when-entra-id-fails-the-emergency-sign-in)) |
| A role comes back, or goes, each time someone signs in | The role map names it, so Entra ID decides it | Change the person's app roles or groups in Entra ID, or take the role out of `PINAX_OIDC_ROLE_MAP` |
| Someone lost every mapped role, and the audit log says `auth.roles_unknown` | Mapped by groups, and they are in more groups than Entra ID lists | Map by app roles instead |
| `/auth/emergency` answers *Not Found* | No active storage administrator is an emergency account, or Pinax is not in `oidc` mode | `pinax user emergency USERNAME` on the server |
