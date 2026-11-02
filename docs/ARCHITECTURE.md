# Pinax architecture

Pinax reports on what research storage holds: how much, how old, what kind, and who owns it, for
file systems of many petabytes and more than a billion files. This document describes the design, the
snapshot index that makes it fast, how it is built and queried, security, and measured performance.

## Goals and constraints

1. **Directory-level answers at any scale.** A PI opens their lab folder and sees a treemap of its
   subfolders colored by age, then drills down. That must take well under a second whether the folder
   holds a thousand files or a hundred million.
2. **Many dimensions of the same folder.** Size, file count, space on disk, age (by modification and access
   time), file type and category, and owner, for any folder at any depth.
3. **File-level search when needed**: by name or pattern, type, size, age, and owner, scoped to what the
   person may see.
4. **History.** Each scan is kept as a snapshot, so growth and ageing show over time.
5. **Strict scoping.** People see only the top-level folders their labs own; IT sees everything.
6. **Read-only.** Pinax never touches the file system it reports on. It reads fs-inventory scans.

## Components

```text
  Storage nodes or scan hosts
  ┌──────────────────────────────────────────────────┐
  │ fs-inventory scan (Rust; parallel, resumable,    │
  │ read-only) -> Parquet files and a manifest       │
  └──────────────────────────────────────────────────┘
                          │ scan folder (a shared path)
                          ▼
  Pinax server
  ┌──────────────────────────────────────────────────┐
  │ pinax ingest (DuckDB): scan -> snapshot index    │
  │   var/snapshots/<fs>/<scan>/                     │
  │     index.duckdb    directory-level tables       │
  │     files/gNNNNNN/  per-file Parquet, by group   │
  │                     of top-level folders         │
  │                                                  │
  │ catalog (SQLite, or PostgreSQL)                  │
  │   people and roles, labs, folder ownership,      │
  │   filesystems, snapshots, folder history, ...    │
  │                                                  │
  │ FastAPI /api ── read-only DuckDB cursors         │
  │ static UI (HTML, CSS, JS modules; no build)      │
  └──────────────────────────────────────────────────┘
                          ▲ HTTPS, through nginx/Caddy (passwords
                          │ or Entra ID) or oauth2-proxy
  PIs, lab members, finance, auditors, IT, and scripts
```

| Piece | Choice | Why |
|---|---|---|
| Crawler | fs-inventory 1.0.0 | Built for this: parallel, bounded memory, resumable, lossless names, about 40 bytes per entry in Parquet (measured at 100 million files) |
| Index engine | DuckDB (embedded) | Columnar, vectorized, spills to disk, reads Parquet natively; no server to run |
| Per-file store | Parquet, split by top-level folder and sorted by path | Compact; a lab's queries read only its files; a subfolder is a contiguous range |
| Catalog | SQLAlchemy on SQLite by default, or PostgreSQL; Alembic migrations | Small, transactional data: who owns what, which snapshots exist, folder history, prices, the audit log |
| Web | FastAPI and plain JavaScript modules | Same stack and patterns as Tundra; nothing to build |

## What Pinax keeps

Pinax keeps three kinds of data. Only the catalog cannot be made again, so it is the one to back up.

