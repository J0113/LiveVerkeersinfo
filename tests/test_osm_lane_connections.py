"""Directed connection tests for the independent OSM Lanes layer."""

from __future__ import annotations

import math
from itertools import combinations

from pyproj import Transformer
from shapely import from_wkt
from shapely.geometry import LineString
from shapely.ops import substring, transform

from ndwinfo.geometry.directed_lines import (
    angle_delta_deg,
    bearing_deg,
    turn_token_matches,
    unit_vector,
)
from ndwinfo.parsers.osm_lane_connections import (
    _lane_turn_tokens,
    build_lane_connections,
    build_lane_network,
    lane_traversals,
)
from ndwinfo.parsers.osm_lane_lines import make_lane_line_rows

NODE = (4.713322, 52.5169868)
M_LON = 1.0 / 68000.0
M_LAT = 1.0 / 111200.0
RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)
WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)


def _point(east_m, north_m):
    return NODE[0] + east_m * M_LON, NODE[1] + north_m * M_LAT


def _road(road_id, offsets, tags, *, nodes, shared_nodes=None):
    line = LineString([_point(*offset) for offset in offsets])
    rows, failures = make_lane_line_rows(
        road_id,
        tags.get("highway", "motorway"),
        tags,
        line,
        node_refs=nodes,
        shared_node_ids=set(nodes) if shared_nodes is None else set(shared_nodes),
    )
    assert failures == []
    return rows


def _contexts(*items):
    return {
        road_id: {"highway": tags.get("highway", "motorway"), "tags": tags}
        for road_id, tags in items
    }


def _road_rd(road_id, coordinates, tags, *, nodes):
    return _road(
        road_id,
        [
            (
                (coordinate[0] - 105329.72126189008),
                (coordinate[1] - 499002.9422807221),
            )
            for coordinate in coordinates
        ],
        tags,
        nodes=nodes,
    )


def _bearing_offset(length_m, bearing_degrees):
    angle = math.radians(bearing_degrees)
    return length_m * math.cos(angle), length_m * math.sin(angle)


def test_turn_tokens_use_the_drivers_left_and_right():
    assert turn_token_matches("left", 90)
    assert not turn_token_matches("left", -90)
    assert turn_token_matches("right", -90)
    assert not turn_token_matches("right", 90)


def test_equal_count_straight_maps_lane_i_to_i():
    first_tags = {"highway": "motorway", "oneway": "yes", "lanes": "2", "ref": "A9"}
    second_tags = dict(first_tags)
    rows = _road(1, [(0, 50), (0, 0)], first_tags, nodes=[10, 11])
    # A small junction-box gap means the movement needs a connector feature;
    # perfectly touching lane endpoints deliberately omit redundant geometry.
    rows += _road(2, [(0, -2), (0, -50)], second_tags, nodes=[11, 12])
    connections, diagnostics, _ = build_lane_connections(
        rows, _contexts((1, first_tags), (2, second_tags))
    )
    assert diagnostics == []
    assert {
        (row["from_lane_id"].rsplit(":", 1)[-1], row["to_lane_id"].rsplit(":", 1)[-1])
        for row in connections
    } == {("1", "1"), ("2", "2")}
    assert all("from_trim_m" not in row["raw"] for row in connections)
    assert all("to_trim_m" not in row["raw"] for row in connections)


def test_legacy_rows_without_original_endpoints_are_excluded_from_adjacency():
    tags = {"highway": "primary", "oneway": "yes", "lanes": "1", "ref": "N1"}
    rows = _road(1, [(0, 30), (0, 0)], tags, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -30)], tags, nodes=[11, 12])
    legacy_rows = []
    for row in rows:
        raw = dict(row["raw"])
        raw.pop("source_start")
        raw.pop("source_end")
        legacy_rows.append({**row, "raw": raw})

    connections, diagnostics, counters = build_lane_connections(
        legacy_rows,
        _contexts((1, tags), (2, tags)),
    )

    assert connections == []
    assert counters["missing_source_geometry"] == 2
    assert {
        (item["segment_id"], item["direction"])
        for item in diagnostics
        if item.get("reason") == "missing_source_geometry"
    } == {("1:10:11", "fwd"), ("2:11:12", "fwd")}


def test_bidirectional_lane_offsets_taper_into_separate_oneway_roads():
    incoming_tags = {
        "highway": "primary",
        "name": "Provincialeweg",
        "ref": "N203",
        "lanes": "1",
        "oneway": "yes",
    }
    bidirectional_tags = {
        "highway": "primary",
        "name": "Provincialeweg",
        "ref": "N203",
        "lanes": "2",
    }
    outgoing_tags = dict(incoming_tags)
    common = (4.711054, 52.518257)
    rows = []
    for road_id, tags, coordinates, nodes in (
        (
            6627417,
            incoming_tags,
            [(4.712094, 52.5181464), (4.7111994, 52.5182933), common],
            [1837442703, 2475804084, 5446407093],
        ),
        (
            565536411,
            bidirectional_tags,
            [common, (4.7104497, 52.5181649), (4.7069876, 52.5195858)],
            [5446407093, 2475804080, 262843290],
        ),
        (
            565536408,
            outgoing_tags,
            [common, (4.711287, 52.5182623), (4.7120417, 52.5181048)],
            [5446407093, 1837442717, 1837442709],
        ),
    ):
        produced, failures = make_lane_line_rows(
            road_id,
            tags["highway"],
            tags,
            LineString(coordinates),
            node_refs=nodes,
            shared_node_ids={5446407093},
        )
        assert failures == []
        rows.extend(produced)

    connections, _, _ = build_lane_connections(
        rows,
        _contexts(
            (6627417, incoming_tags),
            (565536411, bidirectional_tags),
            (565536408, outgoing_tags),
        ),
    )
    by_pair = {
        (row["from_lane_id"], row["to_lane_id"]): row for row in connections
    }
    assert (
        "ll:6627417:1837442703:5446407093:fwd:1",
        "ll:565536411:5446407093:262843290:fwd:1",
    ) in by_pair
    eastbound = by_pair[
        (
            "ll:565536411:5446407093:262843290:bwd:1",
            "ll:565536408:5446407093:1837442709:fwd:1",
        )
    ]
    assert eastbound["raw"]["from_trim_m"] >= 5.0
    assert eastbound["raw"]["to_trim_m"] >= 5.0

    source_row = next(
        row for row in rows if row["id"] == eastbound["from_lane_id"]
    )
    target_row = next(
        row for row in rows if row["id"] == eastbound["to_lane_id"]
    )
    source = transform(WGS84_TO_RD.transform, from_wkt(source_row["geom"]))
    target = transform(WGS84_TO_RD.transform, from_wkt(target_row["geom"]))
    source_visible = substring(
        source, 0, source.length - eastbound["raw"]["from_trim_m"]
    )
    target_visible = substring(target, eastbound["raw"]["to_trim_m"], target.length)
    source_direction = unit_vector(
        source_visible.coords[-2], source_visible.coords[-1]
    )
    target_direction = unit_vector(
        target_visible.coords[0], target_visible.coords[1]
    )
    travel_direction = unit_vector(
        (0.0, 0.0),
        (
            source_direction[0] + target_direction[0],
            source_direction[1] + target_direction[1],
        ),
    )
    connector = transform(WGS84_TO_RD.transform, from_wkt(eastbound["geom"]))
    assert len(connector.coords) == 2
    assert abs(
        connector.length
        - LineString([connector.coords[0], connector.coords[-1]]).length
    ) < 0.001
    assert all(
        (end[0] - start[0]) * travel_direction[0]
        + (end[1] - start[1]) * travel_direction[1]
        > 0
        for start, end in zip(connector.coords, connector.coords[1:])
    )


