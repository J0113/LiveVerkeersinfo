import asyncio
import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from ndwinfo.api.routers import (
    charging,
    emission,
    feeds,
    nwb,
    osm,
    signs,
    situations,
    traffic,
    truckparking,
    verkeersborden,
    vild,
    weggeg,
)
from ndwinfo.config import settings
from ndwinfo.db import SessionLocal
from ndwinfo.models import SystemState

app = FastAPI(title="LiveVerkeersinfo", version="0.1.0")

_last_activity_write: float = 0.0
_ACTIVITY_WRITE_INTERVAL = 30.0


def _write_activity() -> None:
    with SessionLocal() as session:
        state = session.get(SystemState, 1)
        if state is None:
            state = SystemState(id=1)
            session.add(state)
        state.last_api_request_at = datetime.now(timezone.utc)
        session.commit()


@app.middleware("http")
async def track_api_activity(request: Request, call_next):
    global _last_activity_write
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        now = time.monotonic()
        if now - _last_activity_write > _ACTIVITY_WRITE_INTERVAL:
            _last_activity_write = now
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _write_activity)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

app.include_router(traffic.router, prefix="/api")
app.include_router(situations.router, prefix="/api")
app.include_router(signs.router, prefix="/api")
app.include_router(charging.router, prefix="/api")
app.include_router(truckparking.router, prefix="/api")
app.include_router(verkeersborden.router, prefix="/api")
app.include_router(emission.router, prefix="/api")
app.include_router(feeds.router, prefix="/api")
app.include_router(vild.router, prefix="/api")
app.include_router(nwb.router, prefix="/api")
app.include_router(osm.router, prefix="/api")
app.include_router(weggeg.router, prefix="/api")


@app.get("/api/config", tags=["configuration"])
def public_config():
    """Public, non-secret browser feature flags."""
    return {"nwbDiagnosticMode": settings.nwb_diagnostic_mode}


_WEB_DIR = Path("web")
# Local JS/CSS references in index.html get a content-hash query param so browsers
# refetch only when a file actually changes. Computed once at startup; a rebuild
# restarts the app and recomputes. Skips external URLs (any src/href with "://").
_ASSET_REF = re.compile(r'(src|href)="([^":?]+\.(?:js|css))(?:\?[^"]*)?"')


def _render_index() -> str:
    def bust(match: re.Match) -> str:
        attr, rel = match.group(1), match.group(2)
        try:
            digest = hashlib.md5((_WEB_DIR / rel).read_bytes()).hexdigest()[:10]
        except OSError:
            return f'{attr}="{rel}"'
        return f'{attr}="{rel}?v={digest}"'

    return _ASSET_REF.sub(bust, (_WEB_DIR / "index.html").read_text(encoding="utf-8"))


if _WEB_DIR.is_dir() and any(os.scandir(_WEB_DIR)):
    _INDEX_HTML = _render_index()

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML)

    # Explicit routes above win for "/"; the mount serves every other asset.
    app.mount("/", StaticFiles(directory="web", html=True), name="static")
