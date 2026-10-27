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
                                                     │     files/tNNNNNN/  per-file Parquet, by     │
                                                     │                     top-level folder         │
                                                     │                                              │
                                                     │ catalog (SQLite; PostgreSQL in production)   │
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
| Crawler | fs-inventory 1.0.0 | Built for this: parallel, bounded memory, resumable, lossless names, about 25 bytes per entry in Parquet |
| Index engine | DuckDB (embedded) | Columnar, vectorized, spills to disk, reads Parquet natively; no server to run |
| Per-file store | Parquet, split by top-level folder and sorted by path | Compact; a lab's queries read only its files; a subfolder is a contiguous range |
| Catalog | SQLAlchemy on SQLite (PostgreSQL in production) | Small, transactional data: who owns what, which snapshots exist, folder history |
| Web | FastAPI and plain JavaScript modules | Same stack and patterns as Tundra; nothing to build |

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
| `dirs` | directory | Subtree totals: files, folders, bytes, allocated bytes; files and bytes per age bucket by modification time; bytes per age bucket by access time; files and bytes per category; oldest and newest modification, newest access and change; size-weighted time sums for mean ages; count of folders not read completely. Plus the directory's own files (count, bytes, times) and its entry count and any scan error. | `(depth, path)` |
| `dir_ext` | directory and file type | Totals over the directory's *own* files of that type | `path` |
| `dir_owner` | directory and owner (uid) | Totals over the directory's *own* files of that owner | `path` |
| `top_ext`, `top_owner` | top-level folder and type / owner | The same, summed per top-level folder | `top_id` |
| `tops`, `file_parts` | top-level folder | Name, path, ID; file count and bytes | |
| `users`, `groups` | uid / gid | Names from the scan (`--resolve-ids`); a missing name is a deleted account | |
| `errors`, `name_flags` | scan error / top-level folder and flag | Errors with their top-level folder; counts of problem names | |
| `meta` | key | Scan ID, root, reference time, the age buckets and categories used, totals | |
| `files/tNNNNNN/part-*.parquet` | non-directory entry | Path, name, normalized type, category, kind, size, allocated bytes, owner, group, times, links, name flags | `path` within each top-level folder |

### Why the sort orders matter

DuckDB keeps minimum and maximum values for every block of rows (zone maps), and skips blocks that cannot
match. The sort orders turn every query Pinax makes into a contiguous read:

- **Children of a folder** `P` at depth `d` are exactly the rows of `dirs` with `depth = d + 1` and
  `path` in `[P/, P0)` (in byte order `0` follows `/`). Because `dirs` is sorted by `(depth, path)`, those
  rows are adjacent. Grandchildren are the same at `d + 2`.
- **A subtree's type or owner breakdown** sums the rows of `dir_ext` or `dir_owner` with `path = P` or `path`
  in `[P/, P0)`, which are adjacent because those tables are sorted by `path`. Top-level folders and the
  whole filesystem use the pre-summed `top_ext` and `top_owner`.
- **Files below a folder** live in that top-level folder's own Parquet files, sorted by path, so a
  subfolder is a range of row groups and the rest are skipped. Nobody's query ever opens another lab's files.

### File types and categories

A file's type is its lowercased extension, joined with the one before when the last is a compression
suffix: `S1_R1.fastq.gz` is `fastq.gz`, `brain.nii.gz` is `nii.gz`. Its category comes from the inner
type, so compressed sequencing data is still sequencing data. There are twelve categories (sequencing and
genomics, imaging, tabular and array data, mass spectrometry, archives, models, code, documents, media,
containers, logs and temporary, other) defined in `categories.py`. Seven age buckets run from under 90 days
to ten years or more. Both lists are recorded in each snapshot, so changing them affects only new snapshots.

## Building the index

`ingest/build.py`, in DuckDB, writing to a temporary folder that is renamed into place when complete:

1. **Top-level folders**: the depth-1 directories of the scan.
2. **Classify and stage**: one pass over the scan's entries computes each file's top-level folder, type,
   and category, and writes them partitioned by top-level folder.
3. **Per top-level folder**, one at a time: sort its files by path into `files/tNNNNNN/`, then aggregate
   them by parent directory (totals, age buckets, categories, time sums), by directory and type, and by
   directory and owner. Working one folder at a time bounds memory by the largest folder, not the file system,
   and DuckDB spills to disk beyond `PINAX_BUILD_MEMORY_LIMIT`.
4. **Subtree totals, bottom-up one level at a time**: starting from the deepest level, each directory's
   totals are its own files plus the sums of its children's totals (minimums and maximums for times).
   Each level reads only its own rows and the level below, so the whole tree costs one pass, however deep.
5. **Write** `dirs`, `dir_ext`, `dir_owner`, and the per-folder summaries in their sort orders, plus users,
   groups, errors, and metadata.

Then ingest records the snapshot and one `FolderStat` row per top-level folder in the catalog: those rows are
the durable history (growth, cold share, mean age per scan) and they survive when old indexes are pruned.

Only scans whose manifest says `complete` are accepted (`--allow-incomplete` also accepts `incomplete`, where
stalled directories are missing). Directories the scanner could not read are counted in `incomplete`, so the
interface can say when totals are lower bounds.

