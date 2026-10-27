# Deployment

How to run Pinax against real research storage: where scans run, how they reach Pinax, the web server,
single sign-on, sizing, and day-to-day operations.

## Shape of a deployment

```text
  scan host(s) with the filesystem mounted          Pinax server
  ─────────────────────────────────────────        ──────────────────────────────────────
  fs-inventory scan  ->  /scans/<fs>/<date>/   ->   pinax ingest  ->  var/snapshots/
  fs-inventory verify    (shared or copied)         pinax serve   (behind the SSO proxy)
```

- **Scans** run where the filesystem is mounted with good metadata performance: a storage node, an NFS
  client close to the storage, or the Pinax server itself (`pinax scan`). fs-inventory is a single static
  binary; install the pinned version with `scripts/install-fs-inventory.sh`.
- **Ingest** runs on the Pinax server (or any host that can write `var/snapshots`). It needs the scan folder,
  not the filesystem.
- **The web app** needs only `var/` (the catalog and indexes). It never mounts research storage.

## Registering filesystems

```bash
uv run pinax filesystem add fast /fh/fast --name Fast --description "Primary research storage"
```

The root must be the path the scan records. If the scan host mounts the filesystem somewhere else (for
example `/mnt/fast`), register that root and add `--display-root /fh/fast` so paths show the way people know them.

Then map each top-level folder to its lab or department under *Folders and labs*. Unmapped folders are
visible only to administrators, and the overview counts them.

## Scheduling scans

Weekly scans suit most research filesystems; nightly scans suit smaller ones or ones you watch closely.
A scan of a billion files typically takes hours, so give each one its own output folder and ingest only
scans that verify. Example cron job on a scan host that shares `/scans` with the Pinax server:

```bash
#!/bin/bash
set -euo pipefail
dir=/scans/fast/$(date -u +%Y%m%dT%H%M%SZ)
/opt/fs-inventory/fs-inventory scan /fh/fast -o "$dir" --resolve-ids --threads 64 --stall-timeout 15m
/opt/fs-inventory/fs-inventory verify "$dir"
ssh pinax "cd /opt/pinax && uv run pinax ingest $dir --fs fast"
```

Or on the Pinax server, in one step:

```bash
uv run pinax scan fast -- --stall-timeout 15m
```

Notes:

- `--resolve-ids` records owner names, which the Owners report and storage health use to flag deleted accounts.
  Run it on a host that resolves the same accounts as the storage (LDAP or sssd).
- `fs-inventory probe /fh/fast --threads 16,32,64,128` finds a good thread count without writing anything.
- An interrupted scan can be continued with `fs-inventory resume`. Pinax refuses scans that are not complete;
  `pinax ingest --allow-incomplete` also accepts `incomplete` scans (stalled directories are missing).
- Keep scan folders as long as you want to be able to rebuild indexes. Pinax does not need them after ingest.
  It reads only the parts a scan's manifest lists, so leftovers of an interrupted run in the folder are ignored.
- fs-inventory does not enter other filesystems mounted below the root unless asked. If labs' folders are their
  own filesystems (filesets, exports, automounts), scan with `--cross-mounts`; otherwise each is counted as a
  folder not read, with nothing in it, and Pinax says its totals are lower bounds.
- Scans in microseconds (the default) and in nanoseconds (`--time-unit ns`) are both accepted; Pinax keeps
  microseconds.

## Checking a filesystem's numbers

After the first scan of a filesystem, and after upgrading Pinax, check the index against fs-inventory's own
computation of the same totals:

```bash
uv run pinax check 12
```

It runs `fs-inventory rollup` on a copy of the scan's manifest (the scan folder is not written to) and compares
every directory, printing the ones that differ. Rollup keeps an age histogram of about 0.5 KB for every
directory down to the depth compared, so on scans with tens of millions of directories bound it, for example
`--age-depth 4`; the other totals are still compared at every depth.

## Running the web app

```bash
PINAX_ENVIRONMENT=prod PINAX_AUTH_MODE=header uv run pinax serve --host 127.0.0.1 --port 8001
```

Run it as a service (systemd) behind a reverse proxy that does single sign-on (for example oauth2-proxy
with Entra ID) and sets `X-Remote-User`. Pinax must not be reachable except through the proxy in header mode.
Every person needs an active account in the catalog; their lab memberships decide what they see.

Uvicorn workers: each worker opens snapshot indexes read-only, so several workers can share `var/`. Give each
enough memory for `PINAX_QUERY_MEMORY_LIMIT` times the number of indexes it keeps open (8 by default).

## Sizing

| Item | Rule of thumb |
|---|---|
| fs-inventory scan output | about 25 bytes per entry (25 GB per billion) |
| Snapshot index | about 55 bytes per file (55 GB per billion), times `PINAX_KEEP_SNAPSHOTS` |
| Index build | about 2 seconds per million files on 10 cores; memory bounded by `PINAX_BUILD_MEMORY_LIMIT`, spilling beside the index |
| Query memory | `PINAX_QUERY_MEMORY_LIMIT` per open index (4 GB default) |
| Catalog | tiny: one row per top-level folder per scan for history |

Put `var/snapshots` on fast local SSD or NVMe: the index is read with many small range requests.

## Catalog database

SQLite in WAL mode suits a single server. For production, point `PINAX_DATABASE_URL` at PostgreSQL and add
the driver (`uv add "psycopg[binary]"`). The schema has no migrations yet (Alembic is planned), so create it
fresh: Pinax creates missing tables at start-up.

## Backups

- **Catalog**: back up the database (people, labs, folder ownership, snapshot records, folder history). It is small.
- **Indexes**: optional. They can be rebuilt from scan folders with `pinax ingest`.
- **Scans**: keep the ones you may want to rebuild from, or to compare later.

## Operations

| Task | How |
|---|---|
| See scans and their status | *Scans* in the interface, or `uv run pinax snapshots` |
| A failed ingest | The snapshot shows *Failed* with the error; fix the cause and run `pinax ingest` on the same scan again |
| Too little disk for indexes | Lower `PINAX_KEEP_SNAPSHOTS`; pruning runs after every ingest |
| Someone cannot see their lab's data | Check *People and access* (membership) and *Folders and labs* (the folder is mapped to their lab) |
| A folder shows "could not be read completely" | The scanner lacked permission somewhere below it; see *Storage health*, and scan as a user that can read everything |
| Move to a new fs-inventory release | Install it with `scripts/install-fs-inventory.sh --version vX.Y.Z`; Pinax reads schema versions 2 and 3 |
