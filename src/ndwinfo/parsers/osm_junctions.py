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
# Contested approaches can overlap for much farther than an ordinary
# lane-count transition because their OSM centrelines meet at one node. Let a
# disjoint allocation walk farther upstream until its source cross-sections are
# disjoint too; the half-length cap below still protects short way fragments.
CONTINUATION_CONTESTED_MAX_TRIM_M = 80.0
CONTINUATION_CONTESTED_TRIM_STEP_M = 0.5
# Five centimetres under each adjoining band is enough to hide an antialias
# seam. A longer overlap becomes visible outside the road when the two lane
# tangents diverge: the polygon side is then a chord between different edges.
CONTINUATION_PATCH_M = 0.05
# Edge movement no larger than the overlap at both ends is already covered by
# the patch above. Anything larger needs room for a real taper, even when an
# allocated approach and target block contain the same number of lanes.
CONTINUATION_EDGE_SHIFT_EPS_M = CONTINUATION_PATCH_M * 2.0

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
        if row["direction"] == "fwd"
        and not str(row.get("role", "")).startswith("connector")
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
            if row["direction"] == direction
            and not str(row.get("role", "")).startswith("connector")
        ]
        if not lanes:
            continue

        starts: list[tuple[int, tuple[float, float]]] = []
        ends: list[tuple[int, tuple[float, float]]] = []
        row_ids: dict[int, str] = {}
        lane_roles: dict[int, str] = {}
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
            lane_roles[row["lane"]] = row["role"]
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
        turn_key = (
            "turn:lanes"
            if tags.get("oneway") in _ONEWAY
            else f"turn:lanes:{'forward' if direction == 'fwd' else 'backward'}"
        )
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
            "lane_roles": lane_roles,
            "lane_lengths": lane_lengths,
            "lane_count": len(lanes),
            "leave_bearing": leave_bearing,
            "arrive_bearing": arrive_bearing,
            "turn_tokens": split_turn_lanes(tags.get(turn_key)),
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


def _section_lateral_position(
    lanes: list,
    bearing: float,
    lane_width: float,
    projection_bearing: Optional[float] = None,
) -> float:
    """Cross-section centre projected onto the driver's left axis."""
    centre, _, _ = _cross_section(lanes, bearing, lane_width)
    ux, uy = _unit(bearing if projection_bearing is None else projection_bearing)
    left = (-uy, ux)
    return centre[0] * left[0] + centre[1] * left[1]


def _approach_lateral_position(approach: dict, target_bearing: float) -> float:
    """Lateral position just before converging source centre lines meet.

    At an OSM merge every source centre line ends on the same node, so their
    endpoint cross-section centres are identical and cannot order the branches.
    Projecting a short distance back along each arrival tangent recovers which
    side each carriageway approaches from without retaining whole geometries.
    """
    centre, _, _ = _cross_section(
        approach["lane_ends"], approach["arrive_bearing"], approach["width_m"]
    )
    ux, uy = _unit(approach["arrive_bearing"])
    probe = (
        centre[0] - ux * JUNCTION_RADIUS_M,
        centre[1] - uy * JUNCTION_RADIUS_M,
    )
    target_u = _unit(target_bearing)
    target_left = (-target_u[1], target_u[0])
    return probe[0] * target_left[0] + probe[1] * target_left[1]


def _primary_approach_index(group: list[tuple[dict, dict]]) -> int:
    """The through/mainline approach to preserve when incoming lanes exceed target lanes."""
    target = group[0][1]

    def score(index: int) -> tuple:
        approach = group[index][0]
        turn = abs(_norm_deg(target["leave_bearing"] - approach["arrive_bearing"]))
        return (
            approach["highway"] != target["highway"],
            approach["highway"].endswith("_link"),
            turn,
            -approach["lane_count"],
        )

    return min(range(len(group)), key=score)


def _nearest_monotone_blocks(
    group: list[tuple[dict, dict]],
    target_lanes: list,
    block_sizes: list[int],
) -> list[list]:
    """Choose ordered target blocks nearest each ordered approach.

    Blocks may overlap when more lanes arrive than the target carries, but
    their starts and ends remain monotone so connectors can converge without
    crossing. The search space is tiny in practice (normally 2-4 lanes).
    """
    target = group[0][1]
    approach_positions = [
        _approach_lateral_position(approach, target["leave_bearing"])
        for approach, _ in group
    ]
    candidates: list[list[tuple[int, int, list, float]]] = []
    for position, size in zip(approach_positions, block_sizes):
        options = []
        for start in range(len(target_lanes) - size + 1):
            end = start + size
            block = target_lanes[start:end]
            target_position = _section_lateral_position(
                block, target["leave_bearing"], target["width_m"]
            )
            options.append((start, end, block, (position - target_position) ** 2))
        candidates.append(options)

    best_cost: Optional[float] = None
    best_blocks: Optional[list[list]] = None

    def visit(
        index: int,
        last_start: int,
        last_end: int,
        cost: float,
        blocks: list[list],
    ) -> None:
        nonlocal best_cost, best_blocks
        if best_cost is not None and cost >= best_cost:
            return
        if index == len(candidates):
            best_cost = cost
            best_blocks = list(blocks)
            return
        for start, end, block, option_cost in candidates[index]:
            if start < last_start or end < last_end:
                continue
            blocks.append(block)
            visit(index + 1, start, end, cost + option_cost, blocks)
            blocks.pop()

    visit(0, 0, 0, 0.0, [])
    # An exotic width ordering can have no monotone solution. Omitting that
    # contested group leaves a local gap, which is safer than recreating the
    # crossing bug by letting every approach claim the complete target.
    return best_blocks or []


