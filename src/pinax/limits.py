# SPDX-License-Identifier: Apache-2.0
"""How long a request's queries may run, and stopping them when nobody is waiting.

Each GET under /api has a budget (`QueryBudget`, an ASGI middleware): its index queries are interrupted once the
request has run for PINAX_QUERY_TIMEOUT seconds, or as soon as the browser goes away (it moved to another page, or
the page was closed), so an abandoned search of a whole filesystem does not keep the cores busy. Queries find the
budget of the request they serve with `guard`; outside a request (the command line, ingest) nothing is limited.

File-level queries of a whole filesystem read every file in it: at most PINAX_WIDE_QUERIES run at once (`wide`), and
others wait for a turn within their own time limit.
"""

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

import duckdb

from . import logs
from .config import get_settings

TIMEOUT, BUSY, GONE = "timeout", "busy", "gone"


class Stopped(Exception):
    """A request's queries were stopped: it ran out of time, waited too long for a turn, or the browser left."""

    def __init__(self, reason: str, seconds: float):
        super().__init__(reason)
        self.reason = reason
        self.seconds = seconds


class Budget:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.reason: str | None = None
        self._running: set[duckdb.DuckDBPyConnection] = set()
        self._lock = threading.Lock()

    def stop(self, reason: str) -> None:
        with self._lock:
            self.reason = self.reason or reason
            running = list(self._running)
        for cur in running:
            cur.interrupt()

    def check(self) -> None:
        if self.reason is not None:
            raise Stopped(self.reason, self.seconds)

    @contextmanager
    def running(self, cur: duckdb.DuckDBPyConnection) -> Iterator[None]:
        with self._lock:
            self._running.add(cur)
        try:
            self.check()
            yield
        except duckdb.InterruptException:
            self.check()
            raise
        finally:
            with self._lock:
                self._running.discard(cur)


_budget: ContextVar[Budget | None] = ContextVar("pinax_budget", default=None)


@contextmanager
def guard(cur: duckdb.DuckDBPyConnection) -> Iterator[None]:
    """Run a query on `cur` within the current request's budget, if there is one, and count its time in the
    request's log line."""
    budget = _budget.get()
    started = time.perf_counter()
    try:
        with budget.running(cur) if budget is not None else nullcontext():
            yield
    finally:
        logs.count_query(time.perf_counter() - started)


_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


@contextmanager
def wide() -> Iterator[None]:
    """Take one of the PINAX_WIDE_QUERIES turns for a file-level query of a whole filesystem (0: no limit)."""
    global _slots
    limit = get_settings().wide_queries
    if limit <= 0:
        yield
        return
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(limit)
        slots = _slots
    budget = _budget.get()
    while not slots.acquire(timeout=0.2):
        if budget is not None and budget.reason is not None:
            raise Stopped(BUSY if budget.reason == TIMEOUT else budget.reason, budget.seconds)
    try:
        yield
    finally:
        slots.release()


class QueryBudget:
    """ASGI middleware: a budget for each GET under /api (see the module's docstring)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        first = await receive()
        if first["type"] != "http.request" or first.get("more_body"):  # gone already, or a GET with a body
            await self.app(scope, _replay(first, receive), send)
            return
        seconds = get_settings().query_timeout
        budget = Budget(seconds)
        timer = asyncio.get_running_loop().call_later(seconds, budget.stop, TIMEOUT) if seconds > 0 else None
        gone = asyncio.Event()

        async def listen():
            # A GET has no more body, so the next message is the browser going away (or the response being done).
            while (await receive())["type"] != "http.disconnect":
                pass
            budget.stop(GONE)
            gone.set()

        async def after_first():
            await gone.wait()
            return {"type": "http.disconnect"}

        listener = asyncio.create_task(listen())
        token = _budget.set(budget)
        try:
            await self.app(scope, _replay(first, after_first), send)
        finally:
            _budget.reset(token)
            listener.cancel()
            if timer is not None:
                timer.cancel()


def _replay(first: dict, then):
    """A `receive` that returns `first` once, then whatever `then` returns."""
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return first
        return await then()

    return receive
