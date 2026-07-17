"""Parser for Geofabrik OSM PBF extracts: driving-road ways only.

Streams a province/country .osm.pbf with pyosmium's FileProcessor (not
SimpleHandler -- its way()/node() callbacks can't yield to an outer
iterator). with_locations() resolves way geometry from node coordinates in
one pass, caching node locations in a sparse in-memory index sized for the
extract's node count (verified ~910MB peak RSS for the ~18.6M-node
Noord-Holland extract; re-benchmark before pointing this at a full-country
extract).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import osmium
import osmium.geom

ROAD_HIGHWAY_TYPES = {
    "motorway", "trunk", "primary", "secondary",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
}


def _way_row(osm_id: int, tags: dict[str, str], wkt: str | None) -> dict[str, Any] | None:
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
        "geom": wkt,
        "raw": dict(tags),
    }


def parse_roads(path: Path) -> Iterator[dict[str, Any]]:
    """Stream driving-road way dicts from a local .osm.pbf extract."""
    wkt_factory = osmium.geom.WKTFactory()
    processor = osmium.FileProcessor(str(path)).with_locations("sparse_mem_array")
    for obj in processor:
        if not obj.is_way():
            continue
        try:
            wkt = wkt_factory.create_linestring(obj)
        except Exception:
            wkt = None  # e.g. way with fewer than 2 resolved node locations
        row = _way_row(obj.id, dict(obj.tags), wkt)
        if row:
            yield row