def test_equal_direction_count_lateral_shift_uses_straight_taper():
    three_lane_tags = {
        "highway": "secondary",
        "name": "Viaductweg",
        "lanes": "3",
        "lanes:forward": "1",
        "lanes:backward": "2",
    }
    two_lane_tags = {
        "highway": "secondary",
        "name": "Viaductweg",
        "lanes": "2",
    }
    rows = _road(
        6626259,
        [(0, 80), (0, 0)],
        three_lane_tags,
        nodes=[46716246, 46717206],
    )
    rows += _road(
        6626303,
        [(0, 0), (0, -80)],
        two_lane_tags,
        nodes=[46717206, 6930249630],
    )

    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts((6626259, three_lane_tags), (6626303, two_lane_tags)),
    )

    assert diagnostics == []
    connector_row = next(
        row
        for row in connections
        if row["from_lane_id"].endswith(":fwd:1")
        and row["to_lane_id"].endswith(":fwd:1")
    )
    assert 6.9 <= connector_row["raw"]["from_trim_m"] <= 7.1
    assert 6.9 <= connector_row["raw"]["to_trim_m"] <= 7.1
    connector = transform(WGS84_TO_RD.transform, from_wkt(connector_row["geom"]))
    assert len(connector.coords) == 2
    assert connector.is_simple
    assert abs(
        connector.length
        - LineString([connector.coords[0], connector.coords[-1]]).length
    ) < 0.001


def test_link_to_bidirectional_road_uses_bezier_above_straight_angle_threshold():
    link_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
    }
    bidirectional_tags = {
        "highway": "secondary",
        "lanes": "2",
    }
    target_end = _bearing_offset(80.0, 31.5)
    rows = _road(6627424, [(-80, 0), (0, 0)], link_tags, nodes=[10, 11])
    rows += _road(
        6627361,
        [(0, 0), target_end],
        bidirectional_tags,
        nodes=[11, 12],
    )

    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts((6627424, link_tags), (6627361, bidirectional_tags)),
    )

    assert diagnostics == []
    connector_row = next(
        row
        for row in connections
        if row["from_road_id"] == 6627424 and row["to_road_id"] == 6627361
    )
    assert 6.9 <= connector_row["raw"]["from_trim_m"] <= 7.1
    assert 6.9 <= connector_row["raw"]["to_trim_m"] <= 7.1
    connector = transform(WGS84_TO_RD.transform, from_wkt(connector_row["geom"]))
    assert len(connector.coords) == 13
    assert connector.is_simple


def test_motorway_lane_four_exit_gets_full_link_transition_runway():
    mainline_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "4",
        "turn:lanes": "none|none|none|slight_right",
    }
    link_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
    }
    target_end = _bearing_offset(100.0, -15.0)
    rows = _road(
        511171447,
        [(-100, 0), (0, 0)],
        mainline_tags,
        nodes=[10, 11],
    )
    rows += _road(
        6627424,
        [(0, 0), target_end],
        link_tags,
        nodes=[11, 12],
    )

    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts((511171447, mainline_tags), (6627424, link_tags)),
    )

    assert diagnostics == []
    connector_row = next(
        row
        for row in connections
        if row["from_lane_id"].endswith(":fwd:4")
        and row["to_lane_id"].endswith(":fwd:1")
    )
    assert 20.9 <= connector_row["raw"]["from_trim_m"] <= 21.1
    assert 20.9 <= connector_row["raw"]["to_trim_m"] <= 21.1
    connector = transform(WGS84_TO_RD.transform, from_wkt(connector_row["geom"]))
    assert connector.is_simple
    assert connector.length < 1.05 * LineString(
        [connector.coords[0], connector.coords[-1]]
    ).length


def test_short_link_entry_can_use_eighty_percent_for_smooth_lane_four_handover():
    link_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
    }
    mainline_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "4",
    }
    target_end = _bearing_offset(100.0, -16.0)
    rows = _road(
        1337245183,
        [(-25.32, 0), (0, 0)],
        link_tags,
        nodes=[10, 11],
    )
    rows += _road(
        490418308,
        [(0, 0), target_end],
        mainline_tags,
        nodes=[11, 12],
    )

    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts((1337245183, link_tags), (490418308, mainline_tags)),
    )

    assert diagnostics == []
    connector_row = next(
        row
        for row in connections
        if row["from_lane_id"].endswith(":fwd:1")
        and row["to_lane_id"].endswith(":fwd:4")
    )
    assert 20.2 <= connector_row["raw"]["from_trim_m"] <= 20.3
    assert 20.9 <= connector_row["raw"]["to_trim_m"] <= 21.1
    connector = transform(WGS84_TO_RD.transform, from_wkt(connector_row["geom"]))
    assert connector.is_simple
    assert connector.length < 1.05 * LineString(
        [connector.coords[0], connector.coords[-1]]
    ).length


