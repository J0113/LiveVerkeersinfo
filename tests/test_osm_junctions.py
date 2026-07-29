"""Unit tests for junction lane connectors derived from turn:lanes.

Coordinates are real Noord-Holland ones (the Provincialeweg junction that
motivated the feature, way 1267507394 `left|left|through|right`), so a
WGS84-vs-metres slip fails here rather than passing a planar sanity check.
"""

from __future__ import annotations

import math

from pyproj import Geod
from shapely import from_wkt
from shapely.geometry import LineString
from shapely.ops import transform

from ndwinfo.parsers.osm_junctions import (
    _RD_TO_WGS84,
    _WGS84_TO_RD,
    BEZIER_SAMPLES,
    CONTINUATION_PATCH_M,
    combine_connector_rows,
    continuation_records,
    junction_record,
    make_connector_rows,
    make_continuation_rows,
)
from ndwinfo.parsers.osm_lanes import make_all_lane_rows, make_lane_rows

GEOD = Geod(ellps="WGS84")

# A junction node, and approaches/exits laid out around it. ~1 deg lon = 68.0km
# and 1 deg lat = 111.2km at this latitude.
NODE = (4.713322, 52.5169868)
M_LON = 1.0 / 68000.0
M_LAT = 1.0 / 111200.0


def _point(east_m: float, north_m: float) -> tuple[float, float]:
    """A (lon, lat) given as metres east/north of NODE."""
    return (NODE[0] + east_m * M_LON, NODE[1] + north_m * M_LAT)


def _line(*offsets_m) -> LineString:
    """Line through points given as (east_m, north_m) from NODE."""
    return LineString([_point(e, n) for e, n in offsets_m])


def _record(osm_id: int, tags: dict, line: LineString, highway: str = "primary") -> dict:
    rows = make_lane_rows(osm_id, highway, tags, line)
    rec = junction_record(osm_id, tags, rows)
    assert rec is not None, f"way {osm_id} produced no junction record"
    return rec


def _az_diff(a: float, b: float) -> float:
    """Absolute angle between two azimuths, wrap-safe (Geod.inv returns -180..180)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


# Approach: heading south (from 60m north down to the node), 4 lanes.
APPROACH_LINE = _line((0, 60), (0, 0))
# Through exit: continues south, starting 8m past the node — a junction box has
# width, and OSM gives each side of it its own node.
THROUGH_LINE = _line((0, -8), (0, -60))
# Through exit that begins exactly where the approach ends.
THROUGH_TOUCHING_LINE = _line((0, 0), (0, -60))
# Left exit: heads east, starting 18m from the node like the real junction's
# left target does — OSM splits the junction across several nodes.
LEFT_LINE = _line((6, -17), (60, -17))
# Right exit: heads west.
RIGHT_LINE = _line((-6, -17), (-60, -17))


def _connectors(approach_tags: dict, exits: list[tuple[int, dict, LineString]]) -> list[dict]:
    records = {1: _record(1, approach_tags, APPROACH_LINE)}
    for osm_id, tags, line in exits:
        records[osm_id] = _record(osm_id, tags, line)
    return make_connector_rows(records)


def _continuations(ways: list[tuple[int, dict, LineString]]) -> list[dict]:
    records = {}
    lane_rows = {}
    for osm_id, tags, line in ways:
        rows = make_lane_rows(osm_id, "primary", tags, line)
        lane_rows.update((row["id"], row) for row in rows)
        for record in continuation_records(osm_id, tags, line, rows):
            records[record["key"]] = record
    return [
        row for row in make_continuation_rows(records, lane_rows)
        if row["role"] == "connector"
    ]


def _continuations_with_highways(
    ways: list[tuple[int, str, dict, LineString]],
    *,
    include_lane_rows: bool = False,
    include_markings: bool = False,
    merge_context: bool = False,
) -> list[dict] | tuple[list[dict], dict[str, dict]]:
    records = {}
    lane_rows = {}
    generated_by_way: dict[int, list[dict]] = {}
    if merge_context:
        for row in make_all_lane_rows(ways):
            generated_by_way.setdefault(row["source_id"], []).append(row)
    for osm_id, highway, tags, line in ways:
        rows = (
            generated_by_way.get(osm_id, [])
            if merge_context
            else make_lane_rows(osm_id, highway, tags, line)
        )
        lane_rows.update((row["id"], row) for row in rows)
        for record in continuation_records(osm_id, tags, line, rows):
            records[record["key"]] = record
    continuation_rows = make_continuation_rows(records, lane_rows)
    selected = (
        continuation_rows
        if include_markings
        else [row for row in continuation_rows if row["role"] == "connector"]
    )
    return (selected, lane_rows) if include_lane_rows else selected


def _surface_centreline(row: dict) -> LineString:
    """Recover the sampled centreline from a continuation surface's two edges."""
    surface = from_wkt(row["geom"])
    coords = list(surface.exterior.coords)
    samples = BEZIER_SAMPLES + 1
    left = coords[:samples]
    right = list(reversed(coords[samples:2 * samples]))
    return LineString([
        ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        for a, b in zip(left, right)
    ])