| | Where | What it holds | Size | If it is lost |
|---|---|---|---|---|
| **Catalog** | `PINAX_VAR_ROOT/pinax.db` (SQLite) | Filesystems; a record of every scan; folder history (each top-level folder's totals for every scan); people, their site roles, labs and departments, memberships (with when each role was given), folder ownership and who owned each folder when; local sign-in password hashes and sessions; API tokens (their SHA-256, never the token); prices and free tiers; capacities; cleanup plans, tags, and saved views; the audit log; the ingest watcher's heartbeat | Small: about one row per top-level folder per scan (2,000 folders scanned weekly add about 100,000 rows a year) | Restore it from a backup. Without one, register the filesystems again, import people, labs, and folder ownership from the import files, and ingest the kept scans again; prices and free tiers, ownership history, folder history of scans no longer kept, site roles, API tokens (scripts need new ones), plans, tags, views, and the audit log are lost |
| **Snapshot indexes** | `PINAX_VAR_ROOT/snapshots/<filesystem>/<scan>/`, one per kept snapshot | `index.duckdb`: every directory's totals, and its changes since the scan before, with the files new and removed since; `files/`: Parquet, one row per file, for searches and file lists | About 56 bytes per file, plus about 60 per file new or removed since the scan before, for each of the newest `PINAX_KEEP_SNAPSHOTS` (6) snapshots of each filesystem | `pinax rebuild SNAPSHOT_ID` makes it again from its scan |
| **Scans** | Where fs-inventory wrote them: `PINAX_SCAN_ROOT` for `pinax scan` and the watcher, or anywhere for `pinax ingest` | fs-inventory's Parquet and `manifest.json`: every entry's path, size, times, and owner | About 40 bytes per entry | Nothing changes in the app; that snapshot's index can no longer be rebuilt or checked |

**What reads what.** The web app reads only the catalog and the indexes, never the scans or the filesystem.
Ingest reads a scan once, builds its index, and records the snapshot and its folder history in the catalog. After
that a scan is read again only by `pinax rebuild` (after an upgrade that changes the index format, or when an
index is lost) and by `pinax check` (the cross-check against `fs-inventory rollup`, which also uses the scan
before it to check the changes).

**Retention.** After every ingest, the indexes of all but the newest `PINAX_KEEP_SNAPSHOTS` ready snapshots of
that filesystem are deleted, and those snapshots are marked *pruned*. Their folder history stays, so *History* for
top-level folders, labs, departments, and filesystems, and every month's charges, cover every scan ever ingested.
Treemaps, reports, searches, the change map, and history below the top level need a full index, so they cover the
kept snapshots only. Only ready snapshots can be rebuilt, so a pruned snapshot stays pruned.

**Scans are never deleted by Pinax.** Keep the scan of every snapshot that still has its index, so each can be
rebuilt after an upgrade and checked. Once a snapshot is pruned, Pinax has no further use for its scan, and the
folder can be deleted (or kept for other analysis). `pinax snapshots` lists each snapshot's status and scan folder.
Weekly scans of a billion-file filesystem take about 40 GB each, about 2 TB a year if none are deleted.

**Names are sensitive.** Scans and indexes hold every file and folder name and owner account names, and the
catalog holds top-level folder names; names in research storage can carry identifiers, such as a study subject's
ID in a file name. The catalog also holds people's names and emails and, with local sign-in, password hashes.
Treat all three like the filesystem's own metadata: only the `pinax` user and group can read `/var/lib/pinax`
(mode 0750), and backups and copies need the same protection. In the app, only storage administrators and auditors
see across labs (finance sees every lab's charges, not its folders).

### The catalog database

The catalog is SQLite by default: a file in `PINAX_VAR_ROOT` in WAL mode, opened by the Pinax processes themselves,
with nothing to install and no database server to run. Many readers work at once; ingests take turns under a lock in
`PINAX_VAR_ROOT`. That suits one server running the web app and the watcher, as [DEPLOYMENT.md](DEPLOYMENT.md)
installs it, and every demo and trial.

For production, `PINAX_DATABASE_URL` can point at PostgreSQL instead (with Pinax's `postgres` extra installed): a
database that IT runs, backs up, replicates, and monitors as it does others, and whose writers do not wait on one
file's lock. Pinax is still one server: indexes and background searches are on its disk. The whole test suite runs
on both.

**Migrations** (Alembic, in `src/pinax/migrations`, `migrate.py`) create the catalog and upgrade it in place. They
run when Pinax starts, as new tables and columns were added before, unless `PINAX_DB_AUTO_UPGRADE=false` leaves them
to `pinax db upgrade`, which can also print its SQL for a database administrator. Processes that start together
take turns (a lock on PostgreSQL, a lock file beside a SQLite catalog), and a catalog that a newer Pinax has
upgraded is refused rather than used. The first revision (`0001`) is the catalog as 0.9.0 left it, written out
rather than taken from the models so it stays fixed; on a catalog made before migrations it adds only the tables and
columns that catalog lacks, as Pinax did at start-up until then. Constraints and indexes are named by a rule
(`db.NAMING`), so later revisions can name what they change on either database. A test compares the schema the
migrations make with the models', on both.

## The key idea: totals per directory, computed once per scan

Drawing a treemap of a folder needs, for each child, its total size, file count, age distribution, and
category breakdown *over its whole subtree*. Computing that from a billion file rows per click is
impossible; computing it once per scan, for every directory, is cheap. So ingest turns a scan into a
**snapshot index** in which every directory row already carries its subtree totals, and the interface
never reads file rows to draw a chart.

The age color of a folder comes from those totals: the *average age* is weighted by size
(sum of size × modification day, divided by total size), and the *last change*, *oldest file*, and
*last access* come from subtree minimum and maximum times. The *cold share* comes from the age histogram.

## The snapshot index

`var/snapshots/<filesystem>/<scan start>-<scan id>/`:

| Table or file | One row per | Holds | Sorted by |
|---|---|---|---|
| `dirs` | directory | Subtree totals: files, folders, bytes, allocated bytes; files and bytes per age bucket by modification time; bytes per age bucket by access time; files and bytes per category; oldest and newest modification, newest access and change; size-weighted time sums for mean ages; count of folders not read completely. Plus the directory's own files (count, bytes, times), its entry count and any scan error, and its device and inode (from index version 6), and its extra hard links and their bytes (from version 7). | `(depth, path)` |
| `dir_ext` | directory and file type | Totals over the directory's *own* files of that type | `path` |
| `dir_owner` | directory and owner (uid) | Totals over the directory's *own* files of that owner | `path` |
| `top_ext`, `top_owner` | top-level folder and type / owner | The same, summed per top-level folder | `top_id` |
| `changes` | directory of this scan or the filesystem's previous one | Both scans' subtree totals (files, folders, bytes, allocated bytes, size-weighted time sum, files and bytes per age bucket), each scan's own ages, and whether the directory is in each: what changed, new and removed folders included. Only when there was a previous scan | `(depth, path)` |
| `file_changes` | file new since the previous scan, or removed since | `change` (`new` or `removed`), then the file's path, name, type, category, kind, size, allocated bytes, owner, group, times, links, and name flags (a removed file's from the previous scan). Only when there was a previous scan, from index version 5 | `(change, path)` |
| `work_dirs` | workflow working folder or package cache | Its path and kind: a Nextflow `work/` (beside a `.nextflow`), Snakemake `.snakemake/`, Cromwell `cromwell-executions/`, conda `pkgs/` (in a conda installation), or a Singularity or Apptainer `cache/`. From index version 6 | `path` |
| `cold_units` | age threshold and cold folder | For each threshold (90 days to 10 years), the largest folders in which every file is at least that old and whose parent has a newer one: path, depth, files, bytes | `(bucket, path)` |
| `tops`, `file_parts` | top-level folder | Name, path, ID; file count and bytes | |
| `users`, `groups` | uid / gid | Names from the scan (`--resolve-ids`); a missing name is a deleted account | |
| `errors`, `name_flags` | scan error / top-level folder and flag | Errors with their top-level folder; counts of problem names | |
| `meta` | key | Scan ID, root, reference time, the age buckets and categories used, totals | |
| `files/gNNNNNN/part-*.parquet` | non-directory entry | Top-level folder, path, name, normalized type, category, kind, size, allocated bytes, owner, group, times, links, name flags; at most 64 groups of top-level folders (`file_parts` says which group holds each) | `path` within each group |

### Why the sort orders matter

DuckDB keeps minimum and maximum values for every block of rows (zone maps), and skips blocks that cannot
match. The sort orders turn every query Pinax makes into a contiguous read:

- **Children of a folder** `P` at depth `d` are exactly the rows of `dirs` with `depth = d + 1` and
  `path` in `[P/, P0)` (in byte order `0` follows `/`). Because `dirs` is sorted by `(depth, path)`, those
  rows are adjacent. Grandchildren are the same at `d + 2`.
- **A subtree's type or owner breakdown** sums the rows of `dir_ext` or `dir_owner` with `path = P` or `path`
  in `[P/, P0)`, which are adjacent because those tables are sorted by `path`. Top-level folders and the
  whole filesystem use the pre-summed `top_ext` and `top_owner`.
- **Files below a folder** live in the Parquet files of its top-level folder's group, sorted by path, so the
  folder is a range of row groups and the rest are skipped. Every file query is limited to the folder's path
  range, so a lab's query never returns another lab's files, even when they share a group.
- **New and removed files below a folder** are the rows of `file_changes` with `change = 'new'` (or `'removed'`)
  and `path` in `[P/, P0)`, which are adjacent because it is sorted by `(change, path)`.

### File types and categories

A file's type is its lowercased extension, joined with the one before when the last is a compression
suffix: `S1_R1.fastq.gz` is `fastq.gz`, `brain.nii.gz` is `nii.gz`. Its category comes from the inner
type, so compressed sequencing data is still sequencing data. There are twelve categories (sequencing and
genomics, imaging, tabular and array data, mass spectrometry, archives, models, code, documents, media,
containers, logs and temporary, other) defined in `categories.py`. Seven age buckets run from under 90 days
to ten years or more. Both lists are recorded in each snapshot, so changing them affects only new snapshots.
Ages are whole microseconds from the scan's start, so a file exactly on an edge is in the older bucket, and
times after the scan started count as under 90 days.

## Building the index

`ingest/build.py`, in DuckDB, writing to a temporary folder that is renamed into place when complete:

1. **Top-level folders**: the depth-1 directories of the scan.
2. **Classify and stage**: one pass over the scan's entries computes each file's top-level folder, type,
   and category, and writes them partitioned by group of top-level folders.
3. **Per group of top-level folders**, one at a time: sort the group's files by path into `files/gNNNNNN/`,
   then aggregate them by parent directory (totals, age buckets, categories, time sums), by directory and type,
   and by directory and owner. There are at most 64 groups, however many top-level folders there are, so the
   write that stages them keeps a bounded number of files open (DuckDB keeps one open and buffered per
   partition). Working one group at a time bounds memory by the largest group, not the file system, and DuckDB
   spills to disk beyond `PINAX_BUILD_MEMORY_LIMIT`.

Steps that read the scan itself use fewer threads than the rest when memory requires: fs-inventory writes row
groups of about a million entries, and a thread decodes a whole row group (146 MB of the columns Pinax reads, in
a synthetic scan), so ten threads needed more than 8 GB. The build reads the largest row group's size from the
Parquet metadata and runs as many threads as fit in three quarters of the memory limit, and halves them and
retries any step that still runs out.
4. **Subtree totals, bottom-up one level at a time**: starting from the deepest level, each directory's
   totals are its own files plus the sums of its children's totals (minimums and maximums for times).
   Each level reads only its own rows and the level below, so the whole tree costs one pass, however deep.
5. **Write** `dirs`, `dir_ext`, `dir_owner`, and the per-folder summaries in their sort orders, plus users,
   groups, errors, and metadata.
6. **Cold folders**: for each of the six age thresholds, every directory whose files are all at least that old
   (no files in younger buckets) while its parent's are not: one self-join of `dirs` per threshold.
7. **Changes**: when the filesystem has an earlier ready snapshot whose index is on disk, its `dirs` table is
   attached read-only and joined with the new one by path, and both scans' totals are kept in `changes`. A
   directory in only one scan is new or removed; a renamed folder is both. The comparison is done once, and
   survives the earlier index being pruned. Then the files: each group's files are anti-joined by path with those
   of the previous scan's top-level folders of the same names, both ways, and every new and removed file goes into
   `file_changes` (a top-level folder only in the previous scan is all removed files). One group at a time, like
   the sort, so memory is bounded by a group. A million files with a week of synthetic changes (55,000 files new
   or removed) adds 0.85 s to a 7 s build, and the table takes about 60 bytes per file in it.

Then ingest records the snapshot and one `FolderStat` row per top-level folder in the catalog: those rows are
the durable history (growth, cold share, mean age per scan) and they survive when old indexes are pruned. The
snapshot also records the whole filesystem's totals (`info["totals"]`), so a filesystem's history reads one row
per scan rather than one per folder.

Ingests run one at a time, under a file lock in `PINAX_VAR_ROOT`, and a scan ID is recorded once. `pinax ingest
--watch` (`watch.py`) polls a folder for new scans and ingests each complete one once; scans it does not ingest
are recorded as skipped or failed, with why, and a heartbeat row tells *Scans* whether the watcher is alive
(`docs/DEPLOYMENT.md`).

Only scans whose manifest says `complete` are accepted (`--allow-incomplete` also accepts `incomplete`, where
stalled directories are missing). Directories the scanner could not read or did not enter (mount points,
`--max-depth`) are counted in `incomplete`, so the interface can say when totals are lower bounds. A directory
reached a second time under `--detect-cycles` is not: its contents are counted where it was first found.

## Checking the totals

`pinax check` (`crosscheck.py`) compares an index with `fs-inventory rollup`, a separate implementation in
Rust of the same subtree totals. Rollup runs on a shadow of the scan folder (a copy of the manifest and a
link to the entries, so the scan itself is never written to; for a scan with hard-linked files, a copy of the
entries in which every link after a file's first has no size, since rollup counts every link and Pinax counts a
file once, the rule written again in `crosscheck.py` rather than shared), with Pinax's age bucket edges
(`90d,1y,2y,3y,5y,10y`) and an age depth as deep as the scan, so every directory gets its own histogram. The
two are then joined on `dir_id` and compared on 31 measures per directory: path, files, folders, bytes,
allocated bytes, incomplete folders, oldest and newest modification time, newest access and change time, and
files and bytes per age bucket by modification time and bytes per bucket by access time. Rollup's `future`
bucket is part of Pinax's first. Any difference is a bug in one of them until explained.

| Scan | Folders | Index build | Rollup | Compare | Result |
|---|---|---|---|---|---|
| Demo, four scans | 36 to 935 | under 1 s | 0.1 s | under 0.1 s | equal |
| Synthetic, 1 M files | 244 K | 9.2 s | 0.5 s | 0.3 s | equal |
| Synthetic, 10 M files | 244 K | 23 s | 1.4 s | 0.3 s | equal |

The first run against 0.1.0 found two differences, both fixed in Pinax. Ages were fractional days, and
rounding put a file exactly on a bucket edge in the younger bucket for about one scan start time in eleven
(one in four at the ten-year edge). Directories revisited under `--detect-cycles` were counted as incomplete.
The tests include a scan built to sit on those edges.

When an index holds `changes`, `pinax check` also runs `fs-inventory rollup` on the previous scan and compares
every directory of both scans: its totals in each and whether it is in each, against the two rollups joined by
path. A wrong difference cannot then come from Pinax's own totals agreeing with themselves. The previous scan's
folder must still exist for this part of the check. At 100 million files (2.44 million folders), comparing takes
0.9 seconds of the build and adds 228 MB to the index, about 2.3 bytes per file; checking it takes 24 seconds,
nearly all of it the two rollups, and every directory was equal (between two synthetic scans with the same
contents). Between changed scans from `synth_scan.py --evolve` at one million files, comparing takes 0.2 seconds
of a six-second build and checking it 1.1 seconds, every directory equal; changed scans at 100 million files wait
for server-class hardware (wave 15).

## Real filesystems

What real storage has and a model does not, and how Pinax handles it:

- **Names.** fs-inventory writes bytes that are not UTF-8 as `\xNN` in `path` and keeps the exact bytes in
  `path_raw`; Pinax uses `path`. Names with control characters (a newline, a tab) or bidirectional overrides
  (which make `report\u202Etxt.exe` display as `reportexe.txt`) are shown as escapes in the interface, and
  storage health counts names by flag. A valid name that contains the text `\xFF` renders like the byte 0xFF,
  so two such siblings would share a path; totals are joined on `dir_id` and stay right, but the two folders
  would show as one place.
- **Mount points** not entered are folders not read, so their totals are lower bounds; scans with
  `--cross-mounts` include them.
- **Times** in nanoseconds (`--time-unit ns`) are kept as microseconds, truncated toward zero as rollup does;
  times before 1970 and after the scan are accepted.
- **Scan folders** are read through the parts their manifest lists, so leftovers of an interrupted run don't
  count twice.
- **Deep and wide trees.** Subtree totals take one statement per depth level, about 10 ms each, so a chain
  1,000 folders deep adds about 10 seconds to a build. Paths of thousands of characters, folders of two million
  files, and folders of 200,000 subfolders work (timings below). The interface collapses long breadcrumb trails
  and clamps long paths.

A real scan was checked the same way: the maintainer's documents folder on a laptop (56 K files, 4.7 K folders,
118 GB, 16 levels, 260 file types, 15 flagged names, one owner). Every directory is equal to rollup; the index
builds in 0.8 s and every query takes under 25 ms. It also showed common types that were counted as *other*:
camera photo and video formats, macro-enabled spreadsheets, diagrams, executables, and disk images, 35% of its
bytes. With those added, *other* is 0.8% of its bytes, nearly all of it files without an extension. Production
storage, with many owners and a billion files, is measured in wave 15.

## Querying

`index.py` opens each snapshot's `index.duckdb` read-only and gives every request its own cursor. All
values from requests are bound as parameters; the only SQL fragments built in code come from fixed lists
(the size metrics, the sort orders). Recently used indexes stay open (`IndexCache`).

- **Treemap** (`/api/tree`): the node, its largest 60 children by the chosen size metric, and for two levels
  each child's largest 12 children. The rest are summed into one "smaller folders" tile; a folder's own files
  are a "files in this folder" tile, computed as its subtree minus all of its subfolders.
- **Icicle and sunburst** (`/api/depth`): every folder up to four, six, or eight levels below the node that holds at
  least 0.25% of it by the size metric, in one scan of the depth range and the node's path range, with the floor as
  a filter. A folder holds at least as much as any folder inside it, so the folders kept form a tree (each one's
  parent is kept too), with at most 400 on a level; the browser lays them out. At 10 million files (265,000 folders)
  it takes 4–7 ms anywhere, against 17–31 ms for the treemap's two levels. It reads every folder within the levels
  below the node, about 2 ms per million on the laptop below, so at the root of a filesystem of 100 million folders
  it grows to a few hundred milliseconds, like the other reports that read every folder (see below).
- **Types and owners**: range sums over `dir_ext` / `dir_owner`, or the pre-summed per-folder tables.
- **Cold folders**: directories whose newest file is older than the threshold while their parent has a newer
  change: each is a self-contained cold unit (folders inside it are not listed again). If the folder asked
  about is itself cold, it is the one unit; otherwise its units are the filesystem's units inside it, a range
  of `cold_units`. Indexes built before version 3 find them at query time by joining directories with their
  parents.
- **Files**: the top-level folder's Parquet files, restricted to the subtree's path range, filtered and sorted.
  How many match in all is a second pass over the same files, so *Find files* asks for it separately
  (`/api/files/count`), and only when the page is full: otherwise the page is every match.
- **Change map** (`/api/changes`): a directory's children in this scan and an earlier one, joined by path, with
  folders in only one of them, largest first by the larger of the two sizes (or now, or then), the rest folded, and
  each listed child's largest children for two levels. The previous scan comes from the index's `changes` table,
  read with the same depth-and-path-range scans as the age map; any other retained scan (`since`, a day: the newest
  kept on or before it) has its index attached read-only, once per index, and its `dirs` joined on the same
  ranges. At a million files, a lab folder's two levels take 18 ms from `changes` and 29 ms joined (the age map:
  24 ms).
