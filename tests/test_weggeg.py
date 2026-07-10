"""Focused tests for official lane configuration and NDW matching."""

from __future__ import annotations

from datetime import datetime, timezone

from ndwinfo.weggeg import (
    attach_nwb_metadata,
    build_lane_speed_features,
    transform_lane_configuration,
)


def _configuration(description: str = "2 -> 2"):
    return {
        "type": "Feature",
        "id": "weggeg-one",
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [[[4.0000, 52.0000], [4.0100, 52.0000]]],
        },
        "properties": {
            "wvk_id": 42,
            "omschr": description,
            "izi_side": "R",
            "kantcode": "R",
            "begafstand": 0,
            "endafstand": 700,
            "wvk_begdat": "2026-01-01T00:00:00Z",
        },
    }


def _observation(*, road="A4", carriageway="R", bearing=90, measured_at="2026-07-10T10:00:00Z"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [4.005, 52.0001]},
        "properties": {
            "site_id": "NDW_MONICA_1",
            "road": road,
            "carriageway": carriageway,
            "bearing": bearing,
            "measured_at": measured_at,
            "lanes": [
                {"lane": 1, "speed_kmh": 42.0, "flow_veh_h": 900, "n_inputs": 3},
                {"lane": 2, "speed_kmh": 86.0, "flow_veh_h": 700, "n_inputs": 2},
            ],
        },
    }


def _prepared_configuration(description="2 -> 2"):
    config = transform_lane_configuration(_configuration(description))
    assert config is not None
    attach_nwb_metadata(
        [config],
        [
            {
                "properties": {
                    "nwb_road_section_id": 42,
                    "road_number": "A4",
                    "carriageway_position": "R",
                }
            }
        ],
    )
    return config


def test_lane_configuration_parses_variable_lane_count_without_inventing_geometry():
    config = _prepared_configuration("3 -> 4")
    assert config["properties"]["lane_count_start"] == 3
    assert config["properties"]["lane_count_end"] == 4
    assert config["properties"]["lane_count"] == 4
    assert config["properties"]["lane_count_variable"] is True
    assert config["geometry"] == _configuration("3 -> 4")["geometry"]


def test_invalid_lane_description_is_rejected():
    assert transform_lane_configuration(_configuration("unknown")) is None


def test_matching_expands_lanes_and_preserves_lane_one_as_leftmost():
    features = build_lane_speed_features(
        [_prepared_configuration()],
        [_observation()],
        now=datetime(2026, 7, 10, 10, 1, tzinfo=timezone.utc),
    )
    assert [f["properties"]["speed_kmh"] for f in features] == [42.0, 86.0]
    assert [f["properties"]["lane_offset_index"] for f in features] == [-0.5, 0.5]
    assert all(f["properties"]["geometry_kind"] == "schematic-lane-offset" for f in features)
    assert features[0]["properties"]["match_confidence"] == "high"


def test_wrong_road_carriageway_or_heading_is_not_matched():
    config = _prepared_configuration()
    observations = [
        _observation(road="A5"),
        _observation(carriageway="L"),
        _observation(bearing=270),
    ]
    features = build_lane_speed_features(
        [config],
        observations,
        now=datetime(2026, 7, 10, 10, 1, tzinfo=timezone.utc),
    )
    assert all(f["properties"]["speed_kmh"] is None for f in features)


def test_stale_measurement_is_not_coloured():
    features = build_lane_speed_features(
        [_prepared_configuration()],
        [_observation(measured_at="2026-07-10T09:00:00Z")],
        max_age_s=600,
        now=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
    )
    assert all(f["properties"]["speed_kmh"] is None for f in features)


def test_multiple_observations_use_input_weighted_average():
    first = _observation()
    second = _observation()
    first["properties"]["lanes"] = [{"lane": 1, "speed_kmh": 40, "n_inputs": 3}]
    second["properties"]["lanes"] = [{"lane": 1, "speed_kmh": 80, "n_inputs": 1}]
    features = build_lane_speed_features(
        [_prepared_configuration()],
        [first, second],
        now=datetime(2026, 7, 10, 10, 1, tzinfo=timezone.utc),
    )
    assert features[0]["properties"]["speed_kmh"] == 50.0
    assert features[0]["properties"]["input_count"] == 4
