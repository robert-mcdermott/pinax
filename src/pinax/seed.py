# SPDX-License-Identifier: Apache-2.0
"""Demo data: two research filesystems, labs, people, and two real fs-inventory scans of each.

The demo trees hold about 40,000 *sparse* files: each has a realistic logical size (a 120 GB BAM,
a 2 GB microscopy session) but uses almost no disk, and realistic modification and access times
going back fifteen years. They are scanned with the real fs-inventory binary, so everything Pinax
shows comes from genuine scans. One step is demo-only: every file on a laptop belongs to you, so
after each scan the seed rewrites the owner and group columns to fictional lab accounts (see
`_assign_demo_owners`). A second scan taken after some simulated activity gives the
"since the previous scan" comparisons something to show.

All names are fictional. Lab folders follow the lastname_f convention used on research storage, and
the labs and people match Tundra's demo.
"""

import json
import os
import random
import shutil
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .config import Settings, get_settings
from .db import Base, get_engine, reset_engine, session_scope
from .models import AuditEvent, Filesystem, Lab, LabFolder, LabMembership, User
from .snapshots import indexes, ingest_scan

KB, MB, GB, TB = 1000, 1000**2, 1000**3, 1000**4
YEAR = 365.25 * 86400

FILESYSTEMS = [
    # slug, name, display root, description
    ("fast", "Fast", "/fh/fast", "Primary research storage (PowerScale), mounted on every cluster node"),
    ("secure", "Secure", "/fh/secure", "Restricted storage for identifiable and regulated data"),
]

LABS = [
    # slug, name, kind, division, research area, [(filesystem, top-level folder)]
    ("alvarez_m", "Alvarez Lab", "lab", "Basic Sciences", "Cryo-EM structural biology", [("fast", "alvarez_m")]),
    ("bennett_k", "Bennett Lab", "lab", "Human Biology", "Organoid imaging", [("fast", "bennett_k")]),
    ("chen_l", "Chen Lab", "lab", "Public Health Sciences", "Cancer epidemiology cohorts",
     [("fast", "chen_l"), ("secure", "chen_l")]),
    ("dasgupta_r", "Dasgupta Lab", "lab", "Vaccine and Infectious Disease", "HIV sequencing", [("fast", "dasgupta_r")]),
    ("eriksen_t", "Eriksen Lab", "lab", "Clinical Research", "Early-phase trial analytics",
     [("fast", "eriksen_t"), ("secure", "eriksen_t")]),
    ("fujimoto_a", "Fujimoto Lab", "lab", "Basic Sciences", "Single-cell genomics", [("fast", "fujimoto_a")]),
    ("garcia_s", "Garcia Lab", "lab", "Human Biology", "CRISPR functional screens", [("fast", "garcia_s")]),
    ("haddad_n", "Haddad Lab", "lab", "Translational Data Science", "Pathology image ML", [("fast", "haddad_n")]),
    ("iverson_p", "Iverson Lab", "lab", "Vaccine and Infectious Disease", "Flow cytometry immunology",
     [("fast", "iverson_p")]),
    ("johansson_e", "Johansson Lab", "lab", "Public Health Sciences", "Microbiome studies", [("fast", "johansson_e")]),
    ("kowalski_d", "Kowalski Lab", "lab", "Basic Sciences", "Mass spectrometry proteomics", [("fast", "kowalski_d")]),
    ("lindqvist_h", "Lindqvist Lab", "lab", "Clinical Research", "Whole-genome sequencing", [("fast", "lindqvist_h")]),
    ("genomics_sr", "Genomics Shared Resource", "department", "Shared Resources", "Sequencing core services",
     [("fast", "gsr_illumina"), ("fast", "gsr_nanopore")]),
]

# Top-level folders that exist but belong to no lab: only administrators see them.
UNMAPPED = [("fast", "shared_reference"), ("fast", "scratch_legacy"), ("secure", "irb_archive")]

