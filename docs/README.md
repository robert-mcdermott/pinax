# Pinax documentation

Pinax shows what is stored on large research filesystems, who owns it, how old it is, how it is changing, and what it
costs, from fs-inventory scans. These guides cover using it, installing it, and running it. The same pages, with the
screenshots in both themes, make the website (`node docs/site/build.mjs`; [docs/site/README.md](site/README.md)).

## For everyone

| Guide | What it covers |
| --- | --- |
| [How Pinax sees storage](CONCEPTS.md) | Filesystems, scans, snapshots, folder ownership, sizes, ages and cold data, change, costs, plans, tags, and the catalog |
| [User guide](USER-GUIDE.md) | Every page a lab sees: My data, the age map, history and forecasts, the change map and what changed, the reports, finding files, storage charges, cleanup plans, tags and collections, saved views |
| [The demo](DEMO.md) | Pinax with fictional labs and six months of scans, in one command, and a 20-minute walk-through |

## For administrators

Read in this order when setting Pinax up for the first time:

| Guide | What it covers |
| --- | --- |
| [Trying Pinax on your own data](SETUP.md) | One machine, one filesystem, a first scan, and your first people, in a few minutes |
| [Deployment](DEPLOYMENT.md) | Installing on a server (services, single sign-on or passwords), scans on a schedule and automatic ingest, checking the numbers, the catalog on SQLite or PostgreSQL, backups, upgrades, sizing, charges, logs, metrics, and day-to-day tasks |
| [Configuration](CONFIGURATION.md) | Every setting, with its default |
| [Administration guide](ADMINISTRATION.md) | The administrator's pages: All data, folders and labs, people and access, scans, storage health, capacity, costs, chargeback, tags, every lab's plans, and the audit log |
| [Importing labs and people](IMPORT.md) | The CSV files for labs, people, memberships, and folder ownership |
| [Command line](CLI.md) | Every `pinax` command and option |
| [Troubleshooting](TROUBLESHOOTING.md) | What a problem looks like, why, and what to do |

## How it works

| Guide | What it covers |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | The design: the snapshot index and how it is built, checked, and queried, the catalog, security, and measured performance |
| [Development](DEVELOPMENT.md) | Working on Pinax: setup, code layout, conventions, tests (on PostgreSQL too), migrations, the HTTP API, benchmarks |
