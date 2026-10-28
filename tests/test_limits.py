# SPDX-License-Identifier: Apache-2.0
"""Time limits: a request's queries stop when it runs out of time or the browser leaves, and file-level queries of
whole filesystems take turns."""

import asyncio
import threading
import time

import duckdb
import pytest
from conftest import ROOT, login
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from pinax import config, limits
from pinax.index import SnapshotIndex

# Minutes of work, unless something stops it.
SLOW = "SELECT count(*) FROM range(100000000000) r WHERE md5(r.range::VARCHAR) LIKE '%pinax%'"


@pytest.fixture
def setting(monkeypatch):
    def put(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        config.get_settings.cache_clear()
        monkeypatch.setattr(limits, "_slots", None)
    return put


def test_a_request_that_runs_too_long_is_stopped(client, setting, monkeypatch):
    setting("PINAX_QUERY_TIMEOUT", "1")
    monkeypatch.setattr(SnapshotIndex, "files", lambda self, *a, **k: self._rows(SLOW))
    login(client, "ivy")
    t0 = time.monotonic()
    r = client.get("/api/files", params={"fs": "fast", "q": "x"})
    assert r.status_code == 504
    assert r.json()["detail"].startswith("Pinax stopped this after 1 second.")
    assert time.monotonic() - t0 < 10
    # Others are not affected.
    assert client.get("/api/tree", params={"fs": "fast"}).status_code == 200


def test_queries_stop_when_the_browser_leaves(env):
    inner = FastAPI()
    con = duckdb.connect()
    seen = {}

    @inner.get("/api/slow")
    def slow():
        cur = con.cursor()
        with limits.guard(cur):
            cur.execute(SLOW).fetchall()

    @inner.exception_handler(limits.Stopped)
    async def stopped(_request, exc):
        seen["reason"] = exc.reason
        return JSONResponse({}, status_code=503)

    sent = []

    async def browser_leaves():
        messages = [{"type": "http.request", "body": b"", "more_body": False}]

        async def receive():
            if messages:
                return messages.pop(0)
            await asyncio.sleep(0.3)
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": "/api/slow", "raw_path": b"/api/slow", "query_string": b"", "root_path": "", "headers": [],
                 "server": ("test", 80), "client": ("test", 1)}
        await asyncio.wait_for(limits.QueryBudget(inner)(scope, receive, send), 10)

    t0 = time.monotonic()
    asyncio.run(browser_leaves())
    assert seen["reason"] == limits.GONE
    assert sent[0]["status"] == 503
    assert time.monotonic() - t0 < 5
    con.close()


def test_whole_filesystem_file_queries_take_turns(env, setting):
    setting("PINAX_WIDE_QUERIES", "1")
    held, release = threading.Event(), threading.Event()

    def hold():
        with limits.wide():
            held.set()
            release.wait(10)

    other = threading.Thread(target=hold)
    other.start()
    assert held.wait(5)
    budget = limits.Budget(0.5)
    token = limits._budget.set(budget)
    timer = threading.Timer(0.5, budget.stop, [limits.TIMEOUT])
    timer.start()
    try:
        with pytest.raises(limits.Stopped) as stopped, limits.wide():
            pass
        assert stopped.value.reason == limits.BUSY
    finally:
        limits._budget.reset(token)
        release.set()
        other.join()
    with limits.wide():  # the turn is free again
        pass


def test_only_whole_filesystem_file_queries_wait_for_a_turn(client, monkeypatch):
    turns = []
    real = limits.wide
    monkeypatch.setattr(limits, "wide", lambda: turns.append(1) or real())
    login(client, "ada")
    assert client.get("/api/files", params={"fs": "fast", "path": ROOT + "/alpha_lab", "q": "*.bam"}).status_code == 200
    assert turns == []
    login(client, "ivy")
    files = client.get("/api/files", params={"fs": "fast", "q": "*.bam"}).json()
    assert files["total"]["files"] == 1 and len(turns) == 2  # the files, and their total
