"""Lane-to-lane connectors through junctions and shared way boundaries.

Turns a `turn:lanes=left|left|through|right` tag into actual curved geometry
from each approach lane to the lane it feeds on the way it turns onto, so a
carriageway reads as continuing through a junction instead of stopping dead at
it. See docs/11-osm-pbf.md for the coverage this can and can't reach.

**A junction is a box, not a node.** OSM routinely models one intersection as
several nodes metres apart: at the Provincialeweg junction (way 1267507394,
`left|left|through|right`) only the *through* way starts at the approach's end
node -- its left target starts 18m away at a different node. Matching exits on
a shared node finds a left+through+right set for 18 of this extract's 4,706
turn-tagged ways; taking every exit whose start is within JUNCTION_RADIUS_M
instead finds a left target for 2,368 of them and both a left and a right for
1,219.

Turn connectors remain deliberately limited to `oneway` ways. A separate,
exact-shared-node continuation pass also handles the directional halves of
two-way roads. It only bridges the straightest continuation of the same named
or numbered road, so it can close offset-lane seams without inventing turns.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from pyproj import Transformer
from shapely import from_wkt
from shapely.geometry import LineString, Polygon
from shapely.ops import substring, transform

from ndwinfo.parsers.osm_lanes import split_turn_lanes

# How far from an approach's end an exit may start and still count as the same
# junction. Wide enough for OSM's multi-node junction boxes (18m at the
# Provincialeweg), short enough not to reach the next junction along.
JUNCTION_RADIUS_M = 25.0
# Each turn token's idealised turn angle: degrees, negative left, positive
# right. None = this token is not a junction movement (merges have their own
# model in osm_lanes; a U-turn's exit is indistinguishable from the opposite
# carriageway by angle alone, so it isn't attempted).
_TOKEN_ANGLE: dict[str, Optional[float]] = {
    "left": -90.0, "slight_left": -35.0, "sharp_left": -135.0,
    "through": 0.0, "none": 0.0, "": 0.0,
    "right": 90.0, "slight_right": 35.0, "sharp_right": 135.0,
    "merge_to_left": None, "merge_to_right": None, "reverse": None,
}
# How far an exit's real angle may sit from a token's idealised one.
ANGLE_TOLERANCE_DEG = 50.0
# Beyond this an "exit" is the opposite carriageway doubling back, not a turn.
MAX_TURN_DEG = 160.0
BEZIER_SAMPLES = 14
# Under this the approach and exit lanes already touch; a connector would be a
# degenerate stub with an unstable tangent.
MIN_CONNECTOR_M = 1.0
# Shared-node continuations are stricter than token-driven junction movements:
# they are surface joins, not inferred turns.
CONTINUATION_MAX_TURN_DEG = 55.0
# Lane-count and one-way/two-way changes need room to fan between their two
# cross-sections.  Limit the side-edge angle so a 1 -> 3 transition becomes a
# gradual taper instead of a block at the shared OSM node.
CONTINUATION_TAPER_ANGLE_DEG = 12.0
CONTINUATION_MIN_TRIM_M = 1.5
CONTINUATION_MAX_TRIM_M = 12.0
# Five centimetres under each adjoining band is enough to hide an antialias
# seam. A longer overlap becomes visible outside the road when the two lane
# tangents diverge: the polygon side is then a chord between different edges.
CONTINUATION_PATCH_M = 0.05

_ONEWAY = {"yes", "true", "1", "-1"}
_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)


def _bearing(frm: tuple[float, float], to: tuple[float, float]) -> float:
    """Grid bearing in RD metres: degrees clockwise from +y (north)."""
    return math.degrees(math.atan2(to[0] - frm[0], to[1] - frm[1]))


def _unit(bearing_deg: float) -> tuple[float, float]:
    rad = math.radians(bearing_deg)
    return (math.sin(rad), math.cos(rad))


def _norm_deg(deg: float) -> float:
    """Wrap to (-180, 180]: negative is a left turn, positive a right one."""
    return (deg + 180.0) % 360.0 - 180.0


def _node(coord) -> tuple[float, float]:
    """Stable WGS84 key for an original OSM way endpoint."""
    return (round(coord[0], 7), round(coord[1], 7))


def _ordered_points(
    lanes: list[tuple[int, tuple[float, float]]], bearing: float
) -> list[tuple[int, tuple[float, float]]]:
    """Lane points ordered from the driver's left to right."""
    ux, uy = _unit(bearing)
    left = (-uy, ux)
    return sorted(lanes, key=lambda item: -(item[1][0] * left[0] + item[1][1] * left[1]))


