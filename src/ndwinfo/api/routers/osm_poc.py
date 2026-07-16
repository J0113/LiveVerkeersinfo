"""Bounded Overpass + live NDW speed proof-of-concept endpoint."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ndwinfo.api.deps import BBox, BBoxDep, DbDep
from ndwinfo.api.routers.traffic import get_speed
from ndwinfo.config import settings
from ndwinfo.osm_poc import (
    build_overpass_query,
    link_measurements_to_roads,
    parse_overpass_roads,
)

router = APIRouter(prefix="/poc/osm", tags=["OSM speed POC"])

_cache: OrderedDict[str, tuple[float, dict, dict]] = OrderedDict()
_cache_lock = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}
_overpass_slot = threading.BoundedSemaphore(1)
_GRID_DEGREES = 0.025
_MAX_CACHE_ENTRIES = 24


@router.get("/roads")
def get_osm_speed_roads(
    b: BBoxDep,
    db: DbDep,
    profile: Literal["major", "detailed"] = "major",
    include_speeds: bool = True,
    speed_limit: Annotated[int, Query(ge=1, le=1500)] = 750,
):
    """Draw directed OSM ways and associate current NDW speed sites.

    This is deliberately a small-area POC, not a public Overpass-backed
    production API.  Viewports are snapped to reusable grid bounds and cached;
    detailed roads require a much smaller requested area.
    """
    requested_area = (b.max_lon - b.min_lon) * (b.max_lat - b.min_lat)
    # Public Overpass is acceptable for this deliberately small POC corridor,
    # not for national/runtime ingestion.  Tight limits also keep the UI fast.
    area_limit = 0.015 if profile == "major" else 0.004
    if requested_area > area_limit:
        raise HTTPException(
            400,
            f"OSM POC {profile} bbox area {requested_area:.4f} deg² exceeds {area_limit}",
        )

    snapped = _snapped_bbox(b)
    key = f"{profile}:" + ",".join(f"{v:.3f}" for v in snapped)
    roads, source_meta, cache_hit = _get_cached_roads(key, snapped, profile)
    roads = copy.deepcopy(roads)

    measurement_bbox = BBox(*snapped)
    measurements = {"type": "FeatureCollection", "features": []}
    match_meta = {
        "measurement_count": 0,
        "matched_count": 0,
        "ambiguous_count": 0,
        "unmatched_count": 0,
        "roads_with_measurements": 0,
    }
    if include_speeds:
        speed_response = get_speed(b=measurement_bbox, db=db, limit=speed_limit)
        measurements = json.loads(speed_response.body)
        match_meta = link_measurements_to_roads(roads, measurements)

    metadata = {
        "poc": True,
        "profile": profile,
        "requested_bbox": [b.min_lon, b.min_lat, b.max_lon, b.max_lat],
        "data_bbox": list(snapped),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "osm_cache_hit": cache_hit,
        "osm_source": settings.osm_overpass_url,
        **source_meta,
        **roads.pop("metadata", {}),
        **match_meta,
    }
    return Response(
        content=json.dumps(
            {"roads": roads, "measurements": measurements, "metadata": metadata},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        media_type="application/json",
    )


def _snapped_bbox(b: BBox) -> tuple[float, float, float, float]:
    return (
        math.floor(b.min_lon / _GRID_DEGREES) * _GRID_DEGREES,
        math.floor(b.min_lat / _GRID_DEGREES) * _GRID_DEGREES,
        math.ceil(b.max_lon / _GRID_DEGREES) * _GRID_DEGREES,
        math.ceil(b.max_lat / _GRID_DEGREES) * _GRID_DEGREES,
    )


def _get_cached_roads(
    key: str, bbox: tuple[float, float, float, float], profile: str
) -> tuple[dict, dict, bool]:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] > now:
            _cache.move_to_end(key)
            return cached[1], cached[2], True
        key_lock = _key_locks.setdefault(key, threading.Lock())

    # The POC is diagnostic-only. Never let abandoned browser requests occupy
    # the whole FastAPI worker pool while synchronous Overpass calls continue.
    if not key_lock.acquire(blocking=False):
        raise HTTPException(429, "This OSM POC area is already being loaded")
    if not _overpass_slot.acquire(blocking=False):
        with _cache_lock:
            if _key_locks.get(key) is key_lock:
                _key_locks.pop(key, None)
        key_lock.release()
        raise HTTPException(429, "Another OSM POC request is already running")
    try:
        now = time.monotonic()
        with _cache_lock:
            cached = _cache.get(key)
            if cached and cached[0] > now:
                _cache.move_to_end(key)
                return cached[1], cached[2], True

        query = build_overpass_query(bbox, profile)
        started = time.monotonic()
        payload = None
        selected_url = None
        errors = []
        urls = [settings.osm_overpass_url, settings.osm_overpass_fallback_url]
        for url in dict.fromkeys(candidate for candidate in urls if candidate):
            try:
                response = httpx.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": "LiveVerkeersInfo-OSM-POC/0.1"},
                    timeout=httpx.Timeout(35.0, connect=10.0),
                    follow_redirects=True,
                )
                response.raise_for_status()
                payload = response.json()
                selected_url = url
                break
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{url}: {exc}")
        if payload is None:
            raise HTTPException(503, "OSM Overpass is unavailable: " + " | ".join(errors))

        roads = parse_overpass_roads(payload, max_features=settings.osm_poc_max_features)
        source_meta = {
            "osm_source": selected_url,
            "osm_fetched_at": datetime.now(timezone.utc).isoformat(),
            "osm_fetch_ms": round((time.monotonic() - started) * 1000),
            "osm_copyright": "© OpenStreetMap contributors, ODbL",
        }
        with _cache_lock:
            _cache[key] = (
                time.monotonic() + settings.osm_poc_cache_ttl_s,
                roads,
                source_meta,
            )
            _cache.move_to_end(key)
            while len(_cache) > _MAX_CACHE_ENTRIES:
                _cache.popitem(last=False)
        return roads, source_meta, False
    finally:
        _overpass_slot.release()
        with _cache_lock:
            if _key_locks.get(key) is key_lock:
                _key_locks.pop(key, None)
        key_lock.release()