- **What changed** (`/api/changed`): for the same two scans, the folders below a place in both scans that grew or
  shrank most, at any depth, leaving out a folder when one of its subfolders holds nine tenths or more of its
  change (so a chain of folders is listed once, where the change is); the outermost new and removed folders; and
  the largest new and removed files. Against the previous scan those are a range of `file_changes`; against an
  older one (or an index built before version 5), the two scans' file lists are anti-joined on path over the
  place's path range, in batches of groups of top-level folders up to ten million files (a larger group alone), so
  memory is bounded by a group's files. At a million files with a week of synthetic changes, a lab folder's
  queries take about 50 ms, and the whole filesystem's 105 ms against the previous scan (0.5 s against an older
  one); most of the 105 ms are the folder lists, which read every folder in the place.
- **History** (`/api/history`): size, space on disk, files, bytes per age bucket, cold share, and mean age per
  scan. A filesystem uses the totals recorded with each scan; a top-level folder, a lab, or a person's home sums
  folder history in the catalog, for every scan, pruned ones included. A folder below the top level reads its
  node from each retained snapshot's index, so its history covers `PINAX_KEEP_SNAPSHOTS` scans. Filesystems are
  scanned on their own schedules, so a place on several of them has a point per day with a scan, each
  filesystem as of its latest scan by then, its mean age carried forward to the point's time; cold shares are
  as of each filesystem's scan. With 2,000 top-level folders and 150 weekly scans, a filesystem's history takes
  3 ms and a lab's 4–8 ms (snapshots ingested before 0.3.0, which lack recorded totals, are summed instead:
  1.3 s at that size).

