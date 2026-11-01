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
  of data. Show one or two levels, or switch to a table. Or see a folder several levels deep at once, as an
  icicle or a sunburst, to find data deep in a chain of folders.
- **History**: how any folder, lab, or filesystem grew and aged over its scans: size by age as stacked
  columns, with files, cold share, and average age beside it, or as a table with CSV. A forecast carries the
  trend of the last six months of scans a year ahead, with a range and a check of how far off it was three
  months ago.
- **Change map**: the treemap colored by how much each folder grew (teal) or shrank (gold) since an earlier
  scan, the previous one by default, with new and removed folders drawn; drill down as in the age map.
- **What changed**: for any folder and two scans, where it grew and shrank, the new and removed folders, and the
  largest new and removed files, each list with CSV.
- **Any kept scan**: every report shows the latest scan or any earlier one kept in full, chosen beside the scan
  date; links keep the choice. Older scans keep their folder totals, shown in *History*.
- **Reports for the same folder**: age profile by folder, file types and categories (with a callout for
  uncompressed sequencing data), cold folders that could be archived as a unit, owners (flagging accounts
  that no longer exist), and file search by name or glob, type, size, age, and owner, with CSV export.
- **For PIs and lab members**: *My data* shows every folder their labs own, across filesystems, with size,
  age profile, cold share, and change since the previous scan. People see only their own labs' folders.
  *Storage charges* shows what each of their labs pays each month, by filesystem and folder, how each charge is
  worked out, and the months before; this month's is an estimate, and a figure on *My data*. The next twelve
  months are estimated at the trend of each lab's folders, with the prices and free tiers as scheduled.
- **Cleanup plans**: from a folder's panel on the age map, a lab marks folders to delete, move to cheaper storage,
  or compress, and sees the space it would free and how much its monthly charge would fall. Pinax suggests folders
  (workflow working folders, cold data, uncompressed sequencing data), and later scans show what was done, following
  a folder that was renamed rather than removed. Pinax itself deletes, moves, and compresses nothing.
- **Tags**: folders described in terms a lab and IT share (project, grant, data stage, source, classification, IRB
  protocol, a public copy, keep until, legal hold), from a list administrators keep, plus labs' own; a tag applies to
  everything below its folder. *Collections* finds data by tag across filesystems, with its size, cost, history, and
  forecast; *Storage charges* splits a lab's charges by grant; protected folders stay out of cleanup plans.
- **Saved views and searches in the background**: any report can be saved under a name and shared with a lab; a
  search of a whole filesystem can run in the background, past a page's time limit, its results kept for a week.
  Hard-linked files are counted once.
- **For IT**: an overview of every filesystem, the largest folders and biggest changes, unmapped folders,
  storage health (scan errors, very large directories, small-file hotspots, problem file names, deleted
  owners), the scan history, each filesystem's capacity and the month it fills at its trend, and people and
  access: add people, change their details, lab memberships, and administrator role, and deactivate and
  reactivate them; add labs and departments, change their details, and deactivate and reactivate them; every
  change audited; costs: each filesystem's price
  per month and the free tiers for labs and departments, from a day on; and chargeback: every lab's and
  department's charges for a month, totals by filesystem, the storage no one owns, and a CSV statement for finance.
- **Roles beyond labs**: auditors see everything and change nothing; finance sees every lab's charges and the
  prices behind them, and no folders; division heads see their divisions' labs as the labs' members do. Given on a
  person's page, beside their labs, with every way of signing in; checked on the server for every request.
- **Sign-in three ways**: the demo's persona switcher, to show Pinax with the seeded people (no setup; refused in
  production); usernames and passwords Pinax keeps, for sites without single sign-on; or single sign-on, such as
  Entra ID behind oauth2-proxy. People, labs, and access are managed the same way in all three.
- **Built for scale**: each scan is indexed once into directory-level totals, so a treemap of a petabyte
  draws in tens of milliseconds: at 100 million files, every directory-level query takes 50 ms or less, and the
  index builds in two minutes. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and measured
  performance.
- **Numbers you can check**: `pinax check` compares every directory's totals with `fs-inventory rollup`, an
  independent implementation, and reports any difference.
- **Ready to run**: new scans are ingested automatically, labs and people are imported from CSV with a dry run
  first, and `deploy/` has systemd units and an oauth2-proxy configuration for single sign-on. The catalog is
  SQLite, with nothing to install, or PostgreSQL for production; migrations upgrade either in place, and
  `pinax db copy` moves a catalog from one to the other.
