# Changelog

## Unreleased

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