def _allocate_target_blocks(group: list[tuple[dict, dict]]) -> list[tuple[dict, dict, list]]:
    """Allocate a target cross-section among approaches choosing the same exit.

    A solo continuation keeps the historical full-width fan. Contested exits
    get spatially ordered, contiguous blocks. When more lanes arrive than the
    target has, blocks may share only the lanes needed for the convergence;
    no two approaches independently fan across the full target width.
    """
    if len(group) == 1:
        approach, target = group[0]
        return [(approach, target, target["lane_starts"])]

    target = group[0][1]
    target_lanes = target["lane_starts"]
    target_count = len(target_lanes)
    if target_count == 0:
        return []

    # Records are already directional. Grouping on target["key"] means a
    # two-way road's fwd and bwd halves can never compete for one cross-section.
    group = sorted(
        group,
        key=lambda pair: -_approach_lateral_position(
            pair[0], target["leave_bearing"]
        ),
    )
    incoming_count = sum(approach["lane_count"] for approach, _ in group)

    if incoming_count <= target_count:
        # Conserved merges get exact disjoint blocks. A wider target distributes
        # its otherwise-unexplained new lanes proportionally, with the
        # through/mainline approach winning equal-remainder ties.
        ideal_sizes = [
            approach["lane_count"] * target_count / incoming_count
            for approach, _ in group
        ]
        block_sizes = [max(1, math.floor(size)) for size in ideal_sizes]
        primary = _primary_approach_index(group)
        while sum(block_sizes) < target_count:
            index = max(
                range(len(group)),
                key=lambda i: (
                    ideal_sizes[i] - block_sizes[i],
                    i == primary,
                    group[i][0]["lane_count"],
                ),
            )
            block_sizes[index] += 1
        blocks = []
        cursor = 0
        for size in block_sizes:
            blocks.append(target_lanes[cursor:cursor + size])
            cursor += size
    else:
        block_sizes = [
            min(approach["lane_count"], target_count) for approach, _ in group
        ]
        full_width = [i for i, size in enumerate(block_sizes) if size == target_count]
        if len(full_width) > 1 and target_count > 1:
            primary = _primary_approach_index(group)
            for index in full_width:
                if index != primary:
                    block_sizes[index] = target_count - 1
        blocks = _nearest_monotone_blocks(group, target_lanes, block_sizes)
        if not blocks:
            return []

    return [
        (approach, target_rec, block)
        for (approach, target_rec), block in zip(group, blocks)
    ]


def _continuation_lane_map(
    approach: dict,
    target: dict,
    approach_lanes: list,
    target_lanes: list,
) -> dict[int, int]:
    """Map source lanes to target lanes when tags explain a count change.

    The continuation surface still spans the complete physical cross-section.
    This mapping controls the internal markings and records the actual flow:

    - a source ``merge_to_left/right`` lane maps onto its surviving neighbour;
    - a target edge lane with a turn-only token is grown beside the through
      block instead of making every incoming lane fan across the full width.
    """
    source_numbers = [lane for lane, _point in approach_lanes]
    target_numbers = [lane for lane, _point in target_lanes]
    if not source_numbers or not target_numbers:
        return {}

    source_tokens = approach.get("turn_tokens")
    if (
        approach["lane_count"] > len(target_numbers)
        and source_tokens
        and len(source_tokens) == approach["lane_count"]
        and any(
            "merge_to_left" in tokens or "merge_to_right" in tokens
            for tokens in source_tokens
        )
    ):
        # Geometry uses only the physically distinct survivors, but flow
        # metadata must still say where the coincident merging lane lands.
        source_numbers = [lane for lane, _point in approach["lane_ends"]]

    if len(source_numbers) == len(target_numbers):
        return dict(zip(source_numbers, target_numbers))

    if (
        len(source_numbers) > len(target_numbers)
        and source_tokens
        and len(source_tokens) == approach["lane_count"]
        and len(source_numbers) == approach["lane_count"]
    ):
        merge_targets: dict[int, int] = {}
        for index, tokens in enumerate(source_tokens):
            if "merge_to_left" in tokens and index > 0:
                merge_targets[index] = index - 1
            elif "merge_to_right" in tokens and index + 1 < len(source_numbers):
                merge_targets[index] = index + 1
        survivors = [
            index for index in range(len(source_numbers))
            if index not in merge_targets
        ]
        if merge_targets and len(survivors) == len(target_numbers):
            survivor_targets = {
                source_index: target_numbers[target_index]
                for target_index, source_index in enumerate(survivors)
            }
            lane_map: dict[int, int] = {}
            for index, source_lane in enumerate(source_numbers):
                resolved = index
                for _ in range(len(source_numbers)):
                    if resolved not in merge_targets:
                        break
                    resolved = merge_targets[resolved]
                target_lane = survivor_targets.get(resolved)
                if target_lane is None:
                    return {}
                lane_map[source_lane] = target_lane
            return lane_map

    target_tokens = target.get("turn_tokens")
    if (
        len(target_numbers) > len(source_numbers)
        and target_tokens
        and len(target_tokens) == target["lane_count"]
        and len(target_numbers) == target["lane_count"]
    ):
        through_positions = [
            index
            for index, tokens in enumerate(target_tokens)
            if tokens.intersection({"", "none", "through"})
        ]
        if len(through_positions) == len(source_numbers):
            return {
                source_lane: target_numbers[target_index]
                for source_lane, target_index in zip(source_numbers, through_positions)
            }
    return {}


