# SPDX-License-Identifier: Apache-2.0
"""System and audit logs (O5).

`configure` sets up Pinax's logs for every command, `pinax serve` included: text for the journal, or JSON lines for
a log collector (PINAX_LOG_FORMAT), to standard error or to PINAX_LOG_FILE (reopened when `logrotate` moves it).

- **Requests.** `RequestLog`, the outermost middleware, gives each request an ID (a trusted proxy's `X-Request-ID`,
  or a new one), returned to the browser as `X-Request-ID`, and writes one line for each request to the API: the
  method, path (never the query string, which can name files), route, status, time taken, and the time its index
  queries took, with the person and their address. A request slower than PINAX_SLOW_REQUEST_SECONDS is a warning.
  Every line written while a request runs carries its ID, person, and address.
- **The person's address**, behind a proxy in PINAX_TRUSTED_PROXIES, is the nearest untrusted address in
  `X-Forwarded-For`; otherwise the address the request came from.
- **Audit.** Every audit event (models.AuditEvent: who changed what, every sign-in) is also logged by `pinax.audit`
  once its change is committed, and only then: one rolled back never happened. PINAX_AUDIT_LOG writes them to a file
  of their own as well, as JSON lines, for a site's security monitoring. From the command line, the line names the
  account that ran it.
- **Never logged:** passwords, session cookies or tokens, request bodies, or query strings.
"""

import getpass
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.orm import Session

from . import metrics

log = logging.getLogger("pinax.request")
audit_log = logging.getLogger("pinax.audit")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_OURS = "_pinax_handler"  # marks the handlers configure() added, so a second call replaces only those


@dataclass
class RequestInfo:
    id: str
    address: str
    user: str = ""
    queries: int = 0
    query_seconds: float = 0.0


current: ContextVar[RequestInfo | None] = ContextVar("pinax_request", default=None)


def set_user(username: str) -> None:
    """The person a request was found to be (auth.py)."""
    if (info := current.get()) is not None:
        info.user = username


def count_query(seconds: float) -> None:
    """An index query of the current request took this long (limits.guard)."""
    if (info := current.get()) is not None:
        info.queries += 1
        info.query_seconds += seconds
        metrics.QUERIES.inc()
        metrics.QUERY_SECONDS.inc(seconds)


