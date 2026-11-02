# Troubleshooting

What a problem looks like, why it happens, and what to do, for the people who run Pinax. Start with the log
(`journalctl -u pinax-web -u pinax-ingest`, or `PINAX_LOG_FILE`): every failed request is a warning there with its
request ID, which is also the `X-Request-ID` of the response a person saw ([Logs](DEPLOYMENT.md#logs)).
`/api/health` and `pinax db status` answer the first questions in a second.

## Pinax does not start

| What you see | Why | What to do |
| --- | --- | --- |
| `PINAX_AUTH_MODE=demo lets anyone sign in as anyone, so it is refused when PINAX_ENVIRONMENT=prod` | The demo's sign-in on a production server | Set `PINAX_AUTH_MODE` to `local` (or leave it out: outside the demo, `local` is the default) or `header` ([Signing in](DEPLOYMENT.md#6-signing-in)) |
| `PINAX_METRICS_TOKEN must be at least 16 characters` | A metrics token too short to guard `/metrics` | `openssl rand -hex 32` makes one ([Metrics](DEPLOYMENT.md#metrics)) |
| `The catalog (...) is at revision ..., and this Pinax needs revision ...` | A release with a migration, with `PINAX_DB_AUTO_UPGRADE=false` | Back the catalog up, then `pinax db upgrade` (or apply `pinax db upgrade --sql`) ([Upgrading](DEPLOYMENT.md#upgrading)) |
| `... which this Pinax does not know: a newer Pinax has upgraded it` | An older release started on a catalog a newer one upgraded | Run the newer release, or restore the backup made before the upgrade |
| `The catalog's database needs a driver that is not installed (psycopg)` | `PINAX_DATABASE_URL` is PostgreSQL, without the driver | `uv sync --frozen --no-dev --extra postgres` ([PostgreSQL](DEPLOYMENT.md#postgresql)) |
| `connection refused`, `password authentication failed`, `no password supplied` | PostgreSQL unreachable, or no password for the role | Check the host and port, and the `pinax` user's `~/.pgpass` (mode 0600, or the driver ignores it) |
| `Pinax cannot write its log file ...` | `PINAX_LOG_FILE` or `PINAX_AUDIT_LOG` in a folder the `pinax` user cannot write | Use `/var/log/pinax/` (the units make it) or another writable folder |
| `address already in use` | Another program on the port | `pinax serve --port` another, and point the proxy at it |

## Signing in

| What you see | Why | What to do |
| --- | --- | --- |
| Nobody can sign in, because Entra ID is failing | The provider is down, or a setting is wrong or has expired (such as the client secret) | An emergency account signs in at `/auth/emergency` ([When Entra ID fails](ENTRA-ID.md#when-entra-id-fails-the-emergency-sign-in)); on the server, fix the settings, or switch to local sign-in for a while |
| The alert `PinaxEmergencySignIn` fires | Someone signed in, or tried to, with an emergency account | The audit log's `auth.emergency_*` events name the account and address; ask who holds its password |
| *You can't use Pinax yet*, or another message after signing in with Entra ID | Pinax refused the sign-in, or could not finish it, and says why | [Signing in with Entra ID](ENTRA-ID.md#troubleshooting) lists each message and what to do |
| *Unknown user*, after signing in through single sign-on | The username the proxy sends is not a person in Pinax, or differs from theirs | Add them, with the exact name the header carries (with Entra ID, usually the user principal name) ([People and access](ADMINISTRATION.md#people-and-access)) |
| *Sign in through the single sign-on proxy* | A request with the sign-on header that did not come from a trusted proxy | Make `PINAX_TRUSTED_PROXIES` the proxy's address; requests must not reach Pinax's port around the proxy |
| *This account has been deactivated* | They were deactivated | Reactivate them in *People and access* |
| Local sign-in fails for a correct password | Five failures in a row lock the account for 15 minutes; the answer is the same whatever went wrong | Wait, or give a new temporary password, which unlocks it: their page in *People and access*, or `pinax user password USERNAME` |
| Signed in, but asked to choose a password | They signed in with a temporary password | They choose their own; nothing else works until then |
| Signed in on HTTP, then signed out at once | Session cookies are Secure in production, and a browser sends them only over HTTPS | Serve Pinax over HTTPS, or set `PINAX_COOKIE_SECURE=false` for a trial |

## Someone cannot see what they expect

| What you see | Why | What to do |
| --- | --- | --- |
| *My data* is empty | They belong to no lab, or their labs own no folders | Add them to their lab (their page in *People and access*), and map the lab's folders (*Folders and labs*) |
| A lab's folder is missing | The folder is mapped to no lab, or to another, or the lab is inactive | *Folders and labs*, with *Unmapped only* for folders no one owns |
| A new folder is missing | It was created after the newest scan | It appears after the next scan is ingested |
| A report says an older scan | Someone chose an earlier scan beside the date; links keep the choice | *Back to the latest* in the header |
| A report says the totals are lower bounds | fs-inventory could not read a folder below (permissions), or the scan stalled | *Storage health* lists folders not read completely; give the scanning account read access to metadata |
| A division head's *My data* is empty | No active lab has their division as its *Division*, spelled the same | Set each lab's *Division* on its page in *People and access*; the site role follows it ([Site roles](ADMINISTRATION.md#site-roles)) |
| Finance sees no folders; an auditor or a division head has no buttons to change things | Their roles read, and finance sees charges, not folders | As designed. A lab's tags and cleanup plan are changed by its PI and delegates, and the site's settings by administrators |

## Scans and ingest

| What you see | Why | What to do |
| --- | --- | --- |
| A new scan does not appear | Automatic ingest is not running, or the scan is not finished | *Scans* shows automatic ingest's state; `systemctl status pinax-ingest`. A scan is ingested once its manifest says it finished |
| *Scans* says *skipped* | The scan's status was incomplete (stalled directories are missing) or it failed verification | Its message says why. Fix the cause and scan again; or, accepting lower bounds, `pinax ingest SCAN --allow-incomplete` |
| *Scans* says *failed* | The index could not be built: usually the disk filled, or the scan is damaged | Its message says why; `pinax ingest` the scan again once fixed |
| `scan ... is already snapshot N` | The scan was ingested before | Nothing to do; to build its index again, `pinax rebuild N` |
| `no filesystem is registered for ...` | The scan's root matches no filesystem | `pinax filesystem add` with the root the scan records, or `--fs SLUG` |
| Automatic ingest *not responding* | The watcher stopped or hangs | `journalctl -u pinax-ingest`; `systemctl restart pinax-ingest` |
| `/api/health` is `degraded`, `indexes_missing` | A ready snapshot's index folder is gone | `pinax rebuild SNAPSHOT_ID` rebuilds it from its scan |
| The disk under `PINAX_VAR_ROOT` fills | Indexes, and room for the one being built | Lower `PINAX_KEEP_SNAPSHOTS`; delete scans of pruned snapshots (Pinax never deletes scans) ([Sizing](DEPLOYMENT.md#sizing)) |

## Numbers

| What you see | Why | What to do |
| --- | --- | --- |
| A total looks wrong | | `pinax check SNAPSHOT_ID` compares every directory with `fs-inventory rollup` and lists any difference ([Checking a filesystem's numbers](DEPLOYMENT.md#checking-a-filesystems-numbers)) |
| Size and space on disk differ a lot | Compressed or sparse files use less space than their size; many small files use more | Both are right; reports show both where they differ |
| A folder is smaller than `du` says | Hard links are counted once, at their first link by path | The age map's panel says what a folder's extra links hold |
| Access times look wrong | Many filesystems do not record access times reliably (`noatime`, `relatime`) | Rely on *Last change*; show access only where the filesystem records it |
| History has no forecast | A forecast needs six scans over eight weeks, after any step (a project moved in or out) | It appears as scans accumulate |
| *Capacity* has no fill date | At its trend the filesystem does not fill within three years, or a larger capacity is scheduled before it would | The earliest date at the fast end of the trend's range is still shown |
| A past month's charge changed | A price, a free tier, or a folder's owner was changed back to before that month | Each change is in the audit log; statements already sent are not changed by Pinax ([Charges and statements](DEPLOYMENT.md#charges-and-statements)) |

## Pages and queries

| What you see | Why | What to do |
| --- | --- | --- |
| *Pinax stopped this after 180 seconds* | A search or comparison of a whole filesystem read for longer than `PINAX_QUERY_TIMEOUT` | Search within a folder or narrow it; an administrator can run it in the background (*Searches*); or raise the limit with the proxy's ([Configuration](CONFIGURATION.md#queries)) |
| *Pinax is already running as many searches of whole filesystems as it allows* | `PINAX_WIDE_QUERIES` turns were all in use | Try again shortly, or raise it if the server has the cores and memory |
| Pages are slow while a search runs | Searches of whole filesystems use every core, `PINAX_WIDE_QUERIES` (2) at a time | Fewer at once leave the rest more of the cores, and searches wait longer for a turn ([Many people at once](DEPLOYMENT.md#many-people-at-once)) |
| *Pinax is busy: more people are using it at once than it can serve* | No catalog connection was free within 10 seconds (`pinax_catalog_busy_total`, and `Turned away` in the log) | Usually a moment of heavy use: try again shortly. If it recurs, with PostgreSQL raise `PINAX_DATABASE_POOL_SIZE` within the database's `max_connections` ([Many people at once](DEPLOYMENT.md#many-people-at-once)) |
| Every page is slow at busy times, with only two or three cores busy | One web process does each page's Python work one thread at a time: about 250 to 300 people at once on a laptop | A server with faster cores; see [Many people at once](DEPLOYMENT.md#many-people-at-once), and `PINAX_WEB_THREADS` for when fewer threads serve more |
| Lines marked *(slow)* in the log | Requests over `PINAX_SLOW_REQUEST_SECONDS` | The line names the route and the time in index queries; see [Sizing](DEPLOYMENT.md#sizing) |
| *Something went wrong* on a page | An error on the server | The log has it, with the request's ID |

## API tokens and scripts

| What you see | Why | What to do |
| --- | --- | --- |
| 401, `Send a live API token as Authorization: Bearer <token>` | No token, a mistyped one, or one that expired, was revoked, or belongs to someone deactivated | *API tokens* (or the person's page) shows each token's state; make a new one if it ended. On `/script/api/` a token is the only way in |
| *403 The token '...' only reads* | A change made with a token that reads only | Make a token with *Your access* for a script that changes things ([API tokens for scripts](USER-GUIDE.md#api-tokens-for-scripts)) |
| *403 A search in the background is a person's* | A service token starting a search in the background | Start it with a person's own token |
| *403 Tokens are made and revoked, and passwords changed, in Pinax's pages* | A token used on the token or password routes | As designed: a leaked token cannot make another |
| Through oauth2-proxy, a script gets a sign-in page or a redirect | The proxy's configuration does not let `/script/api/` through, or the script calls `/api/` | `skip_auth_routes` in `deploy/oauth2-proxy/pinax.cfg` includes `^/script/api/`; scripts call `https://pinax.example.org/script/api/...` ([Single sign-on with oauth2-proxy](DEPLOYMENT.md#single-sign-on-with-oauth2-proxy)) |
| A token made with *Your access* no longer changes what it did | Its person's access changed: a token is never more than its person | As designed; it follows their access as it is now |

## Monitoring

| What you see | Why | What to do |
| --- | --- | --- |
| `/metrics` answers 404 | Metrics are off | Set `PINAX_METRICS_TOKEN` and restart `pinax-web` |
| `/metrics` answers 401 | The token is missing or different | Prometheus sends it as `Authorization: Bearer ...` from `credentials_file` |
| Through oauth2-proxy, Prometheus gets a sign-in page | The proxy's configuration does not let `/metrics` through | `skip_auth_routes` in `deploy/oauth2-proxy/pinax.cfg` includes `GET=^/metrics$` |
| No `process_*` metrics | They exist on Linux only | |
| The alert `PinaxBusy` fires | Requests were turned away as busy in the last 15 minutes | As for *Pinax is busy* under [Pages and queries](#pages-and-queries) |
