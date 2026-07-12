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
from shapely.geometry import Point, box, shape
from shapely.ops import transform as transform_geometry
from shapely.strtree import STRtree

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
    carriageway_type: str | None
    form_of_way: int | None
    road_number: str | None
    route_begin_km: float | None
    route_end_km: float | None
    road_section_length_m: float | None
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
        "carriageway_type": None,
        "form_of_way": None,
        "road_number": None,
        "route_begin_km": None,
        "route_end_km": None,
        "road_section_length_m": None,
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
            props["route_begin_km"] = road.get("begin_km")
            props["route_end_km"] = road.get("end_km")
            props["road_section_length_m"] = road.get("length_m")
            props["carriageway_type"] = road.get("carriageway_type")
            props["form_of_way"] = road.get("form_of_way")
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
    max_interpolation_span_km: float = 5.0,
    max_extrapolation_distance_km: float = 0.75,
    now: datetime | None = None,
) -> list[GeoJson]:
    """Match each NDW site once, aggregate it, and expand sections per lane."""
    current_time = now or datetime.now(timezone.utc)
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    # Transform every static segment once and let GEOS select only nearby
    # candidates. This replaces the former observations × segments scan.
    metric_lines = [
        transform_geometry(_TO_RD, shape(config["geometry"])) for config in configurations
    ]
    spatial_index = STRtree(metric_lines) if metric_lines else None

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
        if spatial_index is None:
            continue
        candidate_indices = spatial_index.query(point, predicate="dwithin", distance=max_distance_m)
        best_distance: float | None = None
        for raw_index in candidate_indices:
            index = int(raw_index)
            config = configurations[index]
            cp = config["properties"]
            if not _same_road(props.get("road"), cp.get("road_number")):
                continue
            if not _same_carriageway(props.get("carriageway"), cp.get("carriageway_position")):
                continue
            line = metric_lines[index]
            distance = point.distance(line)
            heading_difference = _heading_difference(props.get("bearing"), line, point)
            if heading_difference is not None and heading_difference > max_heading_difference:
                continue
            score = distance + (heading_difference or 0) * 0.35
            if best is None or score < best[0]:
                best = (score, config)
                best_distance = distance
        if best is None:
            continue
        config = best[1]
        assert best_distance is not None
        for lane in props.get("lanes") or []:
            lane_number = _optional_int(lane.get("lane")) if isinstance(lane, Mapping) else None
            if lane_number is None or not 1 <= lane_number <= config["properties"]["lane_count"]:
                continue
            buckets.setdefault((config["properties"]["weggeg_id"], lane_number), []).append(
                {
                    **lane,
                    "distance": best_distance,
                    "measured_at": props.get("measured_at"),
                    "site_id": props.get("site_id"),
                }
            )

    direct = {
        key: _aggregate_readings(readings)
        for key, readings in buckets.items()
    }
    estimated = _estimate_lane_speed_gaps(
        configurations,
        direct,
        max_interpolation_span_km=max_interpolation_span_km,
        max_extrapolation_distance_km=max_extrapolation_distance_km,
    )

    output: list[GeoJson] = []
    for config in configurations:
        cp = config["properties"]
        count = cp["lane_count"]
        for lane_number in range(1, count + 1):
            lane_key = (cp["weggeg_id"], lane_number)
            readings = buckets.get(lane_key, [])
            aggregate = direct.get(lane_key) or estimated.get(lane_key) or _aggregate_readings([])
            is_estimated = lane_key in estimated
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
                "match_confidence": "estimated" if is_estimated else aggregate["match_confidence"],
                "speed_estimated": is_estimated,
                "speed_estimation_method": aggregate.get("estimation_method"),
                "interpolation_span_km": aggregate.get("interpolation_span_km"),
                "nearest_measurement_distance_m": aggregate.get("nearest_measurement_distance_m"),
                "geometry_kind": "schematic-lane-offset",
                "speed_provenance": (
                    "NDW current measurements"
                    if readings
                    else "NDW constrained route interpolation"
                    if is_estimated
                    else None
                ),
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


def features_intersecting_bbox(
    features: Sequence[GeoJson], bbox: Sequence[float]
) -> list[GeoJson]:
    """Clip an expanded matching context back to the requested viewport."""
    viewport = box(*bbox)
    return [feature for feature in features if shape(feature["geometry"]).intersects(viewport)]


