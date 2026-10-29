# SPDX-License-Identifier: Apache-2.0
"""Hard links (R7): a file with several links counts its size once, at its first link by path; its other links are
files with no size. Checked against brute force on every directory and, with the binary, against fs-inventory
rollup of the scan with its extra links' sizes taken out."""

import duckdb
import pytest
from conftest import FILES, ROOT
from scanfix import fs_inventory, write_scan

from pinax.ingest.build import build_index

# run1.bam is also linked into the Beta Lab's archive (a later path, so the link there counts no size); S2_R1 has a
# second link beside it, whose name sorts after it.
LINKED = {"alpha_lab/projects/2015_old/run1.bam": 777, "alpha_lab/projects/2025_new/S2_R1.fastq.gz": 778}
EXTRA = [{"path": "beta_lab/archive/run1_link.bam", "size": 5000, "age_days": 4000, "uid": 2001, "ino": 777, "nlink": 2},
         {"path": "alpha_lab/projects/2025_new/S2_R1_copy.fastq.gz", "size": 2000, "age_days": 20, "uid": 2001,
          "ino": 778, "nlink": 2}]


def _files() -> list[dict]:
    return [dict(f, ino=LINKED[f["path"]], nlink=2) if f["path"] in LINKED else f for f in FILES] + EXTRA


@pytest.fixture
def linked(tmp_path):
    write_scan(tmp_path / "scan", ROOT, _files())
    build_index(tmp_path / "scan", tmp_path / "index", progress=lambda m: None)
    return tmp_path


def _brute(files: list[dict]) -> dict[str, tuple[int, int, int, int]]:
    """Each directory's files, bytes counted once per inode (at its first path), extra links, and their bytes."""
    first = {}
    for f in sorted(files, key=lambda f: f["path"]):
        if f.get("nlink", 1) > 1:
            first.setdefault(f["ino"], f["path"])
    dirs = {""} | {"/".join(f["path"].split("/")[:i]) for f in files for i in range(1, f["path"].count("/") + 1)}
    out = {}
    for d in dirs:
        below = [f for f in files if not d or f["path"].startswith(d + "/")]
        extra = [f for f in below if f.get("nlink", 1) > 1 and first[f["ino"]] != f["path"]]
        out[f"{ROOT}/{d}" if d else ROOT] = (len(below), sum(f["size"] for f in below) - sum(f["size"] for f in extra),
                                             len(extra), sum(f["size"] for f in extra))
    return out


def test_every_directory_counts_a_linked_file_once(linked):
    with duckdb.connect(str(linked / "index" / "index.duckdb"), read_only=True) as con:
        rows = con.execute("SELECT path, files, bytes, hl_files, hl_bytes FROM dirs").fetchall()
    got = {p: (f, b, hf, hb) for p, f, b, hf, hb in rows}
    assert got == _brute(_files())
    assert got[f"{ROOT}/beta_lab/archive"] == (2, 8000, 1, 5000)  # the link counts as a file, without its size
    assert got[f"{ROOT}/alpha_lab/projects/2015_old"][1] == 8050  # where the first link is, the size counts


def test_the_index_says_what_links_hold(linked):
    from pinax.index import SnapshotIndex

    idx = SnapshotIndex(linked / "index")
    try:
        assert idx.has_links
        assert idx.links(ROOT) == {"files": 2, "bytes": 7000}
        assert idx.links(f"{ROOT}/alpha_lab") == {"files": 1, "bytes": 2000}
        # Types and owners count once too.
        bam = next(t for t in idx.types(f"{ROOT}/beta_lab") if t["ext"] == "bam")
        assert bam["files"] == 1 and bam["bytes"] == 0
    finally:
        idx.close()


def test_a_scan_without_links_is_unchanged(tmp_path):
    write_scan(tmp_path / "scan", ROOT, FILES)
    build_index(tmp_path / "scan", tmp_path / "index", progress=lambda m: None)
    with duckdb.connect(str(tmp_path / "index" / "index.duckdb"), read_only=True) as con:
        assert con.execute("SELECT sum(hl_files), max(bytes) FROM dirs").fetchone() == (0, sum(f["size"] for f in FILES))


needs_binary = pytest.mark.skipif(fs_inventory() is None,
                                  reason="fs-inventory is not installed (scripts/install-fs-inventory.sh)")


@needs_binary
def test_the_check_still_agrees_with_rollup(linked):
    from pinax.crosscheck import check

    report = check(linked / "scan", linked / "index", linked / "work", binary=fs_inventory())
    assert report.ok, report.text()