Every report takes the scan to show: the latest by default, `snapshot` (an ID), or `at` (a day: each
filesystem's newest scan taken by the end of it, so a home on several filesystems stays consistent). A day whose
scan is no longer kept in full answers 410, except in History, which shows every scan's folder totals;
`/api/scan-days` lists the days a place was scanned and which are kept. The interface keeps `at` in the address
and adds it to every place link and report request, so a chosen scan stays chosen.

A request without a filesystem and path covers the person's *home*: each of their labs' folders (which may be
on several filesystems), or every filesystem root for an administrator. Results from each place are merged;
mean ages are rebased to a common reference time.

### Time limits

A request reads for at most `PINAX_QUERY_TIMEOUT` seconds (180 by default). `limits.py` gives each GET under
`/api` a budget: when it runs out, or as soon as the browser leaves the page (the interface cancels a page's reads
when another page opens), the request's running DuckDB queries are interrupted, and it answers 504 saying what to
narrow. File-level queries of a whole filesystem (a search at its root, the new and removed files against an older
scan, problem names) read every file in it, so at most `PINAX_WIDE_QUERIES` (2) run at once: the others wait for
a turn within their own limit, and a query of a folder never waits. Pinax's limit is below the proxy's
(oauth2-proxy waits five minutes in `deploy/oauth2-proxy/pinax.cfg`), so a long request ends with Pinax's
explanation rather than a gateway error.

**Searches in the background** (`jobs.py`, *Searches*) are for what a page cannot wait for: a search of a whole
filesystem (for administrators, from *Find files*) runs in Pinax's own worker threads, at most `PINAX_WIDE_QUERIES`
at once, taking the same turns as those of pages, each within `PINAX_JOB_TIMEOUT` (an hour), and writes every match,
in order, to a Parquet file under `VAR_ROOT/jobs`, kept for `PINAX_JOB_DAYS` (7). A job searches the scans the
person saw when they asked; only they and administrators see it, and its CSV is written once, when first asked
for. A job left queued or running when Pinax stops is marked as stopped when it starts again.

## Forecasts

Every history (`/api/history`) comes with a forecast of its size (`forecast.py`): where the place is heading over
the next twelve months if it keeps growing as it has. It is worked out when asked from the history's own points,
so it covers whatever History covers, and nothing is stored.

- **The trend** is a straight line through the scans of the last six months: Sen's slope, the median of the
  slopes between every pair of scans, so one odd scan (a temporary copy, a scan that missed a folder) barely moves
  it. The forecast carries that slope forward from the latest scan, to the same day of each of the next twelve
  months. Weekly or monthly scans give too few points to tell growth curves apart, and a line is easy to explain.
- **The range** is the confidence interval of Sen's slope (from the ranks of the pairwise slopes, as in
  Kendall's test, at 80%), widened by how far the scans scatter about the trend. On synthetic histories with
  known growth and noise it holds the outcome a year ahead in 86–90% of cases, a little more than the 80% it is
  set for.
- **Steps.** A change between two scans far from the usual one, off it by more than a tenth of the size and by
  much more than the changes usually vary, is a step: a cleanup, data brought in or moved out. A trend drawn
  across a step describes neither side of it, so the trend starts after the last one, and says so.
- **Enough scans.** A forecast needs at least six scans over at least eight weeks, after the last step;
  otherwise it says why there is none (too few scans, too soon after a step, or the place is not in the latest
  scan).
- **The backtest** is what the same method forecast for the latest scan from the scans it had three months
  before, against the size now: how far off a forecast of this place has been.

Labs and departments are forecast from the folders they own now, as History shows them, so a folder moving
between labs does not look like growth.

### Capacity

How much each filesystem holds, and when it fills at its trend (`capacity.py`, *Capacity*):

- **How much it holds.** `pinax scan` asks the mounted filesystem its total, used, and available space
  (`statvfs`, which reads no file) and records them in `pinax.json` beside the scan's manifest; ingest keeps them
  with the snapshot (`info["storage"]`). An administrator can set the capacity instead, from a day on
  (`capacity` table, audited): from that day it takes the place of what the storage reports, and a later day
  schedules an expansion. Scans written by fs-inventory directly need it set.
- **What fills it**, the best measure the scans allow: the storage's own figure for space used, which counts what
  scans cannot see (the storage's snapshots, overhead), once six scans have it; else the scans' space on disk;
  else, for scans that record no space on disk (sparse files, as in the demo), the files' size.
- **When it fills.** The forecast of that measure is carried forward day by day against the capacity as
  scheduled: the first day it reaches it, the earliest within its range, and the latest, up to three years
  ahead. A filesystem that fills within `PINAX_FILL_WARNING_MONTHS` (12) is flagged on the overview.

A filesystem's history carries its capacity, drawn as a line on *Size by age* where it fits the scale (no more
than twice the data). That chart shows the files' size, while the date it fills uses the measure above, so the
two can differ by what the size does not count.

### Hard links

A file with several hard links is one file on disk, so totals count its size once (R7): at its first link by
path, for its device and inode. Its other links are still files (each is a name someone sees), with no size, in
every total: folders, ages, types, owners, folder history, and so charges. Each folder records its extra links and
the bytes they would have counted again (`hl_files`, `hl_bytes`, from index version 7), shown in the age map's
panel. *Find files* lists every link, with the file's size. Only hard-linked files are compared at build (a window
over files with more than one link), so a scan with few costs nothing: 2 ms at a million files without any.

