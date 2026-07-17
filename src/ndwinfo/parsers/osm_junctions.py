"""Lane-to-lane connectors through a junction, driven by turn:lanes.

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

Only `oneway` ways take part, as approach or as exit. Every turn-tagged way in
this extract is oneway, and a two-way approach would need its exits filtered by
which direction is even legal to enter -- not worth guessing.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from pyproj import Transformer
from shapely import from_wkt
from shapely.geometry import LineString

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
    lanes = [row for row in lane_rows if row["direction"] == "fwd" and row.get("role") != "connector"]
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
                # Lanes turning the same way feed the exit's lanes in the same
                # left-to-right order. More turning lanes than the exit has
                # (they merge beyond the junction) all land on its last one.
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
