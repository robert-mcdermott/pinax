# How Pinax sees storage

The ideas the rest of the documentation relies on, for the people who use Pinax and the people who run it. Each is
explained in depth elsewhere; this is the map.

## Filesystems and scans

A **filesystem** is a body of storage Pinax reports on, such as *Fast* at `/fh/fast`: registered once by an
administrator, by the root its scans record, with a name people know it by.

Pinax never crawls storage itself. **fs-inventory**, a separate scanner built for very large filesystems, walks the
filesystem's metadata (names, sizes, owners, and times, never file contents) and writes a **scan**: a folder of
Parquet files and a manifest. Scans run on a schedule, typically weekly, from a host that can read the filesystem.
A scan of two billion files takes hours, and costs about 40 bytes per entry on disk.

**Pinax is read-only.** It reads scans; it never opens, moves, changes, or deletes a file on the storage it reports
on. Cleanup plans and tags are records in Pinax that people act on themselves.

## Snapshots and indexes

**Ingest** turns a scan into a **snapshot**: an **index** (DuckDB and Parquet files under `PINAX_VAR_ROOT`) in which
every directory already carries the totals of everything below it. That is why a treemap of a petabyte draws in
tens of milliseconds: a page never adds up files, it reads totals computed once per scan. Ingest also records each
top-level folder's totals, which are kept for good (the **folder history**), and what changed since the previous
scan.

Pinax keeps the newest six indexes of each filesystem (`PINAX_KEEP_SNAPSHOTS`); older snapshots are **pruned**:
their index is deleted, their folder history kept. Every report can show the latest scan or any earlier one still
kept in full.

Totals can be **checked**: `pinax check` compares every directory of an index with `fs-inventory rollup`, an
independent computation, and reports any difference. When fs-inventory could not read a folder, its totals are
lower bounds, and Pinax says so where they appear.

## Labs, departments, and folder ownership

The unit of ownership is a **top-level folder** of a filesystem, such as `/fh/fast/fujimoto_a`. Each is owned by
one **lab** (a PI's group) or **department** (a core facility, a division), or by no one. A lab may own folders on
several filesystems.

**People** belong to labs as a **PI** (or a department's owner) or a **delegate** (a lab manager, a staff
scientist). Members see exactly the top-level folders their labs own, everything below them, and nothing else: the
check is made on the server for every request, and other labs' folders do not appear at all. **Storage
administrators** see every filesystem and folder, and the administration pages.

Ownership changes over time (a folder moves to another lab, a lab closes), and Pinax keeps when each change
happened, so a past month is charged as it was.

## Sizes

Two sizes, shown side by side where they differ:

- **Size** (logical): the sum of the files' sizes, what people think of as how much data they have.
- **Space on disk** (allocated): what the storage actually uses, smaller for compressed or sparse files, larger for
  many small files on large blocks.

A file with several **hard links** is counted once, at its first link by path; its other links are files with no
size. Sizes are shown in binary units (TiB, GiB) unless a price or capacity was set in decimal ones (TB).

## Ages and cold data

Each file has times: when it last **changed** (modified), and when it was last **read** (accessed, where the
filesystem records it reliably). Ages are measured from the scan's start, by modification time unless a view says
otherwise, in seven **age buckets**: under 90 days, 90 days to 1 year, 1 to 2, 2 to 3, 3 to 5, 5 to 10, and 10 years
or more.

A folder's **average age** is weighted by size, so one large old file outweighs many small new ones. **Cold data**
is data unchanged for `PINAX_COLD_DAYS` (two years by default); a **cold folder** is the largest folder in which
everything is that old, a unit that could be archived or deleted whole.

The age map's colors run from red (changed recently) to blue (cold), split at two years.

## Change over time

Every report compares scans: the **change map** colors folders by how much they grew or shrank since an earlier
scan; **what changed** lists the folders and files new and removed between two scans; **history** draws each folder
over all its scans. A **forecast** carries the trend of the last six months of scans a year ahead, with a range and a
check of how well the same method did three months ago; a step (a project moved in or out) restarts the trend.

## Costs and charges

Administrators set each filesystem's **price** (per TB or TiB a month, from a day on) and **free tiers** (an amount
free for every lab, for every department, or for one lab as an exception). A lab's monthly **charge** is worked out
from its folders' sizes over the month, scan by scan, less what is free, at the price on each day: a month with a
price change is charged in parts. This month's charge is an estimate to the month's end. **Chargeback** is every
lab's charges for a month, with a CSV statement for finance.

## Cleanup plans and tags

A **cleanup plan** lists folders a lab means to delete, move to cheaper storage, or compress, and shows what each
would free and how much the lab's charge would fall. Later scans show what was done. Pinax suggests folders worth
adding, and follows a folder that was renamed rather than removed (by its inode).

**Tags** describe folders in terms a lab and IT share: project, grant, data stage, source, classification, IRB
protocol, a public copy, keep until, legal hold. Administrators keep the standard list; labs add their own. A tag
applies to everything below its folder. *Collections* finds data by tag; charges split by grant; folders on legal
hold or kept until a date stay out of cleanup plans.

## The catalog

Everything that is not an index (people, labs, folder ownership, filesystems, snapshot records, folder history,
prices, plans, tags, views, the audit log) is in the **catalog**: SQLite in `PINAX_VAR_ROOT` by default, or
PostgreSQL. It is small, and it is the one part that cannot be made again from scans, so it is backed up. Indexes
can be rebuilt from their scans.

Every change made in the app, by import, or from the command line is **audited**: who, what, when, and from where.
