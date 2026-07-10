"""Typed access to PDOK's NWB road-section OGC API Features collection.

This module is deliberately independent of FastAPI and MapLibre. It owns the
upstream query, pagination, validation, and conversion to the application's
stable road-segment properties. Future traffic matching should consume those
properties rather than depending on the complete upstream PDOK schema.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, Literal, Mapping, Sequence, TypedDict, TypeVar

import httpx

GeoJson = dict[str, Any]
T = TypeVar("T")


class RoadSegmentProperties(TypedDict):
    """Stable application model kept independent from future PDOK schema additions."""

    segment_id: str
    nwb_road_section_id: int | None
    begin_junction_id: int | None
    end_junction_id: int | None
    road_number: str | None
    street_name: str | None
    road_manager_type: str | None
    road_manager_name: str | None
    direction: str | None
    administrative_direction: str | None
    carriageway_position: str | None
    position_to_orientation_line: str | None
    carriageway_type: str | None
    frc: int | None
    form_of_way: int | None
    openlr: str | None
    begin_km: float | None
    end_km: float | None
    length_m: float | None
    valid_from: str | None
    road_class: Literal["motorway", "primary", "local"]
    traffic_state: None


class NwbRoadSegment(TypedDict):
    type: Literal["Feature"]
    id: str
    geometry: GeoJson
    properties: RoadSegmentProperties


@dataclass(frozen=True)
class NwbDetailProfile:
    """A zoom-dependent upstream query and rendering profile."""

    name: str
    min_zoom: int
    manager_types: tuple[str | None, ...]
    max_features: int


@dataclass(frozen=True)
class NwbFetchResult:
    feature_collection: GeoJson
    truncated: bool
    invalid_features: int


@dataclass(frozen=True)
class TrafficMatchObservation:
    """Extension point for a future NDW observation-to-NWB matcher.

    Matching should prefer an explicit NWB id or OpenLR reference, then road,
    direction/carriageway and kilometre metadata, and only finally a spatial
    nearest-segment search with heading constraints.
    """

    nwb_road_section_id: int | None = None
    openlr: str | None = None
    road_number: str | None = None
    carriageway: str | None = None
    bearing: float | None = None


class TtlLruCache(Generic[T]):
    """Small in-process TTL/LRU cache suitable for monthly reference geometry."""

    def __init__(self, ttl_s: float, max_entries: int) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        item = self._items.get(key)
        if item is None:
            return None
        created_at, value = item
        if time.monotonic() - created_at >= self.ttl_s:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        self._items[key] = (time.monotonic(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


def detail_profile(zoom: float, configured_max: int = 5000) -> NwbDetailProfile | None:
    """Choose bounded NWB detail without ever requesting the whole country.

    PDOK's Core OGC API exposes equality filters for road-manager type, but not
    range filters for FRC. At z9-10 Rijkswaterstaat roads are therefore the
    useful server-side major-road subset. z11 adds provincial roads. Detailed
    viewport geometry starts at z12.
    """

    cap = max(1, configured_max)
    if zoom < 9:
        return None
    if zoom < 11:
        return NwbDetailProfile("national", 9, ("R",), min(cap, 2500))
    if zoom < 12:
        return NwbDetailProfile("major", 11, ("R", "P"), min(cap, 4000))
    return NwbDetailProfile("detailed", 12, (None,), cap)


def cache_key(bbox: Sequence[float], profile: NwbDetailProfile) -> str:
    rounded = ",".join(f"{value:.5f}" for value in bbox)
    return f"{profile.name}:{rounded}"


def build_query_params(
    bbox: Sequence[float], manager_type: str | None, *, limit: int = 1000
) -> dict[str, str | int]:
    """Build an OGC API Features CRS84 viewport request."""

    if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
        raise ValueError("bbox must contain four finite values")
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("bbox minima must be less than maxima")
    params: dict[str, str | int] = {
        "bbox": ",".join(f"{value:.6f}" for value in bbox),
        "limit": min(max(limit, 1), 1000),
        "f": "json",
        "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "bbox-crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
    }
    if manager_type is not None:
        params["wegbehsrt"] = manager_type
    return params


async def fetch_road_segments(
    client: httpx.AsyncClient,
    url: str,
    bbox: Sequence[float],
    profile: NwbDetailProfile,
) -> NwbFetchResult:
    """Fetch, paginate, validate and normalize a viewport of NWB road sections."""

    per_query_cap = max(1, profile.max_features // len(profile.manager_types))
    tasks = [
        _fetch_manager_subset(client, url, bbox, manager_type, per_query_cap)
        for manager_type in profile.manager_types
    ]
    subsets = await asyncio.gather(*tasks)

    features: list[GeoJson] = []
    seen: set[str] = set()
    invalid = 0
    truncated = False
    for raw_features, subset_truncated in subsets:
        truncated = truncated or subset_truncated
        for raw in raw_features:
            transformed = transform_feature(raw)
            if transformed is None:
                invalid += 1
                continue
            stable_id = transformed["properties"]["segment_id"]
            if stable_id in seen:
                continue
            seen.add(stable_id)
            features.append(transformed)
            if len(features) >= profile.max_features:
                truncated = True
                break
        if len(features) >= profile.max_features:
            break

    return NwbFetchResult(
        feature_collection={
            "type": "FeatureCollection",
            "features": features,
            "metadata": {
                "detail": profile.name,
                "truncated": truncated,
                "invalidFeatures": invalid,
            },
        },
        truncated=truncated,
        invalid_features=invalid,
    )


async def _fetch_manager_subset(
    client: httpx.AsyncClient,
    url: str,
    bbox: Sequence[float],
    manager_type: str | None,
    cap: int,
) -> tuple[list[Mapping[str, Any]], bool]:
    params: dict[str, str | int] | None = build_query_params(bbox, manager_type)
    next_url = url
    features: list[Mapping[str, Any]] = []
    truncated = False

    while next_url and len(features) < cap:
        response = await client.get(
            next_url,
            params=params,
            headers={"Accept": "application/geo+json"},
        )
        response.raise_for_status()
        payload = response.json()
        page_features = payload.get("features")
        if not isinstance(page_features, list):
            raise ValueError("PDOK response does not contain a features array")
        remaining = cap - len(features)
        features.extend(item for item in page_features[:remaining] if isinstance(item, Mapping))
        if len(page_features) > remaining:
            truncated = True
            next_url = None
        else:
            next_url = _next_link(payload)
        params = None  # the absolute next link already contains the cursor and original query

    return features, truncated or bool(next_url)


def _next_link(payload: Mapping[str, Any]) -> str | None:
    links = payload.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, Mapping) and link.get("rel") == "next":
            href = link.get("href")
            if isinstance(href, str) and href.startswith("https://"):
                return href
    return None


def transform_feature(raw: Mapping[str, Any]) -> NwbRoadSegment | None:
    """Convert a PDOK feature to the stable internal road-segment GeoJSON model."""

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

    frc = _optional_int(props.get("frc"))
    road_manager_type = _optional_str(props.get("wegbehsrt"))
    if frc is not None and frc <= 2:
        road_class = "motorway"
    elif frc is not None and frc <= 4:
        road_class = "primary"
    elif road_manager_type in {"R", "P"}:
        road_class = "primary"
    else:
        road_class = "local"

    properties: RoadSegmentProperties = {
        "segment_id": source_id,
        "nwb_road_section_id": _optional_int(props.get("wvk_id")),
        "begin_junction_id": _optional_int(props.get("jte_id_beg")),
        "end_junction_id": _optional_int(props.get("jte_id_end")),
        # `wegnummer` is often only the zero-padded numeric value ("005").
        # `wegnr_hmp` preserves the public road name used by NDW ("A5").
        "road_number": _road_number(props),
        "street_name": _optional_str(props.get("stt_naam")),
        "road_manager_type": road_manager_type,
        "road_manager_name": _optional_str(props.get("wegbehnaam")),
        "direction": _optional_str(props.get("rijrichtng")),
        "administrative_direction": _optional_str(props.get("admrichtng")),
        "carriageway_position": _optional_str(props.get("rpe_code")),
        "position_to_orientation_line": _optional_str(props.get("pos_tv_wol")),
        "carriageway_type": _optional_str(props.get("bst_code")),
        "frc": frc,
        "form_of_way": _optional_int(props.get("fow")),
        "openlr": _optional_str(props.get("openlr")),
        "begin_km": _optional_float(props.get("beginkm")),
        "end_km": _optional_float(props.get("eindkm")),
        "length_m": _optional_float(props.get("st_lengthshape")),
        "valid_from": _optional_str(props.get("wvk_begdat")),
        "road_class": road_class,
        # Reserved for future live observations; styling can switch this property
        # without replacing the NWB source or its stable segment identifiers.
        "traffic_state": None,
    }
    return {"type": "Feature", "id": source_id, "geometry": geometry, "properties": properties}


def matching_keys(feature: Mapping[str, Any]) -> dict[str, Any]:
    """Return explicit identifiers a future live-traffic matcher should prefer."""

    props = feature.get("properties")
    if not isinstance(props, Mapping):
        return {}
    return {
        key: props.get(key)
        for key in (
            "segment_id",
            "nwb_road_section_id",
            "openlr",
            "road_number",
            "direction",
            "carriageway_position",
        )
        if props.get(key) is not None
    }


def _valid_multiline(value: Any) -> GeoJson | None:
    if not isinstance(value, Mapping) or value.get("type") not in {"LineString", "MultiLineString"}:
        return None
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, list):
        return None
    lines = [coordinates] if value.get("type") == "LineString" else coordinates
    valid_lines: list[list[list[float]]] = []
    for line in lines:
        if not isinstance(line, list):
            continue
        valid_points: list[list[float]] = []
        for point in line:
            if (
                isinstance(point, list)
                and len(point) >= 2
                and isinstance(point[0], (int, float))
                and isinstance(point[1], (int, float))
                and math.isfinite(point[0])
                and math.isfinite(point[1])
                and -180 <= point[0] <= 180
                and -90 <= point[1] <= 90
            ):
                valid_points.append([float(point[0]), float(point[1])])
        if len(valid_points) >= 2:
            valid_lines.append(valid_points)
    return {"type": "MultiLineString", "coordinates": valid_lines} if valid_lines else None


def _optional_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() not in {"#"} else None


def _road_number(props: Mapping[str, Any]) -> str | None:
    return (
        _optional_str(props.get("wegnr_hmp"))
        or _optional_str(props.get("wegnr_aw"))
        or _optional_str(props.get("wegnummer"))
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        result = float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
    return result if result is not None and math.isfinite(result) else None
