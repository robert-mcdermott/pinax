# Developer guide

How the code is organized, the conventions to follow, and how to extend the interface without breaking the
light and dark themes or the validated chart colors.

## Setup and everyday commands

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

```bash
uv run pytest
```

```bash
uvx ruff check src tests bench
```

Use `uv` for everything: `uv add <package>` for dependencies and `uv run` for scripts. Tests create their own
temporary catalog and snapshot folders and never touch the demo. The end-to-end test and the rollup
cross-check tests run the real fs-inventory binary and are skipped when it is not installed.

### Tests on PostgreSQL

The catalog runs on SQLite or PostgreSQL, and the whole suite runs on both. With `PINAX_TEST_DATABASE_URL` set, each
test gets a schema of its own in that database, dropped afterwards. A throwaway PostgreSQL in a container, with its
data in memory:

```bash
podman run -d --name pinax-pg-test -e POSTGRES_USER=pinax -e POSTGRES_PASSWORD=pinax-test -e POSTGRES_DB=pinax -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data docker.io/library/postgres:16 -c fsync=off -c max_connections=200
```

```bash
uv sync --extra postgres
```

```bash
PINAX_TEST_DATABASE_URL=postgresql://pinax:pinax-test@127.0.0.1:55432/pinax uv run pytest
```

`docker` takes the same arguments. Tests about SQLite catalogs only (`conftest.sqlite_only`) are skipped there.

### Continuous integration

GitHub Actions run the same checks for every push (`.github/workflows/ci.yml`), each job on a fresh Ubuntu runner:

| Job | What it runs |
|---|---|
| Lint | `uvx ruff check src tests bench`, with ruff pinned |
| Tests on SQLite | `scripts/install-fs-inventory.sh`, `uv sync --frozen`, `uv run pytest` |
| Tests on PostgreSQL | the throwaway PostgreSQL above, then the same with `--extra postgres` and `PINAX_TEST_DATABASE_URL` |

Both suites install the pinned fs-inventory, so the tests that run the real binary run there rather than being
skipped. A pull request from a branch of this repository shows the runs of its pushes; one from a fork gets its own.
The workflow pins uv (`0.13.1`), ruff, and the actions (by commit, with the version beside each), so a run means the
same thing from one month to the next; update them together, deliberately.

## Code layout

```text
src/pinax/
  config.py        settings (see docs/CONFIGURATION.md)
  db.py            catalog engine and sessions (SQLite in WAL mode, or PostgreSQL)
  migrate.py       the catalog's migrations: upgrading at start-up, `pinax db`; revisions in migrations/versions/
  dbcopy.py        `pinax db copy`: a whole catalog into another, empty database
  logs.py          system and audit logs: a line per request with its ID, person, and address; audit events logged
                   once committed; text or JSON
  metrics.py       Prometheus metrics at /metrics: each filesystem's scans, size, and capacity from the catalog, and
                   the web app's requests, queries, and audit events
  models.py        catalog schema: people and their site roles, labs, folder ownership, filesystems, snapshots,
                   folder history, capacity, marked folders, API tokens, audit
  categories.py    file categories, type normalization, age buckets
  ingest/build.py  fs-inventory scan -> snapshot index (DuckDB SQL)
  crosscheck.py    `pinax check`: every directory of an index against fs-inventory rollup
  index.py         SnapshotIndex: read-only queries over one index; Stats: additive totals
  snapshots.py     running scans, ingest (one at a time, under a file lock), retention, the cache of open indexes
  watch.py         `pinax ingest --watch`: each new scan under a folder once, and a heartbeat for *Scans*
  onboard.py       `pinax import`: labs, people, memberships, and folder ownership from CSV (docs/IMPORT.md)
  people.py        people: adding, editing, site roles, deactivation (shared by the app and the CLI)
  labs.py          labs and departments: adding, editing, deactivation; slug rules shared with the import
  signin.py        local sign-in: password hashes, sessions, lockout
  oidc.py          sign-in with Entra ID, or another OpenID Connect provider (Authlib), at /auth/; sessions as local
  tokens.py        API tokens: making (hashed, shown once), finding, what each may do, revoking
  audit.py         the audit log: filters, pages of events, and its CSV
  review.py        the access review: who has access to what, since when, what is worth a look; its sign-off
  ownership.py     who owned each top-level folder when, for charging past months
  costs.py         prices and free tiers per filesystem, from a day on
  charges.py       monthly charges: time-weighted, per lab or department and filesystem; and the months ahead
  forecast.py      where a size is heading: the trend of its recent scans, with a range and a backtest
  capacity.py      each filesystem's capacity, what the storage reports, and when it fills
  marks.py         marked folders: followed from scan to scan by path, and by inode when they move
  plans.py         cleanup plans: the folders each lab means to delete, move, or compress
  tags.py          folder tags: the standard list, labs' own tags, and tagging folders
  views.py         saved views: pages people keep and share with their labs
  jobs.py          searches in the background: worker threads, results kept as Parquet
  auth.py          identity (demo cookie, SSO header, Pinax's session, or API token), what each person sees and may
                   change, and `/script/api/` for scripts
  explore.py       explore and report services: resolving places, merging results across places
  serialize.py     JSON views of catalog objects
  api/             routers: identity, sign-in, explore, admin, access, costs, capacity, plans, tags, views, tokens,
                   audit, review, health
  seed.py          demo filesystems, people, and scans
  cli.py, main.py  command line and FastAPI app
  static/          index.html, app/, css/pinax.css, js/, img/
bench/             synth_scan.py (synthetic scans of any size), bench_index.py (build and query timings),
                   bench_charges.py (monthly charges over a synthetic year of weekly scans)
tests/             pytest suite; scanfix.py writes exact fs-inventory-shaped scans
```

