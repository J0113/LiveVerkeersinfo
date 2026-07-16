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
    include_image: Annotated[
        bool,
        Query(description="Include base64 display graphics for a bounded driver request"),
    ] = False,
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
            Drip.carriageway,
            Drip.num_display_areas,
            Drip.display_text,
            Drip.message,
            func.ST_AsGeoJSON(Drip.geom, 6).label("geom_json"),
        )
        .where(func.ST_Intersects(Drip.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return _drip_properties(r, include_image=include_image)

    return geo_response(make_fc(rows, "geom_json", props))


def _drip_properties(row, *, include_image: bool = False) -> dict:
    """Build a compact DRIP response; graphics are explicit and opt-in."""
    message = row.message or {}
    image_data = message.get("image_data")
    properties = {
        "controller_id": row.controller_id,
        "vms_index": row.vms_index,
        "description": row.description,
        "vms_type": row.vms_type,
        "physical_support": row.physical_support,
        "bearing": row.bearing,
        "carriageway": row.carriageway,
        "num_display_areas": row.num_display_areas,
        "display_text": row.display_text,
        "working_status": message.get("working_status"),
        "has_image": bool(image_data),
        "updated_at": message.get("status_update_time"),
    }
    if include_image and image_data:
        properties["image_format"] = message.get("image_format")
        properties["image_b64"] = image_data
    return properties
