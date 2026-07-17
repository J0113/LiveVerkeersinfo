"""Unit tests for OSM per-lane geometry derivation.

Uses real Noord-Holland coordinates (way 243556317, an A9 motorway
segment) rather than arbitrary planar ones -- a WGS84-vs-metres offsetting
bug would pass a planar sanity check but fail a real geodesic one.
"""

from __future__ import annotations

from shapely import from_wkt
from shapely.geometry import LineString

from ndwinfo.parsers.osm_lanes import build_merge_index, make_all_lane_rows, make_lane_rows

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
    motorway = make_lane_rows(
        1, "motorway_link", {"lanes": "1", "oneway": "yes"}, REAL_LINE
    )
    secondary = make_lane_rows(
        1, "secondary_link", {"lanes": "1", "oneway": "yes"}, REAL_LINE
    )
    assert motorway[0]["width_m"] == 3.5
    assert secondary[0]["width_m"] == 2.75


def _end_gap_m(row_a: dict, row_b: dict) -> float:
    """Distance between two lane rows' last coordinates."""
    ea = from_wkt(row_a["geom"]).coords[-1]
    eb = from_wkt(row_b["geom"]).coords[-1]
    _, _, dist = GEOD.inv(ea[0], ea[1], eb[0], eb[1])
    return dist


def _start_gap_m(row_a: dict, row_b: dict) -> float:
    sa = from_wkt(row_a["geom"]).coords[0]
    sb = from_wkt(row_b["geom"]).coords[0]
    _, _, dist = GEOD.inv(sa[0], sa[1], sb[0], sb[1])
    return dist


def test_merge_to_right_lane_converges_onto_its_right_neighbour():
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "merge_to_right|through"}
    ways = [(1, "motorway", tags, REAL_LINE)]
    rows = make_all_lane_rows(ways)
    by_lane = {row["lane"]: row for row in rows}
    assert by_lane[1]["role"] == "merge_right"
    assert by_lane[2]["role"] == "normal"
    # Still full length -- a merging lane is drawn, not deleted.
    assert abs(_lane_length_m(by_lane[1]) - _lane_length_m(by_lane[2])) < 15
    # Starts a lane apart, ends on top of lane 2.
    assert abs(_start_gap_m(by_lane[1], by_lane[2]) - 3.5) < 0.2
    assert _end_gap_m(by_lane[1], by_lane[2]) < 0.2


def _dist_from_m(row: dict, at: int, point) -> float:
    c = from_wkt(row["geom"]).coords[at]
    _, _, dist = GEOD.inv(point[0], point[1], c[0], c[1])
    return dist


def test_merge_end_recentres_the_survivor_onto_the_ways_own_line():
    # OSM draws a way down the middle of the carriageway it describes, so the
    # next way (one lane, 273 chains here go 2->1) is centred on the shared
    # node. Holding lane 2 at its 2-lane offset leaves it half a lane short of
    # meeting it.
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "merge_to_right|none"}
    ways = [(1, "motorway", tags, REAL_LINE)]
    rows = make_all_lane_rows(ways)
    for row in rows:
        assert _dist_from_m(row, -1, REAL_LINE.coords[-1]) < 0.2, row["id"]
    # ...while still starting a full lane apart, straddling the line.
    by_lane = {r["lane"]: r for r in rows}
    assert abs(_start_gap_m(by_lane[1], by_lane[2]) - 3.5) < 0.2
    assert abs(_dist_from_m(by_lane[1], 0, REAL_LINE.coords[0]) - 1.75) < 0.1
    assert abs(_dist_from_m(by_lane[2], 0, REAL_LINE.coords[0]) - 1.75) < 0.1


def test_three_lane_merge_recentres_both_survivors():
    # 3 -> 2: the survivors end at +/-1.75, not at their 3-lane +3.5/0.
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "merge_to_right|none|none"}
    ways = [(1, "motorway", tags, REAL_LINE)]
    rows = make_all_lane_rows(ways)
    by_lane = {r["lane"]: r for r in rows}
    assert by_lane[1]["role"] == "merge_right"
    assert abs(_dist_from_m(by_lane[2], -1, REAL_LINE.coords[-1]) - 1.75) < 0.15
    assert abs(_dist_from_m(by_lane[3], -1, REAL_LINE.coords[-1]) - 1.75) < 0.15
    # The merging lane lands on its target's final position, not its start one.
    assert _end_gap_m(by_lane[1], by_lane[2]) < 0.2
    # Cross-section starts as a normal 3-lane one, centred on the way's line.
    assert abs(_start_gap_m(by_lane[1], by_lane[2]) - 3.5) < 0.1
    assert abs(_start_gap_m(by_lane[2], by_lane[3]) - 3.5) < 0.1
    assert _dist_from_m(by_lane[2], 0, REAL_LINE.coords[0]) < 0.1


