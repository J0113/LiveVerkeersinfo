"""Matrix signs (MSI) and DRIPs endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import and_, case, func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import (
    Drip,
    MsiSign,
    MsiState,
    OsmRoad,
    RoadPointAssignment,
    RoadPointLink,
)
from ndwinfo.road_matching.points import matrix_gantry_id, matrix_gantry_key
from ndwinfo.road_matching.types import MatrixSign

router = APIRouter(prefix="/signs", tags=["signs"])


def _matrix_gantry_id(row) -> str:
    return matrix_gantry_id(
        matrix_gantry_key(
            MatrixSign(
                uuid=row.uuid,
                road=row.road,
                carriageway=row.carriageway,
                lane=row.lane,
                km=float(row.km) if row.km is not None else None,
                bearing=float(row.bearing) if row.bearing is not None else None,
            )
        )
    )


@router.get("/matrix")
def get_matrix_signs(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
    geometry: Annotated[
        Literal["source", "matched", "best"],
        Query(description="source point, matched point, or matched point with source fallback"),
    ] = "source",
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    # Rank ghost UUIDs before applying the API limit.  The old query limited
    # first, so a dense viewport could spend its entire cap on replaced/old
    # records and hide live lanes from the same gantry.
    ghost_rank = func.row_number().over(
        partition_by=(
            MsiSign.road,
            MsiSign.carriageway,
            MsiSign.lane,
            func.round(MsiSign.km, 2),
        ),
        order_by=(MsiState.ts_state.desc().nulls_last(), MsiSign.uuid),
    ).label("ghost_rank")
    ranked = (
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
            MsiSign.geom,
            ghost_rank,
        )
        .outerjoin(MsiState, MsiSign.uuid == MsiState.uuid)
        .where(func.ST_Intersects(MsiSign.geom, bbox_geom))
        .subquery("ranked_matrix_signs")
    )
    source_geom = func.ST_AsGeoJSON(ranked.c.geom, 6)
    matched_geom = func.ST_AsGeoJSON(RoadPointLink.matched_geom, 6)
    if geometry == "source":
        geometry_expr = source_geom
    elif geometry == "matched":
        geometry_expr = matched_geom
    else:
        geometry_expr = case(
            (
                and_(
                    RoadPointAssignment.status == "matched",
                    RoadPointLink.matched_geom.isnot(None),
                ),
                matched_geom,
            ),
            else_=source_geom,
        )
    rows = db.execute(
        select(
            ranked.c.uuid,
            ranked.c.road,
            ranked.c.carriageway,
            ranked.c.lane,
            ranked.c.km,
            ranked.c.bearing,
            ranked.c.ts_state,
            ranked.c.aspect_type,
            ranked.c.value,
            ranked.c.flashing,
            ranked.c.red_ring,
            ranked.c.raw,
            geometry_expr.label("geom_json"),
            RoadPointAssignment.status.label("match_status"),
            RoadPointAssignment.confidence.label("match_confidence"),
            RoadPointAssignment.method.label("match_method"),
            RoadPointAssignment.failure_reason.label("match_failure_reason"),
            RoadPointAssignment.candidate_count.label("match_candidate_count"),
            RoadPointLink.road_id.label("matched_road_id"),
            OsmRoad.ref.label("matched_road_ref"),
            OsmRoad.name.label("matched_road_name"),
            OsmRoad.highway.label("matched_road_highway"),
            RoadPointLink.segment_id.label("matched_segment_id"),
            RoadPointLink.direction.label("matched_direction"),
            RoadPointLink.anchor_lane_id.label("matched_anchor_lane_id"),
            RoadPointLink.applies_to_lane_id.label("matched_applies_to_lane_id"),
            RoadPointLink.position_fraction.label("matched_position_fraction"),
            RoadPointLink.source_distance_m.label("match_source_distance_m"),
            RoadPointLink.bearing_error_deg.label("match_bearing_error_deg"),
            RoadPointLink.road_ref_quality.label("matched_road_ref_quality"),
        )
        .select_from(ranked)
        .outerjoin(
            RoadPointAssignment,
            and_(
                RoadPointAssignment.source_kind == "matrix",
                RoadPointAssignment.source_key == ranked.c.uuid,
            ),
        )
        .outerjoin(
            RoadPointLink,
            and_(
                RoadPointLink.source_kind == "matrix",
                RoadPointLink.source_key == ranked.c.uuid,
                RoadPointLink.link_index == 0,
            ),
        )
        .outerjoin(OsmRoad, OsmRoad.osm_id == RoadPointLink.road_id)
        .where(ranked.c.ghost_rank == 1)
        # Deterministic, but deliberately not road-ordered: ordering by road
        # would make a truncated viewport always drop the same high-numbered
        # roads instead of thinning evenly across it.
        .order_by(ranked.c.uuid)
        .limit(limit)
    ).all()

    def props(r):
        return {
            "uuid": r.uuid,
            "gantry_id": _matrix_gantry_id(r),
            "gantry_lane": r.lane,
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
            "geometry_mode": geometry,
            "match_status": r.match_status,
            "match_confidence": r.match_confidence,
            "match_method": r.match_method,
            "match_failure_reason": r.match_failure_reason,
            "match_candidate_count": r.match_candidate_count,
            "matched_road_id": r.matched_road_id,
            "matched_road_ref": r.matched_road_ref,
            "matched_road_name": r.matched_road_name,
            "matched_road_highway": r.matched_road_highway,
            "matched_segment_id": r.matched_segment_id,
            "matched_direction": r.matched_direction,
            "matched_anchor_lane_id": r.matched_anchor_lane_id,
            "matched_applies_to_lane_id": r.matched_applies_to_lane_id,
            "matched_position_fraction": (
                float(r.matched_position_fraction)
                if r.matched_position_fraction is not None
                else None
            ),
            "match_source_distance_m": (
                float(r.match_source_distance_m)
                if r.match_source_distance_m is not None
                else None
            ),
            "match_bearing_error_deg": (
                float(r.match_bearing_error_deg)
                if r.match_bearing_error_deg is not None
                else None
            ),
            "matched_road_ref_quality": r.matched_road_ref_quality,
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
