# Pinax roadmap

The plan for building Pinax. Each **wave** is one release: wave 1 is 0.1.0, wave 2 is 0.2.0, and so on.
Each wave is built on its own branch (`wave2`), one commit per **slice**, and merged into `main` with a pull
request when the wave is complete; the merge is tagged and released. A slice is a bite-sized unit of work,
and ends with:
- something that runs;
- tests or validation that check it;
- its documentation;
- an entry in `CHANGELOG.md`.

Slices are done in order within a wave unless noted. Each slice names the requirements (`requirements.md`)
it serves. When a wave is reached, it is planned in detail: its slices are confirmed or re-cut, and the
decisions taken are recorded here. Later waves are outlined at the end.

**Progress** is marked here as each slice is committed: done, next or to come. Each requirement's status is
in `requirements.md`; release changes are in `CHANGELOG.md`.

**Where things stand:** wave 1 is released as 0.1.0 and wave 2 as 0.2.0 (tags `v0.1.0` and `v0.2.0` on `main`).
Wave 3, change over time, is done on `wave3` as 0.3.0, for merging to `main` and tagging `v0.3.0`. The production
pilot, which needs production storage, is next, as wave 4.

The order follows one principle:
1. **Trust first.** Totals that agree with an independent computation on every directory, and that say when
   they are lower bounds.
2. **Then real storage at real scale.** First everything a production deployment needs, checked on real local
   scans and on synthetic data at scale (wave 2); then production storage itself and a pilot (wave 4).
3. **Then change over time.** Where the growth comes from, and whether data is ageing. It comes before the pilot
   (wave 3) because it can be built and checked without production storage, which is not yet reachable.
4. **Then reach.** Everyone in the organization, signed in, with access kept in step with the directory.
5. **Then action.** Cost, digests, and the decisions reports should lead to.

## Decisions taken in this plan

| Decision | Choice |
| --- | --- |
| Product and executable | **Pinax** ("Pinax: Explore your Data"), `pinax`; Python package `pinax`; Apache-2.0 |
| Architecture | FastAPI and SQLAlchemy (catalog) with DuckDB snapshot indexes and per-file Parquet; plain-JS interface with no build step, adapted from Tundra (`docs/ARCHITECTURE.md`) |
| Crawler | fs-inventory, pinned (1.0.0) and installed by `scripts/install-fs-inventory.sh`; Pinax never crawls storage itself and never changes fs-inventory |
| Unit of display | Directories, with subtree totals precomputed per scan; files only in searches and lists |
| Unit of ownership | A top-level folder of a filesystem, owned by one lab or department; people, labs, memberships, and folder ownership use Tundra's model |
| Ages | Measured from the scan's start, by modification time unless a view says otherwise; seven buckets from under 90 days to ten years or more |
| Colors | Interface tokens from Tundra; a validated hot-to-cold age scale (red for data changed within two years, blue from then on, split at the default cold threshold; until wave 3 a one-hue orange-to-red ramp); a custom Pinax logo, not the Fred Hutch logo |
| Development data | The demo seed (sparse files scanned by the real fs-inventory), `bench/synth_scan.py`, and scans of the maintainer's own files; real scans are never committed, only measurements |
| Default port | 8001 (Tundra uses 8000) |

## In every wave, from wave 2

- **Demo.** Each new capability arrives in the demo seed with data that shows it, and in the README's demo script.
- **Documentation.** The slice that builds a feature updates the README and the guide it belongs in
  (`docs/ARCHITECTURE.md`, `CONFIGURATION.md`, `DEPLOYMENT.md`, `DEVELOPMENT.md`), and `.env.example` for new settings.
- **Trust.** New totals are checked against an independent computation in the tests, not only against themselves.
- **Budgets.** A slice that changes the index or its queries reruns `bench/bench_index.py` and records the numbers;
  `requirements.md` Q3–Q5 must still hold.
- **Interface.** Both themes and a 375-pixel-wide window are checked; any new chart color is validated.
- **Release.** At the end of the wave: version bumped, `CHANGELOG.md` dated, pull request merged, `vX.Y.0` tagged,
  release published with the changelog.

## 0.1.0: see the data (wave 1)

A PI opens Pinax and gets:
- every folder their labs own, across filesystems, with size, age profile, and change since the previous scan;
- an age map of any folder that shows where the old data sits, down to any depth;
- the same folder broken down by type, owner, and cold subfolders, and its files searchable.

IT gets every filesystem at once, storage health, scans, and people and access.

### Wave 1

**Done** on the branch `initial-pinax`, built in one pass rather than one commit per slice; its slices are
recorded below as built. Merged to `main` in pull request #1 and released as v0.1.0.

| Slice | Status |
| --- | --- |
| 1. The program and its shell | Done |
| 2. Reading scans and building the index | Done |
| 3. Queries, places, and access | Done |
| 4. The age map | Done |
| 5. Reports | Done |
| 6. Home pages and IT views | Done |
| 7. Demo, tests, benchmarks, documentation; release 0.1.0 | Done |

