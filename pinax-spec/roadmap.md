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

**Where things stand:** waves 1 to 13 are released as 0.1.0 to 0.13.0 (tags on `main`). Besides forecasts, 0.6.0
has what was merged since 0.5.0:
- labs and departments can be added, changed, and deactivated in the app, so a site needs no CSV files at all;
- guidance for operating Pinax;
- time limits for requests, and new and removed files recorded at ingest, for filesystems of two billion files.

After load (wave 14) comes the production pilot (wave 15), when production storage is reachable. Ideas kept for later but
not planned are in the [parking lot](#parking-lot).

The order follows one principle:
1. **Trust first.** Totals that agree with an independent computation on every directory, and that say when
   they are lower bounds.
2. **Then real storage at real scale.** First everything a production deployment needs, checked on real local
   scans and on synthetic data at scale (wave 2); then production storage itself and a pilot (wave 15).
3. **Then change over time.** Where the growth comes from, and whether data is ageing. It comes before the pilot
   (wave 3) because it can be built and checked without production storage, which is not yet reachable.
4. **Then what a site needs to run Pinax, and to charge for storage.** People managed in the app, and sign-in
   without single sign-on (wave 4); costs and chargeback (wave 5). Both come before the pilot for the same reason.
5. **Then action.** Where storage is heading, what a lab could free and what that would save, and folders
   described in terms a lab and IT share (waves 6 to 8). These come before the pilot for the same reason.
6. **Then reach.** Everyone in the organization, signed in, with access kept in step with the directory (wave 13).
7. **Then many people at once.** What a server does when many of them use it together, measured before the pilot
   brings them (wave 14).

Wave 9 (action: hard links, saved views, background searches) came before the pilot and reach once the pilot was
still out of reach after wave 8, as this plan allowed; the waves after it were renumbered. Wave 10 (operations)
is the part of reach that a production deployment needs before it has many users: a catalog on PostgreSQL that
migrations upgrade in place, logs a site can collect, and metrics its monitoring can trend and alert on. It came
next at the maintainer's request, and the pilot and the rest of reach were renumbered after it. Wave 11 (deeper
views: the age map as an icicle and a sunburst, and what 0.10.0 left) came next the same way, and the pilot, reach,
and hardening were renumbered again. Wave 12 (access: roles, API tokens, the audit log, and access reviews) is the
part of reach that works with every way of signing in; it came next, at the maintainer's request. The rest of
reach came next as wave 13, also at the maintainer's request, while the pilot waits for production storage: local
sign-in as the default, optional sign-in with Entra ID, an emergency administrator sign-in, roles from Entra ID, and
CI. Local sign-in's two-factor codes and per-address limits, and a container image, went to the parking lot. Wave 14
(load: many people at once, measured with a load test on a laptop, taken from hardening; and file categories a site
defines) comes next the same way, while the pilot also waits for an Entra ID test registration. The pilot is
renumbered wave 15, and takes lab membership from the directory (A6), with the decision on where membership and
ownership come from; hardening is wave 16 (1.0.0).

