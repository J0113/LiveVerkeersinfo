"""Compact, fail-closed OSM lane schemas for directed road segments.

OSM ``*:lanes`` values are ordered left-to-right in the applicable direction.
The schema keeps that order and uses array index + 1 as the lane number, so it
does not duplicate a lane identifier in every attribute.  A field is only
resolved when its pipe array has exactly the directed lane count.  Raw OSM tags
remain stored on the segment for provenance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LANE_ATTRIBUTE_TAGS = {
    "turn": "turn:lanes",
    "change": "change:lanes",
    "destination": "destination:lanes",
    "maxspeed": "maxspeed:lanes",
}

_MAIN_MOTORWAY_CLASSES = {"motorway", "trunk"}
_MAX_LANE_COUNT = 32
_THROUGH_MOVEMENTS = {"through"}
_BRANCH_MOVEMENTS = {
    "left",
    "slight_left",
    "sharp_left",
    "right",
    "slight_right",
    "sharp_right",
}


def build_lane_schema(
    tags: Mapping[str, str],
    direction: str,
    *,
    lane_count: int | None,
    oneway: str,
    highway: str | None = None,
) -> dict[str, Any] | None:
    """Build a direction-specific schema without inventing lane attributes.

    Unsuffixed lane arrays are safe on one-way roads.  On a bidirectional way
    they are ignored unless a direction suffix is present.  Missing and
    unequal arrays are listed as unknown and never padded or truncated.
    """
    supplied_lane_count = lane_count
    lane_count = (
        lane_count
        if type(lane_count) is int and 1 <= lane_count <= _MAX_LANE_COUNT
        else None
    )
    suffix = "backward" if direction in {"backward", "reverse"} else "forward"
    selected = {
        field: _directional_lane_value(tags, osm_key, suffix, oneway)
        for field, osm_key in LANE_ATTRIBUTE_TAGS.items()
    }
    selected["access"] = next(
        (
            value
            for key in ("motor_vehicle:lanes", "vehicle:lanes", "access:lanes")
            if (value := _directional_lane_value(tags, key, suffix, oneway)) is not None
        ),
        None,
    )
    if lane_count is None and supplied_lane_count is None and not any(selected.values()):
        return None

    attributes: dict[str, list[str | None]] = {}
    unknown: list[str] = []
    for field, value in selected.items():
        values = _pipe_values(value) if value is not None else None
        if lane_count is None or values is None or len(values) != lane_count:
            unknown.append(field)
            continue
        attributes[field] = values

    schema: dict[str, Any] = {
        "version": 1,
        "lane_count": lane_count,
        "lane_order": "left_to_right",
        "attributes": attributes,
        "unknown": unknown,
    }
    if lane_count is None:
        schema["unknown"] = ["lanes", *unknown]
        return schema

    schema["roles"] = _derive_roles(
        lane_count,
        attributes.get("turn"),
        attributes.get("destination"),
        highway,
    )
    return schema


def safe_lane_transition(
    source_schema: Mapping[str, Any] | None,
    target_schema: Mapping[str, Any] | None,
    *,
    connected: bool,
    same_travel_direction: bool,
    same_osm_way: bool = False,
    explicitly_continuous: bool = False,
) -> tuple[tuple[int, int], ...] | None:
    """Return a 1:1 transition only when continuity is explicitly safe.

    Equal counts alone are insufficient: parallel ways can meet at one node
    and lane identity may change across a junction.  Count changes always stay
    unresolved in this lightweight model, even when continuity is explicit.
    """
    if (
        not connected
        or not same_travel_direction
        or not (same_osm_way or explicitly_continuous)
    ):
        return None
    source_count = _schema_lane_count(source_schema)
    target_count = _schema_lane_count(target_schema)
    if source_count is None or source_count != target_count:
        return None
    return tuple((lane, lane) for lane in range(1, source_count + 1))


def _directional_lane_value(
    tags: Mapping[str, str], osm_key: str, suffix: str, oneway: str
) -> str | None:
    directional = _optional(tags.get(f"{osm_key}:{suffix}"))
    if directional is not None:
        return directional
    if oneway != "both":
        return _optional(tags.get(osm_key))
    return None


def _pipe_values(value: str) -> list[str | None]:
    return [_optional(part) for part in value.split("|")]


def _derive_roles(
    lane_count: int,
    turns: list[str | None] | None,
    destinations: list[str | None] | None,
    highway: str | None,
) -> list[str]:
    """Derive only roles supported by explicit lane movements.

    A branch-only lane on a main motorway/trunk is called an exit only when a
    destination is also explicitly present. A mixed through+branch indication
    does not prove physical weaving and therefore remains unknown.
    """
    roles = ["unknown"] * lane_count
    if turns is None:
        return roles
    main_motorway = (highway or "").lower() in _MAIN_MOTORWAY_CLASSES
    for index, turn in enumerate(turns):
        movements = {
            movement.strip().lower()
            for movement in (turn or "").split(";")
            if movement.strip()
        }
        has_through = bool(movements & _THROUGH_MOVEMENTS)
        has_branch = bool(movements & _BRANCH_MOVEMENTS)
        if movements == _THROUGH_MOVEMENTS:
            roles[index] = "through"
        elif (
            main_motorway
            and has_branch
            and not has_through
            and destinations is not None
            and destinations[index] is not None
        ):
            roles[index] = "exit"
    return roles


def _schema_lane_count(schema: Mapping[str, Any] | None) -> int | None:
    if schema is None:
        return None
    value = schema.get("lane_count")
    return value if type(value) is int and 1 <= value <= _MAX_LANE_COUNT else None


def _optional(value: str | None) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    return cleaned or None
