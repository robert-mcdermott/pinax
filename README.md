<p align="center"><img src="src/pinax/static/img/pinax-logo.svg" alt="Pinax: Explore your data" width="320"></p>

# Pinax

Pinax is a file system reporting system for research storage. It reads inventories written by
[fs-inventory](https://github.com/robert-mcdermott/fs-inventory) and shows what is stored, how much, how
old, what kind, and who owns it, for multi-petabyte file systems with billions of files.

A *pinax* was a tablet or register; the *Pinakes* was the catalog of the Library of Alexandria.

- **Age map** (`/app/#/explore`): a treemap of any folder's subfolders, sized by how much they hold and
  colored by how old the data is. A large red tile is a large folder of old data; click it to go one level
  down and find where the old data is. Size by bytes, space on disk, file count, data of one kind
  (for example sequencing data), or data older than a threshold. Color by average age, time since the last
  change, age of the oldest file, time since the last access, the share that is cold, or the share of one kind
  of data. Show one or two levels, or switch to a table.
- **Reports for the same folder**: age profile by folder, file types and categories (with a callout for
  uncompressed sequencing data), cold folders that could be archived as a unit, owners (flagging accounts
  that no longer exist), and file search by name or glob, type, size, age, and owner, with CSV export.
- **For PIs and lab members**: *My data* shows every folder their labs own, across filesystems, with size,
  age profile, cold share, and change since the previous scan. People see only their own labs' folders.
- **For IT**: an overview of every filesystem, the largest folders and biggest changes, unmapped folders,
  storage health (scan errors, very large directories, small-file hotspots, problem file names, deleted
  owners), the scan history, and people and access.
- **Built for scale**: each scan is indexed once into directory-level totals, so a treemap of a petabyte
  draws in tens of milliseconds: at 100 million files, every directory-level query takes 50 ms or less, and the
  index builds in two minutes. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and measured
  performance.
- **Numbers you can check**: `pinax check` compares every directory's totals with `fs-inventory rollup`, an
  independent implementation, and reports any difference.
- **Ready to run**: new scans are ingested automatically, labs and people are imported from CSV with a dry run
  first, and `deploy/` has systemd units and an oauth2-proxy configuration for single sign-on.
- **Light and dark themes**, keyboard-reachable tiles, and a table view for every chart.

## Documentation

| Document | Covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design, the snapshot index, how it is built and queried, security, measured performance and sizing |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting, its default, and what it does |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Installing on a server: systemd services, single sign-on, scheduled scans, upgrades, sizing, operations |
| [docs/IMPORT.md](docs/IMPORT.md) | The CSV format for importing labs, people, memberships, and folder ownership |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Code layout, conventions, the HTTP API, the interface, themes and chart colors, tests, benchmarks |
| [pinax-spec/roadmap.md](pinax-spec/roadmap.md) | The plan: waves (one release each) and their slices, what is done, what is next, decisions |
| [pinax-spec/requirements.md](pinax-spec/requirements.md) | What Pinax must do, with stable IDs and each requirement's status |
| [CHANGELOG.md](CHANGELOG.md) | What each release contains |

## Quick start

Requires [uv](https://docs.astral.sh/uv/). Install the pinned fs-inventory release (1.0.0) into `bin/`;
the script verifies its SHA-256 checksum:

```bash
scripts/install-fs-inventory.sh
```

```bash
uv sync
```

```bash
uv run pinax seed
```

```bash
uv run pinax serve
```

Then open <http://127.0.0.1:8001>. `seed` writes two demo filesystems of about 34,000 sparse files
(117 TB of logical size on almost no disk) with realistic ages going back fifteen years, scans each one twice
with fs-inventory (with some simulated activity in between), and ingests the scans. It takes about
ten seconds. It resets `var/` and `demo/` each time, and refuses to run when `PINAX_ENVIRONMENT=prod`.

## Demo script (about 12 minutes)

1. **Home page.** Choose *Continue as Hanna Lindqvist* (a PI). *My data* shows one folder: 20 TiB, most of
   it unchanged for years.
2. **Age map.** Open it. `wgs/2019_tumor_normal` is a large dark red tile: 17 TiB of alignments last changed
   about seven years ago. Hover for details; switch *Color by* to *Time since last access* and
   *Size by* to *Size of sequencing and genomics*.
3. **File types.** The callout shows terabytes of uncompressed SAM and FASTQ files; click `.sam` to list them.
4. **Switch identity** (top right) to *Aiko Fujimoto*. Drill into `projects`, then `2022_tumor_atlas`, then
   `raw`: an active project hides `2014_legacy_reruns`, the darkest red tile, eleven years old.
   **Cold data** at two years lists `raw` as one cold unit; at ten years it lists `2014_legacy_reruns` alone.
5. **Switch to Jordan Okafor**, a delegate for two labs. *My data* shows both labs' folders; the age map
   puts both side by side.
6. **Switch to Priya Natarajan** (storage administrator). *All data* shows both filesystems, the largest
   folders, the biggest changes since the previous scan (new sequencing runs in `gsr_illumina`), and
   three unmapped folders. Open the age map of *Fast*: `scratch_legacy` is a large, very old, unmapped tile.
7. **Folders and labs.** Tick *Unmapped only*, then map `shared_reference` to the Genomics Shared Resource.
   Switch to *Casey Morgan*: the folder now appears in their data.
8. **Storage health.** An unreadable folder, a 12,000-file directory of small PNG tiles, problem file names
   (a newline, a leading dash, a Windows-reserved character, a name that is not NFC), and files owned by
   accounts that no longer exist.
9. **Find files.** Search `*.bam`, older than five years, largest first; download the CSV.
10. **Scans.** The snapshots of both filesystems, and whether automatic ingest is watching for new scans.
11. **Import from CSV.** In a terminal, `uv run pinax import examples/import` lists the 14 changes the example
    files would make: two departments, a lab, three people, their memberships, and owners for the three unmapped
    folders. Add `--apply`, and *Folders and labs* shows no unmapped folders; every change is in the audit log.

## Using real filesystems

Register a filesystem by its scan root, then scan and ingest it in one step:

```bash
uv run pinax filesystem add fast /fh/fast --name Fast
```

```bash
uv run pinax scan fast
```

Or ingest a scan made elsewhere, for example by a scheduled job on a storage node:

```bash
uv run pinax ingest /scans/fast/2026-10-26
```

Or let Pinax pick up each new scan written under a folder, checking every five minutes:

```bash
uv run pinax ingest --watch /scans
```

Check that every directory's totals equal an independent computation by `fs-inventory rollup` (the ID is from
`pinax snapshots`):

```bash
uv run pinax check 1
```

Every top-level folder of a filesystem is a unit of ownership: map each one to a lab or department under
*Folders and labs*, and its members see it. To set up many labs and people at once, import them from CSV
([docs/IMPORT.md](docs/IMPORT.md)):

```bash
uv run pinax import examples/import
```

To install Pinax on a server, follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): systemd services (in `deploy/`),
single sign-on with oauth2-proxy, scheduled scans, upgrades, sizing, and operations.