def _allocated_overlap_bound(a: dict, b: dict) -> float:
    """Area bound implied by allocated widths and sampled centreline spacing."""
    centre_a = transform(_WGS84_TO_RD.transform, _surface_centreline(a))
    centre_b = transform(_WGS84_TO_RD.transform, _surface_centreline(b))
    steps = 64
    overlap_widths = []
    for index in range(steps + 1):
        fraction = index / steps
        width_a = (
            (1.0 - fraction) * a["lane_count"]
            + fraction * len(a["raw"]["to_lanes"])
        ) * a["width_m"]
        width_b = (
            (1.0 - fraction) * b["lane_count"]
            + fraction * len(b["raw"]["to_lanes"])
        ) * b["width_m"]
        pa = centre_a.interpolate(fraction, normalized=True)
        pb = centre_b.interpolate(fraction, normalized=True)
        overlap_widths.append(max(0.0, (width_a + width_b) / 2.0 - pa.distance(pb)))
    spacing = max(centre_a.length, centre_b.length) / steps
    integrated = sum(
        (overlap_widths[index] + overlap_widths[index + 1]) * 0.5 * spacing
        for index in range(steps)
    )
    seam = (a["width_m"] + b["width_m"]) * CONTINUATION_PATCH_M * 2.0
    # Centreline-normal integration slightly underestimates a curved polygon's
    # diagonal edge projection. Keep a proportional margin rather than a fixed
    # square-metre allowance.
    return integrated * 1.2 + seam


def test_left_left_through_right_connects_every_lane():
    rows = _connectors(
        {"lanes": "4", "oneway": "yes", "turn:lanes": "left|left|through|right"},
        [
            (2, {"lanes": "2", "oneway": "yes"}, LEFT_LINE),
            (3, {"lanes": "1", "oneway": "yes"}, THROUGH_LINE),
            (4, {"lanes": "1", "oneway": "yes"}, RIGHT_LINE),
        ],
    )
    by_lane = {r["lane"]: r for r in rows}
    assert sorted(by_lane) == [1, 2, 3, 4]
    assert all(r["role"] == "connector" for r in rows)
    # The two left lanes feed the left way's two lanes, in order.
    assert by_lane[1]["raw"]["to_osm_id"] == 2 and by_lane[1]["raw"]["to_lane"] == 1
    assert by_lane[2]["raw"]["to_osm_id"] == 2 and by_lane[2]["raw"]["to_lane"] == 2
    assert by_lane[3]["raw"]["to_osm_id"] == 3  # through
    assert by_lane[4]["raw"]["to_osm_id"] == 4  # right


def test_connector_starts_on_its_approach_lane_and_ends_on_its_exit_lane():
    approach_tags = {"lanes": "4", "oneway": "yes", "turn:lanes": "left|left|through|right"}
    exit_tags = {"lanes": "2", "oneway": "yes"}
    rows = _connectors(approach_tags, [(2, exit_tags, LEFT_LINE)])
    conn = next(r for r in rows if r["lane"] == 1)

    approach_lane1 = next(
        r
        for r in make_lane_rows(1, "primary", approach_tags, APPROACH_LINE)
        if r["lane"] == 1
    )
    exit_lane1 = next(
        r for r in make_lane_rows(2, "primary", exit_tags, LEFT_LINE) if r["lane"] == 1
    )
    curve = from_wkt(conn["geom"])

    def _gap(a, b):
        return GEOD.inv(a[0], a[1], b[0], b[1])[2]

    assert _gap(curve.coords[0], from_wkt(approach_lane1["geom"]).coords[-1]) < 0.1
    assert _gap(curve.coords[-1], from_wkt(exit_lane1["geom"]).coords[0]) < 0.1


