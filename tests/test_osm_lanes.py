import json
from types import SimpleNamespace

from ndwinfo.api.routers import roads
from ndwinfo.osm.graph import build_directed_segments
from ndwinfo.osm.lanes import build_lane_schema, safe_lane_transition
from ndwinfo.osm.tags import normalize_way_tags


def test_realistic_a4_lane_roles_require_explicit_tags():
    untagged = normalize_way_tags(
        {"highway": "motorway", "oneway": "yes", "lanes": "5", "ref": "A4"},
        "forward",
    )["lane_schema"]
    assert untagged["roles"] == ["unknown"] * 5
    assert untagged["attributes"] == {}

    tagged = normalize_way_tags(
        {
            "highway": "motorway",
            "oneway": "yes",
            "lanes": "5",
            "ref": "A4",
            "turn:lanes": (
                "through|through|through|through;slight_right|slight_right"
            ),
            "destination:lanes": (
                "Amsterdam;Schiphol|Amsterdam;Schiphol|Amsterdam;Schiphol|"
                "Amsterdam;Schiphol|Zaanstad;Haarlem"
            ),
        },
        "forward",
    )["lane_schema"]

    assert tagged["lane_order"] == "left_to_right"
    assert tagged["roles"] == ["through", "through", "through", "unknown", "exit"]
    assert tagged["attributes"]["turn"][3:] == [
        "through;slight_right",
        "slight_right",
    ]
    assert tagged["attributes"]["destination"][4] == "Zaanstad;Haarlem"


def test_directional_arrays_do_not_leak_to_opposite_carriageway():
    tags = {
        "highway": "primary",
        "lanes": "4",
        "lanes:forward": "2",
        "lanes:backward": "2",
        "turn:lanes:forward": "through|right",
        "turn:lanes:backward": "left|through",
        # Unsafe on a two-way road and deliberately ignored.
        "change:lanes": "yes|no|no|yes",
    }
    forward = normalize_way_tags(tags, "forward")["lane_schema"]
    backward = normalize_way_tags(tags, "backward")["lane_schema"]

    assert forward["attributes"]["turn"] == ["through", "right"]
    assert backward["attributes"]["turn"] == ["left", "through"]
    assert "change" in forward["unknown"]
    assert "change" in backward["unknown"]


def test_reverse_oneway_uses_backward_lane_array_in_its_declared_order():
    schema = normalize_way_tags(
        {
            "highway": "primary",
            "oneway": "-1",
            "lanes": "2",
            "turn:lanes:backward": "left|through",
        },
        "reverse",
    )["lane_schema"]

    # OSM lane arrays are already left-to-right in their applicable travel
    # direction. Reversing the way geometry must not reverse the token array.
    assert schema["attributes"]["turn"] == ["left", "through"]


def test_empty_lane_token_is_preserved_as_unknown_without_shifting_neighbors():
    schema = build_lane_schema(
        {"turn:lanes": "through||right"},
        "forward",
        lane_count=3,
        oneway="forward",
        highway="motorway",
    )

    assert schema["attributes"]["turn"] == ["through", None, "right"]
    assert schema["roles"] == ["through", "unknown", "unknown"]


def test_unequal_pipe_arrays_remain_unknown_without_padding_or_truncation():
    schema = build_lane_schema(
        {
            "turn:lanes": "through|through|right",
            "maxspeed:lanes": "100|80",
            "access:lanes": "yes|yes|no|no",
        },
        "forward",
        lane_count=3,
        oneway="forward",
        highway="motorway",
    )

    assert schema["attributes"] == {"turn": ["through", "through", "right"]}
    assert "maxspeed" in schema["unknown"]
    assert "access" in schema["unknown"]
    assert schema["roles"] == ["through", "through", "unknown"]


def test_motor_vehicle_lane_access_overrides_generic_access():
    schema = build_lane_schema(
        {
            "motor_vehicle:lanes": "yes|no",
            "vehicle:lanes": "yes|yes",
            "access:lanes": "yes|yes",
        },
        "forward",
        lane_count=2,
        oneway="forward",
        highway="primary",
    )

    assert schema["attributes"]["access"] == ["yes", "no"]


def test_unknown_lane_count_cannot_assign_even_well_formed_values():
    schema = build_lane_schema(
        {"turn:lanes": "through|right"},
        "forward",
        lane_count=None,
        oneway="forward",
        highway="motorway",
    )
    assert schema["lane_count"] is None
    assert schema["attributes"] == {}
    assert "lanes" in schema["unknown"]
    assert "turn" in schema["unknown"]