def test_exact_near_straight_link_entry_gets_extra_transition_runway():
    link_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A9",
    }
    mainline_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A9",
        "placement": "right_of:1",
        "turn:lanes": "none|none|merge_to_left",
    }
    source_tail = _bearing_offset(30.0, 26.0)
    target_end = _bearing_offset(120.0, 40.0)
    rows = _road(
        6626441,
        [(-100, -80), (-source_tail[0], -source_tail[1]), (0, 0)],
        link_tags,
        nodes=[9, 10, 11],
        shared_nodes={11},
    )
    rows += _road(
        1017357984,
        [(0, 0), target_end],
        mainline_tags,
        nodes=[11, 12],
    )

    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts((6626441, link_tags), (1017357984, mainline_tags)),
    )

    assert diagnostics == []
    connector_row = next(
        row
        for row in connections
        if row["from_lane_id"].endswith(":fwd:1")
        and row["to_lane_id"].endswith(":fwd:3")
    )
    assert 41.9 <= connector_row["raw"]["from_trim_m"] <= 42.1
    assert 41.9 <= connector_row["raw"]["to_trim_m"] <= 42.1
    connector = transform(WGS84_TO_RD.transform, from_wkt(connector_row["geom"]))
    assert connector.is_simple
    assert connector.length < 1.01 * LineString(
        [connector.coords[0], connector.coords[-1]]
    ).length


def test_merge_to_left_adds_many_to_one_join_connection():
    source_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A22",
        "turn:lanes": "none|none|merge_to_left",
    }
    target_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
    }
    rows = _road(411074875, [(0, 50), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(511169421, [(0, -2), (0, -50)], target_tags, nodes=[11, 12])
    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts((411074875, source_tags), (511169421, target_tags)),
    )

    assert diagnostics == []
    assert {
        (
            row["from_lane_id"].rsplit(":", 1)[-1],
            row["to_lane_id"].rsplit(":", 1)[-1],
            row["connection_type"],
        )
        for row in connections
    } == {
        ("1", "1", "continuation"),
        ("2", "2", "continuation"),
        ("3", "2", "join"),
    }
    join = next(row for row in connections if row["connection_type"] == "join")
    assert join["raw"]["turn_lane"] == "merge_to_left"
    assert 5.0 <= join["raw"]["from_trim_m"] <= 15.0
    assert 5.0 <= join["raw"]["to_trim_m"] <= 15.0
    connector = from_wkt(join["geom"])
    source_lane = from_wkt(
        next(
            row["geom"]
            for row in rows
            if row["id"] == join["from_lane_id"]
        )
    )
    target_lane = from_wkt(
        next(
            row["geom"]
            for row in rows
            if row["id"] == join["to_lane_id"]
        )
    )
    assert connector.coords[0] != source_lane.coords[-1]
    assert connector.coords[-1] != target_lane.coords[0]


def test_merge_to_right_preserves_surviving_lane_order():
    source_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A22",
        "turn:lanes": "merge_to_right|none|none",
    }
    target_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
    }
    rows = _road(1, [(0, 50), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(0, -2), (0, -50)], target_tags, nodes=[11, 12])
    connections, diagnostics, _ = build_lane_connections(
        rows, _contexts((1, source_tags), (2, target_tags))
    )

    assert {
        (
            row["from_lane_id"].rsplit(":", 1)[-1],
            row["to_lane_id"].rsplit(":", 1)[-1],
        )
        for row in connections
    } == {("1", "1"), ("2", "1"), ("3", "2")}


def test_untagged_narrowing_is_unresolved_instead_of_dropping_a_lane():
    source_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A1",
    }
    target_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A1",
    }
    rows = _road(1, [(0, 50), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -50)], target_tags, nodes=[11, 12])

    connections, diagnostics, counters = build_lane_connections(
        rows,
        _contexts((1, source_tags), (2, target_tags)),
    )

    assert connections == []
    unresolved = next(
        item
        for item in diagnostics
        if item.get("reason") == "unresolved_narrowing_merge"
    )
    assert unresolved["source_lanes"] == [1, 2, 3]
    assert unresolved["target_lanes"] == [1, 2]
    assert unresolved["excess_lane_count"] == 1
    assert unresolved["unresolved_source_lanes"] == [1, 2, 3]
    assert counters["unresolved_lane_family_mismatch"] == 1


def test_one_lane_exit_uses_rightmost_mainline_lane():
    main = {"highway": "motorway", "oneway": "yes", "lanes": "3", "ref": "A9"}
    continuation = dict(main)
    ramp = {"highway": "motorway_link", "oneway": "yes", "lanes": "1"}
    rows = _road(1, [(0, 50), (0, 0)], main, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -50)], continuation, nodes=[11, 12])
    rows += _road(3, [(0, 0), (-20, -45)], ramp, nodes=[11, 13])
    connections, _, _ = build_lane_connections(
        rows, _contexts((1, main), (2, continuation), (3, ramp))
    )
    exit_rows = [
        row for row in connections if row["raw"]["movement_type"] == "exit"
    ]
    assert len(exit_rows) == 1
    assert exit_rows[0]["connection_type"] == "continuation"
    assert exit_rows[0]["from_lane_id"].endswith(":fwd:3")
    assert exit_rows[0]["to_lane_id"].endswith(":fwd:1")


def test_one_lane_entry_targets_rightmost_mainline_lane():
    ramp = {"highway": "motorway_link", "oneway": "yes", "lanes": "1"}
    target = {"highway": "motorway", "oneway": "yes", "lanes": "3", "ref": "A9"}
    rows = _road(1, [(-20, 45), (0, 0)], ramp, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -50)], target, nodes=[11, 12])
    connections, _, _ = build_lane_connections(
        rows, _contexts((1, ramp), (2, target))
    )
    entry = next(
        row for row in connections if row["raw"]["movement_type"] == "entry"
    )
    assert entry["connection_type"] == "continuation"
    assert entry["to_lane_id"].endswith(":fwd:3")


def test_roundabout_approach_and_exit_connect_to_the_ring():
    approach_tags = {"highway": "primary", "oneway": "yes", "lanes": "1"}
    ring_tags = {
        "highway": "primary",
        "junction": "roundabout",
        "lanes": "1",
        "placement": "right_of:1",
    }
    exit_tags = {"highway": "primary", "oneway": "yes", "lanes": "1"}
    rows = _road(1, [(0, 30), (0, 0)], approach_tags, nodes=[10, 11])
    rows += _road(2, [(0, 0), (-30, -30)], ring_tags, nodes=[11, 12])
    rows += _road(3, [(-30, -30), (0, -60)], exit_tags, nodes=[12, 13])

    connections, _, _ = build_lane_connections(
        rows,
        _contexts(
            (1, approach_tags),
            (2, ring_tags),
            (3, exit_tags),
        ),
    )

    road_pairs = {
        (row["from_road_id"], row["to_road_id"]): row
        for row in connections
    }
    assert {(1, 2), (2, 3)} <= set(road_pairs)
    assert road_pairs[(1, 2)]["raw"]["movement_type"] == "roundabout"
    assert road_pairs[(2, 3)]["raw"]["movement_type"] == "roundabout"
    assert road_pairs[(1, 2)]["raw"]["adjacency_evidence"] == "node_exact"
    assert road_pairs[(2, 3)]["raw"]["adjacency_evidence"] == "node_exact"