def test_connector_leaves_and_arrives_along_the_road():
    # A corner, not a straight line: it must leave tangent to the approach
    # (heading south) and arrive tangent to the exit (heading east).
    rows = _connectors(
        {"lanes": "4", "oneway": "yes", "turn:lanes": "left|left|through|right"},
        [(2, {"lanes": "2", "oneway": "yes"}, LEFT_LINE)],
    )
    curve = from_wkt(next(r for r in rows if r["lane"] == 1)["geom"])
    leave_az = GEOD.inv(*curve.coords[0], *curve.coords[1])[0]
    arrive_az = GEOD.inv(*curve.coords[-2], *curve.coords[-1])[0]
    assert _az_diff(leave_az, 180.0) < 15  # still heading south out of the approach
    assert _az_diff(arrive_az, 90.0) < 15  # heading east into the exit
    # A curve, not a chord: it bulges past the straight line between its ends.
    chord = GEOD.inv(*curve.coords[0], *curve.coords[-1])[2]
    assert GEOD.geometry_length(curve) > chord * 1.05


def test_turn_with_no_exit_in_range_is_skipped():
    # The real Provincialeweg case: the right turn leads to a road class this
    # project doesn't ingest, so there's nothing to connect to.
    rows = _connectors(
        {"lanes": "4", "oneway": "yes", "turn:lanes": "left|left|through|right"},
        [(3, {"lanes": "1", "oneway": "yes"}, THROUGH_LINE)],
    )
    assert {r["lane"] for r in rows} == {3}  # only the through lane resolved


def test_exit_too_far_away_is_not_the_same_junction():
    far = _line((300, -17), (360, -17))
    rows = _connectors(
        {"lanes": "4", "oneway": "yes", "turn:lanes": "left|left|through|right"},
        [(2, {"lanes": "2", "oneway": "yes"}, far)],
    )
    assert rows == []


def test_opposite_carriageway_is_not_a_turn():
    # A way leaving north from the junction is the other side of the same road,
    # not a movement -- no token should land on it.
    back = _line((3, 4), (3, 60))
    rows = _connectors(
        {"lanes": "4", "oneway": "yes", "turn:lanes": "left|left|through|right"},
        [(2, {"lanes": "2", "oneway": "yes"}, back)],
    )
    assert rows == []


def test_nearer_exit_wins_when_two_look_equally_through():
    # A 25m radius also reaches a parallel carriageway heading the same way,
    # which is indistinguishable from `through` by angle alone.
    near = _line((0, -8), (0, -60))
    parallel = _line((14, -8), (14, -60))
    rows = _connectors(
        {"lanes": "1", "oneway": "yes", "turn:lanes": "through"},
        [
            (2, {"lanes": "1", "oneway": "yes"}, parallel),
            (3, {"lanes": "1", "oneway": "yes"}, near),
        ],
    )
    assert [r["raw"]["to_osm_id"] for r in rows] == [3]


def test_multi_token_lane_connects_to_both_movements():
    rows = _connectors(
        {"lanes": "2", "oneway": "yes", "turn:lanes": "left;through|through"},
        [
            (2, {"lanes": "1", "oneway": "yes"}, LEFT_LINE),
            (3, {"lanes": "2", "oneway": "yes"}, THROUGH_LINE),
        ],
    )
    lane1 = [r for r in rows if r["lane"] == 1]
    assert {r["raw"]["to_osm_id"] for r in lane1} == {2, 3}
    assert len({r["id"] for r in rows}) == len(rows)  # ids stay unique per movement


def test_turn_lanes_cardinality_mismatch_is_ignored():
    rows = _connectors(
        {"lanes": "4", "oneway": "yes", "turn:lanes": "left|through"},  # 2 tokens, 4 lanes
        [(3, {"lanes": "1", "oneway": "yes"}, THROUGH_LINE)],
    )
    assert rows == []


def test_way_without_turn_lanes_produces_no_connectors():
    rows = _connectors(
        {"lanes": "4", "oneway": "yes"},
        [(3, {"lanes": "1", "oneway": "yes"}, THROUGH_LINE)],
    )
    assert rows == []


