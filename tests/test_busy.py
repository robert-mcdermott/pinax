# SPDX-License-Identifier: Apache-2.0
"""Many requests at once (wave 14): a request holds a catalog connection only within one of its threads, so a pool
smaller than the threads never leaves them all waiting on each other; and when no connection is free in time, Pinax
says it is busy instead of failing."""

import asyncio

import sqlalchemy.exc
from sqlalchemy import select


def _signed_in(monkeypatch, username: str) -> str:
    """Local sign-in, and a session for `username` (its cookie's token), without checking a password."""
    from pinax import config
    from pinax.db import session_scope
    from pinax.models import User
    from pinax.signin import start_session

    monkeypatch.setenv("PINAX_AUTH_MODE", "local")
    config.get_settings.cache_clear()
    with session_scope() as s:
        return start_session(s, s.scalar(select(User).where(User.username == username)))


def test_a_small_pool_serves_many_requests_at_once(catalog, monkeypatch):
    """The stall wave 14's load test found: each request took a connection to see who was signed in and kept it
    while waiting for a thread to run the page, until every thread was waiting for a connection."""
    import anyio.to_thread
    import httpx

    from pinax import config, db
    from pinax.main import app
    from pinax.signin import COOKIE

    token = _signed_in(monkeypatch, "ada")
    monkeypatch.setenv("PINAX_DATABASE_POOL_SIZE", "1")  # PostgreSQL: 1, and 1 more at busy times
    monkeypatch.setenv("PINAX_WEB_THREADS", "2")  # SQLite: one for each of the web app's threads
    monkeypatch.setattr(db, "POOL_TIMEOUT", 5)
    config.get_settings.cache_clear()
    db.reset_engine()

    async def many():
        anyio.to_thread.current_default_thread_limiter().total_tokens = 6
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pinax",
                                     cookies={COOKIE: token}) as c:
            return await asyncio.gather(*(c.get("/api/home") for _ in range(40)))

    answers = asyncio.run(many())
    assert [r.status_code for r in answers] == [200] * 40, {r.text for r in answers if r.status_code != 200}


def test_no_free_connection_is_busy(client, monkeypatch):
    from conftest import login

    from pinax import config, db, metrics

    monkeypatch.setattr(db, "POOL_TIMEOUT", 0.5)
    config.get_settings.cache_clear()
    db.reset_engine()
    engine, held = db.get_engine(), []
    before = metrics.REGISTRY.get_sample_value("pinax_catalog_busy_total") or 0
    try:
        while True:  # every connection the pool has, in use
            try:
                held.append(engine.connect())
            except sqlalchemy.exc.TimeoutError:
                break
        r = login(client, "ada").get("/api/home")
    finally:
        for conn in held:
            conn.close()
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert r.json()["detail"].startswith("Pinax is busy: more people are using it at once than it can serve")
    assert metrics.REGISTRY.get_sample_value("pinax_catalog_busy_total") == before + 1
    assert login(client, "ada").get("/api/home").status_code == 200
