"""EV charging endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import exists, func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import ChargeAvailability, ChargePoint

router = APIRouter(prefix="/charging", tags=["charging"])


@router.get("")
def get_charging(
    b: BBoxDep,
    db: DbDep,
    available: Annotated[
        bool, Query(description="Only return points with available connectors")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)

    q = (
        select(
            ChargePoint.id,
            ChargePoint.cpo_id,
            ChargePoint.address,
            ChargePoint.city,
            ChargePoint.operator_name,
            ChargePoint.owner_name,
            ChargePoint.open,
            ChargePoint.last_updated,
            func.ST_AsGeoJSON(ChargePoint.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(ChargePoint.geom, bbox_geom))
        .limit(limit)
    )

    if available:
        has_available = exists().where(
            ChargeAvailability.cp_id == ChargePoint.id,
            ChargeAvailability.available > 0,
        )
        q = q.where(has_available)

    cp_rows = db.execute(q).all()
    cp_ids = [r.id for r in cp_rows]

    # Fetch availability for all matching charge points in one query
    avail_by_cp: dict[str, list[dict]] = {}
    if cp_ids:
        avail_rows = db.execute(
            select(
                ChargeAvailability.cp_id,
                ChargeAvailability.idx,
                ChargeAvailability.total,
                ChargeAvailability.available,
                ChargeAvailability.power_max,
                ChargeAvailability.power_type,
                ChargeAvailability.connector_type,
                ChargeAvailability.connector_format,
            ).where(ChargeAvailability.cp_id.in_(cp_ids))
        ).all()
        for a in avail_rows:
            avail_by_cp.setdefault(a.cp_id, []).append(
                {
                    "idx": a.idx,
                    "total": a.total,
                    "available": a.available,
                    "power_max": float(a.power_max) if a.power_max is not None else None,
                    "power_type": a.power_type,
                    "connector_type": a.connector_type,
                    "connector_format": a.connector_format,
                }
            )

    def props(r):
        return {
            "id": r.id,
            "cpo_id": r.cpo_id,
            "address": r.address,
            "city": r.city,
            "operator_name": r.operator_name,
            "owner_name": r.owner_name,
            "open": r.open,
            "last_updated": r.last_updated.isoformat() if r.last_updated else None,
            "availability": avail_by_cp.get(r.id, []),
        }

    return geo_response(make_fc(cp_rows, "geom_json", props))
