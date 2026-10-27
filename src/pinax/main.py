# SPDX-License-Identifier: Apache-2.0
"""FastAPI application: the JSON API under /api and the static interface."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router as api_router
from .config import get_settings, unsafe
from .db import init_db
from .snapshots import indexes

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("pinax")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if problem := unsafe(get_settings()):
        raise RuntimeError(f"Refusing to start: {problem}")
    init_db()
    yield
    indexes.clear()


app = FastAPI(title="Pinax", version=__version__, lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.include_router(api_router)


@app.middleware("http")
async def same_origin(request: Request, call_next):
    """With local sign-in, Pinax's own session cookie is what authorizes a request, so a change must come from a
    page of Pinax: a browser names the page's origin on every request that changes something."""
    if (request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/")
            and get_settings().auth_mode == "local"):
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


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse({"detail": "Internal error"}, status_code=500)


@app.get("/app", include_in_schema=False)
def app_redirect():
    return RedirectResponse("/app/")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "img" / "favicon.svg", media_type="image/svg+xml")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
