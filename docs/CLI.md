# Command line

Every `pinax` command, what it does, and its options. Run them as the user Pinax runs as, with its settings. On a
server installed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, its [step 3](DEPLOYMENT.md#3-settings) defines a
shell function that does both:

```bash
pinax() { sudo -u pinax bash -c 'set -a; . /etc/pinax/pinax.env; exec /opt/pinax/.venv/bin/pinax "$@"' pinax "$@"; }
```

From a checkout, `uv run pinax ...` reads `.env` in the current folder. `pinax COMMAND --help` lists a
command's options. Every command logs to standard error as the settings say ([Logs](DEPLOYMENT.md#logs)), and an
error is one line beginning `error:` with a non-zero exit.

| Command | What it does |
| --- | --- |
| [`pinax serve`](#pinax-serve) | Run the web app |
| [`pinax filesystem`](#pinax-filesystem) | Register and list filesystems |
| [`pinax scan`](#pinax-scan) | Scan a filesystem with fs-inventory, then ingest the scan |
| [`pinax ingest`](#pinax-ingest) | Build a snapshot index from a scan, or watch a folder for new scans |
| [`pinax snapshots`](#pinax-snapshots) | List snapshots |
| [`pinax rebuild`](#pinax-rebuild) | Build snapshots' indexes again, after an upgrade or a lost index |
| [`pinax check`](#pinax-check) | Check an index against `fs-inventory rollup`, directory by directory |
| [`pinax user`](#pinax-user) | Add people, give temporary passwords, list people |
| [`pinax import`](#pinax-import) | Import labs, people, memberships, and folder ownership from CSV |
| [`pinax tags`](#pinax-tags) | Tag folders in bulk from CSV |
| [`pinax db`](#pinax-db) | The catalog's database: status, upgrades, copying it |
| [`pinax seed`](#pinax-seed) | Build the demo |

## pinax serve

Runs the web app on one host and port. It brings the catalog up to date first (unless `PINAX_DB_AUTO_UPGRADE` is
false), and refuses to start with settings that are unsafe, such as the demo's sign-in in production.

| Option | Default | |
| --- | --- | --- |
| `--host HOST` | `127.0.0.1` | The address to listen on. Behind a proxy on the same host, keep `127.0.0.1` |
| `--port PORT` | `8001` | The port |

```bash
pinax serve --host 127.0.0.1 --port 8001
```

The systemd unit `deploy/systemd/pinax-web.service` runs it. Pinax works out each person's address itself from
`PINAX_TRUSTED_PROXIES`, so uvicorn's own proxy headers are off.

## pinax filesystem

### pinax filesystem add

Registers a filesystem by the root its scans record.

| Argument or option | |
| --- | --- |
| `SLUG` | A short ID used in addresses and metrics, such as `fast` |
| `ROOT` | The absolute path of the scan root, as the scanning host sees it, such as `/fh/fast` |
| `--name NAME` | The name people know it by (default: the slug) |
| `--display-root PATH` | The root as people know it, if the scanning host mounts it elsewhere (scans of `/mnt/fast` shown as `/fh/fast`) |
| `--description TEXT` | A line about it, shown to administrators |

```bash
pinax filesystem add fast /fh/fast --name Fast
```

### pinax filesystem list

Lists the filesystems and each one's latest snapshot.

## pinax scan

Scans a registered filesystem with fs-inventory into `PINAX_SCAN_ROOT/<filesystem>/<UTC time>/`, verifies the scan,
records what the storage reports about its capacity, and ingests it. Owner names are resolved (`--resolve-ids`),
with `PINAX_SCAN_THREADS` threads and any `PINAX_SCAN_EXTRA_ARGS`.

| Argument or option | |
| --- | --- |
| `FILESYSTEM` | The filesystem's slug |
| `--no-ingest` | Only scan; ingest later with `pinax ingest` (or let automatic ingest find it) |
| `-- OPTIONS` | Further fs-inventory scan options for this scan, after `--` |

```bash
pinax scan fast -- --stall-timeout 10m
```

The timer `deploy/systemd/pinax-scan@.timer` runs `pinax scan --no-ingest` on a schedule, and automatic ingest picks
the scan up ([Scans](DEPLOYMENT.md#scans)).

## pinax ingest

Builds a snapshot index from a finished fs-inventory scan, adds the snapshot to the catalog, records each top-level
folder's totals and what changed since the previous scan, and deletes indexes beyond `PINAX_KEEP_SNAPSHOTS`. One
ingest runs at a time; others wait for the lock in `PINAX_VAR_ROOT`.

| Argument or option | |
| --- | --- |
| `SCAN_DIR` | The scan's folder. With `--watch`, the folder to watch (default: `PINAX_SCAN_ROOT`) |
| `--fs SLUG` | The filesystem (default: the one whose root matches the scan's) |
| `--allow-incomplete` | Accept a scan whose status is incomplete (stalled directories are missing, and its totals are lower bounds) |
| `--watch` | Ingest each new scan under the folder once, checking every `--interval` seconds, with a heartbeat that *Scans* shows |
| `--interval SECONDS` | With `--watch`: seconds between checks (default 300) |
| `--once` | With `--watch`: check once and exit, 1 if any scan failed (for cron or a timer) |
| `--no-verify` | With `--watch`: skip `fs-inventory verify` before ingesting |

```bash
pinax ingest /var/lib/pinax/scans/fast/20261028T020000Z
```

```bash
pinax ingest --watch /var/lib/pinax/scans
```

A scan already ingested is refused (rebuild it with `pinax rebuild`); automatic ingest records a scan it cannot
load as *skipped* or *failed*, with why, and does not try it again.

## pinax snapshots

Lists every snapshot: its ID, filesystem, when the scan started, its status (`ready`, `pruned`, `failed`,
`skipped`, `building`), files, bytes, and scan folder.

## pinax rebuild

Builds ready snapshots' indexes again from their scans, in place: each snapshot keeps its old index until the new
one replaces it, and keeps it if the build fails.

| Argument or option | |
| --- | --- |
| `SNAPSHOT_ID ...` | The snapshots to rebuild |
| `--outdated` | Every ready snapshot whose index is missing or in an older format, oldest first (after an upgrade that changes the index format) |

```bash
pinax rebuild --outdated
```

## pinax check

Compares every directory's totals in an index with `fs-inventory rollup`, an independent computation, and with a
previous scan, what changed. Exits 0 when everything agrees, 1 with a report of each difference.

| Argument or option | |
| --- | --- |
| `TARGET` | A snapshot ID, or an fs-inventory scan folder |
| `--index PATH` | With a scan folder: the index built from it (default: build one, check it, and delete it) |
| `--allow-incomplete` | With a scan folder: accept a scan with status incomplete |
| `--examples N` | Directories to list for each difference (default 10) |
| `--age-depth N` | Compare age histograms down to this depth (default: all; rollup needs about 0.5 KB of memory per directory down to it) |
| `--previous-scan PATH` | With a scan folder and `--index`: the previous scan, to check the index's changes (a snapshot ID finds it in the catalog) |
| `--json` | Print the report as JSON |

```bash
pinax check 11
```

## pinax user

### pinax user add

Adds a person. With local sign-in it prints a temporary password, which they change when they first sign in; with
single sign-on, the username is the one the identity provider or the proxy sends ([Signing in with Entra
ID](ENTRA-ID.md)).

| Argument or option | |
| --- | --- |
| `USERNAME` | Their sign-in name |
| `--name NAME` | Full name (required) |
| `--email EMAIL` | Email (required) |
| `--title TITLE` | Job title |
| `--admin` | Make them a storage administrator |

```bash
pinax user add jdoe@example.org --name "Jane Doe" --email jdoe@example.org --admin
```

This is how the first administrator is made, before anyone can sign in; everything after can be done in *People and
access*.

### pinax user password

Local sign-in: prints a new temporary password for `USERNAME`, unlocks their account, and ends their sessions.

### pinax user list

Lists people, whether they are administrators, and whether they are active and can sign in.

## pinax import

Imports labs and departments, people, memberships, and folder ownership from CSV files
([IMPORT.md](IMPORT.md)). A dry run that lists every change unless `--apply`; with `--apply`, all of the changes are
made, or none if any row is refused.

| Argument or option | |
| --- | --- |
| `FOLDER` | A folder holding any of `labs.csv`, `people.csv`, `memberships.csv`, and `folders.csv` |
| `--labs FILE`, `--people FILE`, `--memberships FILE`, `--folders FILE` | A file instead of the one in the folder |
| `--apply` | Make the changes |

```bash
pinax import examples/import
```

## pinax tags

### pinax tags import

Tags folders from a CSV of filesystem, path, tag, and value (a lab's own tag as `lab:key`). A dry run unless
`--apply`; with it, every row or none.

```bash
pinax tags import tags.csv --apply
```

## pinax db

### pinax db status

Names the catalog's database (never its password), its migration revision, and the newest this Pinax has. Exits 0
when it is up to date and 1 otherwise: empty, made before migrations, behind, or newer than this Pinax.

### pinax db upgrade

Brings the catalog up to date: creates a new one, adopts one made before migrations (0.9.0 and earlier), or applies
the revisions it lacks. Back it up first. `--sql` prints the SQL instead, for a database administrator to review and
apply.

### pinax db copy

Copies every table of the catalog into another, empty database, such as SQLite into PostgreSQL, in one transaction,
then sets PostgreSQL's id sequences past the copied rows and compares every table's count. Stop Pinax first.

| Argument or option | |
| --- | --- |
| `TARGET` | The database to copy into, such as `postgresql://pinax@db.example.org/pinax` (its password in `~/.pgpass`) |
| `--from SOURCE` | The catalog to copy (default: this one, `PINAX_DATABASE_URL`) |

```bash
pinax db copy postgresql://pinax@db.example.org/pinax?sslmode=verify-full
```

### pinax db revision

For developers: writes the next migration from what changed in `models.py`, against an up-to-date catalog
([DEVELOPMENT.md](DEVELOPMENT.md)).

## pinax seed

Deletes the catalog and indexes in `PINAX_VAR_ROOT` and writes the demo: two filesystems of sparse files, 14 labs
and departments, 23 people, six scans a month apart, prices, capacities, a cleanup plan, tags, and saved views
([DEMO.md](DEMO.md)). It runs only when `PINAX_ENVIRONMENT=demo`.

| Option | |
| --- | --- |
| `--scale N` | Multiply the number of demo files (1.0 is about 35,000) |
