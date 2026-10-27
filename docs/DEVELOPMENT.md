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
  auth.py          identity (demo cookie or SSO header) and folder access
  explore.py       explore and report services: resolving places, merging results across places
  serialize.py     JSON views of catalog objects
  api/             routers: identity, explore, admin, access
  seed.py          demo filesystems, people, and scans
  cli.py, main.py  command line and FastAPI app
  static/          index.html, app/, css/pinax.css, js/, img/
bench/             synth_scan.py (synthetic scans of any size), bench_index.py (build and query timings)
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
| `GET /api/home`, `GET /api/history?fs=&folder=` | Folder cards and lab totals; one folder's history |
| `GET /api/admin/overview`, `/health`, `/names`, `/snapshots`, `/labs`, `/people` | Administrator views |
| `POST /api/admin/folders`, `POST /api/admin/labs/{slug}/members` | Folder ownership and membership changes |

## Frontend conventions

The interface is plain HTML, CSS, and JavaScript modules with no framework or build step, adapted from Tundra.

- `js/core.js`: safe `html` templating, `api()`, formatting, icons, the header and theme toggle, toasts and
  modals, the hash router, and Pinax helpers (`displayPath`, `placeHash`, `parseQuery`).
- `js/viz.js`: age buckets, color modes, size metrics, legends, and small charts (age bars, histograms, bars).
- `js/treemap.js`: the squarified treemap, drawn as positioned links so tiles are real navigation targets.
- `js/shell.js`: the sidebar and the scope header (breadcrumbs, scan date, links to every report for the same folder).
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
| `--age-0` … `--age-6` | Age buckets, youngest to oldest. One hue (orange to deep red), monotone lightness |
| `--share-0` … `--share-4` | "Share of" color modes (teal), lowest to highest |
| `--tile-none` | Tiles with no files, or none of the chosen kind |
| `--tile-frame`, `--tile-ink` | The frame around nested tiles; dark label text on light tiles |

The ramps were checked with the dataviz ordinal validator (monotone lightness, adjacent steps at least 0.06
apart in OKLCH lightness, a single hue within 40 degrees, and the step nearest the surface at least 2:1
against it). In dark mode the age ramp is *not* inverted, so red means old in both themes; its darkest step
clears 2:1 against the navy surface. Re-run the validator if you change any step.

Rules:

- **Age is ordinal**: always use the age ramp for it, in bucket order. Never use those reds for anything else.
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

The interface shows control characters and bidirectional overrides in names as escapes (`visible()` in
`core.js`, applied to every value `html` puts in text). Attribute values keep the exact text, since some are
read back.

## Demo data

`seed.py` defines the demo filesystems (`FILESYSTEMS`), labs (`LABS`), unmapped folders, people (`PEOPLE`),
each folder's layout (`lab_files`), problem names, the activity between the two scans, and the demo-only owner
rewrite. Sizes and times are deterministic for a given `--scale`.
