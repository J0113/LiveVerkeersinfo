"""Viewport-bounded OSM driving-road geometry (served from PostGIS)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import make_fc
from ndwinfo.config import settings
from ndwinfo.models import OsmRoad, OsmRoadLane
from ndwinfo.osm_tags import osm_maxspeed_kmh

router = APIRouter(prefix="/osm", tags=["osm"])


def _highway_types_for_zoom(zoom: float) -> tuple[str, ...] | None:
    """Which highway classes to include at a given zoom, or None for all 8.

    Bounds detail so a province-wide viewport at low zoom doesn't request
    every secondary road alongside the motorway network (NH alone is >10x
    api_max_limit for all 8 classes). Returns () to mean "hidden".
    """
    if zoom < 7:
        return ()
    if zoom < 9:
        return ("motorway", "motorway_link")
    if zoom < 11:
        return (
            "motorway", "motorway_link",
            "trunk", "trunk_link",
            "primary", "primary_link",
        )
    return None  # all 8 classes


@router.get("/roads")
def get_osm_roads(
    b: BBoxDep,
    db: DbDep,
    zoom: Annotated[float, Query(ge=0, le=24)] = 12,
) -> Response:
    """Return OSM driving-road ways intersecting the current viewport."""
    highway_types = _highway_types_for_zoom(zoom)
    if highway_types == ():
        return _geo_response_with_metadata([], {"detail": "hidden", "truncated": False})

    cap = settings.osm_max_features
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    q = (
        select(OsmRoad, func.ST_AsGeoJSON(OsmRoad.geom, 6).label("geom_json"))
        .where(func.ST_Intersects(OsmRoad.geom, bbox_geom))
        .order_by(OsmRoad.osm_id)  # deterministic — an unordered LIMIT drops arbitrary rows
        .limit(cap + 1)
    )
    if highway_types is not None:
        q = q.where(OsmRoad.highway.in_(highway_types))

    rows = db.execute(q).all()
    truncated = len(rows) > cap
    rows = rows[:cap]

    def props(r):
        tags = r.OsmRoad.raw or {}
        return {**tags, "osm_id": r.OsmRoad.osm_id, "highway": r.OsmRoad.highway}

    fc = make_fc(rows, "geom_json", props)
    return _geo_response_with_metadata(fc["features"], {"truncated": truncated})


@router.get("/lanes")
def get_osm_lanes(b: BBoxDep, db: DbDep) -> Response:
    """Return per-lane offset geometry intersecting the current viewport.

    No zoom-based highway-class tiering (unlike /roads) -- this is already
    a detail-zoom-only layer gated client-side (minZoom), and lane rows are
    a small fraction of the way count.
    """
    cap = settings.osm_lane_max_features
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    q = (
        select(
            OsmRoadLane,
            OsmRoad.raw.label("osm_tags"),
            func.ST_AsGeoJSON(OsmRoadLane.geom, 6).label("geom_json"),
        )
        .join(OsmRoad, OsmRoad.osm_id == OsmRoadLane.source_id)
        .where(func.ST_Intersects(OsmRoadLane.geom, bbox_geom))
        .order_by(OsmRoadLane.id)
        .limit(cap + 1)
    )
    rows = db.execute(q).all()
    truncated = len(rows) > cap
    rows = rows[:cap]

    def props(r):
        tags = r.OsmRoadLane.raw or {}
        return {
            **tags,
            "source_id": r.OsmRoadLane.source_id,
            "lane": r.OsmRoadLane.lane,
            "lane_count": r.OsmRoadLane.lane_count,
            "direction": r.OsmRoadLane.direction,
            "role": r.OsmRoadLane.role,
            "highway": r.OsmRoadLane.highway,
            "name": r.OsmRoadLane.name,
            "ref": r.OsmRoadLane.ref,
            "width_m": float(r.OsmRoadLane.width_m) if r.OsmRoadLane.width_m is not None else None,
            "maxspeed_kmh": osm_maxspeed_kmh(r.osm_tags, r.OsmRoadLane.direction),
        }

    fc = make_fc(rows, "geom_json", props)
    return _geo_response_with_metadata(fc["features"], {"truncated": truncated})


def _geo_response_with_metadata(features: list[dict], metadata: dict) -> Response:
    import json

    return Response(
        content=json.dumps(
            {"type": "FeatureCollection", "features": features, "metadata": metadata},
            separators=(",", ":"),
        ),
        media_type="application/geo+json",
    )
