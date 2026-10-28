# Changelog

## Unreleased

### Added

- **Forecasts of size.** Every history comes with where the place is heading over the next twelve months: a
  straight-line trend through the last six months of scans (the median of the slopes between pairs of scans, so
  one odd scan barely moves it), carried forward from the latest scan, with a range and a backtest (what the same
  method forecast three months earlier, against the size now). A step, such as a cleanup, starts the trend again;
  a forecast needs six scans over eight weeks. *History* draws it after the last scan, as a dashed line over
  the shaded range, with *In a year* beside the other figures, a table of the months ahead, and the reason when
  there is none; the history CSV adds the months ahead (`kind` is `scan` or `forecast`). `/api/history` returns it
  as `forecast`.
- **Capacity, and when each filesystem fills.** *Capacity* (for administrators) shows how much each filesystem
  holds, how much is used, its growth per month, and the month it fills at its trend, with the earliest and
  latest within the range. `pinax scan` records what the storage reports for the filesystem (`statvfs`, in
  `pinax.json` beside the scan's manifest), and an administrator can set the capacity from a day on instead,
  including an expansion scheduled ahead (audited). Use is measured by the storage's own figure where scans have
  it, else space on disk. The overview shows each filesystem's share used and when it fills, and flags one that
  fills within `PINAX_FILL_WARNING_MONTHS` (12); a filesystem's *History* draws its capacity and shows *Fills*.
- **Labs and departments in the app.** *People and access* adds a lab or department (*Add lab or department*: a
  slug, a name, its kind, division, and research area), and each one's page changes its details, deactivates it
  (its members stop seeing its folders; its members and folders are kept, and its folders are still charged to
  it), and reactivates it, with its members, folders, and recent changes. Every change is audited as the import's
  are (`lab.created`, `lab.updated`, and `lab.deactivated`, `lab.reactivated`), and slugs follow the import's
  rules. Changing a lab's kind asks first, since its free tier for past months changes too.
- **Time limits for requests.** A request stops after `PINAX_QUERY_TIMEOUT` seconds (180 by default) and says
  what to narrow, and stops as soon as the browser leaves the page: the interface cancels a page's reads when
  another page opens, and Pinax interrupts their queries, so an abandoned search of a whole filesystem no longer
  keeps the server busy. At most `PINAX_WIDE_QUERIES` (2) file-level queries of a whole filesystem run at once;
  others wait their turn. `deploy/oauth2-proxy/pinax.cfg` waits five minutes for Pinax (`upstream_timeout`,
  oauth2-proxy 7.3 or later) instead of oauth2-proxy's 30 seconds.
- **New and removed files recorded at ingest.** Each index keeps every file new or removed since the previous
  scan (`file_changes`), so *What changed* lists them from a range of it: 15 ms for a million-file filesystem,
  against 0.4 s to compare the two scans' files. Against an older scan the files are still compared, in batches of
  groups of top-level folders, so memory is bounded by a group rather than the filesystem.
- **Sizing for two billion files** in `docs/DEPLOYMENT.md`: disk, memory, the room a build needs, what stays fast,
  and what to measure on the first production scan.
- **Guides for the demo and for real data.** `docs/DEMO.md` has how to run the demo, who is who, its six months
  and prices, and the walk-through, which used to be in the README; `docs/SETUP.md` is new, on running Pinax on
  your own data. The README is shorter and links to both.

### Changed

- **Index version 5.** Older indexes still answer, comparing files when asked; `pinax rebuild --outdated` adds
  the new and removed files to them.
- ***Find files* shows its results before counting them.** The count of all matches is a second pass over the
  files, so it is asked for separately (`/api/files/count`, or `total=false` on `/api/files`), and only when the
  page is full.
- **`pinax seed` runs only when `PINAX_ENVIRONMENT=demo`.** It deletes the catalog and indexes in
  `PINAX_VAR_ROOT`, and was refused only in `prod`, so a `dev` setup with real data could be wiped by mistake.
  The default environment is `demo`, so the demo is unchanged.
- **Guidance for operating Pinax.** `docs/ARCHITECTURE.md` says what Pinax keeps (the catalog, the indexes, and
  the scans), what each is for, what can be made again, and which to back up, and why the catalog moves to
  PostgreSQL in wave 10 (it said PostgreSQL was the production database; production uses SQLite until then).
  `docs/DEPLOYMENT.md` adds which scans to keep (Pinax never deletes them), disk totals, a safe online backup and
  restore of the catalog, reading the whole audit log, and what support staff need to know about charges and
  statements. The scan-size figure is the measured one, about 40 bytes per entry, everywhere.
- **The roadmap.** Forecasts (0.6.0), cleanup plans (0.7.0), and folder tags (0.8.0) come next, since they need
  no production storage; the production pilot is now wave 9 (0.9.0), and reach, action, and hardening follow it.
  Ideas that need a system Pinax has no access to, such as digests by email, Slack or Teams messages, and quotas
  from the storage system, are kept in a parking lot rather than planned.

## 0.5.0 (2026-10-27)

Fifth release (wave 5, "costs and chargeback"): what storage costs each lab and department. Administrators set a
price per month for each filesystem and free tiers for labs and departments, from a day on; each month is charged
on the time-weighted average of its scans, like a utility bill. Lab members see what their labs pay and how each
charge is worked out, and finance gets a monthly statement as CSV. The demo has prices, so its six months make six
months of charges. Next is the production pilot (0.6.0), when production storage is reachable. See
[pinax-spec/roadmap.md](pinax-spec/roadmap.md).

Upgrading from 0.4.0: the catalog gains `price`, `free_tier`, and `folder_owner` tables, created at start-up, so an
existing catalog keeps working; nothing is charged until a price is set. Ownership history starts with the upgrade:
a folder that changed owner before it is charged to its current owner for past months. `PINAX_CURRENCY` sets the
currency (USD by default). Indexes are unchanged (version 4).

### Added

- **Prices and free tiers (wave 5, slice 1).** *Costs*, for administrators, sets what each filesystem's storage
  costs per month, per GB, GiB, TB, or TiB, from a day on (earlier prices are kept, and future ones scheduled), and
  how much is free: one free tier for every lab and one for every department (which may be none), with exceptions
  for particular labs. Every change is audited. `PINAX_CURRENCY` sets the currency (USD by default).
- **Monthly charges (wave 5, slice 2).** Each lab's and department's charge on each filesystem for a month, from
  folder history: the time-weighted average of its folders' sizes (each scan holds until the next), less its free
  tier, at the price in effect, worked out exactly and rounded to the cent once per lab and filesystem. The month in
  progress is an estimate to its end. Storage no one owns is unbilled. For administrators at
  `/api/admin/costs/charges?month=YYYY-MM`.
- **Storage charges (wave 5, slice 3).** A page for everyone in a lab or department: what each of their labs pays
  for a month, by filesystem and folder, with each charge worked out in words (average size, less the free tier,
  at the price, for the days it applied), and each lab's past months. This month's charge is an estimate to the
  month's end and shows on *My data*. Administrators may look at any lab's.
- **Chargeback (wave 5, slice 4).** For administrators: every lab's and department's charges for a month, totals
  by filesystem with the prices that applied, and the storage no one owns, with a CSV statement for finance (a row
  for each lab or department on each filesystem, marked final or estimate). Monthly charges are faster: about
  25 ms for every lab for a month on a year of weekly scans of 2,000 folders (`bench/bench_charges.py`).
- **Ownership history.** Pinax now records each change of a top-level folder's owner (in the app or by
  `pinax import`), so past months are charged to the owners they had. The new `folder_owner` table is created at
  start-up; folders that have never changed need no history.
