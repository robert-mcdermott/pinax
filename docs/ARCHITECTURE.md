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
   Storage nodes / scan hosts                        Pinax server
   ┌──────────────────────────────┐                  ┌──────────────────────────────────────────────┐
   │ fs-inventory scan  (Rust,    │   scan folder    │ pinax ingest  (DuckDB)                       │
   │ parallel, resumable, read-   │ ───────────────► │   scan -> snapshot index                     │
   │ only) -> Parquet + manifest  │  (shared path)   │   var/snapshots/<fs>/<scan>/                 │
   └──────────────────────────────┘                  │     index.duckdb    directory-level tables   │
                                                     │     files/gNNNNNN/  per-file Parquet, by     │
                                                     │                     group of top folders     │
                                                     │                                              │
                                                     │ catalog (SQLite; PostgreSQL in wave 10)      │
                                                     │   people, labs, folder ownership,            │
                                                     │   filesystems, snapshots, folder history     │
                                                     │                                              │
                                                     │ FastAPI  /api  ── read-only DuckDB cursors   │
                                                     │ static UI (HTML, CSS, JS modules; no build)  │
                                                     └──────────────────────────────────────────────┘
                                                                   ▲ HTTPS (SSO proxy in front)
                                                         PIs, lab members, IT
```

| Piece | Choice | Why |
|---|---|---|
| Crawler | fs-inventory 1.0.0 | Built for this: parallel, bounded memory, resumable, lossless names, about 40 bytes per entry in Parquet (measured at 100 million files) |
| Index engine | DuckDB (embedded) | Columnar, vectorized, spills to disk, reads Parquet natively; no server to run |
| Per-file store | Parquet, split by top-level folder and sorted by path | Compact; a lab's queries read only its files; a subfolder is a contiguous range |
| Catalog | SQLAlchemy on SQLite; PostgreSQL planned (wave 10) | Small, transactional data: who owns what, which snapshots exist, folder history, prices, the audit log |
| Web | FastAPI and plain JavaScript modules | Same stack and patterns as Tundra; nothing to build |

## What Pinax keeps

Pinax keeps three kinds of data. Only the catalog cannot be made again, so it is the one to back up.

| | Where | What it holds | Size | If it is lost |
|---|---|---|---|---|
| **Catalog** | `PINAX_VAR_ROOT/pinax.db` (SQLite) | Filesystems; a record of every scan; folder history (each top-level folder's totals for every scan); people, labs and departments, memberships, folder ownership and who owned each folder when; local sign-in password hashes and sessions; prices and free tiers; the audit log; the ingest watcher's heartbeat | Small: about one row per top-level folder per scan (2,000 folders scanned weekly add about 100,000 rows a year) | Restore it from a backup. Without one, register the filesystems again, import people, labs, and folder ownership from the import files, and ingest the kept scans again; prices and free tiers, ownership history, folder history of scans no longer kept, and the audit log are lost |
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
(mode 0750), and backups and copies need the same protection. In the app, only storage administrators see across
labs.

### The catalog database

The catalog is SQLite in WAL mode, a file opened by the Pinax processes themselves, with no database server to
run. Many readers work at once; ingests take turns under a lock in `PINAX_VAR_ROOT`. That suits one server running
the web app and the watcher, as [DEPLOYMENT.md](DEPLOYMENT.md) installs it.

The catalog has no migrations yet: each release creates new tables and adds new columns (each nullable or with a
default) at start-up, so an older catalog keeps working, but a change to an existing column or table cannot be
applied to it. Wave 10 moves the catalog to PostgreSQL with migrations, for three reasons: schema changes of any
kind can then upgrade a production catalog in place; the web app and ingest can run on more than one machine
against one catalog; and IT's usual database backups, replication, and monitoring apply. The indexes stay
DuckDB and Parquet files. Until then, `PINAX_DATABASE_URL` can point at PostgreSQL for a fresh catalog (with the
`psycopg` driver added), untested and without migrations; production uses SQLite.

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
| `dirs` | directory | Subtree totals: files, folders, bytes, allocated bytes; files and bytes per age bucket by modification time; bytes per age bucket by access time; files and bytes per category; oldest and newest modification, newest access and change; size-weighted time sums for mean ages; count of folders not read completely. Plus the directory's own files (count, bytes, times), its entry count and any scan error, and its device and inode (from index version 6). | `(depth, path)` |
| `dir_ext` | directory and file type | Totals over the directory's *own* files of that type | `path` |
| `dir_owner` | directory and owner (uid) | Totals over the directory's *own* files of that owner | `path` |
| `top_ext`, `top_owner` | top-level folder and type / owner | The same, summed per top-level folder | `top_id` |
| `changes` | directory of this scan or the filesystem's previous one | Both scans' subtree totals (files, folders, bytes, allocated bytes, size-weighted time sum, files and bytes per age bucket), each scan's own ages, and whether the directory is in each: what changed, new and removed folders included. Only when there was a previous scan | `(depth, path)` |
| `file_changes` | file new since the previous scan, or removed since | `change` (`new` or `removed`), then the file's path, name, type, category, kind, size, allocated bytes, owner, group, times, links, and name flags (a removed file's from the previous scan). Only when there was a previous scan, from index version 5 | `(change, path)` |
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
link to the entries, so the scan itself is never written to), with Pinax's age bucket edges
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
for server-class hardware (wave 9).

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
storage, with many owners and a billion files, is measured in wave 9.

## Querying

`index.py` opens each snapshot's `index.duckdb` read-only and gives every request its own cursor. All
values from requests are bound as parameters; the only SQL fragments built in code come from fixed lists
(the size metrics, the sort orders). Recently used indexes stay open (`IndexCache`).

- **Treemap** (`/api/tree`): the node, its largest 60 children by the chosen size metric, and for two levels
  each child's largest 12 children. The rest are summed into one "smaller folders" tile; a folder's own files
  are a "files in this folder" tile, computed as its subtree minus all of its subfolders.
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

- **Identity**, three ways (`PINAX_AUTH_MODE`):
  - `demo`: a persona cookie, anyone can be anyone, for showing Pinax; refused at start-up with
    `PINAX_ENVIRONMENT=prod`.
  - `local` (`signin.py`): passwords hashed with scrypt; sessions kept on the server (the cookie holds a random
    token, the catalog only its SHA-256), HttpOnly, SameSite=Lax, Secure in production, ended by signing out,
    expiry, deactivation, or a password set or changed; a temporary password, made up by Pinax and shown once,
    must be changed before anything else; five failures in a row lock an account for 15 minutes, with the same
    answer whatever went wrong; requests that change something must come from a page of Pinax (their `Origin`).
  - `header`: trusts a username header set by a single sign-on proxy, as Tundra does, and only on requests that
    come straight from the proxy's address (`PINAX_TRUSTED_PROXIES`); the same header from anywhere else is
    refused. `scripts/sso-check.py` checks this behind a real oauth2-proxy.
- **Authorization is on the server, for every request.** A request names a filesystem and path; Pinax checks
  that the path is canonical (no `.`, `..`, or empty components) and inside the filesystem root, takes its
  top-level folder, and allows it only for administrators or members of the lab that owns that folder.
  Otherwise it answers 404, so the existence of other labs' folders is not revealed.
- File queries read only the Parquet files of the top-level folders in scope.
- People, labs, memberships, and folder ownership use the same model as Tundra (`Lab`, `LabFolder`,
  `User`, `LabMembership`); changes are audited. People are never deleted: a deactivated person cannot sign in,
  and keeps their memberships and administrator role, unused, until reactivated. At least one active storage
  administrator always remains, and nobody can remove their own administrator role or deactivate themselves
  (`people.py`, shared by *People and access* and `pinax import`).
- Pinax never reads file contents or writes to the file system it reports on; it reads scan output.

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
whole filesystem, like the folder lists of *What changed* (about 0.1 s per 250,000 folders here), grow with them.
Wave 9 measures production storage.

Index size is about 56 bytes per file, mostly the per-file Parquet (which is kept sorted and compressed). A
billion-file snapshot needs about 56 GB; keeping six snapshots per file system (the default) needs about
330 GB. `file_changes` adds about 60 bytes per file new or removed since the previous scan: a week in which 2% of
two billion files are new or removed adds about 2.4 GB. Folder history in the catalog is tiny.

Reproduce with:

```bash
uv run python bench/synth_scan.py --files 10000000 -o /scratch/synth-10m
```

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
| SQLite catalog | SQLite on one server; PostgreSQL in wave 10 | SQLAlchemy models; migrations still to add ([The catalog database](#the-catalog-database)) |
| Demo identity switcher | Single sign-on proxy, `PINAX_AUTH_MODE=header` | Checked behind oauth2-proxy 7.15 with a stand-in identity provider |
| People, labs, and folder mappings managed in the app | The same, fed from the authoritative source; until then imported from CSV (`pinax import`) | Same model as Tundra |
| One server | Web and ingest can run on separate hosts sharing `var/snapshots` | Indexes are replaced, never changed in place; keep every ingest on one host, since the ingest lock is a file lock in `PINAX_VAR_ROOT` |

## Known limits

- **Hard links** are counted once per path, so a file with several links counts several times. The scan has
  `ino` and `nlink`, so de-duplicated totals are possible later.
- **Modification time** is what copies and restores usually preserve; a folder copied last week from old data
  still looks old. That is usually what people want, and the interface says which time each view uses.
- **Access time** depends on mount options; many storage systems update it rarely or never.
- One root per filesystem; scans with several roots are refused.
- **A lab's kind** (lab or department) has no history: charges use the kind it has now, so changing it changes
  which free tier its past months had too. *People and access* says so before the change.
