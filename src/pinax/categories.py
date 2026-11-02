# SPDX-License-Identifier: Apache-2.0
"""File categories and age buckets: the dimensions every snapshot index is built with.

A file's *type* is its normalized extension: the lowercased text after the last dot, joined with
the extension before it when the last one is a compression suffix, so `S1_R1.fastq.gz` is
`fastq.gz` and `brain.nii.gz` is `nii.gz`. Its *category* comes from the inner extension
(`fastq`, `nii`), so compressed sequencing data still counts as sequencing data.

The categories are the catalog's (`file_category`, filled with DEFAULTS the first time), and a site may change
them: each lists extensions, and may also list patterns, regular expressions on the file's name (RE2, as DuckDB
has them, so none can take exponential time), checked before every extension, category by category and in order;
the first that matches wins, and a file nothing takes is *other*, always last. They are decided when an index is
built and recorded in it, with a fingerprint (`fingerprint`), so changing them only affects indexes built after;
an index built with other categories than the catalog's is outdated (`pinax rebuild --outdated`).
"""

import hashlib
import json
from dataclasses import dataclass, field

# Compression suffixes that wrap another format.
COMPRESSION_SUFFIXES = ("gz", "bgz", "bz2", "xz", "zst", "zstd", "lz4", "z", "lzma", "br")


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    description: str
    extensions: tuple[str, ...]
    patterns: tuple[str, ...] = field(default=())


MAX_CATEGORIES = 24  # each adds a file count and a size to every folder of an index
MAX_PATTERNS = 25  # in all, so a build at a billion files stays within its time

DEFAULTS: tuple[Category, ...] = (
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
        "heic", "heif", "webp", "jfif", "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2", "insp",
    )),
    Category("tabular", "Tabular and array data", "Spreadsheets, data frames, arrays, and scientific containers", (
        "csv", "tsv", "tab", "xlsx", "xls", "parquet", "feather", "arrow", "orc", "avro", "json", "jsonl", "ndjson",
        "xml", "h5", "hdf5", "hdf", "he5", "nc", "netcdf", "mat", "npy", "npz", "rds", "rdata", "rda", "sav", "dta",
        "sas7bdat", "xpt", "db", "sqlite", "sqlite3", "duckdb", "pkl", "pickle", "fst", "qs", "yaml", "yml", "dat",
        "xlsm", "xlsb", "accdb", "mdb",
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
        "exe", "dll", "dylib",
    )),
    Category("documents", "Documents and text", "Papers, slides, notes, and plain text", (
        "pdf", "doc", "docx", "ppt", "pptx", "odt", "odp", "ods", "rtf", "txt", "md", "rst", "tex", "bib", "html", "htm",
        "epub", "key", "pages", "numbers", "msg", "eml", "ai", "eps", "ps", "svg", "indd", "psd",
        "vsd", "vsdx", "emf", "wmf", "oft", "one",
    )),
    Category("media", "Video and audio", "Recordings and instrument video", (
        "mp4", "mov", "avi", "mkv", "wmv", "m4v", "mpg", "mpeg", "webm", "wav", "mp3", "flac", "aac", "m4a", "ogg",
        "aiff", "aif", "insv", "lrv", "mts", "m2ts", "3gp",
    )),
    Category("containers", "Containers and disk images", "Container images, virtual machines, and disk images", (
        "sif", "simg", "img", "iso", "qcow2", "vmdk", "vdi", "vhd", "vhdx", "ova", "ovf", "dmg", "docker",
        "asif", "sparseimage",
    )),
    Category("transient", "Logs and temporary", "Logs, job output, swap, backup, and temporary files", (
        "log", "out", "err", "tmp", "temp", "bak", "swp", "swo", "old", "orig", "lock", "pid", "core", "dump", "trace",
        "crdownload", "part", "cache",
    )),
    Category("other", "Other or no extension", "Files with no extension or one Pinax doesn't recognize", ()),
)

OTHER_KEY = "other"


def extension_map(categories: tuple[Category, ...] | list[Category]) -> dict[str, int]:
    """Extension -> category index. First category wins for an extension listed twice."""
    out: dict[str, int] = {}
    for i, c in enumerate(categories):
        for ext in c.extensions:
            out.setdefault(ext, i)
    return out


