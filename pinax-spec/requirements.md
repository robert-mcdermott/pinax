# Pinax requirements

What Pinax must do, and the wave that delivers each requirement.
- `roadmap.md` orders them into waves; wave *N* is released as 0.*N*.0.
- `docs/ARCHITECTURE.md` explains how they are met; `docs/DEVELOPMENT.md` holds the conventions.

Status: **planned (wave N)** until built. Then **done**, **partial** (the gap noted) or **parked**, with the
validation that shows it. Requirement IDs are stable and never reused.

## Users

- **PIs and lab members** who want to know how much data their lab keeps, how old it is, what kind it is, who
  owns it, and where the old data sits, without asking IT or writing `du` loops.
- **Lab managers and data managers** who look after several labs' folders and tidy them up.
- **Storage engineers** who plan capacity, find what is growing, and spot what hurts the storage (huge
  directories, small files, unreadable folders).
- **Research data services staff** who map folders to labs, chase data whose owners have left, and decide what to
  archive.

## Platform

| # | Requirement | Status |
| --- | --- | --- |
| P1 | A uv-managed Python package with one command per task (`pinax serve`, `seed`, `scan`, `ingest`, `filesystem`, `snapshots`) | done (wave 1, slice 1) |
| P2 | Apache-2.0; `LICENSE` and `NOTICE`; SPDX headers in source files | done (wave 1, slice 1) |
| P3 | Read-only: never opens, moves, or changes a file on the storage it reports on; reads only fs-inventory scans | done (wave 1, slice 2) |
| P4 | Light and dark themes, keyboard-reachable charts, WCAG AA text contrast, a table view for every chart, chart colors validated in both themes | done (wave 1, slices 1 and 4: age and share ramps pass the ordinal checks in both themes; checked at 375 px) |
| P5 | A demo that builds in one command with real fs-inventory scans of realistic data | done (wave 1, slice 7: about 34,000 sparse files, 117 TB logical, two scans per filesystem, about ten seconds; wave 3, slice 2: six scans per filesystem a month apart, with a story in each month, about 35,000 files and 123 TB, about fifteen seconds; wave 6: capacities, one filesystem filling within the year) |
| P6 | Service files for the web app and ingest; a container image | partial (wave 2, slice 6: systemd units in `deploy/`; wave 10: container) |
| P7 | PostgreSQL catalog with migrations | planned (wave 10) |

## Scans and indexes

