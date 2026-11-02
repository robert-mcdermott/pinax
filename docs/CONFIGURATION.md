# Configuration

Settings are `PINAX_*` environment variables, or a `.env` file in the folder you start Pinax from.
[.env.example](../.env.example) lists them all with their defaults. Defaults suit the local demo.

## Runtime

| Setting | Default | What it does |
|---|---|---|
| `PINAX_APP_NAME` | `Pinax` | Name shown by the API |
| `PINAX_ENVIRONMENT` | `demo` | `demo`, `dev`, or `prod`. `pinax seed` deletes the catalog and indexes, so it runs only in `demo`; use `dev` for real data on a workstation ([SETUP.md](SETUP.md)) and `prod` on a server |
| `PINAX_WEB_THREADS` | `40` | Threads that answer the web app's requests (each request's sign-in check, then its page). Python runs one thread's code at a time, so when the server is saturated with quick pages, fewer (8) answer more of them a second; but each request waiting for a turn at a whole-filesystem search, or running a long report, holds one, so fewer leave less for everyone else ([Sizing](DEPLOYMENT.md#sizing)) |

## Locations

| Setting | Default | What it does |
|---|---|---|
| `PINAX_VAR_ROOT` | `var/` | The catalog database (`pinax.db`) and snapshot indexes (`snapshots/`) |
| `PINAX_SCAN_ROOT` | `scans/` | Where `pinax scan` writes fs-inventory output, as `<filesystem>/<UTC time>/`, and where the watcher looks. Scans made elsewhere can live anywhere. Pinax never deletes scans ([what to keep](ARCHITECTURE.md#what-pinax-keeps)) |
| `PINAX_DEMO_ROOT` | `demo/` | Demo filesystems and scans written by `pinax seed` |
| `PINAX_DATABASE_URL` | SQLite in `VAR_ROOT` | The catalog's database. Empty for SQLite in `VAR_ROOT`, or a PostgreSQL URL such as `postgresql://pinax@db.example.org/pinax?sslmode=verify-full`, with Pinax installed with its `postgres` extra ([PostgreSQL](DEPLOYMENT.md#postgresql)). Leave the password out: PostgreSQL's driver reads it from `~/.pgpass` or `PGPASSWORD` |
| `PINAX_DB_AUTO_UPGRADE` | `true` | Upgrade the catalog when Pinax starts, if a release needs it. `false` leaves that to `pinax db upgrade` (which can print its SQL), and Pinax refuses to start with a catalog that is behind ([The catalog database](ARCHITECTURE.md#the-catalog-database)) |
| `PINAX_DATABASE_POOL_SIZE` | `10` | PostgreSQL connections each Pinax process keeps open, with as many more at busy times. A request waits up to 10 seconds for one, then is turned away as busy. With SQLite, the web app keeps one for each of its threads (`PINAX_WEB_THREADS`) |

## Scanning

| Setting | Default | What it does |
|---|---|---|
| `PINAX_FS_INVENTORY` | `bin/fs-inventory`, then the `PATH` | The fs-inventory binary |
| `PINAX_SCAN_THREADS` | `32` | `--threads` for `pinax scan`. Use `fs-inventory probe` to choose one for your storage |
| `PINAX_SCAN_EXTRA_ARGS` | `[]` | More fs-inventory options for every `pinax scan`, as a JSON list, for example `["--stall-timeout","10m","--max-memory","4G"]` |

`pinax scan` always passes `--resolve-ids` (owner names) and verifies the scan before ingesting it.

## Building indexes

| Setting | Default | What it does |
|---|---|---|
| `PINAX_BUILD_MEMORY_LIMIT` | `8GB` | DuckDB memory limit while building an index; it spills to a temporary folder beside the index beyond this. 8 GB builds 100 million files in two minutes; 17 GB was no faster |
| `PINAX_BUILD_THREADS` | `0` | DuckDB threads while building; 0 means one per CPU. Steps that read the scan use fewer when memory requires: each thread decodes whole row groups of about a million entries, so the build uses as many as fit in three quarters of `PINAX_BUILD_MEMORY_LIMIT` (and halves them if a step still runs out) |
| `PINAX_KEEP_SNAPSHOTS` | `6` | Snapshots per filesystem that keep their full index. After each ingest, older ones are pruned: their indexes are deleted and their folder history kept (pruned snapshots cannot be rebuilt). Their scans are not deleted |

## Queries

| Setting | Default | What it does |
|---|---|---|
| `PINAX_QUERY_MEMORY_LIMIT` | `8GB` | DuckDB's memory for the open snapshot indexes together: what their queries use at once, and the blocks they keep cached. Each open index has an equal share, set again as indexes open and close (one alone has all of it, eight an eighth). A size such as `8GB` or `16GiB` |
| `PINAX_QUERY_THREADS` | `0` | DuckDB threads per open index; 0 means one per CPU. A search and the pages that read the same index share them: in the load test, 5 of 10 slowed whole-filesystem searches by about a quarter and gave other pages little back ([Sizing](DEPLOYMENT.md#many-people-at-once)) |
| `PINAX_QUERY_TIMEOUT` | `180` | Seconds a request may run before Pinax stops its queries and says why; 0 for no limit. A request is also stopped when the browser leaves the page. Keep it below the proxy's timeout (oauth2-proxy's `upstream_timeout`, 5 minutes in `deploy/oauth2-proxy/pinax.cfg`) |
| `PINAX_WIDE_QUERIES` | `2` | How many file-level queries of a whole filesystem (a search at its root, the new and removed files of *What changed* against an older scan, problem file names) run at once; others wait their turn within their time limit. 0 for no limit |
| `PINAX_SEARCH_MAX_ROWS` | `500` | Most files a search shows on screen |
| `PINAX_EXPORT_MAX_ROWS` | `100000` | Most files a CSV download contains |
| `PINAX_COLD_DAYS` | `730` | When data counts as cold, in days since its last change. Rounded to the age bucket that starts nearest (two years by default) |
| `PINAX_CURRENCY` | `USD` | The currency prices are in (an ISO 4217 code such as `EUR`), shown in each viewer's own number format. Prices and free tiers themselves are set on *Costs*, not here |
| `PINAX_JOB_TIMEOUT` | `3600` | How long, in seconds, a search in the background may run (see *Searches*); at least 60 |
| `PINAX_JOB_DAYS` | `7` | How many days the results of a search in the background are kept |
| `PINAX_FILL_WARNING_MONTHS` | `12` | A filesystem that fills within this many months at its trend is flagged on the overview and on *Capacity*. Capacities themselves are set on *Capacity*, or come from what `pinax scan` records |

## Identity

| Setting | Default | What it does |
|---|---|---|
| `PINAX_AUTH_MODE` | `local`; `demo` in the demo environment | How people sign in. `local`: usernames and passwords Pinax keeps, with nothing else to set up. `demo`: a persona switcher, anyone can be anyone, for showing Pinax with the seeded people (refused when `PINAX_ENVIRONMENT=prod`). `header`: trust the username in `AUTH_HEADER`, set by a single sign-on proxy such as oauth2-proxy in front of Entra ID ([Single sign-on with oauth2-proxy](DEPLOYMENT.md#single-sign-on-with-oauth2-proxy)). `oidc`: Pinax signs people in with Microsoft Entra ID, or another OpenID Connect provider, itself ([Signing in with Entra ID](ENTRA-ID.md)) |
| `PINAX_AUTH_HEADER` | `X-Remote-User` | The header the proxy sets the username in; behind oauth2-proxy, `X-Forwarded-Preferred-Username`, `X-Forwarded-Email`, or `X-Forwarded-User` |
| `PINAX_TRUSTED_PROXIES` | `["127.0.0.1", "::1"]` | The proxies in front of Pinax, as addresses or networks (a JSON list, such as `'["10.1.2.3", "10.8.0.0/16"]'`). In header mode, the header is believed only from them; from anywhere else a request gets 401. In every mode, a request from one of them is logged and audited with the person's own address from `X-Forwarded-For`, and keeps the proxy's `X-Request-ID` |
| `PINAX_OIDC_ISSUER` | unset | `oidc`: the identity provider's issuer; for Entra ID, `https://login.microsoftonline.com/<tenant ID>/v2.0` |
| `PINAX_OIDC_CLIENT_ID` | unset | `oidc`: Pinax's application (client) ID with the provider |
| `PINAX_OIDC_CLIENT_SECRET` | unset | `oidc`: its client secret; it expires, so note when ([Secrets](ENTRA-ID.md#setting-up-pinax)) |
| `PINAX_OIDC_USERNAME_CLAIM` | `preferred_username` | `oidc`: the ID token's claim that names the person (Entra ID's user principal name); `email` for test providers |
| `PINAX_OIDC_USERNAME_DOMAINS` | unset | `oidc`: allowed domains, comma-separated. With `example.org`, `jdoe@example.org` signs in as a person named `jdoe@example.org` or `jdoe`, and other domains are refused. Unset, the claim must be a username exactly |
| `PINAX_PUBLIC_URL` | unset | `oidc`: how people reach Pinax, such as `https://pinax.example.org` (`https://`, or `http://` for localhost only); the provider returns people to it |
| `PINAX_OIDC_ROLE_MAP` | unset | `oidc`, optional: Pinax's roles from Entra ID, as JSON, such as `{"admin": ["Pinax.Admin"], "division:Basic Sciences": ["Pinax.Head.Basic"]}`. Each role it names (`admin`, `auditor`, `finance`, `division:<Division>`) is set at every sign-in and not in the app ([Roles from Entra ID](ENTRA-ID.md#roles-from-entra-id)) |
| `PINAX_OIDC_ROLES_CLAIM` | `roles` | `oidc`: the ID token's claim the role map reads: `roles` (Entra ID's app roles) or `groups` (group object IDs) |
| `PINAX_SESSION_SECRET` | unset | `oidc`: signs the sign-in's own state, for the minutes a sign-in takes; at least 32 characters (`openssl rand -hex 32`) |
| `PINAX_SESSION_HOURS` | `12` | Local sign-in and `oidc`: how long a session lasts from signing in |
| `PINAX_LOGIN_MAX_FAILURES` | `5` | Local sign-in: failed sign-ins in a row that lock an account |
| `PINAX_LOGIN_LOCKOUT_MINUTES` | `15` | Local sign-in: how long a locked account stays locked (a new temporary password unlocks it at once) |
| `PINAX_PASSWORD_MIN_LENGTH` | `12` | Local sign-in: the shortest password allowed (it also may not contain the username) |
| `PINAX_COOKIE_SECURE` | unset | Local sign-in and `oidc`: mark the session cookie Secure, so it is sent only over HTTPS. Unset means yes when `PINAX_ENVIRONMENT=prod`, no otherwise |
| `PINAX_TOKEN_MAX_DAYS` | `365` | The longest an API token may last, in days; people choose from a week up to this ([API tokens](ADMINISTRATION.md#api-tokens)) |

## Logs

| Setting | Default | What it does |
|---|---|---|
| `PINAX_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `PINAX_LOG_FORMAT` | `text` | `text`, for the journal, or `json`: one JSON object per line, for a log collector, with the request's ID, person, and address, and each request's and audit event's details as fields of their own ([Logs](DEPLOYMENT.md#logs)) |
| `PINAX_LOG_FILE` | unset | A file to write the log to instead of standard error (the journal, under systemd). Pinax opens it again when `logrotate` moves it (`deploy/logrotate/pinax`) |
| `PINAX_LOG_REQUESTS` | `true` | One line for each request to the API (method, path without its query string, status, time taken, time in index queries, person, address, request ID). `false` keeps only slow and failed requests |
| `PINAX_SLOW_REQUEST_SECONDS` | `5` | A request that takes this long or longer is logged as a warning, marked *slow*; 0 never |
| `PINAX_AUDIT_LOG` | unset | A file to write every audit event to as well, as JSON lines, once its change is committed: for a site's security monitoring. Audit events are always in the catalog and in the log too |

## Metrics

| Setting | Default | What it does |
|---|---|---|
| `PINAX_METRICS_TOKEN` | unset | Turns on `/metrics`, Prometheus's format, for requests that send it as `Authorization: Bearer <token>`; without it, `/metrics` answers 404. At least 16 characters (`openssl rand -hex 32` makes one); a shorter one stops Pinax from starting ([Metrics](DEPLOYMENT.md#metrics)) |

## Dimensions

The age buckets are defined in `src/pinax/categories.py`, not in settings, because the interface's color ramp has
exactly seven validated steps (one per age bucket). The file categories are the catalog's, with the twelve built-in
ones to start ([File types and categories](ARCHITECTURE.md#file-types-and-categories)). Each snapshot records the
buckets and categories it was built with.