## Backend conventions

- **Routers stay thin**: parse input, call a service in `explore.py` (or `snapshots.py`), return JSON.
- **Authorization is server-side, on every call.** Resolve places with `explore.resolve`, which checks that
  the path is canonical and inside the filesystem, and that the person may see its top-level folder.
  Never trust the client for paths or labs. Answer 404, not 403, for folders a person may not see.
- **Seeing and changing are separate questions.** Guard a read that administrators and auditors may make with
  `require_sees_all`, one that finance may make too with `require_sees_charges`, and every change to the site's
  settings with `require_admin`. A lab's data is seen by `Identity.labs_seen` (its members and its division's
  heads) and changed by `Identity.lab_roles` (its PI and delegates). A request with an API token is the token's
  person, or a service with a role, and `tokens.may` decides what the token may do besides.
- **Bind every value.** Index queries take parameters; SQL fragments are built only from fixed lists
  (`METRICS`, `FILE_SORTS`). Never interpolate request input into SQL.
- **The index is immutable.** A snapshot index is never changed after it is built; build a new one instead.
  New per-directory measures go in `ADDITIVE` or `EXTREMES` in `ingest/build.py`, which flow through the
  roll-up, `Stats`, and the API automatically. Bump `INDEX_VERSION` when the layout or a measure's meaning
  changes.
- **Changes between scans** are kept per directory for the measures in `CHANGE_MEASURES` (`ingest/build.py`);
  a measure added there is compared with the previous scan's in `_change_measures()` in `crosscheck.py`.
- **The index must agree with fs-inventory.** A change to the build keeps `uv run pinax check` passing on the
  demo scans and on a synthetic scan (see Benchmarks). A measure fs-inventory rollup also computes is added to
  `_measures()` in `crosscheck.py`.
- **Age buckets use whole microseconds** (`epoch_us`), never fractional days, so a file exactly on an edge is
  in the older bucket, as in fs-inventory rollup.
