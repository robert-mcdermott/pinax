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
import re
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
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


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


# ---------- Changing them (storage administrators, from *File categories*) ----------

EXTENSION = re.compile(r"[a-z0-9][a-z0-9._+-]{0,31}")


def category_json(row) -> dict:
    return {"key": row.key, "label": row.label, "description": row.description, "position": row.position,
            "extensions": row.extensions, "patterns": row.patterns, "builtin": row.builtin,
            "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None}


def _rows(session) -> list:
    from sqlalchemy import select

    from .models import FileCategory

    return list(session.scalars(select(FileCategory).order_by(FileCategory.position)))


def _as_category(row) -> Category:
    return Category(row.key, row.label, row.description, tuple(row.extensions), tuple(row.patterns))


def clean_extensions(extensions: list[str]) -> list[str]:
    """Lowercased, without a leading dot, each once; one that is not an extension is refused."""
    out: list[str] = []
    for e in extensions:
        e = e.strip().lower().lstrip(".")
        if not e:
            continue
        if not EXTENSION.fullmatch(e):
            raise CategoryError(f"{e!r} is not an extension: letters, digits, and . _ + - only, up to 32")
        if e not in out:
            out.append(e)
    return out


def clean_patterns(patterns: list[str]) -> list[str]:
    out = [p.strip() for p in patterns if p.strip()]
    for pattern in out:
        if problem := pattern_problem(pattern):
            raise CategoryError(f"The pattern {pattern!r} {problem}")
    return out


def proposed(session, key: str | None, *, label: str | None = None, description: str | None = None,
             extensions: list[str] | None = None, patterns: list[str] | None = None, remove: bool = False,
             to: int | None = None) -> tuple[Category, ...]:
    """The catalog's categories as they would be with one change: `key`'s (None: a new one) fields, removing it, or
    moving it to place `to`. Raises CategoryError if the result is not a list a build can use."""
    rows = _rows(session)
    cats = [_as_category(r) for r in rows]
    builtin = {r.key for r in rows if r.builtin}
    if key is not None and key not in {c.key for c in cats}:
        raise CategoryError("No such category", 404)
    i = next((n for n, c in enumerate(cats) if c.key == key), None)
    if remove:
        if key in builtin:
            raise CategoryError("A built-in category can be changed but not removed")
        del cats[i]
        return tuple(cats)
    if to is not None:
        if key == OTHER_KEY or not 0 <= to < len(cats) - 1:
            raise CategoryError("*Other* stays last; the others move among themselves")
        cats.insert(to, cats.pop(i))
        return tuple(cats)
    old = cats[i] if i is not None else Category("", "", "", ())
    label = (label if label is not None else old.label).strip()[:80]
    if not label:
        raise CategoryError("A category needs a name")
    exts = tuple(clean_extensions(extensions)) if extensions is not None else old.extensions
    pats = tuple(clean_patterns(patterns)) if patterns is not None else old.patterns
    if key == OTHER_KEY and (exts or pats):
        raise CategoryError("*Other* takes what no other category does: it has no extensions or patterns")
    new = Category(old.key or _new_key(label, cats), label,
                   (description if description is not None else old.description).strip()[:300], exts, pats)
    if i is None:
        cats.insert(len(cats) - 1, new)  # before *other*
    else:
        cats[i] = new
    check(cats)
    return tuple(cats)


def _new_key(label: str, cats: list[Category]) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32] or "category"
    if any(c.key == key for c in cats) or key == OTHER_KEY:
        raise CategoryError(f"There is a category {label!r} already", 409)
    return key


def apply(session, actor, cats: tuple[Category, ...], action: str, key: str) -> None:
    """Make the catalog's categories `cats` (from `proposed`), audited as `action` on `key`."""
    from .db import utcnow
    from .models import AuditEvent, FileCategory

    rows = {r.key: r for r in _rows(session)}
    before = {k: _as_category(r) for k, r in rows.items()}
    for gone in set(rows) - {c.key for c in cats}:
        session.delete(rows.pop(gone))
    session.flush()
    for n, c in enumerate(cats):
        row = rows.get(c.key)
        if row is None:
            row = rows[c.key] = FileCategory(key=c.key, builtin=False, position=n)
            session.add(row)
        row.label, row.description, row.position = c.label, c.description, n
        row.extensions, row.patterns = list(c.extensions), list(c.patterns)
        if before.get(c.key) != c:
            row.updated_at = utcnow()
    after = next((c for c in cats if c.key == key), None)
    was = before.get(key)
    detail: dict = {"category": key, "fingerprint": fingerprint(cats)}
    if action == "category.changed":
        detail |= {f: {"from": list(getattr(was, f)) if f in ("extensions", "patterns") else getattr(was, f),
                       "to": list(getattr(after, f)) if f in ("extensions", "patterns") else getattr(after, f)}
                   for f in ("label", "description", "extensions", "patterns") if getattr(was, f) != getattr(after, f)}
    elif action == "category.moved":
        detail["position"] = [c.key for c in cats].index(key) + 1
    elif action == "category.added":
        detail |= {"extensions": list(after.extensions), "patterns": list(after.patterns)}
    name = (after or was).label
    summary = {"category.added": f"File category {name!r} added", "category.changed": f"File category {name!r} changed",
               "category.removed": f"File category {name!r} removed",
               "category.moved": f"File category {name!r} moved to place {detail.get('position')}"}[action]
    session.add(AuditEvent(actor_id=actor.id if actor else None, action=action, summary=summary, detail=detail))
    session.flush()


# ---------- What a change would do ----------

