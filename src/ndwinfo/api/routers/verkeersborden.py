"""Traffic signs (verkeersborden) endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import TrafficSign

router = APIRouter(prefix="/verkeersborden", tags=["verkeersborden"])


@router.get("")
def get_verkeersborden(
    b: BBoxDep,
    db: DbDep,
    rvv_code: Annotated[
        str | None, Query(alias="rvvCode", description="Filter by RVV code")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    q = (
        select(
            TrafficSign.id,
            TrafficSign.rvv_code,
            TrafficSign.status,
            TrafficSign.placement,
            TrafficSign.side,
            TrafficSign.bearing,
            TrafficSign.road_name,
            TrafficSign.image_url,
            TrafficSign.text_signs,
            func.ST_AsGeoJSON(TrafficSign.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(TrafficSign.geom, bbox_geom))
        .limit(limit)
    )
    if rvv_code:
        q = q.where(TrafficSign.rvv_code == rvv_code)

    rows = db.execute(q).all()

    def props(r):
        return {
            "id": r.id,
            "rvv_code": r.rvv_code,
            "status": r.status,
            "placement": r.placement,
            "side": r.side,
            "bearing": r.bearing,
            "road_name": r.road_name,
            "image_url": r.image_url,
            "text_signs": r.text_signs,
        }

    return geo_response(make_fc(rows, "geom_json", props))
