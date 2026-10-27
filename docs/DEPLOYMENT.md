# Deployment

Installing Pinax on a Linux server: the services, single sign-on, scans, upgrades, sizing, and operations.
The files this guide installs are in `deploy/`. This guide is checked against the tests and a local run; the
pilot installation (wave 4 in `pinax-spec/roadmap.md`) will confirm it on a real server.

## Shape of a deployment

```text
  browser ──► oauth2-proxy (sign-on, :443 or :4180) ──► pinax-web (127.0.0.1:8001)
                                                              │ reads
  pinax-scan@fast.timer ──► fs-inventory scan ──► /var/lib/pinax/scans/fast/<date>/
                                                              │ watched by
                                                       pinax-ingest ──► /var/lib/pinax/snapshots/
```

- **Scans** run where the filesystem is mounted with good metadata performance: the Pinax server itself (the
  `pinax-scan@` timer), or a storage node or NFS client that writes into the scan folder. fs-inventory reads
  only metadata, never file contents.
- **Ingest** (`pinax-ingest`) watches the scan folder and builds an index from each new scan. It needs the scan
  folder, not the filesystem.
- **The web app** (`pinax-web`) reads only `/var/lib/pinax`: the catalog and the indexes. It listens on this
  host only, and believes the username header only from oauth2-proxy.

## What you need