- **Times** come out of DuckDB as epoch seconds (`epoch(...)`), never as timezone-aware values.
- **Schema changes** to the catalog are migrations (Alembic, `src/pinax/migrations/versions/`): edit `models.py`,
  bring a catalog up to date (`uv run pinax db upgrade`), then `uv run pinax db revision "What it changes"` writes
  the next revision from the difference. Read it before keeping it: Alembic sees a renamed column as one dropped
  and one added, and a new column on existing rows needs a server default or `nullable=True`. Never change a
  revision that has been released. `tests/test_migrations.py` checks that the migrations make the models' schema;
  run it on PostgreSQL too ([Tests on PostgreSQL](#tests-on-postgresql)).
- **Logging**: `log = logging.getLogger("pinax.<module>")`; the request's ID, person, and address are added to every
  line on their own. Never log a query string, a request body, a password, or a token, and name the catalog with
  `migrate.describe` (which hides its password). An audit event needs nothing more: adding an `AuditEvent` logs it
  once committed.
- **New settings**: add the field to `config.py`, and document it in `docs/CONFIGURATION.md` and `.env.example`.

## HTTP API

Documented automatically at `/api/docs`. Scripts send an API token as `Authorization: Bearer ...`, to `/api/...`
or, through a sign-on proxy, to `/script/api/...`, where only a token counts. The main endpoints, each taking optional `fs` and `path` (without
them, the person's home), and `lab` to limit home to one lab:

| Endpoint | Returns |
|---|---|
| `GET /api/tree?metric=&depth=1\|2` | The node, its children (and grandchildren) as treemap tiles, breadcrumbs, scan date |
| `GET /api/types` | Bytes and files by category and type |
| `GET /api/owners` | Bytes and files by owner, flagging deleted accounts |
| `GET /api/cold?older=N` | Self-contained folders unchanged for at least age bucket N |
| `GET /api/files?q=&category=&ext=&min_size=&older=&newer=&uid=&sort=&fmt=csv` | Files, with totals; CSV download |
| `POST`, `GET /api/files/jobs`; `GET`, `DELETE /api/files/jobs/{id}`; `GET /api/files/jobs/{id}/csv` | A search in the background, the person's searches, one with a page of its results, stopping or deleting one, and its CSV |
| `GET /api/home`, `GET /api/history?fs=&path=&lab=` | Folder cards and lab totals; a place's history, with the forecast of its size |
| `GET /api/admin/overview`, `/health`, `/names`, `/snapshots`, `/labs`, `/people` | Administrator views, for administrators and auditors |
| `POST /api/admin/folders`, `POST /api/admin/labs/{slug}/members` | Folder ownership and membership changes |
| `GET /api/admin/people/{username}`; `POST /api/admin/people`, `PUT /api/admin/people/{username}`, `POST /api/admin/people/{username}/password`, `/deactivate` and `/reactivate` | A person, with their labs, site roles (`roles`), tokens, and history; adding, changing, a temporary password, deactivating |
| `GET`, `PUT /api/admin/labs/{slug}`, `POST /api/admin/labs`, `POST /api/admin/labs/{slug}/deactivate` and `/reactivate` | A lab or department, with its record; adding, changing, and deactivating one |
| `GET /api/charges?month=&lab=&history=` | A person's labs' charges for a month, with their past months and the next twelve at their trend (`ahead`) |
| `GET /api/charges/split?lab=&month=&tag=` | A lab's month split by a tag's values, and the tags its folders have to split by |
| `GET /api/plans?lab=`, `/api/plans/here?fs=&path=`; `POST /api/plans`, `PUT`, `DELETE /api/plans/{id}`, `POST /api/plans/{id}/moved` | A person's labs' cleanup plans; a folder's place in its lab's plan; adding, changing, and taking out a folder; confirming or dismissing a move |
| `GET /api/plans/suggestions?lab=`, `/api/plans/all` | Folders worth adding to a lab's plan; every lab's plan, for administrators and auditors |
| `GET`, `POST /api/admin/tags`; `PUT`, `DELETE /api/admin/tags/{id}`, `POST /api/admin/tags/{id}/active` | The standard tags and labs' own; adding, changing, retiring, and deleting an unused one |
| `GET /api/tags/here?fs=&path=`, `/api/tags/below?fs=&path=`; `POST /api/tags/folders`, `DELETE /api/tags/folders/{id}`; `POST /api/tags/custom`; `POST /api/admin/tags/{id}/merge` | A folder's tags and those it inherits; the tagged folders below one; tagging and untagging; a lab's own tag; folding one into a standard tag |
| `GET /api/tags/filters`, `/api/tags/collection?want=ID[:value]` (repeated) | The tags a person can look for, with their values; the folders that have every wanted tag, with totals, history, and forecast |
| `GET`, `POST /api/views`; `PUT`, `DELETE /api/views/{id}` | A person's views and those shared with their labs; saving, renaming, sharing, deleting |
| `GET /api/admin/costs`, `/api/admin/costs/charges?month=&fmt=csv`, `/api/admin/costs/ahead` | Prices and free tiers; every lab's charges for a month, or its CSV statement; every lab's next twelve months, added up (for administrators, auditors, and finance) |
| `GET /api/admin/capacity`, `POST /api/admin/capacity/{fs}`, `DELETE /api/admin/capacity/{fs}/{id}` | Each filesystem's capacity, its schedule, and when it fills; setting and removing a capacity from a day on |
| `GET`, `POST /api/tokens`, `DELETE /api/tokens/{id}`; `GET`, `POST /api/admin/tokens` | A person's API tokens, making one (its secret is in this answer only), revoking one; everyone's, and making a service token |
| `GET /api/admin/audit?person=&action=&lab=&fs=&path=&since=&until=&offset=&limit=&fmt=csv` | A page of audit events, newest first, with the total and what to filter by; every one that matches as CSV |
| `GET /api/admin/access-review?fmt=csv`, `POST /api/admin/access-review/sign-off` | Who has access to what, with when each was given and what is worth a look, or its CSV; recording a review |
| `GET /auth/login?next=`, `/auth/callback`, `/auth/logout`, `/auth/signed-out` | With `PINAX_AUTH_MODE=oidc` only (404 otherwise): to the provider, back with its code, signing out of Pinax and the provider, and the page after |
| `GET`, `POST /auth/emergency` | The emergency sign-in (a form), with `oidc` and an emergency account only (404 otherwise) |
| `GET /api/health`; `GET /metrics` | Health for load balancers (no sign-in); Prometheus metrics, only with `PINAX_METRICS_TOKEN` as a bearer token |

## Frontend conventions

The interface is plain HTML, CSS, and JavaScript modules with no framework or build step, adapted from Tundra.

- `js/core.js`: safe `html` templating, `api()`, formatting, icons, the header and theme toggle, toasts and
  modals, the hash router, and Pinax helpers (`displayPath`, `placeHash`, `parseQuery`).
- `js/viz.js`: age buckets, color modes, size metrics, legends, and small charts (age bars, histograms, bars).
- `js/treemap.js`: the squarified treemap, drawn as positioned links so tiles are real navigation targets.
- `js/timechart.js`: charts over time (stacked columns and lines on a shared date axis, with a hover and keyboard
  readout that several charts share, and a forecast drawn after the last scan, `ahead`); `js/history.js` draws the
  history view with it.
- `js/capacity.js`: *Capacity*, each filesystem's capacity and when it fills (`capacity.py`, `api/capacity.py`).
- `js/plans.js`: *Cleanup plans*, and the cleanup plan box in the age map's panel (`plans.py`, `api/plans.py`).
- `js/tags.js`: *Tags*, the standard tags for administrators, and the tags box in the age map's panel (`tags.py`,
  `api/tags.py`).
- `js/collections.js`: *Collections*, finding data by tag.
- `js/views.js`: *Save view* on every report's header, and the saved views on the home pages.
- `js/searches.js`: *Searches*, searches run in the background.
- `js/costs.js`: *Costs*, each filesystem's prices and free tiers (`costs.py`, `api/costs.py`).
- `js/charges.js`: *Storage charges*, what a person's labs pay each month and how, and *Chargeback*, every lab's
  charges for a month, for administrators, auditors, and finance (`charges.py`, `api/costs.py`).
- `js/tokens.js`: *API tokens*, a person's own and, for administrators and auditors, everyone's; the tokens on a
  person's page (`tokens.py`, `api/tokens.py`).
- `js/audit.js`: *Audit log* (`audit.py`, `api/audit.py`); `js/review.js`: *Access review* (`review.py`,
  `api/review.py`).
- `js/changes.js`: the change map, on `js/treemap.js`, with its seven-step gold-to-teal scale (`--chg-*`);
  `js/changed.js`: what changed, the same comparison as lists.
- `js/shell.js`: the sidebar and the scope header (breadcrumbs, the scan date and its picker, links to every report
  for the same folder). The chosen scan is `state.at`, read from the address by the router and added by
  `placeQuery` to every place link and report request; `hashAt(day)` is the same page at another scan.
- `js/explore.js`, `js/reports.js`, `js/admin.js`: the views. `js/app.js`: routes and the home page.
- **Escape everything.** Build markup with `html`; use `raw()` only for markup you wrote.
- A view is `(element, query)`. The place (filesystem and path) lives in the URL, so every view is bookmarkable.

## Themes and colors

Light by default; the toggle sets `data-theme="dark"` on `<html>` and remembers it under `pinax-theme`. A
one-line script in each page's `<head>` applies it before the page draws. Components use the semantic tokens
inherited from Tundra (`--bg`, `--surface`, `--text`, `--accent`, ...); never hard-code colors.

### Chart colors

Charts use their own tokens in `css/pinax.css`, defined separately for each theme:

| Tokens | Used for |
|---|---|
| `--age-0` … `--age-6` | Age buckets, youngest to oldest: hot to cold. Red for under two years, blue from two years (the default cold threshold), palest either side of the split |
| `--share-0` … `--share-4` | "Share of" color modes (teal), lowest to highest |
| `--tile-none` | Tiles with no files, or none of the chosen kind (a true gray, apart from the palest blue) |
| `--chg-n3` … `--chg-p3` | The change map: gold for shrinking, a true gray for no change, teal for growing |
| `--tile-frame`, `--tile-ink` | The frame around nested tiles; dark label text on light tiles |

The ramps were checked with the dataviz ordinal validator (monotone lightness, adjacent steps at least 0.06
apart in OKLCH lightness, a single hue within 40 degrees, and the step nearest the surface at least 2:1
against it). The age scale and the change map are two arms each, checked as two ordinal ramps out from the
split, with the steps either side of it, and the no-files and no-change grays, at least ΔE 15 apart with normal
vision and 8 under color-vision deficiencies. In dark mode each arm brightens outward instead of darkening, so the
most recent and the oldest data stand out most on navy; red is recent and blue is old in both themes. Re-run the
validator if you change any step.

Rules:

- **Age is ordinal**: always use the age scale for it, in bucket order: red is recent, blue is cold. Never use
  those reds and blues for anything else; the change map has its own gold and teal.
- **Single-series bars** (categories, owners) use `--chart-1`; the label names each bar.
- **Tile labels** pick white or `--tile-ink`, whichever contrasts more with the tile, at draw time.
- **Never color alone**: tooltips, tables, and labels carry every value; each chart has a legend and a table view.
- Check both themes and a 375-pixel-wide window for anything new.

### Logo

The Pinax mark (`img/pinax-mark.svg`) is a tablet divided like a treemap, in the brand palette. Use the mark on
navy in the header, `img/pinax-logo.svg` (mark and wordmark) where there is room, and `img/favicon.svg` for tabs.

## Benchmarks

`bench/synth_scan.py` writes a synthetic fs-inventory scan of any size in DuckDB (ten million files in about
twenty seconds); `bench/bench_index.py` builds an index from it and times the queries the interface makes.
Use them to check that a change keeps directory-level queries flat as scans grow. `uv run pinax check SCAN_DIR`
builds a temporary index from the same scan and compares every directory with fs-inventory rollup.

`synth_scan.py --wide N --deep D` adds what real filesystems have and the model lacks: a folder of N files, a
folder of N/10 subfolders, and a chain D folders deep. `bench_index.py` prints the scan's shape (counts only:
depth, widest folders, longest path, flagged names, links, mount points) and the query timings, never a path or
a name, so a run on a real filesystem can be recorded as it is (`--json FILE` saves it). `--patterns N` builds with
N patterns on file names, of the kinds a site writes, and `--categories N` with N file categories (one for each of the
synthetic scan's types, ahead of the defaults), to measure what a site's own categories cost a build.

`synth_scan.py --evolve SCAN -o OUT --days 30` writes SCAN as it might look 30 days later, for checking and timing
comparisons between scans: `--remove` (2%) of its projects deleted, `--grow` (5%) of its folders with files gaining
about a fifth more, `--touch` (1%) of its files rewritten, and `--new-tops` (1) top-level folders copied from
existing ones; new and rewritten files are dated within the days, and the rest is unchanged, so older. The
manifest records what changed (`evolved`) and the scan it came from. Evolve the result again for a longer history.
Ingest them in order, or build with `build_index(..., previous=...)`, and `pinax check` compares the change with
fs-inventory rollup of both:

```bash
uv run python bench/synth_scan.py --evolve /scratch/synth-10m -o /scratch/synth-10m-next --days 30
```

`--started` dates a scan in the past, so a scan and the ones evolved from it can end today.

### The load test

The benchmarks above time one request at a time; the load test measures many people at once. `bench/load_setup.py`
makes an installation in a folder of its own: three synthetic scans a month apart, the last one today, ingested in
order; a lab for each top-level folder, with a PI and members, over five divisions; storage administrators, an
auditor, finance, and a head of each division; a price and a free tier; and one made-up password for everyone. It
takes about two minutes at ten million files, and 2.8 GB. `--database-url` puts the catalog on an empty PostgreSQL
database instead of SQLite.

```bash
uv run python bench/load_setup.py /scratch/load-10m --files 10000000
```

`bench/load.py` starts `pinax serve` from it (or uses `--url`), and simulated people each sign in with the password,
as a browser does on a first visit, then open one page after another, `--think` seconds apart (5 to 15). A page is
the API requests the interface makes for it, sent together; each role has its sidebar's pages, people of labs in
their lab's folders and its projects, administrators everywhere. `--people` arrive over `--ramp` seconds (60), and
`--minutes` (5) are measured after that, in the proportions of `--mix`. Who does what follows `--seed`, so two runs
make the same requests.

```bash
uv run python bench/load.py /scratch/load-10m --people 50 --minutes 5
```

Two harder cases. `--searchers N` adds N storage administrators searching whole filesystems, by name, path, and age,
one search after another: each reads every file, `PINAX_WIDE_QUERIES` run at once, and the others wait for a turn.
`--ramp 0` has everyone sign in at the same moment, each password checked with scrypt, slow by design. `--set
PINAX_SETTING=VALUE` (repeatable) gives the server it starts a setting of its own, to measure a change without editing
the installation.

```bash
uv run python bench/load.py /scratch/load-10m --people 100 --searchers 6 --set PINAX_QUERY_TIMEOUT=30
```

The report, in the installation's `reports/` as Markdown and JSON, has each page's and each API route's median,
95th and 99th percentile times, errors, and requests a second; the server's memory and the cores it keeps busy,
sampled every second; the searchers' searches, finished and stopped, with what Pinax said; and whether the budgets
of wave 14 are met. A request on a kept-alive connection the server closed just then is sent again, as browsers do,
and counted. `--compare A.json B.json` says whether two runs agree: each page's median,
and the pages a second, within 10%. The load test and Pinax share the computer, so a laptop shows where Pinax slows
and why more than what a server does. `tests/test_load.py` runs a small one, so it keeps working.

## Deployment files

`deploy/` holds what `docs/DEPLOYMENT.md` installs: the systemd units, `pinax.env`, and an oauth2-proxy
configuration. `tests/test_deploy.py` checks that each unit's command is one `pinax` accepts and that the settings
they name exist. `scripts/sso-check.py --oauth2-proxy PATH` runs a real oauth2-proxy against a stand-in OpenID
Connect provider on this machine and checks header mode end to end.

The interface shows control characters and bidirectional overrides in names as escapes (`visible()` in
`core.js`, applied to every value `html` puts in text). Attribute values keep the exact text, since some are
read back.

## Documentation and the website

The guides are Markdown in `docs/` ([README.md](README.md) is their map), and the website is built from them:
`node docs/site/build.mjs` makes `site/`, each page a guide or some of its sections, with screenshots of the demo in
both themes ([site/README.md](site/README.md)). A change that users or administrators would notice updates the guide
it belongs in: the user guide, the administration guide, the command line, configuration, deployment, or
troubleshooting. `tests/test_docs.py` checks that every command and subcommand has its section in `docs/CLI.md`,
that every guide is on a page of the site, and that every screenshot a guide shows exists in both themes.
`node docs/capture/capture.mjs` captures the screenshots from a demo of its own; re-capture them when the interface
or the demo changes.

## Demo data

`seed.py` defines the demo filesystems (`FILESYSTEMS`), labs (`LABS`), unmapped folders, people (`PEOPLE`),
each folder's layout (`lab_files`), problem names, the six months of activity between its scans (`month_files`,
`REMOVED`, `MOVED`, `COMPRESSED`, `JOINS`, summed up in `MONTHS`), and the demo-only rewrite of each scan's owners
and date (`_make_demo_scan`). Sizes and times are deterministic for a given `--scale`, relative to when it runs.
