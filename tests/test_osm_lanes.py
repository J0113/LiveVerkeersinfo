"""Unit tests for OSM per-lane geometry derivation.

Uses real Noord-Holland coordinates (way 243556317, an A9 motorway
segment) rather than arbitrary planar ones -- a WGS84-vs-metres offsetting
bug would pass a planar sanity check but fail a real geodesic one.
"""

from __future__ import annotations

from shapely import from_wkt
from shapely.geometry import LineString

from ndwinfo.parsers.osm_lanes import make_lane_rows

try:
    from pyproj import Geod
    GEOD = Geod(ellps="WGS84")
except ImportError:  # pragma: no cover
    GEOD = None

REAL_LINE = LineString([
    (5.0301601, 52.331827), (5.0293729, 52.3319888), (5.0287838, 52.3320786),
    (5.0283772, 52.3321299), (5.028013, 52.3321693), (5.0276097, 52.3321947),
    (5.0272658, 52.3322073),
])


def _lane_length_m(row: dict) -> float:
    return GEOD.geometry_length(from_wkt(row["geom"]))


def _spacing_m(row_a: dict, row_b: dict) -> float:
    pa = from_wkt(row_a["geom"]).interpolate(0.5, normalized=True)
    pb = from_wkt(row_b["geom"]).interpolate(0.5, normalized=True)
    _, _, dist = GEOD.inv(pa.x, pa.y, pb.x, pb.y)
    return dist


def _side_delta_deg(row: dict) -> float:
    """Signed angle (deg) of a lane's offset from the centreline's forward
    bearing at its midpoint: ~+90 = right of travel direction, ~-90 = left."""
    cmid = REAL_LINE.interpolate(0.5, normalized=True)
    p0 = REAL_LINE.interpolate(0.45, normalized=True)
    p1 = REAL_LINE.interpolate(0.55, normalized=True)
    fwd_az, _, _ = GEOD.inv(p0.x, p0.y, p1.x, p1.y)
    lmid = from_wkt(row["geom"]).interpolate(0.5, normalized=True)
    az_to_lane, _, _ = GEOD.inv(cmid.x, cmid.y, lmid.x, lmid.y)
    return (az_to_lane - fwd_az + 180) % 360 - 180


