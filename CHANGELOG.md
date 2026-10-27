# Changelog

## 0.1.0 (2026-10-26)

First release (wave 1, "see the data"): a working proof of concept. See
[pinax-spec/roadmap.md](pinax-spec/roadmap.md) for what wave 1 contains and what comes next.

- Ingest fs-inventory 1.0.0 Parquet scans into per-snapshot indexes (DuckDB and Parquet) with subtree totals
  for every directory.
- Age map treemap with drill-down, and reports for any folder: ages, file types, cold folders, owners, and
  file search with CSV export.
- Home pages for PIs and lab members and for IT, storage health, scans, and people and access.
- Demo seed, tests, synthetic-scale benchmarks, and documentation.