| # | Requirement | Status |
| --- | --- | --- |
| S1 | Read fs-inventory Parquet scans of schema versions 2 and 3; accept only complete scans (`--allow-incomplete` for `incomplete`); refuse several roots, other formats, missing columns | done (wave 1, slice 2) |
| S2 | Install the pinned fs-inventory release with checksum and version checks; `pinax scan` runs scan and verify, then ingests | done (wave 1, slice 2: fs-inventory 1.0.0) |
| S3 | Build a snapshot index with subtree totals for every directory, bounded memory, at billion-file scale | partial (wave 1, slice 2: measured to 10 million files; wave 2, slice 2: a real local scan and 12 million synthetic files with wide and deep folders; wave 2, slice 3: 100 million synthetic files in 121 s within 8 GB; wave 9: production scans) |
| S4 | Ingest new verified scans automatically, never twice, with failures visible | done (wave 2, slice 4: `pinax ingest --watch`) |
| S5 | Several filesystems, each with its own root and display root | done (wave 1, slice 2) |
| S6 | Keep the newest snapshots' indexes; keep folder history for every snapshot | done (wave 1, slice 2: `PINAX_KEEP_SNAPSHOTS`) |
| S7 | Say when totals are lower bounds (folders not read completely) | done (wave 1, slices 2 and 4) |
| S8 | Explore any retained snapshot, not only the latest | done (wave 1: the API takes a snapshot; wave 3, slice 6: a scan picker on every report, by day, kept by links; pruned scans open History) |
| S9 | The change in every directory between two scans of a filesystem: size, space on disk, files, folders, and age mix, with new and removed folders; checked against an independent computation | partial (wave 3, slice 1: since the previous scan, at ingest, checked against two scans' fs-inventory rollups; slices 4 and 5: any two retained scans) |
| S10 | Follow a folder renamed or moved within a filesystem by its directory's inode, so a marked folder keeps its tags and plan | planned (wave 7) |

## Exploration

| # | Requirement | Status |
| --- | --- | --- |
| E1 | An age map of any folder: a treemap of its subfolders, drill down by clicking, up by breadcrumbs, at directory level | done (wave 1, slice 4) |
| E2 | Size tiles by size, space on disk, file count, one category's size, or size older than a threshold | done (wave 1, slice 4) |
| E3 | Color tiles by average age, time since last change, oldest file, time since last access, cold share, or one category's share | done (wave 1, slice 4) |
| E4 | One or two levels; the long tail folded into one tile; a folder's own files as a tile | done (wave 1, slice 4: 60 children, 12 grandchildren each) |
| E5 | A person's home spans every folder their labs own, on any filesystem; a lab's own scope | done (wave 1, slice 3) |
| E6 | Change over time: history charts, a change map, what changed between two scans | done (wave 3, slices 3 to 5) |
| E7 | History charts of a folder, lab or department, and filesystem over its scans: size, files, cold share, mean age | done (wave 3, slice 3: every scan for homes, labs, filesystems, and top-level folders; the retained snapshots below the top level; a table and CSV) |
| E8 | A change map: the treemap colored by growth or shrinkage since a chosen scan, drilling down like the age map | done (wave 3, slice 4: since any retained scan, new and removed folders drawn; 7–31 ms at a million files; wave 9: at 100 million) |

## Reports

| # | Requirement | Status |
| --- | --- | --- |
| R1 | Age profile of any folder by modification and access time, and of each subfolder | done (wave 1, slice 5) |
| R2 | File types and categories; compressed files counted as what they contain | done (wave 1, slices 2 and 5: twelve categories; wave 2, slice 2: types found on a real disk added) |
| R3 | Cold folders: the largest self-contained folders unchanged for a chosen time | done (wave 1, slice 5; wave 2, slice 3: precomputed per threshold, 21 ms for a whole 100-million-file filesystem) |
| R4 | Owners of any folder, flagging accounts that no longer exist | done (wave 1, slice 5) |
| R5 | Find files by name or glob, type, category, size, age, and owner; CSV export | done (wave 1, slice 5) |
| R6 | Call out uncompressed sequencing data | done (wave 1, slice 5) |
| R7 | Totals that count hard-linked files once | planned (wave 11) |
| R8 | Cost by lab, department, and filesystem | done (wave 5, slice 1: prices and free tiers per filesystem, from a day on; slice 2: monthly charges per lab or department and filesystem, time-weighted, with ownership history; slice 3: what labs pay, for their members, worked out; slice 4: chargeback for finance, with a CSV statement) |
| R9 | What changed in a folder between two scans: the subfolders that grew and shrank most, new and removed folders, and the largest new and removed files; CSV | done (wave 3, slice 5: 70–90 ms for a lab folder at a million files; wave 9: at 100 million) |
| R10 | Forecasts of size: each filesystem, lab and department, home, and all data, 12 months ahead from the trend of its scans, with a range and a backtest | done (wave 6, slice 1: in every history, tested on synthetic histories with known growth, noise, steps, and gaps; 5 ms for a lab and 2 ms for all data on a year of weekly scans of 2,000 folders; slice 2: in *History*, with its range, backtest, and table) |
| R11 | Each filesystem's capacity, and when it fills at its trend | done (wave 6, slice 3: recorded by `pinax scan` or set by administrators from a day on; the day it fills, with its range, tested against the trend worked out by hand) |
| R12 | Each lab's and department's charges for the next 12 months at its trend, with the prices and free tiers as scheduled | done (wave 6, slice 4: on *Storage charges* and *Chargeback*, equal to the cent to an hour-by-hour charge on the forecast sizes) |

## Cleanup plans

| # | Requirement | Status |
| --- | --- | --- |
| C1 | A lab's PI and delegates, and administrators, mark folders to delete, move to another filesystem, or compress, while exploring; Pinax changes nothing on storage (P3) | planned (wave 7) |
| C2 | What a plan saves: the space freed and the fall in the lab's monthly charge, with free tiers and the destination's price | planned (wave 7) |
| C3 | Progress from later scans: done, partly done, moved (no saving), or still to do, and what was actually freed | planned (wave 7) |
| C4 | Suggested folders, each with its saving: cold folders, uncompressed sequencing data, workflow working folders | planned (wave 7) |
| C5 | Every lab's plan for administrators, planned and freed per filesystem, with CSV | planned (wave 7) |

## Tags

| # | Requirement | Status |
| --- | --- | --- |
| T1 | Standard tags kept by administrators, typed (flag, text, date, choice), with a default list a site changes | planned (wave 8) |
| T2 | Data owners and administrators tag folders, and tags apply to everything below; labs make custom tags, which administrators can merge into standard ones | planned (wave 8) |
| T3 | Find folders by tag and value across filesystems, with their totals, history, and forecast; CSV | planned (wave 8) |
| T4 | Charges split by a tag's values, such as by grant or project | planned (wave 8) |
| T5 | Tags that protect folders from cleanup plans (*Keep until*, *Legal hold*) | planned (wave 8) |

## Access

| # | Requirement | Status |
| --- | --- | --- |
| A1 | People see only the top-level folders their active labs own, checked on the server for every request; others' folders answer 404; paths must be canonical | done (wave 1, slice 3: tests cover prefixes, `..`, other roots) |
| A2 | Storage administrators see every filesystem and folder | done (wave 1, slice 3) |
| A3 | Labs and departments own top-level folders (one owner each), the same model as Tundra; changes audited | done (wave 1, slice 6; after 0.5.0: added, changed, deactivated, and reactivated in the app, as well as by import) |
| A4 | Demo identity; trust a single sign-on proxy's header in production | done (wave 1, slice 1; wave 2, slice 6: only from trusted proxies, checked behind oauth2-proxy 7.15) |
| A5 | Import labs, people, memberships, and folder ownership in bulk | done (wave 2, slice 5: `pinax import`, docs/IMPORT.md) |
| A6 | Single sign-on with roles from directory groups; membership synced from the directory | planned (wave 10) |
| A7 | An access recertification report | planned (wave 10) |
| A9 | Sign in without single sign-on: passwords Pinax keeps, temporary passwords that must be changed, lockout, sessions, audit; the first administrator from the command line; the demo's sign-in-as-anyone refused in production | done (wave 4, slice 2: `PINAX_AUTH_MODE=local`) |
| A8 | Manage people in the app, as in Tundra: add people, change their details, lab memberships, and administrator role, deactivate and reactivate them; every change audited; an administrator always remains | done (wave 4, slice 1: *People and access*) |

## IT operations

| # | Requirement | Status |
| --- | --- | --- |
| O1 | Overview of every filesystem: totals, largest folders, biggest changes, unmapped folders, activity | done (wave 1, slice 6) |
| O2 | Storage health: scan errors, folders not read, very large directories, small-file hotspots, problem names, deleted owners | done (wave 1, slice 6) |
| O3 | Scans: every snapshot with its status, timings, and sizes | done (wave 1, slice 6) |
| O4 | Alerts when a scan or ingest fails | partial (wave 2, slice 4: failed and skipped scans, and a watcher that stopped, shown on *Scans*; `--once` exits 1 on a failure; wave 10: a metric that monitoring can alert on; alerts by email or chat are in the roadmap's parking lot) |
| O5 | Metrics, structured logs, slow-query logging | planned (wave 10) |

## Quality and performance

| # | Requirement | Status |
| --- | --- | --- |
| Q1 | Totals agree with an independent computation | partial (wave 1, slice 7: equal to brute-force sums over every file in the tests; wave 2, slice 1: `pinax check` equal to `fs-inventory rollup` on every directory of the demo and synthetic scans; wave 2, slice 2: equal on a real local scan and on wide and deep synthetic scans; wave 3, slice 1: the changes between scans too; wave 3, slice 2: the changes between every pair of demo scans and between evolved synthetic scans; wave 5, slice 2: monthly charges equal to an hour-by-hour sum; wave 9: production scans) |
| Q2 | Every request value is bound as a query parameter; SQL fragments come only from fixed lists | done (wave 1, slice 3) |
| Q3 | Directory-level queries (treemap, types, owners, cold folders of a lab) within 100 ms at a billion files | partial (wave 1: 23–64 ms at 10 million files; wave 2, slice 3: 1–50 ms at 100 million synthetic files; wave 5, slice 4: a month's charges for every lab in 25 ms on a year of weekly scans of 2,000 folders; wave 9: on production) |
| Q4 | Searches scoped to a lab folder within 1 s; whole-filesystem searches within 60 s at a billion files | partial (wave 1: 10 ms and 273 ms at 10 million files; wave 2, slice 3: 22 ms and 2.8 s at 100 million, about 30 s projected at a billion; wave 9: on production) |
| Q5 | Index build within 2 hours per billion files on a server; index at most 60 bytes per file | partial (wave 1: 2.2 s per million files on a 10-core laptop; 55 bytes per file; wave 2, slice 3: 1.2 s per million at 100 million within 8 GB, about 20 minutes projected per billion; 56 bytes per file; wave 9: on a server) |