def test_touching_exit_needs_no_connector():
    # The through way starts exactly where the approach's lanes end, so the
    # bands already meet -- a connector would be a degenerate stub.
    rows = _connectors(
        {"lanes": "1", "oneway": "yes", "turn:lanes": "through"},
        [(3, {"lanes": "1", "oneway": "yes"}, THROUGH_TOUCHING_LINE)],
    )
    assert rows == []


def test_through_lanes_keep_their_cross_section_position_on_a_wider_exit():
    # Real Provincialeweg shape: the left lane turns away and the two through
    # lanes continue onto a four-lane way. Numbering the through-only group from
    # exit lane 1 shifts both connectors left; their absolute position maps to
    # exit lanes 3 and 4 instead.
    rows = _connectors(
        {"lanes": "3", "oneway": "yes", "turn:lanes": "left|through|through"},
        [(2, {"lanes": "4", "oneway": "yes"}, THROUGH_LINE)],
    )
    assert {r["lane"]: r["raw"]["to_lane"] for r in rows} == {2: 3, 3: 4}


def test_none_lane_after_merge_stays_on_the_same_exit_lane():
    rows = _connectors(
        {"lanes": "2", "oneway": "yes", "turn:lanes": "merge_to_right|none"},
        [(2, {"lanes": "2", "oneway": "yes"}, THROUGH_LINE)],
    )
    assert {r["lane"]: r["raw"]["to_lane"] for r in rows} == {2: 2}


def test_continuation_fans_across_a_wider_cross_section_without_internal_seams():
    approach = _line((0, 60), (0, 0))
    exit_line = _line((0, 0), (0, -60))
    rows = _continuations([
        (1, {"lanes": "3", "oneway": "yes", "name": "Provincialeweg", "ref": "N203"}, approach),
        (2, {"lanes": "4", "oneway": "yes", "name": "Provincialeweg", "ref": "N203"}, exit_line),
    ])
    # One surface spans the whole transition.  Separate per-lane polygons
    # overlap and MapLibre antialiases every internal edge, producing diagonal
    # pale seams at exactly this kind of lane-count change.
    assert len(rows) == 1
    assert rows[0]["raw"]["to_lanes"] == [1, 2, 3, 4]
    assert rows[0]["raw"]["continuation"] is True
    surface = from_wkt(rows[0]["geom"])
    assert surface.geom_type == "Polygon"
    assert surface.is_valid


def test_contested_a44_merge_allocates_ramp_and_mainline_to_separate_blocks():
    # Exact topology from the reported junction:
    # 551716643 (one-lane on-ramp) + 948690091 (two-lane mainline)
    # -> 386967467 (three lanes).
    node = (4.622332, 52.231048)
    ramp = LineString([(4.627301, 52.231691), node])
    mainline = LineString([(4.624278, 52.231206), node])
    target = LineString([node, (4.618934, 52.230765)])
    common = {"ref": "A44", "oneway": "yes"}
    rows, lane_rows = _continuations_with_highways([
        (551716643, "motorway_link", {**common, "lanes": "1"}, ramp),
        (948690091, "motorway", {**common, "lanes": "2"}, mainline),
        (
            386967467,
            "motorway",
            {**common, "lanes": "3", "turn:lanes": "none|none|merge_to_left"},
            target,
        ),
    ], include_lane_rows=True)

    by_source = {row["source_id"]: row for row in rows}
    assert set(by_source) == {551716643, 948690091}
    assert by_source[948690091]["raw"]["to_lanes"] == [1, 2]
    assert by_source[551716643]["raw"]["to_lanes"] == [3]
    assert all(row["raw"]["to_osm_id"] == 386967467 for row in rows)

    ramp_centre = transform(_WGS84_TO_RD.transform, _surface_centreline(by_source[551716643]))
    main_centre = transform(_WGS84_TO_RD.transform, _surface_centreline(by_source[948690091]))
    assert not ramp_centre.crosses(main_centre)
    rd_target = transform(_WGS84_TO_RD.transform, target)
    target_start, target_end = rd_target.coords[0], rd_target.coords[-1]
    target_length = math.dist(target_start, target_end)
    target_unit = (
        (target_end[0] - target_start[0]) / target_length,
        (target_end[1] - target_start[1]) / target_length,
    )
    end_delta = (
        ramp_centre.coords[-1][0] - main_centre.coords[-1][0],
        ramp_centre.coords[-1][1] - main_centre.coords[-1][1],
    )
    assert abs(end_delta[0] * target_unit[0] + end_delta[1] * target_unit[1]) < 0.1

    ramp_surface = transform(_WGS84_TO_RD.transform, from_wkt(by_source[551716643]["geom"]))
    main_surface = transform(_WGS84_TO_RD.transform, from_wkt(by_source[948690091]["geom"]))
    assert not ramp_surface.boundary.crosses(main_surface.boundary)
    assert ramp_surface.intersection(main_surface).area <= _allocated_overlap_bound(
        by_source[551716643], by_source[948690091]
    )

    # The normal lane bands stop at the connector's new source station. Their
    # adjacent outside strokes must be cut after their geometric intersection,
    # otherwise MapLibre still draws an X even though the connector polygons
    # themselves share a clean seam.
    ramp_line = transform(
        _WGS84_TO_RD.transform, from_wkt(lane_rows["551716643:fwd:1"]["geom"])
    )
    main_right_line = transform(
        _WGS84_TO_RD.transform, from_wkt(lane_rows["948690091:fwd:2"]["geom"])
    )
    assert not ramp_line.offset_curve(1.75).crosses(
        main_right_line.offset_curve(-1.75)
    )


