<p align="center"><img src="src/pinax/static/img/pinax-logo.svg" alt="Pinax: Explore your data" width="320"></p>

# Pinax

Pinax is a file system reporting system for research storage. It reads inventories written by
[fs-inventory](https://github.com/robert-mcdermott/fs-inventory) and shows what is stored, how much, how
old, what kind, and who owns it, for multi-petabyte file systems with billions of files.

A *pinax* was a tablet or register; the *Pinakes* was the catalog of the Library of Alexandria.

- **Age map** (`/app/#/explore`): a treemap of any folder's subfolders, sized by how much they hold and
  colored by how old the data is: red for recent data, blue for cold. A large dark blue tile is a large folder of
  data untouched for years; click it to go one level
  down and find where the old data is. Size by bytes, space on disk, file count, data of one kind
  (for example sequencing data), or data older than a threshold. Color by average age, time since the last
  change, age of the oldest file, time since the last access, the share that is cold, or the share of one kind
  of data. Show one or two levels, or switch to a table.
- **History**: how any folder, lab, or filesystem grew and aged over its scans: size by age as stacked
  columns, with files, cold share, and average age beside it, or as a table with CSV.
- **Change map**: the treemap colored by how much each folder grew (teal) or shrank (gold) since an earlier
  scan, the previous one by default, with new and removed folders drawn; drill down as in the age map.
- **What changed**: for any folder and two scans, where it grew and shrank, the new and removed folders, and the
  largest new and removed files, each list with CSV.
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

Then open <http://127.0.0.1:8001>. `seed` writes two demo filesystems of about 35,000 sparse files
(123 TB of logical size on under 10 MB of disk) with realistic ages going back fifteen years, scans each one six
times with fs-inventory, with six months of simulated activity in between, and ingests the scans. It takes about
fifteen seconds. It resets `var/` and `demo/` each time, and refuses to run when `PINAX_ENVIRONMENT=prod`.

### The demo's six months

The six scans of each filesystem are dated a month apart, the last one on the day you seed. Every month the
Genomics Shared Resource delivers two Illumina runs and a nanopore run, the Fujimoto Lab adds a batch of samples
(it grows from 21 to 27 TB), and a few other labs add results. Besides that:

| Scan | Since the scan before |
| --- | --- |
| 1 | The starting point, five months ago |
| 2 | The Fujimoto Lab's Cell Ranger results arrive; the Chen and Haddad labs delete temporary files and checkpoints |
| 3 | A light-sheet project moves from the Bennett Lab to the Haddad Lab (`haddad_n/datasets/2024_lightsheet_bennett`) |
| 4 | The Quintero Lab joins, mostly with data from the PI's previous institution: new here, but years old |
| 5 | The Lindqvist Lab compresses its uncompressed FASTQ files (`wgs/2024_germline/fastq_uncompressed` becomes `fastq`) |
| 6 | The Fujimoto Lab writes its atlas |

Meanwhile nobody touches the Kowalski Lab's `raw/2024_tmt_plex`, which ages past two years: the lab's cold share
climbs from 64% to 100% at the same size. *History* shows all of it, the *Change map* where each change is, and
*What changed* lists them.

## Demo script (about 12 minutes)

1. **Home page.** Choose *Continue as Hanna Lindqvist* (a PI). *My data* shows one folder: 19 TiB, most of
   it unchanged for years.
2. **Age map.** Open it. `wgs/2019_tumor_normal` is a large blue tile: 17 TiB of alignments last changed
   about seven years ago. Hover for details; switch *Color by* to *Time since last access* and
   *Size by* to *Size of sequencing and genomics*.
3. **File types.** The callout shows terabytes of uncompressed SAM files; click `.sam` to list them. (The lab
   compressed its uncompressed FASTQ files before the fifth scan.)
4. **Switch identity** (top right) to *Aiko Fujimoto*. Drill into `projects`, then `2022_tumor_atlas`, then
   `raw`: an active project hides `2014_legacy_reruns`, the darkest blue tile, eleven years old. Next to it,
   `2025_immune_aging` is red: data written this year.
   **Cold data** at two years lists `raw` as one cold unit; at ten years it lists `2014_legacy_reruns` alone.
   **History** for `fujimoto_a`: the lab grew from 19 to 25 TiB in six scans, and new data on top of the columns
   brought its cold share down from 77% to 60%. Hover a column for that scan; the three small charts follow.
5. **Switch to Jordan Okafor**, a delegate for two labs. *My data* shows both labs' folders; the age map
   puts both side by side. Then **switch to Valeria Quintero**, whose lab joined three scans ago: one folder, two
   thirds of it unchanged for more than two years, because most of it came from the PI's previous institution.
   Its *History* starts at the fourth scan.
6. **Switch to Priya Natarajan** (storage administrator). *All data* shows both filesystems, the largest
   folders, the biggest changes since the previous scan (a batch of samples and an atlas in `fujimoto_a`,
   sequencing runs in `gsr_illumina`), and
   three unmapped folders. Open the age map of *Fast*: `scratch_legacy` is a large, very old, unmapped tile.
   Open *History* for `kowalski_d`: the same size every scan, while its cold share climbs to 100%. Then
   `lindqvist_h/wgs/2024_germline`: a terabyte smaller at the fifth scan, when the lab compressed its FASTQ files.
   Open the *Change map* of *Fast* and compare with the scan of May 30: `fujimoto_a` and `haddad_n` grew,
   `bennett_k` shrank by 81%. In `bennett_k`, the removed `imaging/2024_lightsheet` keeps the room it had; in
   `haddad_n`, `datasets/2024_lightsheet_bennett` is new, and its tooltip says it is over a year old: it moved.
   *What changed* for *Fast* since May 30 lists the Fujimoto project and the sequencing runs where it grew, the
   moved project and the compressed FASTQ among new and removed folders, and the largest new files (Cell Ranger
   BAMs); each list downloads as CSV.
7. **Folders and labs.** Tick *Unmapped only*, then map `shared_reference` to the Genomics Shared Resource.
   Switch to *Casey Morgan*: the folder now appears in their data.
8. **Storage health.** An unreadable folder, a 12,000-file directory of small PNG tiles, problem file names
   (a newline, a leading dash, a Windows-reserved character, a name that is not NFC), and files owned by
   accounts that no longer exist.
9. **Find files.** Search `*.bam`, older than five years, largest first; download the CSV.
10. **Scans.** Six snapshots of each filesystem, a month apart, each with its full index (`PINAX_KEEP_SNAPSHOTS`
    keeps six), and whether automatic ingest is watching for new scans.
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
