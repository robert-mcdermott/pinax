# SPDX-License-Identifier: Apache-2.0
"""Queries over an index: children with folding, subtree types, cold folders, and search patterns."""

import pytest
from conftest import FILES, ROOT

from pinax.categories import bucket_edges
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
    res = idx.cold_dirs(ROOT + "/alpha_lab", 4)  # three years or more
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


def _brute_cold(place: str, older: int) -> tuple[set[str], int]:
    """Maximal cold folders at or below `place`, straight from the file list: folders whose every file is at
    least as old as the threshold, and whose parent has a newer one (or is above `place`)."""
    edge = bucket_edges()[older - 1]
    rel = place[len(ROOT) + 1:] if place != ROOT else ""
    dirs = {""} | {"/".join(f["path"].split("/")[:i]) for f in FILES for i in range(1, f["path"].count("/") + 1)}

    def under(d: str) -> list[dict]:
        return [f for f in FILES if not d or f["path"].startswith(d + "/")]

    def cold(d: str) -> bool:
        fs = under(d)
        return bool(fs) and all(f["age_days"] >= edge for f in fs)

    def parent(d: str) -> str:
        return d.rsplit("/", 1)[0] if "/" in d else ""

    inside = [d for d in dirs if d == rel or (not rel and d) or d.startswith(rel + "/")]
    units = {d for d in inside if cold(d) and (d == rel or not cold(parent(d)))}
    full = {f"{ROOT}/{d}" if d else ROOT for d in units}
    return full, sum(f["size"] for d in units for f in under(d))


@pytest.mark.parametrize("older", range(1, 7))
def test_precomputed_cold_folders_match_brute_force(idx, older):
    assert idx.has_cold_units
    places = [ROOT, ROOT + "/alpha_lab", ROOT + "/alpha_lab/projects", ROOT + "/alpha_lab/projects/2015_old",
              ROOT + "/beta_lab", ROOT + "/beta_lab/imaging", ROOT + "/orphans"]
    for place in places:
        res = idx.cold_dirs(place, older)
        paths, total = _brute_cold(place, older)
        assert {r["path"] for r in res["rows"]} == paths, (place, older)
        assert (res["count"], res["bytes"]) == (len(paths), total), (place, older)
        # The same as joining every directory with its parent at query time (indexes before version 3).
        legacy = idx._cold_scan(place, idx.ref_epoch - bucket_edges()[older - 1] * 86400)
        assert {r["path"] for r in legacy["rows"]} == paths and legacy["bytes"] == total, (place, older)


def _brute_folders(metric: str) -> dict[str, int]:
    """Every folder's bytes or files, straight from the file list."""
    out: dict[str, int] = {}
    for f in FILES:
        parts = f["path"].split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = f"{ROOT}/{'/'.join(parts[:i])}"
            out[d] = out.get(d, 0) + (f["size"] if metric == "bytes" else 1)
    return out


@pytest.mark.parametrize("metric", ["bytes", "files"])
def test_partition_is_every_folder_above_the_threshold_as_a_tree(idx, metric):
    sizes = _brute_folders(metric)
    cases = [(ROOT, 6, 1), (ROOT, 2, 1), (ROOT, 6, 3000), (ROOT, 6, 2), (ROOT + "/alpha_lab", 2, 100),
             (ROOT + "/alpha_lab/projects/2015_old", 6, 1), (ROOT + "/orphans/old", 6, 1)]
    for place, levels, least in cases:
        rows = idx.partition(place, metric=metric, levels=levels, least=least)
        expect = {d for d, v in sizes.items() if d.startswith(place + "/") and v >= least
                  and d.count("/") - place.count("/") <= levels}
        assert {r["path"] for r in rows} == expect, (place, levels, least)
        assert all(r[metric] == sizes[r["path"]] for r in rows)
        # Level by level, the largest first, each folder after its parent.
        top, seen = idx.depth_of(place) + 1, set()
        for prev, r in zip([None, *rows], rows):
            assert r["depth"] == top or r["parent_id"] in seen
            if prev is not None and prev["depth"] == r["depth"]:
                assert prev[metric] >= r[metric]
            seen.add(r["dir_id"])
    assert idx.measure(ROOT, metric) == sum(f["size"] if metric == "bytes" else 1 for f in FILES)
    assert idx.measure(ROOT + "/nowhere", metric) == 0
