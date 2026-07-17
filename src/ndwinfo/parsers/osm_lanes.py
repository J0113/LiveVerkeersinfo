"""Derive per-lane offset geometry from an osm_road way + its lanes tag.

osm_road.geom is WGS84 (EPSG:4326) -- offsetting there would mean offsetting
by degrees, not metres. Mirrors parsers/weggeg.py's approach: transform to
RD/EPSG:28992 (metres), offset + converge there, transform back to WGS84.

Direction model is deliberately conservative -- see docs/11-osm-pbf.md. Only
`merge_to_left`/`merge_to_right` turn:lanes tokens reshape a lane; only
explicit lanes:forward/backward/both_ways tags get directional treatment on
two-way roads (no lane count is ever guessed).

A merging lane converges into its neighbour rather than being cut short, and
the convergence is spread over the whole chain of consecutive merge-tagged
ways (see build_merge_index) -- a per-way converge would zigzag wherever OSM
splits one physical merge across two ways.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.ops import substring, transform

WIDTH_BY_HIGHWAY = {
    "motorway": 3.5, "motorway_link": 3.5,
    "trunk": 3.5, "trunk_link": 3.5,
    "primary": 3.5, "primary_link": 3.5,
    "secondary": 2.75, "secondary_link": 2.75,
}
# How long the cross-section takes to re-centre, at most. Checked against
# aerial imagery: the paint holds a full lane's width until close to the
# merge point and then pinches over a short taper -- 150m spread the drift
# so far back that lanes read as too narrow along most of a ramp.
MAX_TAPER_M = 25.0
TAPER_SAMPLE_M = 2.0  # vertex spacing inside the converging section
MAX_TAPER_SAMPLES = 200
# Lane offsets run left(+) to right(-) across the cross-section, so the
# neighbour a lane merges into sits one lane-unit toward that side. Membership
# here is also the "is this a merge role" test.
_MERGE_TARGET_DELTA = {"merge_left": 1.0, "merge_right": -1.0}
_MERGE_TOKEN = "merge_to_"
_TURN_KEYS = ("turn:lanes", "turn:lanes:forward", "turn:lanes:backward")
_FORWARD_ONEWAY = {"yes", "true", "1"}
_SKIP_ONEWAY = {"reversible", "alternating"}

_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)


def has_merge_tokens(tags: dict) -> bool:
    """Cheap tag-only test: does this way need the merge index to be built?

    Lets the ingester keep streaming non-merging ways straight through and
    buffer only the (few hundred) merge ways that need cross-way context.
    """
    return any(_MERGE_TOKEN in (tags.get(key) or "") for key in _TURN_KEYS)


def split_turn_lanes(value: Optional[str]) -> Optional[list[set[str]]]:
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


def _turn_value(tokens: Optional[set[str]]) -> Optional[str]:
    """Canonical `turn` property for a lane: sorted tokens joined by ';', or None.

    Matches the connector rows' `raw.turn`, so both answer the same question
    ("what movement does this lane make") in the same shape. An empty token --
    `turn:lanes=|through` leaves lane 1 blank -- carries no indication, so it
    reads as absent rather than as a movement.
    """
    if not tokens:
        return None
    return ";".join(sorted(t for t in tokens if t)) or None


class _Lane:
    __slots__ = (
        "offset_idx", "end_offset_idx", "direction", "role", "travel_dir", "turn",
        "divider_left",
    )

    def __init__(
        self,
        offset_idx: float,
        direction: str,
        role: str,
        travel_dir: int,
        turn: Optional[set[str]] = None,
    ):
        self.offset_idx = offset_idx  # position across the cross-section, left(+) to right(-), in lane units
        self.end_offset_idx = offset_idx  # where it sits once the merge completes; see _resolve_merge_transition
        self.direction = direction
        self.role = role
        self.travel_dir = travel_dir  # +1 = travels toward the line's end, -1 = toward its start
        self.turn = turn  # this lane's turn:lanes token set, or None if untagged/untrustworthy
        self.divider_left = False  # see _mark_dividers


def _oneway_lanes(total: int, turn_tokens: Optional[list[set[str]]]) -> list[_Lane]:
    # Tag present but count doesn't match the lane count -- untrustworthy,
    # distinct from "no turn:lanes tag at all" (role='normal' below).
    mismatched = turn_tokens is not None and len(turn_tokens) != total
    if mismatched:
        turn_tokens = None
    lanes = []
    for lane in range(1, total + 1):
        tokens = None if turn_tokens is None else turn_tokens[lane - 1]
        role = "unknown" if mismatched else _role_for_token(tokens)
        offset_idx = (total + 1) / 2 - lane
        lanes.append(_Lane(offset_idx, "fwd", role, 1, tokens))
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

    fwd_tokens = split_turn_lanes(tags.get("turn:lanes:forward"))
    fwd_mismatched = fwd_tokens is not None and len(fwd_tokens) != n_fwd
    if fwd_mismatched:
        fwd_tokens = None
    bwd_tokens = split_turn_lanes(tags.get("turn:lanes:backward"))
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
            tokens = None if bwd_tokens is None else bwd_tokens[token_idx]
            role = "unknown" if bwd_mismatched else _role_for_token(tokens)
            # Backward traffic travels toward the start of the digitized way,
            # so that's where its merge completes.
            lanes.append(_Lane(offset_idx, "bwd", role, -1, tokens))
        elif idx < n_bwd + n_both:
            lanes.append(_Lane(offset_idx, "unknown", "both_ways", 1))
        else:
            pos_in_block = idx - n_bwd - n_both  # 0 = closest to centre, matches turn:lanes:forward order
            tokens = None if fwd_tokens is None else fwd_tokens[pos_in_block]
            role = "unknown" if fwd_mismatched else _role_for_token(tokens)
            lanes.append(_Lane(offset_idx, "fwd", role, 1, tokens))
    return lanes


def _undirected_lanes(total: int) -> list[_Lane]:
    """Two-way road with a lane count but no lanes:forward/backward split.

    An even count needs no tag to resolve: NL drives on the right, so the left
    half of the cross-section is oncoming and the right half runs forward. This
    is the same rule _assumed_two_way_lanes applies to a road with no `lanes` at
    all, just with a real count behind it -- and it's the common case, covering
    1,646 of this extract's 1,696 two-way `lanes=2` ways (only 50 carry a
    directional split).

    An odd count can't be halved, and which direction gets the extra lane isn't
    derivable: `lanes=1` is one lane shared both ways, and `lanes=3` could be
    2+1 either way round (a centre turn lane only if lanes:both_ways says so --
    see _two_way_lanes). Those stay direction-unknown rather than guess.
    """
    if total % 2:
        return [
            _Lane((total + 1) / 2 - lane, "unknown", "unknown", 1)
            for lane in range(1, total + 1)
        ]

    lanes = []
    half = total // 2
    for lane in range(1, total + 1):
        offset_idx = (total + 1) / 2 - lane
        # Lane 1 is the physically leftmost, so the first half is the oncoming
        # block. Backward traffic travels toward the way's first coordinate.
        backward = lane <= half
        lanes.append(_Lane(
            offset_idx,
            "bwd" if backward else "fwd",
            "normal",
            -1 if backward else 1,
        ))
    return lanes


def _assumed_two_way_lanes() -> list[_Lane]:
    """One lane each way -- what an untagged two-way road means in OSM."""
    return [_Lane(0.5, "bwd", "normal", -1), _Lane(-0.5, "fwd", "normal", 1)]


def _mark_dividers(lanes: list[_Lane]) -> None:
    """Flag which lanes carry a divider on their LEFT edge.

    Decided here rather than in the style because it's a question about a lane's
    *neighbour*, which a per-feature filter can't see. Each internal boundary is
    drawn once, as the left edge of the lane on its right:

    - the leftmost lane of the whole cross-section has no divider; that edge is
      the outside of the carriageway, which the style draws as a casing under
      the bands.
    - a boundary two lanes are merging across gets none either. The merging
      lane's edge sweeps sideways as it converges, so a line on it would drag a
      diagonal across the carriageway -- the merge arrows carry that meaning.
      Checked in both directions: `merge_left` crosses its own left edge, but
      `merge_right` crosses its right-hand neighbour's.
    """
    ordered = sorted(lanes, key=lambda lane: lane.offset_idx, reverse=True)  # left to right
    for idx, lane in enumerate(ordered):
        if idx == 0:
            lane.divider_left = False
            continue
        left = ordered[idx - 1]
        merging_across = (
            _merge_target(lanes, lane) is left or _merge_target(lanes, left) is lane
        )
        lane.divider_left = not merging_across


def _int_lanes(value) -> Optional[int]:
    try:
        total = int(value)
    except (TypeError, ValueError):
        return None
    return total if total >= 1 else None


def _plan_lanes(highway: str, tags: dict) -> Optional[tuple[float, list[_Lane], int, bool]]:
    """Tag-only cross-section plan: (lane width, lanes, frame_flip, assumed), or None.

    frame_flip is -1 for oneway=-1, whose lanes are laid out against a
    reversed copy of the way -- callers that key anything off the way's own
    coordinate order must multiply a lane's travel_dir by it.

    assumed marks a cross-section that OSM's defaults imply rather than tag
    (see below); it surfaces as `lanes_assumed` on the lane's properties so a
    consumer can tell a drawn lane from a counted one.
    """
    width = WIDTH_BY_HIGHWAY.get(highway)
    if width is None:
        return None
    oneway = tags.get("oneway")
    if oneway in _SKIP_ONEWAY:
        return None

    total = _int_lanes(tags.get("lanes"))

    if oneway in _FORWARD_ONEWAY or oneway == "-1":
        flip = -1 if oneway == "-1" else 1
        turn_tokens = split_turn_lanes(tags.get("turn:lanes"))
        assumed = False
        if total is None:
            if turn_tokens:
                total = len(turn_tokens)  # one token per lane, by definition -- a count, not a guess
            else:
                total, assumed = 1, True  # an untagged oneway road is one lane
        return width, _oneway_lanes(total, turn_tokens), flip, assumed

    # No oneway tag, or oneway=no: two-way. Checked against real
    # Noord-Holland data before assuming this -- no highway class here
    # relies on an implicit highway=motorway-means-oneway default;
    # motorway is always tagged oneway=yes explicitly in this extract.
    # An explicit directional split carries its own total, so it wins over `lanes`.
    lanes = _two_way_lanes(tags)
    if lanes is not None:
        return width, lanes, 1, False
    if total is not None:
        return width, _undirected_lanes(total), 1, False
    return width, _assumed_two_way_lanes(), 1, True


def _merge_target(lanes: list[_Lane], lane: _Lane) -> Optional[_Lane]:
    """The lane this one merges into, or None if there isn't one."""
    delta = _MERGE_TARGET_DELTA.get(lane.role)
    if delta is None:
        return None
    # "left"/"right" are the merging driver's, and a backward lane's driver
    # faces the other way -- their left is the decreasing-offset side, not the
    # increasing one. (Their token *ordering* is already reversed separately in
    # _two_way_lanes; this is the orthogonal question of which way they move.)
    wanted = lane.offset_idx + delta * lane.travel_dir
    for other in lanes:
        if other.direction == lane.direction and abs(other.offset_idx - wanted) < 1e-9:
            return other
    return None  # merge tagged on an edge lane with nothing beside it