1. **The program and its shell (P1, P2, P4, A4).** Ported from Tundra with renames:
   - `config.py`, `db.py`, `auth.py`, `main.py`, `cli.py`; settings as `PINAX_*`; SQLite catalog in WAL mode.
   - The interface: `css/pinax.css` (Tundra's tokens, light and dark themes), `js/core.js` (templating, API
     client, router, header, theme toggle, persona switcher), one app at `/app/` with a sidebar, and a landing page.
   - The Pinax mark, wordmark, and favicon; `LICENSE`, `NOTICE`, `.gitignore`.
2. **Reading scans and building the index (S1, S2, S3, S5, S6, S7, P3).**
   - `scripts/install-fs-inventory.sh` installs fs-inventory 1.0.0 into `bin/`, checking SHA-256 and version.
   - `categories.py`: type normalization (`fastq.gz`, `nii.gz`), twelve categories, seven age buckets.
   - `ingest/build.py`: stage and classify, then per top-level folder sort files and aggregate by directory, type,
     and owner, then roll up subtree totals one depth level at a time; intermediate tables in a scratch database.
   - `snapshots.py`: `pinax scan` and `pinax ingest`, the snapshot record, folder history, retention.
   - *Done:* 1 million files in 3.4 s, 10 million in 22 s (2.2 s per million) on a 10-core laptop; 55 bytes per file.