def _type_lookup(ext: str | None) -> str:
    """The extension a type's category comes from: the inner one of a compressed type (`fastq` of `fastq.gz`)."""
    if not ext:
        return ""
    inner, _, last = ext.rpartition(".")
    return inner if inner and last in COMPRESSION_SUFFIXES else ext


def rebuild_info(session, cats: tuple[Category, ...] | list[Category]) -> dict:
    """The ready snapshots built with other categories than `cats`, and about how long building them again takes
    (their last build times), all of them and each filesystem's latest."""
    from sqlalchemy import select

    from .models import Snapshot, SnapshotStatus

    want, defaults = fingerprint(cats), fingerprint(DEFAULTS)
    ready = session.scalars(select(Snapshot).where(Snapshot.status == SnapshotStatus.READY)
                            .order_by(Snapshot.started_at)).all()
    latest = {snap.filesystem_id: snap for snap in ready}
    old = [snap for snap in ready if ((snap.info or {}).get("categories_id") or defaults) != want]
    newest = [snap for snap in old if latest[snap.filesystem_id] is snap]
    seconds = lambda snaps: round(sum(snap.build_seconds or 0 for snap in snaps))
    return {"snapshots": len(ready), "outdated": len(old), "seconds": seconds(old), "latest": len(newest),
            "latest_seconds": seconds(newest)}


def _hits(cats: tuple[Category, ...] | list[Category], alias: str) -> str:
    """A column: the number (in `patterns(cats)`) of the first pattern a sampled file's name matches, or NULL."""
    found = patterns(cats)
    if not found:
        return f", NULL AS {alias}"
    whens = " ".join(f"WHEN regexp_matches(name, '{p.replace(chr(39), chr(39) * 2)}') THEN {n}"
                     for n, (p, _) in enumerate(found))
    return f", CASE {whens} END AS {alias}"


def preview(session, cats: tuple[Category, ...] | list[Category]) -> list[dict]:
    """How each filesystem's latest scan would change with `cats`, from what its index was built with: the types
    whose category comes from their extension, exactly, from the index's table of types (no file read); and the
    files a pattern takes or gives up, estimated from a sample of their names (`SnapshotIndex.sample_files`)."""
    from collections import defaultdict

    from sqlalchemy import select

    from .models import Filesystem
    from .snapshots import indexes, latest_snapshot

    new_ext, new_pats = extension_map(cats), patterns(cats)
    by_ext = lambda lookup, cs, m: cs[m[lookup]].key if lookup in m else OTHER_KEY
    out = []
    for fs in session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name)):
        snap = latest_snapshot(session, fs)
        if snap is None:
            continue
        idx = indexes.get(snap)
        own, own_ext, own_pats = idx.categories, extension_map(idx.categories), patterns(idx.categories)
        moves: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
        for r in idx.types(idx.root):
            was, lookup = idx.category_keys[r["cat"]], _type_lookup(r["ext"])
            if was != by_ext(lookup, own, own_ext):
                continue  # a pattern's: in the sample's estimate
            now = by_ext(lookup, cats, new_ext)
            if now != was:
                m = moves[(r["ext"], was, now)]
                m[0] += int(r["files"])
                m[1] += int(r["bytes"])
        sample = None
        if new_pats or own_pats:
            rows = idx.sample_files(extra=_hits(cats, "hit") + _hits(own, "had"))
            files = int(snap.files or 0)
            named: dict[tuple, dict] = defaultdict(lambda: {"files": 0, "bytes": 0, "examples": []})
            for r in rows:
                if r["hit"] is None and r["had"] is None:
                    continue  # by extension alone: in the exact part
                was = idx.category_keys[r["cat"]]
                now = cats[new_pats[r["hit"]][1]].key if r["hit"] is not None else by_ext(_type_lookup(r["ext"]), cats, new_ext)
                if now != was:
                    m = named[(was, now)]
                    m["files"] += 1
                    m["bytes"] += int(r["size"] or 0)
                    if len(m["examples"]) < 5:
                        m["examples"].append(r["name"])
            scale = files / len(rows) if rows else 0
            sample = {"files": len(rows), "of": files, "moves": [
                {"from": a, "to": b, "files": m["files"], "estimate_files": round(m["files"] * scale),
                 "estimate_bytes": round(m["bytes"] * scale), "examples": m["examples"]}
                for (a, b), m in sorted(named.items(), key=lambda kv: -kv[1]["bytes"])]}
        out.append({"fs": fs.slug, "fs_name": fs.name, "snapshot": snap.id, "files": int(snap.files or 0),
                    "by_type": [{"ext": e, "from": a, "to": b, "files": f, "bytes": b2}
                                for (e, a, b), (f, b2) in sorted(moves.items(), key=lambda kv: -kv[1][1])],
                    "sample": sample})
    return out


def try_pattern(session, pattern: str) -> dict:
    """Whether DuckDB takes `pattern`, and which names it matches in a sample of each filesystem's latest scan."""
    from sqlalchemy import select

    from .models import Filesystem
    from .snapshots import indexes, latest_snapshot

    if problem := pattern_problem(pattern.strip()):
        return {"problem": f"This {problem}", "filesystems": []}
    out = []
    for fs in session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name)):
        snap = latest_snapshot(session, fs)
        if snap is None:
            continue
        rows = indexes.get(snap).sample_files(extra=", regexp_matches(name, ?) AS hit", params=[pattern.strip()])
        hits = [r for r in rows if r["hit"]]
        out.append({"fs": fs.slug, "fs_name": fs.name, "sample": len(rows), "matches": len(hits),
                    "examples": [r["name"] for r in hits[:10]]})
    return {"problem": None, "filesystems": out}
