# Configuration

Settings are `PINAX_*` environment variables, or a `.env` file in the folder you start Pinax from.
[.env.example](../.env.example) lists them all with their defaults. Defaults suit the local demo.

## Runtime

| Setting | Default | What it does |
|---|---|---|
| `PINAX_APP_NAME` | `Pinax` | Name shown by the API |
| `PINAX_ENVIRONMENT` | `demo` | `demo`, `dev`, or `prod`. `pinax seed` refuses to run in `prod` |

## Locations

| Setting | Default | What it does |
|---|---|---|
| `PINAX_VAR_ROOT` | `var/` | The catalog database (`pinax.db`) and snapshot indexes (`snapshots/`) |
| `PINAX_SCAN_ROOT` | `scans/` | Where `pinax scan` writes fs-inventory output, as `<filesystem>/<UTC time>/`. Scans made elsewhere can live anywhere |
| `PINAX_DEMO_ROOT` | `demo/` | Demo filesystems and scans written by `pinax seed` |
| `PINAX_DATABASE_URL` | SQLite in `VAR_ROOT` | SQLAlchemy URL of the catalog, for example `postgresql+psycopg://pinax@db/pinax` |

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
| `PINAX_KEEP_SNAPSHOTS` | `6` | Snapshots per filesystem that keep their full index. Older ones are pruned to folder history |

## Queries

| Setting | Default | What it does |
|---|---|---|
| `PINAX_QUERY_MEMORY_LIMIT` | `4GB` | DuckDB memory limit for each open snapshot index |
| `PINAX_QUERY_THREADS` | `0` | DuckDB threads per open index; 0 means one per CPU |
| `PINAX_SEARCH_MAX_ROWS` | `500` | Most files a search shows on screen |
| `PINAX_EXPORT_MAX_ROWS` | `100000` | Most files a CSV download contains |
| `PINAX_COLD_DAYS` | `730` | When data counts as cold, in days since its last change. Rounded to the age bucket that starts nearest (two years by default) |

## Identity

| Setting | Default | What it does |
|---|---|---|
| `PINAX_AUTH_MODE` | `demo` | `demo`: a persona switcher sets a cookie. `header`: trust the username in `AUTH_HEADER`, set by a single sign-on proxy |
| `PINAX_AUTH_HEADER` | `X-Remote-User` | The header the proxy sets. Only safe when nothing can reach Pinax except through the proxy |

## Dimensions

The age buckets and file categories are defined in `src/pinax/categories.py`, not in settings, because the
interface's color ramp has exactly seven validated steps (one per age bucket). Each snapshot records the
buckets and categories it was built with.