def _resolve_merge_transition(lanes: list[_Lane]) -> Optional[int]:
    """Fill in every lane's end_offset_idx, and say where the merge completes.

    Lane offsets are measured from the way's own line, and OSM draws that line
    down the middle of the carriageway it describes -- so once a lane merges
    away, the *next* way's line is the centre of a cross-section one lane
    narrower, and it meets this way's line at their shared node. Holding the
    surviving lanes at their original offsets therefore leaves them half a lane
    width off wherever a chain ends (413 chain ends in this extract go N->N-1).
    So the whole cross-section re-centres onto what survives, and each merging
    lane lands on its target's *final* position.

    Returns the merge's travel direction — a property of the way, not of each
    lane, since every lane re-centres toward the same end of it — or None if
    nothing merges (in which case lanes keep their offsets for the full length).
    """
    merging = [lane for lane in lanes if _merge_target(lanes, lane) is not None]
    if not merging:
        return None
    directions = {lane.travel_dir for lane in merging}
    if len(directions) != 1:
        return None  # a forward and a backward merge would re-centre toward opposite ends
    survivors = sorted(
        (lane for lane in lanes if lane not in merging), key=lambda lane: -lane.offset_idx
    )
    if not survivors:
        return None  # every lane merges away; nothing to re-centre onto

    for i, lane in enumerate(survivors):
        lane.end_offset_idx = (len(survivors) - 1) / 2 - i
    for lane in merging:
        # Follow the targets to a survivor: a lane can merge into a lane that
        # is itself merging, and only a survivor has a final position yet.
        target = _merge_target(lanes, lane)
        for _ in range(len(lanes)):
            if target is None or target not in merging:
                break
            target = _merge_target(lanes, target)
        lane.end_offset_idx = target.end_offset_idx if target is not None else lane.offset_idx
    return directions.pop()


