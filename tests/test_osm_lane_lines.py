"""Independent OSM Lanes planner, topology, and geometry tests."""

from __future__ import annotations

import math

import pytest
from pyproj import Geod, Transformer
from shapely.geometry import LineString
from shapely.ops import transform

from ndwinfo.parsers.osm_lane_lines import (
    LaneGeometryError,
    make_lane_line_rows,
    offset_lane_geometry,
    plan_lane_cross_section,
    split_logical_segments,
)

GEOD = Geod(ellps="WGS84")
RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)
REAL_LINE = LineString(
    [
        (5.0301601, 52.331827),
        (5.0293729, 52.3319888),
        (5.0287838, 52.3320786),
        (5.0283772, 52.3321299),
        (5.028013, 52.3321693),
    ]
)


def _spacing(row_a, row_b):
    from shapely import from_wkt

    a = from_wkt(row_a["geom"]).interpolate(0.5, normalized=True)
    b = from_wkt(row_b["geom"]).interpolate(0.5, normalized=True)
    return GEOD.inv(a.x, a.y, b.x, b.y)[2]


@pytest.mark.parametrize("count", [2, 3, 4])
def test_every_lane_count_uses_35m_pitch(count):
    rows, failures = make_lane_line_rows(
        1, "secondary", {"lanes": str(count), "oneway": "yes"}, REAL_LINE
    )
    assert failures == []
    assert len(rows) == count
    by_physical = sorted(rows, key=lambda row: row["physical_lane_index"])
    assert all(abs(_spacing(a, b) - 3.5) < 0.1 for a, b in zip(by_physical, by_physical[1:]))


def test_one_lane_is_the_source_line():
    rows, failures = make_lane_line_rows(
        10, "motorway_link", {"lanes": "1", "oneway": "yes"}, REAL_LINE
    )
    assert failures == []
    assert len(rows) == 1
    assert rows[0]["offset_m"] == 0
    assert rows[0]["id"] == "ll:10:0:0:fwd:1"


def test_access_no_road_does_not_generate_lane_lines():
    rows, failures = make_lane_line_rows(
        865148139,
        "motorway_link",
        {"access": "no", "lanes": "2", "oneway": "yes"},
        REAL_LINE,
    )

    assert rows == []
    assert failures == []


def test_roundabout_implies_oneway_but_circular_does_not():
    roundabout = plan_lane_cross_section({"junction": "roundabout", "lanes": "1"})
    assert [lane.direction for lane in roundabout.lanes] == ["fwd"]
    assert roundabout.oneway_source == "roundabout_implied"

    contradicted = plan_lane_cross_section(
        {"junction": "roundabout", "oneway": "no", "lanes": "1"}
    )
    assert [lane.direction for lane in contradicted.lanes] == ["both"]
    assert contradicted.oneway_source == "tag"

    circular = plan_lane_cross_section({"junction": "circular", "lanes": "1"})
    assert [lane.direction for lane in circular.lanes] == ["both"]


def test_single_track_is_one_physical_both_direction_lane():
    plan = plan_lane_cross_section({"lanes": "1"})
    assert len(plan.lanes) == 1
    assert plan.lanes[0].direction == "both"
    assert plan.lanes[0].offset_m == 0


def test_oneway_minus_one_numbering_is_driver_relative():
    plan = plan_lane_cross_section({"lanes": "3", "oneway": "-1"})
    by_physical = sorted(plan.lanes, key=lambda lane: lane.physical_lane_index)
    assert [lane.lane_nr for lane in by_physical] == [3, 2, 1]
    assert [lane.offset_m for lane in by_physical] == [3.5, 0.0, -3.5]


def test_concrete_placement_aligns_lane_centers_to_reference_line():
    forward = plan_lane_cross_section(
        {"lanes": "4", "oneway": "yes", "placement": "right_of:1"}
    )
    assert [lane.offset_m for lane in forward.lanes] == [1.75, -1.75, -5.25, -8.75]
    assert forward.diagnostics["placement_key"] == "placement"

    backward = plan_lane_cross_section(
        {"lanes": "3", "oneway": "-1", "placement:backward": "right_of:1"}
    )
    by_lane_nr = sorted(backward.lanes, key=lambda lane: lane.lane_nr)
    assert [lane.offset_m for lane in by_lane_nr] == [-1.75, 1.75, 5.25]
    assert backward.diagnostics["placement_key"] == "placement:backward"


