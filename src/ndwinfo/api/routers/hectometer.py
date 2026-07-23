"""Hectometer sign endpoint — every-100m markers, both carriageways."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import HectometerPoint

router = APIRouter(prefix="/hectometers", tags=["hectometers"])


def _props(r) -> dict:
    return {"road": r.road, "carriageway": r.carriageway, "km": float(r.km) if r.km is not None else None}


@router.get("")
def get_hectometer_points(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            HectometerPoint.road,
            HectometerPoint.carriageway,
            HectometerPoint.km,
            func.ST_AsGeoJSON(HectometerPoint.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(HectometerPoint.geom, bbox_geom))
        .limit(limit)
    ).all()
    return geo_response(make_fc(rows, "geom_json", _props))
