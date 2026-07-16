"""Pure OSM road-tag normalization.

The importer stores the original tags as provenance, but matching code should
only consume the deterministic normalized fields returned here.  Keeping this
logic independent from pyosmium and the database makes direction handling easy
to test with fixed fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from ndwinfo.osm.lanes import build_lane_schema

DEFAULT_HIGHWAY_CLASSES = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
    }
)

_FORWARD_ONEWAY = {"yes", "true", "1"}
_REVERSE_ONEWAY = {"-1", "reverse"}
_BOTH_ONEWAY = {"no", "false", "0"}
_IMPLIED_ONEWAY_HIGHWAYS = {"motorway"}
_REQUIRES_EXPLICIT_DIRECTION = {"motorway_link"}
_PRIVATE_ACCESS = {"no", "private"}


@dataclass(frozen=True)
class Direction:
    name: str
    reverse_geometry: bool


def is_drivable(
    tags: Mapping[str, str],
    allowed_highways: frozenset[str] = DEFAULT_HIGHWAY_CLASSES,
) -> bool:
    """Whether a way belongs in the MVP motor-vehicle graph.

    Explicit motor-vehicle access wins over generic access.  Destination and
    permissive access remain present because they are physically driveable and
    can be reached by a user's GPS track; wholly private/no-access ways do not.
    """
    highway = _clean(tags.get("highway"))
    if highway not in allowed_highways or _clean(tags.get("area")) == "yes":
        return False
    access = _clean(tags.get("motor_vehicle") or tags.get("vehicle") or tags.get("access"))
    return access not in _PRIVATE_ACCESS


def travel_directions(tags: Mapping[str, str]) -> tuple[Direction, ...]:
    """Return legal directed variants, respecting implicit roundabout oneway."""
    oneway = normalized_oneway(tags)
    if oneway == "reverse":
        return (Direction("reverse", True),)
    if oneway == "forward":
        return (Direction("forward", False),)
    if oneway == "unknown":
        return ()
    return (Direction("forward", False), Direction("backward", True))


def normalized_oneway(tags: Mapping[str, str]) -> str:
    value = _clean(tags.get("oneway"))
    if value in _REVERSE_ONEWAY:
        return "reverse"
    if value in _FORWARD_ONEWAY:
        return "forward"
    if value in _BOTH_ONEWAY:
        return "both"
    # Reversible/alternating roads require live access state that OSM does not
    # provide. Unknown non-empty values are equally unsafe to open both ways.
    if value:
        return "unknown"
    if not value and _clean(tags.get("junction")) in {"roundabout", "circular"}:
        return "forward"
    if _clean(tags.get("highway")) in _IMPLIED_ONEWAY_HIGHWAYS:
        return "forward"
    if _clean(tags.get("highway")) in _REQUIRES_EXPLICIT_DIRECTION:
        return "unknown"
    return "both"


def normalize_way_tags(tags: Mapping[str, str], direction: str) -> dict:
    """Normalize the subset that affects matching or display for one edge."""
    suffix = "backward" if direction in {"backward", "reverse"} else "forward"
    normalized = {
        "highway": _clean(tags.get("highway")),
        "road_number": normalize_road_ref(tags.get("ref")),
        "name": _optional(tags.get("name")),
        "oneway": normalized_oneway(tags),
        "junction": _optional(tags.get("junction")),
        "carriageway_ref": _optional(
            tags.get(f"carriageway_ref:{suffix}") or tags.get("carriageway_ref")
        ),
        "lanes": directional_int(tags, "lanes", suffix),
        "maxspeed": directional_value(tags, "maxspeed", suffix),
        "access": _optional(
            tags.get(f"motor_vehicle:{suffix}")
            or tags.get("motor_vehicle")
            or tags.get(f"access:{suffix}")
            or tags.get("access")
        ),
        "bridge": _optional(tags.get("bridge")),
        "tunnel": _optional(tags.get("tunnel")),
        "layer": _parse_int(tags.get("layer")),
    }
    normalized["lane_schema"] = build_lane_schema(
        tags,
        direction,
        lane_count=normalized["lanes"],
        oneway=normalized["oneway"],
        highway=normalized["highway"],
    )
    return normalized


def normalize_road_ref(value: str | None) -> str | None:
    if not value:
        return None
    # Retain compound refs ("A4;E19") but make whitespace/case deterministic.
    parts = [part.strip().upper().replace(" ", "") for part in value.split(";")]
    normalized = ";".join(part for part in parts if part)
    return normalized or None


def directional_value(tags: Mapping[str, str], key: str, suffix: str) -> str | None:
    return _optional(tags.get(f"{key}:{suffix}") or tags.get(key))


def directional_int(tags: Mapping[str, str], key: str, suffix: str) -> int | None:
    direct = _parse_int(tags.get(f"{key}:{suffix}"))
    if direct is not None:
        return direct
    if key == "lanes" and _optional(tags.get("lanes:both_ways")) is not None:
        # A centre lane cannot safely be split between directions. Directional
        # counts remain usable above; a bare total does not.
        return None
    total = _parse_int(tags.get(key))
    if total is None:
        return None
    # Only halve a total on explicitly bidirectional roads.  Odd totals cannot
    # be allocated reliably and remain unknown rather than inventing a lane.
    if normalized_oneway(tags) == "both":
        return total // 2 if total % 2 == 0 else None
    return total


def material_signature(tags: Mapping[str, str], direction: str) -> str:
    """Stable signature for identity when match-relevant attributes change."""
    normalized = normalize_way_tags(tags, direction)
    keys = (
        "highway",
        "road_number",
        "oneway",
        "junction",
        "carriageway_ref",
        "lanes",
        "lane_schema",
        "maxspeed",
        "access",
        "bridge",
        "tunnel",
        "layer",
    )
    return "|".join(f"{key}={_signature_value(normalized.get(key))}" for key in keys)


def _signature_value(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return str(value) if value is not None else ""


def _parse_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clean(value: str | None) -> str:
    return str(value or "").strip().lower()


def _optional(value: str | None) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    return cleaned or None
