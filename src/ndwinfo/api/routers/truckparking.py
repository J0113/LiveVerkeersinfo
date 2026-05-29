"""Truck parking endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import TruckParking, TruckParkingStatus

router = APIRouter(prefix="/truckparking", tags=["truckparking"])


@router.get("")
def get_truckparking(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            TruckParking.id,
            TruckParking.name,
            TruckParking.operator,
            TruckParking.capacity,
            TruckParkingStatus.origin_time,
            TruckParkingStatus.vacant,
            TruckParkingStatus.occupied,
            TruckParkingStatus.occupancy_pct,
            func.ST_AsGeoJSON(TruckParking.geom, 6).label("geom_json"),
        )
        .outerjoin(TruckParkingStatus, TruckParking.id == TruckParkingStatus.parking_id)
        .where(func.ST_Intersects(TruckParking.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {
            "id": r.id,
            "name": r.name,
            "operator": r.operator,
            "capacity": r.capacity,
            "origin_time": r.origin_time.isoformat() if r.origin_time else None,
            "vacant": r.vacant,
            "occupied": r.occupied,
            "occupancy_pct": float(r.occupancy_pct) if r.occupancy_pct is not None else None,
        }

    return geo_response(make_fc(rows, "geom_json", props))
