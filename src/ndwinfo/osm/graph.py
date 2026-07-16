"""Build deterministic directed graph edges from streamed OSM ways."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from ndwinfo.osm.tags import material_signature, normalize_way_tags, travel_directions

Coordinate = tuple[float, float]


@dataclass(frozen=True)
class DirectedSegment:
    internal_segment_id: str
    osm_way_id: int
    osm_version: int | None
    sequence: int
    source_from_node_id: int
    source_to_node_id: int
    from_node_id: str
    to_node_id: str
    travel_direction: str
    coordinates: tuple[Coordinate, ...]
    length_m: float
    normalized_tags: dict
    tags: dict[str, str]


def build_directed_segments(
    *,
    way_id: int,
    way_version: int | None,
    node_ids: Sequence[int],
    coordinates: Sequence[Coordinate],
    tags: Mapping[str, str],
    split_node_ids: set[int] | frozenset[int],
) -> list[DirectedSegment]:
    """Split a way at shared graph nodes and emit each legal direction.

    OSM tags apply to the complete way.  Material attribute changes therefore
    occur at the boundary between ways; splitting at every shared node makes
    those boundaries explicit without inventing sub-way tag transitions.
    """
    if len(node_ids) != len(coordinates) or len(node_ids) < 2:
        return []

    boundaries = [0]
    boundaries.extend(
        index
        for index, node_id in enumerate(node_ids[1:-1], start=1)
        if node_id in split_node_ids
    )
    boundaries.append(len(node_ids) - 1)

    segments: list[DirectedSegment] = []
    sequence = 0
    clean_tags = {str(key): str(value) for key, value in tags.items()}
    for start, end in zip(boundaries, boundaries[1:]):
        if end <= start:
            continue
        source_nodes = tuple(int(value) for value in node_ids[start : end + 1])
        source_coords = tuple(
            (float(lon), float(lat)) for lon, lat in coordinates[start : end + 1]
        )
        if len(set(source_coords)) < 2:
            continue

        for direction in travel_directions(tags):
            directed_nodes = (
                tuple(reversed(source_nodes)) if direction.reverse_geometry else source_nodes
            )
            directed_coords = (
                tuple(reversed(source_coords)) if direction.reverse_geometry else source_coords
            )
            normalized = normalize_way_tags(tags, direction.name)
            segment_id = stable_segment_id(
                way_id=way_id,
                sequence=sequence,
                source_from_node_id=source_nodes[0],
                source_to_node_id=source_nodes[-1],
                travel_direction=direction.name,
                signature=material_signature(tags, direction.name),
            )
            segments.append(
                DirectedSegment(
                    internal_segment_id=segment_id,
                    osm_way_id=int(way_id),
                    osm_version=way_version,
                    sequence=sequence,
                    source_from_node_id=source_nodes[0],
                    source_to_node_id=source_nodes[-1],
                    from_node_id=internal_node_id(directed_nodes[0]),
                    to_node_id=internal_node_id(directed_nodes[-1]),
                    travel_direction=direction.name,
                    coordinates=directed_coords,
                    length_m=linestring_length_m(directed_coords),
                    normalized_tags=normalized,
                    tags=clean_tags,
                )
            )
        sequence += 1
    return segments


def stable_segment_id(
    *,
    way_id: int,
    sequence: int,
    source_from_node_id: int,
    source_to_node_id: int,
    travel_direction: str,
    signature: str,
) -> str:
    """Content-addressed ID stable across graph snapshots for unchanged edges."""
    identity = json.dumps(
        [
            way_id,
            sequence,
            source_from_node_id,
            source_to_node_id,
            travel_direction,
            signature,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "osms_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def internal_node_id(osm_node_id: int) -> str:
    return f"osmn_{int(osm_node_id)}"


def linestring_length_m(coordinates: Sequence[Coordinate]) -> float:
    """Haversine length; sufficiently accurate for graph plausibility checks."""
    radius_m = 6_371_008.8
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coordinates, coordinates[1:]):
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        a = min(1.0, max(0.0, a))
        total += radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return total
