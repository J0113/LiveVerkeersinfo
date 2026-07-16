from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ndwinfo.api.routers.traffic import (
    _attach_fallback_bearings,
    _binding_summary,
    _canonical_lane_activation,
    _canonical_weggeg_candidates,
    _comparable_carriageways_conflict,
    _merge_lane,
    _normalized_road_number,
    _osm_lane_fallback_features,
    _pick_near_candidate,
    _pick_wide_candidate,
    _weggeg_stable_lane_row,
)


def test_zero_speed_is_a_valid_latest_observation():
    ts = datetime(2026, 7, 15, tzinfo=timezone.utc)
    result = _merge_lane([
        {"speed": 0.0, "flow": 0.0, "n_inputs": 8, "std_dev": 0.0, "ts": ts}
    ])

    assert result["speed_kmh"] == 0.0


def test_normalized_road_number_matches_weggeg_format():
    assert _normalized_road_number("A1") == "001"
    assert _normalized_road_number("N 001") == "001"
    assert _normalized_road_number("A76a") == "076"
    assert _normalized_road_number("001") == "001"
    assert _normalized_road_number(None) is None


def candidate(
    source_id,
    distance,
    *,
    road=False,
    carriageway=False,
    lanes=False,
    bearing=90.0,
    road_number="203",
    carriageway_side=None,
):
    return SimpleNamespace(
        source_id=source_id,
        distance_m=distance,
        road_match=road,
        carriageway_match=carriageway,
        lane_count_match=lanes,
        bearing=bearing,
        road_number=road_number,
        carriageway_side=carriageway_side,
    )


def test_near_match_prioritizes_road_then_carriageway_before_distance():
    candidates = [
        candidate("closest-wrong-road", 0.1, carriageway=True),
        candidate("same-road", 0.8, road=True),
        candidate("same-road-and-side", 2.4, road=True, carriageway=True),
    ]

    assert _pick_near_candidate(candidates).source_id == "same-road-and-side"


def test_near_match_uses_distance_after_metadata_ties():
    candidates = [
        candidate("farther", 2.0, road=True, carriageway=True),
        candidate("closer", 0.5, road=True, carriageway=True),
    ]

    assert _pick_near_candidate(candidates).source_id == "closer"


def test_near_match_prefers_matching_lane_count_before_distance():
    candidates = [
        candidate("closer-wrong-count", 0.4, road=True, carriageway=True),
        candidate("farther-right-count", 1.8, road=True, carriageway=True, lanes=True),
    ]
    assert _pick_near_candidate(candidates).source_id == "farther-right-count"


def test_wide_match_prioritizes_carriageway_then_lane_count():
    candidates = [
        candidate("closest-opposite", 3.0, road=True, lanes=True),
        candidate("same-side-wrong-lanes", 20.0, road=True, carriageway=True),
        candidate("same-side-and-lanes", 24.0, road=True, carriageway=True, lanes=True),
    ]

    assert _pick_wide_candidate(candidates).source_id == "same-side-and-lanes"


def binding(status, segment=None, confidence=0.0, direction_source=None):
    return SimpleNamespace(
        status=status,
        internal_segment_id=segment,
        confidence=confidence,
        direction_source=direction_source,
    )


def test_point_binding_summary_requires_one_unambiguous_segment():
    accepted = _binding_summary([
        binding("accepted", "segment-a", 0.91),
        binding("accepted", "segment-a", 0.82),
    ])
    conflicting = _binding_summary([
        binding("accepted", "segment-a", 0.91),
        binding("accepted", "segment-b", 0.88),
    ])

    assert accepted == {
        "binding_status": "accepted",
        "internal_segment_id": "segment-a",
        "binding_confidence": 0.82,
    }
    assert conflicting["binding_status"] == "ambiguous"
    assert conflicting["internal_segment_id"] is None


def test_point_binding_summary_distinguishes_rejected_and_unmatched():
    assert _binding_summary([binding("rejected")])["binding_status"] == "rejected"
    assert _binding_summary([])["binding_status"] == "unmatched"


def test_point_binding_summary_exposes_vild_direction_provenance():
    summary = _binding_summary([
        binding("accepted", "segment-a", 0.9, direction_source="vild")
    ])

    assert summary["binding_direction_source"] == "vild"


def test_point_binding_summary_fails_closed_for_partial_merged_marker():
    summary = _binding_summary(
        [binding("accepted", "segment-a", 0.9)], expected_count=2
    )

    assert summary["binding_status"] == "ambiguous"
    assert summary["internal_segment_id"] is None


def test_bearing_fallback_never_invents_direction_from_nearest_line():
    features = [
        {"properties": {"openlr_bearing": 361}, "geometry": None},
        {"properties": {"openlr_bearing": None}, "geometry": None},
        {
            "properties": {
                "openlr_bearing": None,
                "canonical_bearing": 295.7,
                "tmc_direction": "negative",
                "binding_direction_source": "vild",
            },
            "geometry": None,
        },
    ]

    _attach_fallback_bearings(None, features)

    assert features[0]["properties"]["bearing"] == 1
    assert features[0]["properties"]["bearing_source"] == "openlr"
    assert "bearing" not in features[1]["properties"]
    assert "bearing_source" not in features[1]["properties"]
    assert features[2]["properties"]["bearing"] == 295.7
    assert features[2]["properties"]["bearing_source"] == "vild_bound_osm"


