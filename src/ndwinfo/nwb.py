"""Future live-traffic-matching extension point for NWB road segments.

Road geometry itself lives in PostGIS (`NwbRoadSegment` in models.py, ingested
from RWS's Wegvakken.gpkg by `ingest/nwb.py`) and is served by
`api/routers/nwb.py`. This module holds only the matching-observation shape a
future NDW-to-NWB matcher should target — kept separate so it has no
dependency on either the ingest pipeline or a particular live-traffic source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


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
