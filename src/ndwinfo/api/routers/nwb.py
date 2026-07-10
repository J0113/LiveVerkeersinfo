"""Viewport-bounded Nationaal Wegenbestand road geometry."""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.routers.traffic import build_speed_feature_collection
from ndwinfo.config import settings
from ndwinfo.nwb import NwbFetchResult, TtlLruCache, cache_key, detail_profile, fetch_road_segments
from ndwinfo.weggeg import (
    LaneConfigurationFetchResult,
    attach_nwb_metadata,
    build_lane_speed_features,
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
    key = cache_key(bbox, profile)
    result = _cache.get(key)
    cache_status = "HIT"
    if result is None:
        cache_status = "MISS"
        try:
            timeout = httpx.Timeout(settings.nwb_request_timeout_s)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                result = await fetch_road_segments(client, settings.nwb_pdok_url, bbox, profile)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("NWB PDOK request failed: %s", exc)
            raise HTTPException(502, "NWB road geometry is temporarily unavailable") from exc
        _cache.set(key, result)

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
    lane_result = _lane_config_cache.get(static_key)
    lane_cache = "HIT"
    try:
        timeout = httpx.Timeout(settings.nwb_request_timeout_s)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            if lane_result is None:
                lane_cache = "MISS"
                lane_result = await fetch_lane_configurations(
                    client, settings.weggeg_pdok_url, bbox, settings.weggeg_max_features
                )
                _lane_config_cache.set(static_key, lane_result)

            profile = detail_profile(12, settings.nwb_max_features)
            assert profile is not None
            road_key = cache_key(bbox, profile)
            road_result = _cache.get(road_key)
            if road_result is None:
                road_result = await fetch_road_segments(
                    client, settings.nwb_pdok_url, bbox, profile
                )
                _cache.set(road_key, road_result)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("NWB/WEGGEG lane request failed: %s", exc)
        raise HTTPException(502, "Lane reference geometry is temporarily unavailable") from exc

    # Work on shallow copies because cached WEGGEG feature properties must not
    # acquire request-specific matching state.
    configurations = [
        {**feature, "properties": dict(feature["properties"])} for feature in lane_result.features
    ]
    attach_nwb_metadata(configurations, road_result.feature_collection["features"])
    observations = build_speed_feature_collection(b, db, settings.api_max_limit)["features"]
    features = build_lane_speed_features(
        configurations,
        observations,
        max_distance_m=settings.lane_match_max_distance_m,
        max_heading_difference=settings.lane_match_max_heading_difference,
        max_age_s=settings.lane_speed_max_age_s,
    )
    measured = sum(feature["properties"]["speed_kmh"] is not None for feature in features)
    metadata = {
        "detail": "lane-configuration",
        "truncated": lane_result.truncated or road_result.truncated,
        "invalidFeatures": lane_result.invalid_features + road_result.invalid_features,
        "measuredLanes": measured,
        "matchedObservations": sum(
            feature["properties"]["measurement_count"] for feature in features
        ),
        "geometryKind": "schematic-lane-offset",
        "laneNumbering": "lane 1 is nearest the median (far left in travel direction)",
    }
    return _lane_response(features, metadata, lane_cache)


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


def _lane_response(features: list[dict], metadata: dict, cache_status: str = "MISS") -> Response:
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
        },
    )
