"""Viewport-bounded Nationaal Wegenbestand road geometry."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import OrderedDict
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool

from ndwinfo.api.deps import BBox, BBoxDep, DbDep
from ndwinfo.api.routers.traffic import build_speed_feature_collection
from ndwinfo.config import settings
from ndwinfo.nwb import NwbFetchResult, TtlLruCache, cache_key, detail_profile, fetch_road_segments
from ndwinfo.weggeg import (
    LaneConfigurationFetchResult,
    attach_nwb_metadata,
    build_lane_speed_features,
    features_intersecting_bbox,
    fetch_lane_configurations,
)

router = APIRouter(prefix="/nwb", tags=["nwb"])
logger = logging.getLogger(__name__)
_cache: TtlLruCache[NwbFetchResult] = TtlLruCache(
    ttl_s=settings.nwb_cache_ttl_s,
    max_entries=settings.nwb_cache_max_entries,
)
_lane_config_cache: TtlLruCache[LaneConfigurationFetchResult] = TtlLruCache(
    ttl_s=settings.weggeg_cache_ttl_s,
    max_entries=settings.weggeg_cache_max_entries,
)
_lane_response_cache: TtlLruCache[dict] = TtlLruCache(
    ttl_s=settings.lane_response_cache_ttl_s,
    max_entries=32,
)
_fetch_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
_MAX_FETCH_LOCKS = 256


def _fetch_lock(key: str) -> asyncio.Lock:
    """Return a bounded per-query lock to coalesce concurrent PDOK misses."""
    lock = _fetch_locks.get(key)
    if lock is not None:
        _fetch_locks.move_to_end(key)
        return lock
    if len(_fetch_locks) >= _MAX_FETCH_LOCKS:
        for old_key, old_lock in list(_fetch_locks.items()):
            if not old_lock.locked():
                del _fetch_locks[old_key]
                break
    lock = asyncio.Lock()
    _fetch_locks[key] = lock
    return lock


async def _cached_roads(
    client: httpx.AsyncClient,
    bbox: tuple[float, ...],
    profile,
) -> tuple[NwbFetchResult, str]:
    key = cache_key(bbox, profile)
    result = _cache.get(key)
    if result is not None:
        return result, "HIT"
    async with _fetch_lock(f"nwb:{key}"):
        # Another concurrent /roads or /lane-speeds request may have filled it.
        result = _cache.get(key)
        if result is not None:
            return result, "HIT"
        result = await fetch_road_segments(client, settings.nwb_pdok_url, bbox, profile)
        _cache.set(key, result)
        return result, "MISS"


async def _cached_lane_configurations(
    client: httpx.AsyncClient,
    bbox: tuple[float, ...],
    key: str,
) -> tuple[LaneConfigurationFetchResult, str]:
    result = _lane_config_cache.get(key)
    if result is not None:
        return result, "HIT"
    async with _fetch_lock(f"weggeg:{key}"):
        result = _lane_config_cache.get(key)
        if result is not None:
            return result, "HIT"
        result = await fetch_lane_configurations(
            client, settings.weggeg_pdok_url, bbox, settings.weggeg_max_features
        )
        _lane_config_cache.set(key, result)
        return result, "MISS"


@router.get("/roads")
async def get_nwb_roads(
    b: BBoxDep,
    zoom: Annotated[float, Query(ge=0, le=24)] = 12,
) -> Response:
    """Return normalized NWB road sections intersecting the current viewport."""

    profile = detail_profile(zoom, settings.nwb_max_features)
    if profile is None:
        return _response(
            {
                "type": "FeatureCollection",
                "features": [],
                "metadata": {"detail": "hidden", "truncated": False, "invalidFeatures": 0},
            }
        )

    # Query and cache on the same ~1 m grid so nearby cache-key collisions can
    # never return geometry for subtly different upstream bounds.
    bbox = tuple(round(value, 5) for value in (b.min_lon, b.min_lat, b.max_lon, b.max_lat))
    try:
        timeout = httpx.Timeout(settings.nwb_request_timeout_s)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            result, cache_status = await _cached_roads(client, bbox, profile)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("NWB PDOK request failed: %s", exc)
        raise HTTPException(502, "NWB road geometry is temporarily unavailable") from exc

    return _response(result.feature_collection, cache_status, result.truncated)


@router.get("/lane-speeds")
async def get_lane_speeds(
    b: BBoxDep,
    db: DbDep,
    zoom: Annotated[float, Query(ge=0, le=24)] = 13,
) -> Response:
    """Return official lane configurations enriched with conservatively matched NDW speeds."""
    if zoom < settings.lane_speed_min_zoom:
        return _lane_response([], {"detail": "hidden", "minimumZoom": settings.lane_speed_min_zoom})

    bbox = tuple(round(value, 5) for value in (b.min_lon, b.min_lat, b.max_lon, b.max_lat))
    static_key = ",".join(f"{value:.5f}" for value in bbox)
    cached_response = _lane_response_cache.get(static_key)
    if cached_response is not None:
        return _lane_response(
            cached_response["features"], cached_response["metadata"], "HIT", "HIT"
        )
    context_b = _expanded_lane_bbox(b, settings.lane_speed_context_radius_km)
    context_bbox = tuple(
        round(value, 5)
        for value in (context_b.min_lon, context_b.min_lat, context_b.max_lon, context_b.max_lat)
    )
    context_key = ",".join(f"{value:.5f}" for value in context_bbox)
    try:
        timeout = httpx.Timeout(settings.nwb_request_timeout_s)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            lane_result, lane_cache = await _cached_lane_configurations(
                client, context_bbox, context_key
            )

            profile = detail_profile(12, settings.nwb_max_features)
            assert profile is not None
            road_result, _ = await _cached_roads(client, context_bbox, profile)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("NWB/WEGGEG lane request failed: %s", exc)
        raise HTTPException(502, "Lane reference geometry is temporarily unavailable") from exc

    # Work on shallow copies because cached WEGGEG feature properties must not
    # acquire request-specific matching state.
    configurations = [
        {**feature, "properties": dict(feature["properties"])} for feature in lane_result.features
    ]
    attach_nwb_metadata(configurations, road_result.feature_collection["features"])
    speed_collection = await run_in_threadpool(
        build_speed_feature_collection, context_b, db, settings.api_max_limit
    )
    features = await run_in_threadpool(
        build_lane_speed_features,
        configurations,
        speed_collection["features"],
        max_distance_m=settings.lane_match_max_distance_m,
        max_heading_difference=settings.lane_match_max_heading_difference,
        max_age_s=settings.lane_speed_max_age_s,
        max_interpolation_span_km=settings.lane_speed_max_interpolation_span_km,
        max_extrapolation_distance_km=settings.lane_speed_max_extrapolation_distance_km,
    )
    features = await run_in_threadpool(features_intersecting_bbox, features, bbox)
    measured = sum(
        feature["properties"]["speed_kmh"] is not None
        and not feature["properties"]["speed_estimated"]
        for feature in features
    )
    estimated = sum(feature["properties"]["speed_estimated"] for feature in features)
    coloured = measured + estimated
    metadata = {
        "detail": "lane-configuration",
        "truncated": lane_result.truncated or road_result.truncated,
        "invalidFeatures": lane_result.invalid_features + road_result.invalid_features,
        "measuredLanes": measured,
        "estimatedLanes": estimated,
        "colouredLanes": coloured,
        "totalLanes": len(features),
        "coveragePct": round((coloured / len(features)) * 100) if features else 0,
        "matchingContextRadiusKm": settings.lane_speed_context_radius_km,
        "matchedObservations": sum(
            feature["properties"]["measurement_count"] for feature in features
        ),
        "geometryKind": "schematic-lane-offset",
        "laneNumbering": "lane 1 is nearest the median (far left in travel direction)",
    }
    _lane_response_cache.set(static_key, {"features": features, "metadata": metadata})
    return _lane_response(features, metadata, lane_cache, "MISS")


def _expanded_lane_bbox(b: BBox, radius_km: float) -> BBox:
    """Add route context for nearby anchors without expanding the response."""
    latitude = (b.min_lat + b.max_lat) / 2
    lat_delta = max(radius_km, 0) / 110.574
    lon_delta = max(radius_km, 0) / (111.320 * max(math.cos(math.radians(latitude)), 0.01))
    return BBox(
        max(-180.0, b.min_lon - lon_delta),
        max(-90.0, b.min_lat - lat_delta),
        min(180.0, b.max_lon + lon_delta),
        min(90.0, b.max_lat + lat_delta),
    )


def _response(data: dict, cache_status: str = "MISS", truncated: bool = False) -> Response:
    import json

    return Response(
        content=json.dumps(data, separators=(",", ":")),
        media_type="application/geo+json",
        headers={
            "Cache-Control": f"public, max-age={min(settings.nwb_cache_ttl_s, 3600)}",
            "X-NWB-Cache": cache_status,
            "X-NWB-Truncated": str(truncated).lower(),
        },
    )


def _lane_response(
    features: list[dict],
    metadata: dict,
    cache_status: str = "MISS",
    response_cache_status: str = "MISS",
) -> Response:
    import json

    return Response(
        content=json.dumps(
            {"type": "FeatureCollection", "features": features, "metadata": metadata},
            separators=(",", ":"),
        ),
        media_type="application/geo+json",
        headers={
            # Static reference data is cached server-side; live measurements are not browser-cached.
            "Cache-Control": "no-store",
            "X-WEGGEG-Cache": cache_status,
            "X-Lane-Response-Cache": response_cache_status,
        },
    )