3. **Queries, places, and access (A1, A2, E5, Q2).**
   - `index.py`: children with the long tail folded, grandchildren, range sums for types and owners, cold folders,
     file listings, all bound as parameters; `Stats` for additive totals.
   - `explore.py`: places (a folder, or a person's home across filesystems), canonical-path and folder checks,
     results merged across places with mean ages rebased to one reference time.
   - *Done:* grouping by "beyond the limit" instead of `FILTER` aggregates cut the two-level treemap from 230 ms to
     25 ms at a million files. A test caught that `alpha_lab/../beta_lab` passed the folder check (nothing leaked);
     non-canonical paths are now refused.
4. **The age map (E1, E2, E3, E4, P4, S7).** `js/treemap.js` (squarified, tiles as links, measured labels,
   2-pixel gaps, nested frames), `js/viz.js` (buckets, metrics, color modes, legends), `js/explore.js` (controls,
   tooltip, table view, detail panel).
   - *Done:* age ramp `#e8a75c`–`#620000` (light) and `#f9ca9c`–`#af2c22` (dark, not inverted) pass the ordinal checks;
     teal share ramps likewise.
5. **Reports (R1, R2, R3, R4, R5, R6).** Age report, file types with the uncompressed sequencing callout, cold folders
   with CSV, owners with deleted accounts, file search with globs and CSV export.
6. **Home pages and IT views (O1, O2, O3, A3).** *My data* and folder cards; the IT overview; folders and labs with
   owner changes; storage health; scans; people and access.
7. **Demo, tests, benchmarks, documentation; release 0.1.0 (P5, Q1).**
   - `seed.py`: two filesystems, thirteen labs and departments, three unmapped folders, nineteen people, sparse files
     with ages back fifteen years, an unreadable folder, problem names, a small-file hotspot, two scans with activity
     between them, and owners rewritten to fictional accounts (demo scans only).
   - 22 tests: every directory's totals against brute-force sums, types and categories, sorting and splitting, refused
     scans, folding, cold-folder maximality, `LIKE` escaping, authorization, reports, and an end-to-end scan with the
     real binary. `bench/synth_scan.py` and `bench/bench_index.py`.
   - README, `docs/ARCHITECTURE.md`, `CONFIGURATION.md`, `DEPLOYMENT.md`, `DEVELOPMENT.md`, `CHANGELOG.md`.
   - *Done*: released as 0.1.0 on 2026-10-26.

## 0.2.0: ready for real storage (wave 2)

Everything a first production deployment needs, built and checked without production storage: every number
checked against an independent tool, the shapes of real filesystems handled, 100 million files indexed, scans
ingested as they arrive, labs onboarded in bulk, and a deployment kit. Production storage and the pilot follow
in wave 4.

### Wave 2 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Independent cross-check (Q1) | For every directory of the demo scans and of a 1- and 10-million-file synthetic scan, Pinax's files, directories, bytes, allocated bytes, oldest and newest modification time, and bytes and files per age bucket equal `fs-inventory rollup`'s (run with Pinax's bucket edges); a committed check script and a test that runs it when the binary is installed | Done |
| 2 | Real-world shapes (S3, Q1, Q3, Q4) | A real local scan (the maintainer's `~/Documents`) and a synthetic scan with a two-million-file folder, a 200,000-subfolder folder, and a chain 1,000 deep pass the slice 1 check; names that are not UTF-8 or hold control characters, mount points, nanosecond times, and leftover parts handled; laptop measurements recorded; no real scan, path, or name committed | Done |
| 3 | Scale to 100 million (S3, R3, Q3–Q5) | A 100-million-file synthetic scan, built and queried on the development laptop, meets Q3–Q5; whole-filesystem cold folders precomputed per age threshold at build time and answered within Q3; build memory and threads tuned and documented | Done |
| 4 | Automatic ingest (S4, O4) | `pinax ingest --watch` (or a timer) ingests each new verified scan under a folder once, skipping incomplete and already-ingested scans, safe if two run at once; failures shown on *Scans* with the error; tests for each case | Done |
| 5 | Bulk onboarding (A5) | Labs, people, memberships, and folder ownership imported from CSV with a dry run that lists every change, refusing ambiguous rows; changes audited; example files and docs | Done |
| 6 | Deployment kit (P6, A4) | systemd units for the web app and ingest; header mode checked behind oauth2-proxy with a local test identity provider; a health endpoint; `docs/DEPLOYMENT.md` rewritten as an installation guide, confirmed by the pilot's installation in wave 4 | Done |
| 7 | Release 0.2.0 | README demo script updated; version 0.2.0, changelog, pull request, tag, release | Done |

### Decisions for wave 2

- `fs-inventory rollup` is the independent reference for Q1: a separate implementation (Rust, streaming) of the same
  totals. Its age buckets are set to Pinax's edges (`--age-buckets 90d,1y,2y,3y,5y,10y`) and `--age-depth` to the scan's
  depth, so every directory has a histogram; any difference is a bug in
  one of them until explained.
- Production storage is out of reach while wave 2 is built (decided 2026-10-26). The real scan in slice 2 is the
  maintainer's `~/Documents`: real names and shapes, but one owner on a laptop disk. Owners and labs are covered by
  the demo seed and synthetic scans; production scans, server hardware, and the pilot are wave 4.
- Pinax records only counts, timings, and checksums from real scans, never paths or names, in the repository;
  the scans themselves stay outside it and are deleted after measuring.
- Watching polls a folder on a timer instead of relying on file-change notifications, which network filesystems
  do not deliver reliably.
- Bulk onboarding is a stop-gap until the authoritative source of folder ownership is decided (wave 5); the import
  format is documented so that source can produce it.
- No catalog migrations yet: waves 2 and 3 may still change the catalog schema, so pilot catalogs are re-created
  from the import files. Snapshot indexes are rebuilt from scans when `INDEX_VERSION` changes.

### Slices as built

1. **Independent cross-check (Q1).**
   - `crosscheck.py` and `pinax check SNAPSHOT_ID | SCAN_DIR`: runs `fs-inventory rollup` on a shadow of the scan
     (the scan folder is never written to) with Pinax's bucket edges and the scan's depth, joins rollup's rows
     to the index on `dir_id`, and compares 31 measures per directory: path, files, directories, bytes, allocated
     bytes, incomplete directories, oldest and newest modification time, newest access and change time, and
     files and bytes per age bucket. Lists the differing directories; exits 1 on any difference. With a scan
     directory and no `--index`, it builds a temporary index first.
   - Found and fixed in the build: age buckets now use whole microseconds instead of fractional days, whose
     rounding put files exactly on an edge in the younger bucket for about one scan start time in eleven (one
     in four at the ten-year edge); a directory revisited under `--detect-cycles` no longer counts as
     incomplete. `INDEX_VERSION` is 2.
   - Tests (when fs-inventory is installed): a scan with files on every bucket edge and a second either side, a
     future file, a symlink, empty, unreadable, unentered, and revisited directories, and a deep path; the check
     detecting changed and missing rows; the command; and a small synthetic scan. Bucket edges and incomplete
     counts are also tested without the binary. `tests/scanfix.py` writes whole-microsecond times and a parts list.
   - *Done*: every directory equal on the four demo scans and on 1- and 10-million-file synthetic scans (244 K
     directories each; rollup 0.5 s and 1.4 s, compare 0.3 s on an Apple M4 laptop).
2. **Real-world shapes (S3, Q1, Q3, Q4).**
   - From a local tree built to be awkward and scanned by fs-inventory (names with newlines, tabs, Windows
     characters, decomposed accents, and a bidirectional override; a path of 5,500 characters 46 levels deep; a
     folder of 3,000 files; times before 1970, at 0, and in the future; symlinks and hard links; an unreadable
     folder; a mounted disk image, with and without `--cross-mounts`; microsecond and nanosecond times), each
     equal to rollup:
     - nanosecond scans failed to build: times are now kept as microseconds, truncated toward zero as rollup does;
     - the build reads only the parts the manifest lists, so leftovers of an interrupted run don't count twice;
     - names show control characters and bidirectional overrides as escapes in the interface;
     - deep folders: breadcrumbs collapse, long paths clamp to three lines, titles wrap (no sideways scroll at
       375 pixels).
   - `synth_scan.py --wide --deep` and a path-free shape report in `bench_index.py`. 12.2 million files with a
     two-million-file folder, a 200,000-subfolder folder, and a chain 1,000 deep: equal to rollup on all 445 K
     directories; build 34 s, treemap of the widest folder 108 ms.
   - `pinax check --age-depth` bounds rollup's memory on scans with tens of millions of directories; the
     comparison streams instead of holding a table.
   - A real scan, the maintainer's `~/Documents` (56 K files, 4.7 K folders, 118 GB, 16 levels, 260 file types, 15
     flagged names, one owner), read-only into a scratch folder: equal to rollup on every directory; build 0.8 s,
     every query under 25 ms. Common types it held were counted as *other* (35% of its bytes): camera photo and
     video formats, macro-enabled spreadsheets, diagrams, executables, and disk images were added to their
     categories, leaving 0.8%. The scan and its indexes were deleted after measuring.
   - *Done*: the real local scan and the wide and deep synthetic scan equal to rollup on every directory, with
     the shapes above handled. Measuring production storage moved to wave 4.
3. **Scale to 100 million (S3, R3, Q3–Q5).**
   - A synthetic scan of 100 million files in 2.44 million folders under 2,000 top-level folders, generated in
     155 s (3.8 GB).
   - Found at this scale and fixed: the build ran out of its 8 GB limit reading the scan, because each thread
     decodes a whole row group of about a million entries (146 MB here). Steps that read the scan now use as many
     threads as fit three quarters of the limit (6 of 10 here), from the Parquet metadata, and halve them and
     retry if a step still runs out. Files are staged and sorted in at most 64 groups of top-level folders
     (`files/gNNNNNN/`), so the staging write keeps few files open however many top-level folders there are.
   - The two-level treemap ranked the children of every subfolder and all subfolders to total the rest: 310 ms
     for the root. It now ranks only the 60 shown and totals the rest in one pass: 39–49 ms.
   - `cold_units`: per age threshold, the largest folders with nothing younger, computed at build time;
     whole-filesystem cold folders in 21 ms. Tested against a brute force over the files and against the
     query-time method for every threshold. `INDEX_VERSION` is 3; older indexes still read.
   - Measured on the 10-core laptop within the 8 GB default: build 121 s (peak 7.7 GB), index 5.56 GB (56 bytes per
     file); directory-level queries 1–50 ms; lab searches 22 ms, whole-filesystem searches 2.8 s; every
     directory equal to rollup. With 17 GB and all ten threads the build was no faster (133 s), so the default
     stays 8 GB.
   - *Done*: Q3–Q5 met at 100 million synthetic files on the laptop; server hardware and production are wave 4.
4. **Automatic ingest (S4, O4).**
   - `watch.py` and `pinax ingest --watch [FOLDER] [--interval N] [--once] [--no-verify] [--allow-incomplete]`: each
     pass finds scan folders up to three levels down, oldest first; complete scans are verified with
     `fs-inventory verify` and ingested; `running` and `interrupted` ones wait; `incomplete` and `failed` ones are
     recorded as skipped (a new snapshot status) with why. Every recorded scan is final for the watcher: failed and
     skipped ones are built only by `pinax ingest SCAN_DIR`.
   - Every ingest takes a file lock in `PINAX_VAR_ROOT`, and a scan ID can be recorded once, so watchers and manual
     ingests can run together.
   - A `heartbeat` table: *Scans* shows the watcher's folder, interval, state (watching, ingesting, not responding
     after three missed passes, stopped) and its last pass; `/api/admin/ingest`.
   - Tests for each case: finding scans; new scans once, oldest first; waiting scans picked up once finished;
     incomplete and failed scans skipped once, and accepted by hand or with `--allow-incomplete`; build failures
     recorded and not retried, and shown by the API; refusals recorded; unregistered roots left alone; two
     watchers at once; the heartbeat; `--once` exit codes; and, with the binary, a scan failing verify and a real
     scan verified and ingested.
   - *Done*: all of the above, checked on the demo scans and the *Scans* page (desktop and 375 pixels).
