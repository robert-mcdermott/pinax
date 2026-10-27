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
Wave 3, the production pilot, is next; it starts when production storage is reachable from the development
machine or a pilot server.

The order follows one principle:
1. **Trust first.** Totals that agree with an independent computation on every directory, and that say when
   they are lower bounds.
2. **Then real storage at real scale.** First everything a production deployment needs, checked on real local
   scans and on synthetic data at scale (wave 2); then production storage itself and a pilot (wave 3).
3. **Then change over time.** Where the growth comes from, and whether data is ageing.
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
| Colors | Interface tokens from Tundra; a validated one-hue age ramp (orange to deep red, red means old in both themes); a custom Pinax logo, not the Fred Hutch logo |
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
in wave 3.

### Wave 2 sequence

| Slice | Scope and requirements | Acceptance before the next slice | Status |
| --- | --- | --- | --- |
| 1 | Independent cross-check (Q1) | For every directory of the demo scans and of a 1- and 10-million-file synthetic scan, Pinax's files, directories, bytes, allocated bytes, oldest and newest modification time, and bytes and files per age bucket equal `fs-inventory rollup`'s (run with Pinax's bucket edges); a committed check script and a test that runs it when the binary is installed | Done |
| 2 | Real-world shapes (S3, Q1, Q3, Q4) | A real local scan (the maintainer's `~/Documents`) and a synthetic scan with a two-million-file folder, a 200,000-subfolder folder, and a chain 1,000 deep pass the slice 1 check; names that are not UTF-8 or hold control characters, mount points, nanosecond times, and leftover parts handled; laptop measurements recorded; no real scan, path, or name committed | Done |
| 3 | Scale to 100 million (S3, R3, Q3–Q5) | A 100-million-file synthetic scan, built and queried on the development laptop, meets Q3–Q5; whole-filesystem cold folders precomputed per age threshold at build time and answered within Q3; build memory and threads tuned and documented | Done |
| 4 | Automatic ingest (S4, O4) | `pinax ingest --watch` (or a timer) ingests each new verified scan under a folder once, skipping incomplete and already-ingested scans, safe if two run at once; failures shown on *Scans* with the error; tests for each case | Done |
| 5 | Bulk onboarding (A5) | Labs, people, memberships, and folder ownership imported from CSV with a dry run that lists every change, refusing ambiguous rows; changes audited; example files and docs | Done |
| 6 | Deployment kit (P6, A4) | systemd units for the web app and ingest; header mode checked behind oauth2-proxy with a local test identity provider; a health endpoint; `docs/DEPLOYMENT.md` rewritten as an installation guide, confirmed by the pilot's installation in wave 3 | Done |
| 7 | Release 0.2.0 | README demo script updated; version 0.2.0, changelog, pull request, tag, release | Done |

### Decisions for wave 2

- `fs-inventory rollup` is the independent reference for Q1: a separate implementation (Rust, streaming) of the same
  totals. Its age buckets are set to Pinax's edges (`--age-buckets 90d,1y,2y,3y,5y,10y`) and `--age-depth` to the scan's
  depth, so every directory has a histogram; any difference is a bug in
  one of them until explained.
- Production storage is out of reach while wave 2 is built (decided 2026-10-26). The real scan in slice 2 is the
  maintainer's `~/Documents`: real names and shapes, but one owner on a laptop disk. Owners and labs are covered by
  the demo seed and synthetic scans; production scans, server hardware, and the pilot are wave 3.
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
     the shapes above handled. Measuring production storage moved to wave 3.
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
   - *Done*: Q3–Q5 met at 100 million synthetic files on the laptop; server hardware and production are wave 3.
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
   - *Done*, except what only a real server shows: the pilot installation in wave 3 confirms the guide and units
     (`systemd-analyze verify` and the sandboxing options cannot be run on the development Mac).
7. **Release 0.2.0.**
   - The demo re-seeded with this wave's code: all four snapshots equal to rollup; the README demo script walked
     through (its numbers hold), with steps added for *Scans* and the CSV import.
   - README features, the architecture's production table, and the changelog's upgrade notes brought up to date.
   - Version 0.2.0 in `pyproject.toml`, `pinax.__version__`, and `uv.lock`; `CHANGELOG.md` dated; pull request
     `wave2` to `main`, then tag `v0.2.0` and the release.
   - *Done*.

## Later waves (outlined)

| Wave | Release | Theme | Main content |
| --- | --- | --- | --- |
| 3 | 0.3.0 | Production pilot | The largest production scan available ingests and passes `pinax check`; build time, index size, and query times measured on production replace the extrapolations in `docs/ARCHITECTURE.md`; 100 million files on server-class hardware; installation on a pilot server from the deployment kit, scanning on a schedule; IT and a few pilot labs use it, and what they find is fixed or recorded |
| 4 | 0.4.0 | Change over time | History charts per folder, lab, and filesystem (E6); a change map colored by growth since a chosen scan; what changed between two scans; explore older snapshots (S8) |
| 5 | 0.5.0 | Reach | PostgreSQL and migrations (P7); single sign-on with roles from directory groups and membership synced from the directory (A6); recertification report (A7); CI, container image (P6); metrics, logs, and alerts (O4, O5); security review |
| 6 | 0.6.0 | Action | Hard-link aware totals (R7); cost views (R8); periodic digests for PIs; saved and shared views; quotas beside folders; whole-filesystem searches as background jobs |
| 7 | 1.0.0 | Hardening | Budgets met at a billion files in production; accessibility audit; documentation complete; pilot and early-user feedback addressed |

## Decisions still open

- The authoritative source of folder ownership (which lab owns each top-level folder), and how Pinax stays in step
  with it (wave 5).
- Which filesystems record access time reliably enough to show it by default.
- Scan cadence and snapshot retention for each filesystem.
- The cost model, if cost views are wanted (wave 6).
- When production storage becomes reachable for wave 3, and which filesystem and labs the pilot uses.
- Who may see unmapped folders besides storage administrators (for example division data managers).
