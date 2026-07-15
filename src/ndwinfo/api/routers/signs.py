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


def _dedupe_ghost_signs(rows):
    """Collapse duplicate MSI records that NDW publishes for one physical sign.

    NDW sometimes lists a decommissioned/replaced sign under an old UUID at
    (effectively) the same spot — parked on 'blank' and never tracking live
    changes — alongside the live UUID. They differ only by ~1m of km. Group by
    (road, carriageway, lane, km rounded to ~10m); when several land in one slot,
    keep the one whose state changed most recently (NULL ts_state = oldest).
    """
    best: dict[tuple, object] = {}
    for r in rows:
        km_bucket = round(float(r.km), 2) if r.km is not None else None
        key = (r.road, r.carriageway, r.lane, km_bucket)
        cur = best.get(key)
        if cur is None or _ts_newer(r.ts_state, cur.ts_state):
            best[key] = r
    return list(best.values())


def _ts_newer(a, b) -> bool:
    if a is None:
        return False
    if b is None:
        return True
    return a > b


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
            MsiSign.bearing,
            MsiState.ts_state,
            MsiState.aspect_type,
            MsiState.value,
            MsiState.flashing,
            MsiState.red_ring,
            MsiState.raw,
            func.ST_AsGeoJSON(MsiSign.geom, 6).label("geom_json"),
        )
        .outerjoin(MsiState, MsiSign.uuid == MsiState.uuid)
        .where(func.ST_Intersects(MsiSign.geom, bbox_geom))
        .limit(limit)
    ).all()

    rows = _dedupe_ghost_signs(rows)

    def props(r):
        return {
            "uuid": r.uuid,
            "road": r.road,
            "carriageway": r.carriageway,
            "lane": r.lane,
            "km": float(r.km) if r.km is not None else None,
            "bearing": float(r.bearing) if r.bearing is not None else None,
            "ts_state": r.ts_state.isoformat() if r.ts_state else None,
            "aspect_type": r.aspect_type,
            "value": r.value,
            "flashing": r.flashing,
            "red_ring": r.red_ring,
            # Full aspect list when the sign shows several at once (e.g.
            # lane_open + speedlimit); absent for single-aspect displays.
            "aspects": (r.raw or {}).get("aspects"),
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
            Drip.num_display_areas,
            Drip.display_text,
            Drip.message,
            func.ST_AsGeoJSON(Drip.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(Drip.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        msg = r.message or {}
        return {
            "controller_id": r.controller_id,
            "vms_index": r.vms_index,
            "description": r.description,
            "vms_type": r.vms_type,
            "physical_support": r.physical_support,
            "bearing": r.bearing,
            "num_display_areas": r.num_display_areas,
            "display_text": r.display_text,
            "working_status": msg.get("working_status"),
            "image_format": msg.get("image_format"),
            "image_b64": msg.get("image_data"),
            "updated_at": msg.get("status_update_time"),
        }

    return geo_response(make_fc(rows, "geom_json", props))