## Marked folders

Cleanup plans (wave 7) and tags (wave 8) are about particular folders, which outlive any one scan, so the catalog
keeps them as **marked folders** (`marks.py`, `marked_folder`): a filesystem, a path as the index has it, and the
directory's device and inode, which every fs-inventory scan records and index version 6 keeps. With each scan of
the filesystem, ingest looks for every marked folder:

- at its path: it is *here*, and its totals for that scan are recorded (`marked_folder_stat`: size, space on disk,
  files, folders, cold bytes, mean age, and bytes per age bucket);
- else at another path with the same inode, since a rename or a move within the filesystem keeps it: it is
  offered as *moved*, for a person to confirm (the mark then follows it) or dismiss. It is not followed on its own,
  because a deleted directory's inode can be given to a new one;
- else it is *gone*.

Only the filesystem's latest scan changes where a mark is; an older scan found late adds to its history. A new
mark's history starts with the snapshots kept in full, and from then on it has every scan, so it outlasts the
indexes, as folder history does for top-level folders. An index before version 6 has no inodes, so its marked
folders are found by path only. At a million files (244,000 folders), finding 1,000 marked folders by path takes
25 ms, and 100 by inode 4 ms (a pass over every folder's inode); the inodes take under 1 MB of the 126 MB index.

### Cleanup plans

A lab's **cleanup plan** (`plans.py`, `cleanup_item`) lists folders it means to free space from, and how: delete
it, move it to another filesystem (whose price sets the saving), or compress it, with a note. Pinax does none of
it, and reads and changes nothing on the storage: a plan records what a lab means to do, the lab's people (or the
storage team, with the plan's CSV) do it, and later scans show what happened. A planned folder is a marked folder,
so it is followed across scans, and its size when it was added is kept. A lab's PI and delegates add to its plan
directly, as do administrators; the folder must be in a top-level folder the lab owns; every change is audited. A
folder inside another planned folder of the same lab is listed, but counted once, as part of the outermost.

**What a plan saves** is the fall in the lab's monthly charge, at today's prices and free tiers: on each filesystem,
the lab's whole size there (its folders in the latest scan) charged as charges are, before and after. A folder
deleted frees its size; one moved frees it and is charged on the filesystem it goes to; one compressed frees an
estimate, from the types in it and what compressing each typically frees (`plans.COMPRESSION`: about three quarters
of uncompressed FASTQ or SAM, half of BAM as CRAM, more of VCF and pileups). A lab within its free tier saves
nothing by freeing storage there, which is why the saving is not size times price. Each folder's saving on its own
is shown too; with free tiers, those need not add up to the plan's.

**Progress** comes from the scans after a folder was added: one gone (and not moved) is done; one compressed is
done once it has shrunk by nine tenths of its estimate (recorded when it was added); one smaller than when added
is partly done; one at another path with its inode freed nothing. What is done is shown with the scan that showed
it, and what it saves a month now: what the freed space would cost the lab if it were back, less what a moved folder
costs where it went. What a plan would still save counts only what is not done. A plan downloads as CSV, a row per
folder, for the lab or the storage team.

**Suggestions** for a lab's plan come from the latest scan of each of its folders: workflow working folders and
package caches (`work_dirs`, found at build: what running the workflow makes again), the largest cold folders at the
cold threshold (`cold_units`), and the folders with the most uncompressed sequencing data (`dir_ext`, estimated as
for compression). Each comes with what it would free and save a month on its own; a folder planned or inside one is
left out, a folder suggested twice is suggested for the larger saving, and one inside another suggested folder is
left to it. On a million-file synthetic scan, the three queries take 8, 38, and 9 ms for a top-level folder.

Administrators see **every lab's plan** (`/api/plans/all`, *Cleanup plans* without a lab chosen): what all of them
would free and save, and what is done; per filesystem, what is to be freed, moved in, and freed so far; the largest
folders still to do; each lab's plan; and one CSV of every folder, for the storage team.

## Tags

Folders are described with **tags** (`tags.py`, `tag` and `folder_tag`). Administrators keep the standard tags on
*Tags*: each has a type (a flag, text, a date, or a choice from the site's own list), says who may apply it (a
lab's PI and delegates, or administrators only), whether a folder may have more than one value, and whether it
protects a folder from cleanup plans (a flag, or a date until it). A default list is added the first time the
catalog is set up, and never again, so a site's changes stay: Project, Grant or award, Data stage, Source, Data
classification (administrators only), IRB protocol, Shared in a repository, Keep until, and Legal hold. A tag in use
is retired rather than deleted (folders keep it; it can no longer be applied), its type cannot change, and a choice
in use cannot be taken away. Every change is audited.

A tag is put on a folder (a marked folder, so it follows the folder across scans) and **applies to everything below
it**: a folder's tags are its own and those of the folders above it, which it cannot drop. A lab's PI and delegates
tag its folders with the standard tags applied by owners and with the lab's own tags (a flag or text, made by them,
seen by the lab's members and administrators); administrators tag any folder with any tag, and can fold a lab's tag
into a standard one from *Tags* (each folder gets the standard tag, with a value given or the lab tag's own, and the
lab's tag is retired; in one transaction, so a value the standard tag cannot take changes nothing). A tag with one value replaces the folder's value. The age map's panel shows a folder's tags and adds
them; tiles that are or hold tagged folders carry a dot, with their tags in the tooltip and the table view.
`pinax tags import FILE` tags folders in bulk from a CSV (filesystem, path, tag, value; a lab's own tag as
`lab:key`), as a dry run unless `--apply`, and changes nothing if any row is refused.

**Collections** find data by its tags (`tags.collection`, *Collections*): the folders that have every tag asked for
(each with a value, or any), on their own or from a folder above, that the person may see. The outermost of them
are the collection, each with its latest totals (recorded with each scan for marked folders), owner, tags, and
monthly cost (its share of its lab's charge on its filesystem, by size). The collection's history is those totals
on each day with a scan of one of its folders, from when they were marked, and its forecast is that history's
(forecast.py). Only the catalog is read, each table once: with 2,000 tagged folders of 200 labs and half a year of
weekly totals (`bench/bench_tags.py`), a collection of 34 folders takes 60–70 ms, of 100 folders 85–95 ms, and of
all 2,000 about 0.6 s.

**Charges by tag** (`charges.split_by_tag`, `/api/charges/split`) split a lab's month by a tag's values, such as by
grant: on each filesystem, each value gets the share of the lab's charge there that its folders' time-weighted
average size is of the lab's (each scan's totals holding until the next, as charges are worked out), and the rest is
untagged. A folder's bytes belong to its nearest tagged folder (itself, or the nearest above it), and a folder with
several values splits evenly between them. *Storage charges* shows the split for each lab, by the grant if its
folders have one, with a CSV.

**Protection.** A protecting tag (*Legal hold*; *Keep until*, before its date) keeps its folder, and everything below
it, out of cleanup plans (`tags.protection`): adding such a folder is refused, and so is adding a folder that holds
one, since cleaning it up would take the protected folder too. A folder planned before it was protected is flagged
on the plan; suggestions leave protected folders out.

## Saved views

A person saves the page they are on (`views.py`, `saved_view`): its address in the app (a report, its place, and
its settings, such as `/explore?fs=fast&path=...&size=files`), under a name, kept to themselves or shared with one of
their labs, whose members see it among their views on *My data* (*All data* for administrators). Only pages of
Pinax are kept, and a view is only an address, so whoever opens it sees only what they may. Saving, changing,
and deleting are audited; only whoever saved a view changes it.

## Costs

What storage costs is kept in the catalog as two schedules per filesystem (`costs.py`):

- **Prices** (`price`): an amount per month per GB, GiB, TB, or TiB, from a day on, until the next price. Amounts
  are integers in millionths of the currency (`PINAX_CURRENCY`), so money is exact; a filesystem has no price
  until its first, and its storage is then not charged.
- **Free tiers** (`free_tier`): storage free of charge, from a day on: one for every lab and one for every
  department, and exceptions for particular labs, which take the place of their kind's tier until they end (an
  exception row with no amount). Amounts are bytes, kept with the unit they were given in, so they show as entered.

Earlier prices and free tiers are kept, so past months keep what they had; a change to a day already past changes
the charges since then, and *Costs* says so before removing one. Every change is audited with what it replaced.
Charges are for the files' size (logical bytes, not space on disk).

**Monthly charges** (`charges.py`) are worked out from folder history, which keeps every scan's size for each
top-level folder. A month (a calendar month in UTC) is charged on its time-weighted average, like a utility bill:
each scan's sizes hold from that scan until the next. The month is cut wherever something changes (a scan, a
folder's owner, a price, a free tier), and in each stretch a lab's charge is

    max(0, size of the folders it owned then - its free tier then) x the price then x (stretch / month)

Everything is worked out exactly, as fractions, and rounded half up to the currency's minor unit (cents, or whole
units for currencies such as JPY) once per lab and filesystem, so a lab's total is the sum of its lines. Before a
filesystem's first scan there is nothing to charge; storage on a filesystem with no price yet is shown, not charged.
Folders no lab owns, and the files directly in the root, are unbilled. The month in progress is an estimate: the
latest scan holds until the month's end.

So that a past month is charged to the owners it had, the catalog keeps **ownership history** (`folder_owner`,
`ownership.py`): a row each time a top-level folder changes owner, in the app or by `pinax import`. The first change
of a folder also records who had it until then, from the beginning; a folder that has never changed is its current
owner's throughout. `/api/admin/costs/charges?month=YYYY-MM` returns every lab's and department's charges for a
month, by filesystem and folder, with the prices that applied, what is unbilled, and totals by filesystem; with
`fmt=csv`, the month's statement for finance, a row for each lab or department on each filesystem and one for each
filesystem's unbilled storage, marked final or estimate. *Chargeback* shows it.

Each line is also split into **periods**, runs of the month with one price and one free tier, so the charge can be
shown worked out: a period's charge is its average billable size times its price times its share of the month.
*Storage charges* (`/api/charges`) shows a person's own labs' lines this way, with each lab's last twelve months;
its members see it whatever their role, and administrators may ask for any lab. A lab is shown the folders it was
charged for, including ones it owned earlier in the month and has since given up, which it can no longer open.

**Charges ahead** (`charges.charges_ahead`) are each lab's and department's charges for the twelve calendar months
after this one, per filesystem: the size of the folders it owns now, at their trend (as History forecasts it), less
the free tier scheduled, at the price scheduled, for each stretch of each month in which neither changes. The
trend, and the low and high ends of its range, are straight lines, so each stretch is worked out exactly (the part
of the line above the free tier), with the low and high ends giving the range. A lab with no trend on a filesystem
(too few scans, or soon after a step) is counted there at its size in the latest scan, and says why. These are
estimates, in floating point, rounded to the currency's unit; they are not bills. *Storage charges* shows a lab's
next twelve months, and *Chargeback* every lab's added up (`/api/admin/costs/ahead`, asked for after the month, so
the month shows at once; adding up the ranges overstates the range); storage no one owns is not counted. With
2,000 folders and 200 labs, one lab's twelve months take about 15 ms, and every lab's about 100 ms.

