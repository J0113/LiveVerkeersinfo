"""VILD reference geometry endpoints (points, lines, areas)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import VildArea, VildLine, VildPoint

router = APIRouter(prefix="/vild", tags=["vild"])


def _raw_props(r) -> dict:
    raw = r.raw or {}
    return {"id": r.id, **{k: v for k, v in raw.items() if v is not None}}


@router.get("/points")
def get_vild_points(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(VildPoint.id, VildPoint.raw, func.ST_AsGeoJSON(VildPoint.geom, 6).label("geom_json"))
        .where(func.ST_Intersects(VildPoint.geom, bbox_geom))
        .limit(limit)
    ).all()
    return geo_response(make_fc(rows, "geom_json", _raw_props))


@router.get("/lines")
def get_vild_lines(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(VildLine.id, VildLine.raw, func.ST_AsGeoJSON(VildLine.geom, 6).label("geom_json"))
        .where(func.ST_Intersects(VildLine.geom, bbox_geom))
        .limit(limit)
    ).all()
    return geo_response(make_fc(rows, "geom_json", _raw_props))


@router.get("/areas")
def get_vild_areas(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(VildArea.id, VildArea.raw, func.ST_AsGeoJSON(VildArea.geom, 6).label("geom_json"))
        .where(func.ST_Intersects(VildArea.geom, bbox_geom))
        .limit(limit)
    ).all()
    return geo_response(make_fc(rows, "geom_json", _raw_props))
