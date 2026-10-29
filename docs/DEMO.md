# The demo

The demo shows Pinax with no setup: two research filesystems, six months of scans, labs and departments, people
to sign in as, and storage prices. Everything it shows comes from real fs-inventory scans of files the seed
writes. This guide says how to run it, what is in it, and how to walk through it.

## Running it

Requires [uv](https://docs.astral.sh/uv/). Install the pinned fs-inventory release into `bin/` (the script
verifies its SHA-256 checksum), then the Python packages, then the demo:

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

Then open <http://127.0.0.1:8001> and choose who to be.

`pinax seed` writes two demo filesystems of about 35,000 *sparse* files (123 TB of logical size on under 10 MB of
disk), with realistic ages going back fifteen years. It scans each one six times with fs-inventory, with six months
of simulated activity in between, dates the scans a month apart (the last one today), and ingests them. It takes
about fifteen seconds. Run it again at any time to start over: it resets `var/` and `demo/` each time, so it also
undoes whatever you changed during a demo. `--scale 0.5` writes about half as many files (the walk-through's
numbers are for the default).

The demo's catalog is SQLite in `var/`, with nothing else to run. It runs the same on PostgreSQL: with the driver
installed (`uv sync --extra postgres`) and `PINAX_DATABASE_URL` pointing at an empty database, `pinax seed` fills it
and `pinax serve` uses it ([PostgreSQL](DEPLOYMENT.md#postgresql)).

Sign-in in the demo is a persona switcher (`PINAX_AUTH_MODE=demo`, the default): anyone can be anyone, and
*Switch identity* (top right) changes who you are. That is fine on a laptop and wrong anywhere else, so Pinax
refuses to start in demo mode when `PINAX_ENVIRONMENT=prod`. And since seeding deletes the catalog, `pinax seed`
runs only when `PINAX_ENVIRONMENT=demo` (the default), never on a catalog set up for real data. To try Pinax on
real data instead, see [SETUP.md](SETUP.md).

## Who is who

The seed adds 14 labs and departments and 20 people; all names are fictional. The ones the walk-through uses:

| Person | Who they are | What they see |
| --- | --- | --- |
| Hanna Lindqvist | PI of the Lindqvist Lab (whole-genome sequencing) | One folder on *Fast*, mostly years old, and its charges |
| Aiko Fujimoto | PI of the Fujimoto Lab (single-cell genomics) | A lab that grows every month |
| Jordan Okafor | Lab manager, a delegate for the Fujimoto and Garcia labs | Both labs' folders side by side |
| Valeria Quintero | PI of the Quintero Lab | A lab that joins at the fourth scan |
| Casey Morgan | Director of the Genomics Shared Resource, a department | The department's sequencing folders |
| Avery Wong | Staff scientist, a delegate for a lab and a department | Folders from both |
| Riya Patel | Postdoctoral fellow | Nothing yet: no lab access |
| Priya Natarajan, Sam Rivera | Storage administrators | Everything, and the administration pages |

Three top-level folders belong to no one (`scratch_legacy` and `shared_reference` on *Fast*, `irb_archive` on
*Secure*), so only administrators see them, and nobody is charged for them.

## The six months

The six scans of each filesystem are dated a month apart, the last one on the day you seed. Every month the
Genomics Shared Resource delivers two Illumina runs and a nanopore run, the Fujimoto Lab adds a batch of samples
(it grows from 21 to 27 TB), and a few other labs add results. Besides that:

| Scan | Since the scan before |
| --- | --- |
| 1 | The starting point, five months ago |
| 2 | The Fujimoto Lab's Cell Ranger results arrive; the Chen and Haddad labs delete temporary files and checkpoints |
| 3 | A light-sheet project moves from the Bennett Lab to the Haddad Lab (`haddad_n/datasets/2024_lightsheet_bennett`) |
| 4 | The Quintero Lab joins, mostly with data from the PI's previous institution: new here, but years old |
| 5 | The Lindqvist Lab compresses its uncompressed FASTQ files (`wgs/2024_germline/fastq_uncompressed` becomes `fastq`) |
| 6 | The Fujimoto Lab writes its atlas; the Dasgupta Lab deletes its 2016 cohort, as the cleanup plan it made after scan 5 says, and renames the manuscript folder it planned to delete (`manuscripts/2021_draft` becomes `2021_published`) |

Meanwhile nobody touches the Kowalski Lab's `raw/2024_tmt_plex`, which ages past two years: the lab's cold share
climbs from 64% to 100% at the same size. *History* shows all of it, the *Change map* where each change is, and
*What changed* lists them.

## Prices

Storage has prices, so the six months make six months of charges. *Fast* costs $25 per TB a month, and $22 from
the 16th of last month; every lab has a terabyte of it free and departments none, and the Lindqvist Lab has its own
5 TB from the 10th of last month (a grant's allocation). *Secure* costs $40 per TiB, with nothing free.

## Capacity

*Fast* holds 125 TiB, so at its trend it fills within the year, and an expansion to 250 TiB is scheduled for eleven
months after the seed, too late. *Secure* holds a tebibyte. The demo's scans record nothing about the storage (its
files are sparse, on a laptop's disk), so the capacities are set, and use is measured by the files' size.


## The walk-through (about 20 minutes)

1. **Home page.** Choose *Continue as Hanna Lindqvist* (a PI). *My data* shows one folder: 19 TiB, most of
   it unchanged for years.
2. **Age map.** Open it. `wgs/2019_tumor_normal` is a large blue tile: 17 TiB of alignments last changed
   about seven years ago. Hover for details; switch *Color by* to *Time since last access* and
   *Size by* to *Size of sequencing and genomics*.
3. **File types.** The callout shows terabytes of uncompressed SAM files; click `.sam` to list them. (The lab
   compressed its uncompressed FASTQ files before the fifth scan.)
4. **Switch identity** (top right) to *Aiko Fujimoto*. Drill into `projects`: `2025_immune_aging` is red, data
   written this year, beside two blue projects. Then `2022_tumor_atlas`, then `raw`: an active project hides
   `2014_legacy_reruns`, the darkest blue tile, nearly twelve years old.
   **Cold data** at two years lists `raw` as one cold unit; at ten years it lists `2014_legacy_reruns` alone.
   **History** for `fujimoto_a`: the lab grew from 19 to 25 TiB in six scans, and new data on top of the columns
   brought its cold share down from 77% to 60%. Hover a column for that scan; the three small charts follow.
   The dashed line is the forecast: at its trend, about 34 TiB in a year (*In a year*), within the shaded range.
   The `bennett_k` lab has none: the project it handed to `haddad_n` is a step, and the trend starts again after
   it, so it needs six scans after the step.
5. **Switch to Jordan Okafor**, a delegate for two labs. *My data* shows both labs' folders; the age map
   puts both side by side. Then **switch to Valeria Quintero**, whose lab joined three scans ago: one folder, two
   thirds of it unchanged for more than two years, because most of it came from the PI's previous institution.
   Its *History* starts at the fourth scan.
6. **Storage charges.** Switch back to *Hanna Lindqvist*. *My data* shows this month's storage charge, an
   estimate to the month's end. Open *Storage charges* and choose last month: the price of *Fast* fell on the
   16th, and the lab's own 5 TB free tier started on the 10th, so the month is worked out in three parts, each its
   average size less what was free, at its price, for its days. *Past months* lists the months since the first
   scan, and *The next 12 months* what the lab will pay if it keeps growing as it has, with a range.
7. **Cleanup plans.** Switch to *Rohan Dasgupta*. *Cleanup plans* shows the plan his lab made after the fifth
   scan. The sixth shows `2016_cohort_A` done, under *Done so far* with what it frees and saves a month;
   `2021_draft` no longer at its path, but a folder with its inode is at `2021_published`: it was renamed, so nothing
   was freed (*It moved there* makes the plan follow it); and `2018_cohort_B` still to do, with what it would save.
   *Suggestions* lists folders worth adding. Open the age map at a folder of the lab: its panel's *Cleanup plan* box
   adds it to the plan.
8. **Tags.** Switch to *Aiko Fujimoto*. On the age map of `fujimoto_a/projects`, the tiles with a dot are tagged:
   `2022_tumor_atlas` has a grant, a project, a source, a stage, and the lab's own *Atlas paper*, which its panel
   lists, and its subfolders inherit; `2018_pilot` is kept until 2030, so its *Cleanup plan* box says it cannot go
   in a plan. *Storage charges* splits the lab's month by grant: the tumor atlas's grant, the immune aging study's,
   and what is not tagged. *Collections* with *Source* is *sequencing* and *Data stage* is *raw* finds the Genomics
   Shared Resource's runs and the Lindqvist Lab's tumor-normal study (its source inherited from `wgs`).
9. **Saved views.** Still as Aiko Fujimoto, *My data* lists *Tumor atlas, oldest data*, a view she saved and shared
   with her lab: it opens the age map of the tumor atlas colored by its oldest files. *Save view* on any report keeps
   another.
10. **Switch to Priya Natarajan** (storage administrator). *All data* shows both filesystems, the largest
   folders, the biggest changes since the previous scan (a batch of samples and an atlas in `fujimoto_a`,
   sequencing runs in `gsr_illumina`), and
   three unmapped folders. Open the age map of *Fast*: `scratch_legacy` is a large, very old, unmapped tile.
   Open *History* for `kowalski_d`: the same size every scan, while its cold share climbs to 100%. Then
   `lindqvist_h/wgs/2024_germline`: a terabyte smaller at the fifth scan, when the lab compressed its FASTQ files.
   Open the *Change map* of *Fast* and compare with the first scan, five months ago: `fujimoto_a` and `haddad_n` grew,
   `bennett_k` shrank by 81%. In `bennett_k`, the removed `imaging/2024_lightsheet` keeps the room it had; in
   `haddad_n`, `datasets/2024_lightsheet_bennett` is new, and its tooltip says it is over a year old: it moved.
   *What changed* for *Fast* since the first scan lists the Fujimoto project and the sequencing runs where it grew, the
   moved project and the compressed FASTQ among new and removed folders, and the largest new files (Cell Ranger
   BAMs); each list downloads as CSV.
11. **Folders and labs.** Tick *Unmapped only*, then map `shared_reference` to the Genomics Shared Resource.
   Switch to *Casey Morgan*: the folder now appears in their data.
12. **Costs and Chargeback.** Back as Priya, *Costs* shows each filesystem's prices and free tiers, with the
   Lindqvist Lab's exception. *Chargeback* for last month lists every lab's and department's charge, the most
   first (the Genomics Shared Resource, a department, has nothing free), the totals by filesystem, and the
   storage no one owns, most of it `scratch_legacy`; *Download CSV* is the statement for finance. This month,
   `shared_reference` is charged to the Genomics Shared Resource from the moment it was mapped. *The next 12
   months* adds up what every lab will pay at its trend.
   *Capacity*: *Fast* is 89% used and fills in about nine months at its trend, a month or two before the expansion
   scheduled for eleven months on, so *All data* flags it under *Needs attention*. Its *History* draws the capacity
   above the columns.
13. **Storage health.** An unreadable folder, a very large directory (12,000 PNG tiles), problem file names
    (a newline, a leading dash, a Windows-reserved character, a name that is not NFC), and files owned by
    accounts that no longer exist.
14. **Find files.** Search `*.bam`, older than five years, largest first; download the CSV. On all of *Fast*,
    *Run in the background* runs the search past a page's time limit: *Searches* already has one, every BAM on
    *Fast*, done by the seed. And the age map of `lindqvist_h/wgs/2019_tumor_normal/from_core` shows four hard
    links to the core's FASTQ files, counted once, at the core.
15. **Scans.** Six snapshots of each filesystem, a month apart, each with its full index (`PINAX_KEEP_SNAPSHOTS`
    keeps six), and whether automatic ingest is watching for new scans. Click a date to open that scan: the
    header says it is an older one, every report and link stays on it, and *Back to the latest* returns. Choose
    another scan beside the date on any report.
16. **People and access.** *Add person*: a username, a name, an email, and a lab; the panel shows the folders
    they will see. Grant or remove the storage administrator role. *Deactivate* them, and they cannot sign in (their
    access is kept, unused); *Reactivate* restores it. Each change is in the person's record and the audit log.
    On the *Labs and departments* tab, each lab's folders and members; *Add lab or department* adds one (a slug, a
    name, and whether it is a lab or a department, which decides its free tier), and its page changes its details
    or deactivates it: its members stop seeing its folders, which stay its own (and charged to it) until they are
    moved.
17. **Import from CSV.** In a terminal, `uv run pinax import examples/import` lists the 14 changes the example
    files would make: two departments, a lab, three people, their memberships, and owners for the three unmapped
    folders. Add `--apply`, and *Folders and labs* shows no unmapped folders; every change is in the audit log.

18. **Logs and metrics.** The terminal running `pinax serve` has a line for each request, with who made it, from
    where, how long it took, and its request ID; each change made above is there too, as a `pinax.audit` line. Stop
    the server, and start it again with metrics on:

    ```bash
    PINAX_METRICS_TOKEN=demo-metrics-token-0123456789 uv run pinax serve
    ```

    ```bash
    curl -s -H "Authorization: Bearer demo-metrics-token-0123456789" http://127.0.0.1:8001/metrics | grep '^pinax_filesystem'
    ```

    Each filesystem's size, files, cold data, capacity, use, and growth, and the day *Fast* could fill at the fast
    end of its trend. `uv run pinax db status` says the catalog is up to date.

## Afterwards

Changes made during the walk-through (a folder mapped, a person or lab added, an import applied) stay until you
seed again:

```bash
uv run pinax seed
```
