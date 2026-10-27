# Roadmap

## Done (proof of concept)

- Snapshot index built from fs-inventory 1.0.0 scans: subtree totals for every directory (size, space on disk,
  files, age histograms by modification and access time, categories, oldest and newest times, mean ages),
  per-directory type and owner breakdowns, per-file Parquet split by top-level folder and sorted by path.
- Age map: squarified treemap with one or two levels, sizing by size, space on disk, files, one category, or
  data older than a threshold; coloring by average age, last change, oldest file, last access, cold share, or
  share of a category; tooltips, legend, table view, drill-down and breadcrumbs.
- Reports for any folder or a person's home: age profile, file types (with uncompressed sequencing data
  called out), cold folders, owners, and file search with CSV export.
- Home pages for PIs and lab members (folders across filesystems, change since the previous scan) and for IT
  (every filesystem, largest folders, biggest changes, unmapped folders, activity).
- Storage health: scan errors, folders not read completely, very large directories, small-file hotspots,
  problem names, deleted owners.
- People and access: labs and departments, members, folder ownership, with an audit trail.
- Scans: `pinax scan` and `pinax ingest`, snapshot list, retention that keeps folder history.
- Demo with two filesystems, real fs-inventory scans, and simulated activity; tests; synthetic-scale benchmarks.

## Next

1. **Scheduled scans and automatic ingest**: watch a scan folder, ingest each new verified scan, and alert on
   failures.
2. **Change maps**: color the treemap by growth or shrinkage since a chosen earlier snapshot, and a "what
   changed" report per folder.
3. **History charts**: growth and ageing over time for each folder, lab, and filesystem, from folder history.
4. **PostgreSQL and Alembic migrations** for the catalog.
5. **Precomputed cold units** for each age threshold at build time, so whole-filesystem cold reports are instant.
6. **Hard-link aware totals** using the scan's inode and link counts.
7. **Cost and chargeback views**: storage tiers and price per terabyte, by lab, department, and filesystem.
8. **Digests for PIs**: a periodic summary of their data (growth, cold data, deleted owners).
9. **Saved and shared views**: name a folder, report, and settings; share a link with a lab.
10. **Membership from the directory**: lab membership from LDAP or Entra groups instead of manual entries.
11. **Quotas**: show quota and usage beside each folder where the storage provides them.
12. **Large-search jobs**: run whole-filesystem searches in the background and notify when the CSV is ready.

## Open decisions

- **The authoritative source of folder ownership** (which lab owns each top-level folder) and how Pinax
  stays in sync with it.
- **Access time**: which filesystems record it reliably enough to show by default.
- **Scan cadence and retention** for each filesystem.
- **Cost model**, if chargeback views are wanted.
- **Who may see unmapped folders** besides storage administrators (for example division data managers).
