from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ndwinfo.api.routers import (
    charging,
    emission,
    feeds,
    signs,
    situations,
    traffic,
    truckparking,
    verkeersborden,
)

app = FastAPI(title="LiveVerkeersinfo", version="0.1.0")

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

import os
if os.path.isdir("web") and any(os.scandir("web")):
    app.mount("/", StaticFiles(directory="web", html=True), name="static")