## Security

- **Identity**, four ways (`PINAX_AUTH_MODE`):
  - `demo`: a persona cookie, anyone can be anyone, for showing Pinax; the default in the demo environment, and
    refused at start-up with `PINAX_ENVIRONMENT=prod`.
  - `local` (`signin.py`), the default everywhere else: passwords hashed with scrypt; sessions kept on the server
    (the cookie holds a random token, the catalog only its SHA-256), HttpOnly, SameSite=Lax, Secure in production,
    ended by signing out, expiry, deactivation, or a password set or changed; a temporary password, made up by Pinax
    and shown once, must be changed before anything else; five failures in a row lock an account for 15 minutes,
    with the same answer whatever went wrong; requests that change something must come from a page of Pinax (their
    `Origin`).
  - `header`: trusts a username header set by a single sign-on proxy, as Tundra does, and only on requests that
    come straight from the proxy's address (`PINAX_TRUSTED_PROXIES`); the same header from anywhere else is
    refused. `scripts/sso-check.py` checks this behind a real oauth2-proxy.
  - `oidc` (`oidc.py`): sign-in with Microsoft Entra ID, or another OpenID Connect provider, in Pinax itself, as
    Tundra does it, with no sign-on proxy. Authlib finds the provider by its discovery document; the authorization
    code flow with PKCE; the ID token checked against the provider's published keys, with its issuer, audience,
    expiry, and nonce. The sign-in's own state is a cookie signed with `PINAX_SESSION_SECRET`, on `/auth/` only,
    for ten minutes. The person is matched by one claim (`PINAX_OIDC_USERNAME_CLAIM`), within
    `PINAX_OIDC_USERNAME_DOMAINS`, never made, and then gets a server-side session as with `local`; refusals are
    audited with why. Pinax refuses to start until its settings are complete. `scripts/dex/dex.sh` runs Dex as a
    stand-in for Entra ID.
  - With `oidc` and a role map (`PINAX_OIDC_ROLE_MAP`), `people.roles_from_provider` sets each role the map names
    (the administrator role among them) from the ID token's `roles` (app roles) or `groups` claim at every sign-in,
    audited with `source: oidc`; `people.save_person` leaves those roles as they are when the change comes from the
    app, and the command line can still set any. With too many groups to list, mapped roles are taken away, not
    guessed (`auth.roles_unknown`). With any other sign-in, the map is ignored.
  - With `oidc`, the emergency sign-in (`/auth/emergency`, A12): a password form for storage administrators marked
    `user.emergency` from the command line only (`pinax user emergency`, which makes a 32-character password), for
    when the provider fails. It answers 404 unless one exists; `signin.sign_in(emergency=True)` keeps local
    sign-in's lockout, gives an hour's session, audits `auth.emergency_signed_in` and `_failed`, and logs each as a
    warning, which the alert rule `PinaxEmergencySignIn` watches through `pinax_audit_events_total`.