def _geometry_approach_block(approach: dict, target_lanes: list) -> list:
    """Physically distinct source bands for a merge-tagged solo narrowing."""
    source_lanes = approach["lane_ends"]
    target_count = len(target_lanes)
    tokens = approach.get("turn_tokens")
    if (
        len(source_lanes) <= target_count
        or not tokens
        or len(tokens) != len(source_lanes)
    ):
        return source_lanes
    survivors = [
        lane_item
        for lane_item, lane_tokens in zip(source_lanes, tokens)
        if (
            "merge_to_left" not in lane_tokens
            and "merge_to_right" not in lane_tokens
        )
    ]
    return survivors if len(survivors) == target_count else source_lanes


def _divider_transition_fractions(
    lane_map: dict[int, int],
    approach_lanes: list,
    target_lanes: list,
) -> list[tuple[float, float]]:
    """Divider start/end positions across two lane sections, left=0/right=1."""
    if not lane_map:
        return []
    target_index = {
        lane: index for index, (lane, _point) in enumerate(target_lanes)
    }
    mapped = []
    for lane, _point in approach_lanes:
        target_lane = lane_map.get(lane)
        if target_lane not in target_index:
            return []
        mapped.append(target_index[target_lane])
    if not mapped or any(left > right for left, right in zip(mapped, mapped[1:])):
        return []

    groups: list[int] = []
    for index in mapped:
        if not groups or groups[-1] != index:
            groups.append(index)
    if not groups:
        return []

    fractions: list[tuple[float, float]] = []
    group_count = len(groups)
    target_count = len(target_lanes)
    for boundary in range(1, group_count):
        fractions.append((
            boundary / group_count,
            groups[boundary] / target_count,
        ))

    # Target-only edge lanes are born at the corresponding source edge. This
    # is the A44 2→3 exit case: the through divider continues 1→1 / 2→2,
    # while the new lane-3 divider starts at the source's right edge.
    first_target = groups[0]
    for boundary in range(1, first_target + 1):
        fractions.append((0.0, boundary / target_count))
    last_target = groups[-1]
    for boundary in range(last_target + 1, target_count):
        fractions.append((1.0, boundary / target_count))
    return sorted(set(fractions), key=lambda item: item[1])


def _continuation_trim(
    approach: dict,
    exit_rec: dict,
    approach_lanes: Optional[list] = None,
    exit_lanes: Optional[list] = None,
) -> tuple[float, float]:
    """Room on both ways for a smooth allocated cross-section transition."""
    approach_lanes = approach_lanes or approach["lane_ends"]
    exit_lanes = exit_lanes or exit_rec["lane_starts"]

    if (
        len(approach_lanes) < approach["lane_count"]
        and len(approach_lanes) == len(exit_lanes)
        and approach["two_way"] == exit_rec["two_way"]
    ):
        # osm_lanes has already converged every merge-tagged centreline onto
        # this survivor section. Trimming both ways again mistakes the
        # resulting offset-curve cap skew for a lateral shift and opens a
        # visible notch at an otherwise aligned handover.
        return (0.0, 0.0)

    _, left0, right0 = _cross_section(
        approach_lanes, approach["arrive_bearing"], approach["width_m"]
    )
    _, left3, right3 = _cross_section(
        exit_lanes, exit_rec["leave_bearing"], exit_rec["width_m"]
    )
    edge_shift = max(math.dist(left0, left3), math.dist(right0, right3))
    if (
        approach["two_way"] == exit_rec["two_way"]
        and edge_shift <= CONTINUATION_EDGE_SHIFT_EPS_M
    ):
        return (0.0, 0.0)
    trim = edge_shift / (2.0 * math.tan(math.radians(CONTINUATION_TAPER_ANGLE_DEG)))
    trim = max(CONTINUATION_MIN_TRIM_M, min(CONTINUATION_MAX_TRIM_M, trim))
    approach_limit = min(approach["lane_lengths"].values()) * 0.25
    exit_limit = min(exit_rec["lane_lengths"].values()) * 0.25
    return min(trim, approach_limit), min(trim, exit_limit)


def _lane_path(
    record: dict,
    lane: int,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> Optional[LineString]:
    """One lane in travel order and RD metres, cached before rows are trimmed."""
    if lane_rows_by_id is None:
        return None
    key = (record["key"], lane)
    if key in path_cache:
        return path_cache[key]
    row_id = record["row_ids"].get(lane)
    row = lane_rows_by_id.get(row_id) if row_id is not None else None
    if row is None:
        return None
    geom = from_wkt(row["geom"])
    if geom.geom_type != "LineString":
        return None
    path = transform(_WGS84_TO_RD.transform, geom)
    if record["direction"] == "bwd":
        path = LineString(list(path.coords)[::-1])
    path_cache[key] = path
    return path


def _section_at_trim(
    record: dict,
    lanes: list,
    trim_m: float,
    *,
    at_entry: bool,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], float]:
    """Centre, edges, and bearing of a real cross-section at a trim station.

    The old continuation code translated the endpoint cross-section along one
    tangent. That is only exact for a straight lane. Sampling the same lane
    geometries that are subsequently trimmed makes the polygon meet every
    curved or merging lane at its actual new cap.
    """
    sampled: list[tuple[int, tuple[float, float]]] = []
    bearing_path: Optional[LineString] = None
    bearing_distance = 0.0
    for lane, _point in lanes:
        path = _lane_path(record, lane, lane_rows_by_id, path_cache)
        if path is None or path.length <= 0:
            sampled = []
            break
        distance = min(trim_m, path.length) if at_entry else max(0.0, path.length - trim_m)
        point = path.interpolate(distance)
        sampled.append((lane, (point.x, point.y)))
        if bearing_path is None:
            bearing_path = path
            bearing_distance = distance

    if sampled and bearing_path is not None:
        probe = min(0.25, bearing_path.length / 4.0)
        before = bearing_path.interpolate(max(0.0, bearing_distance - probe))
        after = bearing_path.interpolate(min(bearing_path.length, bearing_distance + probe))
        bearing = _bearing((before.x, before.y), (after.x, after.y))
        centre, left, right = _cross_section(sampled, bearing, record["width_m"])
        return centre, left, right, bearing

    # DB-free callers and malformed lane lookup retain the prior tangent model.
    bearing = record["leave_bearing"] if at_entry else record["arrive_bearing"]
    base = record["lane_starts"] if at_entry else record["lane_ends"]
    ux, uy = _unit(bearing)
    sign = 1.0 if at_entry else -1.0
    shifted = [
        (lane, (point[0] + sign * ux * trim_m, point[1] + sign * uy * trim_m))
        for lane, point in lanes
    ]
    centre, left, right = _cross_section(shifted or base, bearing, record["width_m"])
    return centre, left, right, bearing