# username, display name, title, admin, [(lab slug, role)]
PEOPLE = [
    ("malvarez", "Marisol Alvarez", "Principal Investigator", False, [("alvarez_m", "pi")]),
    ("kbennett", "Kendra Bennett", "Principal Investigator", False, [("bennett_k", "pi")]),
    ("lchen", "Li Chen", "Principal Investigator", False, [("chen_l", "pi")]),
    ("rdasgupta", "Rohan Dasgupta", "Principal Investigator", False, [("dasgupta_r", "pi")]),
    ("teriksen", "Tove Eriksen", "Principal Investigator", False, [("eriksen_t", "pi")]),
    ("afujimoto", "Aiko Fujimoto", "Principal Investigator", False, [("fujimoto_a", "pi")]),
    ("sgarcia", "Sofia Garcia", "Principal Investigator", False, [("garcia_s", "pi")]),
    ("nhaddad", "Nadia Haddad", "Principal Investigator", False, [("haddad_n", "pi")]),
    ("piverson", "Paul Iverson", "Principal Investigator", False, [("iverson_p", "pi")]),
    ("ejohansson", "Elin Johansson", "Principal Investigator", False, [("johansson_e", "pi")]),
    ("dkowalski", "Dariusz Kowalski", "Principal Investigator", False, [("kowalski_d", "pi")]),
    ("hlindqvist", "Hanna Lindqvist", "Principal Investigator", False, [("lindqvist_h", "pi")]),
    ("jokafor", "Jordan Okafor", "Lab Manager", False, [("fujimoto_a", "delegate"), ("garcia_s", "delegate")]),
    ("mreyes", "Mateo Reyes", "Research Data Manager", False,
     [("chen_l", "delegate"), ("johansson_e", "delegate"), ("eriksen_t", "delegate")]),
    ("awong", "Avery Wong", "Staff Scientist", False, [("lindqvist_h", "delegate"), ("genomics_sr", "delegate")]),
    ("cmorgan", "Casey Morgan", "Director, Genomics Shared Resource", False, [("genomics_sr", "pi")]),
    ("rpatel", "Riya Patel", "Postdoctoral Research Fellow", False, []),  # no lab access yet
    ("pnatarajan", "Priya Natarajan", "Storage Engineering Lead", True, []),
    ("srivera", "Sam Rivera", "Research Data Services, Scientific Computing", True, []),
]

# Extra file-system accounts (lab members who are not Pinax users), and accounts that no longer exist.
MEMBER_NAMES = ["tnguyen", "bokoro", "lfischer", "dmendes", "yzhou", "kpatel", "jkim", "mrossi", "asato", "ebrown",
                "hsingh", "olarsen", "cdiaz", "nmurphy", "rkhan", "vpetrov", "glopez", "wtanaka", "sali", "fmoreau",
                "pwalsh", "imartin", "zahmed", "qliu", "uokonkwo", "xchen", "bjensen", "aharris", "tcosta", "lnovak",
                "mweber", "sgupta", "jsilva", "kito", "ebauer", "ryoung", "dkaur", "fhansen", "nbrooks", "vramos"]


@dataclass
class FileSpec:
    path: str
    size: int
    mtime: float
    atime: float


class TreeBuilder:
    """Describes demo files; `write` creates them as sparse files with the given times."""

    def __init__(self, rng: random.Random, now: float, scale: float):
        self.rng, self.now, self.scale = rng, now, scale
        self.files: list[FileSpec] = []
        self.links: list[tuple[str, str]] = []

    def n(self, count: int) -> int:
        return max(1, round(count * self.scale)) if count > 1 else count

    def add(self, pattern: str, count: int, size: tuple[float, float], age: tuple[float, float], *,
            hot: bool = False, exact: bool = False) -> None:
        """`count` files from `pattern` ({i} is the index), sized uniformly in `size` (bytes), modified
        uniformly between `age` years ago (newest, oldest). Hot files were read in the last two weeks."""
        r = self.rng
        for i in range(count if exact else self.n(count)):
            mtime = self.now - r.uniform(age[0], age[1]) * YEAR
            atime = (self.now - r.uniform(0, 14) * 86400) if hot else min(self.now, mtime + r.uniform(0, 45) * 86400)
            self.files.append(FileSpec(pattern.format(i=i, i1=i + 1), int(r.uniform(*size)), mtime, atime))

    def link(self, path: str, target: str) -> None:
        self.links.append((path, target))

    def write(self, root: Path) -> None:
        for f in self.files:
            p = root / f.path
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as fh:
                fh.truncate(f.size)
            os.utime(p, (f.atime, f.mtime))
        for path, target in self.links:
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.is_symlink():
                os.symlink(target, p)


