"""Traffic speed and travel-time endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import MeasurementSite, TrafficMeasurement

router = APIRouter(prefix="/traffic", tags=["traffic"])


@router.get("/speed")
def get_speed(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            TrafficMeasurement.site_id,
            TrafficMeasurement.index,
            TrafficMeasurement.measured_at,
            TrafficMeasurement.value_type,
            TrafficMeasurement.flow_veh_h,
            TrafficMeasurement.speed_kmh,
            func.ST_AsGeoJSON(MeasurementSite.geom, 6).label("geom_json"),
        )
        .join(MeasurementSite, TrafficMeasurement.site_id == MeasurementSite.id)
        .where(func.ST_Intersects(MeasurementSite.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {
            "site_id": r.site_id,
            "index": r.index,
            "measured_at": r.measured_at.isoformat() if r.measured_at else None,
            "value_type": r.value_type,
            "flow_veh_h": float(r.flow_veh_h) if r.flow_veh_h is not None else None,
            "speed_kmh": float(r.speed_kmh) if r.speed_kmh is not None else None,
        }

    return geo_response(make_fc(rows, "geom_json", props))


@router.get("/traveltime")
def get_traveltime():
    raise HTTPException(501, "travel_time has no geometry — spatial query not supported")