def test_non_conserved_merge_shares_only_the_outer_target_lane():
    node = (4.622332, 52.231048)
    ramp = LineString([(4.627301, 52.231691), node])
    mainline = LineString([(4.624278, 52.231206), node])
    target = LineString([node, (4.618934, 52.230765)])
    common = {"ref": "A44", "oneway": "yes"}
    rows = _continuations_with_highways([
        (10, "motorway_link", {**common, "lanes": "1"}, ramp),
        (20, "motorway", {**common, "lanes": "3"}, mainline),
        (30, "motorway", {**common, "lanes": "3"}, target),
    ])

    by_source = {row["source_id"]: row for row in rows}
    assert by_source[20]["raw"]["to_lanes"] == [1, 2, 3]
    assert by_source[10]["raw"]["to_lanes"] == [3]
    assert sum(len(row["raw"]["to_lanes"]) == 3 for row in rows) == 1

    ramp_surface = transform(_WGS84_TO_RD.transform, from_wkt(by_source[10]["geom"]))
    main_surface = transform(_WGS84_TO_RD.transform, from_wkt(by_source[20]["geom"]))
    ramp_centre = transform(_WGS84_TO_RD.transform, _surface_centreline(by_source[10]))
    main_centre = transform(_WGS84_TO_RD.transform, _surface_centreline(by_source[20]))
    assert not ramp_centre.crosses(main_centre)
    assert ramp_surface.intersection(main_surface).area <= _allocated_overlap_bound(
        by_source[10], by_source[20]
    )


def test_tagged_a44_diverge_splits_source_lane_blocks_across_both_exits():
    node = (4.621856, 52.230899)
    approach = LineString([(4.618287, 52.230591), node])
    mainline = LineString([node, (4.627391, 52.231374)])
    exit_link = LineString([node, (4.625239, 52.230628)])
    common = {"ref": "A44", "oneway": "yes"}
    all_rows = _continuations_with_highways([
        (
            386967473,
            "motorway",
            {**common, "lanes": "3", "turn:lanes": "none|none|slight_right"},
            approach,
        ),
        (127572892, "motorway", {**common, "lanes": "2"}, mainline),
        (7399108, "motorway_link", {**common, "lanes": "1"}, exit_link),
    ], include_markings=True)
    rows = [row for row in all_rows if row["role"] == "connector"]

    by_target = {row["raw"]["to_osm_id"]: row for row in rows}
    assert set(by_target) == {127572892, 7399108}
    assert by_target[127572892]["raw"]["from_lanes"] == [1, 2]
    assert by_target[127572892]["raw"]["to_lanes"] == [1, 2]
    assert by_target[7399108]["raw"]["from_lanes"] == [3]
    assert by_target[7399108]["raw"]["to_lanes"] == [1]
    edge_rows = [
        row
        for row in all_rows
        if row["role"] == "connector_marking"
        and row["raw"]["continuation_marking"] == "edge"
    ]
    edge_lines = [
        transform(_WGS84_TO_RD.transform, from_wkt(row["geom"]))
        for row in edge_rows
    ]
    assert not any(
        edge_lines[left].crosses(edge_lines[right])
        for left in range(len(edge_lines))
        for right in range(left + 1, len(edge_lines))
    )
    by_edge = {
        (row["raw"]["to_osm_id"], row["id"].rsplit(":", 1)[-1]): line
        for row, line in zip(edge_rows, edge_lines)
    }
    assert math.dist(
        by_edge[(127572892, "right")].coords[0],
        by_edge[(7399108, "left")].coords[0],
    ) < 1e-6


