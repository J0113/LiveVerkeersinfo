"""WEGGEG lane-reference geometry endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import WeggegLane, WeggegRoadAttribute

router = APIRouter(prefix="/weggeg", tags=["weggeg"])


@router.get("/lanes")
def get_lanes(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_max_limit,
):
    """Return separate WEGGEG-derived lane centerlines in the requested viewport."""
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            WeggegLane.id,
            WeggegLane.source_id,
            WeggegLane.lane,
            WeggegLane.lane_count,
            WeggegLane.road_number,
            WeggegLane.direction,
            WeggegLane.carriageway_side,
            WeggegLane.raw,
            func.ST_AsGeoJSON(WeggegLane.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(WeggegLane.geom, bbox_geom))
        .order_by(WeggegLane.road_number, WeggegLane.source_id, WeggegLane.lane)
        .limit(limit)
    ).all()

    def properties(row) -> dict:
        return {
            "id": row.id,
            "source_id": row.source_id,
            "lane": row.lane,
            "lane_count": row.lane_count,
            "road_number": row.road_number,
            "direction": row.direction,
            "carriageway_side": row.carriageway_side,
            **{key: value for key, value in (row.raw or {}).items() if value is not None},
        }

    return geo_response(make_fc(rows, "geom_json", properties))


@router.get("/attributes")
def get_attributes(
    b: BBoxDep,
    db: DbDep,
    feature_type: Annotated[
        str | None,
        Query(description="carriageway|convergence|divergence|maximum_speed"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_max_limit,
):
    """Return typed WEGGEG evidence for diagnostics/offline canonical builds."""
    allowed = {"carriageway", "convergence", "divergence", "maximum_speed"}
    if feature_type is not None and feature_type not in allowed:
        from fastapi import HTTPException

        raise HTTPException(400, f"feature_type must be one of {sorted(allowed)}")
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    query = select(
        WeggegRoadAttribute,
        func.ST_AsGeoJSON(WeggegRoadAttribute.geom, 6).label("geom_json"),
    ).where(func.ST_Intersects(WeggegRoadAttribute.geom, bbox_geom))
    if feature_type:
        query = query.where(WeggegRoadAttribute.feature_type == feature_type)
    rows = db.execute(query.limit(limit)).all()

    def properties(row) -> dict:
        item = row.WeggegRoadAttribute
        return {
            "id": item.id,
            "feature_type": item.feature_type,
            "source_id": item.source_id,
            "description": item.description,
            "subtype": item.subtype,
            "road_number": item.road_number,
            "direction": item.direction,
            "carriageway_side": item.carriageway_side,
            "begin_wdl": item.begin_wdl,
            "begin_km": float(item.begin_km) if item.begin_km is not None else None,
            "end_wdl": item.end_wdl,
            "end_km": float(item.end_km) if item.end_km is not None else None,
            "point_km": float(item.point_km) if item.point_km is not None else None,
            "maxspeed_kmh": item.maxspeed_kmh,
            "begin_time": float(item.begin_time) if item.begin_time is not None else None,
            "end_time": float(item.end_time) if item.end_time is not None else None,
        }

    return geo_response(make_fc(rows, "geom_json", properties))