def test_closed_roundabout_connectors_land_on_attached_logical_segments():
    approach_tags = {"highway": "primary", "oneway": "yes", "lanes": "1"}
    ring_tags = {
        "highway": "primary",
        "junction": "roundabout",
        "lanes": "1",
        "placement": "right_of:1",
    }
    exit_tags = {"highway": "primary", "oneway": "yes", "lanes": "1"}
    rows = _road(
        1,
        [(0, 30), (0, 0)],
        approach_tags,
        nodes=[10, 100],
        shared_nodes={100},
    )
    rows += _road(
        2,
        [(0, 0), (-30, -30), (0, -60), (30, -30), (0, 0)],
        ring_tags,
        nodes=[100, 101, 102, 103, 100],
        shared_nodes={100, 102},
    )
    rows += _road(
        3,
        [(0, -60), (0, -90)],
        exit_tags,
        nodes=[102, 13],
        shared_nodes={102},
    )

    connections, _, _ = build_lane_connections(
        rows,
        _contexts(
            (1, approach_tags),
            (2, ring_tags),
            (3, exit_tags),
        ),
    )

    approach_to_ring = next(
        row
        for row in connections
        if row["from_road_id"] == 1 and row["to_road_id"] == 2
    )
    ring_to_exit = next(
        row
        for row in connections
        if row["from_road_id"] == 2 and row["to_road_id"] == 3
    )
    assert approach_to_ring["to_segment_id"] == "2:100:102"
    assert ring_to_exit["from_segment_id"] == "2:100:102"
    assert approach_to_ring["raw"]["adjacency_evidence"] == "node_exact"
    assert ring_to_exit["raw"]["adjacency_evidence"] == "node_exact"


def test_shared_lane_expands_to_two_traversals_without_duplicate_feature():
    tags = {"highway": "secondary", "lanes": "1", "ref": "N1"}
    rows = _road(1, [(0, 20), (0, -20)], tags, nodes=[10, 11])
    traversals = lane_traversals(rows, _contexts((1, tags)))
    assert len(rows) == 1
    assert {traversal.direction for traversal in traversals} == {"fwd", "bwd"}
    assert traversals[0].lane_id == traversals[1].lane_id


def test_shared_backward_travel_exit_is_stored_first_coordinate():
    tags = {"highway": "secondary", "lanes": "1"}
    rows = _road(1, [(0, 20), (0, -20)], tags, nodes=[10, 11])
    stored = from_wkt(rows[0]["geom"])
    backward = next(
        traversal
        for traversal in lane_traversals(rows, _contexts((1, tags)))
        if traversal.direction == "bwd"
    )
    assert backward.exit == stored.coords[0]


def test_manual_block_removes_and_manual_connect_wins():
    tags = {"highway": "primary", "oneway": "yes", "lanes": "1", "ref": "N1"}
    rows = _road(1, [(0, 20), (0, 0)], tags, nodes=[10, 11])
    rows += _road(2, [(0, -2), (0, -20)], tags, nodes=[11, 12])
    traversals = lane_traversals(rows, _contexts((1, tags), (2, tags)))
    source, target = traversals[0], traversals[1]
    blocked, _, _ = build_lane_connections(
        rows,
        _contexts((1, tags), (2, tags)),
        overrides=[{"from": source.id, "to": target.id, "action": "block"}],
    )
    assert blocked == []
    manual, _, _ = build_lane_connections(
        rows,
        _contexts((1, tags), (2, tags)),
        overrides=[
            {
                "from": source.id,
                "to": target.id,
                "action": "connect",
                "note": "reviewed",
            }
        ],
    )
    assert manual[0]["connection_type"] == "manual"
    assert manual[0]["confidence"] == "manual"


def test_coordinate_coincident_endpoints_are_exact_without_node_ids():
    tags = {"highway": "motorway", "oneway": "yes", "lanes": "1", "ref": "A1"}
    rows = _road(1, [(0, 30), (0, 0)], tags, nodes=[0, 0])
    rows += _road(2, [(0, 0), (0, -30)], tags, nodes=[0, 0])

    connections, _, counters = build_lane_connections(
        rows,
        _contexts((1, tags), (2, tags)),
    )

    assert counters["endpoint_exact_movements"] == 1
    # Touching one-lane centrelines omit redundant connector geometry, but the
    # exact movement is still selected and counted.
    assert connections == []


def test_bidirectional_backward_does_not_use_bare_turn_lanes():
    tags = {
        "highway": "primary",
        "lanes": "2",
        "lanes:forward": "1",
        "lanes:backward": "1",
        "turn:lanes": "slight_right",
    }
    rows = _road(1, [(0, 20), (0, -20)], tags, nodes=[10, 11])
    traversals = lane_traversals(rows, _contexts((1, tags)))

    forward = next(item for item in traversals if item.direction == "fwd")
    backward = next(item for item in traversals if item.direction == "bwd")
    assert _lane_turn_tokens(forward) == {"slight_right"}
    assert _lane_turn_tokens(backward) == set()


def test_reverse_oneway_uses_bare_turn_lanes_for_backward_travel():
    tags = {
        "highway": "motorway",
        "oneway": "-1",
        "lanes": "2",
        "turn:lanes": "none|merge_to_left",
    }
    rows = _road(1, [(0, 20), (0, -20)], tags, nodes=[10, 11])
    traversals = lane_traversals(rows, _contexts((1, tags)))

    assert {item.direction for item in traversals} == {"bwd"}
    by_lane = {item.lane_nr: _lane_turn_tokens(item) for item in traversals}
    assert by_lane == {1: {"none"}, 2: {"merge_to_left"}}


def test_invalid_turn_cardinality_cannot_make_box_candidate_eligible():
    source_tags = {
        "highway": "primary",
        "oneway": "yes",
        "lanes": "2",
        "turn:lanes": "slight_right",
    }
    target_tags = {"highway": "primary", "oneway": "yes", "lanes": "1"}
    rows = _road(1, [(0, 30), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(-2, -2), (-20, -30)], target_tags, nodes=[20, 21])

    connections, diagnostics, counters = build_lane_connections(
        rows,
        _contexts((1, source_tags), (2, target_tags)),
    )

    assert connections == []
    assert counters["invalid_turn_lane_cardinality"] == 1
    assert any(item.get("reason") == "invalid_lane_tag_cardinality" for item in diagnostics)


