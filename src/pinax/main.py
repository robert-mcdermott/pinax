# SPDX-License-Identifier: Apache-2.0
"""FastAPI application: the JSON API under /api and the static interface."""

import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, jobs, metrics, migrate
from .api import router as api_router
from .auth import ScriptPrefix
from .config import get_settings, unsafe
from .db import get_engine, init_db
from .limits import BUSY, GONE, QueryBudget, Stopped
from .logs import RequestLog
from .oidc import SignInState
from .oidc import router as oidc_router
from .snapshots import indexes

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("pinax")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if problem := unsafe(settings):
        raise RuntimeError(f"Refusing to start: {problem}")
    init_db()
    jobs.recover()
    log.info("Pinax %s started: environment %s, sign-in %s, catalog %s, data in %s", __version__, settings.environment,
             settings.auth_mode, migrate.describe(get_engine()), settings.var_root)
    yield
    indexes.clear()
    log.info("Pinax stopped")


app = FastAPI(title="Pinax", version=__version__, lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.include_router(api_router)
app.include_router(oidc_router)  # /auth/..., sign-in with an identity provider (oidc mode only)


@app.middleware("http")
async def same_origin(request: Request, call_next):
    """With local sign-in or an identity provider, Pinax's own session cookie is what authorizes a request, so a
    change must come from a page of Pinax: a browser names the page's origin on every request that changes
    something."""
    if (request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/")
            and get_settings().auth_mode in ("local", "oidc")):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Refused: the request did not come from a page of Pinax"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        # Revalidate static assets with ETags so interface updates show up immediately (no build step).
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.exception_handler(Stopped)
async def stopped(request: Request, exc: Stopped):
    metrics.STOPPED.labels(exc.reason).inc()
    if exc.reason == GONE:
        log.info("Stopped %s: the browser left", request.url.path)
        return JSONResponse({"detail": "The browser left before the answer was ready"}, status_code=503)
    # The query string is left out: searches name files, and names can be sensitive.
    log.warning("Stopped %s: %s after %g s", request.url.path, exc.reason, exc.seconds)
    seconds = f"{exc.seconds:g} second{'' if exc.seconds == 1 else 's'}"
    if exc.reason == BUSY:
        detail = (f"Pinax is already running as many searches of whole filesystems as it allows at once, and none "
                  f"finished within {seconds}. Try again shortly, or look within a folder.")
        return JSONResponse({"detail": detail}, status_code=503)
    detail = (f"Pinax stopped this after {seconds}. Searching or comparing a whole filesystem reads every file in "
              "it: look within a folder, or narrow the search by name, type, size, or age.")
    return JSONResponse({"detail": detail}, status_code=504)


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse({"detail": "Internal error"}, status_code=500)


# Each GET's time limit covers everything below it (limits.py); a sign-in's state on /auth/ (oidc.py); scripts'
# /script/api/... is /api/... (auth.py); outermost of all, each request's ID and log line.
app.add_middleware(QueryBudget)
app.add_middleware(SignInState)
app.add_middleware(ScriptPrefix)
app.add_middleware(RequestLog)


@app.get("/app", include_in_schema=False)
def app_redirect():
    return RedirectResponse("/app/")


@app.get("/metrics", include_in_schema=False)
def prometheus(request: Request):
    """Prometheus metrics (metrics.py): off (404) until PINAX_METRICS_TOKEN is set, then only with that token."""
    token = get_settings().metrics_token
    if token is None or not token.get_secret_value():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    given = request.headers.get("authorization", "").encode()
    if not hmac.compare_digest(given, f"Bearer {token.get_secret_value()}".encode()):
        return JSONResponse({"detail": "Send PINAX_METRICS_TOKEN as a bearer token"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    return Response(metrics.render(), media_type=metrics.CONTENT_TYPE)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "img" / "favicon.svg", media_type="image/svg+xml")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