def _pt(coord) -> tuple[float, float]:
    # OSM ways share node objects, so chained ways' endpoints are bit-identical
    # in the source; rounding only guards float noise from the WKT round-trip.
    return (round(coord[0], 7), round(coord[1], 7))


def _walk(keys, succ_of: dict):
    """Yield (start, path_to_an_already-resolved-node_or_end, terminator).

    Iterative: recursion would risk Python's depth limit on a long chain.
    terminator is None at a chain end, an already-memoized key where this path
    joins one that's resolved, or a repeated key if the links form a cycle
    (which real data shouldn't produce, but a bad extract must not hang on).
    """
    resolved: set = set()
    for start in keys:
        if start in resolved:
            continue
        path, seen = [], set()
        cur = start
        while cur is not None and cur not in resolved and cur not in seen:
            seen.add(cur)
            path.append(cur)
            cur = succ_of.get(cur)
        yield path, cur
        resolved.update(path)


def _downstream_lengths(keys, succ_of: dict, length_of: dict) -> dict:
    """dist[k] = total length of every way downstream of k, k itself excluded."""
    memo: dict = {}
    for path, _terminator in _walk(keys, succ_of):
        for key in reversed(path):
            nxt = succ_of.get(key)
            memo[key] = 0.0 if nxt is None or nxt not in memo else length_of[nxt] + memo[nxt]
    return memo