## Commands

| Command | Purpose |
|---|---|
| `uv run pinax serve [--host H] [--port N]` | The web app (default port 8001) |
| `uv run pinax seed [--scale 1.0]` | Reset runtime data and build the demo |
| `uv run pinax filesystem add SLUG ROOT [--name N] [--display-root P]` | Register a filesystem |
| `uv run pinax filesystem list` | List filesystems and their latest scan |
| `uv run pinax scan SLUG [--no-ingest] [-- fs-inventory options]` | Scan with fs-inventory, verify, and ingest |
| `uv run pinax ingest SCAN_DIR [--fs SLUG] [--allow-incomplete]` | Build a snapshot index from a finished scan |
| `uv run pinax ingest --watch [FOLDER] [--interval N] [--once] [--no-verify]` | Ingest each new verified scan under a folder (default `PINAX_SCAN_ROOT`) once |
| `uv run pinax snapshots` | List snapshots |
| `uv run pinax rebuild SNAPSHOT_ID... \| --outdated` | Build ready snapshots' indexes again from their scans, after an upgrade or a lost index |
| `uv run pinax import FOLDER [--apply]` | Import labs, people, memberships, and folder ownership from CSV; a dry run unless `--apply` ([docs/IMPORT.md](docs/IMPORT.md)) |
| `uv run pinax check SNAPSHOT_ID \| SCAN_DIR [--index DIR] [--age-depth N] [--json]` | Check an index, and its changes since the previous scan, against `fs-inventory rollup`, directory by directory |
| `uv run pytest` | Tests: index correctness, the rollup cross-check, queries, authorization, and an end-to-end scan |

API documentation is served at `/api/docs`.

## Layout

```text
src/pinax/
  config.py, db.py, models.py      settings, catalog engine, catalog schema
  categories.py                    file categories, type normalization, age buckets
  ingest/build.py                  scan -> snapshot index (DuckDB + Parquet)
  crosscheck.py                    an index against fs-inventory rollup, directory by directory
  index.py                         read-only queries over one snapshot index
  snapshots.py                     scanning, ingest, retention, the cache of open indexes
  watch.py                         automatic ingest of new scans under a folder
  onboard.py                       labs, people, memberships, and folder ownership from CSV
  auth.py, explore.py              identity and folder access; explore and report services
  serialize.py, api/               JSON views; FastAPI routers (identity, explore, admin, access)
  seed.py, cli.py, main.py         demo data, command line, app
  static/                          HTML, css/pinax.css (themes, chart colors), js/ (core, treemap, views), img/
bench/                             synthetic scans of any size and query benchmarks
scripts/install-fs-inventory.sh    installs the pinned fs-inventory release into bin/
tests/                             pytest suite
docs/                              architecture, configuration, deployment, development
pinax-spec/                        requirements and roadmap
bin/  var/  scans/  demo/          fs-inventory binary and runtime data (gitignored)
```

## License

Copyright 2026 Robert McDermott.

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).