- **Demo prices.** `pinax seed` sets prices and free tiers: *Fast* at $25 per TB a month and $22 from the 16th of
  last month, a terabyte free for every lab and none for departments, the Lindqvist Lab's own 5 TB from the 10th of
  last month, and *Secure* at $40 per TiB with nothing free. The README demo script walks through the charges.

## 0.4.0 (2026-10-27)

Fourth release (wave 4, "people and sign-in"): Pinax can be run by a site without single sign-on, and without CSV
files. People are managed in the app, as in Tundra; a site without an identity provider signs people in with
passwords Pinax keeps, and can move to single sign-on later with one setting. The demo is kept as it was. Next are
costs and chargeback (0.5.0); the production pilot follows when production storage is reachable. See
[pinax-spec/roadmap.md](pinax-spec/roadmap.md).

Upgrading from 0.3.0: the catalog gains sign-in columns on `user` and a `login_session` table, both added at
start-up, so an existing catalog keeps working. Pinax now refuses to start with `PINAX_AUTH_MODE=demo` when
`PINAX_ENVIRONMENT=prod`; a production install uses `local` or `header`. Indexes are unchanged (version 4).

### Added

- **People in the app.** *People and access* has a People tab, with views of active people, administrators,
  people with no access, and inactive ones, and search; and a person editor to add someone, change their name,
  email, title, lab memberships, and storage administrator role, and deactivate or reactivate them. Every change is
  audited with its before and after, and the person's page shows their recent changes. People are never deleted; a
  deactivated person cannot sign in and keeps their access, unused, until reactivated. At least one active
  administrator always remains, and nobody can remove their own role or deactivate themselves. `pinax import`
  follows the same rules for usernames and emails. API: `GET`, `POST`, and `PUT /api/admin/people[/{username}]`,
  and `POST /api/admin/people/{username}/deactivate` and `/reactivate`.
