"""Situations endpoint (all 6 DATEX v3 situation categories)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import Situation

router = APIRouter(prefix="/situations", tags=["situations"])

VALID_CATEGORIES = {"incident", "srti", "roadworks", "bridge_opening", "closure", "speed_limit"}


@router.get("")
def get_situations(
    b: BBoxDep,
    db: DbDep,
    category: Annotated[
        str | None, Query(description="incident|srti|roadworks|bridge_opening|closure|speed_limit")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(VALID_CATEGORIES)}")

    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    q = (
        select(
            Situation.record_id,
            Situation.id,
            Situation.category,
            Situation.record_type,
            Situation.severity,
            Situation.probability,
            Situation.safety_related,
            Situation.source,
            Situation.valid_from,
            Situation.valid_to,
            Situation.speed_limit_kmh,
            func.ST_AsGeoJSON(Situation.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(Situation.geom, bbox_geom))
        .limit(limit)
    )
    if category:
        q = q.where(Situation.category == category)

    rows = db.execute(q).all()

    def props(r):
        return {
            "record_id": r.record_id,
            "id": r.id,
            "category": r.category,
            "record_type": r.record_type,
            "severity": r.severity,
            "probability": r.probability,
            "safety_related": r.safety_related,
            "source": r.source,
            "valid_from": r.valid_from.isoformat() if r.valid_from else None,
            "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            "speed_limit_kmh": r.speed_limit_kmh,
        }

    return geo_response(make_fc(rows, "geom_json", props))
