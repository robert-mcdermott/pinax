# SPDX-License-Identifier: Apache-2.0
"""Searches as background jobs (R13): run past a page's time limit, their results kept and downloaded, seen only by
whoever asked and administrators, stopped at their own time limit, and marked stopped if Pinax stopped."""

import time

import pytest
from conftest import ROOT, login

from pinax import jobs


def _wait(client, job_id: int, seconds: float = 20) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        job = client.get(f"/api/files/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("the search did not finish")


def test_a_search_of_a_whole_filesystem_in_the_background(client):
    login(client, "ivy")
    r = client.post("/api/files/jobs", params={"fs": "fast", "q": "*.ba?", "sort": "size"})
    assert r.status_code == 200, r.text
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done" and job["files"] == 2 and job["bytes"] == 5050
    assert [f["name"] for f in job["rows"]] == ["run1.bam", "run1.bam.bai"]  # largest first, as asked
    csv = client.get(f"/api/files/jobs/{job['id']}/csv")
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("path,") and len(csv.text.splitlines()) == 3
    listed = client.get("/api/files/jobs").json()
    assert [s["id"] for s in listed["searches"]] == [job["id"]] and listed["days"] == 7


def test_who_may_search_and_see(client):
    login(client, "ada")
    assert client.post("/api/files/jobs", params={"fs": "fast", "q": "*.bam"}).status_code == 404  # the root: admins
    mine = client.post("/api/files/jobs", params={"fs": "fast", "path": f"{ROOT}/alpha_lab", "q": "*"}).json()
    assert _wait(client, mine["id"])["files"] == 7
    login(client, "ben")
    assert client.get(f"/api/files/jobs/{mine['id']}").status_code == 404
    assert client.get("/api/files/jobs").json()["searches"] == []
    login(client, "ivy")
    assert [s["by"] for s in client.get("/api/files/jobs").json()["searches"]] == ["Ada"]


def test_deleting_a_search_deletes_what_it_found(client):
    login(client, "ivy")
    job = _wait(client, client.post("/api/files/jobs", params={"fs": "fast"}).json()["id"])
    assert jobs.job_dir(job["id"]).exists()
    assert client.delete(f"/api/files/jobs/{job['id']}").status_code == 200
    assert not jobs.job_dir(job["id"]).exists()
    assert client.get(f"/api/files/jobs/{job['id']}").status_code == 404


def test_searches_left_running_are_stopped_when_pinax_starts(client):
    from pinax.db import session_scope
    from pinax.models import SearchJob, User

    with session_scope() as s:
        user = s.query(User).filter_by(username="ivy").one()
        s.add(SearchJob(user_id=user.id, request={"fs": "fast", "path": ROOT, "sort": "size", "filter": {}, "places": []},
                        status="running"))
    jobs.recover()
    login(client, "ivy")
    (job,) = client.get("/api/files/jobs").json()["searches"]
    assert job["status"] == "stopped" and "Pinax stopped" in job["message"]


def test_a_search_that_runs_too_long_is_stopped(client, monkeypatch):
    from pinax.config import get_settings
    from pinax.index import SnapshotIndex

    slow = "SELECT count(*) FROM range(100000000000) r WHERE md5(r.range::VARCHAR) LIKE '%pinax%'"
    monkeypatch.setattr(jobs, "get_settings", lambda: get_settings().model_copy(update={"job_timeout": 1}))
    monkeypatch.setattr(SnapshotIndex, "files_to", lambda self, *a, **k: self._raw(slow, []))
    login(client, "ivy")
    job = _wait(client, client.post("/api/files/jobs", params={"fs": "fast"}).json()["id"])
    assert job["status"] == "stopped" and "PINAX_JOB_TIMEOUT" in job["message"]
    assert client.get(f"/api/files/jobs/{job['id']}/csv").status_code == 409


@pytest.mark.parametrize("sort", ["nonsense"])
def test_an_unknown_sort_is_refused(client, sort):
    login(client, "ivy")
    assert client.post("/api/files/jobs", params={"fs": "fast", "sort": sort}).status_code == 422
