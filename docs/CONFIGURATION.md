# Configuration

Settings are `PINAX_*` environment variables, or a `.env` file in the folder you start Pinax from.
[.env.example](../.env.example) lists them all with their defaults. Defaults suit the local demo.

## Runtime

| Setting | Default | What it does |
|---|---|---|
| `PINAX_APP_NAME` | `Pinax` | Name shown by the API |
| `PINAX_ENVIRONMENT` | `demo` | `demo`, `dev`, or `prod`. `pinax seed` deletes the catalog and indexes, so it runs only in `demo`; use `dev` for real data on a workstation ([SETUP.md](SETUP.md)) and `prod` on a server |

## Locations

| Setting | Default | What it does |
|---|---|---|
| `PINAX_VAR_ROOT` | `var/` | The catalog database (`pinax.db`) and snapshot indexes (`snapshots/`) |
| `PINAX_SCAN_ROOT` | `scans/` | Where `pinax scan` writes fs-inventory output, as `<filesystem>/<UTC time>/`, and where the watcher looks. Scans made elsewhere can live anywhere. Pinax never deletes scans ([what to keep](ARCHITECTURE.md#what-pinax-keeps)) |
| `PINAX_DEMO_ROOT` | `demo/` | Demo filesystems and scans written by `pinax seed` |
| `PINAX_DATABASE_URL` | SQLite in `VAR_ROOT` | SQLAlchemy URL of the catalog. Production uses the SQLite default until PostgreSQL and migrations arrive in wave 7; a PostgreSQL URL (`postgresql+psycopg://pinax@db/pinax`, with the `psycopg` driver added) works for a fresh catalog, untested ([The catalog database](ARCHITECTURE.md#the-catalog-database)) |

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
| `PINAX_QUERY_MEMORY_LIMIT` | `4GB` | DuckDB memory limit for each open snapshot index |
| `PINAX_QUERY_THREADS` | `0` | DuckDB threads per open index; 0 means one per CPU. On a busy server, fewer than the CPUs keeps one long search from slowing every other page |
| `PINAX_QUERY_TIMEOUT` | `180` | Seconds a request may run before Pinax stops its queries and says why; 0 for no limit. A request is also stopped when the browser leaves the page. Keep it below the proxy's timeout (oauth2-proxy's `upstream_timeout`, 5 minutes in `deploy/oauth2-proxy/pinax.cfg`) |
| `PINAX_WIDE_QUERIES` | `2` | How many file-level queries of a whole filesystem (a search at its root, the new and removed files of *What changed* against an older scan, problem file names) run at once; others wait their turn within their time limit. 0 for no limit |
| `PINAX_SEARCH_MAX_ROWS` | `500` | Most files a search shows on screen |
| `PINAX_EXPORT_MAX_ROWS` | `100000` | Most files a CSV download contains |
| `PINAX_COLD_DAYS` | `730` | When data counts as cold, in days since its last change. Rounded to the age bucket that starts nearest (two years by default) |
| `PINAX_CURRENCY` | `USD` | The currency prices are in (an ISO 4217 code such as `EUR`), shown in each viewer's own number format. Prices and free tiers themselves are set on *Costs*, not here |

## Identity

| Setting | Default | What it does |
|---|---|---|
| `PINAX_AUTH_MODE` | `demo` | How people sign in. `demo`: a persona switcher, anyone can be anyone, for showing Pinax with the seeded people (refused when `PINAX_ENVIRONMENT=prod`). `local`: usernames and passwords Pinax keeps, for sites without single sign-on. `header`: trust the username in `AUTH_HEADER`, set by a single sign-on proxy such as oauth2-proxy in front of Entra ID |
| `PINAX_AUTH_HEADER` | `X-Remote-User` | The header the proxy sets the username in; behind oauth2-proxy, `X-Forwarded-Preferred-Username`, `X-Forwarded-Email`, or `X-Forwarded-User` |
| `PINAX_TRUSTED_PROXIES` | `["127.0.0.1", "::1"]` | In header mode, the addresses or networks (a JSON list, such as `'["10.1.2.3", "10.8.0.0/16"]'`) the header is believed from; from anywhere else a request gets 401 |
| `PINAX_SESSION_HOURS` | `12` | Local sign-in: how long a session lasts from signing in |
| `PINAX_LOGIN_MAX_FAILURES` | `5` | Local sign-in: failed sign-ins in a row that lock an account |
| `PINAX_LOGIN_LOCKOUT_MINUTES` | `15` | Local sign-in: how long a locked account stays locked (a new temporary password unlocks it at once) |
| `PINAX_PASSWORD_MIN_LENGTH` | `12` | Local sign-in: the shortest password allowed (it also may not contain the username) |
| `PINAX_COOKIE_SECURE` | unset | Local sign-in: mark the session cookie Secure, so it is sent only over HTTPS. Unset means yes when `PINAX_ENVIRONMENT=prod`, no otherwise |

## Dimensions

The age buckets and file categories are defined in `src/pinax/categories.py`, not in settings, because the
interface's color ramp has exactly seven validated steps (one per age bucket). Each snapshot records the
buckets and categories it was built with.