def junction_record(osm_id: int, tags: dict, lane_rows: list[dict]) -> Optional[dict]:
    """Compact per-way record for the connector pass, or None if it can't take part.

    Built from lane rows that were computed anyway, so the junction pass needs
    no second PBF read and no buffering of way geometry -- just two coordinates
    per lane.

    Relies on `fwd` lane geometry running in travel order, so a lane's first
    coordinate is where traffic enters it and its last is where it leaves.
    That holds for the oneway ways this accepts, oneway=-1 included (its lanes
    are laid out against a reversed copy of the way). It does NOT generalise:
    a two-way way's `bwd` lanes come back in the way's own coordinate order,
    so traffic enters them at their *last* coordinate -- anything extending
    this past oneway must reverse them first.
    """
    if tags.get("oneway") not in _ONEWAY:
        return None
    lanes = [
        row
        for row in lane_rows
        if row["direction"] == "fwd" and row.get("role") != "connector"
    ]
    if not lanes:
        return None

    starts: dict[int, tuple[float, float]] = {}
    ends: dict[int, tuple[float, float]] = {}
    for row in lanes:
        geom = from_wkt(row["geom"])
        # offset_curve can hand back a MultiLineString on doubled-back geometry
        # (16 lanes in this extract). Which part a turn leaves from isn't
        # well-defined, so the way sits the junction pass out entirely.
        if geom.geom_type != "LineString":
            return None
        coords = list(geom.coords)
        if len(coords) < 2:
            return None
        starts[row["lane"]] = _WGS84_TO_RD.transform(*coords[0])
        ends[row["lane"]] = _WGS84_TO_RD.transform(*coords[-1])

    # Any lane's end segment gives the way's heading -- they're parallel.
    probe = list(from_wkt(lanes[0]["geom"]).coords)
    first = [_WGS84_TO_RD.transform(*c) for c in probe[:2]]
    last = [_WGS84_TO_RD.transform(*c) for c in probe[-2:]]
    sample = lanes[0]
    return {
        "osm_id": osm_id,
        "lane_starts": starts,
        "lane_ends": ends,
        "lane_count": sample["lane_count"],
        "leave_bearing": _bearing(first[0], first[1]),
        "arrive_bearing": _bearing(last[0], last[1]),
        "turn_tokens": split_turn_lanes(tags.get("turn:lanes")),
        "highway": sample["highway"],
        "name": sample["name"],
        "ref": sample["ref"],
        "width_m": sample["width_m"],
    }