Work that needs a system Pinax has no access to, such as a mail service, a chat workspace, or a storage system's
own interface, is kept in the [parking lot](#parking-lot) rather than planned.

## Decisions taken in this plan

| Decision | Choice |
| --- | --- |
| Product and executable | **Pinax** ("Pinax: Explore your Data"), `pinax`; Python package `pinax`; Apache-2.0 |
| Architecture | FastAPI and SQLAlchemy (catalog) with DuckDB snapshot indexes and per-file Parquet; plain-JS interface with no build step, adapted from Tundra (`docs/ARCHITECTURE.md`) |
| Crawler | fs-inventory, pinned (1.0.0) and installed by `scripts/install-fs-inventory.sh`; Pinax never crawls storage itself and never changes fs-inventory |
| Unit of display | Directories, with subtree totals precomputed per scan; files only in searches and lists |
| Unit of ownership | A top-level folder of a filesystem, owned by one lab or department; people, labs, memberships, and folder ownership use Tundra's model |
| Ages | Measured from the scan's start, by modification time unless a view says otherwise; seven buckets from under 90 days to ten years or more |
| Sign-in | Passwords Pinax keeps (`local`), or a trusted single sign-on proxy's header (`header`); from wave 13, also Microsoft Entra ID in Pinax itself. Entra ID stays optional: a small organization, or a department without access to its organization's Entra ID, runs Pinax with local sign-in or its own proxy, and roles, API tokens, and the audit log work with every way of signing in |
| Colors | Interface tokens from Tundra; a validated hot-to-cold age scale (red for data changed within two years, blue from then on, split at the default cold threshold; until wave 3 a one-hue orange-to-red ramp); a custom Pinax logo, not the Fred Hutch logo |
| Development data | The demo seed (sparse files scanned by the real fs-inventory), `bench/synth_scan.py`, and scans of the maintainer's own files; real scans are never committed, only measurements |
| Default port | 8001 (Tundra uses 8000) |

## In every wave, from wave 2

- **Demo.** Each new capability arrives in the demo seed with data that shows it, and in the demo walk-through
  (`docs/DEMO.md`).
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
in wave 15.

### Wave 2 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Independent cross-check (Q1) | For every directory of the demo scans and of a 1- and 10-million-file synthetic scan, Pinax's files, directories, bytes, allocated bytes, oldest and newest modification time, and bytes and files per age bucket equal `fs-inventory rollup`'s (run with Pinax's bucket edges); a committed check script and a test that runs it when the binary is installed | Done |
| 2 | Real-world shapes (S3, Q1, Q3, Q4) | A real local scan (the maintainer's `~/Documents`) and a synthetic scan with a two-million-file folder, a 200,000-subfolder folder, and a chain 1,000 deep pass the slice 1 check; names that are not UTF-8 or hold control characters, mount points, nanosecond times, and leftover parts handled; laptop measurements recorded; no real scan, path, or name committed | Done |
| 3 | Scale to 100 million (S3, R3, Q3–Q5) | A 100-million-file synthetic scan, built and queried on the development laptop, meets Q3–Q5; whole-filesystem cold folders precomputed per age threshold at build time and answered within Q3; build memory and threads tuned and documented | Done |
| 4 | Automatic ingest (S4, O4) | `pinax ingest --watch` (or a timer) ingests each new verified scan under a folder once, skipping incomplete and already-ingested scans, safe if two run at once; failures shown on *Scans* with the error; tests for each case | Done |
| 5 | Bulk onboarding (A5) | Labs, people, memberships, and folder ownership imported from CSV with a dry run that lists every change, refusing ambiguous rows; changes audited; example files and docs | Done |
| 6 | Deployment kit (P6, A4) | systemd units for the web app and ingest; header mode checked behind oauth2-proxy with a local test identity provider; a health endpoint; `docs/DEPLOYMENT.md` rewritten as an installation guide, confirmed by the pilot's installation in wave 15 | Done |
| 7 | Release 0.2.0 | README demo script updated; version 0.2.0, changelog, pull request, tag, release | Done |

### Decisions for wave 2

- `fs-inventory rollup` is the independent reference for Q1: a separate implementation (Rust, streaming) of the same
  totals. Its age buckets are set to Pinax's edges (`--age-buckets 90d,1y,2y,3y,5y,10y`) and `--age-depth` to the scan's
  depth, so every directory has a histogram; any difference is a bug in
  one of them until explained.
- Production storage is out of reach while wave 2 is built (decided 2026-10-26). The real scan in slice 2 is the
  maintainer's `~/Documents`: real names and shapes, but one owner on a laptop disk. Owners and labs are covered by
  the demo seed and synthetic scans; production scans, server hardware, and the pilot are wave 15.
- Pinax records only counts, timings, and checksums from real scans, never paths or names, in the repository;
  the scans themselves stay outside it and are deleted after measuring.
- Watching polls a folder on a timer instead of relying on file-change notifications, which network filesystems
  do not deliver reliably.
- Bulk onboarding is a stop-gap until the authoritative source of folder ownership is decided (wave 15); the import
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
     the shapes above handled. Measuring production storage moved to wave 15.
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
   - *Done*: Q3–Q5 met at 100 million synthetic files on the laptop; server hardware and production are wave 15.
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
   - *Done*, except what only a real server shows: the pilot installation in wave 15 confirms the guide and units
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
     at 100 million files waits for server-class hardware in wave 15.
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
     against 24 ms for the age map; the 100-million-file measurement waits for server-class hardware (wave 15).
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
     measurement waits for server-class hardware (wave 15).
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
     and changed scans at that size, wait for server-class hardware (wave 15).
   - *Done*.

## 0.4.0: people and sign-in (wave 4)

Running Pinax without single sign-on and without CSV files: people managed in the app, as in Tundra, and
passwords for sites without an identity provider, with the demo kept as it was. Built after 0.3.0 at the
maintainer's request, while production storage is out of reach, and merged in pull request #4.

### Wave 4

| Slice | Status |
| --- | --- |
| 1. People in the app (A8) | Done |
| 2. Signing in without single sign-on (A9, A4) | Done |
| 3. Release 0.4.0 | Done |

### Slices as built

1. **People in the app (A8).** At the maintainer's request, so a pilot's people can be added without CSV. *People
   and access* gains a People tab (filters for active, administrators, no access, and inactive; search) and a
   person editor (profile, lab memberships, the storage administrator role, deactivate and reactivate), on
   `people.py`, whose rules for usernames and emails `pinax import` now shares. People are never deleted; a
   deactivated person keeps their access, unused, so reactivating restores it (as `active=false` in the import
   does; Tundra instead removes it). Tests cover each change and its audit, the shared rules, sign-in after
   deactivating and reactivating, and the guards that keep an administrator. Checked in both themes and at 375
   pixels.
2. **Signing in without single sign-on (A9).** At the maintainer's request, so a site without an identity provider
   can run Pinax, and move to single sign-on later. `PINAX_AUTH_MODE=local` (`signin.py`, `api/signin.py`): scrypt
   password hashes; server-side sessions; temporary passwords made up by Pinax and shown once, which must be changed
   before anything else; lockout after repeated failures; requests that change something must come from a page of
   Pinax; every sign-in, failure, lock, and password change audited, never a password. `pinax user add | password |
   list` makes the first administrator. Demo mode is kept as it was, for showing Pinax, and refused when
   `PINAX_ENVIRONMENT=prod`. New catalog columns are added at start-up (`db.add_missing_columns`), so a 0.3.0 catalog
   keeps working. Tests: each of these, the demo unchanged, and an older catalog upgraded; checked in the browser in
   both themes and at 375 pixels, in local and demo modes.
3. **Release 0.4.0.**
   - Version 0.4.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.3.0 (new catalog columns added at start-up; the demo refused in production).
   - The roadmap re-planned: costs and chargeback, wanted by the maintainer and needing no production storage, move
     up from the action wave to wave 5; the production pilot moves to wave 6 (since moved to wave 15), and the
     later waves follow.
   - Pull request to `main`, then tag `v0.4.0` and the release.
   - *Done*.

## 0.5.0: costs and chargeback (wave 5)

What storage costs each lab and department: a price for each filesystem, a free tier, and each month's charge, for
lab owners to see and for finance to bill. It is worked out from folder history, which keeps every scan, so it
needs no production storage, and the demo's six months of scans make six months of bills.

### Wave 5 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Prices and free tiers (R8) | For each filesystem, a price per month per GB, GiB, TB, or TiB, from a date on (earlier prices kept); a free tier per filesystem for labs, and one for departments (none allowed), with exceptions for particular labs; one currency (`PINAX_CURRENCY`); set by administrators on a *Costs* page, every change audited; tested | Done |
| 2 | Monthly charges (R8, Q1) | For each lab or department and filesystem, each month: the time-weighted average size from folder history (each scan's size holds until the next), less the free tier, times the price in effect, worked out day by day; the current month estimated to its end; tested against brute force over scan dates, price changes, free tiers, and folders changing owner | Done |
| 3 | What labs pay (R8) | For lab owners and members: this month's estimate and past months, by filesystem and top-level folder, with how each charge is worked out; a figure on the home page; both themes and 375 pixels | Done |
| 4 | Chargeback for finance (R8) | For administrators: every lab's and department's charges for a month, storage no one owns (unbilled), totals by filesystem, and a CSV statement; within Q3 for a year of weekly scans of 2,000 folders | Done |
| 5 | Demo and release 0.5.0 | Prices and free tiers in the demo seed; the README demo script; version 0.5.0, changelog, pull request, tag, release | Done |

### Decisions for wave 5

Taken with the maintainer:
- Charges are for the files' size, what people see in Pinax, not their space on disk.
- A month is charged on the time-weighted average of its scans, like a utility bill: data that comes and goes
  mid-month is charged for the days it was there.
- The free tier is storage per filesystem: one default for labs and one for departments (which may be none), with
  exceptions for particular labs. It comes off each day's size before the price.
- Prices change from a date on; past months keep the prices they had.
- Folders that no lab or department owns are charged to no one; administrators see them as unbilled.

Open within the wave: whether statements need a billing account (cost center) for each lab, and how charges are
rounded (for now, half up to the cent, once per lab and filesystem, unless finance wants otherwise).

### Slices as built

1. **Prices and free tiers (R8).**
   - `price` and `free_tier` tables: schedules per filesystem, each row from a day on. Prices in millionths of the
     currency (exact, up to six decimal places); free tiers in bytes with the unit they were given in. A lab's own
     free tier takes the place of its kind's until it ends (a row with no amount); a kind's cannot end, only be 0.
   - `costs.py`: what applies on a day (`price_on`, `free_on`), and changes that are validated (units, numbers, one
     price or tier per day, nothing absurd) and audited with what they replaced. `PINAX_CURRENCY` (ISO 4217).
   - `/api/admin/costs` and *Costs* (`js/costs.js`), for administrators: each filesystem's price now and its
     schedule (scheduled ones marked), its free tiers for labs and departments, and exceptions, each with a "from"
     day; removing one already in effect warns that it changes the charges since then.
   - Tests: exact money and units; prices and free tiers on days before, during, and after each, scheduled ones,
     ended exceptions, and departments with none; refusals; removals; the audit; the currency setting.
   - *Done*: checked in the browser in both themes and at 375 pixels.
2. **Monthly charges (R8, Q1).**
   - `folder_owner` table and `ownership.py`: who owned each top-level folder when. A row each time a folder
     changes owner, on *Folders and labs* or by `pinax import`; a folder's first change also records who had it
     until then. A folder that has never changed is its current owner's throughout, so existing catalogs need no
     backfill.
   - `charges.py`: each lab's and department's charge on each filesystem for a month, from folder history (ready
     and pruned scans; failed ones never count). The month is cut at every scan, change of owner, price, and free
     tier, rather than day by day, so a change at 15:00 counts from 15:00. Exact fractions, rounded half up to the
     currency's minor unit once per lab and filesystem; totals add up to their lines. The month in progress is an
     estimate to its end; storage before the first scan is not charged, storage with no price is shown and marked
     unpriced, and unowned folders and the files in the root are unbilled.
   - `/api/admin/costs/charges?month=YYYY-MM`, for administrators: every lab's charges by filesystem and folder,
     the prices that applied and over which days, the free tiers, the unbilled storage, totals by filesystem, and
     the months there are (from the first scan's to now). The pages come in slices 3 and 4.
   - Tests: every lab's exact size, billable size, and charge equal to an independent hour-by-hour sum over a month
     with five scans (one pruned, one failed), a folder changing owner mid-month, a price change, a lab's own free
     tier starting and ending, a department with none, unowned folders, and files in the root; the month after; the
     estimate for the month in progress; months before the first scan and price; rounding (cents and whole units);
     ownership recorded by the app; administrators only.
3. **What labs pay (R8).**
   - Each line split into periods with one price and one free tier, so its charge reads worked out: "Sep 1 – Sep
     15: 43.668 TB on average, with no free tier, so 43.668 TB at $25.00 per TB per month, for 15 of 30 days:
     $545.84". Sizes in the unit the price is in, so the arithmetic reads straight.
   - `/api/charges?month=&lab=`, for anyone in a lab or department, whatever their role: their labs' lines, each
     lab's last twelve months, whether any price is set, and the months there are. Administrators may ask for any
     lab. A folder a lab was charged for but has since given up is listed and cannot be opened.
   - *Storage charges* (`js/charges.js`, in the sidebar for everyone): a month picker (and a lab picker for
     administrators); per lab, its total, each filesystem's charge, its periods and folders, and its past months;
     this month marked as an estimate to its end; a note when no prices are set. On *My data*, this month's
     charges across the person's labs, when a price is set.
   - Tests: members see only their labs (and 404 for others), an administrator any; a lab billed for a folder it
     has given up sees it but cannot open it; each period's charge is its billable size at its price for its share
     of the month, and the periods add up to the line; departments with none free; the month in progress, its
     history, and the months before.
   - *Done*: checked in the browser in both themes and at 375 pixels, with prices set on the demo and without.
4. **Chargeback for finance (R8).**
   - *Chargeback* (`js/charges.js`, under Administration): for a month, the total, how many labs and departments
     are charged, and the storage no one owns; totals by filesystem with the prices that applied; every lab and
     department, the most first, each leading to its own *Storage charges*; and the unbilled folders, leading to
     *Folders and labs*. Notes for a month in progress and for storage with no price.
   - The statement: `/api/admin/costs/charges?month=&fmt=csv`, a row for each lab or department on each filesystem
     and one for each filesystem's unbilled storage: average and charged size in bytes and in the price's unit
     (exact), the prices that applied, the charge, the currency, and whether the month is final or an estimate. The
     file is named for the month (`pinax-charges-2026-09.csv`, or `-estimate`).
   - Speed: sizes are summed in whole byte-microseconds and divided once, ownership is read as rows, and one lab's
     months read only the folders it ever had. On a year of weekly scans of 2,000 folders and 200 labs
     (`bench/bench_charges.py`), every lab for a month takes about 25 ms (49 ms before) and one lab's twelve
     months about 50 ms (566 ms before), within Q3's 100 ms; ten times as many folders take ten times as long
     (`docs/ARCHITECTURE.md`).
   - Tests: totals by filesystem equal their lines; the statement has a row for each line, its charges add up to
     the month's total, its sizes are exact in the price's unit, unbilled and unpriced rows, a month in progress
     marked as an estimate; administrators only.
   - *Done*: checked in the browser in both themes and at 375 pixels.
5. **Demo and release 0.5.0.**
   - `pinax seed` sets prices and free tiers (`seed.demo_costs`), dated from the day of the seed so every demo has
     the same shape: *Fast*'s price falls on the 16th of last month and the Lindqvist Lab's own free tier starts on
     the 10th, so last month's statement is worked out in parts; departments have nothing free; *Secure* is priced
     per TiB with nothing free. Set as the first storage administrator, so the audit log shows them.
   - The README describes the demo's prices and walks through *Storage charges* as a PI, then *Costs* and
     *Chargeback* as an administrator, including a folder mapped during the demo charged from that moment.
   - Version 0.5.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.4.0 (new tables created at start-up; ownership history starts with the upgrade).
   - Pull request to `main`, then tag `v0.5.0` and the release.

## 0.6.0: where storage is heading (wave 6)

How fast each filesystem, lab, and department is growing, how large it will be in a year, when each filesystem
fills, and what each lab will pay. It is worked out from folder history, which keeps every scan, so it needs no
production storage, and the demo's six months of scans give six months of trend.

### Wave 6 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Forecasts of size (R10, Q1) | For a filesystem, a lab or department, a person's home, and all data: the size each month for the next 12 months from the trend of the last six months of scans, with a range; at least six scans over eight weeks, or no forecast and the reason; after a large step (a cleanup, a transfer), the trend starts after it and says so; backtested: the forecast the same method gave three months earlier, against the size now; tested on synthetic histories with known growth, noise, steps, and gaps; within Q3 for a year of weekly scans of 2,000 folders | Done |
| 2 | Forecasts in History (R10) | The forecast drawn after the last scan, set apart from the measured columns (a dashed line and a lighter range), labeled as a projection; its growth per month and its backtest beside it; in the table view and CSV; both themes and 375 pixels; colors validated | Done |
| 3 | Capacity, and when a filesystem fills (R11) | Each filesystem's capacity, from a date on (storage is expanded): recorded by `pinax scan` from the mounted filesystem with each scan, or set by administrators; the overview shows each filesystem's size, capacity, share used, growth per month, and the month it fills, with its range, and flags one that fills soon; History draws the capacity; audited | Done |
| 4 | What labs will pay (R12) | Each lab's and department's charges for each of the next 12 months at its trend, per filesystem, with the prices and free tiers as scheduled (a price change already set applies from its date), and the range; on *Storage charges* for lab owners, and totals for all labs on *Chargeback*; tested against the charges worked out on the forecast sizes | Done |
| 5 | Demo and release 0.6.0 | Capacities for the demo's filesystems, one filling within the year; the README demo script; version 0.6.0, changelog (with what was merged since 0.5.0), pull request, tag, release | Done |

### Decisions for wave 6

- **A straight-line trend.** Weekly or monthly scans give too few points to tell growth curves apart, and a
  straight line is easy to explain to a PI. The trend is robust: the median of the slopes between pairs of scans,
  so one odd scan barely moves it, and the range comes from the confidence interval of that median, widened by the
  scans' scatter. A curve can be reconsidered once the pilot has a year of production scans.
- **Labs and departments are forecast from the folders they own now**, as History shows them, so a folder moving
  between labs does not look like growth.
- **A forecast says how good it is.** Its backtest is shown beside it, in keeping with trust first: the same method,
  run three months earlier, was off by a given share.
- **Capacity has two sources.** fs-inventory does not record a filesystem's size, but `pinax scan` runs where the
  filesystem is mounted and records what the storage reports for it (`statvfs`, which reads no file). Scans
  written by fs-inventory directly need the capacity set by an administrator, and a capacity set takes the place
  of what the storage reports from its day (a mount can show a quota or a shared pool).
- **What fills a filesystem** is the storage's own figure for space used once six scans have it, since it counts
  what scans cannot see (the storage's snapshots and overhead); else the scans' space on disk; else the files'
  size (the demo's sparse files have no space on disk).
- **Only capacities are stored.** Forecasts, fill dates, and charges ahead are worked out when asked.
- **Charges ahead are estimates**, worked out exactly on straight lines but in floating point, and rounded to the
  currency's unit; a lab without a trend on a filesystem is counted at its latest size there, and says why.

Settled within the wave:
- The horizon and the window (12 months ahead, from six months of scans) are constants, not settings, until the
  pilot gives a reason to change them; so are six scans over eight weeks and the three-month backtest.
- "Fills soon" is within `PINAX_FILL_WARNING_MONTHS` (12) at the trend's central estimate, a setting since storage
  teams plan purchases on different cycles.
- `pinax scan` writes `pinax.json` beside fs-inventory's manifest: fs-inventory's `verify` reads only its own
  tables, so the scan stays valid, and the record travels with the scan to whichever host ingests it.
- Divisions get no totals or forecasts of their own yet: a division is a label on labs, with no page. It stays
  open, for when divisions get one.

### Slices as built

1. **Forecasts of size (R10, Q1).**
   - `forecast.py`: Sen's slope through the scans of the last six months (`WINDOW_DAYS`), after the last step, carried
     forward from the latest scan to the same day of each of the next twelve months; the range from the slope's
     80% confidence interval (the ranks of the pairwise slopes, as in Kendall's test) widened by the scans' scatter
     about the trend; a step is a change between two scans off the usual one by more than a tenth of the size and
     by more than four times the usual spread; at least six scans over eight weeks; the backtest from the scans
     three months before the latest. Kept as constants rather than settings, until the pilot shows a reason.
   - `/api/history` returns the forecast with every history (`forecast`): for a filesystem, a top-level or deeper
     folder, a lab or department, a person's home, and all data; none for a place not in the latest scan.
   - Tests (`tests/test_forecast.py`): growth recovered exactly from a steady history and from irregular scans;
     one odd scan barely moves it; the range holds the outcome a year ahead in 75–97% of 300 noisy synthetic
     histories (86–90% when measured); a step starts the trend again, steady fast growth is not a step; too few
     scans, too short a time, and too soon after a step; the backtest; months of different lengths; and in
     History, a lab's forecast, a lab after a cleanup, a new folder, and all data.
   - *Done*: with `bench/bench_charges.py` (2,000 folders, a year of weekly scans), History with its forecast takes
     4.7 ms for a lab and 1.6 ms for all data; the benchmark now records each scan's whole-filesystem totals, as
     ingest does.
2. **Forecasts in History (R10).**
   - `js/timechart.js` draws a forecast after the last scan (`ahead`): a dashed line from the latest total over a
     shaded range, with a hit target for each month ahead, whose readout gives the size, the range, and the change
     from now; the chart's scale includes the range. The small charts stay on the scans.
   - *History*: *In a year* beside the other figures (or why there is no forecast); the forecast shown or hidden
     (`forecast=off`, kept in links); under the chart, what the forecast is and its backtest, or why there is none
     (too few scans, a step, not in the latest scan); in the table view, a table of the months ahead; the CSV adds
     them (`kind` is `scan` or `forecast`, with `low` and `high`).
   - Colors: the line is the strong ink (`--strong`, navy in light, white in dark), set apart from the measured
     age colors, at 11.7:1 and 15:1 against the chart's surface; the range is `--border`, a step from the surface
     in both themes. The dataviz validator's contrast check passes in both; its chroma check is for categorical
     palettes, which this is not.
   - *Done*: checked in the browser on the demo in both themes and at 375 pixels (no horizontal scroll): all data,
     a lab with a forecast, a lab after a step, the table view, and hovering a month ahead.
3. **Capacity, and when a filesystem fills (R11).**
   - `pinax scan` asks the mounted filesystem its total, used, and available space (`statvfs`) and writes them to
     `pinax.json` beside fs-inventory's manifest (fs-inventory's `verify` reads only its own tables, so it is
     untouched); ingest and rebuilds keep them with the snapshot (`info["storage"]`).
   - `capacity` table and `capacity.py`: a capacity from a day on, set by administrators in GB to PiB, audited;
     it takes the place of what the storage reports from its day, and a later day schedules an expansion. Use is
     measured by the storage's own figure once six scans have it, else space on disk, else size (scans with no
     space on disk); the forecast of it is carried forward day by day against the capacity as scheduled, for the
     day it fills and the earliest and latest within the range, up to three years ahead.
     `PINAX_FILL_WARNING_MONTHS` (12) flags one that fills soon.
   - *Capacity* (`js/capacity.js`, `/api/admin/capacity`): each filesystem's capacity and where it comes from, use,
     growth per month, when it fills, what the storage reported, and the schedule, with a form to set one. The
     overview's filesystem cards show the share used and when it fills, with a chip, and *Needs attention* lists
     those that fill soon or are full. A filesystem's *History* draws the capacity on *Size by age* where it fits
     the scale (at most twice the data), and shows *Fills*.
   - Tests (`tests/test_capacity.py`): no capacity until one is known; the day it fills against the trend worked
     out by hand; a scheduled expansion moving it; soon and full, and the setting; what the storage reports, used
     as the measure and as the capacity until one is set; size for scans without space on disk; changes checked
     and audited; the API for administrators only; `pinax scan`'s record kept by ingest; a filesystem's history
     carrying its capacity.
   - *Done*: checked in the browser with a capacity set on the demo's *Fast* (69% of 160 TiB used, filling about
     July 2029): *Capacity*, the overview, and *History*.
4. **What labs will pay (R12).**
   - `charges.charges_ahead`: for each lab or department on each filesystem, the size of the folders it owns now
     at their trend, less the free tier, at the price, for each stretch of the twelve calendar months after this
     one in which neither changes; the trend and its range are straight lines, so each stretch is the exact part of
     a line above the free tier. A day-by-day version gave the same charges to the cent and took four times as long.
     With no trend, at the latest size, saying why. Floating point, rounded to the currency's unit: estimates, not
     bills.
   - `/api/charges` returns each lab's (`ahead`), shown on *Storage charges* as *The next 12 months*: the total,
     its range, and each month's estimate and range; `/api/admin/costs/ahead` the totals for every lab
     (`ahead_totals`), shown on *Chargeback* once the month's charges are, so the statement does not wait for it.
   - With `bench/bench_charges.py` (2,000 folders, 200 labs, a year of weekly scans), on a laptop busy with other
     work (the month's charges for every lab took 103 ms, against 25 ms when it is idle): 15 ms for one lab's
     twelve months and 407 ms for every lab's, so about 100 ms when idle.
5. **Demo and release 0.6.0.**
   - `pinax seed` sets capacities (`seed.demo_capacity`), dated from the day of the seed: *Fast* holds 125 TiB, so
     at its trend it fills in about nine months and is flagged, a month or two before an expansion to 250 TiB
     scheduled eleven months on; *Secure* holds a tebibyte. The demo's scans record nothing about the storage (its
     files are sparse, on a laptop's disk), so use is measured by size.
   - `docs/DEMO.md`: the capacities, *In a year* and the forecast in `fujimoto_a`'s *History*, `bennett_k` with
     none after its step, *The next 12 months* on *Storage charges* and *Chargeback*, and *Capacity*.
   - Version 0.6.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.5.0 (index version 5, the `capacity` table created at start-up, `pinax.json` with new scans,
     the new settings, and oauth2-proxy's `upstream_timeout`).
   - Pull request to `main`, then tag `v0.6.0` and the release.
   - Tests (`tests/test_charges_ahead.py`): every month equal, to the cent, to a charge worked out hour by hour on
     the forecast sizes, with a price change scheduled ahead, and for a lab growing past its free tier during a
     month; a lab without a trend at its latest size; members see their own labs', administrators every lab's,
     the totals adding up.
   - *Done*: checked on the demo's *Storage charges* (`fujimoto_a`: about $8,239 over the next twelve months) and
     *Chargeback*.

## 0.7.0: cleanup plans (wave 7)

What a lab could free, and what it would save. While exploring the age map, a PI or delegate marks folders to
delete, move to cheaper storage, or compress, and sees the space it would free and how much the lab's monthly
charge would fall. Later scans show what was done and what it saved. Pinax still changes nothing on the storage it
reports on (P3): a plan records what a lab means to do, and the scans show what happened.

### Wave 7 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Marked folders, followed across scans (S10) | The catalog records a marked folder by filesystem and path; index version 6 keeps each directory's inode from the scan; at each ingest, every marked folder is found at its path, found at another path with the same inode (proposed as a move, not applied, since inodes are reused), or gone; its totals are recorded for that scan, so a marked folder's history outlasts its snapshot; tested with renames, moves, deletions, and a reused inode; the added build time and index size measured at 100 million files | Done |
| 2 | Marking folders for cleanup (C1) | From the age map (a folder's own panel and each tile's actions, reachable by keyboard), *Cold folders*, and *What changed*: add a folder to its lab's cleanup plan to delete, move to another filesystem, or compress, with a note; by the lab's PI and delegates, and by administrators; a folder inside one already planned counts once; the plan's total shown while exploring; audited; both themes and 375 pixels | Done |
| 3 | What a plan saves (C2) | For each folder and the whole plan: the space freed, and the lab's monthly charge now and after, per filesystem, with free tiers (data within a free tier saves nothing, and the page says so) and the destination's price for a move; compression estimated from typical ratios for each type, and labeled as an estimate; tested against the charges worked out on the reduced sizes | Done |
| 4 | Progress from scans (C3) | With each new scan, each folder is done (gone, and not moved), partly done (smaller), moved (no saving), or still to do; the space freed and the fall in the charge since the plan began; a *Cleanup plan* page for each lab with what is to do and what was done, and when; CSV for the lab or the storage team | Done |
| 5 | Suggestions (C4) | Folders worth adding, each with its saving, added in one click: cold folders (R3), uncompressed sequencing data (R6) to compress, and workflow working folders (Nextflow `work/`, Snakemake `.snakemake/`, Cromwell `cromwell-executions/`, conda and container caches), found when the index is built; within Q3 for a lab folder at 100 million files | Done |
| 6 | Plans across labs (C5) | For administrators: every lab's plan, planned and freed per filesystem, the largest folders planned, and a CSV | Done |
| 7 | Demo and release 0.7.0 | A demo lab with a plan made before the demo's last scan, partly done by it, with one folder renamed rather than removed; the README demo script; version 0.7.0, changelog, pull request, tag, release | Done |

### Decisions for wave 7

- **A plan is not made of tags.** A planned folder has an action, an estimate, and a state that changes as scans
  arrive; tags (wave 8) describe what a folder is, and last. Both rest on the same marked folders.
- **Folders, not files.** The space is in folders, and at two billion files a plan of single files would make
  every plan page a file-level query.
- **A saving is the fall in the lab's charge**, not size times price: a lab within its free tier saves nothing by
  deleting.
- **Done means the scans show it gone.** A folder that was renamed or moved, its inode found at another path, saved
  nothing. Wave 3 matched folders by path and left inodes for later; every fs-inventory scan records them, and
  Pinax starts reading them here.
- **What a plan page warns about.** Storage snapshots keep deleted data on disk until they expire, so capacity
  comes back later than the charge falls; and a hard-linked file (R7) frees nothing until its last link is gone.

Settled with the maintainer when the wave started:
- A lab's delegates add folders to its plan directly, as its PI does; each folder shows who added it, and every
  change is audited. Pinax deletes, moves, and compresses nothing: a plan records what a lab means to do.
- Compression is in the first version, its saving estimated from typical ratios for each type, labeled as an
  estimate.

Settled within the wave:
- A move names the filesystem it goes to (whose price sets the saving), with a note for where on it.
- Plan actions are kept open, so an *archive* action can join them (see *Archiving with Tundra* in the parking lot).
- The build time and index size of the inodes were measured at a million files rather than 100 million, keeping
  to small datasets on the laptop; the pilot measures them at production size.

Open within the wave: the compression ratios for each type (FASTQ, SAM, BAM to CRAM), from published figures and
the site's own.

### Slices as built

1. **Marked folders, followed across scans (S10).**
   - Index version 6 keeps each directory's device and inode in `dirs` (from the scan's `dev` and `ino`; none for a
     scan without them). `SnapshotIndex.nodes` finds directories by path with their inode, and `paths_of` by
     inode, in one pass over the folders.
   - `marked_folder` and `marked_folder_stat` tables, and `marks.py`: a mark is made on a folder in the latest
     scan, with its history from the snapshots kept in full; at each ingest, every mark of the filesystem is found
     at its path (*here*), at another path with its inode (*moved*, to confirm or dismiss, audited), or *gone*, and
     its totals recorded; only the latest scan moves a mark.
   - Tests (`tests/test_marks.py`): the index's inodes; a mark's first totals; a folder renamed, one deleted whose
     inode went to a new folder (offered, not applied), one unchanged, one smaller; confirming and dismissing a
     move, and following the confirmed path after; an older scan found late; history after the indexes are
     pruned; an index without inodes. `tests/scanfix.py` can give directories inodes of their own.
   - *Done*: on a million-file synthetic scan (244,000 folders), the build took 10 s on a busy laptop, of which
     reading the directories with their inodes 0.17 s and writing the directory table 0.6 s; the inodes take
     at most four 256 KiB blocks of the 126 MB index (the index file is the same size as at version 5); finding
     1,000 marked folders by path takes 25 ms, and 100 by inode 4 ms.
2. **Marking folders for cleanup (C1).**
   - `cleanup_item` table and `plans.py`: a folder in the plan of the lab that owns its top-level folder, to
     delete, move to another (active) filesystem, or compress, with a note, who added it, and its size then (from
     its mark); added by the lab's PI and delegates directly, or by administrators; others' folders answer 404, and
     a folder no lab owns has no plan; a folder inside another planned one is counted once, in the outermost; every
     addition, change, and removal audited.
   - `/api/plans` (a person's labs' plans; any lab's for administrators), `/api/plans/here` (a folder's place in its
     lab's plan, for the age map), adding, changing, and taking out, and confirming or dismissing a move.
   - The age map's panel has a *Cleanup plan* box: the folder's action and who added it, the folder it is inside
     if that is planned, or *Add to cleanup plan* (a dialog: delete, move to a filesystem, or compress, and a
     note), with the lab's plan so far. *Cleanup plans* (`js/plans.js`, in the sidebar) lists each lab's folders:
     what to do, size when added and now, who added them, a moved folder to confirm or dismiss, and changing or
     taking one out. Adding from a tile is a click into it and its panel; adding straight from *Cold folders*
     comes with the suggestions (slice 5).
   - Tests (`tests/test_plans.py`): a PI adds a folder, once; a delegate adds directly; no one plans others'
     folders, the root, a path with `..`, a folder not in the scan, or a folder no lab owns; moves name another
     filesystem; nested folders counted once, and the age map's view of it; changing and removing, by whom, and
     audited; a folder that has not moved cannot be confirmed moved.
   - *Done*: checked in the browser as a PI on the demo (adding from the panel, the panel after, *Cleanup plans*).
3. **What a plan saves (C2).**
   - `plans.savings`: for each filesystem a plan touches, the lab's size there in the latest scan, and its monthly
     charge at today's price and free tier before and after (`plans.monthly`, as charges work it out); freed by a
     delete or a move, added by a move on its destination, estimated for compression from the folder's types
     (`plans.COMPRESSION`); each folder's saving on its own; the plan's monthly and yearly saving, worked out
     exactly and rounded once.
   - *Cleanup plans* shows the saving per folder (*about*, for an estimate), per filesystem (the charge now and
     after, or that the lab is within its free tier there), and for the plan, with its yearly figure; the age map's
     box shows the plan's saving.
   - Tests: the charge now equals the charges' own for a month after the scan, and the charge after equals the
     charges' own once a later scan shows the folder deleted; within the free tier nothing is saved; compression
     estimated from a SAM beside an already gzipped FASTQ; a move freeing on one filesystem and charged on the
     other; nested folders counted once.
   - *Done*: checked on the demo (a 10.4 GiB folder at $22 per TB saves $0.25 a month).
4. **Progress from scans (C3).**
   - `plans.progress`: from the marked folder's totals in the scans after it was added: done (gone and not moved;
     or, for compression, shrunk by nine tenths of the estimate kept when it was added), partly done (smaller),
     moved (nothing freed), or to do; the space freed and the scan that showed it. The plan's *done so far*: the
     space freed, and what it saves a month now (what it would cost back, less a moved folder's cost where it
     went). What a plan would still save leaves out what is done.
   - *Cleanup plans*: *To do* (with partly done folders and moves to confirm) and *Done* (what was done, freed, and
     by which scan), the plan's *done so far*, and a CSV per plan.
   - Tests: a folder deleted and one compressed fully by a later scan, done, with what they free and save, and
     nothing left to save; one compressed partly; one renamed, which freed nothing. Later test scans give their
     directories stable inodes, since numbering them by position hands a deleted folder's inode to another (the
     reuse that makes a move something to confirm).
   - *Done*: checked on the demo's *Cleanup plans* (a folder to do; progress waits for the demo's scans, slice 7).
5. **Suggestions (C4).**
   - Index version 6 (unreleased, so not bumped again) gains `work_dirs`: Nextflow `work/` beside a `.nextflow`,
     Snakemake `.snakemake/`, Cromwell `cromwell-executions/`, conda `pkgs/` in a conda installation, and
     Singularity or Apptainer `cache/`; `SnapshotIndex.work_dirs` lists the outermost in a folder, and
     `compressible` the folders with the most files of given types directly in them.
   - `plans.suggestions` and `/api/plans/suggestions`: for each folder a lab owns, its workflow folders (to delete),
     largest cold folders at the cold threshold (to move, or delete with one filesystem), and folders with the most
     uncompressed sequencing data (to compress, estimated), each with what it frees and saves a month on its own;
     planned folders and those inside them left out; a folder suggested twice kept for the larger saving; one inside
     another suggested folder left to it.
   - *Cleanup plans* shows them under each plan, loaded after it; *Add* opens the dialog with the action chosen.
     Adding from *Cold folders* itself is left out: its folders are among the suggestions.
   - Tests: the workflow folders the index finds (and a `work` folder that is not Nextflow's); the suggestions for a
     lab, with a FASTQ to compress, a cold folder, a cold SAM suggested for the larger saving, compression inside a
     work folder left to it, planned folders no longer suggested, and other labs refused.
   - *Done*: on a million-file synthetic scan, finding workflow folders added 0.01 s to the build, and the three
     queries took 8, 38, and 9 ms for a top-level folder of 5,000 files; 100 million files wait for the pilot.
6. **Plans across labs (C5).**
   - `plans.overview` and `/api/plans/all` (administrators only): every lab's plan, what all of them would free and
     save and have done, per filesystem what is to be freed, moved in, and freed so far, and the 20 largest folders
     still to do.
   - *Cleanup plans* for an administrator without a lab chosen: those figures, a table each, a link to each lab's
     plan, and one CSV of every folder.
   - Test: an administrator sees two labs' plans, a folder done, a compression and a move to do, by filesystem and
     largest first; others are refused.
   - *Done*: checked in the browser as an administrator on the demo.
7. **Demo and release 0.7.0.**
   - `pinax seed` makes the Dasgupta Lab's cleanup plan after the fifth scan (`seed.DEMO_PLAN`, by its PI): three
     folders to delete. Month 6 deletes one (`2016_cohort_A`, done) and renames another (`manuscripts/2021_draft`
     to `2021_published`, found by its inode, since the demo's scans are the real fs-inventory's: offered as
     moved), and leaves the third to do.
   - `docs/DEMO.md`: the plan in the months table and a walk-through step as Rohan Dasgupta; the README describes
     cleanup plans.
   - Version 0.7.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.6.0 (index version 6, three catalog tables).
   - Pull request to `main`, then tag `v0.7.0` and the release.

## 0.8.0: folder tags (wave 8)

Folders described in terms a lab and IT share: the project, the grant, the instrument, the stage of the data, how
sensitive it is, and how long it must be kept. Administrators keep a list of standard tags; data owners tag their
folders with them, or with tags of their own; people find data by tag across filesystems, see what it costs, and
split their charges by grant.

### Wave 8 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | The tag list (T1) | Standard tags kept by administrators on a *Tags* page: added, changed, and retired (a tag in use is retired, not deleted); each with a type (a flag, text, a date, or a choice from a list), who may apply it (owners, or administrators only), and whether it protects a folder from cleanup; the default list below at start-up, for a site to change; audited | Done |
| 2 | Tagging folders (T2) | From the age map's folder panel and tile actions: a folder's own tags, and those it inherits from the folders above it; a lab's PI and delegates tag its folders, administrators any folder; a lab makes custom tags of its own, seen by its members and administrators; administrators merge a custom tag into a standard one; `pinax import` takes tags in bulk; tiles that are or hold tagged folders are marked, with their tags in tooltips and the table view; audited; both themes and 375 pixels | Done |
| 3 | Finding data by tag (T3) | A *Collections* page: folders by tag and value, combined (Source is cryo-EM and Data stage is raw), across filesystems, within what the person may see; each folder's size, files, cold share, average age, monthly cost, owner, and tags; nested folders counted once in the total; the collection's history and forecast from the recorded totals (waves 6 and 7); CSV; within Q3, reading only the catalog | Done |
| 4 | Cost by tag (T4) | Charges split by a tag's values, such as by grant or project: each tagged folder's share of its lab's charge on its filesystem, time-weighted from the recorded totals, and what is untagged; on *Storage charges* and in the statement CSV; tested against brute force | Done |
| 5 | Protection (T5) | A folder with a protecting tag (*Keep until*, before its date; *Legal hold*), and everything below it, cannot be added to a cleanup plan; folders already planned are flagged; suggestions leave them out | Done |
| 6 | Demo and release 0.8.0 | Demo folders with standard and custom tags, charges split by grant, and a folder on legal hold; the README demo script; version 0.8.0, changelog, pull request, tag, release | Done |

The default tags, which a site changes on the *Tags* page:

| Tag | Type | Applied by | Why |
| --- | --- | --- | --- |
| Project | Text, more than one | Owners | Finding a project's data wherever it is |
| Grant or award | Text, more than one | Owners | Splitting charges by grant (T4) |
| Data stage | Raw, processed, results, reference, or scratch | Owners | Raw data is often the copy that must be kept; scratch is the first to clean up |
| Source | Sequencing, mass spectrometry, cryo-EM, light microscopy, medical imaging, flow cytometry, simulation, or downloaded | Owners | Where growth comes from; a site adds its own instruments |
| Data classification | The site's levels | Administrators, or owners if the site allows | Where sensitive data is, and whether it is on the right filesystem |
| IRB protocol | Text | Owners | Data from human subjects research |
| Shared in a repository | Text: an accession (GEO, SRA, PRIDE, EMPIAR, …) | Owners | A public copy exists |
| Keep until | Date | Owners | Protects from cleanup until the date |
| Legal hold | Flag | Administrators | Protects from cleanup |

### Decisions for wave 8

- **Tags are on folders and apply to everything below them**, not on files (see wave 7).
- **Standard tags have types**, so their values compare: a date is a date, and a choice comes from its list, which
  is the site's own. Custom tags exist because each lab has its own words; merging them into standard tags keeps
  the list from multiplying.
- **A tag is seen by whoever can see its folder**; a custom tag's name is seen only by its lab and administrators.
  People find only data they may see, so a search across the whole organization is for administrators.
- **Collections read only the catalog**, from the totals recorded for marked folders at each ingest (wave 7), so
  finding data by tag takes milliseconds however many filesystems it spans.

Settled when the wave started:
- *Data classification* is applied by administrators by default; each tag's *applied by* is the site's to change.
- New top-level folders need no tags: required tags wait for a site that wants them.
- A folder cannot drop a tag it inherits; the tag is taken off the folder that has it.

### Slices as built

1. **The tag list (T1).**
   - `tag` and `folder_tag` tables, and `tags.py`: standard tags (no lab) and labs' own; a type (flag, text, date,
     choice with its list), more than one value or not, applied by owners or administrators, and protecting (a flag
     or a date); values checked by type. The default list is added by `init_db` when the catalog has no standard tag
     at all, so a site's removals stay. Adding, changing (not the type; not taking away a choice in use), retiring
     and bringing back, and deleting an unused tag, each audited.
   - `/api/admin/tags` and *Tags* (`js/tags.js`, under *Administration*): the standard tags and labs' own, with how
     many folders have each.
   - Tests (`tests/test_tags.py`): the default list, added once; adding and changing, and what is refused; a tag in
     use retired, not deleted, and its choices kept; administrators only; values by type.
   - *Done*: checked in the browser on the demo.
2. **Tagging folders (T2).**
   - `tags.apply` and `untag`: a lab's PI and delegates (and administrators) put a standard tag applied by owners,
     or the lab's own tag, on its folders; administrators any tag on any folder; a retired tag on none. A one-value
     tag replaces the value; the same value twice is refused. A folder's tags are its own and its ancestors'
     (`tags.effective`). `make_custom` (a lab's own flag or text tag) and `merge` (into a standard tag, retiring
     the lab's). Each audited.
   - `/api/tags/here`, `/below`, `/folders`, `/custom`, and `/api/admin/tags/{id}/merge`. The age map's panel has a
     *Tags* box (own tags with *take it off*, inherited ones from the folder above, *Add tag* with a value field
     for the tag's type, and a tag of the lab's own); tiles that are or hold tagged folders carry a dot, and their
     tags are in the tooltip, the accessible label, and the table view.
   - `pinax tags import FILE [--apply]`: a CSV of filesystem, path, tag, value (`lab:key` for a lab's own), a dry
     run first, and nothing changed if any row is refused. It is a command of its own rather than part of
     `pinax import`, whose plan of labs, people, and folders it does not need.
   - Tests: a PI's tag, inherited below; more than one value; who may tag what; a one-value tag replaced; a retired
     tag; a lab's own tag, other labs refused, and merging it; untagging; the audit; the command line's dry run,
     refusal, and apply.
   - *Done*: checked in the browser on the demo (a tag added from the panel; the tile's dot and tooltip).
3. **Finding data by tag (T3).**
   - `tags.collection`: the folders with every wanted tag (a value, or any), own or inherited, that the person may
     see; the outermost of them; each with its latest recorded totals, owner, tags, and monthly cost (its share of
     its lab's charge, by size); the total; the history from the recorded totals, one point per day with a scan;
     and its forecast. `tags.filters`: the tags a person can look for, with the values on folders they may see.
   - `/api/tags/collection` and `/api/tags/filters`; *Collections* (`js/collections.js`, under *Overview*):
     conditions kept in the address, the figures, size over time with the forecast, the folders, and a CSV.
   - Tests: the outermost folders with a tag; tags combined and inherited; any value; only what the person may see;
     the values to look for; the monthly cost as a share of the lab's charge; the history after a later scan.
   - *Done*: `bench/bench_tags.py` (2,000 tagged folders of 200 labs, half a year of weekly totals): 60–70 ms for a
     collection of 34 folders, 85–95 ms for 100, about 0.6 s for all 2,000, after reading each table once rather
     than per folder (the first version took 1 s for 100 and 18 s for 2,000). Checked in the browser on the demo.
4. **Cost by tag (T4).**
   - `charges.split_by_tag`: a lab's month on each filesystem, split by a tag's values: each value's share of the
     lab's charge is its folders' time-weighted average size (from the totals recorded with each scan, each holding
     until the next) over the lab's; a folder's bytes go to its nearest tagged folder; several values split evenly;
     the rest untagged.
   - `/api/charges/split` (a lab's members and administrators), with the tags its folders have to split by, the grant
     first. *Storage charges* shows it for each lab, loaded after the month, with a CSV of its own; the statement
     for finance is unchanged, since a split by tag is the lab's view of its own charge.
   - Tests (`tests/test_charges_split.py`): equal, to the cent, to brute force hour by hour through a month of weekly
     scans, with nested tagged folders, a folder with two values, and another lab's tagged folder left out; other
     labs refused.
   - *Done*: checked in the browser on the demo (a grant on a lab's sequencing folder).
5. **Protection (T5).**
   - `tags.protection`: a protecting tag in effect (a flag; a date not yet past) on the folder, above it, or below
     it, with why. `plans.add` refuses such a folder (a folder that holds a protected one would take it too);
     planned folders carry `protected`; `plans.here` says why in the age map's box; suggestions skip them.
   - *Cleanup plans* flags a folder protected since it was planned.
   - Tests: a folder kept until a later date, one inside it, and one holding it refused, one beside it not; a date
     past protects nothing; legal hold; a planned folder protected later is flagged; suggestions leave a protected
     cold folder out.
6. **Demo and release 0.8.0.**
   - `pinax seed` tags demo folders after the last scan (`seed.DEMO_TAGS`, each by the person who would): the
     Fujimoto Lab's two projects with their grants and the lab's own *Atlas paper*, its 2018 pilot kept until 2030,
     the Lindqvist Lab's sequencing with a grant and its tumor-normal study raw, restricted, and under an IRB
     protocol, the Genomics Shared Resource's runs raw sequencing, and the Kowalski Lab's 2016 phosphoproteome on
     legal hold.
   - `docs/DEMO.md`: a walk-through step for tags, the grant split, and a collection; the README describes tags.
   - Version 0.8.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.7.0 (two catalog tables and the default tags, at start-up).
   - Pull request to `main`, then tag `v0.8.0` and the release.

## 0.9.0: action (wave 9)

What is left of action that needs neither production storage nor a system outside Pinax: totals that count a
hard-linked file once, views a person saves and shares with their labs, and searches of a whole filesystem that run in
the background rather than within a page's time limit.

### Wave 9 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Hard-link aware totals (R7, Q1) | A file with several hard links counts its size once, at its first link by path (of its device and inode); its other links count as files with no size, in every total (folders, ages, types, owners, charges); each folder records its extra links and what they would have counted again; index version 7; `pinax check` still equal to `fs-inventory rollup`, run on a copy of the scan whose extra links have no size; tested with links within a folder, across folders, and across top-level folders | Done |
| 2 | Saved and shared views (E9) | A person saves the page they are on (a report, its place, and its settings) under a name, keeps it to themselves or shares it with one of their labs, and opens, renames, or deletes it; *My data* and *All data* list them; a shared view shows only what each member may see; audited | Done |
| 3 | Searches as background jobs (R13) | A search of a whole filesystem can run in the background, beyond a page's time limit: at most `PINAX_WIDE_QUERIES` at once, each within `PINAX_JOB_TIMEOUT`; its results kept for `PINAX_JOB_DAYS`, listed on a *Searches* page with their CSV; a job left running when Pinax stops is marked as stopped; only its owner and administrators see it | Done |
| 4 | Demo and release 0.9.0 | Hard links in the demo, a saved and a shared view, and a background search; the README demo script; version 0.9.0, changelog, pull request, tag, release | Done |

### Decisions for wave 9

- **A hard-linked file counts once, at its first link by path.** Its other links are still files (each is a name a
  person sees), so file counts are unchanged; only sizes count once. *Find files* lists every link, with the file's
  size, as a listing of names does.
- **The check stays independent.** `fs-inventory rollup` counts every link, so `pinax check` gives it a copy of
  the scan in which every link after the first has no size; Pinax's rule for which link is first is written again
  there, not shared.
- **Charges follow the totals**: storage held in hard links is charged once, to the folder of its first link.

### Slices as built

1. **Hard-link aware totals (R7, Q1).**
   - The build finds every link of a file after its first (by path, per device and inode) among files with more
     than one link (`extra_links`), and totals the files as counted: such a link has no size or space on disk in
     the directory, type, owner, and part totals; the per-file Parquet keeps every link with its size. `hl_files`
     and `hl_bytes` are rolled up with the other measures; index version 7. `SnapshotIndex.links` reads them (the
     other queries leave them out, so older indexes answer as before), and the age map's panel shows them.
   - `pinax check` runs rollup on a copy of a scan with hard-linked files whose extra links have no size, with the
     shadow's manifest listing that one part (rollup reads the parts a manifest lists); the rule is written again in
     `crosscheck.py`.
   - Tests (`tests/test_hardlinks.py`): every directory against brute force, with a link beside its file and one in
     another lab's folder; what links hold, and types counting once; a scan without links unchanged; with the
     binary, `pinax check` equal to rollup on the linked scan. Test scans can give files an inode and link count.
   - *Done*: on the million-file synthetic scan (no links), finding links took 2 ms of a 9.1 s build.
2. **Saved and shared views (E9).**
   - `saved_view` table and `views.py`: a name, a page of the app (checked to be one), and a lab it is shared with
     (one of the person's own); a person sees their own and those shared with their labs; only whoever saved one
     renames, shares, or deletes it; audited. `/api/views`.
   - *Save view* in every report's header and on *Collections* (one listener for the page, so it works on whatever
     page is shown); *My data* and *All data* list the views, with who sees each, a choice to share, and deleting.
   - Tests (`tests/test_views.py`): kept to oneself until shared, seen by the lab, unshared; only one's own labs and
     pages of Pinax; only whoever saved a view changes it; the audit.
   - *Done*: checked in the browser (saving an age map view, listed on *All data*).
3. **Searches as background jobs (R13).**
   - `search_job` table and `jobs.py`: a search checked as a page would check it, then queued with the scans the
     person saw; worker threads (as many as `PINAX_WIDE_QUERIES`, and the same turns as pages) run it within
     `PINAX_JOB_TIMEOUT` and write every match in order (`SnapshotIndex.files_to`) to Parquet under
     `VAR_ROOT/jobs`; stopping and deleting; at start-up, jobs left queued or running are marked stopped and results
     older than `PINAX_JOB_DAYS` deleted.
   - `/api/files/jobs`: start, list (one's own; every one's for administrators), one with a page of its results, its
     CSV (written once), and stopping or deleting. *Find files* offers *Run in the background* for a search of a
     whole filesystem (administrators); *Searches* follows them while they run.
   - Tests (`tests/test_jobs.py`): a search to the end, its results in order, and its CSV; who may search and see;
     deleting deletes its results; searches left running are stopped at start-up; one that runs too long is stopped
     at its limit; an unknown sort refused.
   - *Done*: checked in the browser on the demo (every BAM on *Fast*: 274 files, 20.3 TiB).
4. **Demo and release 0.9.0.**
   - `pinax seed` hard-links four of the core's FASTQ files into the Lindqvist Lab's tumor-normal study
     (`seed.DEMO_LINKS`, counted once at the core, whose path sorts first), saves a view for Aiko Fujimoto shared
     with her lab and one of Priya Natarajan's (`seed.DEMO_VIEWS`), and runs a search of all of *Fast* for BAM files
     in the background, waiting for it (`jobs.submit(..., wait=True)`).
   - `docs/DEMO.md`: the saved view, the search, and the hard links; the README describes them.
   - Version 0.9.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.8.0 (index version 7, two catalog tables, two settings).
   - Pull request to `main`, then tag `v0.9.0` and the release.

## 0.10.0: operations (wave 10)

What a production deployment needs from Pinax itself, before it has many users: a catalog on PostgreSQL that
migrations upgrade in place (SQLite stays the default, with nothing to install), logs of what the system did and of
who changed what, and metrics that a site's monitoring can trend and alert on. None of it needs production storage.

### Wave 10 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | The catalog on PostgreSQL, with migrations (P7) | Migrations (Alembic) create and upgrade the catalog on SQLite and PostgreSQL, replacing the columns added at start-up; a catalog from 0.9.0 or earlier is adopted in place; `pinax db status` and `pinax db upgrade`; the PostgreSQL driver is an optional extra; the whole test suite passes on both, PostgreSQL in a container; the migrations' schema equals the models' on both | Done |
| 2 | Moving a catalog to PostgreSQL (P7) | `pinax db copy` copies every table of one catalog into another, empty one at the same version, in batches, then sets PostgreSQL's sequences and checks every table's count; a demo catalog copied to PostgreSQL serves the demo the same | Done |
| 3 | System and audit logs (O5) | Logs as text (the journal) or JSON lines (a log collector), to standard error or a file that `logrotate` can rotate; one line per request with a request ID (also returned to the browser), the person, their address, the route, the status, and the time taken; slow requests flagged; every audit event also logged once its change is committed, optionally to a file of its own; the address of the person, not the proxy, behind a trusted proxy; a start-up line with the version and the catalog (never its password); never a password, token, or session | Done |
| 4 | Prometheus metrics (O4, O5) | `/metrics` in Prometheus's format, off until a token is set and then only with it; scans and ingests by filesystem (when each last succeeded and failed, how long it took, the watcher's heartbeat), filesystem totals and capacity, Pinax's own disk, requests by route and their times, stopped queries, whole-filesystem query turns, background searches, sign-ins; filesystems named, never labs, people, or paths; a scrape configuration and alert rules in `deploy/prometheus/` | Done |
| 5 | Demo and release 0.10.0 | The demo unchanged on SQLite, and checked once on PostgreSQL; the deployment guide's PostgreSQL, logs, and monitoring sections; version 0.10.0, changelog, pull request, tag, release | Done |

### Decisions for wave 10

- **SQLite stays the default**, with nothing to install or run; PostgreSQL is for production, with its driver as an
  optional extra (`uv sync --extra postgres`). Both are tested with the whole suite.
- **Migrations with Alembic**, shipped inside the package. They run at start-up, as new tables and columns did
  before, unless `PINAX_DB_AUTO_UPGRADE=false`, for sites whose database administrators apply changes themselves
  (`pinax db upgrade`); a catalog that is behind then stops Pinax from starting, and says why.
- **Still one Pinax server.** Indexes and background searches live on its disk. PostgreSQL brings a database that
  IT runs, backs up, and replicates, and writers that do not wait on one file's lock.
- **Metrics name filesystems only**, never labs, people, or paths: Pinax's own pages trend labs, for those who may
  see them. They are off until `PINAX_METRICS_TOKEN` is set.
- **Audit events stay in the catalog**, where Pinax's pages read them; the log gets a copy of each once its change is
  committed, for a site's log collector or security monitoring.
- **Sign-in with Microsoft Entra ID in Pinax itself**, with OpenID Connect, rather than behind an authenticating
  proxy, is for reach (wave 13), with roles from Entra ID groups or app roles. Header mode behind a proxy stays, for
  other identity providers.

### Slices as built

1. **The catalog on PostgreSQL, with migrations (P7).**
   - Alembic, inside the package (`src/pinax/migrations`, no `alembic.ini`), run by `migrate.py`. The first revision
     (`0001`) writes out the catalog as 0.9.0 left it rather than taking it from the models, so it stays fixed: on
     a new catalog it creates every table, and on one made before migrations it adds the tables and columns that
     catalog lacks, as start-up did until now (`add_missing_columns` moved there). Constraints and indexes are named
     by a rule (`db.NAMING`). `pinax db revision` writes the next revision from the models' changes, for developers.
   - `init_db` upgrades when Pinax starts, unless `PINAX_DB_AUTO_UPGRADE=false`; then a catalog that is behind
     stops Pinax with a message naming `pinax db upgrade`. Processes take turns: `pg_advisory_xact_lock` on
     PostgreSQL, and on SQLite a lock file beside the catalog, held until the upgrade is committed. A catalog at a
     revision this Pinax does not know is refused. `pinax db status`, `pinax db upgrade`, and `--sql` (Alembic's
     offline mode, for a database administrator).
   - PostgreSQL through psycopg 3, an optional extra (`postgres`); `postgresql://` URLs get that driver; a pool of
     `PINAX_DATABASE_POOL_SIZE` connections per process, named `pinax` in `pg_stat_activity`; a message naming the
     extra when the driver is missing. `pinax seed` resets either database (`db.reset_catalog`).
   - Found on the way: two processes turning a new SQLite catalog to WAL at once could fail with "database is
     locked", since SQLite does not wait for that; they now try again for a few seconds.
   - Tests (`tests/test_migrations.py`): the migrations make the models' schema (Alembic's comparison, empty), a
     0.3.0 catalog adopted, a newer catalog refused, upgrading left to an administrator (`db status`, `--sql`,
     `upgrade`), two processes at once, and a new revision holding what the catalog lacks. With
     `PINAX_TEST_DATABASE_URL`, every test runs in a schema of its own in PostgreSQL.
   - *Done*: the whole suite on PostgreSQL 16 in a container (295 passed, the one about SQLite catalogs skipped, in
     3.5 minutes) and on SQLite; the models needed no change for PostgreSQL. The demo seeded into PostgreSQL (34,854
     files, the same as on SQLite) and served the same.
2. **Moving a catalog to PostgreSQL (P7).**
   - `dbcopy.py`, `pinax db copy TARGET [--from SOURCE]`: the source must be up to date; the target is brought to
     the same revision (its tables made if it is empty) and must hold no rows. Every table is copied in the order
     its foreign keys need, 5,000 rows at a time, in one transaction, so a failed copy leaves the target empty;
     on PostgreSQL each table's id sequence is then set past the copied ids. Every table's count is compared with
     the source's, which also catches a Pinax left running.
   - Tests (`tests/test_dbcopy.py`): the test catalog copied row for row into SQLite and, on PostgreSQL runs, from
     there into PostgreSQL, with new rows taking the next ids; only into an empty, different database; the command.
   - *Done*: a copy of the 0.9.0 demo catalog (made before migrations) was refused until `pinax db upgrade` adopted
     it, then copied into PostgreSQL (358 rows in 18 tables); served from PostgreSQL, eight of the API's reports
     answered exactly as the SQLite demo did.
3. **System and audit logs (O5).**
   - `logs.py`. `configure` sets up every command's logs (replacing `basicConfig`): text with the request's
     context, or JSON lines; standard error or `PINAX_LOG_FILE` (a `WatchedFileHandler`, so `logrotate` needs no
     signal; its folder is made, and a file Pinax cannot write is a plain error). `pinax serve` gives uvicorn no
     logging of its own and no access log.
   - `RequestLog`, the outermost middleware: a request ID (a trusted proxy's `X-Request-ID`, or a new one),
     returned as `X-Request-ID`; the person's address (`client_address`: behind `PINAX_TRUSTED_PROXIES`, the nearest
     untrusted hop in `X-Forwarded-For`, now also used for sign-in sessions and their audit, and uvicorn's own
     proxy headers turned off so the sign-on header's check sees the proxy); the person, from `auth`; and each
     index query's time, from `limits.guard`. One line per request to the API with the route's template
     (`route_of`, since included routers know only their part of the path), at warning when slow or failed;
     pages, scripts, and `/api/health` at debug.
   - Audit: SQLAlchemy session events collect each `AuditEvent` at flush and log it after commit (forgotten on
     rollback), with the request's context, or the command line's account; `PINAX_AUDIT_LOG` writes them to a file
     of their own as JSON lines. A start-up line names the version, sign-in, and catalog (without its password).
   - `deploy/logrotate/pinax`, and `LogsDirectory=pinax` in the systemd units.
   - Found on the way: since wave 8, the age map of a whole filesystem asked for its tagged folders without a path
     and was refused (422), so its top-level tiles lost their tag marks; the path now defaults to the root.
   - Tests (`tests/test_logs.py`): a line per request with its route, status, person, request ID, and index
     queries, and without the query string; routes as templates; the load balancer's checks and static files
     quiet; slow requests a warning; another's request ID refused; the person's address behind proxies; audit
     events logged once committed, never when rolled back, and from the command line with its account; JSON lines
     and an audit file; text lines with the context; a password, wrong or right, and the session token never
     logged.
4. **Prometheus metrics (O4, O5).**
   - `metrics.py`, with `prometheus-client` and a registry of Pinax's own (with the process, platform, and garbage
     collector figures, and no `_created` series). `/metrics` (`main.py`) answers 404 until `PINAX_METRICS_TOKEN` is
     set, then only a request with that bearer token (compared in constant time); a token under 16 characters stops
     Pinax from starting.
   - From the catalog, at most every 15 seconds (`CatalogCollector`): for each active filesystem, its newest
     ingested scan's time, ingest time, durations, size, space on disk, files, folders, cold data, and scan errors;
     its newest failed or skipped scan's time; snapshots by state; the size of its kept indexes; its capacity, use,
     growth, and fill dates (at the trend and at the fast end of its range, from `capacity.summary`); the watcher's
     heartbeat; searches in the background, people, labs, and unexpired sessions; Pinax's own disk; and whether the
     catalog could be read.
   - From the web app: requests by method, route template, and status, and their times (a histogram, from
     `RequestLog`); index queries and their time; stopped queries by reason; whole-filesystem query turns running
     and waiting (`limits.wide_state`); background searches finished; audit events by action.
   - `deploy/prometheus/`: a scrape job (bearer token from a file) and 13 alert rules (Pinax or its catalog down, a
     scan not ingested, no new scan for eight days, automatic ingest stopped, a filesystem filling within 90 days,
     could fill within a year, or 90% full, Pinax's own disk, slow pages, server errors, searches turned away,
     failed sign-ins). oauth2-proxy lets `GET /metrics` through to Pinax, which checks the token. `/metrics` is
     logged at debug, like `/api/health`.
   - Tests (`tests/test_metrics.py`): off, then only with the token; a short token refused; each filesystem's
     scans and size equal to the catalog's; a skipped scan newer than the last success; the web app's requests,
     queries, and audit events counted; capacity and use; no lab, person, or path named.
   - *Done*: scraped from the demo copied to PostgreSQL: both filesystems' figures, and Fast's earliest fill date
     (June 2027, as *Capacity* has it). The alert rules parse as YAML; `promtool` was not available to check their
     expressions.
5. **Demo and release 0.10.0.**
   - The demo is unchanged on SQLite. Seeded into PostgreSQL, it holds the same 34,854 files and serves the same
     (slice 1); a copy of the 0.9.0 demo moved there answered as the SQLite demo did (slice 2).
   - `docs/DEMO.md`: the demo on PostgreSQL, and a step for logs and metrics; the README describes them; the
     deployment guide installs v0.10.0.
   - Version 0.10.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.9.0 (the catalog adopted by its first migration, two dependencies, uvicorn's proxy headers
     off, nine settings, the units and proxy configuration to copy again).
   - Pull request to `main`, then tag `v0.10.0` and the release.

## 0.11.0: deeper views (wave 11)

More of the storage at once, and what 0.10.0 left. Like waves 9 and 10, it needs no production storage, and comes
before the pilot at the maintainer's request.

### Wave 11 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | The icicle and the sunburst (E10) | The age map's *View* adds *Icicle* and *Sunburst* beside the map and the table: a folder four, six, or eight levels deep at once, each folder spanning its share of the one it is in, sized and colored as on the map, leaving out folders under 0.25% of the place; hover, focus, and opening a folder as on the map, and the sunburst's middle goes up a level; every folder within the levels and over the floor, and no other, against sums worked out from the files, by size and by files; a lab's members see only its folders; within the directory-query budget (Q3) at 10 million files; both themes and a phone's width; the user guide, with screenshots | Done |
| 2 | Folding a lab's own tag into a standard one from *Tags* (T2), carried from 0.10.0 | Wave 8 built the fold (`tags.merge`, `POST /api/admin/tags/{id}/merge`: each folder with the lab's tag gets the standard tag, with a value given, or the lab tag's own, and the lab's tag is retired), but no button for it. Each row of *Labs' own tags* gets *Fold into a standard tag*: the standard tag, a value if it takes one, how many folders it changes, and a confirmation; then the row shows it retired. Tested through the page's API calls; the administration guide's *Tags* section and its screenshot updated | Done |
| 3 | Release 0.11.0 | The demo shows both new views and a fold (the walk-through's steps); version 0.11.0, changelog dated with notes on upgrading from 0.10.0; the website rebuilt; pull request, tag, release | Done |

### Decisions for wave 11

- **Both an icicle and a sunburst.** They draw the same partition of a folder, so they share a query, a layout,
  colors, and tooltips; only the drawing differs. The icicle is the one to read sizes from: lengths side by side,
  and names that fit. The sunburst shows a tree's shape at a glance and fits a phone, but draws outer rings larger
  than inner ones of the same share, so the guide says to compare angles. The treemap stays the default.
- **A floor, not a count.** The treemap draws a folder's largest 60 children and folds the rest; over several levels,
  counts per folder would multiply. Leaving out folders under 0.25% of the place bounds the drawing (at most 400
  folders on a level) and keeps what is left a tree, since a folder holds at least as much as any folder inside it.
  The space after a folder's subfolders, its own files and the folders left out, stays empty rather than becoming a
  tile.
- **Clicking opens the folder**, as on the map, rather than zooming inside the chart, so the address, the panel, and
  saved views follow.

### Slices as built

1. **The icicle and the sunburst (E10).**
   - `index.partition`: every folder within the levels below a place that holds at least the floor by the size
     metric, in one depth-and-path-range scan of `dirs`, level by level and the largest first; `index.measure`: what
     a place holds by a metric. `explore.depth_tree` nests them, with each top-level folder's owner for
     administrators; a person's home has their places as its first level, with the floor from all of them together.
     `GET /api/depth` (levels 1 to 10; six by default).
   - `depth.js`: the layout; the icicle drawn with the treemap's tiles (`treemap.js` now shares its tiles, colors,
     and tooltips); the sunburst in SVG, each label across its ring or along it, whichever has more room, an outline
     on the arc under the pointer or in focus, and the place in the middle. *Levels* offers four, six, or eight for
     them; the sunburst's box is square.
   - Measured on a synthetic scan of 10 million files with wide and deep folders (265,000 folders): 4–7 ms for six or
     eight levels anywhere, against 17–31 ms for the treemap's two levels (`bench/bench_index.py`). At a
     filesystem's root it reads every folder within the levels, about 2 ms per million folders on this laptop, so a
     root with 100 million folders may take a few hundred milliseconds; the pilot (wave 15) measures it on
     production.
   - Tests: the partition against folder sums worked out from the test filesystem's files, by size and by files, at
     several places, levels, and floors, level by level, the largest first, each folder after its parent; the
     endpoint's nesting, floor, owners, home, and access (`tests/test_index.py`, `tests/test_api.py`).
   - The demo seed scans its filesystems by their real path, so a demo reached through a link (macOS's `/tmp` and
     `/var` are links to `/private`) ingests.
   - *Done*: checked in the browser in both themes, at a phone's width, and by keyboard; both views in the user
     guide, with screenshots.
2. **Folding a lab's own tag into a standard one (T2).**
   - *Tags*: each lab tag that folders have gets *Fold into a standard tag*, a dialog with how many folders it
     changes, the active standard tags, and a value for one that takes it: each folder's own by default, or for a
     lab's flag its name, offered only where the standard tag can take it (text, or a choice of that name), so a date
     or another choice needs one. Below, the tag's description, whether it protects folders from cleanup plans, and,
     for a tag with one value, that the folded value replaces a folder's own. Then the row shows the lab's tag retired.
   - `tags.merge` refuses a retired standard tag. A value the standard tag cannot take is refused and the fold rolled
     back, so nothing changes; the dialog stays open with why.
   - Test (`tests/test_tags.py`): the page's calls in order: the list with the lab tag's folders; a value not among a
     choice's, a retired tag, and a standard tag folded, each refused with nothing changed; a lab member refused; the
     fold, after which the list shows the lab's tag retired on no folders and both folders have the standard tag with
     the value given; a flag folded into text keeps its name as the value; both folds audited.
   - *Done*: a fold on a copy of the demo in the browser; the administration guide's *Tags*, with a screenshot of
     the dialog.
3. **Release 0.11.0.**
   - The demo needs no new data: its folders go six levels deep, and the Fujimoto Lab's *Atlas paper* is a lab tag to
     fold. The walk-through opens `fujimoto_a` as an icicle and a sunburst; the website's home page names the release.
   - Version 0.11.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.10.0 (no migration, setting, or dependency); the deployment guide installs v0.11.0.
   - Pull request to `main`, then tag `v0.11.0` and the release.

## 0.12.0: access (wave 12)

Who may see and do what, beyond labs' members and storage administrators; scripts that use Pinax with a person's
access; and the record of who did what, for auditors. It works with every way of signing in, and needs no production
storage and no Entra ID. It comes before the pilot, which still waits for production storage, and before the rest of
reach, at the maintainer's request.

### Wave 12 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Roles (A10) | Auditor, finance, and division head beside storage administrator, PI, and delegate; given and taken away in *People and access*, and audited; each role's access checked on the server for every request, with tests for each role as A1 has for labs (what it sees, what answers 403 or 404, and that it changes nothing it may not); the sidebar and pages show each role what it may use; the demo has a person in each | Done |
| 2 | API tokens for scripts (A11) | Personal tokens, and service tokens for a named service, stored hashed and shown once, with their person's access or read-only, expiring, revocable, and last used; every change made with one audited with the token's name; a deactivated person's tokens stop; the proxy configuration lets requests with a token through to Pinax; the guide has a script that downloads a month's chargeback statement | Done |
| 3 | The audit log in the app (O6) | A page of every audit event, for administrators and auditors: filtered by person, action, folder, and date, newest first, paged, with CSV | Done |
| 4 | Access review (A7) | Who has access to what, for review and sign-off: each lab's and department's members and roles, each site role's holders, and the folders each can see, with when each was given; CSV; for administrators and auditors | Done |
| 5 | Release 0.12.0 | The demo walk-through for each role; version 0.12.0, changelog, pull request, tag, release | Done |

### Decisions for wave 12

- **Roles are given in Pinax**, with every way of signing in; wave 13 maps Entra ID app roles or groups onto them,
  optionally. Site roles belong to a person, beside their lab memberships, and a person can have several (a finance
  officer who is also a PI). Storage administrator stays as it is.
  - *Auditor*: everything a storage administrator sees, unmapped folders, people and access, and the audit log
    included, and nothing they can change.
  - *Finance*: every lab's and department's charges (*Storage charges* of any of them, *Chargeback* with its
    statement, the next twelve months) and the prices, free tiers, and capacities behind them, to read; not folders,
    files, or people.
  - *Division head*, for one or more divisions (each lab's *Division*): what the members of the division's labs see,
    to read: their folders and reports, charges, plans, and collections. It suits a division's data steward too.
    Tagging folders and planning cleanup stay with each lab's PI and delegates.
- **Seeing and changing are separate on the server.** Where a check asked "administrator?", it now asks whether the
  person sees everything (administrator, auditor), sees every charge (and finance), or may change the site's
  settings (administrator); a lab's data is seen by its members and its division's heads, and changed by its PI and
  delegates. Pages show only what a person may use, but the server decides.
- **A token is never more than its person**: it carries their access, or less (read-only), and stops with them.
- **A service token is no one's, and only reads.** A finance system or a dashboard should not stop when whoever set
  it up leaves, nor act for anyone: an administrator makes it for a named service, and it reads as finance or as an
  auditor. Changes through the API are made with a person's own token, in their name.
- **Scripts get past a sign-on proxy on a path of their own.** oauth2-proxy chooses what to let through by path,
  not by header, so `/script/api/...` is `/api/...` for scripts: the proxy lets it through, and Pinax answers it
  only with a token, never the sign-on header. With local sign-in, a token works on either path.
- **No token makes tokens.** Tokens are made, revoked, and passwords changed only in Pinax's pages, so a token that
  leaks cannot make itself another.
- **A review's sign-off is a record, not a change.** Recording that access was reviewed changes no one's access, so
  auditors, whose work it often is, may record it as administrators may. It is kept in the audit log, with how much
  access there was, rather than in a table of its own.
- **When access was given is recorded from now on.** Earlier grants say *Not recorded* rather than a guess from
  the audit log, whose older events do not all name what they changed.

### Slices as built

1. **Roles (A10).**
   - `user_role` (migration `0002`, the first since the baseline): a person's site roles, `auditor`, `finance`, or
     `division` with the division's name, and when each was given. `auth.Identity` separates seeing from acting:
     `sees_all` (administrators, auditors), `sees_charges` (and finance), `labs_seen` and `folders` (a person's own
     labs, and those of the divisions they head, from the labs' *Division*), `lab_roles` (the labs they act for), and
     `is_admin` (changes). `require_sees_all` and `require_sees_charges` guard the reads only administrators could
     make before; every change keeps `require_admin`, and a lab's tags and plan its PI and delegates (adding to a
     plan now checks that, rather than only seeing the folder, which no longer implies membership).
   - *People and access*: a *Site roles* card on a person's page (administrator, auditor, finance, and each division
     to head), a *Site roles* view of the list, role badges, and what the person would see; `people.save_person`
     sets roles as a full list, a division only one that labs have, each one given or taken away audited
     (`person.role`).
   - Pages follow `/api/me`'s `sees_all`, `sees_charges`, `sees_labs`, and `divisions`: the sidebar of each role;
     finance's home is *Chargeback*; division heads choose among their labs on *Storage charges* and *Cleanup plans*,
     and see all of them unasked; controls that change the site's settings carry `admin-only`, shown to
     administrators only; plans say whether the person may change them (`can_change`).
   - Demo: Helen Marsh (auditor), Daniel Osei (finance), and Grace Whitfield (head of Basic Sciences, three labs),
     on the landing page and in the identity picker's new group.
   - Tests (`tests/test_roles.py`): roles given, refused (an unknown role, a division with no labs), and taken away,
     with their audit; for each role, every read it may make, every one it may not (403 for administration, 404 for
     folders and labs, as for others' labs), and changes refused; the division following the lab; a role beside a
     lab. The migration tests follow the newest revision.
   - *Done*: each role checked in the browser on a demo of its own; roles given on a person's page and audited; the
     administration guide's *Site roles*, with screenshots.
2. **API tokens for scripts (A11).**
   - `api_token` (migration `0003`): a token's name, the SHA-256 of its secret (`pinax_` and 43 random characters)
     and its first characters, its person (none for a service token) or its role (`auditor`, `finance`), whether it
     only reads, who made it and when, when it expires, when and from where it was last used, and when and by whom it
     was revoked. Ended tokens are kept, for the audit log, and listed for 90 days.
   - `tokens.py`: making (a week to `PINAX_TOKEN_MAX_DAYS`, 365 by default; a name unique among the owner's live
     tokens), finding a live token (not expired or revoked, its person active), what it may do (reading only: GET,
     and a person's starting a search; no token on `/api/tokens`, `/api/admin/tokens`, or `/api/auth/`), revoking,
     and revoking a person's tokens when they are deactivated. Making and revoking are audited (`token.made`,
     `token.revoked`), in the person's history.
   - `auth.current_identity` takes `Authorization: Bearer` first, in every mode: a personal token is its person as
     they are now (`identity_for`), a service token a person never stored, with no labs and the token's role. The
     request's log line names the person (`service:<name>` for a service), and `logs` stamps the token's name on
     every audit event the request adds (a `before_flush` event, so every place that audits is covered).
     `auth.ScriptPrefix` turns `/script/api/...` into `/api/...` and marks it token-only;
     `deploy/oauth2-proxy/pinax.cfg` lets it through.
   - *API tokens* (`#/tokens`, the menu under everyone's name, and the sidebar for administrators and auditors):
     one's own tokens, made with a name, a lifetime, and reading only or one's access, shown once with an example;
     service tokens; everyone else's; revoking. A person's page lists their tokens to revoke; recent changes say
     which token made them.
   - Demo: five tokens (a finance analyst's monthly ledger import, a PI's pipeline that tags with her access, a
     data manager's weekly report, an administrator's capacity forecast, and two service tokens), made and used in
     the past; their secrets are thrown away.
   - Tests (`tests/test_tokens.py`): reading as the person and nothing more, the secret stored hashed and never
     logged; changes with a token's access audited with its name, and following the person's access; expiry,
     revoking (one's own, an administrator's, not someone else's), and deactivation; no token on the token or
     password routes; names and lifetimes; service tokens (outliving their maker, what each role reads, reading
     only); scripts through the sign-on proxy on `/script/api/`, where the sign-on header does not count.
   - *Done*: tokens made, shown once, and revoked in the browser, at 375 pixels and in both themes; the guide's
     script run against the demo with a service token, saving a month's final statement; the user guide's *API
     tokens for scripts* and the administration guide's *API tokens*, with a screenshot.
3. **The audit log in the app (O6).**
   - `audit.py`: filters combined as one query, newest first: a person (events they made, `actor_id`, and those
     about them, `detail.username`), an action or a kind (`plan` for `plan.*`), a lab (`detail.lab`, folders mapped
     to or from it, and events whose `detail.path` is in one of its folders now, since a folder's tag records the
     tag's lab, not the folder's), a folder (`detail.path` equal or below, with `LIKE` wildcards escaped, and
     `folder.mapped` of its top-level folder, recorded by filesystem and folder name), and days (UTC). Paths are
     shown with display roots; the CSV keeps what was recorded, streamed a thousand rows at a time from a session of
     its own.
   - Migration `0004`: the audit event's `address`, set for every event a request makes (the `before_flush` event
     that names a token), and indexes on `action` and `actor_id`. Sign-ins from before keep theirs in the detail,
     which the log falls back to.
   - `GET /api/admin/audit` (`require_sees_all`): a page (50, at most 500), the total, every action recorded with
     its count, and the people and labs to choose from; `fmt=csv` for every one.
   - *Audit log* (`#/audit`, the sidebar of administrators and auditors): the filters in one form, the count, each
     change's time, person (or command line, or Pinax) and token, summary and action, address, and *Details*;
     *Newer* and *Older*; *Download CSV*. Links from a person's and a lab's recent changes and from *Recent
     activity*. Long paths break, so the table fits 375 pixels.
   - Demo: site roles given by the demo's administrator through `people.save_person`, so they are audited.
   - Tests (`tests/test_audit.py`): who may read it; order, who, where, and display roots; each filter alone and
     together; folders (a prefix that is not a parent, and wildcards); mistakes named (404 or 422); paging; the CSV
     as recorded; a change made with a token.
   - *Done*: in the browser as an auditor, every filter, paging, and the CSV, at 375 pixels and in both themes; the
     administration guide's *The audit log*, with a screenshot; the walk-through's step 19.
4. **Access review (A7).**
   - Migration `0005`: `user.admin_since` and `lab_membership.granted_at`, set by SQLAlchemy attribute events on
     `User.is_admin` and `LabMembership.role`, so the people page, `pinax import`, `pinax user add`, and the seed all
     record them; a role changed (delegate to PI) is given again. `user_role.granted_at` was there already.
   - `review.py`: active people and labs only. Site roles, administrators first, each with its date; each lab's
     members (PI first) with theirs, the heads of its division, and its folders with when the lab came to own each
     (the latest stretch of `folder_owner` with the current owner, where Pinax recorded a change); each person's
     access, the folders they see and through what, live tokens, and last sign-in; what is worth a look; the counts,
     with deactivated people and labs; the latest sign-offs. The CSV has a row for each piece of access, and *none*
     for someone who sees nothing.
   - `GET /api/admin/access-review` (`fmt=csv`) and `POST /api/admin/access-review/sign-off`, both
     `require_sees_all`; the sign-off is audited as `access.reviewed` with a note and the counts.
   - *Access review* (`#/access-review`, the sidebar of administrators and auditors): the counts, *Find* across every
     list, *Worth a look*, *Site roles*, *Labs and departments*, *People* (folders behind *Details*), *Reviews*, and
     *Record a review*. The sidebar marks a page by its own route only, so *People and access* is not marked on it.
   - Demo: memberships and administrators dated from the first scan; Helen Marsh has recorded a review.
   - Tests (`tests/test_review.py`): who may read it and sign off; site roles, labs, people, and what is worth a look;
     deactivated people and labs left out; dates given, not recorded, and given again; a folder's date; tokens and,
     with local sign-in, people who have not signed in; the sign-off audited, changing nothing; the CSV.
   - *Done*: in the browser as an auditor: *Find*, a review recorded, at 375 pixels and in both themes; the
     administration guide's *Access review*, with a screenshot; the walk-through's step 20.
5. **Release 0.12.0.**
   - The walk-through's *Site roles* (step 17) switches to each of the demo's site roles in turn: Helen Marsh, the
     auditor, on every administration page with nothing to change; Daniel Osei, finance, from *Chargeback* to his
     ledger import's token; Grace Whitfield, head of Basic Sciences, on her division's three labs, reading a
     folder's tags and plan. Each checked on a fresh demo. A plan with nothing in it now tells those who read it
     who adds to it, rather than inviting them to. The demo has 23 people.
   - The access review's token item says why it is worth a look: *The token “…” can make changes as …, not only
     read*, with when it expires and was last used; *Reading it* says what such a token risks (kept by a script, it
     acts as its person, with whatever access they have now) and what to check. The screenshot re-captured.
   - Every guide and the website checked against the wave before the pull request: *How Pinax sees storage*
     (site roles, API tokens, the audit log and access review), *Troubleshooting* (tokens and roles), *Trying Pinax
     on your own data*, the administration guide's opening and the pages auditors and finance read, the
     architecture's catalog, *Development* (modules, the guards that separate seeing from changing, endpoints,
     pages), the documentation index, the README, `.env.example` and `deploy/pinax.env`; the website's home page
     has *Access and audit*, and its pages' descriptions name the roles. Requirements A7, A11, and O6 say how each
     was validated.
   - Version 0.12.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.11.0 (migrations `0002` to `0005`, the oauth2-proxy's `skip_auth_routes`, and
     `PINAX_TOKEN_MAX_DAYS`); the deployment guide installs v0.12.0; the website's home page names the release.
   - Pull request to `main`, then tag `v0.12.0` and the release.

## 0.13.0: reach (wave 13)

Sign-in for any site. Local sign-in is the default, with nothing else to set up; sign-in with Microsoft Entra ID in
Pinax itself is optional, configured by settings, with the roles of wave 12 from Entra ID; an emergency
administrator sign-in covers Entra ID failing; and CI checks every change on SQLite and on PostgreSQL.
It needs no production storage, and comes before the pilot, which still waits for it, at the maintainer's request.

### Wave 13 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Local sign-in the default outside the demo (A9) | With `PINAX_ENVIRONMENT` set to `dev` or `prod` and no `PINAX_AUTH_MODE`, Pinax signs people in with passwords it keeps; the demo environment keeps its persona switcher as it is; the server kit (`deploy/pinax.env`) and the deployment guide set up local sign-in first, with single sign-on as an option; the configuration and setup guides and the tests follow | Done |
| 2 | Sign-in with Entra ID in Pinax itself (A6, in part) | A mode configured by settings and checked at start-up: any OpenID Connect provider by its discovery document, the authorization code flow with PKCE, the ID token checked against the provider's keys (issuer, audience, expiry, nonce); people matched by one claim, within the site's domains, and never made; a server-side session, as local sign-in makes; refused sign-ins audited with why; signing out ends the provider's session too; the whole flow tested against a stand-in provider (forged, expired, and replayed sign-ins, guests, unknown and deactivated people), and checked against Dex in Podman; the guide's request for IT, and a table of Entra ID's errors | Done |
| 3 | The emergency administrator sign-in (A12) | As decided below, each part tested: off unless an emergency account exists; marked only from the command line; an address of its own; the password rules and lockout, a longer minimum password, and short sessions; every use audited, logged as a warning, and counted in the metrics; the accounts listed in the access review; the guide | Done |
| 4 | Roles from Entra ID (A6, in part) | Optionally, Entra ID app roles (the token's `roles` claim) or groups mapped by settings onto the administrator role and the site roles, applied at each sign-in and audited; roles given in the app stay possible; a group overage said, not guessed; tested against the stand-in provider; app roles and groups checked in a real tenant when a test registration is available | Done, but for the check in a real tenant |
| 5 | Continuous integration | GitHub Actions run the linter and the whole suite on SQLite and on PostgreSQL for every push, and for pull requests from forks, with the pinned fs-inventory installed so that the tests that run it run too; the development guide says what runs | Done |
| 6 | Release 0.13.0 | The walk-through and the guides for each way of signing in; version 0.13.0, changelog with notes on upgrading from 0.12.0; pull request, tag, release | Done |

### Decisions for wave 13

- **Not in this wave:** local sign-in's two-factor codes and per-address limits (in the parking lot), and a
  security review (an organization's own, outside the roadmap).
- **CI runs on GitHub Actions.** For a private repository, it uses the account's included minutes. Each push is
  checked once: a pull request from a branch of this repository shows its pushes' runs rather than running again.
- **No container image, for now** (in the parking lot). The web app and automatic ingest would run in one, but
  scans would not: fs-inventory names files' owners from the accounts of the host that scans (LDAP or sssd), which
  a container does not have, and reads every folder with a capability a container grants only when run as root. So
  scans would run outside it, with fs-inventory alone, and without the filesystem's capacity that `pinax scan`
  records. That is a second way to install, and a weaker one, beside the installation with uv and systemd units,
  which is short.
- **A role the map names belongs to Entra ID.** Pinax gives or takes it away at each sign-in and the People page
  only shows it; roles the map does not name stay Pinax's. Adding roles from Entra ID to those given in Pinax was
  set aside: Pinax could not tell which to take away when someone leaves a group in Entra ID.

**Local sign-in is the default; Entra ID is optional.** Pinax may run for a small company or a team with no access
to its organization's Entra ID, so it must work fully with passwords it keeps, with nothing else to set up, and
single sign-on is something a site configures. Today the code's default is the demo's persona switcher (in the demo
environment) and the server kit (`deploy/pinax.env`, `docs/DEPLOYMENT.md`) defaults to oauth2-proxy; this wave makes
local sign-in the default outside the demo, in the code and in the server kit, with Entra ID (and oauth2-proxy) as
the documented options. The demo keeps its persona switcher.

**Sign-in with Entra ID, following Tundra.** Tundra, Pinax's sister application, signs people in with Entra ID itself
(`TUNDRA_AUTH_MODE=oidc`; `src/tundra/oidc.py`, `docs/ENTRA-ID.md`, `tests/test_oidc.py`). What to take from it:
- **Any OpenID Connect provider**, Entra ID the one in production, through Authlib's Starlette client: the
  provider's discovery document, the authorization code flow with PKCE, and the ID token checked against the
  provider's published keys, with its issuer, audience, expiry, and nonce; state kept in a signed session for the
  sign-in only. No proxy: nginx or a load balancer only provides HTTPS.
- **People are matched, not made.** The username comes from one claim (`preferred_username`, Entra ID's UPN; `email`
  for test providers), allowed only in the site's domains, so a guest `jdoe@example.com` never becomes staff `jdoe`;
  the person must already be active in the app. Refused sign-ins are audited with why, as is every sign-in; a
  deactivated person is out on their next click; signing out ends the provider's session too. Who may sign in at all
  is also limited in Entra ID (*Assignment required* on the enterprise application, with a group assigned).
- **Settings checked at start-up**: issuer (`https://login.microsoftonline.com/<tenant>/v2.0`), client ID, client
  secret, public URL, and session secret; Pinax refuses to start, and says what is missing, until they are complete.
- **The request for IT**, in the guide: a single-tenant web application, the callback and signed-out redirect
  addresses, the code flow only, one client secret and its expiry (with how to rotate it), delegated `openid
  profile email` with admin consent; outbound HTTPS to `login.microsoftonline.com`. A troubleshooting table of
  Entra ID's AADSTS errors.
- **Tested without Entra ID**: Dex in Podman with test accounts (an administrator, a PI, someone unknown, a guest
  named like a staff member), and tests of the whole flow against a stand-in provider, with forged and expired
  tokens, replayed sign-ins, and guests.

Where Pinax differs:
- **Sessions**: Pinax keeps server-side sessions (`signin.py`); an Entra ID sign-in makes one, as local sign-in
  does, so signing out and deactivating work the same, rather than Tundra's signed cookie.
- **Usernames**: Pinax's guides suggest the UPN as the username (`jdoe@example.org`), so a site can move from local
  sign-in or a proxy to Entra ID without renaming anyone; Tundra strips the domain. Match the UPN whole, or, with
  allowed domains, either form.
- **Roles from Entra ID**, which Tundra does not do: optionally, Entra ID app roles (in the token's `roles` claim)
  or groups mapped onto Pinax's roles, app roles preferred, since past about 200 groups Entra ID sends a link to
  Microsoft Graph instead of the groups. Roles given in the app stay possible either way.
- **Lab membership from the directory** (A6) is the pilot's (wave 15): it needs Microsoft Graph application
  permissions (such as `GroupMember.Read.All`, with admin consent), a mapping of groups to labs, and a test tenant,
  and settles, with IT, the open decision on the authoritative source of membership and ownership.
- **Testing what Dex cannot show**: app roles, groups and the group overage, and Graph need a test registration in
  an Entra ID tenant.

**An emergency administrator sign-in (A12)**, which Tundra does not have: with sign-in through Entra ID, a way in
for an administrator when Entra ID or Pinax's setup for it fails (an expired client secret, a wrong setting, an
outage, a role mapping that leaves no administrator), as Microsoft advises emergency access accounts for Entra ID
itself. It is a password-only way into an app otherwise behind the organization's single sign-on, multi-factor
sign-in, and access policies, so it is kept small:
- **Off unless an emergency account exists.** A site whose policy forbids any sign-in outside single sign-on makes
  none, and then no one signs in with a password at all.
- **Only storage administrators marked as emergency accounts**, marked and unmarked only from the server's command
  line (`pinax user`), never in the app, so an administrator's single sign-on account, if taken over, cannot make
  one. Everyone else signs in through Entra ID only.
- **An address of its own**, not linked from the sign-in page; the local sign-in's password rules and lockout, a
  longer minimum password, and short sessions.
- **Loud**: every sign-in and failure audited, logged as a warning, and counted in the metrics, so the site's
  monitoring can alert on it; *Access review* lists the emergency accounts under *Worth a look*.
- **The guide's advice**: two such accounts, with long passwords kept in the organization's password vault, signed
  in with now and then to check they work.
- **Behind oauth2-proxy** (header mode), every request passes the proxy, so the way back stays on the server:
  `PINAX_AUTH_MODE=local` and a restart, and `pinax user password`, which work in every mode.

### Slices as built

1. **Local sign-in the default outside the demo (A9).**
   - `config.Settings.auth_mode` is unset by default and filled after the settings are read: `demo` in the demo
     environment, as before, and `local` in `dev` and `prod`. A site that sets `PINAX_AUTH_MODE` keeps what it
     set; `demo` is still refused in production.
   - The server kit sets `PINAX_AUTH_MODE=local` in `deploy/pinax.env`, with oauth2-proxy's two settings there,
     commented. The deployment guide's step 6 is *Signing in* with passwords, and *Single sign-on with oauth2-proxy*
     follows it as an option; *Shape of a deployment*, *What you need*, step 4's usernames, and the check follow.
     The setup guide and the README no longer set `PINAX_AUTH_MODE`; the configuration, architecture, demo, and
     troubleshooting guides, `.env.example`, and the web app's unit say what the default is.
   - Tests (`tests/test_signin.py`): the default in each environment, with no persona list outside the demo; a
     site's own choice kept; the demo refused in production when set.
   - *Done*: a fresh install with only `PINAX_ENVIRONMENT=dev`: `pinax user add --admin` printed a temporary
     password, the browser showed the sign-in page, and the administrator signed in and was asked for a new
     password.
   - Found on the way: the audit log's test took today from the local clock, while the log's days are UTC, so it
     failed in the evening in the Americas; it uses the UTC date now.
2. **Sign-in with Entra ID in Pinax itself (A6, in part).**
   - `oidc.py`, following Tundra's: `PINAX_AUTH_MODE=oidc` with `PINAX_OIDC_ISSUER`, `_CLIENT_ID`, `_CLIENT_SECRET`,
     `_USERNAME_CLAIM` (`preferred_username`), `_USERNAME_DOMAINS`, `PINAX_PUBLIC_URL`, and `PINAX_SESSION_SECRET`,
     checked by `config.unsafe`, so Pinax refuses to start until they are complete. Authlib's Starlette client (with
     joserfc, and httpx2 to reach the provider; itsdangerous signs the state) finds the provider by its discovery
     document, sends the browser there with PKCE (S256), and checks the ID token's signature, issuer, audience,
     expiry, and nonce at `/auth/callback`. The sign-in's own state is a signed cookie on `/auth/` for ten minutes.
   - People are matched, never made: the claim, lower-cased, is a person's username exactly, or, with allowed
     domains, a person named with or without the domain (the whole name first); another domain is refused. A
     match gets a server-side session (`signin.start_session`), so `auth.current_identity` treats `oidc` as `local`
     (without a password to change), deactivation and signing out work the same, and changes must come from a page
     of Pinax. Sign-ins, refusals (with why), and sign-outs are audited with `method: oidc`; the access review knows
     when each person last signed in. `/auth/logout` ends Pinax's session and goes to the provider's
     `end_session_endpoint`. Refusals and failures are pages in Pinax's look, with what to do.
   - The interface: without a session, *Sign in* goes to `/auth/login` with the page to return to; an answer of
     401 does the same; *Sign out* in the menu goes to `/auth/logout`.
   - `scripts/dex/dex.sh` and `dex.yaml`: Dex v2.46.1 in Podman with four of the demo's people (an administrator, a
     PI, someone unknown, and a guest named like an administrator), and the settings to run the demo against it.
   - Tests (`tests/test_oidc.py`, 17), against a stand-in provider reached through httpx2's ASGI transport: signing
     in and out, with the cookie's flags and the audit; refusals (unknown, a guest's domain, no domain);
     deactivation; forged, wrongly addressed, and expired tokens; replays, and a sign-in finished in another
     browser; the provider's errors; returning only to pages of Pinax; both forms of username; other ways in closed;
     API tokens and a temporary password left from local sign-in; the same-origin check; the settings checked at
     start-up.
   - *Done*: the demo against Dex in the browser: the administrator signed in and returned to the page asked for,
     signed out, and the guest and the unknown person were refused with a message, each in the audit log. The
     guide *Signing in with Entra ID* (`docs/ENTRA-ID.md`): how it works, the request for IT, the settings, a trial
     with Dex, and Entra ID's errors; the deployment, configuration, architecture, development, CLI, and
     troubleshooting guides, the README, the examples of settings, and the website follow.
3. **The emergency administrator sign-in (A12).**
   - Migration `0006`: `user.emergency`. `pinax user emergency USERNAME` (with `oidc` only) marks an active storage
     administrator and prints a new password of 32 characters (`signin.set_emergency`), once; `--off` ends it, with
     its password; both audited (`person.emergency_set`, `_removed`) as made from the command line. The people page
     cannot set it.
   - `/auth/emergency` (`oidc.py`): 404 unless Pinax is in `oidc` mode and an active storage administrator is an
     emergency account; a form in Pinax's look, its post refused from another site's page. `signin.sign_in(emergency
     =True)` lets in only emergency accounts that are active administrators, keeps local sign-in's lockout and its
     one message for every failure, gives an hour's session, audits `auth.emergency_signed_in` and
     `auth.emergency_sign_in_failed` (with why), and logs each as a warning; `pinax_audit_events_total` counts them,
     and the alert rule `PinaxEmergencySignIn` fires on any.
   - *Access review* lists each emergency account under *Worth a look*, with its last emergency sign-in, and says
     what to check; a person's page has an *Emergency account* card, and the people list a badge.
   - Tests (`tests/test_emergency.py`, 8): off without an account and outside `oidc`; a sign-in short, audited,
     logged, and counted; failures and the lockout; administrators not marked, no longer administrators, and
     deactivated refused; a post from another site; the command line only (not with local sign-in, not for a
     non-administrator, not from the people page), and ending it; the access review.
   - *Done*: on the demo with Dex: the account marked from the command line, the form and a wrong password in the
     browser, a sign-in with the password (by script, so it was never shown), both warnings in the log, the account
     on *Access review* and the person's page. The guides: *When Entra ID fails* in *Signing in with Entra ID*, the
     CLI, deployment (a task, the metric, the alert), architecture, troubleshooting, and administration guides, and
     the README.
4. **Roles from Entra ID (A6, in part).**
   - `PINAX_OIDC_ROLE_MAP` (JSON: `admin`, `auditor`, `finance`, or `division:<Division>`, each to the claim values
     that give it) and `PINAX_OIDC_ROLES_CLAIM` (`roles`, Entra ID's app roles, by default, or `groups`), read only
     in `oidc` mode (`Settings.mapped_roles`); a key that is not a role, or one without values, stops Pinax at
     start-up.
   - `people.roles_from_provider`, at each sign-in through the provider before the session starts: each mapped role
     given or taken away from the claim, the administrator role among them, audited with `source: oidc` and no
     actor (*Pinax* in the audit log); a warning when no active administrator remains. With groups, Entra ID's
     overage (`_claim_names` naming `groups`, or `hasgroups`) takes every mapped role away, audited as
     `auth.roles_unknown`, rather than keeping or guessing them.
   - `people.save_person` keeps mapped roles as they are when the change comes from the app; the command line can
     still give any (`pinax user add --admin`). `/api/config` names the mapped roles; a person's page shows them as
     *From Entra ID*, without their checkboxes.
   - Tests (`tests/test_oidc_roles.py`, 9): a mapped role following the token at each sign-in; the administrator
     role, given and taken away, and the warning when none remains; a division head; a role the map does not name
     kept; the People page leaving mapped roles alone; groups, and too many to list; the command line; local
     sign-in ignoring the map; a wrong map.
   - *Done*: with Dex and a map of auditor and finance, Helen Marsh's page showed both as *From Entra ID* and locked,
     and the administrator and division heads still editable. Dex sends no app roles or groups, so they are checked
     in a real tenant when a test registration is available. The guides: *Roles from Entra ID* in *Signing in with
     Entra ID*, the configuration, administration, and architecture guides, the README, and the examples of
     settings.
5. **Continuous integration.**
   - `.github/workflows/ci.yml`, on GitHub's Ubuntu 24.04 runners, in three jobs: the linter (ruff, pinned);
     the whole suite on SQLite; and the whole suite on PostgreSQL 16, started as in the development guide (in memory,
     `fsync` off) and waited for until it answers over TCP, since it answers on its socket while it initializes.
     Both suites install the pinned fs-inventory first (`scripts/install-fs-inventory.sh`), so the tests that run
     the real binary run rather than being skipped. uv and the actions are pinned (the actions by commit); each job
     reads the repository only, and leaves no credentials in the checkout.
   - It runs for every push, and for pull requests from forks: a pull request from a branch of this repository
     shows its pushes' runs. A newer push cancels the older run of the same branch.
   - Planned with a container image, which was set aside (the decision above).
   - *Done*: the first run on GitHub, on the branch's first push, passed in about seven minutes: the linter clean,
     392 passed on SQLite and 391 on PostgreSQL (the one about SQLite catalogs skipped), with fs-inventory 1.0.0
     installed in each, the same as each job's steps run here.
6. **Release 0.13.0.**
   - Each way of signing in walked through on fresh installs, from its guide as written. Passwords, from *Running
     Pinax on your own data*: the first administrator's temporary password, a scan and its check, `pinax seed`
     refused outside the demo, signing in and changing the password. Entra ID, with Dex from *Trying it before
     Entra ID is ready*: the administrator and the PI signed in, the guest and the unknown person refused with why,
     signing out; then an emergency account made as the guide advises and signed in at `/auth/emergency`, for an
     hour, with the warning logged. Header mode: the username believed from this host, unknown people and requests
     without it refused, the demo's and the emergency sign-in's addresses closed; oauth2-proxy and its
     configuration are unchanged since 0.12.0. The demo: its persona switcher as before.
   - What the walk-through found: the Entra ID guide's trial left out the PI, and now says how to try the
     emergency sign-in too; `pinax user emergency` printed `<PINAX_PUBLIC_URL>` rather than the address it has;
     `.env.example` and the refusal of the demo in production did not name `oidc`; the setup guide named only
     oauth2-proxy for single sign-on.
   - Every guide and the website checked against the wave: the documentation index and the website's page on Entra
     ID name roles and the emergency sign-in; the home page names the release, passwords or Entra ID, and the
     emergency sign-in. Requirements A6, A9, and A12 say how each was walked through.
   - Version 0.13.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated, with notes on
     upgrading from 0.12.0 (migration `0006`, the new dependencies, the default sign-in for a `dev` install without
     `PINAX_AUTH_MODE`, and the new alert rule); the deployment guide installs v0.13.0.
   - Pull request to `main`, then tag `v0.13.0` and the release.

## 0.14.0: load (wave 14)

Many people at once. What each page costs alone is measured (Q3 to Q5), but not what happens when many people use
Pinax together: one process answers every request, each snapshot index is one DuckDB database that every request
shares, and the catalog takes small writes from everyone signed in (sessions, the audit log). This wave measures it
with a load test, fixes what it finds, and says how many people a server serves. It also lets a site define its own
file categories, which today are a fixed list in the code. It needs no production storage, and comes before the
pilot, which still waits for it, at the maintainer's request; the pilot runs the same test again on its server.

### Wave 14 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | The load test (Q6) | `bench/load.py` drives a running Pinax over HTTP as browsers do: simulated people of each role, each signing in with a password, then working through the pages of a session with pauses between clicks; for every page, the median, 95th and 99th percentile times, errors, and requests a second, and the server's memory through the run, written as a report. `bench/load_setup.py` makes the installation it runs against, on SQLite or PostgreSQL: three synthetic scans of 10 million files a month apart (for history and the change map), 200 labs with their people, and the site roles. A short run is in the test suite, so the load test keeps working. Two runs of the same load agree within 10% | Done |
| 2 | The baseline (Q6) | Runs with 10, 25, 50, and 100 people at once, on SQLite and on PostgreSQL: when times begin to climb, which pages are slowest and why, and memory against the deployment guide's sizing; recorded in the architecture guide's measured performance | Done |
| 3 | Turns and the catalog (Q6) | Whole-filesystem searches by several administrators at once while others browse: the waiting ones finish within the time limit or stop with Pinax's explanation, and other people's pages stay within the budgets; many people signing in at once (each password is checked with scrypt, slow by design); SQLite's locking and PostgreSQL's connection pool under the same load. What fails is found and fixed | Done |
| 4 | Tuning and sizing (Q6) | What slices 2 and 3 found fixed, or its defaults changed (such as the web server's 40 threads, `PINAX_QUERY_THREADS`, `PINAX_WIDE_QUERIES`, `PINAX_DATABASE_POOL_SIZE`, or more than one web process), each change measured again; the deployment guide's sizing says how many people a server of a given size serves, and what to change for more | Done |
| 5 | File categories in the catalog (R14) | The categories, with their extensions and name patterns, kept in the catalog (a migration fills it with today's twelve) rather than in `categories.py`; an index built with the catalog's categories, recorded in it, and every query reading each index's own; name patterns as regular expressions, checked before extensions; the indexes built with other categories than the catalog's are outdated, so `pinax rebuild --outdated` builds them again; the reports, *Find files*, the age map's *one category*, and the change between scans work the same with the default categories, and with added ones. Measured at 10 million files and extrapolated to a billion, as decided below: build time and index size with the default categories unchanged (within 3%), with 25 patterns at most 10% more build time, and with 24 categories the index within Q5's 60 bytes a file; no report slower | Done |
| 6 | Categories for administrators (R14) | *File categories*, under Administration: each category's name, description, extensions, and patterns, changed, added, and removed by storage administrators, and read by auditors; each pattern checked before it is saved, and tried against file names; how the latest scan's types would move between categories, before saving, from its table of types (no file read), and for patterns an estimate from a sample of its files, never a whole-filesystem read from the page; which snapshots will be rebuilt, how, and about how long it takes; every change audited; the guides | Next |
| 7 | Release 0.14.0 | Version 0.14.0, changelog with notes on upgrading from 0.13.0; pull request, tag, release | To come |

### Decisions for wave 14

- **On the maintainer's laptop** (Apple M4, 10 cores, 32 GB). Pinax and the load test share its processors, so the
  numbers show where Pinax slows and what limits it more than what a server would do; the pilot (wave 15) runs the
  same test on its server, with production's indexes.
- **10 million files**, not 100 million, to stay within the laptop's free disk. Folder-level pages cost little more
  at 100 million (Q3), and searches' growth with size is measured (Q4).
- **Budgets**, set here and revisited after the baseline: with 50 people at once, each clicking every 5 to 15
  seconds, the 95th percentile of folder-level pages within 1 s and of a lab's searches within 2 s, no errors, and
  memory within the deployment guide's 16 GB.
- **Simulated people sign in as browsers do**, with passwords and sessions rather than API tokens, so the writes
  that sessions and the audit log make are part of the load. Signing in is also measured on its own (slice 3).
- **The load test is a script of Pinax's own** (`bench/load.py`, with httpx, already a development dependency), not
  a load-testing tool, so nothing new is installed. Its sessions follow the walk-through's pages for each role.
- **Not in this wave:** the accessibility audit, which stays with hardening (wave 16).
- **File categories a site defines**, added at the maintainer's request. Today a file's category comes from its
  extension, in a fixed list of twelve in `categories.py`, and each index is built with it (per folder, the files and
  bytes of each category, which is what keeps *File types* fast at a billion files). Decided:
  - **Still decided when an index is built**, not when it is read: deciding at query time would mean reading every
    file for a report that now reads only folders. A change applies to the next scan; older snapshots keep their
    categories until rebuilt (`pinax rebuild --outdated`, which takes about 25 s per snapshot at 10 million files
    and, extrapolated, 20 minutes at a billion), so history and comparisons do not mix two definitions.
  - **Extensions and name patterns.** A file's type stays its normalized extension (`fastq.gz` is `fastq` inside
    gzip). A category lists extensions, and may also have patterns: regular expressions on the file's name (RE2, as
    DuckDB has them: no backtracking, so no pattern can take exponential time), checked first, in order, so
    `^Undetermined_.*\.fastq` can be its own category. The first match wins; a file nothing matches is *Other*.
    Patterns are limited in number (25) so a build at a billion files stays within its budget (Q5).
  - **What administrators may change:** every category's name, description, extensions, and patterns; new
    categories; removing those added. The built-in categories' keys stay, and *Other* stays last with no rules.
  - **One list for the site**, not per filesystem or lab.
  - **Efficient at a billion files**, since categories are applied to every file of every scan:
    - Extensions stay a join with a small table, as now: a site that changes only extensions pays nothing more.
    - Patterns are checked only when a category has some, only against the file's name (not its path), and in one
      pass: one expression of all the patterns together finds the few files that match any, and only those are
      checked pattern by pattern for the first that matches. At most 25 patterns; build time with them is measured,
      and its share of Q5's two hours per billion files stated.
    - At most 24 categories: each adds a file count and a size to every folder of the index, so the index's size is
      measured with 24 and kept within Q5's 60 bytes a file.
    - Reports pay nothing: they read each folder's stored totals as now, and an index's own list of categories is
      read once when it is opened.
    - Rebuilding is the cost of a change: one snapshot at a time, in place, from the command line or automatic ingest,
      never from the web app; the page says how many snapshots and about how long, and a site may rebuild only the
      latest and let older ones keep their categories (each says which it was built with).

### Slices as built

1. **The load test (Q6).**
   - The pages are the interface's own: each role's sidebar was opened page by page in the demo, as a PI, a storage
     administrator, and finance, and the API requests each page made were recorded. `bench/load.py` sends a page's
     requests together, as a browser does (at most six at a time to the site).
   - A simulated person's first visit loads the page, its styles and scripts, `/api/config`, and `/api/me` (refused,
     as expected), then signs in with the password and loads the page their role lands on. Then, every `--think`
     seconds (5 to 15), a page chosen by weights for the role: people of labs mostly the age map, history, and the
     other reports of their own lab's folder or one of its projects (only projects in the latest scan, since the
     later scans delete some), and their charges and plans; division heads the same for their division's labs;
     administrators and auditors *All data*, the explore pages of the filesystem's root or a lab, the administration
     pages, and now and then a search of every filesystem; finance *Chargeback*, a lab's charges, prices, and
     capacity. Each person has a random number generator of their own from `--seed`, so a run repeats.
   - People arrive over the ramp-up; pages and requests are counted from its end. The server's memory is sampled
     every second with `ps`. The report has each page's and API route's median, 95th and 99th percentiles, and
     maximum; errors with the first 50 answers; the budgets; and `--compare`.
   - `bench/load_setup.py`: three scans from `bench/synth_scan.py` (which gained `--started`, to date a scan in the
     past), ingested with `pinax ingest`; labs, people, memberships, and folders with `pinax import`; then what the
     import does not set, in the catalog: site roles, a price and a free tier, no ownership history (so each lab has
     had its folder all along), and one scrypt hash of a made-up password for everyone.
   - `tests/test_load.py`: an installation of 5,000 files and five labs, and four seconds of seven people, one of
     each role and two PIs, with no error (SQLite only).
   - *Done*: at 10 million files the installation took 107 s and 2.8 GB. Two runs of 25 people for five minutes
     made the same 741 pages, with no error; every page with 30 or more in each run had its median within 10% (all
     pages: 42 and 44 ms; the 95th percentile, 93 and 94 ms), and the server's memory peaked at 1.3 and 1.4 GB.
2. **The baseline (Q6).**
   - 10, 25, 50, and 100 people for five minutes each on SQLite, and on PostgreSQL 16 (in Podman on the same laptop,
     an installation of its own from `load_setup.py --database-url`); every page within the budgets with no error,
     times not growing with people, about one core busy. The table is in the architecture guide (*Many people at
     once*).
   - Past 100 people (the same 100 clicking two to five times as often), the load test now also samples the cores
     the server keeps busy. At 19 pages a second, as fast; at 28, a 99th percentile of 0.9 s and 3 errors; at 4 and 5
     times, Pinax stopped for 30 s at a time and answered *Internal error*, its processors idle. Every thread's stack,
     dumped at the stall (`faulthandler` on a signal): all 40 of the web server's threads waiting for a catalog
     connection, 32 of them in the sign-in check (`signin.find_session`). A request keeps the connection it took to
     check who is signed in while it waits for a thread to run the page, and the catalog's pool (15 connections on
     SQLite, 20 on PostgreSQL) is smaller than the 40 threads: a deadlock until the pool's 30-second timeout. For
     slice 3.
   - Why the slowest pages are slow, from the server's own request log (time in index queries and outside) and a
     count of catalog queries per request: PostgreSQL's 99th percentile of 380 ms is catalog round trips, on pages
     that load labs and people one by one (an administrator's *All data* 1,023 queries, *People and access* 620, a
     lab's suggested folders 175, a lab's charges 51), which grow with a site's labs and people. For slice 4.
   - Memory: DuckDB caches the indexes' blocks, 2.5 GB after five minutes of 100 people, between 1.4 and 3.0 GB over
     20 minutes. Up to eight indexes stay open with 4 GB each, past the sizing guide's 16 GB at a billion files. For
     slice 4.
3. **Turns and the catalog (Q6).**
   - The stall fixed: a request gives its catalog connection back once it knows who is signed in (`db.release`, a
     commit, which keeps what it loaded), so it holds one only while it runs in a thread, and never while waiting for
     one. Also given back before a password is checked (slow by design) and before the audit log's CSV is read with
     a session of its own. With SQLite the web app keeps up to 40 connections, one for each thread (15 before);
     PostgreSQL's pool is unchanged. A request that finds no connection within 10 seconds (30 before) answers 503,
     *Pinax is busy*, counted in `pinax_catalog_busy_total`, with the alert rule `PinaxBusy`. `tests/test_busy.py`
     makes the stall small (two connections, six threads, 40 requests at once: stuck for 25 s and failing without
     the fix, done in two seconds with it), and checks the busy answer.
   - The load test gained `--searchers N` (storage administrators searching whole filesystems by name, path, and
     age, one search after another, reported apart: finished, and stopped with what Pinax said), `--set` (a setting
     for the server it starts), and sends a request again when the server closed the kept-alive connection just as
     it went out, as browsers do: slice 2's 3 errors at three times were these.
   - The same 100 people clicking 3, 4, and 5 times as often: no errors (3, 89, and 156 before); one process
     serves about 28 pages a second on SQLite, 30 at four times on PostgreSQL, and past that pages slow down (a
     median of 0.9 s at four times and 1.5 s at five on SQLite) with three or four cores busy.
   - Eight administrators searching whole filesystems among the 100 people: on SQLite every budget met, the 299
     searches finished, the longest after 7.6 s; with a time limit of 5 s, the 33 that could not finish stopped at
     it with Pinax's explanation (16 waiting for a turn, 17 running). On PostgreSQL nothing failed, but *What
     changed* reached 1.2 s at the 95th percentile, past its budget, with the searches' DuckDB threads on every core
     and the database in a virtual machine on the same ones. For slice 4 (`PINAX_QUERY_THREADS`).
   - 200 people signing in at the same moment: every sign-in within 5.7 s on SQLite and 5.9 s on PostgreSQL (8.4 s
     before the password check gave its connection back), 1.3 GB more memory while 40 passwords are checked. Their
     first pages waited up to 59 s on SQLite and 19 s on PostgreSQL: about 25 requests a second with 3 of 10 cores
     busy, and requests of a few milliseconds waiting half a minute for a thread: the 40 threads most likely short
     of Python's interpreter, not of cores. For slice 4, with the catalog questions asked one by one.
   - SQLite's locking: no errors with every sign-in writing its session and audit event, 200 at once included, and
     sign-ins no slower than on PostgreSQL, whose writers do not share a lock. PostgreSQL's pool: no request turned
     away as busy in any run, its 20 connections enough for the 40 threads once requests give theirs back. That costs
     PostgreSQL a few round trips a request (about 4 ms here, through the virtual machine): at 100 people the median
     page took 62 ms, against 55 ms in slice 2.
   - At 100 people on SQLite, run back to back with 0.13.0's code (a worktree of slice 2's commit): the same, a
     median page of 38 ms in both and a 95th percentile of 104 and 108 ms. Two other runs of this slice's code had
     95th percentiles of 143 and 149 ms, one of them while the test suite ran: the 95th percentile varies by a third
     between runs, the median by a few percent.
4. **Tuning and sizing (Q6).**
   - Catalog questions asked lab by lab and person by person, now asked at once: every lab's PIs in one query, the
     first time a session asks and until it writes or rolls back (`models.pi_names`, for `Lab.pi_name`), people with
     their roles on *People and access*, and a lab's price and free tier once for each filesystem in its suggested
     folders (`plans.rate`). *All data* 1,023 queries to 24, *People and access* 620 to 13, the age map, change map,
     and cold folders of a filesystem's root 131 to 283 to 12 to 14, a lab's suggested folders 175 to 58. One request
     at a time, *All data*'s catalog request 69 to 129 ms on PostgreSQL instead of 400 to 576, 30 to 49 on SQLite
     instead of 125 to 161; a PI's 31 requests took 484 to 492 ms in all, against 494 to 500 before.
   - `PINAX_QUERY_MEMORY_LIMIT` is for every open index together (8 GB; it was 4 GB for each of up to eight):
     `IndexCache` sets each open index's share with DuckDB's `SET memory_limit` as indexes open and close. Every
     index is still opened with the same settings, since DuckDB refuses a second connection to a file open in the
     same process with others. Lowering a share lets cached blocks go (43 MB to 8 MB in a test); at rest an index
     of 10 million files keeps 43 MB. At three times the pace, 12 GB (4 GB each of the three open) and 8 GB did as
     well, 22.0 and 17.9 against 18.7 and 22.2 pages a second, alternating.
   - `PINAX_WEB_THREADS` (40), the web app's threads, and with SQLite its catalog connections. With 8, at four times
     the pace, 24.7 pages a second against 18.5 with 40, and the first pages of 200 sign-ins at once within 31 s
     against 57; but with eight administrators searching, those waiting for a turn held the threads, and the
     others' pages had a 95th percentile of 1.2 s against 0.5. The default stays 40, since at a billion files the
     root's reports and the searches take seconds; the deployment guide says when fewer pay.
   - `PINAX_QUERY_THREADS=5` of 10, with the searchers on PostgreSQL: the others' pages 642 ms at the 95th
     percentile against 733 ms and 2.0 s either side, the searches a quarter slower. The threads are each index's,
     shared by the searches and the pages of it; the guides no longer say fewer keeps a search from slowing pages.
   - More than one web process is left for later: the whole-filesystem turns, the metrics, and the searches in the
     background are each one process's. The deployment guide's sizing (*Many people at once*): one process serves
     about 250 to 300 people at once, bound by Python's work for each page rather than by cores, and what to change
     for more.
   - Measured while the laptop was in use, so runs are compared with their neighbours only. This slice's code
     against slice 3's at three times the pace, in three pairs: 9.9 against 15.6 pages a second, 11.8 against 12.2,
     and 13.2 against 15.7, while the same code varied between 17.9 and 22.2 in neighbouring runs; one request at a
     time, as above, the new code costs no more.
5. **File categories in the catalog (R14).**
   - `file_category` (migration `0007`): each category's key, label, description, place, extensions, and patterns;
     filled with `categories.DEFAULTS`, the twelve built in, the first time Pinax starts (as the default tags are),
     never again. `categories.check`: *other* last and empty, keys of their own, at most 24 categories and 25
     patterns, each pattern one DuckDB takes (RE2: `(a)\1` is refused).
   - The build takes the categories (`build_index(..., categories=)`; ingest and rebuild pass the catalog's): the
     extension join as before; patterns in one expression of them all, and for the names it matches a `CASE` of
     each in order, before the extension. The index records them, and a fingerprint of keys, extensions, patterns,
     and order (`categories.fingerprint`), in its metadata; an index from before, with keys and labels only, reads
     as the defaults. Removed files copied into `file_changes` from an index built with other categories are
     renumbered by key.
   - Each index reads its own: its per-category columns come back named by category (`cb0 AS "cb:genomics"`), so
     `Stats` adds and subtracts by category, and the API's `cat_bytes` and `cat_files` are objects keyed by
     category; file rows carry the category's key; a search by a category an index lacks finds nothing in it, and
     a treemap sized by it measures nothing (`CAST(0 AS BIGINT)`, not `0`, which `ORDER BY` reads as a column's
     place). `/api/config` and the types report take the catalog's list, then any key a scan has that the catalog
     no longer does. The interface reads totals by key.
   - Outdated: `pinax rebuild --outdated` also rebuilds indexes whose fingerprint is not the catalog's, and
     `--latest` only each filesystem's latest. Searches in the background store the category's key; older ones,
     with a number, read it as the defaults'.
   - Measured at 10 million files (`bench_index.py --patterns 25`, `--categories 24`), three builds each, on the
     laptop in use: 0.13.0's code 22.1, 19.2, and 19.9 s; this slice's, default categories, 16.7, 15.8, and 16.6 s;
     25 patterns 18.2, 16.7, and 17.1 s (4% more); 24 categories 18.0, 17.4, and 17.2 s (6% more). Index 56.1 bytes
     a file with the defaults, 56.5 with the patterns' category, 56.9 with 24: within Q5's 60. The first runs had
     24 categories at 39.5 s, its per-folder totals step 17.4 s instead of 5.5, with two filtered sums for each
     category over every file; each folder's totals by category now come from its totals by type (`direct_cat`, a
     few rows a folder), which also takes the defaults' build from about 20 s to 16. Extrapolated to a billion files
     at 1.2 s a million: about 20 minutes, a minute more with 25 patterns or 24 categories, well within Q5's two
     hours, and 57 GB an index with 24.
   - Queries: 21 kinds, an index read by 0.13.0's code and this slice's in turn, totals 883, 945, and 917 ms
     against 939, 908, and 980; the smallest, 300 times each in one process, 3.8 ms and 3.8 to 4.0 for an icicle,
     22 to 23 and 22 to 25 ms for a two-level treemap: no report slower.
   - `tests/test_categories.py`: the catalog's defaults; patterns before extensions, first match winning; a
     category a site adds, before and after `pinax rebuild --outdated`; removed files keeping their categories; an
     index from before; the checks. `test_build.py` checks every folder's totals by category against its files.
     In a demo (seeded to a scratch folder): the age map sized and colored by a category, and a category added by
     pattern (`\.bam$`, *Alignments*), the latest snapshots rebuilt with `--latest`: *File types* lists it, and an
     older scan, not rebuilt, keeps `.bam` as sequencing data.

## Later waves (outlined)

| Wave | Release | Theme | Main content |
| --- | --- | --- | --- |
| 15 | 0.15.0 | Production pilot | The largest production scan available ingests and passes `pinax check`; build time, index size, and query times measured on production replace the extrapolations in `docs/ARCHITECTURE.md`; 100 million files on server-class hardware, including the change map, what changed, and changed scans at that size, and the folder counts of production (two billion files may mean 50 to 200 million folders, which every report that reads all folders grows with); the time limits and whole-filesystem query turns set for production; the load test of wave 14 run again on the pilot's server, with production's indexes (Q6); installation on a pilot server from the deployment kit, scanning on a schedule; IT and a few pilot labs use it, and what they find is fixed or recorded; each filesystem says whether its access times can be trusted, and where they cannot, views by last access are hidden or marked (E11); lab membership synced from the directory (A6), with the decision on the authoritative source of membership and ownership |
| 16 | 1.0.0 | Hardening | Budgets met at a billion files in production; accessibility audit; documentation complete, with the time to rebuild the indexes after losing a server; pilot and early-user feedback addressed |

## Parking lot

Ideas kept for later, and not on a path to be built. An idea leaves the parking lot when it is given a wave.

**Needs a system Pinax has no access to:**

| Idea | What it needs |
| --- | --- |
| Monthly digests for PIs: their storage, what changed, their charges, and their cleanup plan (was in wave 8) | A mail service |
| Alerts by email, Slack, or Teams when a scan or ingest fails (wave 10 keeps a metric that the site's monitoring can alert on) | A mail service or a chat workspace |
| Slack or Teams messages, such as a plan's progress or a filesystem filling | A chat workspace and an app registered in it |
| Cleanup plans sent as tickets to the storage team | The ticketing system's interface (ServiceNow, Jira) |
| Quotas beside folders (was in wave 8) | The storage system's quota interface |
| Cloud buckets and archives beside filesystems, priced by storage class | The cloud accounts' inventory reports (S3 Inventory, Azure Blob inventory) or the archive's catalog |
| The data of people who have left, by lab, for their PI to decide on | Who has left, from HR or the directory; the owners report already flags accounts that no longer exist |

**Not needed for now:**

| Idea | Notes |
| --- | --- |
| Two-factor codes for local sign-in (was in reach) | One-time codes from an authenticator app, with recovery codes. Local sign-in locks an account after five failures in a row, and sites that need more use single sign-on |
| Limits on sign-in attempts per network address (was in reach) | Slowing down an address that tries many accounts, beside the per-account lockout |
| A container image (was in reach) | The web app and automatic ingest in a container, with `/var/lib/pinax` as a volume. Scans would run outside it, since fs-inventory names owners from the scanning host's accounts and reads every folder with a capability a container grants only as root; so would `pinax scan`'s record of the filesystem's capacity. Worth it for a site that runs everything in Kubernetes or OpenShift |

**Not yet chosen:**

| Idea | Notes |
| --- | --- |
| Archiving with Tundra: folders marked *archive* in a cleanup plan, or tagged for archiving (wave 8), sent to [Tundra](https://github.com/robert-mcdermott/tundra) as archive requests, for IT to review, package, upload, and verify there | Tundra is a proof of concept; Pinax shares its model of labs, people, and folder ownership, so who may ask matches. Removing the primary copy stays with the data owner; Pinax's later scans show when it is gone, and Tundra's catalog that the archive is verified. Plan actions are kept open (wave 7) so an *archive* action can join them |
| Likely duplicates: files of the same name and size in several places | Grouping two billion files by name and size at every build is a large job; limited to large files (say 1 GiB and up) it is small. fs-inventory records no checksums, so these would be candidates, not proof |
| Recognizing data by its folder names: sequencing runs, cryo-EM sessions, imaging plates | Partly covered by the *Source* tag (wave 8) |
| Searching files within a collection | Within a lab, as fast as Find in a folder; across the organization, a whole-filesystem search under the time limits |
| Tags on single files | At two billion files, every tagged view would become a file-level query; tags stay on folders (wave 8) |
| Folder history exported as Parquet, for Tableau or Power BI | |
| An MCP server, so an AI assistant can answer storage questions with the person's own access | |

## Decisions still open

- The authoritative source of folder ownership (which lab owns each top-level folder), and how Pinax stays in step
  with it (wave 15).
- Which filesystems record access time reliably enough to show it by default: from wave 15 each filesystem has a
  setting for it (E11), and the pilot decides each production filesystem's.
- Scan cadence and snapshot retention for each filesystem.
- When production storage becomes reachable for wave 15, and which filesystem and labs the pilot uses.
- Who may see unmapped folders besides storage administrators and auditors (who see them from 0.12.0), for example
  division data managers.