def _merge_points(keys, succ_of: dict) -> dict:
    """point[k] = the last way in k's chain, i.e. where its merge completes."""
    memo: dict = {}
    for path, terminator in _walk(keys, succ_of):
        if terminator is None:
            end = path[-1]
        elif terminator in memo:
            end = memo[terminator]  # joins a chain already resolved
        else:
            end = terminator  # cycle: break it here rather than loop forever
        for key in path:
            memo[key] = end
    return memo


def build_merge_index(ways: Iterable[tuple[int, str, dict, LineString]]) -> dict:
    """Map (osm_id, merge_dir) -> (distance_to_merge_point, taper_length).

    OSM routinely splits one physical merge across several consecutive ways
    (e.g. A200 ways 7400291 -> 1014194650, both tagged `merge_to_right|`).
    Converging per-way would snap the lane back to full offset at every way
    boundary, so the chain is resolved first: distance_to_merge_point is
    measured from the way's own travel-end to where the chain actually ends,
    and taper_length is min(MAX_TAPER_M, whole chain length) -- identical for
    every way in a chain, so the convergence is continuous across it.

    merge_dir is relative to the way's own coordinate order (+1 = merge
    completes at the last coordinate). It's keyed per way, not per lane: the
    whole cross-section re-centres over the same stretch, so a way's surviving
    lanes need this context just as much as its merging ones.

    `ways` must be (osm_id, highway, tags, wgs84_line) for every merge-tagged
    way in the extract; anything absent simply gets no chain context.
    """
    entries: dict[tuple, tuple] = {}  # key -> (entry_pt, exit_pt, length_m, roles)
    for osm_id, highway, tags, line in ways:
        plan = _plan_lanes(highway, tags)
        if plan is None or line is None or line.is_empty:
            continue
        _width, lanes, flip, _assumed = plan
        merge_dir = _resolve_merge_transition(lanes)
        if merge_dir is None:
            continue
        roles = frozenset(
            lane.role for lane in lanes if _merge_target(lanes, lane) is not None
        )
        length_m = transform(_WGS84_TO_RD.transform, line).length
        start_pt, end_pt = _pt(line.coords[0]), _pt(line.coords[-1])
        key = (osm_id, merge_dir * flip)
        entry, exit_ = (start_pt, end_pt) if key[1] > 0 else (end_pt, start_pt)
        entries[key] = (entry, exit_, length_m, roles)

    index: dict = {}
    groups: dict[tuple, list] = defaultdict(list)
    for key in entries:
        # Only chain ways whose merge structure matches. Two consecutive ways
        # merging *different* lanes are two transitions that happen to be
        # adjacent, not one spread across both.
        groups[(entries[key][3], key[1])].append(key)

    for keys in groups.values():
        by_entry: dict[tuple, list] = defaultdict(list)
        for key in keys:
            by_entry[entries[key][0]].append(key)
        succ: dict = {}
        for key in keys:
            candidates = [c for c in by_entry.get(entries[key][1], ()) if c != key]
            if len(candidates) == 1:  # a fork isn't a chain; leave it terminating here
                succ[key] = candidates[0]

        length_of = {key: entries[key][2] for key in keys}
        downstream = _downstream_lengths(keys, succ, length_of)
        merge_point = _merge_points(keys, succ)

        # Two ways can flow into the same one (two carriageways joining), so a
        # chain is really a tree. Every way feeding one merge point must share
        # its taper length or the convergence would jump where they join, so
        # the whole tree is sized by its longest branch.
        longest_branch: dict = {}
        for key in keys:
            reach = downstream[key] + length_of[key]
            point = merge_point[key]
            if reach > longest_branch.get(point, 0.0):
                longest_branch[point] = reach
        for key in keys:
            index[key] = (downstream[key], min(MAX_TAPER_M, longest_branch[merge_point[key]]))
    return index