5. **Bulk onboarding (A5).**
   - `onboard.py` and `pinax import [FOLDER] [--labs|--people|--memberships|--folders FILE] [--apply]`: four CSV
     files keyed by lab slug, username, (lab, username), and (filesystem, folder). A dry run lists every create,
     update (each field before and after), and removal; `--apply` makes all of them in one transaction or, if any
     row is refused, none. Present columns set fields, absent ones leave them; `none` removes a membership or
     unmaps a folder; labs and people are deactivated, never deleted.
   - Refused: unreadable files, missing, unknown, or repeated columns, wrong value counts, empty required values,
     keys listed twice, usernames differing only in case, references to labs, people, or filesystems that exist
     nowhere, and values Pinax does not accept.
   - Audit: one event per change with the interface's actions, marked as from an import with who ran it, and an
     `import.applied` summary with each file's SHA-256.
   - `docs/IMPORT.md` is the format, so the future authoritative source can produce it; `examples/import/` adds two
     departments, a lab, and three people to the demo and gives its three unmapped folders owners.
   - Tests: the dry run's every change and no effect; apply, its audit, the access it grants, and idempotence;
     absent and empty columns; each refusal, with nothing applied; the command's exit codes; the example files.
   - *Done*: all of the above.
6. **Deployment kit (P6, A4).**
   - `deploy/`: `pinax-web.service` (127.0.0.1 only), `pinax-ingest.service` (the watcher, at lower priority),
     `pinax-scan@.service` and `.timer` (a weekly scan per filesystem, `--no-ingest`), all as an unprivileged
     `pinax` user that may write only `/var/lib/pinax`, the scan with `CAP_DAC_READ_SEARCH` alone; `pinax.env`; and
     an oauth2-proxy configuration (option names checked against its 7.15 documentation), unit, and secrets file.
   - Header mode believes the username header only from `PINAX_TRUSTED_PROXIES` (default this host); before, it
     believed it from anyone who could reach Pinax.
   - `/api/health`, open and naming nothing: `ok`, `degraded` (a ready snapshot's index missing, or automatic
     ingest stopped or not responding), or 503 when the catalog cannot be read.
   - Writing the guide found that `pinax ingest` refuses a ready scan, so there was no way to rebuild indexes after
     an upgrade, though `INDEX_VERSION` changed twice in this wave: `pinax rebuild SNAPSHOT_ID... | --outdated`
     rebuilds in place, keeping the old index if the build fails, and the index cache reopens rebuilt indexes.
     It also found that `uv` run as root would install Python where the service user cannot run it; the guide
     sets `UV_PYTHON_INSTALL_DIR`.
   - `docs/DEPLOYMENT.md` rewritten as an installation guide: user and folders, Pinax and fs-inventory, settings,
     filesystems and onboarding, services, oauth2-proxy, and checking the installation; then scans, upgrades,
     sizing, backups, and monitoring.
   - `scripts/sso-check.py`: a stand-in OpenID Connect provider (standard library, tokens signed with `openssl`),
     oauth2-proxy 7.15.5 with `deploy/oauth2-proxy/pinax.cfg`, and Pinax in header mode. Passed: Pinax sees the
     signed-in person; a username header the browser sends is not believed; without a session nothing reaches
     Pinax; the health check needs no sign-in. The oauth2-proxy download was checked against its SHA-256 and
     deleted after.
   - Tests: the health states; the header refused from untrusted addresses and accepted from trusted networks;
     demo sign-in off in header mode; every unit's command parsed by the CLI and every setting it names real;
     rebuilds (a lost index, the cache reopening, `--outdated`, a missing scan).
   - *Done*, except what only a real server shows: the pilot installation in wave 4 confirms the guide and units
     (`systemd-analyze verify` and the sandboxing options cannot be run on the development Mac).