def test_exact_continuation_owns_covered_legacy_lane_movements():
    turn_rows = [
        {
            "id": "10:conn:1:20:1",
            "source_id": 10,
            "lane": 1,
            "role": "connector",
            "raw": {"to_osm_id": 20, "to_lane": 1},
        },
        {
            "id": "10:conn:2:30:1",
            "source_id": 10,
            "lane": 2,
            "role": "connector",
            "raw": {"to_osm_id": 30, "to_lane": 1},
        },
    ]
    continuation_rows = [
        {
            "id": "10:join:fwd:20:fwd",
            "source_id": 10,
            "lane": 1,
            "role": "connector",
            "raw": {
                "continuation": True,
                "from_lanes": [1],
                "to_osm_id": 20,
                "to_lanes": [1],
            },
        },
    ]

    combined = combine_connector_rows(turn_rows, continuation_rows)
    assert {row["id"] for row in combined} == {
        "10:conn:2:30:1",
        "10:join:fwd:20:fwd",
    }


def test_tagged_a44_narrowing_maps_the_merging_lane_to_its_neighbour():
    node = (4.618934, 52.230765)
    approach = LineString([(4.622332, 52.231048), node])
    target = LineString([node, (4.584062, 52.228187)])
    common = {"ref": "A44", "oneway": "yes"}
    all_rows, lane_rows = _continuations_with_highways([
        (
            386967467,
            "motorway",
            {**common, "lanes": "3", "turn:lanes": "none|none|merge_to_left"},
            approach,
        ),
        (127572925, "motorway", {**common, "lanes": "2"}, target),
    ], include_lane_rows=True, include_markings=True, merge_context=True)
    rows = [row for row in all_rows if row["role"] == "connector"]

    assert len(rows) == 1
    assert rows[0]["raw"]["from_lanes"] == [1, 2, 3]
    assert rows[0]["raw"]["to_lanes"] == [1, 2]
    assert rows[0]["raw"]["lane_map"] == [
        {"from": 1, "to": 1},
        {"from": 2, "to": 2},
        {"from": 3, "to": 2},
    ]
    assert sum(
        row["role"] == "connector_marking"
        and row["raw"]["continuation_marking"] == "divider"
        for row in all_rows
    ) == 1
    lane_2_end = transform(
        _WGS84_TO_RD.transform,
        from_wkt(lane_rows["386967467:fwd:2"]["geom"]),
    ).coords[-1]
    lane_3_end = transform(
        _WGS84_TO_RD.transform,
        from_wkt(lane_rows["386967467:fwd:3"]["geom"]),
    ).coords[-1]
    assert math.dist(lane_2_end, lane_3_end) < 0.2
    assert all(
        not lane_rows[f"{source}:fwd:{lane}"]["raw"].get("continuation_trim")
        for source, lane_count in ((386967467, 3), (127572925, 2))
        for lane in range(1, lane_count + 1)
    )


def test_tagged_a44_widening_adds_the_slight_right_target_lane_at_the_edge():
    node = (4.618287, 52.230591)
    approach = LineString([(4.584062, 52.228187), node])
    target = LineString([node, (4.622332, 52.231048)])
    common = {"ref": "A44", "oneway": "yes"}
    all_rows = _continuations_with_highways([
        (127572847, "motorway", {**common, "lanes": "2"}, approach),
        (
            386967473,
            "motorway",
            {**common, "lanes": "3", "turn:lanes": "none|none|slight_right"},
            target,
        ),
    ], include_markings=True)
    rows = [row for row in all_rows if row["role"] == "connector"]

    assert len(rows) == 1
    assert rows[0]["raw"]["from_lanes"] == [1, 2]
    assert rows[0]["raw"]["to_lanes"] == [1, 2, 3]
    assert rows[0]["raw"]["lane_map"] == [
        {"from": 1, "to": 1},
        {"from": 2, "to": 2},
    ]
    assert sum(
        row["role"] == "connector_marking"
        and row["raw"]["continuation_marking"] == "divider"
        for row in all_rows
    ) == 2