def test_transition_and_varying_endpoint_placements_remain_centered():
    transition = plan_lane_cross_section(
        {"lanes": "2", "oneway": "yes", "placement": "transition"}
    )
    varying = plan_lane_cross_section(
        {
            "lanes": "2",
            "oneway": "yes",
            "placement:start": "right_of:1",
            "placement:end": "right_of:2",
        }
    )
    assert [lane.offset_m for lane in transition.lanes] == [1.75, -1.75]
    assert [lane.offset_m for lane in varying.lanes] == [1.75, -1.75]


def test_explicit_two_way_blocks_use_the_correct_sides():
    plan = plan_lane_cross_section(
        {"lanes": "3", "lanes:forward": "2", "lanes:backward": "1"}
    )
    by_physical = sorted(plan.lanes, key=lambda lane: lane.physical_lane_index)
    assert [lane.direction for lane in by_physical] == ["bwd", "fwd", "fwd"]
    assert [lane.lane_nr for lane in by_physical] == [1, 1, 2]


def test_directional_conflict_draws_unknown_physical_total():
    plan = plan_lane_cross_section(
        {"lanes": "3", "lanes:forward": "2", "lanes:backward": "2"}
    )
    assert plan.count_source == "conflict"
    assert len(plan.lanes) == 3
    assert {lane.direction for lane in plan.lanes} == {"unknown"}


def test_odd_untagged_direction_total_is_not_guessed():
    plan = plan_lane_cross_section({"lanes": "3"})
    assert len(plan.lanes) == 3
    assert {lane.direction for lane in plan.lanes} == {"unknown"}


def test_missing_counts_use_oneway_and_twoway_defaults():
    one_way = plan_lane_cross_section({"oneway": "yes"})
    two_way = plan_lane_cross_section({})
    assert len(one_way.lanes) == 1 and one_way.count_source == "assumed"
    assert len(two_way.lanes) == 2 and two_way.count_source == "assumed"
    assert {lane.direction for lane in two_way.lanes} == {"fwd", "bwd"}


def test_over_ceiling_count_is_skipped():
    plan = plan_lane_cross_section({"oneway": "yes", "lanes": "13"})
    assert plan.lanes == ()
    assert plan.diagnostics["over_ceiling"] is True


def test_internal_shared_node_splits_into_stable_segments():
    line = LineString([(4.0, 52.0), (4.1, 52.0), (4.2, 52.0)])
    segments = split_logical_segments(99, line, [10, 11, 12], {11})
    assert [segment.segment_id for segment in segments] == ["99:10:11", "99:11:12"]


def test_lane_raw_retains_original_unoffset_segment_endpoints():
    line = LineString([(4.0, 52.0), (4.1, 52.0)])
    rows, failures = make_lane_line_rows(
        99,
        "motorway",
        {"oneway": "yes", "lanes": "2"},
        line,
        node_refs=[10, 11],
        shared_node_ids={10, 11},
    )

    assert failures == []
    assert {tuple(row["raw"]["source_start"]) for row in rows} == {(4.0, 52.0)}
    assert {tuple(row["raw"]["source_end"]) for row in rows} == {(4.1, 52.0)}


def test_closed_ring_has_an_explicit_wraparound_segment():
    line = LineString(
        [(4.0, 52.0), (4.1, 52.0), (4.1, 52.1), (4.0, 52.1), (4.0, 52.0)]
    )
    segments = split_logical_segments(7, line, [10, 11, 12, 13, 10], {11, 13})
    assert [segment.segment_id for segment in segments] == ["7:11:13", "7:13:11"]
    assert list(segments[1].line.coords)[-1] == line.coords[1]


def test_duplicate_segment_key_fails_loudly():
    line = LineString(
        [(4.0, 52.0), (4.1, 52.0), (4.2, 52.0), (4.1, 52.0), (4.2, 52.1)]
    )
    with pytest.raises(ValueError, match="duplicate logical segment"):
        split_logical_segments(8, line, [10, 11, 12, 11, 12], {11, 12})


def test_tight_inner_offset_is_rejected_as_degenerate():
    angles = [math.pi * index / 20 for index in range(21)]
    arc_rd = LineString(
        [(120000 + 8 * math.cos(angle), 480000 + 8 * math.sin(angle)) for angle in angles]
    )
    arc_wgs = transform(RD_TO_WGS84.transform, arc_rd)
    with pytest.raises(LaneGeometryError, match="offset endpoints moved"):
        offset_lane_geometry(arc_wgs, 5.25)