def test_a22_short_transition_suppresses_upstream_shortcuts_and_allocates_families():
    upstream = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A22",
        "turn:lanes": "none|merge_to_left|slight_right",
        "placement": "right_of:1",
    }
    transition = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "4",
        "ref": "A22",
        "turn:lanes": "none|merge_to_left|slight_right|slight_right",
        "placement": "right_of:1",
    }
    mainline = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
        "turn:lanes": "none|merge_to_left",
    }
    exit_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "2",
        "placement": "transition",
    }
    rows = _road(1227426726, [(0, 60), (0, 20)], upstream, nodes=[10, 11])
    rows += _road(1096129216, [(0, 20), (0, 0)], transition, nodes=[11, 12])
    rows += _road(1096129213, [(0, 0), (0, -60)], mainline, nodes=[12, 13])
    rows += _road(1096129217, [(0, 0), (-20, -50)], exit_tags, nodes=[12, 14])

    connections, diagnostics, counters = build_lane_connections(
        rows,
        _contexts(
            (1227426726, upstream),
            (1096129216, transition),
            (1096129213, mainline),
            (1096129217, exit_tags),
        ),
    )
    road_pairs = {
        (row["from_road_id"], row["to_road_id"])
        for row in connections
    }
    assert (1227426726, 1096129216) in road_pairs
    assert (1096129216, 1096129213) in road_pairs
    assert (1096129216, 1096129217) in road_pairs
    assert (1227426726, 1096129213) not in road_pairs
    assert (1227426726, 1096129217) not in road_pairs
    assert (
        counters["junction_box_suppressed_intermediate"]
        + counters["junction_box_rejected_existing_link_predecessor"]
        >= 2
    )
    assert any(
        (
            item.get("reason") == "intermediate_segment_dominates"
            and item.get("dominated_via")
        )
        or item.get("reason") == "existing_link_predecessor_rejects_new_exit"
        for item in diagnostics
    )

    upstream_to_transition = {
        (
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
            row["connection_type"],
        )
        for row in connections
        if row["from_road_id"] == 1227426726
        and row["to_road_id"] == 1096129216
    }
    assert upstream_to_transition == {
        (1, 1, "continuation"),
        (2, 2, "continuation"),
        (3, 3, "continuation"),
        (3, 4, "split"),
    }
    transition_to_mainline = {
        (
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
            row["connection_type"],
        )
        for row in connections
        if row["from_road_id"] == 1096129216
        and row["to_road_id"] == 1096129213
    }
    assert transition_to_mainline == {
        (1, 1, "continuation"),
        (2, 2, "continuation"),
    }
    transition_to_exit = {
        (
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
        )
        for row in connections
        if row["from_road_id"] == 1096129216
        and row["to_road_id"] == 1096129217
    }
    assert transition_to_exit == {(3, 1), (4, 2)}


def test_a22_live_geometry_uses_placement_and_two_sided_trims_without_crossings():
    upstream = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A22",
        "turn:lanes": "none|merge_to_left|slight_right",
        "placement": "right_of:1",
    }
    transition = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "4",
        "ref": "A22",
        "turn:lanes": "none|merge_to_left|slight_right|slight_right",
        "placement": "right_of:1",
    }
    mainline = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
        "turn:lanes": "none|merge_to_left",
    }
    exit_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "2",
        "placement": "transition",
    }
    rows = _road_rd(
        1227426726,
        [(105375.86233619947, 499079.6678778341), (105335.72654123965, 499012.92975589226)],
        upstream,
        nodes=[10, 11],
    )
    rows += _road_rd(
        1096129216,
        [(105335.72654123965, 499012.92975589226), (105329.72126189008, 499002.9422807221)],
        transition,
        nodes=[11, 12],
    )
    rows += _road_rd(
        1096129213,
        [
            (105329.72126189008, 499002.9422807221),
            (105316.80355435025, 498981.2851381327),
            (105281.81510143795, 498922.4738629898),
            (105188.59887957995, 498767.409676974),
        ],
        mainline,
        nodes=[12, 13, 14, 15],
    )
    rows += _road_rd(
        1096129217,
        [(105329.72126189008, 499002.9422807221), (105308.627416987, 498986.44184050773)],
        exit_tags,
        nodes=[12, 16],
    )

    connections, _, _ = build_lane_connections(
        rows,
        _contexts(
            (1227426726, upstream),
            (1096129216, transition),
            (1096129213, mainline),
            (1096129217, exit_tags),
        ),
    )
    fixture = [
        row
        for row in connections
        if row["from_road_id"] in {1227426726, 1096129216}
        and row["to_road_id"] in {1096129216, 1096129213, 1096129217}
    ]
    connector_lines = [
        transform(WGS84_TO_RD.transform, from_wkt(row["geom"])) for row in fixture
    ]
    assert not any(first.crosses(second) for first, second in combinations(connector_lines, 2))

    downstream = [
        row
        for row in fixture
        if row["from_road_id"] == 1096129216
        and row["to_road_id"] in {1096129213, 1096129217}
    ]
    assert len(downstream) == 4
    assert all(row["raw"].get("from_trim_m") for row in downstream)
    assert all(row["raw"].get("to_trim_m") for row in downstream)


def test_a22_entry_and_mainline_share_target_handle_without_crossing():
    mainline = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
    }
    entry = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A22",
        "placement": "transition",
    }
    target = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A22",
        "placement": "right_of:1",
        "turn:lanes": "none|none|merge_to_left",
    }
    rows = _road_rd(
        6632505,
        [(105331.26462980732, 498981.50632425066), (105356.13149098714, 499023.1279882635)],
        mainline,
        nodes=[10, 11],
    )
    rows += _road_rd(
        1096339332,
        [(105346.33586369031, 498995.73087504087), (105356.13149098714, 499023.1279882635)],
        entry,
        nodes=[12, 11],
    )
    rows += _road_rd(
        411074875,
        [(105356.13149098714, 499023.1279882635), (105402.15916726188, 499100.1220308526)],
        target,
        nodes=[11, 13],
    )

    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts(
            (6632505, mainline),
            (1096339332, entry),
            (411074875, target),
        ),
    )
    target_connections = [
        row for row in connections if row["to_road_id"] == 411074875
    ]
    connector_lines = {
        row["id"]: transform(WGS84_TO_RD.transform, from_wkt(row["geom"]))
        for row in target_connections
    }
    crossing_pairs = [
        (first_id, second_id)
        for (first_id, first), (second_id, second) in combinations(
            connector_lines.items(), 2
        )
        if first.crosses(second)
    ]
    assert crossing_pairs == []
    lane_three = [
        row
        for row in target_connections
        if row["to_lane_id"].endswith(":fwd:3")
    ]
    assert len(lane_three) == 1
    entry_connection = lane_three[0]
    source_row = next(
        row for row in rows if row["id"] == entry_connection["from_lane_id"]
    )
    source_line = transform(WGS84_TO_RD.transform, from_wkt(source_row["geom"]))
    source_visible = substring(
        source_line,
        0,
        source_line.length - entry_connection["raw"]["from_trim_m"],
    )
    connector = connector_lines[entry_connection["id"]]
    source_bearing = bearing_deg(source_visible.coords[-2], source_visible.coords[-1])
    connector_bearing = bearing_deg(connector.coords[0], connector.coords[1])
    assert abs(angle_delta_deg(source_bearing, connector_bearing)) < 5.0
    assert any(
        item.get("reason")
        in {"entry_claims_added_lane", "resolved_multi_source_lane_blocks"}
        for item in diagnostics
    )