- **API tokens** (`tokens.py`), with every way of signing in: `Authorization: Bearer pinax_…`, 256 random bits, of
  which the catalog keeps only the SHA-256 and the first characters. A personal token takes its person's access as
  it is at each request, or reads only (GET, and starting a search); a service token belongs to no one and reads as
  an auditor or as finance. Every token expires; it ends sooner when revoked, or when its person is deactivated.
  No token makes tokens or changes a password. Behind a sign-on proxy, `/script/api/...` is `/api/...` answered
  only with a token (`auth.ScriptPrefix`), which the proxy lets through without signing in. A change made with a
  token is audited with its name (a SQLAlchemy event on the session, as for logging audit events).
- **Authorization is on the server, for every request.** A request names a filesystem and path; Pinax checks
  that the path is canonical (no `.`, `..`, or empty components) and inside the filesystem root, takes its
  top-level folder, and allows it only for those who see everything or who see the lab that owns that folder.
  Otherwise it answers 404, so the existence of other labs' folders is not revealed.
- **Seeing and changing are separate** (`auth.Identity`). Storage administrators and auditors see everything
  (`sees_all`), and finance every lab's charges and the prices behind them (`sees_charges`); a lab's members, and
  the heads of its division (a site role, `user_role`), see its folders. Only storage administrators change the
  site's settings (`require_admin`), and only a lab's PI and delegates, or an administrator, tag its folders or plan
  its cleanup. Reads an administrator once made alone take `require_sees_all` or `require_sees_charges`.
- **The audit log** (`audit.py`, *Audit log*), for those who see everything: found by person (by or about them),
  action or kind, lab (by name, and by the paths of its folders), folder (by path prefix, with `LIKE` wildcards
  escaped, and the mapping of its top-level folder), and day; paged, or streamed as CSV from a session of its own.
- **The access review** (`review.py`), for those who see everything: site roles, labs, and people as they are
  now, with when each piece of access was given (`user.admin_since` and `lab_membership.granted_at`, set by
  SQLAlchemy attribute events, so every way of giving access records it; `user_role.granted_at`), and a sign-off
  audited as `access.reviewed`.
- File queries read only the Parquet files of the top-level folders in scope.
- People, labs, memberships, and folder ownership use the same model as Tundra (`Lab`, `LabFolder`,
  `User`, `LabMembership`); changes are audited. People are never deleted: a deactivated person cannot sign in,
  and keeps their memberships and roles, unused, until reactivated. At least one active storage
  administrator always remains, and nobody can remove their own administrator role or deactivate themselves
  (`people.py`, shared by *People and access* and `pinax import`).
- Pinax never reads file contents or writes to the file system it reports on; it reads scan output.
- **Logs** (`logs.py`): one line for each request to the API, with a request ID (returned as `X-Request-ID`), the
  person, and their address (behind a trusted proxy, from `X-Forwarded-For`), and every audit event once its change
  is committed (a SQLAlchemy event on the session, so every place that audits is covered), optionally to a file of
  its own as JSON lines. Another such event records each audit event's address, and its API token's name. Never a password, session token, API token, request body, or query string; the catalog's address is
  logged without its password.
- **Metrics** (`metrics.py`, `/metrics`): off until `PINAX_METRICS_TOKEN` is set, then only with it (compared in
  constant time). They name filesystems, never labs, people, or paths. Figures from the catalog are worked out when
  Prometheus asks, at most every 15 seconds, so they count whichever process did the work; the web app's own
  (requests, index queries, stopped queries, audit events) are counted as it runs, on a registry of Pinax's own.

## Measured performance

On an Apple M4 laptop (10 cores, 32 GB), with synthetic scans from `bench/synth_scan.py` that model a research
file system (lab folders, projects of different ages, a realistic mix of file types and sizes), and the default
8 GB build memory:

| Scan | Index build | Index size | Treemap (root, 2 levels) | Treemap (lab folder) | Types (lab) | Cold folders (lab) | Name search (lab) | Name search (whole file system) |
|---|---|---|---|---|---|---|---|---|
| 1 M files, 24 K folders | 3.4 s | 0.06 GB | 26 ms | 23 ms | 1 ms | 11 ms | 9 ms | 30 ms |
| 10 M files, 244 K folders | 22 s | 0.54 GB | 64 ms | 42 ms | 1 ms | 20 ms | 10 ms | 273 ms |
| 12.2 M files, 445 K folders, with wide and deep folders | 31 s | 0.59 GB | 49 ms | 35 ms | 1 ms | 14 ms | 9 ms | 304 ms |
| 100 M files, 2.44 M folders, 2,000 top-level folders | 121 s | 5.56 GB | 49 ms | 30 ms | 1 ms | 13 ms | 22 ms | 2.8 s |

The first two rows were measured with 0.1.0; treemaps of wide folders are faster since. The third scan adds a
folder of two million files, a folder of 200,000 subfolders, and a chain 1,000 folders deep (`--wide 2000000
--deep 1000`): the treemap of the folder with 200,000 subfolders takes 45 ms, the deepest folder 6 ms, and the
largest files in the two-million-file folder 33 ms; 10 of the 31 seconds of the build are the 1,000 levels of
subtree totals. At 100 million files every directory-level query takes 50 ms or less (whole-filesystem cold
folders 21 ms), and every directory is equal to `fs-inventory rollup` (rollup 11 s, compare 1 s).

The 100-million-file build peaks at 7.7 GB within the 8 GB limit, reading the scan with 6 of 10 threads. With
17 GB and all ten threads it took 133 s instead of 121 s and peaked at 10.5 GB: staging the scan's files does not
speed up with more threads, so more memory buys nothing here.

Build time is about 1.2 seconds per million files, so a billion-file scan would take about 20 minutes on this
laptop, and less on a server with more cores and faster disks; that is small next to the crawl itself.
Directory-level queries depend on the number of directories in the range they read, not on file counts, so
they stay in the tens of milliseconds. Searches across a whole file system scan its files' name column, so they
grow linearly: 2.8 seconds at 100 million files, about 30 seconds at a billion, and about a minute at two billion,
with about as long again for the count of matches (which *Find files* asks for separately). Scoped to a lab folder
they stay fast. The new and removed files of *What changed* against the previous scan grow with what changed, not
with the filesystem; against an older scan, a whole filesystem's comparison grows with its files (0.4 s at a
million, so minutes at two billion, beyond the time limit), and is best asked of a folder. These are projections
from synthetic data, which has about 41 files per folder; production filesystems often have fewer, so two billion
files may mean 50 to 200 million folders, beyond anything measured so far. Reports that read every folder of a
whole filesystem, like the folder lists of *What changed* (about 0.1 s per 250,000 folders here) and the icicle
and sunburst of a filesystem's root (about 2 ms per million folders), grow with them. The pilot (wave 15) measures
production storage.