- **Logs and metrics**: a log line for each request, with who made it and from where, and every audit event, as
  text for the journal or JSON lines for a log collector; Prometheus metrics for each filesystem's scans, size,
  capacity, and fill date, and for Pinax itself, with alert rules to start from.
- **Light and dark themes**, keyboard-reachable tiles, and a table view for every chart.

## Documentation

The guides are in [docs/](docs/README.md), and make a website with screenshots of every page in both themes
(`node docs/site/build.mjs` builds it into `site/`; [docs/site/README.md](docs/site/README.md)).

| Document | Covers |
|---|---|
| [docs/CONCEPTS.md](docs/CONCEPTS.md) | How Pinax sees storage: scans, snapshots, folder ownership, sizes, ages, change, costs, plans, tags |
| [docs/USER-GUIDE.md](docs/USER-GUIDE.md) | Every page a lab sees, for PIs, lab members, and administrators exploring |
| [docs/DEMO.md](docs/DEMO.md) | Running the demo, who is who, its six months, prices, and capacities, and a 20-minute walk-through |
| [docs/SETUP.md](docs/SETUP.md) | Running Pinax on your own data: settings, the first administrator, scans, labs and people, prices |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Installing on a server: systemd services, single sign-on, scheduled scans, the catalog on SQLite or PostgreSQL, backups, upgrades, sizing, charges and statements, logs, metrics, operations |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting, its default, and what it does |
| [docs/ADMINISTRATION.md](docs/ADMINISTRATION.md) | The administrator's pages: folders and labs, people and access, scans, storage health, capacity, costs, chargeback, tags, the audit log |
| [docs/IMPORT.md](docs/IMPORT.md) | The CSV format for importing labs, people, memberships, and folder ownership |
| [docs/CLI.md](docs/CLI.md) | Every `pinax` command and option |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | What a problem looks like, why, and what to do |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design; what Pinax keeps (catalog, indexes, scans) and what to back up; the snapshot index, how it is built and queried, security, measured performance and sizing |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Code layout, conventions, the HTTP API, the interface, themes and chart colors, tests, benchmarks |
| [pinax-spec/roadmap.md](pinax-spec/roadmap.md) | The plan: waves (one release each) and their slices, what is done, what is next, decisions |
| [pinax-spec/requirements.md](pinax-spec/requirements.md) | What Pinax must do, with stable IDs and each requirement's status |
| [CHANGELOG.md](CHANGELOG.md) | What each release contains |

## Try the demo

Requires [uv](https://docs.astral.sh/uv/). Install the pinned fs-inventory release (1.0.0) into `bin/`; the script
verifies its SHA-256 checksum:

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

Then open <http://127.0.0.1:8001> and choose who to be.

The demo needs no setup. `pinax seed` writes two research filesystems of about 35,000 sparse files (123 TB of
logical size on under 10 MB of disk), with ages going back fifteen years, and scans each one six times with
fs-inventory, a month apart, with six months of activity simulated in between: labs grow, clean up, move a
project, and join. It adds 14 labs and departments, 20 people, and storage prices, so there are six months of
history and charges to explore. Sign-in is a persona switcher: be a PI, a delegate, or a storage administrator,
and switch at any time. Seeding takes about fifteen seconds and resets `var/` and `demo/` each time, so it runs
only when `PINAX_ENVIRONMENT=demo` (the default); Pinax refuses demo sign-in when `PINAX_ENVIRONMENT=prod`.

[docs/DEMO.md](docs/DEMO.md) has who is who, what happens in the six months, and a 20-minute walk-through.

## Use your own data

To try Pinax on a real filesystem from this checkout, keep its data apart from the demo's and sign in with
passwords Pinax keeps:

```bash
printf 'PINAX_ENVIRONMENT=dev\nPINAX_AUTH_MODE=local\nPINAX_VAR_ROOT=data/var\nPINAX_SCAN_ROOT=data/scans\n' > .env
```

Add yourself as the first administrator (it prints a temporary password), register the filesystem by its root,
scan it, and start Pinax:

```bash
uv run pinax user add jdoe@example.org --name "Jane Doe" --email jdoe@example.org --admin
```

```bash
uv run pinax filesystem add fast /fh/fast --name Fast
```

```bash
uv run pinax scan fast
```

```bash
uv run pinax serve
```

Sign in with the temporary password and choose your own. Then add labs and people under *People and access*, and
give each top-level folder its owner under *Folders and labs*; its members see it, and nobody else except storage
administrators. With this `.env`, `pinax seed` refuses to run, so it cannot wipe your data; move `.env` aside to
go back to the demo.

[docs/SETUP.md](docs/SETUP.md) walks through it step by step: scans made elsewhere and automatic ingest, checking
the numbers, importing labs and people from CSV, and prices. To install Pinax on a server, with services,
scheduled scans, and single sign-on, follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md);
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) lists every setting.