def test_destination_ref_lanes_selects_the_signed_exit_lane():
    source_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "turn:lanes": "slight_right|slight_right",
        "destination:ref:lanes": "A1|A2",
    }
    target_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A2",
    }
    rows = _road(1, [(0, 30), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(0, 0), (-20, -30)], target_tags, nodes=[11, 12])

    connections, _, _ = build_lane_connections(
        rows,
        _contexts((1, source_tags), (2, target_tags)),
    )

    assert len(connections) == 1
    assert connections[0]["from_lane_id"].endswith(":fwd:2")
    assert connections[0]["raw"]["destination_ref_lane"] == "a2"


def test_change_lanes_can_block_inferred_right_split():
    source_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A1",
        "turn:lanes": "slight_right",
        "change:lanes": "not_right",
        "placement": "right_of:1",
    }
    target_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A1",
        "turn:lanes": "slight_right|slight_right",
        "placement": "right_of:1",
    }
    rows = _road(1, [(0, 30), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -30)], target_tags, nodes=[11, 12])

    connections, diagnostics, counters = build_lane_connections(
        rows,
        _contexts((1, source_tags), (2, target_tags)),
    )

    assert len(connections) == 1
    assert connections[0]["to_lane_id"].endswith(":fwd:1")
    assert counters["change_lane_conflicts"] == 1
    assert any(item.get("reason") == "change_lanes_conflict" for item in diagnostics)


def test_undominated_offset_exit_remains_beside_exact_mainline():
    main = {"highway": "motorway", "oneway": "yes", "lanes": "2", "ref": "A1"}
    continuation = dict(main)
    ramp = {"highway": "motorway_link", "oneway": "yes", "lanes": "1", "ref": "A2"}
    rows = _road(1, [(0, 40), (0, 0)], main, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -40)], continuation, nodes=[11, 12])
    rows += _road(3, [(-3, -2), (-25, -35)], ramp, nodes=[20, 21])

    connections, diagnostics, _ = build_lane_connections(
        rows,
        _contexts((1, main), (2, continuation), (3, ramp)),
    )

    by_roads = {
        (row["from_road_id"], row["to_road_id"]): row for row in connections
    }
    assert set(by_roads) == {(1, 3)}
    exit_row = by_roads[(1, 3)]
    assert exit_row["raw"]["adjacency_evidence"] == "junction_box"
    # Junction-box guesses above 30 degrees do not receive the wider exact-node
    # link taper. One-sided trimming of this uncertain movement can make the
    # cubic loop back over itself.
    assert "from_trim_m" not in exit_row["raw"]
    assert "to_trim_m" not in exit_row["raw"]
    assert not any(
        item.get("reason") == "intermediate_segment_dominates"
        for item in diagnostics
    )


def test_junction_box_rejects_wide_same_ref_link_transition():
    source_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A22",
    }
    link_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A22",
    }
    rows = _road(1, [(0, 30), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(-2, -2), (-30, -2)], link_tags, nodes=[20, 21])

    connections, _, _ = build_lane_connections(
        rows,
        _contexts((1, source_tags), (2, link_tags)),
    )

    assert connections == []


def test_unrelated_exact_target_predecessor_does_not_suppress_tagged_branch():
    source_tags = {
        "highway": "primary",
        "oneway": "yes",
        "lanes": "1",
        "turn:lanes": "right",
    }
    continuation_tags = {
        "highway": "primary",
        "oneway": "yes",
        "lanes": "1",
    }
    target_tags = {
        "highway": "secondary",
        "oneway": "yes",
        "lanes": "1",
    }
    rows = _road(1, [(0, 30), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -30)], continuation_tags, nodes=[11, 12])
    rows += _road(3, [(30, -2), (-2, -2)], target_tags, nodes=[20, 21])
    rows += _road(4, [(-2, -2), (-30, -2)], target_tags, nodes=[21, 22])

    connections, diagnostics, counters = build_lane_connections(
        rows,
        _contexts(
            (1, source_tags),
            (2, continuation_tags),
            (3, target_tags),
            (4, target_tags),
        ),
    )

    branch = next(
        row
        for row in connections
        if row["from_road_id"] == 1 and row["to_road_id"] == 4
    )
    assert branch["confidence"] == "junction_box"
    assert branch["raw"]["movement_type"] == "exit"
    assert not any(
        item.get("reason") == "intermediate_segment_dominates"
        and item.get("from") == branch["from_lane_id"]
        and item.get("to") == branch["to_lane_id"]
        for item in diagnostics
    )
    assert "junction_box_suppressed_exact_target" not in counters


