# Changelog

## Unreleased

### Added

- **Independent cross-check (wave 2, slice 1).** `pinax check` compares every directory of a snapshot index with
  `fs-inventory rollup`, run on a shadow of the scan with Pinax's age buckets: files, directories, bytes,
  allocated bytes, incomplete directories, oldest and newest modification time, newest access and change time,
  and files and bytes per age bucket. It takes a snapshot ID or a scan directory, lists the directories that
  differ, and exits 1 on any difference. The demo scans and 1- and 10-million-file synthetic scans match on
  every directory; tests run the check on an edge-case scan when fs-inventory is installed.

### Fixed

- **Age buckets at their edges (wave 2, slice 1).** Ages are whole microseconds from the scan's start instead of
  fractional days, whose rounding put a file exactly on a bucket edge in the younger bucket for about one scan
  start time in eleven (one in four at the ten-year edge). Indexes are now version 2.
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