def _planned_surface(
    approach: dict,
    target: dict,
    approach_lanes: list,
    target_lanes: list,
    approach_trim: float,
    exit_trim: float,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> Polygon:
    start, _start_left, _start_right, start_bearing = _section_at_trim(
        approach,
        approach_lanes,
        approach_trim,
        at_entry=False,
        lane_rows_by_id=lane_rows_by_id,
        path_cache=path_cache,
    )
    end, _end_left, _end_right, end_bearing = _section_at_trim(
        target,
        target_lanes,
        exit_trim,
        at_entry=True,
        lane_rows_by_id=lane_rows_by_id,
        path_cache=path_cache,
    )
    return _continuation_surface(
        start,
        start_bearing,
        len(approach_lanes) * approach["width_m"],
        end,
        end_bearing,
        len(target_lanes) * target["width_m"],
    )


def _mean_bearing(first: float, second: float) -> float:
    """Circular mean of two bearings."""
    a = _unit(first)
    b = _unit(second)
    return math.degrees(math.atan2(a[0] + b[0], a[1] + b[1]))


def _continuation_boundary(
    p0: tuple[float, float],
    bearing0: float,
    p3: tuple[float, float],
    bearing3: float,
) -> list[tuple[float, float]]:
    """Bezier edge used verbatim by both polygons beside an allocated seam."""
    u0, u3 = _unit(bearing0), _unit(bearing3)
    p0 = (
        p0[0] - u0[0] * CONTINUATION_PATCH_M,
        p0[1] - u0[1] * CONTINUATION_PATCH_M,
    )
    p3 = (
        p3[0] + u3[0] * CONTINUATION_PATCH_M,
        p3[1] + u3[1] * CONTINUATION_PATCH_M,
    )
    span = math.dist(p0, p3)
    handle = max(span / 3.0, CONTINUATION_PATCH_M)
    p1 = (p0[0] + u0[0] * handle, p0[1] + u0[1] * handle)
    p2 = (p3[0] - u3[0] * handle, p3[1] - u3[1] * handle)
    points = []
    for i in range(BEZIER_SAMPLES + 1):
        t = i / BEZIER_SAMPLES
        m = 1.0 - t
        a, b, c, d = m * m * m, 3 * m * m * t, 3 * m * t * t, t * t * t
        points.append((
            a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
        ))
    return points


def _polygon_between_boundaries(
    left: list[tuple[float, float]],
    right: list[tuple[float, float]],
) -> Polygon:
    surface = Polygon(left + list(reversed(right)))
    if not surface.is_valid:
        repaired = surface.buffer(0)
        surface = repaired if repaired.geom_type == "Polygon" else surface.convex_hull
    return surface


def _planned_group_surfaces(
    allocations: list[tuple[dict, dict, list]],
    approach_trims: list[float],
    exit_trim: float,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> list[Polygon]:
    """Build surfaces, sharing exact seam curves for disjoint target blocks."""
    if len(allocations) < 2 or not _disjoint_target_blocks(allocations):
        return [
            _planned_surface(
                approach,
                target,
                approach["lane_ends"],
                target_lanes,
                approach_trim,
                exit_trim,
                lane_rows_by_id,
                path_cache,
            )
            for (approach, target, target_lanes), approach_trim in zip(
                allocations, approach_trims
            )
        ]

    source_sections = [
        _section_at_trim(
            approach,
            approach["lane_ends"],
            approach_trim,
            at_entry=False,
            lane_rows_by_id=lane_rows_by_id,
            path_cache=path_cache,
        )
        for (approach, _target, _target_lanes), approach_trim in zip(
            allocations, approach_trims
        )
    ]
    target_sections = [
        _section_at_trim(
            target,
            target_lanes,
            exit_trim,
            at_entry=True,
            lane_rows_by_id=lane_rows_by_id,
            path_cache=path_cache,
        )
        for _approach, target, target_lanes in allocations
    ]

    boundaries = [
        _continuation_boundary(
            source_sections[0][1],
            source_sections[0][3],
            target_sections[0][1],
            target_sections[0][3],
        )
    ]
    for index in range(len(allocations) - 1):
        source_left = source_sections[index]
        source_right = source_sections[index + 1]
        target_left = target_sections[index]
        target_right = target_sections[index + 1]
        source_seam = (
            (source_left[2][0] + source_right[1][0]) / 2.0,
            (source_left[2][1] + source_right[1][1]) / 2.0,
        )
        target_seam = (
            (target_left[2][0] + target_right[1][0]) / 2.0,
            (target_left[2][1] + target_right[1][1]) / 2.0,
        )
        boundaries.append(_continuation_boundary(
            source_seam,
            _mean_bearing(source_left[3], source_right[3]),
            target_seam,
            _mean_bearing(target_left[3], target_right[3]),
        ))
    boundaries.append(_continuation_boundary(
        source_sections[-1][2],
        source_sections[-1][3],
        target_sections[-1][2],
        target_sections[-1][3],
    ))
    return [
        _polygon_between_boundaries(boundaries[index], boundaries[index + 1])
        for index in range(len(allocations))
    ]


def _disjoint_target_blocks(allocations: list[tuple[dict, dict, list]]) -> bool:
    seen: set[int] = set()
    for _approach, _target, lanes in allocations:
        lane_numbers = {lane for lane, _point in lanes}
        if seen & lane_numbers:
            return False
        seen.update(lane_numbers)
    return True


def _non_crossing_approach_trim(
    allocations: list[tuple[dict, dict, list]],
    approach_trims: list[float],
    exit_trim: float,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> Optional[float]:
    """Find a common upstream station whose disjoint connector edges do not cross."""
    if len(allocations) < 2 or not _disjoint_target_blocks(allocations):
        return max(approach_trims, default=0.0)

    start = max(approach_trims, default=0.0)
    limit = min(
        CONTINUATION_CONTESTED_MAX_TRIM_M,
        min(
            min(approach["lane_lengths"].values()) * 0.6
            for approach, _target, _lanes in allocations
        ) + CONTINUATION_CONTESTED_TRIM_STEP_M,
    )
    target_u = _unit(allocations[0][1]["leave_bearing"])
    target_left = (-target_u[1], target_u[0])

    def lateral(point: tuple[float, float]) -> float:
        return point[0] * target_left[0] + point[1] * target_left[1]

    def normal_edges_cross(candidate: float) -> bool:
        """Whether the still-visible incoming outside strokes form an X."""
        if lane_rows_by_id is None:
            return False
        for index in range(len(allocations) - 1):
            left_approach = allocations[index][0]
            right_approach = allocations[index + 1][0]
            left_lane = left_approach["lane_ends"][-1][0]
            right_lane = right_approach["lane_ends"][0][0]
            left_path = _lane_path(
                left_approach, left_lane, lane_rows_by_id, path_cache
            )
            right_path = _lane_path(
                right_approach, right_lane, lane_rows_by_id, path_cache
            )
            if left_path is None or right_path is None:
                continue
            left_visible = substring(
                left_path, 0.0, max(0.0, left_path.length - candidate)
            )
            right_visible = substring(
                right_path, 0.0, max(0.0, right_path.length - candidate)
            )
            if (
                left_visible.geom_type != "LineString"
                or right_visible.geom_type != "LineString"
            ):
                continue
            left_edge = left_visible.offset_curve(-left_approach["width_m"] / 2.0)
            right_edge = right_visible.offset_curve(right_approach["width_m"] / 2.0)
            if left_edge.crosses(right_edge):
                return True
        return False

    candidate = start
    while candidate <= limit + 1e-9:
        source_sections = [
            _section_at_trim(
                approach,
                approach["lane_ends"],
                candidate,
                at_entry=False,
                lane_rows_by_id=lane_rows_by_id,
                path_cache=path_cache,
            )
            for approach, _target, _target_lanes in allocations
        ]
        separated = all(
            lateral(source_sections[index][2])
            >= lateral(source_sections[index + 1][1]) - CONTINUATION_EDGE_SHIFT_EPS_M
            for index in range(len(source_sections) - 1)
        )
        surfaces = _planned_group_surfaces(
            allocations,
            [candidate] * len(allocations),
            exit_trim,
            lane_rows_by_id,
            path_cache,
        )
        if separated and not normal_edges_cross(candidate) and not any(
            surfaces[left].boundary.crosses(surfaces[right].boundary)
            for left in range(len(surfaces))
            for right in range(left + 1, len(surfaces))
        ):
            return candidate
        candidate += CONTINUATION_CONTESTED_TRIM_STEP_M
    return None


def _diverge_allocations(
    approach: dict,
    candidates: list[tuple[dict, int, float]],
) -> list[tuple[dict, dict, list, list]]:
    """Split a tagged approach into lane blocks selecting distinct exits."""
    tokens = approach.get("turn_tokens")
    if not tokens or len(tokens) != approach["lane_count"] or len(candidates) < 2:
        return []
    assigned: dict[tuple[int, str], tuple[dict, list]] = {}
    for lane_item, lane_tokens in zip(approach["lane_ends"], tokens):
        ideals = [
            _TOKEN_ANGLE[token]
            for token in lane_tokens
            if token in _TOKEN_ANGLE and _TOKEN_ANGLE[token] is not None
        ]
        if not ideals:
            return []
        target, _road_score, _turn = min(
            candidates,
            key=lambda candidate: (
                min(abs(candidate[2] - ideal) for ideal in ideals),
                candidate[1],
                abs(candidate[2]),
            ),
        )
        error = min(abs(_turn - ideal) for ideal in ideals)
        if error > ANGLE_TOLERANCE_DEG:
            return []
        assigned.setdefault(target["key"], (target, []))[1].append(lane_item)
    if len(assigned) < 2:
        return []
    flows = [
        (approach, target, lanes, target["lane_starts"])
        for target, lanes in assigned.values()
    ]
    return sorted(flows, key=lambda flow: flow[2][0][0])


def _planned_diverge_boundaries(
    flows: list[tuple[dict, dict, list, list]],
    approach_trim: float,
    exit_trim: float,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> list[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    """The exact left/right curves used by each branch of a diverge."""
    sections = []
    for approach, target, approach_lanes, target_lanes in flows:
        source = _section_at_trim(
            approach,
            approach_lanes,
            approach_trim,
            at_entry=False,
            lane_rows_by_id=lane_rows_by_id,
            path_cache=path_cache,
        )
        destination = _section_at_trim(
            target,
            target_lanes,
            exit_trim,
            at_entry=True,
            lane_rows_by_id=lane_rows_by_id,
            path_cache=path_cache,
        )
        sections.append((source, destination))

    boundaries = [
        (
            _continuation_boundary(source[1], source[3], destination[1], destination[3]),
            _continuation_boundary(source[2], source[3], destination[2], destination[3]),
        )
        for source, destination in sections
    ]
    # Adjacent source lane blocks meet on one divider. Derive both outgoing
    # gore edges from the exact same seam point and tangent so floating-point
    # differences between independently sampled lane offsets cannot swap their
    # order immediately after the split.
    for index in range(len(boundaries) - 1):
        source_left, target_left = sections[index]
        source_right, target_right = sections[index + 1]
        source_seam = (
            (source_left[2][0] + source_right[1][0]) / 2.0,
            (source_left[2][1] + source_right[1][1]) / 2.0,
        )
        source_bearing = _mean_bearing(source_left[3], source_right[3])
        boundaries[index] = (
            boundaries[index][0],
            _continuation_boundary(
                source_seam,
                source_bearing,
                target_left[2],
                target_left[3],
            ),
        )
        boundaries[index + 1] = (
            _continuation_boundary(
                source_seam,
                source_bearing,
                target_right[1],
                target_right[3],
            ),
            boundaries[index + 1][1],
        )
    return boundaries


def _planned_diverge_surfaces(
    flows: list[tuple[dict, dict, list, list]],
    approach_trim: float,
    exit_trim: float,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> list[Polygon]:
    return [
        _polygon_between_boundaries(left, right)
        for left, right in _planned_diverge_boundaries(
            flows,
            approach_trim,
            exit_trim,
            lane_rows_by_id,
            path_cache,
        )
    ]


def _non_crossing_exit_trim(
    flows: list[tuple[dict, dict, list, list]],
    start: float,
    approach_trim: float,
    lane_rows_by_id: Optional[dict[str, dict]],
    path_cache: dict[tuple[tuple[int, str], int], LineString],
) -> Optional[float]:
    """Mirror of merge trimming: walk diverging exits past their edge crossing."""
    approach = flows[0][0]
    approach_u = _unit(approach["arrive_bearing"])
    approach_left = (-approach_u[1], approach_u[0])

    def lateral(point: tuple[float, float]) -> float:
        return point[0] * approach_left[0] + point[1] * approach_left[1]

    limit = min(
        CONTINUATION_CONTESTED_MAX_TRIM_M,
        min(
            min(target["lane_lengths"].values()) * 0.6
            for _approach, target, _approach_lanes, _target_lanes in flows
        ) + CONTINUATION_CONTESTED_TRIM_STEP_M,
    )
    candidate = start
    while candidate <= limit + 1e-9:
        target_sections = [
            _section_at_trim(
                target,
                target_lanes,
                candidate,
                at_entry=True,
                lane_rows_by_id=lane_rows_by_id,
                path_cache=path_cache,
            )
            for _approach, target, _approach_lanes, target_lanes in flows
        ]
        separated = all(
            lateral(target_sections[index][2])
            >= lateral(target_sections[index + 1][1]) - CONTINUATION_EDGE_SHIFT_EPS_M
            for index in range(len(target_sections) - 1)
        )
        boundaries = _planned_diverge_boundaries(
            flows,
            approach_trim,
            candidate,
            lane_rows_by_id,
            path_cache,
        )
        facing_edges_cross = any(
            LineString(boundaries[index][1]).crosses(
                LineString(boundaries[index + 1][0])
            )
            for index in range(len(boundaries) - 1)
        )
        if separated and not facing_edges_cross:
            return candidate
        candidate += CONTINUATION_CONTESTED_TRIM_STEP_M
    return None


def make_continuation_rows(
    records: dict[tuple[int, str], dict],
    lane_rows_by_id: Optional[dict[str, dict]] = None,
) -> list[dict]:
    """Join the straightest same-road flows that share an original OSM node."""
    by_entry: dict[tuple[float, float], list[dict]] = defaultdict(list)
    for rec in records.values():
        by_entry[rec["entry_node"]].append(rec)

    chosen_by_exit: dict[tuple[int, str], list[tuple[dict, dict]]] = defaultdict(list)
    diverge_groups: list[list[tuple[dict, dict, list, list]]] = []
    for approach in records.values():
        best = None
        best_key = None
        candidates = []
        for exit_rec in by_entry.get(approach["exit_node"], ()):
            if exit_rec["osm_id"] == approach["osm_id"]:
                continue
            road_score = _road_match_score(approach, exit_rec)
            if road_score is None:
                continue
            turn = abs(_norm_deg(exit_rec["leave_bearing"] - approach["arrive_bearing"]))
            if turn > CONTINUATION_MAX_TURN_DEG:
                continue
            signed_turn = _norm_deg(
                exit_rec["leave_bearing"] - approach["arrive_bearing"]
            )
            candidates.append((exit_rec, road_score, signed_turn))
            key = (road_score, turn, abs(exit_rec["lane_count"] - approach["lane_count"]))
            if best_key is None or key < best_key:
                best, best_key = exit_rec, key
        diverge = _diverge_allocations(approach, candidates)
        if diverge:
            diverge_groups.append(diverge)
            continue
        if best is None:
            continue
        chosen_by_exit[best["key"]].append((approach, best))

    rows: list[dict[str, Any]] = []
    trim_requests: dict[str, dict[str, float]] = defaultdict(dict)
    planned = []
    marking_candidates: list[dict[str, Any]] = []
    path_cache: dict[tuple[tuple[int, str], int], LineString] = {}
    for group in chosen_by_exit.values():
        allocations = _allocate_target_blocks(group)
        if not allocations:
            continue
        # Merge-tagged lane geometry already converges onto its surviving
        # neighbour. Its endpoint is coincident with that neighbour, so only
        # the physically distinct survivor bands define the polygon section.
        # Flow metadata below still retains the merging lane itself.
        approach_blocks = [
            (
                _geometry_approach_block(approach, target_lanes)
                if len(allocations) == 1
                else approach["lane_ends"]
            )
            for approach, _best, target_lanes in allocations
        ]
        base_trims = [
            _continuation_trim(
                approach,
                best,
                approach_lanes=approach_lanes,
                exit_lanes=target_lanes,
            )
            for (approach, best, target_lanes), approach_lanes in zip(
                allocations, approach_blocks
            )
        ]
        exit_trim = max((trim[1] for trim in base_trims), default=0.0)
        common_trim = _non_crossing_approach_trim(
            allocations,
            [trim[0] for trim in base_trims],
            exit_trim,
            lane_rows_by_id,
            path_cache,
        )
        # If a malformed or extremely short contested group cannot produce
        # non-crossing surfaces, omit it rather than restore the visual X.
        if common_trim is None:
            continue
        use_common_trim = len(allocations) > 1 and _disjoint_target_blocks(allocations)
        final_approach_trims = [
            common_trim if use_common_trim else trim[0]
            for trim in base_trims
        ]
        if len(allocations) == 1:
            approach, best, target_lanes = allocations[0]
            surfaces = [_planned_surface(
                approach,
                best,
                approach_blocks[0],
                target_lanes,
                final_approach_trims[0],
                exit_trim,
                lane_rows_by_id,
                path_cache,
            )]
        else:
            surfaces = _planned_group_surfaces(
                allocations,
                final_approach_trims,
                exit_trim,
                lane_rows_by_id,
                path_cache,
            )
        for (
            (approach, best, target_lanes),
            approach_lanes,
            final_trim,
            surface,
        ) in zip(allocations, approach_blocks, final_approach_trims, surfaces):
            planned.append((
                approach,
                best,
                approach_lanes,
                target_lanes,
                final_trim,
                exit_trim,
                surface,
                None,
            ))

    for flows in diverge_groups:
        base_trims = [
            _continuation_trim(
                approach,
                target,
                approach_lanes=approach_lanes,
                exit_lanes=target_lanes,
            )
            for approach, target, approach_lanes, target_lanes in flows
        ]
        approach_trim = max((trim[0] for trim in base_trims), default=0.0)
        exit_start = max((trim[1] for trim in base_trims), default=0.0)
        exit_trim = _non_crossing_exit_trim(
            flows,
            exit_start,
            approach_trim,
            lane_rows_by_id,
            path_cache,
        )
        if exit_trim is None:
            continue
        boundary_pairs = _planned_diverge_boundaries(
            flows,
            approach_trim,
            exit_trim,
            lane_rows_by_id,
            path_cache,
        )
        surfaces = [
            _polygon_between_boundaries(left, right)
            for left, right in boundary_pairs
        ]
        for (
            (approach, target, approach_lanes, target_lanes),
            surface,
            boundary_pair,
        ) in zip(flows, surfaces, boundary_pairs):
            planned.append((
                approach,
                target,
                approach_lanes,
                target_lanes,
                approach_trim,
                exit_trim,
                surface,
                boundary_pair,
            ))

    for (
        approach,
        best,
        approach_lanes,
        target_lanes,
        approach_trim,
        exit_trim,
        surface,
        boundary_pair,
    ) in planned:
        lane_map = _continuation_lane_map(
            approach,
            best,
            approach_lanes,
            target_lanes,
        )
        if lane_rows_by_id is not None and (approach_trim or exit_trim):
            approach_side = "end" if approach["direction"] == "fwd" else "start"
            exit_side = "start" if best["direction"] == "fwd" else "end"
            approach_trim_lanes = (
                list(lane_map)
                if len(lane_map) > len(approach_lanes)
                else [lane for lane, _point in approach_lanes]
            )
            for lane in approach_trim_lanes:
                row_id = approach["row_ids"][lane]
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
        wgs84 = transform(_RD_TO_WGS84.transform, surface)
        connector_id = (
            f"{approach['osm_id']}:join:{approach['direction']}:"
            f"{best['osm_id']}:{best['direction']}"
        )
        rows.append({
            "id": connector_id,
            "source_id": approach["osm_id"],
            "lane": 1,
            "lane_count": len(approach_lanes),
            "direction": approach["direction"],
            "role": "connector",
            "highway": approach["highway"],
            "name": approach["name"],
            "ref": approach["ref"],
            "width_m": approach["width_m"],
            "geom": wgs84.wkt,
            "raw": {
                "continuation": True,
                "from_lanes": (
                    list(lane_map)
                    if len(lane_map) > len(approach_lanes)
                    else [lane for lane, _point in approach_lanes]
                ),
                "to_osm_id": best["osm_id"],
                "to_lanes": [lane for lane, _ in target_lanes],
                "lane_map": [
                    {"from": source_lane, "to": target_lane}
                    for source_lane, target_lane in lane_map.items()
                ],
            },
        })

        edge_lines: list[LineString] = []
        if boundary_pair is not None:
            edge_lines = [
                LineString(boundary_pair[0]),
                LineString(boundary_pair[1]),
            ]
        else:
            surface_coords = list(surface.exterior.coords)
            samples = BEZIER_SAMPLES + 1
            if len(surface_coords) >= samples * 2:
                edge_lines = [
                    LineString(surface_coords[:samples]),
                    LineString(reversed(surface_coords[samples:samples * 2])),
                ]
        if edge_lines:
            for side, line in zip(("left", "right"), edge_lines):
                rounded = tuple((round(x, 3), round(y, 3)) for x, y in line.coords)
                marking_candidates.append({
                    "key": min(rounded, tuple(reversed(rounded))),
                    "id": f"{connector_id}:mark:{side}",
                    "line": line,
                    "kind": "edge",
                    "approach": approach,
                    "best": best,
                    "from_lanes": approach_lanes,
                    "to_lanes": target_lanes,
                })

        divider_fractions = _divider_transition_fractions(
            lane_map,
            approach_lanes,
            target_lanes,
        )
        if not divider_fractions:
            divider_count = max(len(approach_lanes), len(target_lanes)) - 1
            divider_fractions = [
                (
                    divider / (divider_count + 1),
                    divider / (divider_count + 1),
                )
                for divider in range(1, divider_count + 1)
            ]
        if divider_fractions:
            _c0, left0, right0, bearing0 = _section_at_trim(
                approach,
                approach_lanes,
                approach_trim,
                at_entry=False,
                lane_rows_by_id=lane_rows_by_id,
                path_cache=path_cache,
            )
            _c3, left3, right3, bearing3 = _section_at_trim(
                best,
                target_lanes,
                exit_trim,
                at_entry=True,
                lane_rows_by_id=lane_rows_by_id,
                path_cache=path_cache,
            )
            for divider, (source_fraction, target_fraction) in enumerate(
                divider_fractions,
                start=1,
            ):
                p0 = (
                    left0[0] + (right0[0] - left0[0]) * source_fraction,
                    left0[1] + (right0[1] - left0[1]) * source_fraction,
                )
                p3 = (
                    left3[0] + (right3[0] - left3[0]) * target_fraction,
                    left3[1] + (right3[1] - left3[1]) * target_fraction,
                )
                marking_candidates.append({
                    "key": None,
                    "id": f"{connector_id}:mark:divider:{divider}",
                    "line": LineString(_continuation_boundary(
                        p0, bearing0, p3, bearing3
                    )),
                    "kind": "divider",
                    "approach": approach,
                    "best": best,
                    "from_lanes": approach_lanes,
                    "to_lanes": target_lanes,
                })

    edge_counts: dict[Any, int] = defaultdict(int)
    for marking in marking_candidates:
        if marking["key"] is not None:
            edge_counts[marking["key"]] += 1
    emitted_shared: set[Any] = set()
    for marking in marking_candidates:
        shared = marking["key"] is not None and edge_counts[marking["key"]] > 1
        if shared:
            if marking["key"] in emitted_shared:
                continue
            emitted_shared.add(marking["key"])
        approach = marking["approach"]
        best = marking["best"]
        rows.append({
            "id": marking["id"],
            "source_id": approach["osm_id"],
            "lane": 1,
            "lane_count": len(marking["from_lanes"]),
            "direction": approach["direction"],
            "role": "connector_marking",
            "highway": approach["highway"],
            "name": approach["name"],
            "ref": approach["ref"],
            "width_m": approach["width_m"],
            "geom": transform(_RD_TO_WGS84.transform, marking["line"]).wkt,
            "raw": {
                "continuation": True,
                "continuation_marking": "divider" if shared else marking["kind"],
                "to_osm_id": best["osm_id"],
                "from_lanes": [lane for lane, _point in marking["from_lanes"]],
                "to_lanes": [lane for lane, _point in marking["to_lanes"]],
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


def combine_connector_rows(
    turn_rows: list[dict],
    continuation_rows: list[dict],
) -> list[dict]:
    """Let an exact-node continuation own movements it already renders.

    ``make_connector_rows`` uses a 25m junction search and emits one thick
    centreline per lane. ``make_continuation_rows`` can model the same tagged
    movement as coordinated polygons when all ways share the exact OSM node.
    Keeping both produces overlapping asphalt and duplicate diagonal curves.
    """
    covered_movements = {
        (
            row["source_id"],
            source_lane,
            row["raw"]["to_osm_id"],
        )
        for row in continuation_rows
        if row["role"] == "connector"
        and row["raw"].get("continuation")
        for source_lane in row["raw"].get("from_lanes", ())
    }
    unique_turn_rows = [
        row
        for row in turn_rows
        if (
            row["source_id"],
            row["lane"],
            row["raw"].get("to_osm_id"),
        )
        not in covered_movements
    ]
    return unique_turn_rows + continuation_rows