- **Signing in without single sign-on.** `PINAX_AUTH_MODE=local`: people sign in with a username and a password
  Pinax keeps (scrypt). Administrators give people temporary passwords, made up by Pinax and shown once, which must
  be changed at the first sign-in; everyone can change their own and sign out. Five failed sign-ins in a row lock
  an account for 15 minutes. Sessions are kept on the server and end on signing out, after 12 hours, on
  deactivation, and when a password is set or changed. Every sign-in, failure, lock, and password change is
  audited. `pinax user add | password | list` makes the first administrator. Settings: `PINAX_SESSION_HOURS`,
  `PINAX_LOGIN_MAX_FAILURES`, `PINAX_LOGIN_LOCKOUT_MINUTES`, `PINAX_PASSWORD_MIN_LENGTH`, `PINAX_COOKIE_SECURE`.

### Changed

- Pinax refuses to start with `PINAX_AUTH_MODE=demo` when `PINAX_ENVIRONMENT=prod`: the demo lets anyone be anyone.
  Demo mode is otherwise unchanged, and remains the default.
- New catalog columns are added at start-up, so an existing catalog keeps working after an upgrade.

## 0.3.0 (2026-10-27)

Third release (wave 3, "change over time"): where the growth comes from and whether data is ageing. Each index
records how every directory changed since the previous scan, checked against `fs-inventory rollup` of both scans;
the demo has six months of scans to show it; and the interface gains history charts, a change map, a report of
what changed, and any kept scan in every report. Age is now shown hot to cold. Built and checked on small and
synthetic data (up to a million files); the production pilot (wave 4) measures it at scale. See
[pinax-spec/roadmap.md](pinax-spec/roadmap.md).

Upgrading from 0.2.0: indexes are now version 4. Indexes built by 0.2.0 still answer, but have no change since the
previous scan, so the change map and What changed for them compare by joining an older kept index, and `pinax
check` cannot check their changes; `pinax rebuild --outdated` builds them again, oldest first. Snapshots ingested
from now on record their filesystem's totals for History; earlier ones are summed from folder history, which is
slower on large catalogs. `/api/history` takes `fs`, `path`, `lab`, `snapshot`, and `at`, instead of `fs` and
`folder`. There is no catalog schema change.

### Added

- **Comparing two scans (wave 3, slice 1).** Each new index records how every directory changed since the
  filesystem's previous scan: both scans' files, folders, bytes, allocated bytes, and files and bytes per age
  bucket, and which folders are new or removed (`changes`, index version 4). `pinax check` compares it with
  `fs-inventory rollup` of both scans, and `pinax rebuild --outdated` adds it to existing snapshots, oldest first.
- **A history to show (wave 3, slice 2).** `pinax seed` takes six scans of each filesystem, dated a month apart,
  with six months of activity between them: a lab that grows, cleanups, a project moved between labs, a lab that
  compresses its FASTQ files, data ageing past the cold threshold, and a lab that joins (the Quintero Lab, with a
  new PI, Valeria Quintero). `bench/synth_scan.py --evolve SCAN --days N` writes a scan as it might look N days
  later, for checking and timing comparisons at any size.
- **History charts (wave 3, slice 3).** *History*, in the sidebar and every report's links, shows how a folder,
  lab or department, filesystem, or a person's home grew and aged over its scans: size by age as stacked
  columns, with files, cold share, and average age as small charts on the same dates, one readout for all four,
  and a table view with CSV. Top-level folders, labs, and filesystems have every scan; deeper folders the
  retained snapshots. `/api/history` takes the same places as the other reports (`fs`, `path`, `lab`,
  `snapshot`) instead of `fs` and `folder`.

