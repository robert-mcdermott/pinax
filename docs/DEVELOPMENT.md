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

## Code layout

```text
src/pinax/
  config.py        settings (see docs/CONFIGURATION.md)
  db.py            catalog engine and sessions (SQLite in WAL mode)
  models.py        catalog schema: people, labs, folder ownership, filesystems, snapshots, folder history, audit
  categories.py    file categories, type normalization, age buckets
  ingest/build.py  fs-inventory scan -> snapshot index (DuckDB SQL)
  crosscheck.py    `pinax check`: every directory of an index against fs-inventory rollup
  index.py         SnapshotIndex: read-only queries over one index; Stats: additive totals
  snapshots.py     running scans, ingest (one at a time, under a file lock), retention, the cache of open indexes
  watch.py         `pinax ingest --watch`: each new scan under a folder once, and a heartbeat for *Scans*
  onboard.py       `pinax import`: labs, people, memberships, and folder ownership from CSV (docs/IMPORT.md)
  people.py        people: adding, editing, roles, deactivation (shared by the app and the CLI)
  labs.py          labs and departments: adding, editing, deactivation; slug rules shared with the import
  signin.py        local sign-in: password hashes, sessions, lockout
  ownership.py     who owned each top-level folder when, for charging past months
  costs.py         prices and free tiers per filesystem, from a day on
  charges.py       monthly charges: time-weighted, per lab or department and filesystem
  auth.py          identity (demo cookie or SSO header) and folder access
  explore.py       explore and report services: resolving places, merging results across places
  serialize.py     JSON views of catalog objects
  api/             routers: identity, sign-in, explore, admin, access, costs, health
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
- **Schema changes** to the catalog: edit `models.py`, then re-seed (`uv run pinax seed`). There are no
  migrations yet; Alembic must be added before production data exists.
- **New settings**: add the field to `config.py`, and document it in `docs/CONFIGURATION.md` and `.env.example`.

## HTTP API

Documented automatically at `/api/docs`. The main endpoints, each taking optional `fs` and `path` (without
them, the person's home), and `lab` to limit home to one lab:

| Endpoint | Returns |
|---|---|
| `GET /api/tree?metric=&depth=1\|2` | The node, its children (and grandchildren) as treemap tiles, breadcrumbs, scan date |
| `GET /api/types` | Bytes and files by category and type |
| `GET /api/owners` | Bytes and files by owner, flagging deleted accounts |
| `GET /api/cold?older=N` | Self-contained folders unchanged for at least age bucket N |
| `GET /api/files?q=&category=&ext=&min_size=&older=&newer=&uid=&sort=&fmt=csv` | Files, with totals; CSV download |
| `GET /api/home`, `GET /api/history?fs=&path=&lab=` | Folder cards and lab totals; a place's history, with the forecast of its size |
| `GET /api/admin/overview`, `/health`, `/names`, `/snapshots`, `/labs`, `/people` | Administrator views |
| `POST /api/admin/folders`, `POST /api/admin/labs/{slug}/members` | Folder ownership and membership changes |
| `GET`, `PUT /api/admin/labs/{slug}`, `POST /api/admin/labs`, `POST /api/admin/labs/{slug}/deactivate` and `/reactivate` | A lab or department, with its record; adding, changing, and deactivating one |
| `GET /api/charges?month=&lab=&history=` | A person's labs' charges for a month, with their past months |
| `GET /api/admin/costs`, `/api/admin/costs/charges?month=&fmt=csv` | Prices and free tiers; every lab's charges for a month, or its CSV statement |

## Frontend conventions

The interface is plain HTML, CSS, and JavaScript modules with no framework or build step, adapted from Tundra.

- `js/core.js`: safe `html` templating, `api()`, formatting, icons, the header and theme toggle, toasts and
  modals, the hash router, and Pinax helpers (`displayPath`, `placeHash`, `parseQuery`).
- `js/viz.js`: age buckets, color modes, size metrics, legends, and small charts (age bars, histograms, bars).
- `js/treemap.js`: the squarified treemap, drawn as positioned links so tiles are real navigation targets.
- `js/timechart.js`: charts over time (stacked columns and lines on a shared date axis, with a hover and keyboard
  readout that several charts share, and a forecast drawn after the last scan, `ahead`); `js/history.js` draws the
  history view with it.
- `js/costs.js`: *Costs*, each filesystem's prices and free tiers (`costs.py`, `api/costs.py`).
- `js/charges.js`: *Storage charges*, what a person's labs pay each month and how, and *Chargeback*, every lab's
  charges for a month, for administrators (`charges.py`, `api/costs.py`).
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
a name, so a run on a real filesystem can be recorded as it is (`--json FILE` saves it).

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

## Deployment files

`deploy/` holds what `docs/DEPLOYMENT.md` installs: the systemd units, `pinax.env`, and an oauth2-proxy
configuration. `tests/test_deploy.py` checks that each unit's command is one `pinax` accepts and that the settings
they name exist. `scripts/sso-check.py --oauth2-proxy PATH` runs a real oauth2-proxy against a stand-in OpenID
Connect provider on this machine and checks header mode end to end.

The interface shows control characters and bidirectional overrides in names as escapes (`visible()` in
`core.js`, applied to every value `html` puts in text). Attribute values keep the exact text, since some are
read back.

## Demo data

`seed.py` defines the demo filesystems (`FILESYSTEMS`), labs (`LABS`), unmapped folders, people (`PEOPLE`),
each folder's layout (`lab_files`), problem names, the six months of activity between its scans (`month_files`,
`REMOVED`, `MOVED`, `COMPRESSED`, `JOINS`, summed up in `MONTHS`), and the demo-only rewrite of each scan's owners
and date (`_make_demo_scan`). Sizes and times are deterministic for a given `--scale`, relative to when it runs.