def patterns(categories: tuple[Category, ...] | list[Category]) -> list[tuple[str, int]]:
    """Every pattern, with its category's index, in the order they are checked."""
    return [(p, i) for i, c in enumerate(categories) for p in c.patterns]


def fingerprint(categories: tuple[Category, ...] | list[Category]) -> str:
    """What decides a file's category, and nothing else (not names or descriptions): an index built with the same
    fingerprint as the catalog's categories is current."""
    rules = [[c.key, sorted(c.extensions), list(c.patterns)] for c in categories]
    return hashlib.sha256(json.dumps(rules, separators=(",", ":")).encode()).hexdigest()[:16]


def from_dimensions(dims: dict | None) -> tuple[Category, ...]:
    """An index's own categories, from its metadata. Indexes built before categories were the catalog's recorded
    only their keys, labels, and descriptions, and were built with the defaults."""
    cats = (dims or {}).get("categories")
    if not cats or "extensions" not in cats[0]:
        return DEFAULTS
    return tuple(Category(c["key"], c["label"], c.get("description", ""), tuple(c["extensions"]),
                          tuple(c.get("patterns", ()))) for c in cats)


class CategoryError(ValueError):
    pass


def check(categories: tuple[Category, ...] | list[Category]) -> None:
    """What a build needs of a list of categories; raises CategoryError saying what is wrong."""
    if not categories or categories[-1].key != OTHER_KEY or categories[-1].extensions or categories[-1].patterns:
        raise CategoryError("The last category must be *other*, with no extensions or patterns")
    if len(categories) > MAX_CATEGORIES:
        raise CategoryError(f"At most {MAX_CATEGORIES} categories")
    keys = [c.key for c in categories]
    if len(set(keys)) != len(keys):
        raise CategoryError("Each category needs a key of its own")
    found = patterns(categories)
    if len(found) > MAX_PATTERNS:
        raise CategoryError(f"At most {MAX_PATTERNS} patterns in all")
    for pattern, _ in found:
        if problem := pattern_problem(pattern):
            raise CategoryError(f"The pattern {pattern!r} {problem}")


def pattern_problem(pattern: str) -> str | None:
    """Why DuckDB (RE2) would not take `pattern` as a regular expression, or None."""
    import duckdb

    if not pattern or len(pattern) > 500:
        return "must have 1 to 500 characters"
    try:
        with duckdb.connect() as con:
            con.execute("SELECT regexp_matches('', ?)", [pattern]).fetchone()
    except duckdb.Error as e:
        return f"is not a regular expression DuckDB reads: {str(e).split(':', 1)[-1].strip()}"
    return None


def current(session) -> tuple[Category, ...]:
    """The catalog's categories, in order (the defaults if the catalog has none yet)."""
    from sqlalchemy import select

    from .models import FileCategory

    rows = session.scalars(select(FileCategory).order_by(FileCategory.position)).all()
    if not rows:
        return DEFAULTS
    return tuple(Category(r.key, r.label, r.description, tuple(r.extensions), tuple(r.patterns)) for r in rows)


def ensure_defaults() -> None:
    """The built-in categories, the first time the catalog is set up (never again, so a site's changes stay)."""
    from sqlalchemy import select

    from .db import session_scope
    from .models import FileCategory

    with session_scope() as s:
        if s.scalar(select(FileCategory.id).limit(1)) is not None:
            return
        for i, c in enumerate(DEFAULTS):
            s.add(FileCategory(key=c.key, label=c.label, description=c.description, position=i,
                               extensions=list(c.extensions), patterns=list(c.patterns), builtin=True))


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


def describe(categories: tuple[Category, ...] | list[Category]) -> dict:
    """The dimensions an index is built with, as stored in its metadata."""
    return {
        "categories": [{"key": c.key, "label": c.label, "description": c.description, "extensions": list(c.extensions),
                        "patterns": list(c.patterns)} for c in categories],
        "categories_id": fingerprint(categories),
        "age_buckets": [{"label": b.label, "short": b.short, "max_days": b.max_days} for b in AGE_BUCKETS],
        "compression_suffixes": list(COMPRESSION_SUFFIXES),
    }


def brief(categories: tuple[Category, ...] | list[Category]) -> list[dict]:
    """Categories as the interface shows them."""
    return [{"key": c.key, "label": c.label, "description": c.description} for c in categories]