7. **Release 0.2.0.**
   - The demo re-seeded with this wave's code: all four snapshots equal to rollup; the README demo script walked
     through (its numbers hold), with steps added for *Scans* and the CSV import.
   - README features, the architecture's production table, and the changelog's upgrade notes brought up to date.
   - Version 0.2.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated; pull request
     `wave2` to `main`, then tag `v0.2.0` and the release.
   - *Done*.

## 0.3.0: change over time (wave 3)

Where the growth comes from, and whether data is ageing: a PI sees how their lab's data grew and aged over the
scans, which folders grew or shrank since a chosen scan, and what is new; IT sees the same for every filesystem.
Every change is checked against an independent computation, like every total.

### Wave 3 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Comparing two scans (S9, Q1) | At ingest, every directory's change since the filesystem's previous scan (size, space on disk, files, folders, bytes per age bucket; new and removed folders) is stored with the index; equal on every directory to the difference between `fs-inventory rollup`'s totals of the two scans, joined by path; tested against brute force over two file lists; the added build time measured at 100 million files | Done |
| 2 | A history to show (demo and benchmarks) | `pinax seed` writes six scans of each filesystem over six months: a lab that grows, a cleanup, a project moved between labs, data ageing past the cold threshold, a new lab; `synth_scan.py --evolve` derives a changed scan from another, for benchmarks and checks at scale; the README demo script updated | Done |
| 3 | History charts (E7) | Size, files, cold share, and mean age over every scan for each top-level folder, lab or department, and filesystem (from folder history), and for any deeper folder across the retained snapshots; a table view; both themes and 375 pixels; chart colors validated | Done |
| 4 | The change map (E8) | The age map's treemap colored by growth or shrinkage since a chosen scan (the previous one by default), sized by current or earlier size, with removed folders shown; drill down and up as in the age map; within Q3 for a lab folder at 100 million files | Done |
| 5 | What changed (R9) | For any folder and two scans: the subfolders that grew and shrank most, new and removed folders, and the largest new and removed files, with CSV; within Q4 for a lab folder at 100 million files | Done |
| 6 | Older snapshots in the interface (S8) | A snapshot picker on every report, the scan date shown wherever a number is, and links that keep the snapshot; a pruned snapshot offers its folder history only | Done |
| 7 | Release 0.3.0 | The demo script walked through; version 0.3.0, changelog, pull request, tag, release | Done |