- A Linux server with systemd. 10 or more cores; 16 GB of memory for the index build (8 GB) and a few open
  indexes (4 GB each at most); fast local SSD for `/var/lib/pinax` (see [Sizing](#sizing)).
- [uv](https://docs.astral.sh/uv/), which installs the Python Pinax needs (3.13).
- Read access to the filesystems' metadata from the host that scans them.
- An OpenID Connect identity provider (for example Microsoft Entra ID) and
  [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) 7.

## Install

### 1. A user and folders

```bash
sudo useradd --system --home-dir /var/lib/pinax --shell /usr/sbin/nologin pinax
```

```bash
sudo install -d -o pinax -g pinax -m 0750 /var/lib/pinax /var/lib/pinax/scans
```

### 2. Pinax and fs-inventory

```bash
sudo git clone https://github.com/robert-mcdermott/pinax.git /opt/pinax
```

```bash
cd /opt/pinax && sudo git checkout v0.2.0 && sudo env UV_PYTHON_INSTALL_DIR=/opt/pinax/.python uv sync --frozen --no-dev
```

```bash
sudo /opt/pinax/scripts/install-fs-inventory.sh
```

`uv sync` makes `/opt/pinax/.venv`, with the locked dependencies only; `UV_PYTHON_INSTALL_DIR` keeps the Python it
installs (if the system has no 3.13) out of root's home, where the `pinax` user could not run it. The installer
puts the pinned fs-inventory release in `/opt/pinax/bin/`, after checking its SHA-256 and version.

### 3. Settings

```bash
sudo install -d -m 0755 /etc/pinax && sudo install -m 0640 -g pinax /opt/pinax/deploy/pinax.env /etc/pinax/pinax.env
```

`/etc/pinax/pinax.env` sets production mode, the data folders, and sign-on through the proxy; every setting is in
[CONFIGURATION.md](CONFIGURATION.md). To run `pinax` commands as the service does, define this in your shell:

```bash
pinax() { sudo -u pinax bash -c 'set -a; . /etc/pinax/pinax.env; exec /opt/pinax/.venv/bin/pinax "$@"' pinax "$@"; }
```

### 4. Filesystems, labs, and people

Register each filesystem by the root the scan records, and how people know it if that differs:

```bash
pinax filesystem add fast /fh/fast --name Fast --description "Primary research storage"
```

If the scanning host mounts it elsewhere (for example `/mnt/fast`), register that root and add
`--display-root /fh/fast`.

Then import labs, people, memberships, and folder ownership ([IMPORT.md](IMPORT.md)); give IT staff
`is_admin` `true` in `people.csv`. Look at the dry run, then apply it:

```bash
pinax import /srv/pinax/onboarding
```

```bash
pinax import /srv/pinax/onboarding --apply
```

Usernames must be what the sign-on proxy sends (step 6): with Entra ID and the default header, the user
principal name, such as `jdoe@example.org`. People can also be added and changed one at a time under *People and
access*, and folders mapped under *Folders and labs*. Someone must be added, and active, before they can sign in.

### 5. The services

```bash
sudo cp /opt/pinax/deploy/systemd/pinax-*.service /opt/pinax/deploy/systemd/pinax-scan@.timer /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now pinax-web pinax-ingest
```

```bash
sudo systemctl enable --now pinax-scan@fast.timer
```

| Unit | What it runs |
|---|---|
| `pinax-web.service` | `pinax serve` on 127.0.0.1:8001 |
| `pinax-ingest.service` | `pinax ingest --watch`: each new scan in `PINAX_SCAN_ROOT`, once ([Scans](#scans)) |
| `pinax-scan@.service`, `.timer` | `pinax scan <filesystem> --no-ingest`, weekly (Sunday 01:00); one timer per filesystem |

All run as `pinax`, with the filesystem read-only to them except `/var/lib/pinax` (change `ReadWritePaths` if
`PINAX_VAR_ROOT` or `PINAX_SCAN_ROOT` is elsewhere). The scan service has one
capability, `CAP_DAC_READ_SEARCH`, so that it can list every directory and read every file's metadata without
running as root. If scans run on another host instead, skip the timer and have that host write into
`/var/lib/pinax/scans` (or another folder `pinax-ingest` watches).

### 6. Sign-on with oauth2-proxy

1. Register an application with the identity provider, with the redirect URI
   `https://pinax.example.org/oauth2/callback`, and create a client secret.
2. Install oauth2-proxy 7 as `/usr/local/bin/oauth2-proxy`, and a system user for it.
3. Copy and edit its files: `deploy/oauth2-proxy/pinax.cfg` to `/etc/oauth2-proxy/pinax.cfg` (tenant, client
   ID, email domain, address), the client secret to `/etc/oauth2-proxy/client-secret`, and
   `deploy/oauth2-proxy/pinax.env` to `/etc/oauth2-proxy/pinax.env` with a cookie secret. Make the secrets
   readable only by the oauth2-proxy user.
4. Start it: `deploy/oauth2-proxy/oauth2-proxy.service`, if your package has no unit of its own.

oauth2-proxy signs people in, removes any user headers a browser sends, and passes the signed-in user to Pinax
as `X-Forwarded-User`, `X-Forwarded-Email`, and `X-Forwarded-Preferred-Username`. `PINAX_AUTH_HEADER` says
which one is the Pinax username. Pinax believes it only from `PINAX_TRUSTED_PROXIES` (this host, by default);
the same header from anywhere else gets *401*. If oauth2-proxy runs on another host, put its address in
`PINAX_TRUSTED_PROXIES` and make Pinax listen where only that host can reach it. `/api/health` is open without
sign-in, for monitoring.

### 7. Check the installation

```bash
curl -s http://127.0.0.1:8001/api/health
```

It answers `{"status": "ok", ...}`. Sign in through oauth2-proxy as an administrator and open *Scans*:
*Automatic ingest* should say *Watching*. Run the first scan rather than waiting for Sunday:

```bash
sudo systemctl start pinax-scan@fast
```

When it has been ingested (`journalctl -u pinax-ingest -f`, or *Scans*), check its numbers against
fs-inventory's own (the ID is on *Scans*, or from `pinax snapshots`):

```bash
pinax check 1
```

## Scans

Weekly scans suit most research filesystems; nightly ones suit smaller filesystems or ones you watch closely.
A scan of a billion files takes hours, so each scan gets its own folder, and only scans that verify are loaded.

`pinax-ingest` looks for scan folders (with a `manifest.json`) up to three levels below `PINAX_SCAN_ROOT`, oldest
first, and treats each by the status fs-inventory gave it:

| Status | What the watcher does |
|---|---|
| `complete` | runs `fs-inventory verify`, then ingests it (skip the verify with `--no-verify`) |
| `running`, `interrupted` | leaves it alone and looks again next time, since it may still finish or be resumed |
| `incomplete`, `failed` | records it on *Scans* as skipped, with why (`--allow-incomplete` ingests incomplete ones) |

A scan is matched to its filesystem by its root, and is handled once: ingested, skipped, or failed with the error,
all of which show on *Scans*. A failed or skipped scan is never retried automatically; fix the cause and run
`pinax ingest SCAN_DIR` by hand. Scans whose root matches no registered filesystem are named in the watcher's log
and left for later. Any number of watchers and manual ingests may run at once: they share a lock in
`PINAX_VAR_ROOT`, so one index is built at a time and no scan is ingested twice.

*Scans* also shows whether the watcher is alive: it reports what it is doing at every pass, and is shown as not
responding when it misses three. To run it from a timer instead of as a service, use `--once` with the timer's
interval as `--interval`; it exits with code 1 when a scan in that pass failed.

A scan host other than the Pinax server can scan and verify, and write into the watched folder:

```bash
dir=/scans/fast/$(date -u +%Y%m%dT%H%M%SZ)
```

```bash
/opt/fs-inventory/fs-inventory scan /fh/fast -o "$dir" --resolve-ids --threads 64 --stall-timeout 15m && /opt/fs-inventory/fs-inventory verify "$dir"
```

Notes:

- `--resolve-ids` records owner names, which the Owners report and storage health use to flag deleted accounts.
  Run it on a host that resolves the same accounts as the storage (LDAP or sssd).
- `fs-inventory probe /fh/fast --threads 16,32,64,128` finds a good thread count without writing anything;
  `PINAX_SCAN_THREADS` sets it for `pinax scan`.
- An interrupted scan can be continued with `fs-inventory resume`; the watcher waits for it.
- Keep scan folders as long as you may want to rebuild their indexes ([Upgrading](#upgrading)). Pinax reads only
  the parts a scan's manifest lists, so leftovers of an interrupted run in the folder are ignored.
- fs-inventory does not enter other filesystems mounted below the root unless asked. If labs' folders are their
  own filesystems (filesets, exports, automounts), scan with `--cross-mounts` (add it to `PINAX_SCAN_EXTRA_ARGS`);
  otherwise each is counted as a folder not read, with nothing in it, and Pinax says its totals are lower bounds.
- Scans in microseconds (the default) and in nanoseconds (`--time-unit ns`) are both accepted.

## Checking a filesystem's numbers

After the first scan of a filesystem, and after upgrading Pinax, check the index against fs-inventory's own
computation of the same totals with `pinax check SNAPSHOT_ID`. It runs `fs-inventory rollup` on a copy of the
scan's manifest (the scan folder is not written to) and compares every directory, printing the ones that differ.
Rollup keeps an age histogram of about 0.5 KB for every directory down to the depth compared, so on scans with
tens of millions of directories bound it, for example `--age-depth 4`; the other totals are still compared at
every depth. Each index also records how every directory changed since the filesystem's previous scan, and the
check compares that too, with a rollup of the previous scan, as long as its scan folder is kept.

## Upgrading

```bash
cd /opt/pinax && sudo git fetch --tags && sudo git checkout vX.Y.Z && sudo env UV_PYTHON_INSTALL_DIR=/opt/pinax/.python uv sync --frozen --no-dev
```

```bash
sudo systemctl restart pinax-web pinax-ingest
```

```bash
pinax rebuild --outdated
```

`CHANGELOG.md` says when a release changes the index format (`INDEX_VERSION`). Older indexes still answer, but
without what the new format adds, so `pinax rebuild --outdated` builds them again from their scans, oldest first
(each one is compared with the snapshot before it),
in place: each snapshot stays available on its old index until the new one replaces it, and keeps it if the build
fails. The web app opens rebuilt indexes without a restart. `pinax rebuild SNAPSHOT_ID` rebuilds one, for example
one whose index was lost.

The catalog has no migrations yet (they come with PostgreSQL, in wave 5): new tables are created at start-up,
and a release that changes existing ones says so; pilot catalogs are then re-created from the import files.

## Sizing

| Item | Rule of thumb |
|---|---|
| fs-inventory scan output | about 40 bytes per entry (40 GB per billion) |
| Snapshot index | about 56 bytes per file (56 GB per billion), times `PINAX_KEEP_SNAPSHOTS` (6) |
| Index build | about 1.2 seconds per million files on 10 cores (100 million in two minutes, a billion in about 20 minutes, measured on synthetic data); 8 GB of memory is enough, spilling beside the index beyond it, and more is no faster |
| Query memory | `PINAX_QUERY_MEMORY_LIMIT` per open index (4 GB at most), up to 8 open |
| Catalog | tiny: one row per top-level folder per scan for history |

Put `/var/lib/pinax/snapshots` on fast local SSD or NVMe: indexes are read with many small range requests.

## Catalog database

SQLite in WAL mode, in `/var/lib/pinax`, suits a single server. PostgreSQL (`PINAX_DATABASE_URL`, with the
`psycopg` driver added) works for a fresh catalog; it and migrations are planned for wave 5.

## Backups

- **Catalog**: back up `/var/lib/pinax/pinax.db` (people, labs, folder ownership, snapshot records, folder
  history), and keep the import files. It is small.
- **Indexes**: optional; `pinax rebuild` makes them again from the scans.
- **Scans**: keep the ones you may rebuild from, or want to compare later.

## Monitoring and operations

`/api/health` needs no sign-in and names nothing: it answers `ok`; `degraded` when a ready snapshot's index is
missing or automatic ingest has stopped or stopped responding; and 503 when the catalog cannot be read. Logs go
to the journal (`journalctl -u pinax-web -u pinax-ingest`).

| Task | How |
|---|---|
| See scans and their status | *Scans*, or `pinax snapshots` |
| A failed or skipped scan | *Scans* shows the error; fix the cause and run `pinax ingest` on the scan |
| Automatic ingest *not responding* | `systemctl status pinax-ingest` and its journal |
| Too little disk for indexes | Lower `PINAX_KEEP_SNAPSHOTS`; pruning runs after every ingest |
| Someone cannot see their lab's data | Check *People and access* (membership) and *Folders and labs* (the folder is mapped to their lab) |
| Someone gets *401* after signing in | Their username (the header named by `PINAX_AUTH_HEADER`) is not an active person in the catalog |
| A folder shows "could not be read completely" | The scanner lacked permission somewhere below it; see *Storage health* |
| Move to a new fs-inventory release | `scripts/install-fs-inventory.sh --version vX.Y.Z`; Pinax reads schema versions 2 and 3 |
