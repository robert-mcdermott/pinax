# SPDX-License-Identifier: Apache-2.0
"""File categories and age buckets: the fixed dimensions every snapshot index is built with.

A file's *type* is its normalized extension: the lowercased text after the last dot, joined with
the extension before it when the last one is a compression suffix, so `S1_R1.fastq.gz` is
`fastq.gz` and `brain.nii.gz` is `nii.gz`. Its *category* comes from the inner extension
(`fastq`, `nii`), so compressed sequencing data still counts as sequencing data.

Both lists are stored in each snapshot's metadata, so changing them only affects new snapshots.
"""

from dataclasses import dataclass

# Compression suffixes that wrap another format.
COMPRESSION_SUFFIXES = ("gz", "bgz", "bz2", "xz", "zst", "zstd", "lz4", "z", "lzma", "br")


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    description: str
    extensions: tuple[str, ...]


CATEGORIES: tuple[Category, ...] = (
    Category("genomics", "Sequencing and genomics", "Reads, alignments, variants, assemblies, and annotations", (
        "fastq", "fq", "fasta", "fa", "fna", "ffn", "faa", "frn", "fas", "2bit", "sam", "bam", "cram", "bai", "crai",
        "csi", "vcf", "bcf", "gvcf", "g.vcf", "tbi", "bed", "bedgraph", "bigwig", "bw", "bigbed", "bb", "gtf", "gff",
        "gff3", "sra", "fast5", "pod5", "slow5", "blow5", "bcl", "cbcl", "locs", "filter", "fai", "dict", "maf", "psl",
        "wig", "pileup", "mpileup", "sff", "ab1", "scf", "gb", "gbk", "embl", "nwk", "newick", "aln", "sto", "phy",
        "h5ad", "loom", "mtx", "10x", "hic", "cool", "mcool", "pairs", "bedpe", "narrowpeak", "broadpeak",
    )),
    Category("imaging", "Imaging and microscopy", "Microscopy, slide scans, medical and electron imaging", (
        "tif", "tiff", "ome.tif", "ome.tiff", "nd2", "czi", "lif", "lsm", "ims", "oib", "oif", "svs", "ndpi", "scn",
        "mrxs", "vsi", "dcm", "dicom", "nii", "nrrd", "mha", "mhd", "mrc", "mrcs", "em", "dm3", "dm4", "tia", "ser",
        "eer", "zarr", "png", "jpg", "jpeg", "bmp", "gif", "jp2", "fcs", "stk", "ics", "ids", "spe", "sld", "xdce",
    )),
    Category("tabular", "Tabular and array data", "Spreadsheets, data frames, arrays, and scientific containers", (
        "csv", "tsv", "tab", "xlsx", "xls", "parquet", "feather", "arrow", "orc", "avro", "json", "jsonl", "ndjson",
        "xml", "h5", "hdf5", "hdf", "he5", "nc", "netcdf", "mat", "npy", "npz", "rds", "rdata", "rda", "sav", "dta",
        "sas7bdat", "xpt", "db", "sqlite", "sqlite3", "duckdb", "pkl", "pickle", "fst", "qs", "yaml", "yml", "dat",
    )),
    Category("massspec", "Mass spectrometry", "Instrument raw files and processed spectra", (
        "raw", "mzml", "mzxml", "mzdata", "mgf", "wiff", "wiff2", "wiff.scan", "baf", "tdf", "tdf_bin", "yep", "d",
        "imzml", "ibd", "mzid", "pepxml", "protxml", "idxml", "mzidentml", "msp", "dia", "blib",
    )),
    Category("compressed", "Archives and compressed", "Tarballs, zip files, and compressed data of unknown type", (
        "tar", "tgz", "tbz", "tbz2", "txz", "zip", "7z", "rar", "gz", "bgz", "bz2", "xz", "zst", "zstd", "lz4", "z",
        "lzma", "br", "cpio", "dar", "squashfs", "sqfs",
    )),
    Category("models", "Models and checkpoints", "Machine-learning weights, checkpoints, and serialized models", (
        "pt", "pth", "ckpt", "safetensors", "onnx", "pb", "tflite", "keras", "joblib", "gguf", "ggml", "mar", "msgpack",
        "params", "weights",
    )),
    Category("code", "Code and notebooks", "Source code, scripts, workflows, and notebooks", (
        "py", "r", "rmd", "qmd", "ipynb", "sh", "bash", "zsh", "pl", "pm", "java", "c", "h", "cc", "cpp", "hpp", "cxx",
        "f", "f90", "f95", "js", "ts", "go", "rs", "jl", "m", "sql", "nf", "wdl", "smk", "cwl", "scala", "rb", "lua",
        "awk", "sed", "mk", "cmake", "slurm", "sbatch", "pbs", "sas", "do", "class", "jar", "so", "o", "a", "pyc",
    )),
    Category("documents", "Documents and text", "Papers, slides, notes, and plain text", (
        "pdf", "doc", "docx", "ppt", "pptx", "odt", "odp", "ods", "rtf", "txt", "md", "rst", "tex", "bib", "html", "htm",
        "epub", "key", "pages", "numbers", "msg", "eml", "ai", "eps", "ps", "svg", "indd", "psd",
    )),
    Category("media", "Video and audio", "Recordings and instrument video", (
        "mp4", "mov", "avi", "mkv", "wmv", "m4v", "mpg", "mpeg", "webm", "wav", "mp3", "flac", "aac", "m4a", "ogg",
        "aiff", "aif",
    )),
    Category("containers", "Containers and disk images", "Container images, virtual machines, and disk images", (
        "sif", "simg", "img", "iso", "qcow2", "vmdk", "vdi", "vhd", "vhdx", "ova", "ovf", "dmg", "docker",
    )),
    Category("transient", "Logs and temporary", "Logs, job output, swap, backup, and temporary files", (
        "log", "out", "err", "tmp", "temp", "bak", "swp", "swo", "old", "orig", "lock", "pid", "core", "dump", "trace",
        "crdownload", "part", "cache",
    )),
    Category("other", "Other or no extension", "Files with no extension or one Pinax doesn't recognize", ()),
)