def test_existing_exact_link_predecessor_rejects_junction_box_exit_shortcut():
    source_tags = {
        "highway": "primary",
        "oneway": "yes",
        "lanes": "1",
    }
    link_tags = {
        "highway": "primary_link",
        "oneway": "yes",
        "lanes": "1",
    }
    rows = _road(1, [(0, 30), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(2, [(10, 20), (-3, -10)], link_tags, nodes=[20, 21])
    rows += _road(3, [(-3, -10), (-15, -40)], link_tags, nodes=[21, 22])

    connections, diagnostics, counters = build_lane_connections(
        rows,
        _contexts((1, source_tags), (2, link_tags), (3, link_tags)),
    )

    assert not any(
        row["from_road_id"] == 1 and row["to_road_id"] == 3
        for row in connections
    )
    assert counters["junction_box_rejected_existing_link_predecessor"] >= 1
    assert any(
        item.get("reason") == "existing_link_predecessor_rejects_new_exit"
        and item.get("exact_predecessors") == ["2:20:21"]
        for item in diagnostics
    )


def test_junction_box_does_not_connect_ordinary_road_directly_to_motorway():
    source_tags = {
        "highway": "primary",
        "oneway": "yes",
        "lanes": "1",
        "turn:lanes": "slight_right",
    }
    motorway_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "1",
    }
    rows = _road(1, [(0, 30), (0, 0)], source_tags, nodes=[10, 11])
    rows += _road(
        2,
        [(-3, -10), (-15, -40)],
        motorway_tags,
        nodes=[20, 21],
    )

    connections, _, _ = build_lane_connections(
        rows,
        _contexts((1, source_tags), (2, motorway_tags)),
    )

    assert connections == []


def test_junction_box_rejects_grade_separated_tagged_turn():
    bridge_tags = {
        "highway": "primary",
        "oneway": "yes",
        "lanes": "1",
        "turn:lanes": "right",
        "bridge": "yes",
        "layer": "1",
    }
    surface_tags = {
        "highway": "primary",
        "oneway": "yes",
        "lanes": "1",
    }
    rows = _road(1, [(0, 30), (0, 0)], bridge_tags, nodes=[10, 11])
    rows += _road(2, [(-2, -2), (-30, -2)], surface_tags, nodes=[20, 21])

    connections, _, _ = build_lane_connections(
        rows,
        _contexts((1, bridge_tags), (2, surface_tags)),
    )

    assert connections == []


def test_short_transition_uses_one_shared_two_endpoint_trim_budget():
    incoming_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A1",
        "placement": "right_of:1",
    }
    short_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A1",
        "placement": "right_of:1",
    }
    outgoing_tags = dict(incoming_tags)
    rows = _road(1, [(0, 40), (0, 0)], incoming_tags, nodes=[10, 11])
    rows += _road(2, [(0, 0), (0, -12)], short_tags, nodes=[11, 12])
    rows += _road(3, [(0, -12), (0, -50)], outgoing_tags, nodes=[12, 13])

    connections, _, counters = build_lane_connections(
        rows,
        _contexts((1, incoming_tags), (2, short_tags), (3, outgoing_tags)),
    )

    short_lane_id = next(
        row["id"] for row in rows if row["road_id"] == 2 and row["lane_nr"] == 1
    )
    start_trim = max(
        (
            float(row["raw"].get("to_trim_m") or 0)
            for row in connections
            if row["to_lane_id"] == short_lane_id
        ),
        default=0.0,
    )
    end_trim = max(
        (
            float(row["raw"].get("from_trim_m") or 0)
            for row in connections
            if row["from_lane_id"] == short_lane_id
        ),
        default=0.0,
    )
    assert start_trim + end_trim <= 9.6 + 0.02
    assert counters["trim_budget_scaled"] >= 1


def test_two_exact_predecessor_blocks_jointly_fill_four_lane_target():
    left_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A9",
    }
    right_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
    }
    target_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "4",
        "ref": "A9",
    }
    rows = _road(
        490599657,
        [(-5, -60), (0, 0)],
        left_tags,
        nodes=[10, 12],
    )
    rows += _road(
        6626662,
        [(8, -60), (0, 0)],
        right_tags,
        nodes=[11, 12],
    )
    rows += _road(
        490422886,
        [(0, 0), (0, 60)],
        target_tags,
        nodes=[12, 13],
    )

    connections, diagnostics, counters = build_lane_connections(
        rows,
        _contexts(
            (490599657, left_tags),
            (6626662, right_tags),
            (490422886, target_tags),
        ),
    )

    mappings = {
        (
            row["from_road_id"],
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
        )
        for row in connections
        if row["to_road_id"] == 490422886
    }
    assert mappings == {
        (490599657, 1, 1),
        (490599657, 2, 2),
        (6626662, 1, 3),
        (6626662, 2, 4),
    }
    assert all(row["connection_type"] == "continuation" for row in connections)
    assert counters["multi_source_block_allocations"] == 1
    resolved = next(
        item
        for item in diagnostics
        if item.get("reason") == "resolved_multi_source_lane_blocks"
    )
    assert [
        block["target_lanes"] for block in resolved["allocation"]
    ] == [[1, 2], [3, 4]]
    connector_lines = [
        transform(WGS84_TO_RD.transform, from_wkt(row["geom"]))
        for row in connections
    ]
    assert not any(
        first.crosses(second)
        for first, second in combinations(connector_lines, 2)
    )


def test_mixed_link_blocks_anchor_transition_to_its_target_slice():
    main_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A208",
    }
    link_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
        "change:lanes": "no|no",
    }
    transition_tags = {**link_tags, "placement": "transition"}
    target_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "4",
        "ref": "A22",
        "placement": "right_of:1",
    }
    rows = _road(1, [(0, -40), (0, 0)], main_tags, nodes=[10, 20])
    rows += _road(2, [(24, -40), (12, -20)], link_tags, nodes=[30, 31])
    rows += _road(3, [(12, -20), (0, 0)], transition_tags, nodes=[31, 20])
    rows += _road(4, [(0, 0), (0, 40)], target_tags, nodes=[20, 40])
    contexts = _contexts(
        (1, main_tags),
        (2, link_tags),
        (3, transition_tags),
        (4, target_tags),
    )

    resolved_rows, connections, diagnostics, counters, resolved_ids = (
        build_lane_network(rows, contexts)
    )

    mappings = {
        (
            row["from_road_id"],
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
        )
        for row in connections
        if row["to_road_id"] == 4
    }
    assert mappings == {
        (1, 1, 1),
        (1, 2, 2),
        (3, 1, 3),
        (3, 2, 4),
    }
    assert all(
        row["raw"]["allocation_evidence"] == "multi_source_lateral_order"
        for row in connections
        if row["to_road_id"] == 4
    )
    assert counters["multi_source_block_allocations"] == 1
    assert 3 in resolved_ids
    assert not any(
        item.get("reason") == "unresolved_transition_placement"
        and item.get("road_id") == 3
        for item in diagnostics
    )

    by_id = {row["id"]: row for row in resolved_rows}
    for lane_nr, target_lane_nr in ((1, 3), (2, 4)):
        transition = transform(
            WGS84_TO_RD.transform,
            from_wkt(by_id[f"ll:3:31:20:fwd:{lane_nr}"]["geom"]),
        )
        target = transform(
            WGS84_TO_RD.transform,
            from_wkt(by_id[f"ll:4:20:40:fwd:{target_lane_nr}"]["geom"]),
        )
        assert (
            LineString([transition.coords[-1], target.coords[0]]).length
            < 0.05
        )


