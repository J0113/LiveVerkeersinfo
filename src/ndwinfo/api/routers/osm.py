"""Viewport-bounded OSM driving-road geometry (served from PostGIS)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query, Response
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import substring, transform
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import make_fc
from ndwinfo.config import settings
from ndwinfo.models import (
    OsmLaneCenterline,
    OsmLaneConnection,
    OsmRoad,
)

router = APIRouter(prefix="/osm", tags=["osm"])
_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)


def _lane_tag_value(
    tags: dict,
    base: str,
    direction: str,
    lane_nr: int,
    lane_count: int,
):
    if direction == "fwd":
        keys = (f"{base}:forward", base)
    elif str(tags.get("oneway", "")).strip().lower() == "-1":
        keys = (f"{base}:backward", base)
    else:
        keys = (f"{base}:backward",)
    for key in keys:
        value = tags.get(key)
        if not isinstance(value, str):
            continue
        fields = value.split("|")
        if len(fields) == lane_count and 1 <= lane_nr <= len(fields):
            return fields[lane_nr - 1]
    return None


def _lane_turn_value(tags: dict, direction: str, lane_nr: int, lane_count: int):
    return _lane_tag_value(tags, "turn:lanes", direction, lane_nr, lane_count)


def _lane_markings(lane_nr: int, lane_count: int) -> dict:
    """Which longitudinal markings bound one lane, in travel order.

    Lane 1 is the leftmost lane of its own direction and `lane_count` the
    rightmost, so a lane's own numbers already say whether each side is the
    outside of the carriageway or a boundary with a same-direction neighbour.
    Callers get booleans rather than the numbers so the map layer never has to
    reason about the cross-section itself.
    """
    return {
        "edge_left": lane_nr == 1,
        "edge_right": lane_nr == lane_count,
        "divider_left": lane_nr > 1,
    }


def _connection_markings(
    from_lane_nr: int | None,
    from_lane_count: int | None,
    to_lane_nr: int | None,
    to_lane_count: int | None,
) -> dict:
    """The markings a connector inherits from the lanes it joins.

    A connector that keeps the same lane number is that lane continuing across a
    way boundary — carriageway, not junction interior — so its markings run on
    without a break. Anything that changes lane number (a split opening a new
    lane, a join closing one) is left unmarked: the boundary genuinely moves
    across it, and guessing a side would paint a line through the taper.

    The right-hand edge additionally needs the lane to be the outermost one at
    both ends, so a widening's outer lane doesn't carry a road edge into the
    stretch where the new lane beside it has already begun.
    """
    unmarked = {"edge_left": False, "edge_right": False, "divider_left": False}
    if from_lane_nr is None or to_lane_nr is None or from_lane_nr != to_lane_nr:
        return unmarked
    return {
        **_lane_markings(from_lane_nr, from_lane_count),
        "edge_right": from_lane_nr == from_lane_count and to_lane_nr == to_lane_count,
    }


def _trim_side(
    stored_direction: str,
    traversal_direction: str,
    *,
    outgoing: bool,
) -> str:
    """Map a travel-relative connection end to the stored line orientation."""
    stored_in_travel_order = (
        stored_direction != "both" or traversal_direction == "fwd"
    )
    if outgoing:
        return "end" if stored_in_travel_order else "start"
    return "start" if stored_in_travel_order else "end"


def _trim_lane_geometry(
    geometry: dict,
    *,
    start_trim_m: float = 0.0,
    end_trim_m: float = 0.0,
) -> dict:
    if not start_trim_m and not end_trim_m:
        return geometry
    line_rd = transform(_WGS84_TO_RD.transform, shape(geometry))
    start = min(start_trim_m, line_rd.length)
    end = max(start, line_rd.length - min(end_trim_m, line_rd.length))
    if end - start <= 0.05:
        return geometry
    visible_rd = substring(line_rd, start, end)
    return mapping(transform(_RD_TO_WGS84.transform, visible_rd))


def _highway_types_for_zoom(zoom: float) -> tuple[str, ...] | None:
    """Which highway classes to include at a given zoom, or None for all 8.

    Bounds detail so a province-wide viewport at low zoom doesn't request
    every secondary road alongside the motorway network (NH alone is >10x
    api_max_limit for all 8 classes). Returns () to mean "hidden".
    """
    if zoom < 7:
        return ()
    if zoom < 9:
        return ("motorway", "motorway_link")
    if zoom < 11:
        return (
            "motorway", "motorway_link",
            "trunk", "trunk_link",
            "primary", "primary_link",
        )
    return None  # all 8 classes


@router.get("/roads")
def get_osm_roads(
    b: BBoxDep,
    db: DbDep,
    zoom: Annotated[float, Query(ge=0, le=24)] = 12,
) -> Response:
    """Return OSM driving-road ways intersecting the current viewport."""
    highway_types = _highway_types_for_zoom(zoom)
    if highway_types == ():
        return _geo_response_with_metadata([], {"detail": "hidden", "truncated": False})

    cap = settings.osm_max_features
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    q = (
        select(OsmRoad, func.ST_AsGeoJSON(OsmRoad.geom, 6).label("geom_json"))
        .where(func.ST_Intersects(OsmRoad.geom, bbox_geom))
        .order_by(OsmRoad.osm_id)  # deterministic — an unordered LIMIT drops arbitrary rows
        .limit(cap + 1)
    )
    if highway_types is not None:
        q = q.where(OsmRoad.highway.in_(highway_types))

    rows = db.execute(q).all()
    truncated = len(rows) > cap
    rows = rows[:cap]

    def props(r):
        tags = r.OsmRoad.raw or {}
        return {**tags, "osm_id": r.OsmRoad.osm_id, "highway": r.OsmRoad.highway}

    fc = make_fc(rows, "geom_json", props)
    return _geo_response_with_metadata(fc["features"], {"truncated": truncated})


@router.get("/lane-lines")
def get_osm_lane_lines(b: BBoxDep, db: DbDep) -> Response:
    """Return independent thin lane centerlines and their directed connectors."""
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    lane_cap = settings.osm_lane_line_max_features
    connection_cap = settings.osm_lane_connection_max_features

    lane_query = (
        select(
            OsmLaneCenterline,
            OsmRoad.raw.label("osm_tags"),
            func.ST_AsGeoJSON(OsmLaneCenterline.geom, 7).label("geom_json"),
        )
        .join(OsmRoad, OsmRoad.osm_id == OsmLaneCenterline.road_id)
        .where(func.ST_Intersects(OsmLaneCenterline.geom, bbox_geom))
        .order_by(OsmLaneCenterline.id)
        .limit(lane_cap + 1)
    )
    # Both endpoints' cross-sections come along on the connection row itself:
    # they decide which markings the connector carries, and both joins are
    # indexed primary-key lookups rather than an extra round trip.
    from_lane = aliased(OsmLaneCenterline)
    to_lane = aliased(OsmLaneCenterline)
    connection_query = (
        select(
            OsmLaneConnection,
            from_lane.lane_nr.label("from_lane_nr"),
            from_lane.lane_count.label("from_lane_count"),
            to_lane.lane_nr.label("to_lane_nr"),
            to_lane.lane_count.label("to_lane_count"),
            func.ST_AsGeoJSON(OsmLaneConnection.geom, 7).label("geom_json"),
        )
        .join(from_lane, from_lane.id == OsmLaneConnection.from_lane_id)
        .join(to_lane, to_lane.id == OsmLaneConnection.to_lane_id)
        .where(func.ST_Intersects(OsmLaneConnection.geom, bbox_geom))
        .order_by(OsmLaneConnection.id)
        .limit(connection_cap + 1)
    )
    lane_rows = db.execute(lane_query).all()
    connection_rows = db.execute(connection_query).all()
    lanes_truncated = len(lane_rows) > lane_cap
    connections_truncated = len(connection_rows) > connection_cap
    lane_rows = lane_rows[:lane_cap]
    connection_rows = connection_rows[:connection_cap]
    lane_ids = [row.OsmLaneCenterline.id for row in lane_rows]
    trim_connections = (
        db.execute(
            select(OsmLaneConnection).where(
                or_(
                    OsmLaneConnection.from_lane_id.in_(lane_ids),
                    OsmLaneConnection.to_lane_id.in_(lane_ids),
                )
            )
        )
        .scalars()
        .all()
        if lane_ids
        else []
    )

    lane_fc = make_fc(
        lane_rows,
        "geom_json",
        lambda row: {
            "kind": "lane",
            "id": row.OsmLaneCenterline.id,
            "road_id": row.OsmLaneCenterline.road_id,
            "segment_id": row.OsmLaneCenterline.segment_id,
            "lane_nr": row.OsmLaneCenterline.lane_nr,
            "lane_count": row.OsmLaneCenterline.lane_count,
            "direction": row.OsmLaneCenterline.direction,
            "offset_m": float(row.OsmLaneCenterline.offset_m),
            "count_source": row.OsmLaneCenterline.count_source,
            "oneway_source": row.OsmLaneCenterline.oneway_source,
            **_lane_markings(
                row.OsmLaneCenterline.lane_nr, row.OsmLaneCenterline.lane_count
            ),
            "highway": (row.osm_tags or {}).get("highway"),
            "name": (row.osm_tags or {}).get("name"),
            "ref": (row.osm_tags or {}).get("ref"),
            "turn:lanes": (row.osm_tags or {}).get("turn:lanes"),
            "turn:lanes:forward": (row.osm_tags or {}).get("turn:lanes:forward"),
            "turn:lanes:backward": (row.osm_tags or {}).get("turn:lanes:backward"),
            "placement": (row.osm_tags or {}).get("placement"),
            "placement:forward": (row.osm_tags or {}).get("placement:forward"),
            "placement:backward": (row.osm_tags or {}).get("placement:backward"),
            "placement:start": (row.osm_tags or {}).get("placement:start"),
            "placement:end": (row.osm_tags or {}).get("placement:end"),
            "destination:lanes": (row.osm_tags or {}).get("destination:lanes"),
            "destination:ref:lanes": (row.osm_tags or {}).get(
                "destination:ref:lanes"
            ),
            "change:lanes": (row.osm_tags or {}).get("change:lanes"),
            "turn_lane": _lane_turn_value(
                row.osm_tags or {},
                row.OsmLaneCenterline.direction,
                row.OsmLaneCenterline.lane_nr,
                row.OsmLaneCenterline.lane_count,
            ),
            "destination_lane": _lane_tag_value(
                row.osm_tags or {},
                "destination:lanes",
                row.OsmLaneCenterline.direction,
                row.OsmLaneCenterline.lane_nr,
                row.OsmLaneCenterline.lane_count,
            ),
            "destination_ref_lane": _lane_tag_value(
                row.osm_tags or {},
                "destination:ref:lanes",
                row.OsmLaneCenterline.direction,
                row.OsmLaneCenterline.lane_nr,
                row.OsmLaneCenterline.lane_count,
            ),
            "change_lane": _lane_tag_value(
                row.osm_tags or {},
                "change:lanes",
                row.OsmLaneCenterline.direction,
                row.OsmLaneCenterline.lane_nr,
                row.OsmLaneCenterline.lane_count,
            ),
        },
    )
    lane_directions = {
        row.OsmLaneCenterline.id: row.OsmLaneCenterline.direction for row in lane_rows
    }
    trims: dict[str, dict[str, float]] = {}
    for connection in trim_connections:
        raw = connection.raw or {}
        for lane_id, traversal_direction, outgoing, raw_key in (
            (
                connection.from_lane_id,
                connection.from_direction,
                True,
                "from_trim_m",
            ),
            (
                connection.to_lane_id,
                connection.to_direction,
                False,
                "to_trim_m",
            ),
        ):
            trim_m = float(raw.get(raw_key) or 0.0)
            stored_direction = lane_directions.get(lane_id)
            if not trim_m or stored_direction is None:
                continue
            side = _trim_side(
                stored_direction, traversal_direction, outgoing=outgoing
            )
            lane_trim = trims.setdefault(lane_id, {"start": 0.0, "end": 0.0})
            lane_trim[side] = max(lane_trim[side], trim_m)
    for row, feature in zip(lane_rows, lane_fc["features"]):
        lane_trim = trims.get(row.OsmLaneCenterline.id)
        if lane_trim and feature["geometry"]:
            feature["geometry"] = _trim_lane_geometry(
                feature["geometry"],
                start_trim_m=lane_trim["start"],
                end_trim_m=lane_trim["end"],
            )
    connection_fc = make_fc(
        connection_rows,
        "geom_json",
        lambda row: {
            "kind": "connection",
            "id": row.OsmLaneConnection.id,
            "from": (
                f"{row.OsmLaneConnection.from_lane_id}"
                f"@{row.OsmLaneConnection.from_direction}"
            ),
            "to": (
                f"{row.OsmLaneConnection.to_lane_id}"
                f"@{row.OsmLaneConnection.to_direction}"
            ),
            "connection_type": row.OsmLaneConnection.connection_type,
            "confidence": row.OsmLaneConnection.confidence,
            **_connection_markings(
                row.from_lane_nr,
                row.from_lane_count,
                row.to_lane_nr,
                row.to_lane_count,
            ),
            **(row.OsmLaneConnection.raw or {}),
        },
    )
    metadata = {
        "truncated": lanes_truncated or connections_truncated,
        "truncated_by_kind": {
            "lanes": lanes_truncated,
            "connections": connections_truncated,
        },
    }
    return _geo_response_with_metadata(
        lane_fc["features"] + connection_fc["features"], metadata
    )


def _geo_response_with_metadata(features: list[dict], metadata: dict) -> Response:
    return Response(
        content=json.dumps(
            {"type": "FeatureCollection", "features": features, "metadata": metadata},
            separators=(",", ":"),
        ),
        media_type="application/geo+json",
    )
