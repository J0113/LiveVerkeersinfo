"""Emission zones endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import EmissionZone

router = APIRouter(prefix="/emission-zones", tags=["emission-zones"])


@router.get("")
def get_emission_zones(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            EmissionZone.id,
            EmissionZone.name,
            EmissionZone.zone_type,
            EmissionZone.status,
            EmissionZone.authority,
            EmissionZone.info_url,
            func.ST_AsGeoJSON(EmissionZone.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(EmissionZone.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {
            "id": r.id,
            "name": r.name,
            "zone_type": r.zone_type,
            "status": r.status,
            "authority": r.authority,
            "info_url": r.info_url,
        }

    return geo_response(make_fc(rows, "geom_json", props))