- **The change map (wave 3, slice 4).** *Change map*, after the age map, colors the treemap by how much each
  folder grew (teal) or shrank (gold) since an earlier scan: the previous one by default, or any whose index is
  kept. New and removed folders are drawn, and a removed folder can be opened. Size by the larger of then and
  now, size now, or size then; one or two levels; a table view. `/api/changes` serves it.

- **What changed (wave 3, slice 5).** *What changed*, after the change map, lists for any folder and two scans
  where it grew and shrank most, the new and removed folders, and the largest new and removed files, with the
  number and size of each, and a CSV of each list. `/api/changed` serves it.

- **Older snapshots in the interface (wave 3, slice 6).** Every report has a scan picker beside its date: the
  latest scan, or any earlier day whose scan is kept in full; an older scan is marked, with a way back, and links
  keep it. A day kept only as folder totals opens History, which marks it. *Scans* links each snapshot. Every report
  endpoint takes `at` (a day), and `/api/scan-days` lists a place's scan days.

### Changed

- **Age colors, hot to cold.** The age map, age bars, legends, and history now show recent data in red and cold
  data in blue: red from under 90 days (the darkest) to one to two years (the palest), blue from two to three years
  (the palest) to ten years or more (the darkest), split at the default two-year cold threshold. The orange-to-red
  ramp made all data look warm. Both themes have their own validated steps; tiles without files are a lighter gray.

- Ingest records each snapshot's whole-filesystem totals, so a filesystem's history reads one row per scan.
  Snapshots ingested earlier are summed from folder history instead, which is slower on large catalogs (about a
  second for 300,000 folder rows).

## 0.2.0 (2026-10-26)

Second release (wave 2, "ready for real storage"): everything a first production deployment needs, built and
checked without production storage, which the production pilot (wave 4) brings. Every directory is checked
against `fs-inventory rollup`; real-world shapes and 100 million files are handled; new scans are ingested
automatically; labs and people are imported from CSV; and there is an installation guide with systemd units and
oauth2-proxy sign-on. See [pinax-spec/roadmap.md](pinax-spec/roadmap.md).

Upgrading from 0.1.0: indexes are now version 3. Indexes built by 0.1.0 still answer, but they have neither the
precomputed cold folders nor the age-bucket fix below; `pinax rebuild --outdated` builds them again from their
scans. The catalog gains a `heartbeat` table (created at start-up) and a `skipped` snapshot status.

### Added

- **Independent cross-check (wave 2, slice 1).** `pinax check` compares every directory of a snapshot index with
  `fs-inventory rollup`, run on a shadow of the scan with Pinax's age buckets: files, directories, bytes,
  allocated bytes, incomplete directories, oldest and newest modification time, newest access and change time,
  and files and bytes per age bucket. It takes a snapshot ID or a scan directory, lists the directories that
  differ, and exits 1 on any difference. The demo scans and 1- and 10-million-file synthetic scans match on
  every directory; tests run the check on an edge-case scan when fs-inventory is installed.
- **Real-filesystem shapes (wave 2, slice 2).** `synth_scan.py --wide N --deep D` adds a folder of N files, a folder
  of N/10 subfolders, and a deep chain; `bench_index.py` reports a scan's shape and query timings without any path
  or name. `pinax check --age-depth N` bounds rollup's memory on very large scans.
- **Deployment kit (wave 2, slice 6).** `deploy/` holds systemd units for the web app, automatic ingest, and a
  weekly scan per filesystem (run as an unprivileged user, with the scan given only `CAP_DAC_READ_SEARCH`), an
  environment file, and an oauth2-proxy configuration. `docs/DEPLOYMENT.md` is now a step-by-step installation
  guide. `/api/health` needs no sign-in and names nothing: `ok`, `degraded` (a missing index, or automatic ingest
  stopped), or 503. `pinax rebuild SNAPSHOT_ID... | --outdated` builds ready snapshots' indexes again from their
  scans, in place, after an upgrade or a lost index; the web app opens rebuilt indexes without a restart.
  `scripts/sso-check.py` checks header mode behind a real oauth2-proxy with a stand-in identity provider.
