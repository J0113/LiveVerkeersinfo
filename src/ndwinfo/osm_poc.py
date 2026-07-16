"""Pure helpers for the OSM road-identification proof of concept.

The production application deliberately does not depend on OSM object ids.  The
POC keeps that boundary explicit: it turns an Overpass response into directed
edge candidates, then associates current NDW measurement points with those
edges using geometry *and* the direction/reference metadata available at the
site.  No database or network dependency lives in this module, so the matching
rules can be tested deterministically.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Any

_ONEWAY_FORWARD = {"yes", "true", "1"}
_ONEWAY_REVERSE = {"-1", "reverse"}
_CELL_DEGREES = 0.002  # roughly 130–220 m in the Netherlands


def build_overpass_query(
    bbox: tuple[float, float, float, float], profile: str
) -> str:
    """Return a bounded Overpass query for drivable road ways."""
    min_lon, min_lat, max_lon, max_lat = bbox
    if profile == "major":
        highway_filter = (
            "motorway|motorway_link|trunk|trunk_link|primary|primary_link|"
            "secondary|secondary_link"
        )
    else:
        highway_filter = (
            "motorway|motorway_link|trunk|trunk_link|primary|primary_link|"
            "secondary|secondary_link|tertiary|tertiary_link|unclassified|"
            "residential|living_street|service|road"
        )
    return (
        "[out:json][timeout:25];"
        f'way["highway"~"^({highway_filter})$"]'
        f"({min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f});"
        "out meta geom qt;"
    )


def parse_overpass_roads(payload: dict[str, Any], max_features: int = 12_000) -> dict:
    """Convert Overpass ways to directed GeoJSON features.

    OSM way-node order is not always travel direction: ``oneway=-1`` reverses
    it, while a normal two-way road becomes two directed candidates.  Those
    candidates intentionally share the same physical geometry on the map but
    have separate edge ids for heading-aware matching.
    """
    features: list[dict] = []
    source_way_count = 0
    truncated = False

    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        highway = tags.get("highway")
        geometry = element.get("geometry") or []
        coords = [
            [float(node["lon"]), float(node["lat"])]
            for node in geometry
            if node.get("lon") is not None and node.get("lat") is not None
        ]
        if not highway or len(coords) < 2:
            continue
        source_way_count += 1

        oneway = str(tags.get("oneway", "")).strip().lower()
        if not oneway and tags.get("junction") in {"roundabout", "circular"}:
            oneway = "yes"

        if oneway in _ONEWAY_REVERSE:
            variants = [("reverse", list(reversed(coords)), 0)]
        elif oneway in _ONEWAY_FORWARD:
            variants = [("forward", coords, 0)]
        else:
            variants = [
                ("forward", coords, 1),
                ("backward", list(reversed(coords)), -1),
            ]

        for direction, directed_coords, direction_offset in variants:
            if len(features) >= max_features:
                truncated = True
                break
            way_id = int(element["id"])
            edge_id = f"osm:{way_id}:{'b' if direction == 'backward' else 'f'}"
            lanes = _effective_lanes(tags, direction)
            props = {
                "edge_id": edge_id,
                "osm_way_id": way_id,
                "osm_version": element.get("version"),
                "osm_timestamp": element.get("timestamp"),
                "travel_direction": direction,
                "direction_offset": direction_offset,
                "highway": highway,
                "name": tags.get("name"),
                "ref": tags.get("ref"),
                "oneway": oneway or "no",
                "junction": tags.get("junction"),
                "carriageway_ref": tags.get("carriageway_ref"),
                "lanes": lanes,
                "lanes_total": _parse_int(tags.get("lanes")),
                "lanes_forward": _parse_int(tags.get("lanes:forward")),
                "lanes_backward": _parse_int(tags.get("lanes:backward")),
                "turn_lanes": _directional_tag(tags, "turn:lanes", direction),
                "change_lanes": _directional_tag(tags, "change:lanes", direction),
                "destination_lanes": _directional_tag(
                    tags, "destination:lanes", direction
                ),
                "maxspeed": _directional_tag(tags, "maxspeed", direction),
                "maxspeed_conditional": _directional_tag(
                    tags, "maxspeed:conditional", direction
                ),
                "access": tags.get("motor_vehicle") or tags.get("access"),
                "surface": tags.get("surface"),
                "width": tags.get("width"),
                "placement": _directional_tag(tags, "placement", direction),
                "bridge": tags.get("bridge"),
                "tunnel": tags.get("tunnel"),
                "layer": tags.get("layer"),
                "destination": tags.get("destination"),
                "destination_ref": tags.get("destination:ref"),
                "speed_kmh": None,
                "speed_source": None,
                "measured_at": None,
                "linked_site_count": 0,
                "linked_site_ids": None,
                "speed_match_confidence": None,
                # Preserve all tags for the inspector without adding a nested
                # object to MapLibre feature properties.
                "osm_tags": json.dumps(tags, ensure_ascii=False, sort_keys=True),
            }
            features.append(
                {
                    "type": "Feature",
                    "id": edge_id,
                    "geometry": {"type": "LineString", "coordinates": directed_coords},
                    "properties": props,
                }
            )
        if truncated:
            break

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "source_way_count": source_way_count,
            "directed_edge_count": len(features),
            "truncated": truncated,
        },
    }


def link_measurements_to_roads(
    roads: dict,
    measurements: dict,
    *,
    max_distance_m: float = 80.0,
) -> dict[str, int]:
    """Associate NDW measurement points with directed OSM road features.

    Explicit road, carriageway and heading conflicts are rejected.  Ambiguous
    candidates remain visible as measurement points but do not colour a road.
    The function mutates both feature collections and returns summary counts.
    """
    road_features = roads.get("features", [])
    measurement_features = measurements.get("features", [])
    index = _RoadGridIndex(road_features)
    road_observations: dict[int, list[dict]] = defaultdict(list)
    matched = ambiguous = unmatched = 0

    for feature in measurement_features:
        props = feature.setdefault("properties", {})
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
        if not coords or len(coords) < 2:
            props["osm_match_status"] = "unmatched"
            unmatched += 1
            continue

        result = _match_measurement(
            index,
            road_features,
            float(coords[0]),
            float(coords[1]),
            props,
            max_distance_m,
        )
        props.update(result["properties"])
        status = result["status"]
        if status == "matched":
            matched += 1
            road_index = result["road_index"]
            speed = _measurement_speed(props)
            road_observations[road_index].append(
                {
                    "site_id": props.get("site_id"),
                    "speed": speed,
                    "confidence": result["confidence"],
                    "measured_at": props.get("measured_at"),
                }
            )
        elif status == "ambiguous":
            ambiguous += 1
        else:
            unmatched += 1

    for road_index, observations in road_observations.items():
        props = road_features[road_index]["properties"]
        site_ids = sorted({str(o["site_id"]) for o in observations if o["site_id"]})
        speeds = [o for o in observations if o["speed"] is not None]
        props["linked_site_count"] = len(site_ids)
        props["linked_site_ids"] = ", ".join(site_ids[:8]) or None
        props["speed_match_confidence"] = round(
            sum(o["confidence"] for o in observations) / len(observations), 3
        )
        timestamps = [o["measured_at"] for o in observations if o["measured_at"]]
        props["measured_at"] = max(timestamps) if timestamps else None
        if speeds:
            total_weight = sum(max(o["confidence"], 0.05) for o in speeds)
            props["speed_kmh"] = round(
                sum(o["speed"] * max(o["confidence"], 0.05) for o in speeds)
                / total_weight,
                1,
            )
            props["speed_source"] = "NDW measured speed"

    return {
        "measurement_count": len(measurement_features),
        "matched_count": matched,
        "ambiguous_count": ambiguous,
        "unmatched_count": unmatched,
        "roads_with_measurements": len(road_observations),
    }


class _RoadGridIndex:
    def __init__(self, features: list[dict]):
        self.cells: dict[tuple[int, int], list[tuple[int, list, list]]] = defaultdict(list)
        for feature_index, feature in enumerate(features):
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            for a, b in zip(coords, coords[1:]):
                min_x, max_x = sorted((float(a[0]), float(b[0])))
                min_y, max_y = sorted((float(a[1]), float(b[1])))
                for cell_x in range(_cell(min_x), _cell(max_x) + 1):
                    for cell_y in range(_cell(min_y), _cell(max_y) + 1):
                        self.cells[(cell_x, cell_y)].append((feature_index, a, b))

    def nearby(self, lon: float, lat: float) -> list[tuple[int, list, list]]:
        out: list[tuple[int, list, list]] = []
        cx, cy = _cell(lon), _cell(lat)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                out.extend(self.cells.get((cx + dx, cy + dy), []))
        return out


def _match_measurement(
    index: _RoadGridIndex,
    roads: list[dict],
    lon: float,
    lat: float,
    props: dict,
    max_distance_m: float,
) -> dict:
    measurement_refs = _road_refs(props.get("road"))
    measurement_side = _carriageway_side(props.get("carriageway"))
    measurement_lanes = _parse_int(props.get("num_lanes"))
    heading = _finite_number(props.get("openlr_bearing"))
    if heading is None:
        heading = _finite_number(props.get("bearing"))

    best_by_road: dict[int, dict] = {}
    for road_index, a, b in index.nearby(lon, lat):
        road_props = roads[road_index]["properties"]
        distance, bearing = _point_segment_distance_and_bearing(lon, lat, a, b)
        if distance > max_distance_m:
            continue
        road_refs = _road_refs(road_props.get("ref"))
        ref_match = bool(measurement_refs and road_refs and measurement_refs & road_refs)
        if measurement_refs and road_refs and not ref_match:
            continue

        road_side = _carriageway_side(road_props.get("carriageway_ref"))
        side_match = bool(measurement_side and road_side and measurement_side == road_side)
        if measurement_side and road_side and not side_match:
            continue

        heading_delta = _angle_diff(heading, bearing) if heading is not None else None
        if heading_delta is not None and heading_delta > 85:
            continue

        road_lanes = _parse_int(road_props.get("lanes"))
        lane_match = bool(
            measurement_lanes is not None
            and road_lanes is not None
            and measurement_lanes == road_lanes
        )
        score = distance
        if heading_delta is not None:
            score += heading_delta * 0.45
        else:
            score += 18
        if ref_match:
            score -= 22
        if side_match:
            score -= 12
        if lane_match:
            score -= 4

        candidate = {
            "road_index": road_index,
            "distance": distance,
            "bearing": bearing,
            "heading_delta": heading_delta,
            "ref_match": ref_match,
            "side_match": side_match,
            "lane_match": lane_match,
            "score": score,
        }
        current = best_by_road.get(road_index)
        if current is None or score < current["score"]:
            best_by_road[road_index] = candidate

    candidates = sorted(best_by_road.values(), key=lambda item: item["score"])
    if not candidates:
        return {
            "status": "unmatched",
            "road_index": None,
            "confidence": 0.0,
            "properties": {"osm_match_status": "unmatched", "osm_match_confidence": 0.0},
        }

    best = candidates[0]
    second_score = candidates[1]["score"] if len(candidates) > 1 else best["score"] + 100
    margin = second_score - best["score"]
    confidence = _candidate_confidence(best, margin, max_distance_m, heading is not None)
    status = "matched" if confidence >= 0.5 and margin >= 4 else "ambiguous"
    road_props = roads[best["road_index"]]["properties"]
    return {
        "status": status,
        "road_index": best["road_index"] if status == "matched" else None,
        "confidence": confidence,
        "properties": {
            "osm_match_status": status,
            "osm_edge_id": road_props.get("edge_id"),
            "osm_way_id": road_props.get("osm_way_id"),
            "osm_road_ref": road_props.get("ref"),
            "osm_road_name": road_props.get("name"),
            "osm_travel_direction": road_props.get("travel_direction"),
            "osm_match_distance_m": round(best["distance"], 1),
            "osm_heading_delta_deg": (
                round(best["heading_delta"], 1)
                if best["heading_delta"] is not None
                else None
            ),
            "osm_match_margin": round(margin, 2),
            "osm_match_confidence": confidence,
        },
    }


def _candidate_confidence(
    candidate: dict, margin: float, max_distance_m: float, has_heading: bool
) -> float:
    distance_component = max(0.0, 1 - candidate["distance"] / max_distance_m)
    heading_component = (
        max(0.0, 1 - candidate["heading_delta"] / 90)
        if candidate["heading_delta"] is not None
        else 0.35
    )
    confidence = (
        0.4 * distance_component
        + 0.25 * heading_component
        + 0.2 * float(candidate["ref_match"])
        + 0.1 * float(candidate["side_match"])
        + 0.05 * float(candidate["lane_match"])
    )
    confidence *= min(1.0, 0.55 + max(margin, 0) / 18)
    if not has_heading:
        confidence = min(confidence, 0.72)
    return round(max(0.0, min(confidence, 1.0)), 3)


def _measurement_speed(props: dict) -> float | None:
    values = []
    for lane in props.get("lanes") or []:
        value = _finite_number(lane.get("speed_kmh"))
        if value is not None and value >= 0:
            values.append(value)
    if not values:
        return None
    values.sort()
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _effective_lanes(tags: dict, direction: str) -> int | None:
    suffix = "backward" if direction == "backward" else "forward"
    directional = _parse_int(tags.get(f"lanes:{suffix}"))
    if directional is not None:
        return directional
    total = _parse_int(tags.get("lanes"))
    if total is None:
        return None
    oneway = str(tags.get("oneway", "")).lower()
    if oneway in _ONEWAY_FORWARD | _ONEWAY_REVERSE:
        return total
    return max(1, total // 2) if total > 1 else total


def _directional_tag(tags: dict, base: str, direction: str):
    suffix = "backward" if direction == "backward" else "forward"
    return tags.get(f"{base}:{suffix}") or tags.get(base)


def _parse_int(value) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _road_refs(value) -> set[str]:
    if not value:
        return set()
    refs = set()
    for prefix, number, suffix in re.findall(
        r"\b([A-Z]{1,3})\s*0*(\d+)([A-Z]?)\b", str(value).upper()
    ):
        refs.add(f"{prefix}{int(number)}{suffix}")
    return refs


def _carriageway_side(value) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if normalized in {"R", "RIGHT", "RECHTS"}:
        return "R"
    if normalized in {"L", "LEFT", "LINKS"}:
        return "L"
    return None


def _cell(value: float) -> int:
    return math.floor(value / _CELL_DEGREES)


def _point_segment_distance_and_bearing(
    lon: float, lat: float, a: list, b: list
) -> tuple[float, float]:
    metres_lon = 111_320 * math.cos(math.radians(lat))
    metres_lat = 110_540
    ax = (float(a[0]) - lon) * metres_lon
    ay = (float(a[1]) - lat) * metres_lat
    bx = (float(b[0]) - lon) * metres_lon
    by = (float(b[1]) - lat) * metres_lat
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denom))
    px, py = ax + t * dx, ay + t * dy
    distance = math.hypot(px, py)
    bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return distance, bearing


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)