CATEGORY_KEYS = tuple(c.key for c in CATEGORIES)
OTHER = len(CATEGORIES) - 1


def extension_map() -> dict[str, int]:
    """Extension -> category index. First category wins for an extension listed twice."""
    out: dict[str, int] = {}
    for i, c in enumerate(CATEGORIES):
        for ext in c.extensions:
            out.setdefault(ext, i)
    return out


@dataclass(frozen=True)
class AgeBucket:
    label: str
    short: str
    max_days: float | None  # exclusive upper edge; None for the last bucket


# Age buckets, youngest first. Each snapshot records its own copy. Seven buckets, because the
# interface's age color ramp has seven validated steps (see docs/DEVELOPMENT.md).
AGE_BUCKETS: tuple[AgeBucket, ...] = (
    AgeBucket("Under 90 days", "<90d", 90),
    AgeBucket("90 days to 1 year", "90d-1y", 365.25),
    AgeBucket("1 to 2 years", "1-2y", 2 * 365.25),
    AgeBucket("2 to 3 years", "2-3y", 3 * 365.25),
    AgeBucket("3 to 5 years", "3-5y", 5 * 365.25),
    AgeBucket("5 to 10 years", "5-10y", 10 * 365.25),
    AgeBucket("10 years or more", "10y+", None),
)


def bucket_edges() -> list[float]:
    return [b.max_days for b in AGE_BUCKETS if b.max_days is not None]


def bucket_for_days(days: float | None) -> int | None:
    if days is None:
        return None
    for i, edge in enumerate(bucket_edges()):
        if days < edge:
            return i
    return len(AGE_BUCKETS) - 1


def describe() -> dict:
    """The dimensions, as stored in snapshot metadata and served to the interface."""
    return {
        "categories": [{"key": c.key, "label": c.label, "description": c.description} for c in CATEGORIES],
        "age_buckets": [{"label": b.label, "short": b.short, "max_days": b.max_days} for b in AGE_BUCKETS],
        "compression_suffixes": list(COMPRESSION_SUFFIXES),
    }