### Decisions for wave 3

- Folders are matched across scans by path, so a renamed or moved folder shows as one removed and one new.
  Matching them by inode is left for later.
- The change since the previous scan is computed once, when a scan is ingested, and kept with its index; the change
  since any other retained scan is computed when asked, for one folder and the levels shown.
- Below the top level, history and comparisons reach back only as far as the retained snapshots
  (`PINAX_KEEP_SNAPSHOTS`, 6 by default); top-level folders, labs, and filesystems have history for every scan.
- The trust check for change uses `fs-inventory rollup` of both scans, so a wrong difference cannot come from
  Pinax's own totals agreeing with themselves.
- Growth and shrinkage use a diverging color pair validated like the age ramp, with a neutral gray for no change:
  gold and teal, apart from the age scale's red and blue.
- Age is shown hot to cold: red for recent data, blue for cold, so cold data looks cold, as the reports call it.
  (Changed in wave 3 from an orange-to-red ramp in which all data looked warm.)

### Slices as built

1. **Comparing two scans (S9, Q1).**
   - `build_index(..., previous=INDEX)` attaches the earlier index read-only and joins the two `dirs` tables by
     path into `changes`: per directory, both scans' files, folders, bytes, allocated bytes, size-weighted time
     sum, and files and bytes per age bucket (`CHANGE_MEASURES`), and whether it is in each. Ingest and rebuild
     pass the filesystem's newest earlier ready snapshot with its index on disk; the index's `meta` records which
     scan it was compared with. `INDEX_VERSION` is 4; `pinax rebuild --outdated` adds comparisons oldest first.
   - `crosscheck.check_changes`: `fs-inventory rollup` of both scans, joined by path, against `changes` on 38
     measures (presence in each scan, and each scan's totals and age mix). `pinax check SNAPSHOT_ID` runs it when
     the previous scan's folder is still there (`--previous-scan` for a scan directory), and says so when not.
     The totals check and this one now share their tally of differences.
   - Tests: every directory of two scans a month apart (a folder deleted, files added and removed, a new top-level
     folder, everything a month older) against brute force over the two file lists; the index saying what it
     was compared with; ingest, a scan found late, and rebuild; and, with the binary, equality with two rollups,
     a tampered change caught, and the command.
   - *Done*: the demo's changes equal to the two rollups on every directory; at 100 million files the comparison
     takes 0.9 s of the build and 228 MB of the index, and checking it 24 s (all 2.44 million directories equal).
2. **A history to show (demo and benchmarks).**
   - `pinax seed` takes six scans of each filesystem, dated a month apart (`SCANS`, as many as
     `PINAX_KEEP_SNAPSHOTS` keeps by default), and changes the demo trees before each (`month_files`, `REMOVED`,
     `MOVED`, `COMPRESSED`, `JOINS`): every month two Illumina runs, a nanopore run, and a new batch for the
     Fujimoto Lab, which grows from 21 to 27 TB; cleanups in the Chen and Haddad labs (scan 2); a light-sheet
     project moved from the Bennett Lab to the Haddad Lab (scan 3); the Quintero Lab joining, mostly with data from
     the PI's previous institution (scan 4); the Lindqvist Lab compressing its FASTQ files (scan 5); and the
     Kowalski Lab's 2024 project ageing past two years, so its cold share climbs from 64% to 100% at the same size.
   - Demo-only, in `_make_demo_scan` beside the owner rewrite: each scan is dated a month after the one before, and
     the times the seed cannot set (directories changing as files are written, inode changes, births, errors) are
     brought back to that date. Files are dated within the month they are written in, and reference data is read
     again each month, so ages and access times are as the dates say. A moved project keeps its owners, as `mv`
     would.
   - `synth_scan.py --evolve SCAN --days N` writes a scan as it might look N days later: a share of projects
     deleted, of folders grown, and of files rewritten, and new top-level folders; each generation names its new
     files apart, so scans can be evolved again and again. It works on any fs-inventory scan, real ones included.
   - Tests: the six months (every file dated within its month and none after its scan, no file written twice, the
     cleanups, move, compression, and cold threshold finding what they expect); a scan dated by the seed; an
     evolved scan's changes against brute force over the two scans' files, and what it says it did; evolving twice;
     and, with the binary, both totals and changes equal to fs-inventory rollup.
   - *Done*: every demo snapshot and every change between consecutive demo scans equal to fs-inventory rollup (12
     snapshots, 10 changes); evolved scans of a real demo scan and of the wide and deep shapes likewise. At one
     million files, `--evolve` takes 1.6 s, comparing 0.2 s of a 6 s build, and checking the change 1.1 s (all
     245,361 directories equal). Kept to small scans on the development laptop; the measurement with changed scans
     at 100 million files waits for server-class hardware in wave 4.
3. **History charts (E7).**
   - `explore.history` and `/api/history` take any place: a person's home, a lab or department, a filesystem, a
     top-level folder (every scan, from folder history), or a deeper folder (the retained snapshots, from their
     indexes; the response says how many older scans are not covered). Points carry size, space on disk, files,
     bytes per age bucket, cold bytes, and mean age, and whether the place was in that scan. A place on several
     filesystems has a point per day with a scan, each filesystem as of its latest scan by then, with its mean
     age carried forward. Ingest now records each snapshot's filesystem totals, so a filesystem's history reads
     one row per scan; older snapshots are summed from folder history in SQL.
   - *History* (`js/history.js`, `js/timechart.js`) in the sidebar and every report's links: size by age as
     stacked columns (oldest at the bottom, so new data lands on top), then files, cold share, and average age as
     small multiples on the same dates rather than a second axis; one readout for all four on hover, tap, or the
     arrow keys; a table view and CSV. Scans a folder was not in leave a gap. The home pages' change figure links
     to it.
   - Colors: the stacked columns use the age ramp (ordinal checks pass in both themes); each small multiple has
     one series in the color of the figure above it (`--series-1`, `--age-5`, `--age-3`), above 3:1 against the
     surface in both themes. As a categorical set the three would fail the validator, but they are never read
     against each other: each chart's title names its series.
   - Tests: every point against brute force over the scans' files for a top-level folder (with pruning), a
     filesystem (and the same from folder history when totals were not recorded), a deeper folder (retained
     snapshots, and one deleted in the last scan through an earlier snapshot), a lab on two filesystems scanned on
     different days, homes, and access.
   - *Done*: checked in both themes and at 375 pixels on the demo (no page overflow; the table scrolls in its
     card). On the demo every history request takes 3–13 ms; with 2,000 folders and 150 weekly scans in the
     catalog, a filesystem's history takes 3 ms and a lab's 4–8 ms.
4. **The change map (E8).**
   - `SnapshotIndex.change_children`: a directory and its children in two scans, joined by path, with each scan's
     files, folders, bytes, allocated bytes, and time sum, and whether it is in each; ranked by the larger of the
     two sizes, now, or then; the rest folded with exact sums and counts of new and removed folders; and each listed
     child's largest children. From the `changes` table for the previous scan, or by attaching an older retained
     index read-only (`attach`, once per index) and joining its `dirs` on the same depth and path ranges.
   - `explore.change_tree` and `/api/changes` take the same places as the age map, `since` (a day: per filesystem,
     the newest kept scan on or before it, or the oldest kept), and `metric`; a removed folder can be opened, since
     it is in the earlier scan. The response says which scan each filesystem was compared with, and offers the days
     of every kept earlier scan.
   - *Change map* (`js/changes.js`) after the age map in the sidebar and report links: the treemap colored by the
     change in size in seven steps (gold shrinking, teal growing, gray unchanged; new and removed folders take the
     last step and say so on the tile); sized by the larger of then and now by default, so a removed folder keeps
     its room; a panel with the change, both sizes, new and removed subfolders, and what grew and shrank most; a
     table view.
   - Colors (`--chg-*`): the brand's diverging pair, gold to teal, since red is the color of old data. Each arm
     passes the ordinal checks in both themes (on navy the arms brighten outward); matching steps of the two arms are
     at least ΔE 17 apart, with normal vision and under color-vision deficiencies; the true gray midpoint is at least
     ΔE 15 from both first steps with normal vision and at least 10 under deficiencies (the target is 8).
   - Tests: every tile against brute force over the two scans' files, from the stored previous scan and from an
     older scan joined when asked; new and removed folders, opening a removed one; folding and two levels with exact
     sums; sizing; access.
   - *Done*: checked in both themes and at 375 pixels on the demo. At one million synthetic files (three evolved
     generations), the root and a lab folder take 7–21 ms from `changes` and 12–31 ms joined with an older scan,
     against 24 ms for the age map; the 100-million-file measurement waits for server-class hardware (wave 4).
5. **What changed (R9).**
   - `SnapshotIndex.change_units`: over a place's subtree in both scans (the stored changes, or an attached older
     index), the folders in both scans that grew and shrank most, leaving out a folder when one of its subfolders
     holds nine tenths or more of its change, so a chain of folders is listed once, where the change is; the
     outermost new and removed folders (whose parent is in both scans); and counts of every new and removed folder.
     New folders are not listed as growth, nor removed ones as shrinkage: each change is listed once.
   - `SnapshotIndex.file_changes`: files in one scan and not the other, matched by path, by anti-joining the two
     scans' file lists over the place's path range; the largest, with their number and total size.
   - `explore.what_changed` and `/api/changed`, with the places, `since`, and comparison of the change map;
     `fmt=csv&list=` downloads any of the six lists up to `PINAX_EXPORT_MAX_ROWS`.
   - *What changed* (`js/changed.js`) after the change map: the change in size, new and removed files and folders,
     then the six lists, each linking to the change map at that folder, with CSV.
   - Tests: every list against brute force over the two scans' files, for the root and three folders (one new, one
     with a removed subfolder), from the stored previous scan and from an older scan; CSV, limits, and access.
   - *Done*: checked in both themes and at 375 pixels on the demo. At one million synthetic files (three evolved
     generations), a lab folder's report takes 70–90 ms and the whole filesystem's 0.5–0.6 s; the 100-million-file
     measurement waits for server-class hardware (wave 4).
   - Before slice 6, at the maintainer's request: the age scale changed from orange-to-red to hot-to-cold (red for
     data changed within two years, blue from then on), so cold data looks cold and a map of mostly old data no
     longer reads as alarming throughout. Each arm passes the ordinal checks in both themes.