def test_lane_transition_only_maps_equal_counts_with_safe_continuity():
    two = build_lane_schema({}, "forward", lane_count=2, oneway="forward")
    three = build_lane_schema({}, "forward", lane_count=3, oneway="forward")

    assert safe_lane_transition(two, two, connected=True, same_travel_direction=True) is None
    assert (
        safe_lane_transition(
            two,
            two,
            connected=False,
            same_travel_direction=True,
            same_osm_way=True,
        )
        is None
    )
    assert (
        safe_lane_transition(
            two,
            three,
            connected=True,
            same_travel_direction=True,
            same_osm_way=True,
        )
        is None
    )
    assert (
        safe_lane_transition(
            two,
            two,
            connected=True,
            same_travel_direction=False,
            same_osm_way=True,
        )
        is None
    )
    assert safe_lane_transition(
        two,
        two,
        connected=True,
        same_travel_direction=True,
        same_osm_way=True,
    ) == (
        (1, 1),
        (2, 2),
    )


def test_implausible_lane_count_is_capped_without_large_role_arrays():
    schema = build_lane_schema(
        {"turn:lanes": "through|right"},
        "forward",
        lane_count=10_000,
        oneway="forward",
        highway="motorway",
    )
    assert schema["lane_count"] is None
    assert "lanes" in schema["unknown"]
    assert "roles" not in schema


def test_motorway_link_turn_is_not_promoted_to_exit_role():
    schema = build_lane_schema(
        {
            "turn:lanes": "slight_right",
            "destination:lanes": "Haarlem",
        },
        "forward",
        lane_count=1,
        oneway="forward",
        highway="motorway_link",
    )
    assert schema["roles"] == ["unknown"]


def test_explicit_lane_attribute_change_changes_segment_identity():
    arguments = {
        "way_id": 42,
        "way_version": 1,
        "node_ids": [1, 2],
        "coordinates": [(4.0, 52.0), (4.01, 52.0)],
        "split_node_ids": set(),
    }
    base_tags = {
        "highway": "motorway",
        "oneway": "yes",
        "lanes": "2",
        "turn:lanes": "through|through",
    }
    base = build_directed_segments(tags=base_tags, **arguments)[0]
    changed = build_directed_segments(
        tags={**base_tags, "turn:lanes": "through|slight_right"},
        **arguments,
    )[0]

    assert base.normalized_tags["lane_schema"]["roles"] == ["through", "through"]
    assert changed.internal_segment_id != base.internal_segment_id


def test_pre_migration_segment_falls_back_to_retained_raw_tags():
    row = SimpleNamespace(
        lane_schema=None,
        tags={
            "lanes": "2",
            "turn:lanes": "through|slight_right",
            "destination:lanes": "Amsterdam|Haarlem",
        },
        travel_direction="forward",
        lanes=2,
        oneway="forward",
        highway="motorway",
    )
    schema = roads._lane_schema(row)
    assert schema["attributes"]["turn"] == ["through", "slight_right"]
    assert schema["roles"] == ["through", "exit"]


def test_roads_response_exposes_lane_schema_without_raw_tags(monkeypatch):
    monkeypatch.setattr(roads, "load_direct_speed_states", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        roads,
        "assign_corridor_speed_states",
        lambda _db, _graph, rows, _direct: {
            row.internal_segment_id: roads.unknown_speed_state() for row in rows
        },
    )
    monkeypatch.setattr(
        roads,
        "load_live_segment_facts",
        lambda _db, _version, ids: {
            segment_id: {"matrix": [], "drips": []} for segment_id in ids
        },
    )
    row = SimpleNamespace(
        internal_segment_id="segment-1",
        graph_version="graph-v1",
        osm_way_id=42,
        from_node_id="a",
        to_node_id="b",
        travel_direction="forward",
        highway="motorway",
        road_number="A4",
        name=None,
        oneway="forward",
        junction=None,
        carriageway_ref=None,
        lanes=2,
        lane_schema=None,
        maxspeed="100",
        access=None,
        bridge=None,
        tunnel=None,
        layer=None,
        length_m=100.0,
        tags={"lanes": "2", "turn:lanes": "through|slight_right"},
        geom_json='{"type":"LineString","coordinates":[[4,52],[4.1,52.1]]}',
    )
    graph = SimpleNamespace(graph_version="graph-v1", source_timestamp=None)

    response = roads._road_response(None, graph, [row], False, "test")
    properties = json.loads(response.body)["features"][0]["properties"]

    assert properties["lane_schema"]["lane_count"] == 2
    assert properties["lane_schema"]["lane_order"] == "left_to_right"
    assert "tags" not in properties
