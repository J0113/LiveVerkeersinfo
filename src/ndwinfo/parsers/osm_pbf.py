"""Parser for Geofabrik OSM PBF extracts: driving-road ways only.

The country extract is read in two passes.  The first pass retains only the
selected driving ways and their referenced node ids; the second resolves
coordinates only for those ids.  This is deliberately slower than
``with_locations("sparse_mem_array")``, but avoids allocating an index for
every node in the Netherlands extract and keeps the import within Docker's
memory limit.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import osmium
from shapely.geometry import LineString

ROAD_HIGHWAY_TYPES = {
    "motorway", "trunk", "primary", "secondary",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
}


def _way_row(
    osm_id: int,
    tags: dict[str, str],
    wkt: str | None,
    node_refs: tuple[int, ...] | None = None,
) -> dict[str, Any] | None:
    """Pure transform: filter to driving-road ways, shape tags/geometry into a row dict.

    tags is stored verbatim (unfiltered) in raw -- the full OSM tag set is
    the "store all tags" source of truth the API spreads into properties.
    """
    highway = tags.get("highway")
    if highway not in ROAD_HIGHWAY_TYPES or not wkt:
        return None
    return {
        "osm_id": osm_id,
        "highway": highway,
        "name": tags.get("name"),
        "ref": tags.get("ref"),
        "node_refs": list(node_refs) if node_refs is not None else None,
        "geom": wkt,
        "raw": dict(tags),
    }


def parse_roads(path: Path) -> Iterator[dict[str, Any]]:
    """Yield driving-road way dicts without indexing every node in the PBF."""
    pending_ways: list[tuple[int, dict[str, str], tuple[int, ...]]] = []
    unresolved_node_ids: set[int] = set()

    # Pass 1: node objects are ignored.  Retain just the relatively small
    # subset of ways that the application serves and the ids they reference.
    for obj in osmium.FileProcessor(str(path)):
        if not obj.is_way():
            continue
        tags = dict(obj.tags)
        if tags.get("highway") not in ROAD_HIGHWAY_TYPES:
            continue
        node_refs = tuple(node.ref for node in obj.nodes)
        if len(node_refs) < 2:
            continue
        pending_ways.append((obj.id, tags, node_refs))
        unresolved_node_ids.update(node_refs)

    node_use_count: Counter[int] = Counter()
    for _osm_id, _tags, node_refs in pending_ways:
        node_use_count.update(set(node_refs))
    shared_node_ids = {
        node_id for node_id, use_count in node_use_count.items() if use_count > 1
    }

    # Pass 2: resolve only nodes used by retained ways.  Discarding ids as
    # they are found prevents keeping both a full id set and coordinate map
    # alive for the entire node section.
    locations: dict[int, tuple[float, float]] = {}
    for obj in osmium.FileProcessor(str(path)):
        if not obj.is_node() or obj.id not in unresolved_node_ids:
            continue
        location = obj.location
        if not location.valid():
            continue
        locations[obj.id] = (location.lon, location.lat)
        unresolved_node_ids.discard(obj.id)

    for osm_id, tags, node_refs in pending_ways:
        try:
            coordinates = [locations[node_id] for node_id in node_refs]
            wkt = LineString(coordinates).wkt
        except (KeyError, ValueError):
            # Truncated/corrupt extracts or ways with unresolved coordinates
            # are skipped, matching the old WKTFactory behaviour.
            wkt = None
        row = _way_row(osm_id, tags, wkt, node_refs)
        if row:
            # Transient build context, removed by the ingester before the
            # osm_road upsert. Keeping it per way lets lane topology stream.
            row["_shared_node_ids"] = set(node_refs) & shared_node_ids
            yield row