Index size is about 56 bytes per file, mostly the per-file Parquet (which is kept sorted and compressed). A
billion-file snapshot needs about 56 GB; keeping six snapshots per file system (the default) needs about
330 GB. `file_changes` adds about 60 bytes per file new or removed since the previous scan: a week in which 2% of
two billion files are new or removed adds about 2.4 GB. Folder history in the catalog is tiny.

Reproduce with:

```bash
uv run python bench/synth_scan.py --files 10000000 -o /scratch/synth-10m
```

### Many people at once

Measured with the load test ([The load test](DEVELOPMENT.md#the-load-test)) on the same laptop, with Pinax and the
load test sharing it, against three scans of 10 million files a month apart, 200 labs, and 615 people. Each person
pauses 5 to 15 seconds between pages, so 100 people open about 9.5 pages a second; five minutes measured after a
minute's ramp-up:

| People at once | Catalog | Pages a second | Median page | 95th percentile | 99th percentile | Errors | Memory at most |
|---|---|---|---|---|---|---|---|
| 10 | SQLite | 0.97 | 41 ms | 98 ms | 154 ms | 0 | 1.2 GB |
| 25 | SQLite | 2.39 | 42 ms | 93 ms | 160 ms | 0 | 1.3 GB |
| 50 | SQLite | 4.73 | 40 ms | 96 ms | 158 ms | 0 | 1.4 GB |
| 100 | SQLite | 9.48 | 38 ms | 106 ms | 168 ms | 0 | 2.5 GB |
| 10 | PostgreSQL | 0.96 | 66 ms | 140 ms | 382 ms | 0 | 1.1 GB |
| 25 | PostgreSQL | 2.38 | 64 ms | 174 ms | 385 ms | 0 | 1.4 GB |
| 50 | PostgreSQL | 4.72 | 60 ms | 162 ms | 387 ms | 0 | 1.5 GB |
| 100 | PostgreSQL | 9.47 | 55 ms | 146 ms | 377 ms | 0 | 2.3 GB |

Times do not grow from 10 to 100 people, and the server keeps about one core busy. Folder pages stay within
143 ms at the 95th percentile (172 ms on PostgreSQL) and a lab's searches within 65 ms (85 ms), far inside wave 14's
budgets of 1 and 2 seconds. A search of every filesystem takes about 0.4 s, and *All data* 157 ms (385 ms on
PostgreSQL).

**More than that, as measured with 0.13.0.** The same 100 people clicking twice as often (19 pages a second) were as
fast; three times as often (28 pages a second), the 99th percentile reached 0.9 s, with 3 errors; four and five
times, Pinax stopped answering for 30 seconds at a time and failed requests with *Internal error*, its processors
idle. A request takes a catalog connection to check who is signed in and keeps it while it waits for one of the web
server's 40 threads to run the page itself; the catalog has 15 connections on SQLite (20 on PostgreSQL), so once 40
requests are waiting for a connection, the ones holding the connections wait for a thread, until the pool gives up
after 30 seconds. So 0.13.0 serves about 20 pages a second, some 200 people at once at this pace; wave 14 fixes this.

**PostgreSQL is slower by its round trips,** on pages that ask the catalog many small questions: an administrator's
*All data* makes 1,023 catalog queries (306 ms on PostgreSQL, 111 ms on SQLite), *People and access* 620 (one for
each person's site roles; 187 and 66 ms), a lab's suggested folders 175 (91 and 46 ms), and a lab's charges 51 (56
and 10 ms). They grow with labs and people.

**Memory** grows as DuckDB caches the indexes' blocks: 2.5 GB after five minutes of 100 people, and over 20 minutes
it rose and fell between 1.4 and 3.0 GB. Each open index may cache up to `PINAX_QUERY_MEMORY_LIMIT` (4 GB) and up to
eight indexes stay open, so at a billion files the bound is past the 16 GB of [Sizing](DEPLOYMENT.md#sizing).

```bash
uv run python bench/bench_index.py /scratch/synth-10m --index /scratch/synth-10m-index
```

### Charges

Monthly charges come from the catalog, not the indexes, so they depend on the number of top-level folders and
scans. On the same laptop, with a synthetic year of weekly scans (`bench/bench_charges.py`: a tenth of the
folders unowned, one in a hundred changing owner, a price change, and free tiers with exceptions):

| Folders, labs | Every lab, a past month | Every lab, this month | One lab, a month | One lab, twelve months |
|---|---|---|---|---|
| 2,000, 200 | 25 ms | 24 ms | 4 ms | 49 ms |
| 20,000, 2,000 | 488 ms | 521 ms | 42 ms | 566 ms |

*Chargeback* and its statement are the first column; a lab's *Storage charges* is the last, and *My data* the one
before. Time grows with folders times the scans in a month; one lab reads only the folders it ever had, but still
every folder's owner.

History with its forecast, on 2,000 folders and a year of weekly scans: 4.7 ms for a lab and 1.6 ms for all data
(the whole filesystem's totals recorded with each scan). The forecast itself works on at most the six months of
points the history already has, a few hundred pairs of scans.

```bash
uv run python bench/bench_charges.py --folders 2000 --weeks 52
```

## Proof of concept and production

| Proof of concept | Production | Notes |
|---|---|---|
| `pinax seed` writes demo trees of sparse files and scans them with fs-inventory | Scheduled fs-inventory scans on storage nodes or scan hosts | Pinax ingests any complete Parquet scan |
| Demo owners rewritten to fictional accounts, and demo scans dated a month apart | Real owners and times from the scan | Only `seed.py` rewrites scans, only its own demo scans |
| SQLite catalog | SQLite on one server, or PostgreSQL | Migrations upgrade either in place ([The catalog database](#the-catalog-database)) |
| Demo identity switcher | Passwords Pinax keeps (`local`); sign-in with Entra ID in Pinax itself (`oidc`); or single sign-on through a proxy (`header`) | `oidc` checked against Dex and a stand-in provider; `header` behind oauth2-proxy 7.15 with a stand-in identity provider |
| People, labs, and folder mappings managed in the app | The same, fed from the authoritative source; until then imported from CSV (`pinax import`) | Same model as Tundra |
| One server | Web and ingest can run on separate hosts sharing `var/snapshots` | Indexes are replaced, never changed in place; keep every ingest on one host, since the ingest lock is a file lock in `PINAX_VAR_ROOT` |

## Known limits

- **Modification time** is what copies and restores usually preserve; a folder copied last week from old data
  still looks old. That is usually what people want, and the interface says which time each view uses.
- **Access time** depends on mount options; many storage systems update it rarely or never.
- One root per filesystem; scans with several roots are refused.
- **A lab's kind** (lab or department) has no history: charges use the kind it has now, so changing it changes
  which free tier its past months had too. *People and access* says so before the change.