def _estimate_lane_speed_gaps(
    configurations: Sequence[GeoJson],
    direct: Mapping[tuple[str, int], Mapping[str, Any]],
    *,
    max_interpolation_span_km: float,
    max_extrapolation_distance_km: float,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Fill short gaps only within one road, carriageway and lane sequence."""
    sequences: dict[tuple[str, str, str, str, int], list[tuple[float, GeoJson]]] = {}
    for config in configurations:
        props = config["properties"]
        road = _same_route_value(props.get("road_number"))
        carriageway = _same_route_value(props.get("carriageway_position"))
        carriageway_type = _same_route_value(props.get("carriageway_type"))
        form_of_way = _same_route_value(props.get("form_of_way"))
        position = _route_position_km(props)
        if (
            road is None
            or carriageway is None
            or carriageway_type is None
            or form_of_way is None
            or position is None
        ):
            continue
        for lane_number in range(1, props["lane_count"] + 1):
            sequence_key = (road, carriageway, carriageway_type, form_of_way, lane_number)
            sequences.setdefault(sequence_key, []).append((position, config))

    estimates: dict[tuple[str, int], dict[str, Any]] = {}
    for (_, _, _, _, lane_number), sequence in sequences.items():
        sequence.sort(key=lambda item: item[0])
        anchors = [
            (position, config, direct[(config["properties"]["weggeg_id"], lane_number)])
            for position, config in sequence
            if direct.get((config["properties"]["weggeg_id"], lane_number), {}).get("speed_kmh")
            is not None
        ]
        if not anchors:
            continue
        for position, config in sequence:
            key = (config["properties"]["weggeg_id"], lane_number)
            if direct.get(key, {}).get("speed_kmh") is not None:
                continue
            before = next((anchor for anchor in reversed(anchors) if anchor[0] <= position), None)
            after = next((anchor for anchor in anchors if anchor[0] >= position), None)
            estimate = None
            if before and after and before[0] != after[0]:
                span = after[0] - before[0]
                if span <= max_interpolation_span_km:
                    ratio = (position - before[0]) / span
                    estimate = _interpolated_aggregate(before[2], after[2], ratio, span)
            elif before or after:
                anchor = before or after
                assert anchor is not None
                distance = abs(position - anchor[0])
                if distance <= max_extrapolation_distance_km:
                    estimate = _extrapolated_aggregate(anchor[2], distance)
            if estimate is not None:
                estimates[key] = estimate
    return estimates


def _route_position_km(props: Mapping[str, Any]) -> float | None:
    begin = _optional_float(props.get("route_begin_km"))
    end = _optional_float(props.get("route_end_km"))
    if begin is None or end is None:
        return None
    length = _optional_float(props.get("road_section_length_m"))
    start_distance = _optional_float(props.get("begin_distance_m"))
    end_distance = _optional_float(props.get("end_distance_m"))
    fraction = 0.5
    if length and length > 0 and start_distance is not None and end_distance is not None:
        fraction = min(max(((start_distance + end_distance) / 2) / length, 0.0), 1.0)
    return begin + (end - begin) * fraction


def _same_route_value(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper().replace(" ", "")
    return normalized or None


def _interpolated_aggregate(
    before: Mapping[str, Any], after: Mapping[str, Any], ratio: float, span: float
) -> dict[str, Any]:
    def interpolate(name: str) -> float | None:
        left, right = before.get(name), after.get(name)
        if left is None or right is None:
            return left if ratio < 0.5 else right
        return float(left) + (float(right) - float(left)) * ratio

    flow = interpolate("flow_veh_h")
    timestamps = [before.get("measured_at"), after.get("measured_at")]
    return {
        "speed_kmh": round(interpolate("speed_kmh"), 1),
        "flow_veh_h": round(flow) if flow is not None else None,
        "measured_at": max(filter(None, timestamps), default=None),
        "measurement_count": 0,
        "input_count": None,
        "match_distance_m": None,
        "match_confidence": "estimated",
        "estimation_method": "linear-between-current-measurements",
        "interpolation_span_km": round(span, 2),
        "nearest_measurement_distance_m": round(min(ratio, 1 - ratio) * span * 1000),
    }


def _extrapolated_aggregate(anchor: Mapping[str, Any], distance: float) -> dict[str, Any]:
    return {
        **anchor,
        "measurement_count": 0,
        "input_count": None,
        "match_distance_m": None,
        "match_confidence": "estimated",
        "estimation_method": "short-nearest-measurement-extension",
        "interpolation_span_km": None,
        "nearest_measurement_distance_m": round(distance * 1000),
    }


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