def test_placement_transition_inherits_connected_lane_endpoints():
    mainline_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
        "access:lanes": "yes|no",
    }
    entry_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A22",
        "access:lanes": "no|yes",
    }
    transition_tags = {
        **entry_tags,
        "placement": "transition",
    }
    target_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "4",
        "ref": "A22",
        "placement": "right_of:1",
        "turn:lanes": "none|none|merge_to_left|none",
        "access:lanes": "yes|no|no|yes",
    }
    rows = _road(1, [(30, 50), (0, 0)], mainline_tags, nodes=[10, 12])
    rows += _road(2, [(0, 50), (0, 32)], entry_tags, nodes=[11, 13])
    rows += _road(3, [(0, 32), (0, 0)], transition_tags, nodes=[13, 12])
    rows += _road(4, [(0, 0), (-10, -30)], target_tags, nodes=[12, 14])
    contexts = _contexts(
        (1, mainline_tags),
        (2, entry_tags),
        (3, transition_tags),
        (4, target_tags),
    )

    (
        resolved_rows,
        connections,
        diagnostics,
        counters,
        resolved_road_ids,
    ) = build_lane_network(rows, contexts)

    assert resolved_road_ids == {3}, diagnostics
    assert counters["resolved_transition_placements"] == 1
    assert any(
        item.get("reason") == "resolved_transition_placement"
        for item in diagnostics
    )
    by_id = {row["id"]: row for row in resolved_rows}
    transition_rows = sorted(
        (row for row in resolved_rows if row["road_id"] == 3),
        key=lambda row: row["lane_nr"],
    )
    target_rows = sorted(
        (row for row in resolved_rows if row["road_id"] == 4),
        key=lambda row: row["lane_nr"],
    )
    expected_targets = target_rows[2:]
    for transition, target in zip(transition_rows, expected_targets):
        transition_line = transform(
            WGS84_TO_RD.transform,
            from_wkt(transition["geom"]),
        )
        target_line = transform(WGS84_TO_RD.transform, from_wkt(target["geom"]))
        assert (
            LineString(
                [transition_line.coords[-1], target_line.coords[0]]
            ).length
            < 0.05
        )
        assert transition["raw"]["transition_placement_resolved"] is True
        assert transition["raw"]["transition_end_shift_m"] > 6.5

    left = transform(
        WGS84_TO_RD.transform,
        from_wkt(transition_rows[0]["geom"]),
    )
    right = transform(
        WGS84_TO_RD.transform,
        from_wkt(transition_rows[1]["geom"]),
    )
    assert all(
        abs(
            LineString([left_point, right_point]).length
            - 3.5
        )
        <= 0.25
        for left_point, right_point in zip(left.coords, right.coords)
    )
    transition_connections = [
        row
        for row in connections
        if row["from_road_id"] == 3 and row["to_road_id"] == 4
    ]
    assert {
        (
            by_id[row["from_lane_id"]]["lane_nr"],
            by_id[row["to_lane_id"]]["lane_nr"],
        )
        for row in transition_connections
    } == {(1, 3), (2, 4)}
    assert all(
        float(row["raw"].get("from_trim_m") or 0) <= 5.01
        for row in transition_connections
    )


def test_a22_exit_prefers_directional_lane_and_reserves_it_from_mainline():
    source_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "4",
        "ref": "A22",
        "placement": "right_of:1",
        "turn:lanes": "none|none|through|slight_right",
        "change:lanes": "yes|yes|not_left|yes",
    }
    mainline_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A22",
        "placement": "right_of:1",
        # This describes the target way's downstream junction and must not
        # remap lanes at its entry node.
        "turn:lanes": "none|none|slight_right",
    }
    transition_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A22",
        "placement": "transition",
    }
    link_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A22",
    }
    rows = _road(
        511168724,
        [(0, -50), (0, 0)],
        source_tags,
        nodes=[8063805632, 1551043751],
    )
    rows += _road(
        511168728,
        [(0, 0), (2, 50)],
        mainline_tags,
        nodes=[1551043751, 5000793253],
    )
    rows += _road(
        1096129203,
        [(0, 0), (16, 40)],
        transition_tags,
        nodes=[1551043751, 916142101],
    )
    rows += _road(
        333802633,
        [(16, 40), (45, 75)],
        link_tags,
        nodes=[916142101, 3408608038],
    )

    resolved_rows, connections, diagnostics, counters, resolved_road_ids = (
        build_lane_network(
            rows,
            _contexts(
                (511168724, source_tags),
                (511168728, mainline_tags),
                (1096129203, transition_tags),
                (333802633, link_tags),
            ),
        )
    )

    source_to_mainline = {
        (
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
        )
        for row in connections
        if row["from_road_id"] == 511168724
        and row["to_road_id"] == 511168728
    }
    source_to_exit = {
        (
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
        )
        for row in connections
        if row["from_road_id"] == 511168724
        and row["to_road_id"] == 1096129203
    }
    assert source_to_mainline == {(1, 1), (2, 2), (3, 3)}
    assert source_to_exit == {(4, 1)}
    transition = next(
        row for row in resolved_rows if row["road_id"] == 1096129203
    )
    assert transition["raw"]["transition_start_from"].endswith(":fwd:4@fwd")
    assert 8.7 <= transition["raw"]["transition_start_shift_m"] <= 8.8
    assert resolved_road_ids == {1096129203}
    assert counters["resolved_transition_placements"] == 1
    assert not any(
        item.get("reason") == "unresolved_transition_placement"
        for item in diagnostics
    )


def test_same_ref_directional_branch_outranks_continuation_fallback():
    source_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "3",
        "ref": "A22",
        "turn:lanes": "none|none|slight_right",
        "placement": "right_of:1",
    }
    mainline_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "ref": "A208",
    }
    branch_tags = {
        "highway": "motorway_link",
        "oneway": "yes",
        "lanes": "1",
        "ref": "A22",
        "placement": "transition",
    }
    rows = _road(1, [(0, 40), (0, 0)], source_tags, nodes=[10, 20])
    rows += _road(2, [(0, 0), (0, -40)], mainline_tags, nodes=[20, 30])
    rows += _road(3, [(0, 0), (-8, -35)], branch_tags, nodes=[20, 40])

    connections, _, _ = build_lane_connections(
        rows,
        _contexts(
            (1, source_tags),
            (2, mainline_tags),
            (3, branch_tags),
        ),
    )

    mappings = {
        (
            row["to_road_id"],
            int(row["from_lane_id"].rsplit(":", 1)[-1]),
            int(row["to_lane_id"].rsplit(":", 1)[-1]),
        )
        for row in connections
        if row["from_road_id"] == 1
    }
    assert mappings == {
        (2, 1, 1),
        (2, 2, 2),
        (3, 3, 1),
    }
    branch = next(row for row in connections if row["to_road_id"] == 3)
    assert branch["raw"]["movement_type"] == "exit"
    assert branch["raw"]["turn_lane"] == "slight_right"
