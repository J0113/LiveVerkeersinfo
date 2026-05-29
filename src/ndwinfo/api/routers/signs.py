"""Matrix signs (MSI) and DRIPs endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import Drip, MsiSign, MsiState

router = APIRouter(prefix="/signs", tags=["signs"])


@router.get("/matrix")
def get_matrix_signs(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            MsiSign.uuid,
            MsiSign.road,
            MsiSign.carriageway,
            MsiSign.lane,
            MsiSign.km,
            MsiState.ts_state,
            MsiState.aspect_type,
            MsiState.value,
            MsiState.flashing,
            MsiState.red_ring,
            func.ST_AsGeoJSON(MsiSign.geom, 6).label("geom_json"),
        )
        .outerjoin(MsiState, MsiSign.uuid == MsiState.uuid)
        .where(func.ST_Intersects(MsiSign.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {
            "uuid": r.uuid,
            "road": r.road,
            "carriageway": r.carriageway,
            "lane": r.lane,
            "km": float(r.km) if r.km is not None else None,
            "ts_state": r.ts_state.isoformat() if r.ts_state else None,
            "aspect_type": r.aspect_type,
            "value": r.value,
            "flashing": r.flashing,
            "red_ring": r.red_ring,
        }

    return geo_response(make_fc(rows, "geom_json", props))


@router.get("/drips")
def get_drips(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            Drip.controller_id,
            Drip.vms_index,
            Drip.description,
            Drip.vms_type,
            Drip.physical_support,
            Drip.bearing,
            Drip.message,
            func.ST_AsGeoJSON(Drip.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(Drip.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {
            "controller_id": r.controller_id,
            "vms_index": r.vms_index,
            "description": r.description,
            "vms_type": r.vms_type,
            "physical_support": r.physical_support,
            "bearing": r.bearing,
            "message": r.message,
        }

    return geo_response(make_fc(rows, "geom_json", props))
