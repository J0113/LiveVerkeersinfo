"""Situations endpoint (all 6 DATEX v3 situation categories)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import case, func, select

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
    category: Annotated[str | None, Query(description="incident|srti|roadworks|bridge_opening|closure|speed_limit")] = None,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(VALID_CATEGORIES)}")

    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    ranked = (
        select(
            Situation.record_id,
            Situation.feed_name,
            Situation.id,
            Situation.category,
            Situation.record_type,
            Situation.record_subtype,
            Situation.record_version,
            Situation.severity,
            Situation.probability,
            Situation.safety_related,
            Situation.source,
            Situation.valid_from,
            Situation.valid_to,
            Situation.speed_limit_kmh,
            Situation.carriageway,
            Situation.bearing,
            Situation.alert_c,
            Situation.locations,
            Situation.lane_impact,
            Situation.operator_action_status,
            Situation.record_status,
            Situation.validity_status,
            Situation.information_status,
            Situation.cause,
            Situation.version_time,
            Situation.geom.label("geom"),
            func.row_number().over(
                partition_by=Situation.record_id,
                order_by=(
                    Situation.version_time.desc().nullslast(),
                    Situation.record_version.desc().nullslast(),
                    case((Situation.feed_name == "actueel_beeld", 0), else_=1),
                    Situation.feed_name,
                ),
            ).label("provenance_rank"),
        )
        .cte("ranked_situations")
    )
    q = select(
        *(
            ranked.c[name]
            for name in (
                "record_id", "feed_name", "id", "category", "record_type",
                "record_subtype", "record_version", "severity", "probability",
                "safety_related", "source", "valid_from", "valid_to",
                "speed_limit_kmh", "carriageway", "bearing", "alert_c",
                "locations", "lane_impact", "operator_action_status",
                "record_status", "validity_status", "information_status",
                "cause", "version_time",
            )
        ),
        func.ST_AsGeoJSON(ranked.c.geom, 6).label("geom_json"),
    ).where(
        ranked.c.provenance_rank == 1,
        func.ST_Intersects(ranked.c.geom, bbox_geom),
    )
    if category:
        q = q.where(ranked.c.category == category)
    q = q.order_by(ranked.c.record_id).limit(limit)

    rows = db.execute(q).all()

    def props(r):
        return {
            "record_id": r.record_id,
            "feed_name": r.feed_name,
            "id": r.id,
            "category": r.category,
            "record_type": r.record_type,
            "record_subtype": r.record_subtype,
            "record_version": r.record_version,
            "severity": r.severity,
            "probability": r.probability,
            "safety_related": r.safety_related,
            "source": r.source,
            "valid_from": r.valid_from.isoformat() if r.valid_from else None,
            "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            "speed_limit_kmh": r.speed_limit_kmh,
            "carriageway": r.carriageway,
            "bearing": float(r.bearing) if r.bearing is not None else None,
            "alert_c": r.alert_c,
            "locations": r.locations,
            "lane_impact": r.lane_impact,
            "operator_action_status": r.operator_action_status,
            "record_status": r.record_status,
            "validity_status": r.validity_status,
            "information_status": r.information_status,
            "cause": r.cause,
            "version_time": r.version_time.isoformat() if r.version_time else None,
        }

    return geo_response(make_fc(rows, "geom_json", props))