6. **Older snapshots in the interface (S8).**
   - Every report takes `at`, a day: each filesystem's newest scan by the end of it (`explore.Scan`, accepted
     wherever a snapshot ID was). A day whose newest scan was pruned answers 410 with where its totals are; History
     still answers, since it shows every scan's folder totals. A home leaves out a filesystem with no full scan by
     then. `/api/scan-days` lists a place's scan days, newest first, and whether each is kept in full.
   - The interface: `state.at` from the address, added by `placeQuery` to every place link and report request, so
     the sidebar, report links, breadcrumbs, tiles, and CSV downloads keep the scan. The scope header's date is a
     picker of the place's scan days; an older one is marked, with *Back to the latest*; a day kept only as folder
     totals opens History, which marks it and says why. History marks any chosen scan in its charts and table.
     *Scans* links each kept snapshot to its age map and each pruned one to History.
   - Tests: scan days and which are kept; reports at a chosen day, between scans, and the latest; a home on two
     filesystems at a day; every report taking the day; a pruned day (410, History still showing it), a day before
     any scan, and a home leaving a pruned filesystem out. They caught File types and Owners ignoring the day.
   - *Done*: checked in the browser, in both themes and at 375 pixels: the picker, links keeping the scan, the
     change map and What changed comparing with the scans before the chosen one, History's mark, and *Scans* links.
