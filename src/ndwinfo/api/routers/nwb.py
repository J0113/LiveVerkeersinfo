"""Viewport-bounded Nationaal Wegenbestand road geometry."""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from ndwinfo.api.deps import BBoxDep
from ndwinfo.config import settings
from ndwinfo.nwb import NwbFetchResult, TtlLruCache, cache_key, detail_profile, fetch_road_segments

router = APIRouter(prefix="/nwb", tags=["nwb"])
logger = logging.getLogger(__name__)
_cache: TtlLruCache[NwbFetchResult] = TtlLruCache(
    ttl_s=settings.nwb_cache_ttl_s,
    max_entries=settings.nwb_cache_max_entries,
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