def test_lane_activation_requires_fresh_accepted_canonical_binding():
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    accepted = {
        "binding_status": "accepted",
        "internal_segment_id": "osm:203:f:0",
        "measurement_stale": False,
        "measured_at": now.isoformat(),
    }

    assert _canonical_lane_activation(accepted, now=now)
    for key, value in (
        ("binding_status", "ambiguous"),
        ("binding_status", "rejected"),
        ("internal_segment_id", None),
        ("measurement_stale", True),
    ):
        assert not _canonical_lane_activation(accepted | {key: value}, now=now)

    future = accepted | {"measured_at": (now + timedelta(seconds=31)).isoformat()}
    assert not _canonical_lane_activation(future, now=now)


def test_weggeg_geometry_must_agree_with_osm_road_and_direction():
    feature = {
        "properties": {
            "canonical_bearing": 90.0,
            "canonical_road_number": "N203",
            "canonical_carriageway": "R",
        }
    }
    aligned = candidate("aligned", 1.0, bearing=94, carriageway_side="R")
    opposite = candidate("opposite", 1.0, bearing=274, carriageway_side="R")
    wrong_road = candidate(
        "wrong-road", 1.0, bearing=94, road_number="204", carriageway_side="R"
    )
    wrong_side = candidate("wrong-side", 1.0, bearing=94, carriageway_side="L")

    assert _canonical_weggeg_candidates(
        feature, [aligned, opposite, wrong_road, wrong_side]
    ) == [aligned]


def test_weggeg_geometry_requires_an_osm_tangent():
    feature = {"properties": {"canonical_road_number": "N203"}}
    assert _canonical_weggeg_candidates(feature, [candidate("lane", 1.0)]) == []


def test_unrelated_carriageway_vocabularies_are_not_a_false_conflict():
    assert _comparable_carriageways_conflict("R", "L")
    assert not _comparable_carriageways_conflict("HR", "R")
    assert not _comparable_carriageways_conflict(None, "R")


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FallbackDb:
    def __init__(self, rows):
        self.rows = rows

    def scalar(self, _statement):
        return SimpleNamespace(id=1)

    def execute(self, _statement):
        return _Rows(self.rows)


def test_osm_lane_fallback_requires_equal_explicit_lane_counts():
    now = datetime.now(timezone.utc)
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [4.71, 52.51]},
        "properties": {
            "site_id": "sensor-a",
            "binding_status": "accepted",
            "binding_confidence": 0.82,
            "internal_segment_id": "segment-a",
            "measurement_stale": False,
            "measured_at": now.isoformat(),
            "num_lanes": 2,
            "lanes": [
                {"lane": 1, "speed_kmh": 42.0, "flow_veh_h": 480},
                {"lane": 2, "speed_kmh": 51.0, "flow_veh_h": 520},
            ],
        },
    }
    row = SimpleNamespace(
        internal_segment_id="segment-a",
        road_number="N203",
        carriageway_ref=None,
        lanes=2,
        geom_json='{"type":"LineString","coordinates":[[4.7,52.51],[4.72,52.51]]}',
    )

    features = _osm_lane_fallback_features(
        _FallbackDb([row]),
        [point],
        None,
        excluded_lane_keys=set(),
        remaining_limit=20,
    )
    assert [feature["properties"]["lane"] for feature in features] == [1, 2]
    assert all(
        feature["properties"]["geometry_source"] == "osm_schematic"
        and feature["properties"]["road_authority"] == "osm"
        for feature in features
    )

    mismatch = {**point, "properties": {**point["properties"], "num_lanes": 3}}
    assert _osm_lane_fallback_features(
        _FallbackDb([row]),
        [mismatch],
        None,
        excluded_lane_keys=set(),
        remaining_limit=20,
    ) == []


def test_weggeg_transition_rows_cannot_claim_full_length_lane_geometry():
    stable = SimpleNamespace(raw={
        "lane_transition": {"travel": [2, 2], "lane_presence": "both"}
    })
    transition = SimpleNamespace(raw={
        "lane_transition": {"travel": [2, 3], "lane_presence": "source_end"}
    })
    legacy_stable = SimpleNamespace(raw={"OMSCHR": "2 -> 2"})
    legacy_transition = SimpleNamespace(raw={"OMSCHR": "2 -> 3"})
    assert _weggeg_stable_lane_row(stable)
    assert not _weggeg_stable_lane_row(transition)
    assert _weggeg_stable_lane_row(legacy_stable)
    assert not _weggeg_stable_lane_row(legacy_transition)


def test_osm_fallback_fills_only_lane_missing_from_weggeg():
    now = datetime.now(timezone.utc)
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [4.71, 52.51]},
        "properties": {
            "site_id": "sensor-a",
            "binding_status": "accepted",
            "binding_confidence": 0.82,
            "internal_segment_id": "segment-a",
            "measurement_stale": False,
            "measured_at": now.isoformat(),
            "num_lanes": 2,
            "lanes": [
                {"lane": 1, "speed_kmh": 80.0},
                {"lane": 2, "speed_kmh": 70.0},
            ],
        },
    }
    row = SimpleNamespace(
        internal_segment_id="segment-a",
        road_number="N203",
        carriageway_ref=None,
        lanes=2,
        geom_json='{"type":"LineString","coordinates":[[4.7,52.51],[4.72,52.51]]}',
    )
    features = _osm_lane_fallback_features(
        _FallbackDb([row]), [point], None,
        excluded_lane_keys={("segment-a", 1)}, remaining_limit=20,
    )
    assert [feature["properties"]["lane"] for feature in features] == [2]