def _merge_geometry(
    rd_line: LineString,
    base_offset_m: float,
    target_offset_m: float,
    travel_dir: int,
    dist_to_merge_m: float,
    taper_len_m: float,
):
    """Lane line that runs at base_offset then drifts across to target_offset.

    Used for every lane whose position changes over a merge, not just the
    merging one -- a survivor's target is its re-centred position, a merging
    lane's is wherever its target ends up.

    Returns None when the shape can't be built reliably; the caller then falls
    back to a plain constant offset (full length, no convergence).
    """
    line = rd_line if travel_dir > 0 else LineString(list(rd_line.coords)[::-1])
    if travel_dir < 0:
        # Reversing the line swaps which side a positive offset lands on.
        base_offset_m, target_offset_m = -base_offset_m, -target_offset_m

    length = line.length
    if length <= 0 or taper_len_m <= 0:
        return None

    own = _offset_geometry(line, base_offset_m)
    neighbour = _offset_geometry(line, target_offset_m)
    # A multipart offset means the source curve doubled back on itself; which
    # part to blend isn't well-defined, so don't guess.
    if isinstance(own, MultiLineString) or isinstance(neighbour, MultiLineString):
        return None
    if own.is_empty or neighbour.is_empty:
        return None

    taper_start = (length + dist_to_merge_m - taper_len_m) / length
    if taper_start >= 1.0:
        return _restore(own, travel_dir)  # merge point still far downstream
    taper_start = max(taper_start, 0.0)

    coords: list[tuple[float, float]] = []
    if taper_start > 0:
        head = substring(own, 0.0, taper_start, normalized=True)
        if not isinstance(head, LineString) or head.is_empty:
            return None
        coords.extend(list(head.coords)[:-1])

    taper_span = length * (1.0 - taper_start)
    steps = max(4, min(MAX_TAPER_SAMPLES, int(taper_span / TAPER_SAMPLE_M) + 1))
    for i in range(steps + 1):
        frac = taper_start + (1.0 - taper_start) * i / steps
        remaining = length * (1.0 - frac) + dist_to_merge_m
        weight = min(1.0, max(0.0, 1.0 - remaining / taper_len_m))
        pa = own.interpolate(frac, normalized=True)
        pb = neighbour.interpolate(frac, normalized=True)
        coords.append((pa.x + (pb.x - pa.x) * weight, pa.y + (pb.y - pa.y) * weight))

    if len(coords) < 2:
        return None
    return _restore(LineString(coords), travel_dir)


