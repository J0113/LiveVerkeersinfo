import asyncio
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from ndwinfo.api.routers import (
    charging,
    emission,
    feeds,
    signs,
    situations,
    traffic,
    truckparking,
    verkeersborden,
    vild,
)
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

app.include_router(traffic.router, prefix="/api")
app.include_router(situations.router, prefix="/api")
app.include_router(signs.router, prefix="/api")
app.include_router(charging.router, prefix="/api")
app.include_router(truckparking.router, prefix="/api")
app.include_router(verkeersborden.router, prefix="/api")
app.include_router(emission.router, prefix="/api")
app.include_router(feeds.router, prefix="/api")
app.include_router(vild.router, prefix="/api")


# Clean URL for the driving HUD. StaticFiles(html=True) maps "/drive/" to a
# directory, not drive.html, so serve it explicitly (route wins over the mount).
@app.get("/drive", include_in_schema=False)
def drive_page():
    return FileResponse("web/drive.html")


if os.path.isdir("web") and any(os.scandir("web")):
    app.mount("/", StaticFiles(directory="web", html=True), name="static")
