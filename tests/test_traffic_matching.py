from types import SimpleNamespace

from ndwinfo.api.deps import BBox
from ndwinfo.api.routers.traffic import (
    _attach_osm_matches,
    _normalized_road_ref,
    _osm_lane_speed_feature_collection,
    _osm_maxspeed_kmh,
    _pick_osm_candidate,
    _speed_location_key,
)


def osm_candidate(
    source_id,
    distance,
    bearing,
    *,
    ref="A9",
    lane_count=2,
    direction="fwd",
    connected_source_ids=None,
):
    return SimpleNamespace(
        source_id=source_id,
        distance_m=distance,
        bearing=bearing,
        ref=ref,
        lane_count=lane_count,
        direction=direction,
        connected_source_ids=connected_source_ids or [],
    )


def test_normalized_road_ref_preserves_a_n_distinction():
    assert _normalized_road_ref("A009") == "A9"
    assert _normalized_road_ref("N 203") == "N203"
    assert _normalized_road_ref("001") == "1"


def test_colocated_opposite_directions_do_not_share_an_aggregation_key():
    coords = (4.71055, 52.51824)
    positive = _speed_location_key(coords, "N203", None, "positive", None)
    same_direction = _speed_location_key(coords, "N203", None, "positive", None)
    negative = _speed_location_key(coords, "N203", None, "negative", None)

    assert positive == same_direction
    assert positive != negative


def test_osm_match_rejects_opposite_direction_and_conflicting_road():
    site = {"road_ref": "A9", "bearing": 20, "num_lanes": 2}
    candidates = [
        osm_candidate(1, 1, 200),
        osm_candidate(2, 2, 22, ref="A8"),
        osm_candidate(3, 8, 25),
    ]
    assert _pick_osm_candidate(site, candidates).source_id == 3


def test_osm_match_prefers_road_and_lane_count_before_distance():
    site = {"road_ref": "N203", "bearing": 90, "num_lanes": 2}
    candidates = [
        osm_candidate(1, 1, 90, ref=None, lane_count=2),
        osm_candidate(2, 5, 92, ref="N203", lane_count=1),
        osm_candidate(3, 10, 95, ref="N203", lane_count=2),
    ]
    assert _pick_osm_candidate(site, candidates).source_id == 3


def test_osm_match_allows_a_n_transition_and_prefers_it_over_missing_ref():
    site = {"road_ref": "N200", "bearing": 91.1, "num_lanes": 2}
    candidates = [
        osm_candidate(7400222, 9.9, 94.6, ref=None, lane_count=1),
        osm_candidate(561881901, 9.2, 93.2, ref="A200", lane_count=2),
    ]

    assert _pick_osm_candidate(site, candidates).source_id == 561881901


def test_osm_match_prefers_exact_prefix_over_a_n_transition():
    site = {"road_ref": "N208", "bearing": 20, "num_lanes": 2}
    candidates = [
        osm_candidate(1, 5, 20, ref="A208", lane_count=2),
        osm_candidate(2, 12, 21, ref="N208", lane_count=2),
    ]

    assert _pick_osm_candidate(site, candidates).source_id == 2


def test_close_reference_match_overrides_lane_count_mismatch():
    site = {"road_ref": "N200", "bearing": 355, "num_lanes": 3}
    candidates = [
        osm_candidate(7400311, 2.5, 350, ref="A200", lane_count=2),
        osm_candidate(490597686, 23.9, 356.4, ref="N200", lane_count=3),
    ]

    assert _pick_osm_candidate(site, candidates).source_id == 7400311


def test_close_override_requires_less_than_five_metres_and_close_bearing():
    site = {"road_ref": "A9", "bearing": 0, "num_lanes": 3}
    candidates = [
        osm_candidate(1, 5.0, 0, ref="A9", lane_count=2),
        osm_candidate(2, 2.0, 16, ref="A9", lane_count=2),
        osm_candidate(3, 10, 1, ref="A9", lane_count=3),
    ]

    assert _pick_osm_candidate(site, candidates).source_id == 3


def test_osm_match_returns_none_for_indistinguishable_candidates():
    site = {"road_ref": "A9", "bearing": 0, "num_lanes": 2}
    candidates = [
        osm_candidate(1, 5.0, 1),
        osm_candidate(2, 5.5, 2),
    ]
    assert _pick_osm_candidate(site, candidates) is None


def test_contiguous_osm_way_fragments_are_not_ambiguous():
    site = {"road_ref": "A9", "bearing": 0, "num_lanes": 2}
    candidates = [
        osm_candidate(1, 5.0, 1, connected_source_ids=[2]),
        osm_candidate(2, 5.5, 2, connected_source_ids=[1]),
    ]
    assert _pick_osm_candidate(site, candidates).source_id == 1