def _restore(line: LineString, travel_dir: int) -> LineString:
    """Put a travel-ordered line back into the source way's coordinate order."""
    if travel_dir > 0:
        return line
    return LineString(list(line.coords)[::-1])


def make_lane_rows(
    osm_id: int,
    highway: str,
    tags: dict,
    line: LineString,
    merge_index: Optional[dict] = None,
) -> list[dict]:
    """Expand one osm_road way into offset per-lane rows (WGS84 out).

    merge_index comes from build_merge_index(); without it merging lanes are
    still labelled but drawn as plain full-length offsets, since how far the
    merge has left to run isn't knowable from this way alone.
    """
    plan = _plan_lanes(highway, tags)
    if plan is None or line is None or line.is_empty:
        return []
    width, lanes, flip, assumed = plan
    _mark_dividers(lanes)
    merge_index = merge_index or {}
    raw = {"lanes_tag": tags.get("lanes"), "oneway": tags.get("oneway")}
    if assumed:
        raw["lanes_assumed"] = True

    rd_line = transform(_WGS84_TO_RD.transform, line)
    if flip < 0:
        rd_line = LineString(list(rd_line.coords)[::-1])

    merge_dir = _resolve_merge_transition(lanes)
    context = merge_index.get((osm_id, merge_dir * flip)) if merge_dir is not None else None

    rows: list[dict] = []
    for lane in lanes:
        offset_m = lane.offset_idx * width
        end_offset_m = lane.end_offset_idx * width
        lane_geom = None
        if context is not None and end_offset_m != offset_m:
            lane_geom = _merge_geometry(
                rd_line, offset_m, end_offset_m, merge_dir, context[0], context[1]
            )
        if lane_geom is None:
            lane_geom = _offset_geometry(rd_line, offset_m)
        if lane_geom.is_empty:
            continue
        wgs84_geom = transform(_RD_TO_WGS84.transform, lane_geom)
        lane_raw = dict(raw)
        lane_raw["divider_left"] = lane.divider_left
        turn = _turn_value(lane.turn)
        if turn is not None:
            lane_raw["turn"] = turn
        rows.append({
            "lane_offset_idx": lane.offset_idx,  # internal, dropped below before insert
            "direction": lane.direction,
            "role": lane.role,
            "highway": highway,
            "name": tags.get("name"),
            "ref": tags.get("ref"),
            "width_m": width,
            "geom": wgs84_geom.wkt,
            "raw": lane_raw,
        })

    # Assign stable, physically-ordered lane numbers (1 = leftmost) per
    # direction group, now that emptiness has been resolved.
    _number_lanes(rows)
    for row in rows:
        row["source_id"] = osm_id
        row["lane_count"] = sum(1 for r in rows if r["direction"] == row["direction"])
        row["id"] = f"{osm_id}:{row['direction']}:{row['lane']}"
        del row["lane_offset_idx"]
    return rows


def make_all_lane_rows(ways: Iterable[tuple[int, str, dict, LineString]]) -> list[dict]:
    """Lane rows for a set of merge-tagged ways, chain context resolved first."""
    ways = list(ways)
    index = build_merge_index(ways)
    rows: list[dict[str, Any]] = []
    for osm_id, highway, tags, line in ways:
        rows.extend(make_lane_rows(osm_id, highway, tags, line, index))
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