def continuation_records(
    osm_id: int,
    tags: dict,
    line: LineString,
    lane_rows: list[dict],
) -> list[dict]:
    """Compact directional records used to join consecutive OSM ways.

    Unlike turn connectors, these records include both directional halves of a
    two-way road. Original way endpoints provide the topology; offset lane
    endpoints provide the surface that must be bridged.
    """
    if line is None or line.is_empty or line.geom_type != "LineString":
        return []
    source_coords = list(line.coords)
    if len(source_coords) < 2:
        return []

    records: list[dict] = []
    for direction in ("fwd", "bwd"):
        lanes = [
            row for row in lane_rows
            if row["direction"] == direction and row.get("role") != "connector"
        ]
        if not lanes:
            continue

        starts: list[tuple[int, tuple[float, float]]] = []
        ends: list[tuple[int, tuple[float, float]]] = []
        row_ids: dict[int, str] = {}
        lane_lengths: dict[int, float] = {}
        probes: list[tuple[float, float]] | None = None
        for row in lanes:
            geom = from_wkt(row["geom"])
            if geom.geom_type != "LineString":
                probes = None
                break
            coords = list(geom.coords)
            if len(coords) < 2:
                probes = None
                break
            # Two-way backward lane geometry is returned in the source way's
            # coordinate order; traffic traverses it in reverse.
            travel_coords = coords if direction == "fwd" else list(reversed(coords))
            rd_coords = [_WGS84_TO_RD.transform(*coord) for coord in travel_coords]
            starts.append((row["lane"], rd_coords[0]))
            ends.append((row["lane"], rd_coords[-1]))
            row_ids[row["lane"]] = row["id"]
            lane_lengths[row["lane"]] = LineString(rd_coords).length
            if probes is None:
                probes = travel_coords
        if probes is None or not starts:
            continue

        first = [_WGS84_TO_RD.transform(*c) for c in probes[:2]]
        last = [_WGS84_TO_RD.transform(*c) for c in probes[-2:]]
        leave_bearing = _bearing(first[0], first[1])
        arrive_bearing = _bearing(last[0], last[1])
        reverse_source = direction == "bwd" or tags.get("oneway") == "-1"
        entry_coord, exit_coord = (
            (source_coords[-1], source_coords[0])
            if reverse_source else (source_coords[0], source_coords[-1])
        )
        sample = lanes[0]
        records.append({
            "key": (osm_id, direction),
            "osm_id": osm_id,
            "direction": direction,
            "two_way": tags.get("oneway") not in _ONEWAY,
            "entry_node": _node(entry_coord),
            "exit_node": _node(exit_coord),
            "lane_starts": _ordered_points(starts, leave_bearing),
            "lane_ends": _ordered_points(ends, arrive_bearing),
            "row_ids": row_ids,
            "lane_lengths": lane_lengths,
            "lane_count": len(lanes),
            "leave_bearing": leave_bearing,
            "arrive_bearing": arrive_bearing,
            "highway": sample["highway"],
            "name": sample["name"],
            "ref": sample["ref"],
            "width_m": sample["width_m"],
        })
    return records