def test_n203_opposite_pair_selects_distinct_directed_osm_ways():
    candidates = [
        osm_candidate(100, 2.0, 255.9, ref="N203", lane_count=1, direction="fwd"),
        osm_candidate(101, 2.1, 75.5, ref="N203", lane_count=1, direction="bwd"),
    ]
    positive = {"road_ref": "N203", "bearing": 255.6, "num_lanes": 1}
    negative = {"road_ref": "N203", "bearing": 75.6, "num_lanes": 1}

    pos_match = _pick_osm_candidate(positive, candidates)
    neg_match = _pick_osm_candidate(negative, candidates)

    assert abs((positive["bearing"] - negative["bearing"] + 180) % 360 - 180) > 175
    assert (pos_match.source_id, pos_match.direction) == (100, "fwd")
    assert (neg_match.source_id, neg_match.direction) == (101, "bwd")


def test_lane_line_numbering_is_used_as_the_ndw_lane_number():
    """Both count 1 as the leftmost lane of the direction of travel.

    The lane-line graph numbers per direction and in travel order, so a
    backward lane needs no mirroring the way physical OSM ordering did.
    """
    result = _lane_feature_collection_for(
        lane_rows=[_lane_line_row(1, direction="bwd"), _lane_line_row(2, direction="bwd")],
        direction="bwd",
        lanes=[{"lane": 1, "speed_kmh": 88.0}, {"lane": 2, "speed_kmh": None}],
    )

    assert [feature["properties"]["lane"] for feature in result["features"]] == [1]
    assert result["features"][0]["properties"]["speed_kmh"] == 88.0


def test_osm_maxspeed_uses_directional_value_and_converts_mph():
    tags = {
        "maxspeed": "80",
        "maxspeed:forward": "100",
        "maxspeed:backward": "50 mph",
    }

    assert _osm_maxspeed_kmh(tags, "fwd") == 100
    assert _osm_maxspeed_kmh(tags, "bwd") == 80.5
    assert _osm_maxspeed_kmh({"maxspeed": "signals"}, "fwd") is None


def test_osm_maxspeed_oneway_minus_one_uses_backward_value():
    tags = {"oneway": "-1", "maxspeed": "80", "maxspeed:backward": "60"}
    assert _osm_maxspeed_kmh(tags, "fwd") == 60


def _lane_line_row(lane_nr, *, direction="fwd"):
    """One osm_lane_centerline row as the speed query selects it."""
    return SimpleNamespace(
        id=f"ll:42:10:11:{direction}:{lane_nr}",
        source_id=42,
        lane_nr=lane_nr,
        lane_count=2,
        direction=direction,
        highway="primary",
        osm_tags={
            "maxspeed": "80",
            "carriageway_ref": "d",
            "name": "Provincialeweg",
            "ref": "N203",
        },
        geom_json='{"type":"LineString","coordinates":[[4.70,52.51],[4.71,52.52]]}',
    )


def _lane_feature_collection_for(*, lane_rows, direction, lanes):
    class Result:
        def all(self):
            return lane_rows

    class Db:
        def execute(self, *_args, **_kwargs):
            return Result()

    points = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [4.705, 52.515]},
        "properties": {
            "site_id": "sensor-1",
            "osm_source_id": 42,
            "osm_direction": direction,
            "lanes": lanes,
        },
    }]
    return _osm_lane_speed_feature_collection(
        Db(), points, BBox(4.70, 52.51, 4.72, 52.53)
    )


def test_osm_lane_output_omits_missing_speed_and_includes_maxspeed():
    result = _lane_feature_collection_for(
        lane_rows=[_lane_line_row(1), _lane_line_row(2)],
        direction="fwd",
        lanes=[{"lane": 1, "speed_kmh": None}, {"lane": 2, "speed_kmh": 72.0}],
    )

    assert [feature["properties"]["lane"] for feature in result["features"]] == [2]
    assert result["features"][0]["properties"]["width_m"] == 3.5
    assert result["features"][0]["properties"]["name"] == "Provincialeweg"
    assert result["features"][0]["properties"]["carriageway_ref"] == "d"
    assert result["features"][0]["properties"]["maxspeed_kmh"] == 80


def test_osm_attachment_exposes_replacement_api_contract():
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [4.71055, 52.51824]},
        "properties": {"road": "N203", "bearing": 255.6, "num_lanes": 1},
    }
    candidate = SimpleNamespace(
        i=0,
        source_id=565536411,
        lane_count=1,
        ref="N203",
        name="Provincialeweg",
        direction="fwd",
        highway="primary",
        osm_tags={"maxspeed": "80", "carriageway_ref": "Li"},
        connected_source_ids=[],
        distance_m=3.6,
        bearing=261.3,
    )

    class Result:
        def all(self):
            return [candidate]

    class Db:
        def execute(self, *_args, **_kwargs):
            return Result()

    _attach_osm_matches(Db(), [feature])
    props = feature["properties"]

    assert props["osm_source_id"] == 565536411
    assert props["osm_direction"] == "fwd"
    assert props["osm_lane_count"] == 1
    assert props["osm_match_method"] == "vild_bearing"
    assert props["osm_highway"] == "primary"
    assert props["osm_bearing"] == 261.3
    assert props["osm_ref"] == "N203"
    assert props["osm_name"] == "Provincialeweg"
    assert props["osm_carriageway_ref"] == "Li"
    assert props["maxspeed_kmh"] == 80
    assert not any(key.startswith("weggeg_") for key in props)