## Querying

`index.py` opens each snapshot's `index.duckdb` read-only and gives every request its own cursor. All
values from requests are bound as parameters; the only SQL fragments built in code come from fixed lists
(the size metrics, the sort orders). Recently used indexes stay open (`IndexCache`).

- **Treemap** (`/api/tree`): the node, its largest 60 children by the chosen size metric, and for two levels
  each child's largest 12 children. The rest are summed into one "smaller folders" tile; a folder's own files
  are a "files in this folder" tile, computed as its subtree minus all of its subfolders.
- **Types and owners**: range sums over `dir_ext` / `dir_owner`, or the pre-summed per-folder tables.
- **Cold folders**: directories whose newest file is older than the threshold while their parent has a newer
  change: each is a self-contained cold unit (folders inside it are not listed again).
- **Files**: the top-level folder's Parquet files, restricted to the subtree's path range, filtered and sorted.

A request without a filesystem and path covers the person's *home*: each of their labs' folders (which may be
on several filesystems), or every filesystem root for an administrator. Results from each place are merged;
mean ages are rebased to a common reference time.

## Security

- **Identity**: demo mode uses a persona cookie; production trusts a username header set by a single sign-on
  proxy (`PINAX_AUTH_MODE=header`), exactly as Tundra does.
- **Authorization is on the server, for every request.** A request names a filesystem and path; Pinax checks
  that the path is canonical (no `.`, `..`, or empty components) and inside the filesystem root, takes its
  top-level folder, and allows it only for administrators or members of the lab that owns that folder.
  Otherwise it answers 404, so the existence of other labs' folders is not revealed.
- File queries read only the Parquet files of the top-level folders in scope.
- People, labs, memberships, and folder ownership use the same model as Tundra (`Lab`, `LabFolder`,
  `User`, `LabMembership`); changes are audited.
- Pinax never reads file contents or writes to the file system it reports on; it reads scan output.

## Measured performance

On an Apple M4 laptop (10 cores, 32 GB), with synthetic scans from `bench/synth_scan.py` that model a research
file system (200 lab folders, projects of different ages, a realistic mix of file types and sizes):

| Scan | Index build | Index size | Treemap (root, 2 levels) | Treemap (lab folder) | Types (lab) | Cold folders (lab) | Name search (lab) | Name search (whole file system) |
|---|---|---|---|---|---|---|---|---|
| 1 M files, 24 K folders | 3.4 s | 0.06 GB | 26 ms | 23 ms | 1 ms | 11 ms | 9 ms | 30 ms |
| 10 M files, 244 K folders | 22 s | 0.54 GB | 64 ms | 42 ms | 1 ms | 20 ms | 10 ms | 273 ms |

Build time is about 2.2 seconds per million files once fixed costs are amortized, so a billion-file scan
would take on the order of 40 minutes on this laptop, and less on a server with more cores and faster disks.
That is small next to the crawl itself. Directory-level queries depend on the number of directories in the
range they read, not on file counts, so they stay in the tens of milliseconds for lab folders. Searches across
a whole file system scan its files' name column, so they grow linearly: roughly 25 seconds at a billion files.
Scoped to a lab folder they stay fast.

Index size is about 55 bytes per file, mostly the per-file Parquet (which is kept sorted and compressed). A
billion-file snapshot needs about 55 GB; keeping six snapshots per file system (the default) needs about
330 GB. Folder history in the catalog is tiny.

Reproduce with:

```bash
uv run python bench/synth_scan.py --files 10000000 -o /scratch/synth-10m
```

```bash
uv run python bench/bench_index.py /scratch/synth-10m --index /scratch/synth-10m-index
```

## Proof of concept and production

| Proof of concept | Production | Notes |
|---|---|---|
| `pinax seed` writes demo trees of sparse files and scans them with fs-inventory | Scheduled fs-inventory scans on storage nodes or scan hosts | Pinax ingests any complete Parquet scan |
| Demo owners rewritten to fictional accounts after each demo scan | Real owners from the scan | Only `seed.py` rewrites owners, only in its own demo scans |
| SQLite catalog | PostgreSQL | SQLAlchemy models; migrations (Alembic) still to add |
| Demo identity switcher | Single sign-on proxy, `PINAX_AUTH_MODE=header` | |
| People, labs, and folder mappings managed in the app | The same, fed from the authoritative source | Same model as Tundra |
| One server | Web and ingest can run on separate hosts sharing `var/snapshots` | Indexes are immutable once built |

## Known limits

- **Hard links** are counted once per path, so a file with several links counts several times. The scan has
  `ino` and `nlink`, so de-duplicated totals are possible later.
- **Modification time** is what copies and restores usually preserve; a folder copied last week from old data
  still looks old. That is usually what people want, and the interface says which time each view uses.
- **Access time** depends on mount options; many storage systems update it rarely or never.
- One root per filesystem; scans with several roots are refused.
- Whole-filesystem cold-folder reports read every directory row; for very large file systems they take
  seconds. Precomputing cold units per threshold at build time would make them instant (see the roadmap).
