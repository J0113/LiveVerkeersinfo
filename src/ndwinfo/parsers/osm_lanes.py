"""Derive per-lane offset geometry from an osm_road way + its lanes tag.

osm_road.geom is WGS84 (EPSG:4326) -- offsetting there would mean offsetting
by degrees, not metres. Mirrors parsers/weggeg.py's approach: transform to
RD/EPSG:28992 (metres), offset + taper there, transform back to WGS84.

Direction model is deliberately conservative -- see docs/11-osm-pbf.md. Only
`merge_to_left`/`merge_to_right` turn:lanes tokens taper a lane; only
explicit lanes:forward/backward/both_ways tags get directional treatment on
two-way roads (no lane count is ever guessed).
"""

from __future__ import annotations

from typing import Optional

from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.ops import substring, transform

WIDTH_BY_HIGHWAY = {
    "motorway": 3.5, "motorway_link": 3.5,
    "trunk": 3.5, "trunk_link": 3.5,
    "primary": 3.5, "primary_link": 3.5,
    "secondary": 2.75, "secondary_link": 2.75,
}
MAX_TAPER_M = 150.0
_TAPERING_ROLES = ("merge_left", "merge_right")
_FORWARD_ONEWAY = {"yes", "true", "1"}
_SKIP_ONEWAY = {"reversible", "alternating"}

_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)


def _split_turn_lanes(value: Optional[str]) -> Optional[list[set[str]]]:
    """Split a turn:lanes-style value into one token-set per lane, left to right."""
    if not value:
        return None
    return [set(part.split(";")) for part in value.split("|")]


def _role_for_token(tokens: Optional[set[str]]) -> str:
    if tokens is None:
        return "normal"
    if "merge_to_left" in tokens:
        return "merge_left"
    if "merge_to_right" in tokens:
        return "merge_right"
    return "normal"


def _offset_geometry(line: LineString, offset_m: float):
    if offset_m == 0:
        return line
    return line.offset_curve(offset_m)


def _taper(line, taper_from: str):
    """Shorten an offset line by the taper amount at its start or end."""
    if isinstance(line, MultiLineString):
        # Merge-tagged lanes are drawn on simple, mostly-straight motorway
        # geometry in practice; a multipart offset result here means the
        # source curve was unusual enough that trimming isn't reliable --
        # leave it untapered rather than guess which part to shorten.
        return line
    length = line.length
    if length <= 0:
        return line
    taper_len = min(MAX_TAPER_M, length * 0.5)
    if taper_from == "end":
        return substring(line, 0, length - taper_len)
    return substring(line, taper_len, length)


class _Lane:
    __slots__ = ("offset_idx", "direction", "role", "taper_from")

    def __init__(self, offset_idx: float, direction: str, role: str, taper_from: Optional[str]):
        self.offset_idx = offset_idx  # position across the cross-section, left(+) to right(-), in lane units
        self.direction = direction
        self.role = role
        self.taper_from = taper_from


def _oneway_lanes(total: int, turn_tokens: Optional[list[set[str]]]) -> list[_Lane]:
    # Tag present but count doesn't match the lane count -- untrustworthy,
    # distinct from "no turn:lanes tag at all" (role='normal' below).
    mismatched = turn_tokens is not None and len(turn_tokens) != total
    if mismatched:
        turn_tokens = None
    lanes = []
    for lane in range(1, total + 1):
        if mismatched:
            role = "unknown"
        elif turn_tokens is not None:
            role = _role_for_token(turn_tokens[lane - 1])
        else:
            role = "normal"
        offset_idx = (total + 1) / 2 - lane
        taper_from = "end" if role in _TAPERING_ROLES else None
        lanes.append(_Lane(offset_idx, "fwd", role, taper_from))
    return lanes


