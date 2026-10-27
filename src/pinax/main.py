# SPDX-License-Identifier: Apache-2.0
"""FastAPI application: the JSON API under /api and the static interface."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router as api_router
from .db import init_db
from .snapshots import indexes

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("pinax")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield
    indexes.clear()


app = FastAPI(title="Pinax", version=__version__, lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.include_router(api_router)


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
