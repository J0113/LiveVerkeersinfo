from ndwinfo.osm.graph import build_directed_segments
from ndwinfo.osm.tags import (
    is_drivable,
    normalize_road_ref,
    normalize_way_tags,
    normalized_oneway,
    travel_directions,
)


def test_access_and_highway_filter_is_fail_closed():
    assert is_drivable({"highway": "motorway"})
    assert is_drivable({"highway": "primary", "access": "destination"})
    assert not is_drivable({"highway": "primary", "motor_vehicle": "no"})
    assert not is_drivable({"highway": "residential"})
    assert not is_drivable({"highway": "primary", "area": "yes"})


def test_oneway_reverse_and_roundabout_direction_rules():
    reverse = {"highway": "motorway_link", "oneway": "-1"}
    assert normalized_oneway(reverse) == "reverse"
    assert [(d.name, d.reverse_geometry) for d in travel_directions(reverse)] == [
        ("reverse", True)
    ]

    roundabout = {"highway": "primary", "junction": "roundabout"}
    assert normalized_oneway(roundabout) == "forward"
    assert [d.name for d in travel_directions(roundabout)] == ["forward"]

    explicit_two_way = {
        "highway": "primary",
        "junction": "roundabout",
        "oneway": "no",
    }
    assert [d.name for d in travel_directions(explicit_two_way)] == [
        "forward",
        "backward",
    ]


def test_motorway_implied_oneway_and_uncertain_direction_fail_closed():
    motorway = {"highway": "motorway"}
    assert normalized_oneway(motorway) == "forward"
    assert [direction.name for direction in travel_directions(motorway)] == ["forward"]

    explicit_two_way = {"highway": "motorway", "oneway": "no"}
    assert normalized_oneway(explicit_two_way) == "both"
    assert [direction.name for direction in travel_directions(explicit_two_way)] == [
        "forward",
        "backward",
    ]

    untagged_link = {"highway": "motorway_link"}
    assert normalized_oneway(untagged_link) == "unknown"
    assert travel_directions(untagged_link) == ()

    for uncertain in ("reversible", "alternating", "unexpected"):
        tags = {"highway": "primary", "oneway": uncertain}
        assert normalized_oneway(tags) == "unknown"
        assert travel_directions(tags) == ()


def test_directional_tags_override_total_values():
    tags = {
        "highway": "primary",
        "lanes": "5",
        "lanes:forward": "3",
        "lanes:backward": "2",
        "maxspeed": "80",
        "maxspeed:backward": "60",
        "maxspeed:conditional": "100 @ (06:00-19:00)",
        "placement:forward": "middle_of:2",
        "shoulder": "right",
        "ref": "a 12; e 35",
    }
    forward = normalize_way_tags(tags, "forward")
    backward = normalize_way_tags(tags, "backward")
    assert forward["lanes"] == 3
    assert backward["lanes"] == 2
    assert forward["maxspeed"] == "80"
    assert backward["maxspeed"] == "60"
    assert forward["maxspeed_conditional"] == "100 @ (06:00-19:00)"
    assert forward["placement"] == "middle_of:2"
    assert forward["shoulder"] == "right"
    assert normalize_road_ref(tags["ref"]) == "A12;E35"


def test_odd_undirected_lane_total_is_not_guessed():
    tags = {"highway": "primary", "lanes": "3"}
    assert normalize_way_tags(tags, "forward")["lanes"] is None
    assert normalize_way_tags(tags, "backward")["lanes"] is None


def test_bidirectional_total_with_both_ways_lane_is_not_halved():
    for both_ways in ("2", "unknown"):
        tags = {
            "highway": "primary",
            "lanes": "4",
            "lanes:both_ways": both_ways,
        }
        assert normalize_way_tags(tags, "forward")["lanes"] is None
        assert normalize_way_tags(tags, "backward")["lanes"] is None

    explicit = {
        "highway": "primary",
        "lanes": "4",
        "lanes:both_ways": "2",
        "lanes:forward": "1",
        "lanes:backward": "1",
    }
    assert normalize_way_tags(explicit, "forward")["lanes"] == 1
    assert normalize_way_tags(explicit, "backward")["lanes"] == 1


def test_shared_node_splits_way_into_topological_edges_in_both_directions():
    segments = build_directed_segments(
        way_id=42,
        way_version=7,
        node_ids=[10, 11, 12, 13],
        coordinates=[(5.0, 52.0), (5.01, 52.0), (5.02, 52.0), (5.03, 52.0)],
        tags={"highway": "primary", "ref": "N12", "lanes": "2"},
        split_node_ids={12},
    )
    assert len(segments) == 4
    assert [(s.sequence, s.travel_direction) for s in segments] == [
        (0, "forward"),
        (0, "backward"),
        (1, "forward"),
        (1, "backward"),
    ]
    assert segments[0].from_node_id == "osmn_10"
    assert segments[0].to_node_id == "osmn_12"
    assert segments[1].from_node_id == "osmn_12"
    assert segments[1].to_node_id == "osmn_10"
    assert segments[2].from_node_id == "osmn_12"
    assert segments[2].to_node_id == "osmn_13"
    assert all(segment.length_m > 0 for segment in segments)


def test_oneway_minus_one_reverses_geometry_and_endpoints():
    [segment] = build_directed_segments(
        way_id=99,
        way_version=1,
        node_ids=[1, 2, 3],
        coordinates=[(4.0, 52.0), (4.1, 52.0), (4.2, 52.0)],
        tags={"highway": "motorway_link", "oneway": "-1"},
        split_node_ids=set(),
    )
    assert segment.travel_direction == "reverse"
    assert segment.from_node_id == "osmn_3"
    assert segment.to_node_id == "osmn_1"
    assert segment.coordinates[0] == (4.2, 52.0)
    assert segment.coordinates[-1] == (4.0, 52.0)


def test_internal_segment_id_is_stable_across_osm_version_only_change():
    args = dict(
        way_id=123,
        node_ids=[1, 2],
        coordinates=[(5.0, 52.0), (5.01, 52.0)],
        tags={"highway": "motorway", "oneway": "yes", "maxspeed": "100"},
        split_node_ids=set(),
    )
    old = build_directed_segments(way_version=10, **args)[0]
    new = build_directed_segments(way_version=11, **args)[0]
    assert old.internal_segment_id == new.internal_segment_id

    changed = build_directed_segments(
        way_version=12,
        **{**args, "tags": {**args["tags"], "maxspeed": "80"}},
    )[0]
    assert changed.internal_segment_id != old.internal_segment_id