def test_no_merge_means_no_recentring():
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "left|through|right"}
    rows = make_all_lane_rows([(1, "motorway", tags, REAL_LINE)])
    by_lane = {r["lane"]: r for r in rows}
    for lane in (1, 2, 3):
        assert abs(_dist_from_m(by_lane[lane], 0, REAL_LINE.coords[0])
                   - _dist_from_m(by_lane[lane], -1, REAL_LINE.coords[-1])) < 0.2


def test_merge_on_an_edge_lane_with_no_neighbour_is_left_alone():
    # Nothing to the right of the rightmost lane to converge onto.
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "through|merge_to_right"}
    ways = [(1, "motorway", tags, REAL_LINE)]
    rows = make_all_lane_rows(ways)
    by_lane = {row["lane"]: row for row in rows}
    assert by_lane[2]["role"] == "merge_right"
    assert abs(_spacing_m(by_lane[1], by_lane[2]) - 3.5) < 0.1
    assert _end_gap_m(by_lane[1], by_lane[2]) > 3.0


def test_merge_without_index_stays_a_plain_full_length_offset():
    # No chain context -> how far the merge still has to run is unknowable.
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "merge_to_right|through"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    by_lane = {row["lane"]: row for row in rows}
    assert by_lane[1]["role"] == "merge_right"
    assert abs(_end_gap_m(by_lane[1], by_lane[2]) - 3.5) < 0.2


def test_turn_lanes_cardinality_mismatch_ignored():
    # 3 lanes but only 2 turn tokens -- untrustworthy, not misassigned.
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "through|merge_to_right"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    assert all(row["role"] == "unknown" for row in rows)
    # and none of them got tapered off the back of a misapplied token
    lengths = {row["lane"]: _lane_length_m(row) for row in rows}
    assert max(lengths.values()) - min(lengths.values()) < 5