def _two_way_lanes(tags: dict) -> Optional[list[_Lane]]:
    has_fwd = "lanes:forward" in tags
    has_bwd = "lanes:backward" in tags
    has_both = "lanes:both_ways" in tags
    if not (has_fwd or has_bwd or has_both):
        return None  # caller falls back to the undirected case

    def _int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    n_bwd = _int(tags.get("lanes:backward"))
    n_both = _int(tags.get("lanes:both_ways"))
    n_fwd = _int(tags.get("lanes:forward"))
    total = n_bwd + n_both + n_fwd
    if total < 1:
        return None

    fwd_tokens = _split_turn_lanes(tags.get("turn:lanes:forward"))
    fwd_mismatched = fwd_tokens is not None and len(fwd_tokens) != n_fwd
    if fwd_mismatched:
        fwd_tokens = None
    bwd_tokens = _split_turn_lanes(tags.get("turn:lanes:backward"))
    bwd_mismatched = bwd_tokens is not None and len(bwd_tokens) != n_bwd
    if bwd_mismatched:
        bwd_tokens = None

    lanes: list[_Lane] = []
    # Physical left-to-right layout: backward block (outermost = leftmost),
    # then the shared both_ways lane, then forward block (innermost first).
    # NL drives right: forward lanes sit on the right (negative offset) half.
    for idx in range(total):
        offset_idx = (total - 1) / 2 - idx
        if idx < n_bwd:
            # turn:lanes:backward is ordered left-to-right from the backward
            # driver's perspective, which faces the opposite way -- their
            # left is physically the lane closest to the centreline. That's
            # the reverse of our outermost-first physical ordering.
            pos_in_block = idx  # 0 = outermost/leftmost physically
            token_idx = n_bwd - 1 - pos_in_block
            if bwd_mismatched:
                role = "unknown"
            elif bwd_tokens is not None:
                role = _role_for_token(bwd_tokens[token_idx])
            else:
                role = "normal"
            # A backward lane merges as that traffic travels -- i.e. toward
            # the start of the digitized way, not the end.
            taper_from = "start" if role in _TAPERING_ROLES else None
            lanes.append(_Lane(offset_idx, "bwd", role, taper_from))
        elif idx < n_bwd + n_both:
            lanes.append(_Lane(offset_idx, "unknown", "both_ways", None))
        else:
            pos_in_block = idx - n_bwd - n_both  # 0 = closest to centre, matches turn:lanes:forward order
            if fwd_mismatched:
                role = "unknown"
            elif fwd_tokens is not None:
                role = _role_for_token(fwd_tokens[pos_in_block])
            else:
                role = "normal"
            taper_from = "end" if role in _TAPERING_ROLES else None
            lanes.append(_Lane(offset_idx, "fwd", role, taper_from))
    return lanes


def _undirected_lanes(total: int) -> list[_Lane]:
    lanes = []
    for lane in range(1, total + 1):
        offset_idx = (total + 1) / 2 - lane
        lanes.append(_Lane(offset_idx, "unknown", "unknown", None))
    return lanes


def make_lane_rows(osm_id: int, highway: str, tags: dict, line: LineString) -> list[dict]:
    """Expand one osm_road way into offset per-lane rows (WGS84 out)."""
    width = WIDTH_BY_HIGHWAY.get(highway)
    lanes_tag = tags.get("lanes")
    if width is None or not lanes_tag or line is None or line.is_empty:
        return []
    try:
        total = int(lanes_tag)
    except ValueError:
        return []
    if total < 1:
        return []

    oneway = tags.get("oneway")
    if oneway in _SKIP_ONEWAY:
        return []

    rd_line = transform(_WGS84_TO_RD.transform, line)

    if oneway in _FORWARD_ONEWAY:
        lanes = _oneway_lanes(total, _split_turn_lanes(tags.get("turn:lanes")))
    elif oneway == "-1":
        rd_line = LineString(list(rd_line.coords)[::-1])
        lanes = _oneway_lanes(total, _split_turn_lanes(tags.get("turn:lanes")))
    else:
        # No oneway tag, or oneway=no: two-way. Checked against real
        # Noord-Holland data before assuming this -- no highway class here
        # relies on an implicit highway=motorway-means-oneway default;
        # motorway is always tagged oneway=yes explicitly in this extract.
        lanes = _two_way_lanes(tags)
        if lanes is None:
            lanes = _undirected_lanes(total)

    rows: list[dict] = []
    for lane in lanes:
        offset_m = lane.offset_idx * width
        lane_geom = _offset_geometry(rd_line, offset_m)
        if lane.taper_from and isinstance(lane_geom, LineString):
            lane_geom = _taper(lane_geom, lane.taper_from)
        if lane_geom.is_empty:
            continue
        wgs84_geom = transform(_RD_TO_WGS84.transform, lane_geom)
        rows.append({
            "lane_offset_idx": lane.offset_idx,  # internal, dropped below before insert
            "direction": lane.direction,
            "role": lane.role,
            "highway": highway,
            "name": tags.get("name"),
            "ref": tags.get("ref"),
            "width_m": width,
            "geom": wgs84_geom.wkt,
            "raw": {"lanes_tag": lanes_tag, "oneway": oneway},
        })

    # Assign stable, physically-ordered lane numbers (1 = leftmost) per
    # direction group, now that tapering/emptiness has been resolved.
    _number_lanes(rows)
    for row in rows:
        row["source_id"] = osm_id
        row["lane_count"] = sum(1 for r in rows if r["direction"] == row["direction"])
        row["id"] = f"{osm_id}:{row['direction']}:{row['lane']}"
        del row["lane_offset_idx"]
    return rows


def _number_lanes(rows: list[dict]) -> None:
    """Number lanes 1..N left to right within each direction group."""
    by_direction: dict[str, list[dict]] = {}
    for row in rows:
        by_direction.setdefault(row["direction"], []).append(row)
    for group in by_direction.values():
        group.sort(key=lambda r: -r["lane_offset_idx"])  # left (most positive) first
        for i, row in enumerate(group, start=1):
            row["lane"] = i
