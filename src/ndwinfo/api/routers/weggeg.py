"""WEGGEG lane-reference geometry endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import WeggegLane

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
