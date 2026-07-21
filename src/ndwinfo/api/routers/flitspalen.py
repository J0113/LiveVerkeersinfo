"""Flitspalen.nl static speed camera endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import FlitspalenCamera, FlitspalenCameraRoute

router = APIRouter(prefix="/flitspalen", tags=["flitspalen"])


@router.get("")
def get_flitspalen_cameras(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            FlitspalenCamera.id,
            FlitspalenCamera.city,
            FlitspalenCamera.street,
            FlitspalenCamera.description,
            FlitspalenCamera.speed_limit_kmh,
            FlitspalenCamera.camera_type,
            FlitspalenCamera.rotatable,
            FlitspalenCamera.bearing_deg,
            func.ST_AsGeoJSON(FlitspalenCamera.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(FlitspalenCamera.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {
            "id": r.id,
            "city": r.city,
            "street": r.street,
            "description": r.description,
            "speed_limit_kmh": r.speed_limit_kmh,
            "camera_type": r.camera_type,
            "rotatable": r.rotatable,
            "bearing_deg": r.bearing_deg,
        }

    return geo_response(make_fc(rows, "geom_json", props))


@router.get("/pairs")
def get_flitspalen_pairs(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    """Trajectcontrole SC/SCE routes, precomputed at ingest (see ingest/flitspalen_route.py)."""
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            FlitspalenCameraRoute.sc_id,
            FlitspalenCameraRoute.sce_id,
            FlitspalenCameraRoute.street,
            func.ST_AsGeoJSON(FlitspalenCameraRoute.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(FlitspalenCameraRoute.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {"sc_id": r.sc_id, "sce_id": r.sce_id, "street": r.street}

    return geo_response(make_fc(rows, "geom_json", props))