## Commands

| Command | Purpose |
|---|---|
| `uv run pinax serve [--host H] [--port N]` | The web app (default port 8001) |
| `uv run pinax seed [--scale 1.0]` | Reset runtime data and build the demo (only when `PINAX_ENVIRONMENT=demo`) |
| `uv run pinax filesystem add SLUG ROOT [--name N] [--display-root P]` | Register a filesystem |
| `uv run pinax filesystem list` | List filesystems and their latest scan |
| `uv run pinax scan SLUG [--no-ingest] [-- fs-inventory options]` | Scan with fs-inventory, verify, and ingest |
| `uv run pinax ingest SCAN_DIR [--fs SLUG] [--allow-incomplete]` | Build a snapshot index from a finished scan |
| `uv run pinax ingest --watch [FOLDER] [--interval N] [--once] [--no-verify]` | Ingest each new verified scan under a folder (default `PINAX_SCAN_ROOT`) once |
| `uv run pinax snapshots` | List snapshots |
| `uv run pinax rebuild SNAPSHOT_ID... \| --outdated` | Build ready snapshots' indexes again from their scans, after an upgrade or a lost index |
| `uv run pinax user add USERNAME --name NAME --email EMAIL [--admin]` | Add a person, for example the first administrator; with `PINAX_AUTH_MODE=local`, prints a temporary password (`pinax user password USERNAME` makes a new one, `pinax user list` lists people) |
| `uv run pinax import FOLDER [--apply]` | Import labs, people, memberships, and folder ownership from CSV; a dry run unless `--apply` ([docs/IMPORT.md](docs/IMPORT.md)) |
| `uv run pinax check SNAPSHOT_ID \| SCAN_DIR [--index DIR] [--age-depth N] [--json]` | Check an index, and its changes since the previous scan, against `fs-inventory rollup`, directory by directory |
| `uv run pinax tags import FILE [--apply]` | Tag folders from CSV; a dry run unless `--apply` |
| `uv run pinax db status \| upgrade [--sql] \| copy TARGET` | The catalog's database: whether it is up to date, upgrading it, copying it to another database |
| `uv run pytest` | Tests: index correctness, the rollup cross-check, queries, authorization, and an end-to-end scan |

Every command and option is in [docs/CLI.md](docs/CLI.md). API documentation is served at `/api/docs`.

## Layout

```text
src/pinax/
  config.py, db.py, models.py      settings, catalog engine, catalog schema
  migrate.py, migrations/          the catalog's migrations (Alembic), on SQLite or PostgreSQL
  categories.py                    file categories, type normalization, age buckets
  ingest/build.py                  scan -> snapshot index (DuckDB + Parquet)
  crosscheck.py                    an index against fs-inventory rollup, directory by directory
  index.py                         read-only queries over one snapshot index
  snapshots.py, watch.py           scanning, ingest, retention, open indexes; automatic ingest of new scans
  people.py, labs.py, signin.py    people, labs and departments, local sign-in
  onboard.py, ownership.py         labs, people, and folder ownership from CSV; who owned each folder when
  costs.py, charges.py             prices and free tiers; monthly charges
  auth.py, explore.py              identity and folder access; explore and report services
  serialize.py, api/               JSON views; FastAPI routers
  seed.py, cli.py, main.py         demo data, command line, app
  static/                          HTML, css/pinax.css (themes, chart colors), js/ (core, treemap, views), img/
bench/                             synthetic scans of any size, index and charges benchmarks
scripts/install-fs-inventory.sh    installs the pinned fs-inventory release into bin/
tests/                             pytest suite
docs/                              demo, setup, architecture, configuration, deployment, import, development
pinax-spec/                        requirements and roadmap
bin/  var/  scans/  demo/  data/  fs-inventory binary and runtime data (gitignored)
```

## License

Copyright 2026 Robert McDermott.

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).