def test_second_a44_cluster_allocates_mainline_and_ramp_without_crossing():
    # Exact shared-node topology around 52.231815, 4.632914. This is the same
    # generic 2+1→3 shape as the first reported merge, but with longer curved
    # source ways and the ramp approaching from the opposite screen direction.
    node = (4.6329136, 52.231815)
    mainline = LineString([
        (4.6279692, 52.2314232),
        (4.6307529, 52.2316525),
        node,
    ])
    ramp = LineString([
        (4.6292633, 52.2312691),
        (4.6302939, 52.2314897),
        (4.6320846, 52.2316698),
        node,
    ])
    target = LineString([node, (4.6373067, 52.232177)])
    common = {"ref": "A44", "oneway": "yes"}
    rows = _continuations_with_highways([
        (127572916, "motorway", {**common, "lanes": "2"}, mainline),
        (7399114, "motorway_link", {**common, "lanes": "1"}, ramp),
        (
            386967459,
            "motorway",
            {**common, "lanes": "3", "turn:lanes": "none|none|merge_to_left"},
            target,
        ),
    ])

    by_source = {row["source_id"]: row for row in rows}
    assert set(by_source) == {127572916, 7399114}
    assert by_source[127572916]["raw"]["to_lanes"] == [1, 2]
    assert by_source[7399114]["raw"]["to_lanes"] == [3]
    main_centre = _surface_centreline(by_source[127572916])
    ramp_centre = _surface_centreline(by_source[7399114])
    assert not main_centre.crosses(ramp_centre)


def test_second_a44_cluster_splits_slight_right_lane_to_exit():
    node = (4.6326406, 52.2319142)
    approach = LineString([
        (4.6420488, 52.2329186),
        (4.6375032, 52.2323172),
        node,
    ])
    mainline = LineString([node, (4.6279541, 52.2314922)])
    exit_link = LineString([
        node,
        (4.6315143, 52.2318992),
        (4.6280765, 52.2317329),
    ])
    common = {"ref": "A44", "oneway": "yes"}
    rows = _continuations_with_highways([
        (
            127572943,
            "motorway",
            {**common, "lanes": "3", "turn:lanes": "none|none|slight_right"},
            approach,
        ),
        (386967462, "motorway", {**common, "lanes": "2"}, mainline),
        (7399104, "motorway_link", {**common, "lanes": "1"}, exit_link),
    ])

    by_target = {row["raw"]["to_osm_id"]: row for row in rows}
    assert set(by_target) == {386967462, 7399104}
    assert by_target[386967462]["raw"]["from_lanes"] == [1, 2]
    assert by_target[386967462]["raw"]["to_lanes"] == [1, 2]
    assert by_target[7399104]["raw"]["from_lanes"] == [3]
    assert by_target[7399104]["raw"]["to_lanes"] == [1]


def test_separate_oneways_join_both_directions_of_a_two_way_road():
    # The second screenshot's topology: two one-way carriageways meet one
    # shared two-way centreline. Each offset directional half needs its own
    # short bridge to/from the common OSM node.
    east_to_node = _line((60, 0), (0, 0))
    node_to_east = _line((0, 0), (60, 0))
    node_to_west = _line((0, 0), (-60, 0))
    common = {"name": "Provincialeweg", "ref": "N203"}
    rows = _continuations([
        (1, {**common, "lanes": "1", "oneway": "yes"}, east_to_node),
        (2, {**common, "lanes": "1", "oneway": "yes"}, node_to_east),
        (3, {**common, "lanes": "2"}, node_to_west),
    ])
    joins = {(r["source_id"], r["direction"], r["raw"]["to_osm_id"]) for r in rows}
    assert joins == {(1, "fwd", 3), (3, "bwd", 2)}
    # The two-way target is represented by separate directional records, so
    # neither one-way is treated as contesting the other's one-lane section.
    assert all(r["raw"]["to_lanes"] == [1] for r in rows)
    assert all(r["width_m"] == 3.5 for r in rows)