def lab_files(t: TreeBuilder, top: str) -> None:
    a = t.add
    common = [
        (f"{top}/admin/README.md", 1, (2 * KB, 6 * KB), (0.5, 4)),
        (f"{top}/admin/protocols/protocol_v{{i1}}.docx", 6, (40 * KB, 900 * KB), (1, 8)),
        (f"{top}/manuscripts/2021_draft/figures/fig{{i1}}.pdf", 6, (200 * KB, 8 * MB), (4.5, 5)),
        (f"{top}/manuscripts/2021_draft/manuscript_v7.docx", 1, (1 * MB, 3 * MB), (4.5, 4.6)),
    ]
    for c in common:
        a(*c)
    match top:
        case "alvarez_m":
            a(f"{top}/projects/2017_ribosome_complex/raw_movies/movie_{{i:05d}}.tiff", 600, (0.8 * GB, 1.6 * GB), (7.6, 8.8))
            a(f"{top}/projects/2017_ribosome_complex/motion_corrected/mc_{{i:05d}}.mrc", 600, (0.2 * GB, 0.4 * GB), (7.5, 7.7))
            a(f"{top}/projects/2017_ribosome_complex/relion/run_it025_data.star", 1, (300 * MB, 320 * MB), (7.4, 7.4))
            a(f"{top}/projects/2017_ribosome_complex/relion/logs/run_{{i}}.log", 12, (50 * KB, 400 * KB), (7.4, 7.6))
            a(f"{top}/projects/2020_membrane_transporter/raw_movies/movie_{{i:05d}}.eer", 400, (1 * GB, 2 * GB), (5.2, 6.1))
            a(f"{top}/projects/2020_membrane_transporter/maps/final_map_{{i}}.mrc", 4, (1.5 * GB, 2.5 * GB), (4.9, 5))
            a(f"{top}/projects/2025_spliceosome/raw_movies/movie_{{i:05d}}.eer", 300, (1 * GB, 2 * GB), (0.05, 0.6))
            a(f"{top}/projects/2025_spliceosome/cryosparc/J{{i}}/particles.cs", 20, (1 * GB, 5 * GB), (0.01, 0.4))
            a(f"{top}/software/scripts/{{i:02d}}_pipeline.py", 30, (2 * KB, 40 * KB), (0.2, 3))
        case "bennett_k":
            a(f"{top}/imaging/2019_colon_organoids/plate_{{i:03d}}_well.tif", 288, (0.4 * GB, 1.2 * GB), (6.6, 7.4))
            a(f"{top}/imaging/2021 Confocal (old scope)/session_{{i:02d}}.nd2", 40, (5 * GB, 20 * GB), (4.6, 5.2))
            a(f"{top}/imaging/2024_lightsheet/run_{{i:03d}}.czi", 60, (20 * GB, 80 * GB), (0.1, 1.4))
            a(f"{top}/analysis/segmentation/masks/mask_{{i:04d}}.png", 800, (100 * KB, 1 * MB), (2, 3))
            a(f"{top}/analysis/segmentation/cellprofiler_output.csv", 1, (200 * MB, 210 * MB), (2, 2))
            # A file whose name is not in normalization form C, as macOS clients often write.
            a(f"{top}/imaging/2021 Confocal (old scope)/" + unicodedata.normalize("NFD", "résumé_of_settings.txt"),
              1, (3 * KB, 4 * KB), (4.6, 4.6))
        case "chen_l":
            for wave, age in (("2017_wave3", (8, 9)), ("2019_wave4", (6, 7)), ("2023_wave6", (0.3, 2.5))):
                a(f"{top}/cohort_studies/{wave}/survey_responses_{{i:02d}}.csv", 18, (20 * MB, 80 * MB), age)
                a(f"{top}/cohort_studies/{wave}/codebook.pdf", 1, (1 * MB, 2 * MB), age)
                a(f"{top}/cohort_studies/{wave}/derived/analysis_dataset.parquet", 1, (2 * GB, 6 * GB), age)
            a(f"{top}/analysis/R/model_{{i:02d}}.R", 40, (4 * KB, 30 * KB), (0.1, 5))
            a(f"{top}/analysis/output/tables/table_{{i:03d}}.tsv", 200, (100 * KB, 5 * MB), (0.2, 6))
            a(f"{top}/tmp/bootstrap_{{i:04d}}.rds", 150, (50 * MB, 400 * MB), (0.01, 0.3))
        case "dasgupta_r":
            for cohort, age, n in (("2016_cohort_A", (9.2, 10.1), 240), ("2018_cohort_B", (7.6, 8.2), 200),
                                   ("2024_cohort_C", (0.2, 1.8), 180)):
                a(f"{top}/sequencing/{cohort}/fastq/sample_{{i:03d}}_R1.fastq.gz", n // 2, (2 * GB, 6 * GB), age)
                a(f"{top}/sequencing/{cohort}/fastq/sample_{{i:03d}}_R2.fastq.gz", n // 2, (2 * GB, 6 * GB), age)
                a(f"{top}/sequencing/{cohort}/consensus/sample_{{i:03d}}.fasta", n // 2, (10 * KB, 40 * KB), age)
            a(f"{top}/phylogenetics/trees/tree_{{i:02d}}.nwk", 30, (20 * KB, 200 * KB), (1, 6))
            t.link(f"{top}/sequencing/latest", "2024_cohort_C")
        case "eriksen_t":
            a(f"{top}/analytics/dashboards/trial_{{i:02d}}.ipynb", 20, (100 * KB, 3 * MB), (0.1, 2))
            a(f"{top}/analytics/exports/visit_summary_{{i:03d}}.csv", 120, (1 * MB, 20 * MB), (0.1, 4))
        case "fujimoto_a":
            p = f"{top}/projects/2022_tumor_atlas"
            a(f"{p}/raw/fastq/S{{i:03d}}_L001_R1_001.fastq.gz", 200, (10 * GB, 30 * GB), (3.4, 4.2))
            a(f"{p}/raw/fastq/S{{i:03d}}_L001_R2_001.fastq.gz", 200, (10 * GB, 30 * GB), (3.4, 4.2))
            a(f"{p}/cellranger/sample_{{i:02d}}/outs/possorted_genome_bam.bam", 60, (30 * GB, 90 * GB), (3.2, 3.6))
            a(f"{p}/cellranger/sample_{{i:02d}}/outs/filtered_feature_bc_matrix.h5", 60, (200 * MB, 900 * MB), (3.2, 3.6))
            a(f"{p}/analysis/atlas_integrated.h5ad", 1, (60 * GB, 70 * GB), (0.1, 0.2))
            a(f"{p}/analysis/notebooks/{{i:02d}}_clustering.ipynb", 25, (1 * MB, 30 * MB), (0.05, 1.5))
            # Old reruns tucked inside an otherwise active project: the drill-down find.
            a(f"{p}/raw/2014_legacy_reruns/run_{{i:03d}}.bam", 40, (40 * GB, 90 * GB), (11, 11.8))
            a(f"{top}/projects/2018_pilot/fastq/pilot_{{i:03d}}.fastq.gz", 120, (5 * GB, 15 * GB), (7.8, 8.4))
            a(f"{top}/projects/2018_pilot/cellranger_v2/sample_{{i:02d}}.bam", 24, (20 * GB, 50 * GB), (7.6, 7.9))
            a(f"{top}/projects/2025_immune_aging/raw/fastq/S{{i:03d}}_R1.fastq.gz", 120, (10 * GB, 30 * GB), (0.02, 0.5))
            a(f"{top}/projects/2025_immune_aging/raw/fastq/S{{i:03d}}_R2.fastq.gz", 120, (10 * GB, 30 * GB), (0.02, 0.5))
            a(f"{top}/containers/cellranger_{{i}}.sif", 4, (2 * GB, 4 * GB), (0.5, 3))
        case "garcia_s":
            a(f"{top}/screens/2019_genome_wide/fastq/lib{{i:02d}}.fastq.gz", 96, (2 * GB, 8 * GB), (6.5, 7))
            a(f"{top}/screens/2019_genome_wide/counts/lib{{i:02d}}.counts.tsv", 96, (5 * MB, 20 * MB), (6.4, 6.5))
            a(f"{top}/screens/2023_kinome/fastq/lib{{i:02d}}.fastq.gz", 48, (2 * GB, 8 * GB), (2.6, 3.0))
            a(f"{top}/screens/2026_base_editing/fastq/lib{{i:02d}}.fastq.gz", 24, (2 * GB, 8 * GB), (0.01, 0.2))
            a(f"{top}/analysis/mageck/run_{{i:02d}}.gene_summary.txt", 40, (2 * MB, 8 * MB), (0.1, 6))
        case "haddad_n":
            # Many small files in one directory: a small-file hotspot for storage health.
            a(f"{top}/datasets/2020_tcga_tiles/tiles/tile_{{i:05d}}.png", 12000, (50 * KB, 300 * KB), (5.4, 5.9))
            a(f"{top}/datasets/2020_tcga_tiles/slides/TCGA-{{i:04d}}.svs", 300, (1 * GB, 4 * GB), (5.9, 6.3))
            a(f"{top}/models/checkpoints/epoch_{{i:03d}}.pt", 120, (2 * GB, 8 * GB), (0.8, 3))
            a(f"{top}/models/exported/model_v{{i1}}.onnx", 6, (300 * MB, 1 * GB), (0.1, 1))
            a(f"{top}/containers/pytorch_{{i}}.sif", 6, (3 * GB, 8 * GB), (0.5, 3))
            a(f"{top}/logs/slurm-{{i:06d}}.out", 600, (5 * KB, 2 * MB), (0.01, 2))
        case "iverson_p":
            for year in range(2018, 2027):
                a(f"{top}/experiments/{year}_panels/run_{{i:04d}}.fcs", 220, (5 * MB, 50 * MB),
                  (max(0.02, 2026.8 - year - 1), 2026.8 - year))
        case "johansson_e":
            a(f"{top}/16S/2015_gut_cohort/reads/sample_{{i:04d}}.fastq.gz", 500, (200 * MB, 1 * GB), (10.4, 11))
            a(f"{top}/shotgun/2021_infant_microbiome/reads/sample_{{i:03d}}.fastq.gz", 120, (5 * GB, 15 * GB), (4.6, 5.3))
            a(f"{top}/shotgun/2021_infant_microbiome/kraken2/sample_{{i:03d}}.kreport", 120, (1 * MB, 5 * MB), (4.4, 4.6))
            a(f"{top}/databases/kraken2_standard/hash.k2d", 1, (400 * GB, 410 * GB), (3, 3.1), hot=True)
            a(f"{top}/databases/kraken2_standard/taxo.k2d", 1, (4 * GB, 5 * GB), (3, 3.1), hot=True)
        case "kowalski_d":
            a(f"{top}/raw/2016_phosphoproteome/run_{{i:03d}}.raw", 300, (1 * GB, 4 * GB), (9.5, 10.2))
            a(f"{top}/raw/2023_dia_cohort/run_{{i:03d}}.raw", 200, (2 * GB, 5 * GB), (1.6, 2.8))
            a(f"{top}/analysis/maxquant/txt/{{i:02d}}_proteinGroups.txt", 40, (10 * MB, 90 * MB), (1.5, 9))
            a(f"{top}/instrument_exports/locked/export_{{i:02d}}.mzML", 10, (500 * MB, 2 * GB), (3, 4))
        case "lindqvist_h":
            p = f"{top}/wgs/2019_tumor_normal"
            a(f"{p}/bam/sample_{{i:03d}}.tumor.bam", 60, (100 * GB, 180 * GB), (6.8, 7.3))
            a(f"{p}/bam/sample_{{i:03d}}.normal.bam", 60, (60 * GB, 110 * GB), (6.8, 7.3))
            a(f"{p}/bam/sample_{{i:03d}}.tumor.bam.bai", 60, (8 * MB, 12 * MB), (6.8, 7.3))
            a(f"{p}/vcf/sample_{{i:03d}}.somatic.vcf.gz", 60, (100 * MB, 600 * MB), (6.5, 6.7))
            # Uncompressed alignments and reads: candidates for CRAM or gzip.
            a(f"{p}/sam_intermediate/sample_{{i:03d}}.sam", 12, (300 * GB, 500 * GB), (6.9, 7.1))
            a(f"{top}/wgs/2024_germline/cram/sample_{{i:03d}}.cram", 80, (15 * GB, 30 * GB), (0.3, 1.9))
            a(f"{top}/wgs/2024_germline/fastq_uncompressed/sample_{{i:03d}}_R1.fastq", 20, (60 * GB, 120 * GB), (1.2, 1.5))
        case "gsr_illumina":
            for run in range(140):
                years = 0.05 + (139 - run) * 0.075  # a run every few weeks for ten years
                rid = f"runs/{2026 - int(years)}_run_{run:04d}_A0{run % 4}"
                a(f"{top}/{rid}/fastq/Sample_{{i:02d}}_R1.fastq.gz", 24, (2 * GB, 9 * GB), (years, years + 0.01))
                a(f"{top}/{rid}/fastq/Sample_{{i:02d}}_R2.fastq.gz", 24, (2 * GB, 9 * GB), (years, years + 0.01))
                a(f"{top}/{rid}/InterOp/metrics_{{i}}.bin", 3, (1 * MB, 30 * MB), (years, years + 0.01))
            a(f"{top}/billing/{{i:04d}}_usage_report.xlsx", 10, (100 * KB, 900 * KB), (0.1, 9))
        case "gsr_nanopore":
            for run in range(40):
                years = 0.05 + (39 - run) * 0.12
                fmt = "pod5" if years < 2.5 else "fast5"
                a(f"{top}/runs/run_{run:03d}/{fmt}/batch_{{i:03d}}.{fmt}", 50, (1 * GB, 4 * GB), (years, years + 0.02))
        case "shared_reference":
            a(f"{top}/genomes/hg38/hg38.fa", 1, (3.1 * GB, 3.2 * GB), (5.5, 5.5), hot=True)
            a(f"{top}/genomes/hg38/hg38.fa.fai", 1, (8 * KB, 9 * KB), (5.5, 5.5), hot=True)
            a(f"{top}/genomes/hg38/bwa/hg38.fa.{{i}}", 5, (1 * GB, 6 * GB), (5.4, 5.5), hot=True)
            a(f"{top}/genomes/hg38/star_2.7/SA_{{i}}", 8, (2 * GB, 12 * GB), (4, 4.1), hot=True)
            a(f"{top}/genomes/mm10/mm10.fa", 1, (2.7 * GB, 2.8 * GB), (8, 8))
            a(f"{top}/annotations/gencode.v{{i}}.annotation.gtf.gz", 12, (40 * MB, 60 * MB), (0.5, 7))
        case "scratch_legacy":
            a(f"{top}/old_user_backups/user_{{i:03d}}.tar.gz", 120, (20 * GB, 200 * GB), (9, 14))
            a(f"{top}/2013_cluster_migration/rsync_logs/run_{{i:03d}}.log", 200, (1 * MB, 50 * MB), (12, 13))
            a(f"{top}/2013_cluster_migration/images/node_{{i:02d}}.img", 24, (50 * GB, 120 * GB), (12.5, 13))
        case "secure/chen_l":
            a(f"{top}/cohort_linked/2018_registry_link/linked_records_{{i:02d}}.sas7bdat", 30, (2 * GB, 8 * GB), (7, 8))
            a(f"{top}/cohort_linked/2024_registry_link/linked_records_{{i:02d}}.sas7bdat", 30, (2 * GB, 8 * GB), (0.2, 1.5))
        case "secure/eriksen_t":
            for trial, age in (("FH-2015-031", (9, 10.5)), ("FH-2018-112", (6.5, 8)), ("FH-2024-007", (0.05, 1.8))):
                a(f"{top}/trials/{trial}/edc_exports/visit_{{i:03d}}.csv", 120, (2 * MB, 40 * MB), age)
                a(f"{top}/trials/{trial}/sas/derived_{{i:02d}}.sas7bdat", 20, (100 * MB, 2 * GB), age)
                a(f"{top}/trials/{trial}/reports/csr_{{i:02d}}.pdf", 6, (2 * MB, 30 * MB), age)
        case "secure/irb_archive":
            a(f"{top}/protocols/{{i:04d}}_consent_forms.pdf", 400, (1 * MB, 20 * MB), (6, 15))


def name_oddities(t: TreeBuilder) -> None:
    """A few names that cause trouble in migrations and scripts, for the storage health page."""
    for name in ("-output.txt", "draft notes ", "results:final.csv", "CON.txt", "trailing.dot.", "line\nbreak.txt"):
        t.add(f"eriksen_t/analytics/misc/{name}", 1, (1 * KB, 5 * KB), (1, 2), exact=True)


def build_trees(settings: Settings, rng: random.Random, now: float, scale: float) -> dict[str, dict[str, TreeBuilder]]:
    """Per filesystem: the files present at the first scan ("base"), those added before the second
    ("later"), and those deleted before it ("removed", relative paths)."""
    out = {}
    for fs, *_ in FILESYSTEMS:
        out[fs] = {"base": TreeBuilder(rng, now, scale), "later": TreeBuilder(rng, now, scale)}
    tops = [(fs, top) for _, _, _, _, _, folders in LABS for fs, top in folders] + UNMAPPED
    for fs, top in tops:
        key = top if fs == "fast" else f"{fs}/{top}"
        if fs == "fast":
            lab_files(out[fs]["base"], top)
        else:
            _secure(out[fs]["base"], key, top)
    name_oddities(out["fast"]["base"])
    later = out["fast"]["later"]
    for run in range(140, 144):  # four new sequencing runs since the first scan
        rid = f"runs/2026_run_{run:04d}_A0{run % 4}"
        later.add(f"gsr_illumina/{rid}/fastq/Sample_{{i:02d}}_R1.fastq.gz", 24, (2 * GB, 9 * GB), (0.001, 0.004))
        later.add(f"gsr_illumina/{rid}/fastq/Sample_{{i:02d}}_R2.fastq.gz", 24, (2 * GB, 9 * GB), (0.001, 0.004))
    later.add("fujimoto_a/projects/2025_immune_aging/cellranger/sample_{i:02d}/outs/possorted_genome_bam.bam", 30,
              (30 * GB, 90 * GB), (0.001, 0.005))
    later.add("alvarez_m/projects/2025_spliceosome/raw_movies/movie_b{i:05d}.eer", 150, (1 * GB, 2 * GB), (0.001, 0.01))
    return out


def _secure(t: TreeBuilder, key: str, top: str) -> None:
    """Secure-filesystem layouts are keyed "secure/<folder>"; their files live under <folder>."""
    sub = TreeBuilder(t.rng, t.now, t.scale)
    lab_files(sub, key)
    for f in sub.files:
        f.path = f.path.replace(key + "/", top + "/", 1)
        t.files.append(f)


REMOVED = [  # cleaned up between the two scans (relative to the filesystem root)
    ("fast", "chen_l/tmp"),
    ("fast", "haddad_n/models/checkpoints"),
]


# ---------- Owners (demo only) ----------

def demo_accounts(rng: random.Random) -> tuple[dict[str, list[tuple[int, str | None]]], dict[str, int]]:
    """Per top-level folder: the accounts that own its files. Uses the PI and delegates from PEOPLE,
    two or three other lab members, and sometimes an account that has since been deleted."""
    by_lab: dict[str, list[str]] = {}
    for username, _, _, _, labs in PEOPLE:
        for slug, _role in labs:
            by_lab.setdefault(slug, []).append(username)
    uid = 20000
    names: dict[str, int] = {}

    def account(name: str | None) -> tuple[int, str | None]:
        nonlocal uid
        if name in names:
            return names[name], name
        uid += 1
        if name is not None:
            names[name] = uid
        return uid, name

    pool = list(MEMBER_NAMES)
    rng.shuffle(pool)
    owners: dict[str, list[tuple[int, str | None]]] = {}
    for slug, _, _, _, _, folders in LABS:
        accts = [account(u) for u in by_lab.get(slug, [])]
        accts += [account(pool.pop()) for _ in range(rng.choice((2, 3)))]
        if rng.random() < 0.6:
            accts.append(account(None))  # a departed member whose account was removed
        for fs, top in folders:
            owners[f"{fs}/{top}"] = accts
    svc = account("svc_research")
    for fs, top in UNMAPPED:
        owners[f"{fs}/{top}"] = [svc, account(None)] if top == "scratch_legacy" else [svc]
    groups = {slug: 30000 + i for i, (slug, *_rest) in enumerate(LABS)}
    return owners, groups


def _assign_demo_owners(scan_dir: Path, fs: str, root: str, owners: dict, groups: dict) -> None:
    """Rewrite uid and gid in a demo scan so files belong to fictional lab accounts, and write
    matching users and groups tables. Real scans are never modified."""
    prefix = root.rstrip("/") + "/"
    con = duckdb.connect()
    rows, lab_of = [], {}
    for slug, _, _, _, _, folders in LABS:
        for f, top in folders:
            lab_of[f"{f}/{top}"] = slug
    for key, accts in owners.items():
        f, top = key.split("/", 1)
        if f != fs:
            continue
        gid = groups.get(lab_of.get(key, ""), 29999)
        for i, (uid, _name) in enumerate(accts):
            rows.append((top, i, uid, gid, len(accts)))
    con.execute("CREATE TABLE m (top VARCHAR, i INTEGER, uid BIGINT, gid BIGINT, n INTEGER)")
    con.executemany("INSERT INTO m VALUES (?, ?, ?, ?, ?)", rows)
    manifest = json.loads((scan_dir / "manifest.json").read_text())
    L = len(prefix) + 1
    for part in manifest["parts"]["entries"]:
        src = scan_dir / part["file"]
        tmp = src.with_suffix(".tmp")
        con.execute(f"""
            COPY (
              WITH e AS (SELECT *, split_part(substr(path, {L}), '/', 1) AS _top,
                                split_part(substr(path, {L}), '/', 2) AS _proj FROM read_parquet('{src}')),
              k AS (SELECT e.*, (SELECT n FROM m WHERE m.top = e._top LIMIT 1) AS _n FROM e)
              SELECT k.* EXCLUDE (_top, _proj, _n, uid, gid),
                     coalesce(m.uid, 20000)::BIGINT AS uid, coalesce(m.gid, 29999)::BIGINT AS gid
              FROM k LEFT JOIN m ON m.top = k._top AND m.i = CASE
                   WHEN hash(k.path) % 7 = 0 THEN hash(k.path) % k._n
                   ELSE hash(k._top || '/' || k._proj) % k._n END
            ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)
        """)
        # Restore the original column order.
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()]
        con.execute(f"COPY (SELECT {', '.join(cols)} FROM read_parquet('{tmp}')) TO '{src}' "
                    "(FORMAT parquet, COMPRESSION zstd)")
        tmp.unlink()
        part["bytes"] = src.stat().st_size
    names = {uid: name for accts in owners.values() for uid, name in accts}
    names[20000] = "svc_storage"
    users = sorted(names.items())
    gnames = {gid: f"{slug.split('_')[0]}_lab" for slug, gid in groups.items()} | {29999: "research"}
    for table, key, items in (("users", "uid", users), ("groups", "gid", sorted(gnames.items()))):
        d = scan_dir / table
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
        con.execute(f"CREATE OR REPLACE TABLE t ({key} BIGINT, name VARCHAR)")
        con.executemany("INSERT INTO t VALUES (?, ?)", items)
        out = d / "part-000000.parquet"
        con.execute(f"COPY t TO '{out}' (FORMAT parquet)")
        manifest["parts"][table] = [{"bytes": out.stat().st_size, "file": f"{table}/part-000000.parquet",
                                     "rows": len(items)}]
    (scan_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (scan_dir / "DEMO-OWNERS.txt").write_text(
        "Pinax demo: uid and gid in this scan were rewritten to fictional lab accounts by `pinax seed`.\n")
    con.close()


# ---------- Running ----------

def _rmtree(path: Path) -> None:
    if not path.exists():
        return

    def unlock(func, p, _exc):  # the demo's deliberately unreadable folder: restore access, then retry
        os.chmod(os.path.dirname(p), stat.S_IRWXU)
        if os.path.isdir(p) and not os.path.islink(p):
            os.chmod(p, stat.S_IRWXU)
        func(p)

    for dirpath, dirnames, _ in os.walk(path):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if not os.path.islink(full):
                os.chmod(full, stat.S_IRWXU)
    shutil.rmtree(path, onexc=unlock)


def _scan(settings: Settings, root: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [settings.fs_inventory_path(), "scan", str(root), "-o", str(out), "--resolve-ids", "--progress", "none",
           "--threads", "16"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"fs-inventory failed ({proc.returncode}): {proc.stderr}")


def run_seed(scale: float = 1.0) -> None:
    settings = get_settings()
    rng = random.Random(20261026)
    now = time.time()

    print("Resetting demo data and the catalog")
    indexes.clear()
    reset_engine()
    _rmtree(settings.demo_root)
    _rmtree(settings.snapshot_root)
    db_file = settings.var_root / "pinax.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_file) + suffix).unlink(missing_ok=True)
    fs_roots = {fs: settings.demo_root / "fs" / fs for fs, *_ in FILESYSTEMS}

    print("Writing demo filesystems (sparse files)")
    trees = build_trees(settings, rng, now, scale)
    for fs, root in fs_roots.items():
        root.mkdir(parents=True, exist_ok=True)
        trees[fs]["base"].write(root)
    locked = fs_roots["fast"] / "kowalski_d" / "instrument_exports" / "locked"
    locked.chmod(0)  # unreadable: the scan records an error and carries on

    Base.metadata.create_all(get_engine())
    with session_scope() as s:
        fss = {}
        for slug, name, display, desc in FILESYSTEMS:
            fss[slug] = Filesystem(slug=slug, name=name, root=str(fs_roots[slug].resolve()), display_root=display,
                                   description=desc)
            s.add(fss[slug])
        labs = {}
        for slug, name, kind, division, area, folders in LABS:
            lab = Lab(slug=slug, name=name, kind=kind, division=division, research_area=area)
            s.add(lab)
            s.flush()
            for fs, top in folders:
                s.add(LabFolder(lab_id=lab.id, filesystem_id=fss[fs].id, path=top))
            labs[slug] = lab
        for username, display, title, admin, memberships in PEOPLE:
            u = User(username=username, display_name=display, title=title, is_admin=admin,
                     email=f"{username}@example.org")
            s.add(u)
            s.flush()
            for slug, role in memberships:
                s.add(LabMembership(user_id=u.id, lab_id=labs[slug].id, role=role))
        s.add(AuditEvent(action="demo.seeded", summary="Demo data created by pinax seed"))

    owners, groups = demo_accounts(rng)
    scan_root = settings.demo_root / "scans"
    for round_no in (1, 2):
        if round_no == 2:
            print("Simulating activity: new sequencing runs and projects, cleanups")
            for fs, root in fs_roots.items():
                trees[fs]["later"].write(root)
            for fs, rel in REMOVED:
                shutil.rmtree(fs_roots[fs] / rel, ignore_errors=True)
            time.sleep(1.1)
        for fs, root in fs_roots.items():
            out = scan_root / fs / f"scan-{round_no}"
            print(f"Scanning {fs} with fs-inventory (scan {round_no} of 2)")
            _scan(settings, root, out)
            _assign_demo_owners(out, fs, str(root.resolve()), owners, groups)
            with session_scope() as s:
                snap = ingest_scan(s, out, fs_slug=fs, settings=settings, progress=lambda m: None)
                print(f"  snapshot {snap.id}: {snap.files:,} files, {snap.bytes / TB:,.1f} TB logical, "
                      f"index built in {snap.build_seconds}s")
    total = sum(len(t["base"].files) + len(t["later"].files) for t in trees.values())
    print(f"Done: {total:,} demo files across {len(fs_roots)} filesystems. Run `uv run pinax serve`.")