7. **Release 0.3.0.**
   - The demo re-seeded with this wave's code: all 12 snapshots, and all 10 changes between consecutive scans,
     equal to rollup. The README demo script walked through against the demo, its numbers checked through the API
     and the pages seen in the browser; the CSV import applied and the demo seeded again. Two sentences
     corrected: where `2025_immune_aging` sits, and the tiles folder shown as a very large directory.
   - Version 0.3.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with upgrade
     notes; pull request `wave3` to `main`, then tag `v0.3.0` and the release.
   - Not measured: anything above a million files. The change map's Q3 and What changed's Q4 at 100 million files,
     and changed scans at that size, wait for server-class hardware (wave 4).
   - *Done*.

### After 0.3.0

- **People in the app (A8)**, at the maintainer's request, so the pilot's people can be added without CSV. *People
  and access* gains a People tab (filters for active, administrators, no access, and inactive; search) and a
  person editor (profile, lab memberships, the storage administrator role, deactivate and reactivate), on
  `people.py`, whose rules for usernames and emails `pinax import` now shares. People are never deleted; a
  deactivated person keeps their access, unused, so reactivating restores it (as `active=false` in the import
  does; Tundra instead removes it). Tests cover each change and its audit, the shared rules, sign-in after
  deactivating and reactivating, and the guards that keep an administrator. Checked in both themes and at 375
  pixels.

## Later waves (outlined)

| Wave | Release | Theme | Main content |
| --- | --- | --- | --- |
| 4 | 0.4.0 | Production pilot | The largest production scan available ingests and passes `pinax check`; build time, index size, and query times measured on production replace the extrapolations in `docs/ARCHITECTURE.md`; 100 million files on server-class hardware; installation on a pilot server from the deployment kit, scanning on a schedule; IT and a few pilot labs use it, and what they find is fixed or recorded |
| 5 | 0.5.0 | Reach | PostgreSQL and migrations (P7); single sign-on with roles from directory groups and membership synced from the directory (A6); recertification report (A7); CI, container image (P6); metrics, logs, and alerts (O4, O5); security review |
| 6 | 0.6.0 | Action | Hard-link aware totals (R7); cost views (R8); periodic digests for PIs; saved and shared views; quotas beside folders; whole-filesystem searches as background jobs |
| 7 | 1.0.0 | Hardening | Budgets met at a billion files in production; accessibility audit; documentation complete; pilot and early-user feedback addressed |

## Decisions still open

- The authoritative source of folder ownership (which lab owns each top-level folder), and how Pinax stays in step
  with it (wave 5).
- Which filesystems record access time reliably enough to show it by default.
- Scan cadence and snapshot retention for each filesystem.
- The cost model, if cost views are wanted (wave 6).
- When production storage becomes reachable for wave 4, and which filesystem and labs the pilot uses.
- Who may see unmapped folders besides storage administrators (for example division data managers).