def test_oneway_motorway_lanes_are_35m_apart():
    tags = {"lanes": "3", "oneway": "yes"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    assert len(rows) == 3
    assert all(row["width_m"] == 3.5 for row in rows)
    by_lane = {row["lane"]: row for row in rows}
    assert abs(_spacing_m(by_lane[1], by_lane[2]) - 3.5) < 0.1
    assert abs(_spacing_m(by_lane[2], by_lane[3]) - 3.5) < 0.1


def test_oneway_secondary_lanes_are_275m_apart():
    tags = {"lanes": "2", "oneway": "yes"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    assert len(rows) == 2
    assert all(row["width_m"] == 2.75 for row in rows)
    assert abs(_spacing_m(rows[0], rows[1]) - 2.75) < 0.1


def test_link_class_inherits_parent_width():
    assert make_lane_rows(1, "motorway_link", {"lanes": "1", "oneway": "yes"}, REAL_LINE)[0]["width_m"] == 3.5
    assert make_lane_rows(1, "secondary_link", {"lanes": "1", "oneway": "yes"}, REAL_LINE)[0]["width_m"] == 2.75


def test_merge_to_right_tapers_only_that_lane():
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "through|through|merge_to_right"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    by_lane = {row["lane"]: row for row in rows}
    assert by_lane[3]["role"] == "merge_right"
    assert by_lane[1]["role"] == "normal"
    full_len = _lane_length_m(by_lane[1])
    tapered_len = _lane_length_m(by_lane[3])
    assert tapered_len < full_len - 20  # visibly shorter, not just rounding


def test_turn_lanes_cardinality_mismatch_ignored():
    # 3 lanes but only 2 turn tokens -- untrustworthy, not misassigned.
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "through|merge_to_right"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    assert all(row["role"] == "unknown" for row in rows)
    # and none of them got tapered off the back of a misapplied token
    lengths = {row["lane"]: _lane_length_m(row) for row in rows}
    assert max(lengths.values()) - min(lengths.values()) < 5


def test_oneway_minus_one_still_leftmost_first_lane():
    tags = {"lanes": "2", "oneway": "-1"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    assert len(rows) == 2
    assert {row["lane"] for row in rows} == {1, 2}


def test_two_way_with_explicit_forward_backward_split():
    tags = {"lanes": "2", "lanes:forward": "1", "lanes:backward": "1"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    assert {row["direction"] for row in rows} == {"fwd", "bwd"}
    fwd = next(r for r in rows if r["direction"] == "fwd")
    bwd = next(r for r in rows if r["direction"] == "bwd")
    # NL drives right: forward lane sits on the right of travel direction (~+90deg),
    # backward on the left (~-90deg).
    assert _side_delta_deg(fwd) > 45
    assert _side_delta_deg(bwd) < -45


def test_two_way_backward_merge_trims_the_start_not_the_end():
    tags = {
        "lanes": "3", "lanes:forward": "2", "lanes:backward": "1",
        "turn:lanes:backward": "merge_to_left",
    }
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    bwd = next(r for r in rows if r["direction"] == "bwd")
    assert bwd["role"] == "merge_left"
    bwd_geom = from_wkt(bwd["geom"])
    # trimmed line's start point should no longer coincide with the source
    # line's start -- the cut happened at the start, not the end.
    src_start = REAL_LINE.coords[0]
    _, _, dist_from_src_start = GEOD.inv(src_start[0], src_start[1], bwd_geom.coords[0][0], bwd_geom.coords[0][1])
    assert dist_from_src_start > 20


def test_two_way_odd_total_without_directional_tags_stays_unknown():
    # Draft-1 bug: an odd lanes= total on a two-way road does NOT imply a
    # center-turn lane unless lanes:both_ways is actually present.
    tags = {"lanes": "3"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    assert len(rows) == 3
    assert all(row["direction"] == "unknown" for row in rows)
    assert all(row["role"] == "unknown" for row in rows)


def test_two_way_lanes_both_ways_gets_both_ways_role():
    tags = {"lanes": "3", "lanes:forward": "1", "lanes:backward": "1", "lanes:both_ways": "1"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    both = [r for r in rows if r["role"] == "both_ways"]
    assert len(both) == 1
    assert both[0]["direction"] == "unknown"


def test_oneway_reversible_and_alternating_are_skipped():
    assert make_lane_rows(1, "motorway", {"lanes": "3", "oneway": "reversible"}, REAL_LINE) == []
    assert make_lane_rows(1, "motorway", {"lanes": "3", "oneway": "alternating"}, REAL_LINE) == []


def test_way_without_lanes_tag_returns_nothing():
    assert make_lane_rows(1, "motorway", {"oneway": "yes"}, REAL_LINE) == []


def test_unsupported_highway_class_returns_nothing():
    assert make_lane_rows(1, "residential", {"lanes": "2", "oneway": "yes"}, REAL_LINE) == []


def test_taper_capped_at_half_of_a_short_way():
    short_line = LineString([(5.0301601, 52.331827), (5.0299, 52.331827 + 0.0002)])  # ~30m
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "through|merge_to_right"}
    rows = make_lane_rows(1, "motorway", tags, short_line)
    by_lane = {row["lane"]: row for row in rows}
    full_len = _lane_length_m(by_lane[1])
    tapered_len = _lane_length_m(by_lane[2])
    assert 0 < tapered_len < full_len
    assert tapered_len > full_len * 0.4  # capped at ~50%, not trimmed to near-nothing


def test_ids_are_stable_and_direction_scoped():
    tags = {"lanes": "2", "lanes:forward": "1", "lanes:backward": "1"}
    rows = make_lane_rows(555, "secondary", tags, REAL_LINE)
    ids = {row["id"] for row in rows}
    assert ids == {"555:fwd:1", "555:bwd:1"}