def _exit_grid(records: dict[int, dict]) -> dict[tuple[int, int], list[dict]]:
    """Bucket exits by their entry point so lookup isn't O(ways^2)."""
    grid: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for rec in records.values():
        entry = rec["lane_starts"].get(1)
        if entry is None:
            continue
        grid[(int(entry[0] // JUNCTION_RADIUS_M), int(entry[1] // JUNCTION_RADIUS_M))].append(rec)
    return grid


def _nearby_exits(grid: dict, point: tuple[float, float]) -> list[tuple[dict, float]]:
    """Candidate exits paired with how far their entry is from `point`."""
    cx, cy = int(point[0] // JUNCTION_RADIUS_M), int(point[1] // JUNCTION_RADIUS_M)
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for rec in grid.get((cx + dx, cy + dy), ()):
                distance = math.dist(rec["lane_starts"][1], point)
                if distance <= JUNCTION_RADIUS_M:
                    out.append((rec, distance))
    return out


def _pick_exit(exits, arrive_bearing: float, target_angle: float, approach_id: int):
    """The exit whose real turn angle is closest to what the token asks for.

    Distance breaks ties: a 25m radius can also reach a parallel carriageway
    heading the same way, which looks just as much like `through` by angle
    alone. The nearer candidate is the one actually at this junction.
    """
    best, best_key = None, None
    for rec, distance in exits:
        if rec["osm_id"] == approach_id:
            continue
        turn = _norm_deg(rec["leave_bearing"] - arrive_bearing)
        if abs(turn) > MAX_TURN_DEG:
            continue  # doubling back: the opposite carriageway, not a turn
        error = abs(turn - target_angle)
        if error > ANGLE_TOLERANCE_DEG:
            continue
        key = (error, distance)
        if best_key is None or key < best_key:
            best, best_key = rec, key
    return best


def _bezier(p0, bearing0: float, p3, bearing3: float) -> Optional[LineString]:
    """Curve leaving p0 along bearing0 and arriving at p3 along bearing3."""
    span = math.dist(p0, p3)
    if span < MIN_CONNECTOR_M:
        return None
    handle = span / 3.0
    u0, u3 = _unit(bearing0), _unit(bearing3)
    p1 = (p0[0] + u0[0] * handle, p0[1] + u0[1] * handle)
    p2 = (p3[0] - u3[0] * handle, p3[1] - u3[1] * handle)
    pts = []
    for i in range(BEZIER_SAMPLES + 1):
        t = i / BEZIER_SAMPLES
        m = 1.0 - t
        a, b, c, d = m * m * m, 3 * m * m * t, 3 * m * t * t, t * t * t
        pts.append((
            a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
        ))
    return LineString(pts)


def _continuation_surface(
    p0,
    bearing0: float,
    width0: float,
    p3,
    bearing3: float,
    width3: float,
) -> Polygon:
    """Curved, variable-width road surface between two cross-sections."""
    u0, u3 = _unit(bearing0), _unit(bearing3)
    p0 = (p0[0] - u0[0] * CONTINUATION_PATCH_M, p0[1] - u0[1] * CONTINUATION_PATCH_M)
    p3 = (p3[0] + u3[0] * CONTINUATION_PATCH_M, p3[1] + u3[1] * CONTINUATION_PATCH_M)
    span = math.dist(p0, p3)
    handle = max(span / 3.0, CONTINUATION_PATCH_M)
    p1 = (p0[0] + u0[0] * handle, p0[1] + u0[1] * handle)
    p2 = (p3[0] - u3[0] * handle, p3[1] - u3[1] * handle)

    left_edge = []
    right_edge = []
    for i in range(BEZIER_SAMPLES + 1):
        t = i / BEZIER_SAMPLES
        m = 1.0 - t
        a, b, c, d = m * m * m, 3 * m * m * t, 3 * m * t * t, t * t * t
        point = (
            a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
        )
        tangent = (
            3 * m * m * (p1[0] - p0[0])
            + 6 * m * t * (p2[0] - p1[0])
            + 3 * t * t * (p3[0] - p2[0]),
            3 * m * m * (p1[1] - p0[1])
            + 6 * m * t * (p2[1] - p1[1])
            + 3 * t * t * (p3[1] - p2[1]),
        )
        tangent_len = math.hypot(*tangent)
        unit = u0 if tangent_len < 1e-9 else (tangent[0] / tangent_len, tangent[1] / tangent_len)
        left = (-unit[1], unit[0])
        half_width = ((1.0 - t) * width0 + t * width3) / 2.0
        left_edge.append((point[0] + left[0] * half_width, point[1] + left[1] * half_width))
        right_edge.append((point[0] - left[0] * half_width, point[1] - left[1] * half_width))

    surface = Polygon(left_edge + list(reversed(right_edge)))
    if not surface.is_valid:
        repaired = surface.buffer(0)
        # At a short bend the two sampled sides can cross at the shared node.
        # buffer(0) repairs that as two disjoint slivers, which would bring the
        # fill-edge seam back.  The join is only centimetres long here, so its
        # single convex envelope is the correct continuous road surface.
        surface = repaired if repaired.geom_type == "Polygon" else surface.convex_hull
    return surface


def _road_match_score(approach: dict, exit_rec: dict) -> Optional[int]:
    """Prefer the same numbered road, then the same named road."""
    if approach.get("ref") and approach["ref"] == exit_rec.get("ref"):
        return 0
    if approach.get("name") and approach["name"] == exit_rec.get("name"):
        return 1
    return None


def _cross_section(lanes: list, bearing: float, lane_width: float) -> tuple:
    """Return the centre and outer edges of a left-to-right lane section."""
    unit = _unit(bearing)
    left = (-unit[1], unit[0])
    left_centre = lanes[0][1]
    right_centre = lanes[-1][1]
    left_edge = (
        left_centre[0] + left[0] * lane_width / 2.0,
        left_centre[1] + left[1] * lane_width / 2.0,
    )
    right_edge = (
        right_centre[0] - left[0] * lane_width / 2.0,
        right_centre[1] - left[1] * lane_width / 2.0,
    )
    centre = (
        (left_edge[0] + right_edge[0]) / 2.0,
        (left_edge[1] + right_edge[1]) / 2.0,
    )
    return centre, left_edge, right_edge


def _continuation_trim(approach: dict, exit_rec: dict) -> tuple[float, float]:
    """Room on both ways for a smooth complex cross-section transition."""
    complex_transition = (
        approach["two_way"] != exit_rec["two_way"]
        or approach["lane_count"] != exit_rec["lane_count"]
    )
    if not complex_transition:
        return (0.0, 0.0)

    _, left0, right0 = _cross_section(
        approach["lane_ends"], approach["arrive_bearing"], approach["width_m"]
    )
    _, left3, right3 = _cross_section(
        exit_rec["lane_starts"], exit_rec["leave_bearing"], exit_rec["width_m"]
    )
    edge_shift = max(math.dist(left0, left3), math.dist(right0, right3))
    trim = edge_shift / (2.0 * math.tan(math.radians(CONTINUATION_TAPER_ANGLE_DEG)))
    trim = max(CONTINUATION_MIN_TRIM_M, min(CONTINUATION_MAX_TRIM_M, trim))
    approach_limit = min(approach["lane_lengths"].values()) * 0.25
    exit_limit = min(exit_rec["lane_lengths"].values()) * 0.25
    return min(trim, approach_limit), min(trim, exit_limit)


def make_continuation_rows(
    records: dict[tuple[int, str], dict],
    lane_rows_by_id: Optional[dict[str, dict]] = None,
) -> list[dict]:
    """Join the straightest same-road flows that share an original OSM node."""
    by_entry: dict[tuple[float, float], list[dict]] = defaultdict(list)
    for rec in records.values():
        by_entry[rec["entry_node"]].append(rec)

    rows: list[dict[str, Any]] = []
    trim_requests: dict[str, dict[str, float]] = defaultdict(dict)
    for approach in records.values():
        best = None
        best_key = None
        for exit_rec in by_entry.get(approach["exit_node"], ()):
            if exit_rec["osm_id"] == approach["osm_id"]:
                continue
            road_score = _road_match_score(approach, exit_rec)
            if road_score is None:
                continue
            turn = abs(_norm_deg(exit_rec["leave_bearing"] - approach["arrive_bearing"]))
            if turn > CONTINUATION_MAX_TURN_DEG:
                continue
            key = (road_score, turn, abs(exit_rec["lane_count"] - approach["lane_count"]))
            if best_key is None or key < best_key:
                best, best_key = exit_rec, key
        if best is None:
            continue

        start, _, _ = _cross_section(
            approach["lane_ends"], approach["arrive_bearing"], approach["width_m"]
        )
        end, _, _ = _cross_section(
            best["lane_starts"], best["leave_bearing"], best["width_m"]
        )
        approach_trim, exit_trim = _continuation_trim(approach, best)
        u0 = _unit(approach["arrive_bearing"])
        u3 = _unit(best["leave_bearing"])
        start = (
            start[0] - u0[0] * approach_trim,
            start[1] - u0[1] * approach_trim,
        )
        end = (
            end[0] + u3[0] * exit_trim,
            end[1] + u3[1] * exit_trim,
        )

        if lane_rows_by_id is not None and (approach_trim or exit_trim):
            approach_side = "end" if approach["direction"] == "fwd" else "start"
            exit_side = "start" if best["direction"] == "fwd" else "end"
            for row_id in approach["row_ids"].values():
                trim_requests[row_id][approach_side] = max(
                    trim_requests[row_id].get(approach_side, 0.0), approach_trim
                )
            for row_id in best["row_ids"].values():
                trim_requests[row_id][exit_side] = max(
                    trim_requests[row_id].get(exit_side, 0.0), exit_trim
                )

        # One polygon covers the complete directional cross-section.  The old
        # per-lane polygons overlapped at widening/narrowing transitions; their
        # independently antialiased edges were the diagonal seams visible in
        # the map.
        surface = _continuation_surface(
            start,
            approach["arrive_bearing"],
            approach["lane_count"] * approach["width_m"],
            end,
            best["leave_bearing"],
            best["lane_count"] * best["width_m"],
        )
        wgs84 = transform(_RD_TO_WGS84.transform, surface)
        rows.append({
            "id": (
                f"{approach['osm_id']}:join:{approach['direction']}:"
                f"{best['osm_id']}:{best['direction']}"
            ),
            "source_id": approach["osm_id"],
            "lane": 1,
            "lane_count": approach["lane_count"],
            "direction": approach["direction"],
            "role": "connector",
            "highway": approach["highway"],
            "name": approach["name"],
            "ref": approach["ref"],
            "width_m": approach["width_m"],
            "geom": wgs84.wkt,
            "raw": {
                "continuation": True,
                "to_osm_id": best["osm_id"],
                "to_lanes": [lane for lane, _ in best["lane_starts"]],
            },
        })

    if lane_rows_by_id is not None:
        for row_id, request in trim_requests.items():
            row = lane_rows_by_id.get(row_id)
            if row is None:
                continue
            geom = from_wkt(row["geom"])
            rd_geom = transform(_WGS84_TO_RD.transform, geom)
            start = request.get("start", 0.0)
            end = rd_geom.length - request.get("end", 0.0)
            if end <= start:
                continue
            trimmed = substring(rd_geom, start, end)
            row["geom"] = transform(_RD_TO_WGS84.transform, trimmed).wkt
            row["raw"]["continuation_trim"] = True
    return rows


def make_connector_rows(records: dict[int, dict]) -> list[dict]:
    """Connector lane rows for every approach whose turn:lanes resolves to an exit."""
    grid = _exit_grid(records)
    rows: list[dict[str, Any]] = []

    for approach in records.values():
        tokens = approach["turn_tokens"]
        # Same cardinality guard as the lane model: a token count that doesn't
        # match the lanes can't be attributed to a physical lane.
        if not tokens or len(tokens) != approach["lane_count"]:
            continue
        approach_end = approach["lane_ends"].get(1)
        if approach_end is None:
            continue
        exits = _nearby_exits(grid, approach_end)
        if not exits:
            continue

        # Resolve every (lane, token) to an exit first: which lane of the exit
        # each one feeds depends on how many other lanes turn the same way.
        # Keyed by (lane, exit) because a lane feeds an exit once even when two
        # of its tokens point at it -- `left;slight_left` onto the same way is
        # one movement, not two.
        movements: dict[tuple[int, int], tuple[int, str, dict]] = {}
        for lane_no, token_set in enumerate(tokens, start=1):
            if lane_no not in approach["lane_ends"]:
                continue
            for token in sorted(token_set):
                target = _TOKEN_ANGLE.get(token)
                if target is None:
                    continue
                chosen = _pick_exit(exits, approach["arrive_bearing"], target, approach["osm_id"])
                if chosen is None:
                    continue
                movements.setdefault((lane_no, chosen["osm_id"]), (lane_no, token, chosen))

        by_exit: dict[int, list[tuple[int, str, dict]]] = defaultdict(list)
        for move in movements.values():
            by_exit[move[2]["osm_id"]].append(move)

        for group in by_exit.values():
            group.sort(key=lambda m: m[0])  # left to right across the approach
            exit_rec = group[0][2]
            for position, (lane_no, token, _) in enumerate(group):
                if _TOKEN_ANGLE[token] == 0.0:
                    # A straight movement keeps its position in the whole
                    # approach cross-section. Starting every through-only group
                    # at exit lane 1 makes the rightmost survivor cut across a
                    # merge and shifts a 3->4 transition two lanes left.
                    exit_lane = min(
                        exit_rec["lane_count"],
                        int((lane_no - 0.5) * exit_rec["lane_count"] / approach["lane_count"]) + 1,
                    )
                else:
                    # Turning lanes feed the target's lanes left-to-right; if
                    # there are more of them than the exit has, the extras
                    # merge onto its last lane beyond the junction.
                    exit_lane = min(position + 1, exit_rec["lane_count"])
                start = approach["lane_ends"][lane_no]
                end = exit_rec["lane_starts"].get(exit_lane)
                if end is None:
                    continue
                curve = _bezier(start, approach["arrive_bearing"], end, exit_rec["leave_bearing"])
                if curve is None:
                    continue  # already touching; the bands meet without help
                wgs84 = LineString([_RD_TO_WGS84.transform(x, y) for x, y in curve.coords])
                rows.append({
                    "id": f"{approach['osm_id']}:conn:{lane_no}:{exit_rec['osm_id']}:{exit_lane}",
                    "source_id": approach["osm_id"],
                    "lane": lane_no,
                    "lane_count": approach["lane_count"],
                    "direction": "fwd",
                    "role": "connector",
                    "highway": approach["highway"],
                    "name": approach["name"],
                    "ref": approach["ref"],
                    "width_m": approach["width_m"],
                    "geom": wgs84.wkt,
                    "raw": {
                        "turn": token,
                        "to_osm_id": exit_rec["osm_id"],
                        "to_lane": exit_lane,
                    },
                })
    return rows