def _trusted(addr: str, trusted: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for net in trusted:
        try:
            if ip in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue  # auth.from_trusted_proxy reports a bad entry
    return False


def client_address(scope: dict, trusted: list[str]) -> str:
    """The person's address: behind trusted proxies, the nearest address in X-Forwarded-For that is not one of them."""
    peer = scope["client"][0] if scope.get("client") else ""
    if peer and not _trusted(peer, trusted):
        return peer
    forwarded = [v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-forwarded-for"]
    hops = [h.strip() for h in ",".join(forwarded).split(",") if h.strip()]
    for hop in reversed(hops):
        if not _trusted(hop, trusted):
            return hop[:64]
    return (hops[0] if hops else peer)[:64]


def route_of(scope: dict) -> str:
    """The template of the route a request matched, such as `/api/views/{view_id}` (so figures per route stay few):
    `static` for the interface's files, `unmatched` for an address under /api that is no route."""
    path, route = scope.get("path", ""), scope.get("route")
    template = getattr(route, "path_format", "") if route is not None else ""
    if not template or not hasattr(route, "endpoint"):  # a mount, such as the static files
        return "unmatched" if path.startswith("/api/") else "static"
    try:  # an included router's route knows only its own part of the path
        concrete = template.format(**scope.get("path_params", {}))
    except (KeyError, IndexError, ValueError):
        return template
    return path[: len(path) - len(concrete)] + template if concrete and path.endswith(concrete) else template


class RequestLog:
    """ASGI middleware: a request ID, the person's address, and one line per request to the API."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from .config import get_settings

        settings = get_settings()
        headers = dict(scope.get("headers", []))
        given = headers.get(b"x-request-id", b"").decode("latin-1")
        peer = scope["client"][0] if scope.get("client") else ""
        rid = given if given and REQUEST_ID.match(given) and (not peer or _trusted(peer, settings.trusted_proxies)) \
            else uuid.uuid4().hex[:16]
        info = RequestInfo(id=rid, address=client_address(scope, settings.trusted_proxies))
        token = current.set(info)
        metrics.IN_PROGRESS.inc()
        started = time.perf_counter()
        status, size = 500, 0

        async def sending(message):
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", rid.encode())]
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, sending)
        finally:
            current.reset(token)
            metrics.IN_PROGRESS.dec()
            seconds = time.perf_counter() - started
            path = scope.get("path", "")
            route = route_of(scope)
            metrics.request_done(scope.get("method", ""), route, status, seconds)
            slow = settings.slow_request_seconds > 0 and seconds >= settings.slow_request_seconds
            if path == "/api/health" or path == "/metrics" or not path.startswith("/api/"):
                level = logging.DEBUG  # pages, scripts, styles, and the load balancer's and Prometheus's checks
            elif slow or status >= 500:
                level = logging.WARNING
            else:
                level = logging.INFO if settings.log_requests else logging.DEBUG
            if log.isEnabledFor(level):
                queries = f", {info.queries} index quer{'y' if info.queries == 1 else 'ies'} {info.query_seconds * 1000:.0f} ms" \
                    if info.queries else ""
                log.log(level, "%s %s %d in %.0f ms%s%s", scope.get("method", ""), path, status, seconds * 1000, queries,
                        " (slow)" if slow else "",
                        extra={"request_id": rid, "user": info.user, "address": info.address,
                               "http": {"method": scope.get("method", ""), "path": path, "route": route, "status": status,
                                        "ms": round(seconds * 1000, 1), "bytes": size, "queries": info.queries,
                                        "query_ms": round(info.query_seconds * 1000, 1), "slow": slow}})


class _Context(logging.Filter):
    """Adds the current request's ID, person, and address to every record written while it runs."""

    def filter(self, record: logging.LogRecord) -> bool:
        info = current.get()
        for name in ("request_id", "user", "address"):
            if not hasattr(record, name):
                setattr(record, name, getattr(info, "id" if name == "request_id" else name, "") if info else "")
        return True


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        parts = [f"{k}={v}" for k, v in (("user", getattr(record, "user", "")), ("address", getattr(record, "address", "")),
                                         ("request", getattr(record, "request_id", "")),
                                         ("account", getattr(record, "os_user", ""))) if v]
        return f"{line} [{' '.join(parts)}]" if parts else line


class JsonFormatter(logging.Formatter):
    """One JSON object per line: the time (UTC), level, logger, message, the request's context, and any structured
    fields (`http`, `audit`)."""

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for name in ("request_id", "user", "address", "source", "os_user"):
            if value := getattr(record, name, ""):
                out[name] = value
        for name in ("http", "audit", "pinax"):
            if (value := getattr(record, name, None)) is not None:
                out[name] = value
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class LogError(Exception):
    """A log file Pinax cannot write."""


def _handler(path) -> logging.Handler:
    if not path:
        return logging.StreamHandler()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return logging.handlers.WatchedFileHandler(path, encoding="utf-8")
    except OSError as e:
        raise LogError(f"Pinax cannot write its log file {path}: {e.strerror}") from e


def configure(settings) -> None:
    """Pinax's logs, as the settings say (see the module's docstring). Called again, it replaces what it added."""
    root = logging.getLogger()
    for h in [h for h in root.handlers + audit_log.handlers if getattr(h, _OURS, False)]:
        (root if h in root.handlers else audit_log).removeHandler(h)
        h.close()
    handler = _handler(settings.log_file)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    handler.addFilter(_Context())
    setattr(handler, _OURS, True)
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    if settings.audit_log:
        audit = _handler(settings.audit_log)
        audit.setFormatter(JsonFormatter())
        audit.addFilter(_Context())
        setattr(audit, _OURS, True)
        audit_log.addHandler(audit)
    audit_log.setLevel(logging.INFO)
    logging.getLogger("alembic").setLevel(logging.WARNING)  # Pinax says what it applied; Alembic's steps are noise
    logging.getLogger("uvicorn.access").disabled = True  # RequestLog writes a better line for each request


# ---------- Audit events, once committed ----------

def _os_user() -> str:
    try:
        return os.environ.get("SUDO_USER") or getpass.getuser()
    except Exception:  # noqa: BLE001 - no account name to be had
        return ""


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _context) -> None:
    from .models import AuditEvent

    for obj in session.new:
        if isinstance(obj, AuditEvent):
            session.info.setdefault("pinax_audit", []).append(
                {"id": obj.id, "ts": obj.ts, "action": obj.action, "summary": obj.summary, "actor_id": obj.actor_id,
                 "detail": obj.detail})


@event.listens_for(Session, "after_commit")
def _write(session: Session) -> None:
    info = current.get()
    for e in session.info.pop("pinax_audit", []):
        extra = {"audit": {"id": e["id"], "action": e["action"], "actor_id": e["actor_id"], "detail": e["detail"],
                           "at": e["ts"].isoformat() + "Z" if e["ts"] else None}}
        if info is not None:  # the request's context, for any handler (not only Pinax's own)
            extra |= {"request_id": info.id, "user": info.user, "address": info.address}
        else:
            extra |= {"source": "cli", "os_user": _os_user()}
        audit_log.info("%s: %s", e["action"], e["summary"], extra=extra)
        metrics.AUDIT.labels(e["action"]).inc()


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop("pinax_audit", None)