def test_touching_continuation_emits_a_lane_width_surface_not_a_line_cap():
    rows = _continuations([
        (1, {"lanes": "1", "oneway": "yes", "ref": "N203"}, APPROACH_LINE),
        (2, {"lanes": "1", "oneway": "yes", "ref": "N203"}, THROUGH_TOUCHING_LINE),
    ])
    assert len(rows) == 1
    surface = from_wkt(rows[0]["geom"])
    assert surface.geom_type == "Polygon"
    assert abs(GEOD.geometry_area_perimeter(surface)[0]) > 0.2


def test_short_bent_continuation_stays_one_polygon():
    rows = _continuations([
        (1, {"lanes": "1", "oneway": "yes", "ref": "N203"}, _line((60, 0), (0, 0))),
        (2, {"lanes": "1", "oneway": "yes", "ref": "N203"}, _line((0, 0), (-60, -30))),
    ])
    assert len(rows) == 1
    surface = from_wkt(rows[0]["geom"])
    assert surface.geom_type == "Polygon"
    assert surface.is_valid


def test_confirmed_continuation_trims_flat_lane_caps_under_the_surface():
    ways = [
        (1, {"lanes": "1", "oneway": "yes", "ref": "N203"}, APPROACH_LINE),
        (2, {"lanes": "2", "ref": "N203"}, THROUGH_TOUCHING_LINE),
    ]
    records = {}
    lane_rows = {}
    original_lengths = {}
    for osm_id, tags, line in ways:
        rows = make_lane_rows(osm_id, "primary", tags, line)
        for row in rows:
            lane_rows[row["id"]] = row
            original_lengths[row["id"]] = GEOD.geometry_length(from_wkt(row["geom"]))
        for record in continuation_records(osm_id, tags, line, rows):
            records[record["key"]] = record

    surfaces = [
        row for row in make_continuation_rows(records, lane_rows)
        if row["role"] == "connector"
    ]

    assert len(surfaces) == 1
    trimmed_rows = [
        (row_id, row)
        for row_id, row in lane_rows.items()
        if row["raw"].get("continuation_trim")
    ]
    assert len(trimmed_rows) == 2
    for row_id, row in trimmed_rows:
        assert row["raw"]["continuation_trim"] is True
        trimmed_by = original_lengths[row_id] - GEOD.geometry_length(from_wkt(row["geom"]))
        # The one-way/two-way offset and 1 -> 2 width change need a real taper,
        # not the old 75cm patch that rendered as an abrupt rectangular step.
        assert 4.0 < trimmed_by < 4.3


def test_two_way_approach_takes_no_part():
    rows = make_lane_rows(9, "secondary", {"lanes": "2"}, APPROACH_LINE)
    assert junction_record(9, {"lanes": "2"}, rows) is None


def test_oneway_minus_one_approach_ends_where_traffic_leaves():
    # oneway=-1 traffic runs against the way's coordinate order, and the whole
    # pass keys off "lane_ends is where traffic leaves". If lane geometry ever
    # stops coming back in travel order, connectors silently grow from the
    # wrong end of the way -- so pin it here rather than in osm_lanes alone.
    # Way drawn north->south, travelled south->north: it leaves at the north end.
    tags = {"lanes": "1", "oneway": "-1", "turn:lanes": "through"}
    line = _line((0, 60), (0, 0))
    rec = _record(1, tags, line)
    assert rec is not None
    north, south = _point(0, 60), _point(0, 0)

    def _near(rd_pt, lonlat):
        lon, lat = _RD_TO_WGS84.transform(*rd_pt)
        return GEOD.inv(lon, lat, lonlat[0], lonlat[1])[2] < 1.0

    assert _near(rec["lane_ends"][1], north), "traffic leaves at the north end"
    assert _near(rec["lane_starts"][1], south), "traffic enters at the south end"
    # Heading north out of the junction, not south.
    assert _az_diff(rec["arrive_bearing"], 0.0) < 5


def test_more_turning_lanes_than_the_exit_has_land_on_its_last_lane():
    rows = _connectors(
        {"lanes": "3", "oneway": "yes", "turn:lanes": "left|left|left"},
        [(2, {"lanes": "2", "oneway": "yes"}, LEFT_LINE)],
    )
    to_lanes = {r["lane"]: r["raw"]["to_lane"] for r in rows}
    assert to_lanes == {1: 1, 2: 2, 3: 2}
