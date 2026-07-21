"""ANWB incidents endpoint (jams / roadworks / dynamic radars)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import AnwbIncident

router = APIRouter(prefix="/anwb", tags=["anwb"])

VALID_CATEGORIES = {"jams", "roadworks", "radars"}


@router.get("")
def get_anwb_incidents(
    b: BBoxDep,
    db: DbDep,
    category: Annotated[str | None, Query(description="jams|roadworks|radars")] = None,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(VALID_CATEGORIES)}")

    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    q = (
        select(
            AnwbIncident.record_id,
            AnwbIncident.id,
            AnwbIncident.category,
            AnwbIncident.incident_type,
            AnwbIncident.road,
            AnwbIncident.from_label,
            AnwbIncident.to_label,
            AnwbIncident.reason,
            AnwbIncident.distance_m,
            AnwbIncident.delay_s,
            AnwbIncident.hm,
            AnwbIncident.valid_from,
            func.ST_AsGeoJSON(AnwbIncident.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(AnwbIncident.geom, bbox_geom))
        .limit(limit)
    )
    if category:
        q = q.where(AnwbIncident.category == category)

    rows = db.execute(q).all()

    def props(r):
        return {
            "record_id": r.record_id,
            "id": r.id,
            "category": r.category,
            "incident_type": r.incident_type,
            "road": r.road,
            "from_label": r.from_label,
            "to_label": r.to_label,
            "reason": r.reason,
            "distance_m": r.distance_m,
            "delay_s": r.delay_s,
            "hm": float(r.hm) if r.hm is not None else None,
            "valid_from": r.valid_from.isoformat() if r.valid_from else None,
        }

    return geo_response(make_fc(rows, "geom_json", props))
