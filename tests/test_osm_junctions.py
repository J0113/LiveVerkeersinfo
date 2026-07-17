"""Unit tests for junction lane connectors derived from turn:lanes.

Coordinates are real Noord-Holland ones (the Provincialeweg junction that
motivated the feature, way 1267507394 `left|left|through|right`), so a
WGS84-vs-metres slip fails here rather than passing a planar sanity check.
"""

from __future__ import annotations

from pyproj import Geod
from shapely import from_wkt
from shapely.geometry import LineString

from ndwinfo.parsers.osm_junctions import (
    _RD_TO_WGS84,
    continuation_records,
    junction_record,
    make_connector_rows,
    make_continuation_rows,
)
from ndwinfo.parsers.osm_lanes import make_lane_rows

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
    for osm_id, tags, line in ways:
        rows = make_lane_rows(osm_id, "primary", tags, line)
        for record in continuation_records(osm_id, tags, line, rows):
            records[record["key"]] = record
    return make_continuation_rows(records)


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

    surfaces = make_continuation_rows(records, lane_rows)

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
