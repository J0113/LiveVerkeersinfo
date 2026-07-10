"""WEGGEG lane configuration access and conservative NDW lane matching.

RWS WEGGEG describes how many lanes exist along a Rijksweg section. It does
not contain surveyed geometry for every individual lane. The generated lane
features therefore retain the official section centreline and expose a visual
offset index for MapLibre. This distinction is kept explicit in the metadata.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, TypedDict

import httpx
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as transform_geometry

from ndwinfo.nwb import _next_link, _optional_float, _optional_int, _optional_str, _valid_multiline

GeoJson = dict[str, Any]
_LANE_COUNTS = re.compile(r"^\s*(\d+)\s*->\s*(\d+)\s*$")
_TO_RD = Transformer.from_crs("EPSG:4326", "EPSG:28992", always_xy=True).transform


class LaneConfigurationProperties(TypedDict):
    weggeg_id: str
    nwb_road_section_id: int
    lane_count_start: int
    lane_count_end: int
    lane_count: int
    lane_count_variable: bool
    description: str
    side: str | None
    carriageway_position: str | None
    road_number: str | None
    begin_distance_m: float | None
    end_distance_m: float | None
    valid_from: str | None


@dataclass(frozen=True)
class LaneConfigurationFetchResult:
    features: list[GeoJson]
    truncated: bool
    invalid_features: int


def build_weggeg_query_params(bbox: Sequence[float], *, limit: int = 1000) -> dict[str, str | int]:
    if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
        raise ValueError("bbox must contain four finite values")
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("bbox minima must be less than maxima")
    return {
        "bbox": ",".join(f"{value:.6f}" for value in bbox),
        "limit": min(max(limit, 1), 1000),
        "f": "json",
        "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "bbox-crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
    }


async def fetch_lane_configurations(
    client: httpx.AsyncClient, url: str, bbox: Sequence[float], max_features: int
) -> LaneConfigurationFetchResult:
    params: dict[str, str | int] | None = build_weggeg_query_params(bbox)
    next_url: str | None = url
    features: list[GeoJson] = []
    invalid = 0
    truncated = False
    while next_url and len(features) < max_features:
        response = await client.get(
            next_url, params=params, headers={"Accept": "application/geo+json"}
        )
        response.raise_for_status()
        payload = response.json()
        raw_features = payload.get("features")
        if not isinstance(raw_features, list):
            raise ValueError("PDOK WEGGEG response does not contain a features array")
        for raw in raw_features:
            transformed = transform_lane_configuration(raw) if isinstance(raw, Mapping) else None
            if transformed is None:
                invalid += 1
                continue
            features.append(transformed)
            if len(features) >= max_features:
                truncated = True
                break
        next_url = _next_link(payload) if len(features) < max_features else None
        params = None
    return LaneConfigurationFetchResult(features, truncated, invalid)


def transform_lane_configuration(raw: Mapping[str, Any]) -> GeoJson | None:
    source_id = raw.get("id")
    props = raw.get("properties")
    geometry = _valid_multiline(raw.get("geometry"))
    if (
        not isinstance(source_id, str)
        or not source_id
        or not isinstance(props, Mapping)
        or not geometry
    ):
        return None
    road_section_id = _optional_int(props.get("wvk_id"))
    description = _optional_str(props.get("omschr"))
    match = _LANE_COUNTS.match(description or "")
    if road_section_id is None or match is None:
        return None
    start, end = (int(match.group(1)), int(match.group(2)))
    if not 1 <= start <= 12 or not 1 <= end <= 12:
        return None
    stable: LaneConfigurationProperties = {
        "weggeg_id": source_id,
        "nwb_road_section_id": road_section_id,
        "lane_count_start": start,
        "lane_count_end": end,
        "lane_count": max(start, end),
        "lane_count_variable": start != end,
        "description": description or "",
        "side": _optional_str(props.get("izi_side")),
        "carriageway_position": _optional_str(props.get("kantcode")),
        "road_number": None,
        "begin_distance_m": _optional_float(props.get("begafstand")),
        "end_distance_m": _optional_float(props.get("endafstand")),
        "valid_from": _optional_str(props.get("wvk_begdat")),
    }
    return {"type": "Feature", "id": source_id, "geometry": geometry, "properties": stable}


def attach_nwb_metadata(
    configurations: list[GeoJson], nwb_features: Sequence[Mapping[str, Any]]
) -> None:
    """Join stable NWB metadata to WEGGEG using the official wvk_id key."""
    roads: dict[int, Mapping[str, Any]] = {}
    for feature in nwb_features:
        props = feature.get("properties")
        if isinstance(props, Mapping):
            road_id = _optional_int(props.get("nwb_road_section_id"))
            if road_id is not None:
                roads[road_id] = props
    for feature in configurations:
        props = feature["properties"]
        road = roads.get(props["nwb_road_section_id"])
        if road:
            props["road_number"] = road.get("road_number")
            # NWB's R/L carriageway position is preferred over the more
            # source-specific WEGGEG side encoding.
            props["carriageway_position"] = road.get("carriageway_position") or props.get(
                "carriageway_position"
            )


def build_lane_speed_features(
    configurations: Sequence[GeoJson],
    observations: Sequence[Mapping[str, Any]],
    *,
    max_distance_m: float = 45.0,
    max_heading_difference: float = 50.0,
    max_age_s: float = 600.0,
    now: datetime | None = None,
) -> list[GeoJson]:
    """Match each NDW site once, aggregate it, and expand sections per lane."""
    current_time = now or datetime.now(timezone.utc)
    metric_lines: dict[str, Any] = {}
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}

    for observation in observations:
        geometry = observation.get("geometry")
        props = observation.get("properties")
        if (
            not isinstance(geometry, Mapping)
            or geometry.get("type") != "Point"
            or not isinstance(props, Mapping)
        ):
            continue
        measured_at = _parse_timestamp(props.get("measured_at"))
        if measured_at and (current_time - measured_at).total_seconds() > max_age_s:
            continue
        best: tuple[float, GeoJson] | None = None
        point = transform_geometry(_TO_RD, Point(geometry["coordinates"][:2]))
        for config in configurations:
            cp = config["properties"]
            if not _same_road(props.get("road"), cp.get("road_number")):
                continue
            if not _same_carriageway(props.get("carriageway"), cp.get("carriageway_position")):
                continue
            config_id = cp["weggeg_id"]
            line = metric_lines.setdefault(
                config_id, transform_geometry(_TO_RD, shape(config["geometry"]))
            )
            distance = point.distance(line)
            if distance > max_distance_m:
                continue
            heading_difference = _heading_difference(props.get("bearing"), line, point)
            if heading_difference is not None and heading_difference > max_heading_difference:
                continue
            score = distance + (heading_difference or 0) * 0.35
            if best is None or score < best[0]:
                best = (score, config)
        if best is None:
            continue
        config = best[1]
        distance = point.distance(metric_lines[config["properties"]["weggeg_id"]])
        for lane in props.get("lanes") or []:
            lane_number = _optional_int(lane.get("lane")) if isinstance(lane, Mapping) else None
            if lane_number is None or not 1 <= lane_number <= config["properties"]["lane_count"]:
                continue
            buckets.setdefault((config["properties"]["weggeg_id"], lane_number), []).append(
                {
                    **lane,
                    "distance": distance,
                    "measured_at": props.get("measured_at"),
                    "site_id": props.get("site_id"),
                }
            )

    output: list[GeoJson] = []
    for config in configurations:
        cp = config["properties"]
        count = cp["lane_count"]
        for lane_number in range(1, count + 1):
            readings = buckets.get((cp["weggeg_id"], lane_number), [])
            aggregate = _aggregate_readings(readings)
            lane_id = f"{cp['weggeg_id']}:lane:{lane_number}"
            properties = {
                **cp,
                "lane_feature_id": lane_id,
                "lane_number": lane_number,
                # Lane 1 is nearest the median/far-left from the driver's view.
                "lane_offset_index": lane_number - ((count + 1) / 2),
                "speed_kmh": aggregate["speed_kmh"],
                "flow_veh_h": aggregate["flow_veh_h"],
                "measured_at": aggregate["measured_at"],
                "measurement_count": aggregate["measurement_count"],
                "input_count": aggregate["input_count"],
                "match_distance_m": aggregate["match_distance_m"],
                "match_confidence": aggregate["match_confidence"],
                "geometry_kind": "schematic-lane-offset",
                "speed_provenance": "NDW current measurements" if readings else None,
            }
            output.append(
                {
                    "type": "Feature",
                    "id": lane_id,
                    "geometry": config["geometry"],
                    "properties": properties,
                }
            )
    return output


def _aggregate_readings(readings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    valid = [r for r in readings if r.get("speed_kmh") is not None]
    weights = [max(_optional_int(r.get("n_inputs")) or 1, 1) for r in valid]
    speed = (
        sum(float(r["speed_kmh"]) * w for r, w in zip(valid, weights)) / sum(weights)
        if valid
        else None
    )
    flows = [float(r["flow_veh_h"]) for r in readings if r.get("flow_veh_h") is not None]
    distances = [float(r["distance"]) for r in readings]
    timestamps = [str(r["measured_at"]) for r in readings if r.get("measured_at")]
    distance = min(distances) if distances else None
    return {
        "speed_kmh": round(speed, 1) if speed is not None else None,
        "flow_veh_h": round(sum(flows) / len(flows)) if flows else None,
        "measured_at": max(timestamps) if timestamps else None,
        "measurement_count": len(valid),
        "input_count": sum(weights) if valid else None,
        "match_distance_m": round(distance, 1) if distance is not None else None,
        "match_confidence": "high"
        if distance is not None and distance <= 15
        else "medium"
        if distance is not None
        else None,
    }


def _same_road(observed: Any, reference: Any) -> bool:
    def normalize(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.upper().replace(" ", "")
        if value.startswith("RW") and value[2:].isdigit():
            value = f"A{int(value[2:])}"
        if value.isdigit():
            value = str(int(value))
        return value

    left, right = normalize(observed), normalize(reference)
    return left is not None and right is not None and left == right


def _same_carriageway(observed: Any, reference: Any) -> bool:
    if not observed or not reference:
        return True
    return str(observed).strip().upper() == str(reference).strip().upper()


def _heading_difference(bearing: Any, line: Any, point: Point) -> float | None:
    try:
        observed = float(bearing)
    except (TypeError, ValueError):
        return None
    nearest = line.project(point)
    delta = min(max(line.length * 0.002, 2.0), 10.0)
    before = line.interpolate(max(0.0, nearest - delta))
    after = line.interpolate(min(line.length, nearest + delta))
    if before.equals(after):
        return None
    road_bearing = math.degrees(math.atan2(after.x - before.x, after.y - before.y)) % 360
    return abs((observed - road_bearing + 180) % 360 - 180)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc)
        )
    except ValueError:
        return None