- **Bulk onboarding (wave 2, slice 5).** `pinax import` reads labs, people, memberships, and folder ownership from
  up to four CSV files (format in `docs/IMPORT.md`, examples in `examples/import/`). It lists every change first and
  changes nothing without `--apply`; any ambiguous or invalid row (a key listed twice, a username differing only in
  case, a reference to something that doesn't exist, an unknown column) refuses the whole import with its file and
  line. Applied changes are audited one by one with the interface's actions, plus a summary with each file's
  checksum. Running it again makes only the differences.
- **Automatic ingest (wave 2, slice 4).** `pinax ingest --watch [FOLDER]` checks a folder every few minutes
  (`--interval`) and ingests each new complete scan once, after `fs-inventory verify`. Scans still running or
  interrupted are left until finished; incomplete and failed ones are recorded as skipped, with why. Failed and
  skipped scans show on *Scans* with the error and are never retried automatically. Watchers and manual ingests
  share a lock, so any number can run at once without building two indexes together or ingesting a scan twice.
  *Scans* shows whether the watcher is running; `--once` runs one pass for a timer and exits 1 if a scan failed.
- **Build threads fit the memory limit (wave 2, slice 3).** Steps that read the scan use only as many threads as
  fit `PINAX_BUILD_MEMORY_LIMIT`, from the size of the scan's row groups, and halve them and retry if a step still
  runs out of memory. The 8 GB default builds 100 million files in about two minutes on a 10-core laptop; 17 GB
  and all ten threads were no faster.
- **Precomputed cold folders (wave 2, slice 3).** Each index holds, per age threshold, the largest folders in which
  nothing is younger (`cold_units`), so cold-folder reports for a whole filesystem read a range of rows instead
  of joining every directory with its parent. A file exactly on a threshold now counts as old enough, as in the
  age histograms. Indexes are now version 3; older ones still answer, at query time.
- **More file types (wave 2, slice 2).** Camera photo and video formats (HEIC, WebP, camera raw, action-camera
  video), macro-enabled spreadsheets and Access databases, Visio drawings and Windows metafiles,
  executables and libraries, and Apple disk images now have categories; a real disk had 35% of its bytes in
  *other* before and 0.8% after. New snapshots use them.

### Fixed

- **Sign-on headers from anyone (wave 2, slice 6).** In header mode, Pinax believed the username header on any
  request that reached it. It now believes it only from `PINAX_TRUSTED_PROXIES` (this host by default) and
  refuses it from anywhere else.
- **Large scans ran out of memory (wave 2, slice 3).** Building the index of a 100-million-file scan stopped at the
  8 GB limit: fs-inventory writes row groups of about a million entries, each thread reading one decodes all of
  it, and ten threads needed more than 8 GB. See the thread budget above. Files are also staged and sorted in at
  most 64 groups of top-level folders (`files/gNNNNNN/`) instead of one partition per folder, so the staging
  write keeps a bounded number of files open however many top-level folders there are; indexes built before
  still read.
- **Treemaps of wide folders (wave 2, slice 3).** The two-level treemap ranked the children of every subfolder,
  not only of the 60 shown, and ranked all subfolders to total the rest: 310 ms for a root with 2,000 top-level
  folders, now 39 ms.
- **Age buckets at their edges (wave 2, slice 1).** Ages are whole microseconds from the scan's start instead of
  fractional days, whose rounding put a file exactly on a bucket edge in the younger bucket for about one scan
  start time in eleven (one in four at the ten-year edge). Indexes are now version 2.
- **Nanosecond scans (wave 2, slice 2).** Scans made with `--time-unit ns` failed to build; their times are now
  kept as microseconds, truncated toward zero as fs-inventory rollup does.
- **Leftover parts (wave 2, slice 2).** The build reads only the parts a scan's manifest lists, so files left by an
  interrupted run in the scan folder are not counted twice.
- **Odd names in the interface (wave 2, slice 2).** Control characters and bidirectional overrides in names are
  shown as escapes, so a name with a newline no longer looks like one with a space and `report\u202Etxt.exe` no
  longer displays as `reportexe.txt`. Deep folders no longer push the page sideways: long breadcrumb trails
  collapse and long paths are clamped.
- **Revisited directories (wave 2, slice 1).** A directory reached a second time under `--detect-cycles` no
  longer counts as incomplete; its contents are counted where it was first found, as fs-inventory does.

## 0.1.0 (2026-10-26)

First release (wave 1, "see the data"): a working proof of concept. See
[pinax-spec/roadmap.md](pinax-spec/roadmap.md) for what wave 1 contains and what comes next.

- Ingest fs-inventory 1.0.0 Parquet scans into per-snapshot indexes (DuckDB and Parquet) with subtree totals
  for every directory.
- Age map treemap with drill-down, and reports for any folder: ages, file types, cold folders, owners, and
  file search with CSV export.
- Home pages for PIs and lab members and for IT, storage health, scans, and people and access.
- Demo seed, tests, synthetic-scale benchmarks, and documentation.
