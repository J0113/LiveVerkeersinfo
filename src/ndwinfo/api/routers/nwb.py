"""Viewport-bounded Nationaal Wegenbestand road geometry (served from PostGIS)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import make_fc
from ndwinfo.config import settings
from ndwinfo.models import NwbRoadSegment

router = APIRouter(prefix="/nwb", tags=["nwb"])

_COLUMNS = (
    NwbRoadSegment.wvk_id,
    NwbRoadSegment.begin_junction_id,
    NwbRoadSegment.end_junction_id,
    NwbRoadSegment.road_number,
    NwbRoadSegment.street_name,
    NwbRoadSegment.road_manager_type,
    NwbRoadSegment.road_manager_name,
    NwbRoadSegment.direction,
    NwbRoadSegment.administrative_direction,
    NwbRoadSegment.carriageway_position,
    NwbRoadSegment.position_to_orientation_line,
    NwbRoadSegment.carriageway_type,
    NwbRoadSegment.frc,
    NwbRoadSegment.form_of_way,
    NwbRoadSegment.openlr,
    NwbRoadSegment.begin_km,
    NwbRoadSegment.end_km,
    NwbRoadSegment.length_m,
    NwbRoadSegment.valid_from,
    NwbRoadSegment.road_class,
)


@router.get("/roads")
def get_nwb_roads(
    b: BBoxDep,
    db: DbDep,
    zoom: Annotated[float, Query(ge=0, le=24)] = 12,
) -> Response:
    """Return normalized NWB road sections intersecting the current viewport.

    Zoom bounds detail so a country-wide viewport at low zoom doesn't render
    (or transfer) the full ~1.6M-segment national network to the browser.
    """
    if zoom < 9:
        return geo_response_with_metadata([], {"detail": "hidden", "truncated": False})

    if zoom < 11:
        manager_types, cap, detail = ("R",), min(settings.nwb_max_features, 2500), "national"
    elif zoom < 12:
        manager_types, cap, detail = ("R", "P"), min(settings.nwb_max_features, 4000), "major"
    else:
        manager_types, cap, detail = None, settings.nwb_max_features, "detailed"

    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    q = (
        select(*_COLUMNS, func.ST_AsGeoJSON(NwbRoadSegment.geom, 6).label("geom_json"))
        .where(func.ST_Intersects(NwbRoadSegment.geom, bbox_geom))
        .limit(cap + 1)
    )
    if manager_types is not None:
        q = q.where(NwbRoadSegment.road_manager_type.in_(manager_types))

    rows = db.execute(q).all()
    truncated = len(rows) > cap
    rows = rows[:cap]

    def props(r):
        return {
            "segment_id": str(r.wvk_id),
            "nwb_road_section_id": r.wvk_id,
            "begin_junction_id": r.begin_junction_id,
            "end_junction_id": r.end_junction_id,
            "road_number": r.road_number,
            "street_name": r.street_name,
            "road_manager_type": r.road_manager_type,
            "road_manager_name": r.road_manager_name,
            "direction": r.direction,
            "administrative_direction": r.administrative_direction,
            "carriageway_position": r.carriageway_position,
            "position_to_orientation_line": r.position_to_orientation_line,
            "carriageway_type": r.carriageway_type,
            "frc": r.frc,
            "form_of_way": r.form_of_way,
            "openlr": r.openlr,
            "begin_km": float(r.begin_km) if r.begin_km is not None else None,
            "end_km": float(r.end_km) if r.end_km is not None else None,
            "length_m": float(r.length_m) if r.length_m is not None else None,
            "valid_from": r.valid_from.isoformat() if r.valid_from else None,
            "road_class": r.road_class,
            # Reserved for a future live-traffic join keyed by segment_id.
            "traffic_state": None,
        }

    fc = make_fc(rows, "geom_json", props)
    return geo_response_with_metadata(fc["features"], {"detail": detail, "truncated": truncated})


def geo_response_with_metadata(features: list[dict], metadata: dict) -> Response:
    import json

    return Response(
        content=json.dumps(
            {"type": "FeatureCollection", "features": features, "metadata": metadata},
            separators=(",", ":"),
        ),
        media_type="application/geo+json",
        headers={"X-NWB-Truncated": str(metadata.get("truncated", False)).lower()},
    )