def test_each_lane_carries_its_own_turn_tokens():
    # `turn` is what the map draws its arrows from, so a lane must get its own
    # token set -- left to right, one per lane.
    tags = {"lanes": "4", "oneway": "yes", "turn:lanes": "left|left|through|right"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    turns = {row["lane"]: row["raw"].get("turn") for row in rows}
    assert turns == {1: "left", 2: "left", 3: "through", 4: "right"}


def test_multi_token_lane_turn_is_canonical_and_sorted():
    # Order within a lane's token set isn't meaningful, but the value keys an
    # icon -- so `through;left` and `left;through` must be one arrow, not two.
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "through;left|right"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    by_lane = {row["lane"]: row for row in rows}
    assert by_lane[1]["raw"]["turn"] == "left;through"


def test_lane_with_no_turn_indication_has_no_turn():
    # `turn:lanes=|through` says nothing about lane 1; absent, not a movement.
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "|through"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    by_lane = {row["lane"]: row for row in rows}
    assert "turn" not in by_lane[1]["raw"]
    assert by_lane[2]["raw"]["turn"] == "through"


def test_untrustworthy_turn_lanes_produce_no_turn():
    # Same reasoning as the role guard: a token count that doesn't match the
    # lanes can't be attributed, so no lane gets an arrow from it.
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "left|through"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    assert all("turn" not in row["raw"] for row in rows)


def test_backward_turn_tokens_follow_the_backward_driver():
    # turn:lanes:backward is ordered left-to-right from the backward driver's
    # seat, which is the reverse of our physical outermost-first ordering. The
    # arrow is drawn for that driver, so lane numbering must not scramble it.
    tags = {
        "lanes": "3", "lanes:forward": "1", "lanes:backward": "2",
        "turn:lanes:backward": "left|through",
    }
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    bwd = sorted((r for r in rows if r["direction"] == "bwd"), key=lambda r: r["lane"])
    # lane 1 is physically leftmost = the backward driver's *rightmost* = through.
    assert [r["raw"].get("turn") for r in bwd] == ["through", "left"]


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


def test_two_way_backward_merge_converges_at_the_ways_start():
    # Backward traffic travels toward the way's first coordinate, so that end is
    # where its merge completes. Tokens are ordered from the backward driver's
    # own left, i.e. from the lane nearest the centreline outward.
    tags = {
        "lanes": "4", "lanes:forward": "2", "lanes:backward": "2",
        "turn:lanes:backward": "through|merge_to_left",
    }
    ways = [(1, "secondary", tags, REAL_LINE)]
    rows = make_all_lane_rows(ways)
    bwd = sorted((r for r in rows if r["direction"] == "bwd"), key=lambda r: r["lane"])
    assert [r["role"] for r in bwd] == ["merge_left", "normal"]
    merging, target = bwd[0], bwd[1]
    # The outer backward lane merges toward the centreline, at the way's START.
    assert _start_gap_m(merging, target) < 0.2
    assert abs(_end_gap_m(merging, target) - 2.75) < 0.2


def test_two_way_odd_total_without_directional_tags_stays_unknown():
    # Draft-1 bug: an odd lanes= total on a two-way road does NOT imply a
    # center-turn lane unless lanes:both_ways is actually present. Which
    # direction would get the extra lane isn't derivable either.
    tags = {"lanes": "3"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    assert len(rows) == 3
    assert all(row["direction"] == "unknown" for row in rows)
    assert all(row["role"] == "unknown" for row in rows)


def test_leftmost_lane_has_no_divider():
    # Its left edge is the outside of the carriageway -- a divider there would
    # double up on the explicit outside-edge stroke.
    rows = make_lane_rows(1, "motorway", {"lanes": "3", "oneway": "yes"}, REAL_LINE)
    by_lane = {r["lane"]: r for r in rows}
    assert by_lane[1]["raw"]["divider_left"] is False
    assert by_lane[2]["raw"]["divider_left"] is True
    assert by_lane[3]["raw"]["divider_left"] is True
    assert by_lane[1]["raw"]["edge_left"] is True
    assert by_lane[1]["raw"]["edge_right"] is False
    assert by_lane[2]["raw"]["edge_left"] is False
    assert by_lane[2]["raw"]["edge_right"] is False
    assert by_lane[3]["raw"]["edge_left"] is False
    assert by_lane[3]["raw"]["edge_right"] is True


def test_two_way_centreline_gets_a_divider():
    # Each direction block numbers from 1, so the forward block's lane 1 is not
    # an outside edge -- it's the centreline, and it must still be drawn.
    tags = {"lanes": "4", "lanes:forward": "2", "lanes:backward": "2"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    fwd = {r["lane"]: r for r in rows if r["direction"] == "fwd"}
    bwd = {r["lane"]: r for r in rows if r["direction"] == "bwd"}
    assert bwd[1]["raw"]["divider_left"] is False  # outside of the road
    assert fwd[1]["raw"]["divider_left"] is True   # centreline
    assert len([r for r in rows if r["raw"]["divider_left"]]) == 3  # 4 lanes, 3 boundaries
    assert len([r for r in rows if r["raw"]["edge_left"]]) == 1
    assert len([r for r in rows if r["raw"]["edge_right"]]) == 1


def test_no_divider_on_a_boundary_a_lane_merges_across():
    # The merging lane's edge sweeps sideways as it converges, so a line on it
    # would drag a diagonal across the asphalt. merge_to_right crosses its
    # right-hand neighbour's left edge, not its own -- the asymmetry that a
    # per-feature style filter can't express.
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "through|merge_to_right|through"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    by_lane = {r["lane"]: r for r in rows}
    assert by_lane[2]["raw"]["divider_left"] is True    # 1|2 boundary is untouched
    assert by_lane[3]["raw"]["divider_left"] is False   # 2|3 is the one being merged across


def test_no_divider_on_a_merge_to_left_boundary():
    tags = {"lanes": "3", "oneway": "yes", "turn:lanes": "through|merge_to_left|through"}
    rows = make_lane_rows(1, "motorway", tags, REAL_LINE)
    by_lane = {r["lane"]: r for r in rows}
    assert by_lane[2]["raw"]["divider_left"] is False   # 1|2 is merged across
    assert by_lane[3]["raw"]["divider_left"] is True


def test_two_way_single_shared_lane_stays_unknown():
    # lanes=1 on a two-way road is one lane shared both ways, not a direction
    # we failed to work out -- 1,304 ways in this extract.
    rows = make_lane_rows(1, "secondary", {"lanes": "1"}, REAL_LINE)
    assert len(rows) == 1
    assert rows[0]["direction"] == "unknown"


def test_two_way_even_total_splits_down_the_middle():
    # No lanes:forward/backward, but NL drives on the right: the left half of
    # an even cross-section is oncoming. Covers the common `lanes=2` two-way way.
    rows = make_lane_rows(1, "secondary", {"lanes": "2"}, REAL_LINE)
    by_dir = {r["direction"]: r for r in rows}
    assert set(by_dir) == {"fwd", "bwd"}
    assert all(r["role"] == "normal" for r in rows)
    # Forward sits on the right of the way's direction (~+90deg), oncoming left.
    assert _side_delta_deg(by_dir["fwd"]) > 45
    assert _side_delta_deg(by_dir["bwd"]) < -45


def test_two_way_four_lanes_splits_two_and_two():
    rows = make_lane_rows(1, "secondary", {"lanes": "4"}, REAL_LINE)
    counts = {}
    for r in rows:
        counts[r["direction"]] = counts.get(r["direction"], 0) + 1
    assert counts == {"bwd": 2, "fwd": 2}
    # Each direction numbers its own block from its physically leftmost lane.
    assert {r["lane"] for r in rows if r["direction"] == "fwd"} == {1, 2}
    assert {r["lane"] for r in rows if r["direction"] == "bwd"} == {1, 2}


def test_untagged_two_way_matches_the_even_split_rule():
    # An untagged road and an explicit lanes=2 are the same cross-section, so
    # they must not disagree about which side is oncoming.
    assumed = make_lane_rows(1, "secondary", {}, REAL_LINE)
    counted = make_lane_rows(2, "secondary", {"lanes": "2"}, REAL_LINE)
    def side(rows, direction):
        row = next(r for r in rows if r["direction"] == direction)
        return round(_side_delta_deg(row))
    assert side(assumed, "fwd") == side(counted, "fwd")
    assert side(assumed, "bwd") == side(counted, "bwd")


def test_two_way_lanes_both_ways_gets_both_ways_role():
    tags = {"lanes": "3", "lanes:forward": "1", "lanes:backward": "1", "lanes:both_ways": "1"}
    rows = make_lane_rows(1, "secondary", tags, REAL_LINE)
    both = [r for r in rows if r["role"] == "both_ways"]
    assert len(both) == 1
    assert both[0]["direction"] == "unknown"


def test_oneway_reversible_and_alternating_are_skipped():
    assert make_lane_rows(1, "motorway", {"lanes": "3", "oneway": "reversible"}, REAL_LINE) == []
    assert make_lane_rows(1, "motorway", {"lanes": "3", "oneway": "alternating"}, REAL_LINE) == []


def test_untagged_oneway_is_one_lane():
    rows = make_lane_rows(1, "motorway", {"oneway": "yes"}, REAL_LINE)
    assert len(rows) == 1
    assert rows[0]["lane"] == 1 and rows[0]["lane_count"] == 1
    assert rows[0]["direction"] == "fwd"
    assert rows[0]["raw"]["lanes_assumed"] is True


def test_untagged_two_way_is_one_lane_each_way():
    rows = make_lane_rows(1, "secondary", {}, REAL_LINE)
    assert len(rows) == 2
    assert {r["direction"] for r in rows} == {"fwd", "bwd"}
    assert all(r["raw"]["lanes_assumed"] is True for r in rows)
    # NL drives right: same sides as an explicitly split two-way road.
    fwd = next(r for r in rows if r["direction"] == "fwd")
    bwd = next(r for r in rows if r["direction"] == "bwd")
    assert _side_delta_deg(fwd) > 45
    assert _side_delta_deg(bwd) < -45
    assert abs(_spacing_m(fwd, bwd) - 2.75) < 0.1


def test_tagged_lane_count_is_not_marked_assumed():
    rows = make_lane_rows(1, "motorway", {"lanes": "2", "oneway": "yes"}, REAL_LINE)
    assert all("lanes_assumed" not in r["raw"] for r in rows)


def test_untagged_oneway_takes_its_count_from_turn_lanes():
    # turn:lanes carries one token per lane by definition, so it's a count.
    tags = {"oneway": "yes", "turn:lanes": "left|through|right"}
    rows = make_lane_rows(1, "primary", tags, REAL_LINE)
    assert len(rows) == 3
    assert [r["role"] for r in sorted(rows, key=lambda r: r["lane"])] == ["normal"] * 3
    assert all("lanes_assumed" not in r["raw"] for r in rows)


def test_untagged_reversible_way_is_still_skipped():
    # The default lane count doesn't override the direction model's refusals.
    assert make_lane_rows(1, "motorway", {"oneway": "reversible"}, REAL_LINE) == []


def test_unsupported_highway_class_returns_nothing():
    assert make_lane_rows(1, "residential", {"lanes": "2", "oneway": "yes"}, REAL_LINE) == []


def test_short_way_converges_over_its_whole_length():
    short_line = LineString([(5.0301601, 52.331827), (5.0299, 52.331827 + 0.0002)])  # ~30m
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "merge_to_right|through"}
    rows = make_all_lane_rows([(1, "motorway", tags, short_line)])
    by_lane = {row["lane"]: row for row in rows}
    # Way is shorter than MAX_TAPER_M, so the convergence spans all of it:
    # a lane apart at the start, joined at the end.
    assert abs(_start_gap_m(by_lane[1], by_lane[2]) - 3.5) < 0.2
    assert _end_gap_m(by_lane[1], by_lane[2]) < 0.2


def test_merge_chain_converges_once_across_both_ways():
    # Like A200 ways 7400291 -> 1014194650, but short enough that MAX_TAPER_M
    # spans the shared node: OSM splits one physical merge across two
    # consecutive ways, both tagged `merge_to_right|`. Converging per-way would
    # snap the lane back to full offset at the shared node.
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "merge_to_right|"}
    upstream = LineString([(5.0301601, 52.331827), (5.0297211, 52.331827)])   # ~30m
    downstream = LineString([(5.0297211, 52.331827), (5.0295015, 52.331827)])  # ~15m
    rows = make_all_lane_rows([
        (1, "motorway", tags, upstream),
        (2, "motorway", tags, downstream),
    ])
    by_way = {(r["source_id"], r["lane"]): r for r in rows}
    # Still a full lane apart where the chain starts...
    assert abs(_start_gap_m(by_way[(1, 1)], by_way[(1, 2)]) - 3.5) < 0.2
    # ...upstream hands over mid-convergence, downstream picks up from there...
    handover_up = _end_gap_m(by_way[(1, 1)], by_way[(1, 2)])
    handover_down = _start_gap_m(by_way[(2, 1)], by_way[(2, 2)])
    assert abs(handover_up - handover_down) < 0.2
    assert 0.2 < handover_up < 3.4  # partway converged, not reset to a full lane apart
    # ...and it completes only at the end of the chain.
    assert _end_gap_m(by_way[(2, 1)], by_way[(2, 2)]) < 0.2


def test_merge_index_shares_one_taper_length_across_a_chain():
    tags = {"lanes": "2", "oneway": "yes", "turn:lanes": "merge_to_right|"}
    upstream = LineString([(5.0301601, 52.331827), (5.0287838, 52.3320786)])
    downstream = LineString([(5.0287838, 52.3320786), (5.0272658, 52.3322073)])
    index = build_merge_index([
        (1, "motorway", tags, upstream),
        (2, "motorway", tags, downstream),
    ])
    up_dist, up_taper = index[(1, 1)]
    down_dist, down_taper = index[(2, 1)]
    assert down_dist == 0  # last way in the chain: merge point is its own end
    assert up_dist > 50  # ...and the upstream way still has that way to run
    assert up_taper == down_taper  # identical, or the convergence would kink at the join


def test_ids_are_stable_and_direction_scoped():
    tags = {"lanes": "2", "lanes:forward": "1", "lanes:backward": "1"}
    rows = make_lane_rows(555, "secondary", tags, REAL_LINE)
    ids = {row["id"] for row in rows}
    assert ids == {"555:fwd:1", "555:bwd:1"}
