# SPDX-License-Identifier: Apache-2.0
"""Queries over an index: children with folding, subtree types, cold folders, and search patterns."""

import pytest
from conftest import ROOT

from pinax.index import SnapshotIndex, Stats, child_range, pattern_to_like
from pinax.ingest.build import build_index


@pytest.fixture
def idx(tmp_path, scan):
    out = tmp_path / "index"
    build_index(scan, out, progress=lambda m: None)
    i = SnapshotIndex(out)
    yield i
    i.close()


def test_child_range_is_exact():
    lo, hi = child_range("/a/b")
    inside = ["/a/b/c", "/a/b/c/d", "/a/b/"]
    outside = ["/a/b", "/a/bc", "/a/b-x", "/a/b0", "/a/c"]
    assert all(lo <= p < hi for p in inside)
    assert not any(lo <= p < hi for p in outside)
    assert child_range("/") == ("/", "0")


def test_children_fold_the_rest_and_add_up(idx):
    res = idx.children(ROOT, limit=2, grand_limit=1)
    assert [r["name"] for r in res["rows"]] == ["alpha_lab", "beta_lab"]
    assert res["all"]["n"] == 4 and res["rest"]["n"] == 2
    node = Stats.from_row(idx.node(ROOT)).json(idx.ref_day)
    listed = sum(r["bytes"] for r in res["rows"])
    assert listed + res["rest"]["bytes"] + 7 == node["bytes"]  # 7 bytes sit directly in the root
    alpha = next(r for r in res["rows"] if r["name"] == "alpha_lab")
    g = res["grand"][alpha["dir_id"]]
    assert [r["name"] for r in g["rows"]] == ["projects"] and g["rest"]["n"] == 0


def test_types_over_a_deep_subtree(idx):
    rows = {r["ext"]: r for r in idx.types(ROOT + "/alpha_lab/projects/2015_old")}
    assert set(rows) == {"bam", "bai", "fastq.gz"}
    assert rows["bam"]["bytes"] == 5000 and rows["fastq.gz"]["files"] == 1


def test_cold_folders_are_maximal(idx):
    cutoff = idx.ref_epoch - 3 * 365.25 * 86400
    res = idx.cold_dirs(ROOT + "/alpha_lab", cutoff)
    paths = {r["path"] for r in res["rows"]}
    # 2015_old is cold as a whole, so its reads/ subfolder is not listed separately; legacy/ is cold
    # inside an active project.
    assert paths == {ROOT + "/alpha_lab/projects/2015_old", ROOT + "/alpha_lab/projects/2025_new/legacy"}
    assert res["bytes"] == 5000 + 50 + 3000 + 9000


def test_search_patterns_escape_like_wildcards(idx):
    assert pattern_to_like("*.bam") == "%.bam"
    assert pattern_to_like("100%") == "%100\\%%"
    hits = idx.files(ROOT, where="name ILIKE ? ESCAPE '\\'", params=[pattern_to_like("100%_")])
    assert [h["name"] for h in hits] == ["notes 100%_done.txt"]
    assert idx.files(ROOT, where="name ILIKE ? ESCAPE '\\'", params=[pattern_to_like("1_0")]) == []
    bams = idx.files(ROOT, where="name ILIKE ? ESCAPE '\\'", params=[pattern_to_like("*.bam")])
    assert [h["name"] for h in bams] == ["run1.bam"]


def test_files_in_one_top_folder_only(idx):
    names = {f["name"] for f in idx.files(ROOT + "/alpha_lab", limit=100)}
    assert "brain.nii.gz" not in names  # alpha_lab2 shares a prefix but is another folder
    assert "run1.bam" in names
