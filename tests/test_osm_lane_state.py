from datetime import datetime, timedelta, timezone

from ndwinfo.osm.lane_state import build_lane_speed_states


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def observation(lane, speed, *, count=3, age_s=30, confidence=0.8):
    return {
        "lane": lane,
        "site_lane_count": count,
        "speed_kmh": speed,
        "measured_at": NOW - timedelta(seconds=age_s),
        "confidence": confidence,
    }


def test_lane_speed_state_retains_zero_and_aggregates_matching_lanes():
    states = build_lane_speed_states(
        3,
        [
            observation(1, 100),
            observation(2, 0),
            observation(2, 20),
            observation(3, 80),
        ],
        lane_order_verified=True,
        now=NOW,
    )

    assert [state["lane"] for state in states] == [1, 2, 3]
    assert states[1]["speed_kmh"] == 10.0
    assert states[1]["speed_sample_count"] == 2
    assert states[1]["speed_stale"] is False


def test_lane_state_rejects_lane_count_mismatch_and_out_of_range_lane():
    states = build_lane_speed_states(
        3,
        [observation(1, 90, count=4), observation(4, 70, count=3)],
        lane_order_verified=True,
        now=NOW,
    )

    assert states == []


def test_lane_state_preserves_stale_provenance_without_exposing_speed():
    states = build_lane_speed_states(
        2,
        [observation(1, 55, count=2, age_s=900)],
        lane_order_verified=True,
        now=NOW,
        stale_after_s=600,
    )

    assert states[0]["speed_kmh"] is None
    assert states[0]["speed_method"] == "unknown"
    assert states[0]["speed_stale"] is True
    assert states[0]["speed_observed_at"] is not None


def test_unknown_carriageway_count_never_claims_lane_identity():
    assert build_lane_speed_states(
        None, [observation(1, 90)], lane_order_verified=True, now=NOW
    ) == []


def test_equal_lane_count_without_verified_order_never_claims_lane_identity():
    assert build_lane_speed_states(3, [observation(1, 90)], now=NOW) == []


def test_invalid_speed_and_excessive_future_timestamp_are_not_fresh():
    states = build_lane_speed_states(
        2,
        [
            observation(1, -1, count=2),
            observation(1, 301, count=2),
            observation(2, 80, count=2, age_s=-120),
        ],
        lane_order_verified=True,
        now=NOW,
    )

    assert states[0]["speed_kmh"] is None
    assert states[1]["speed_kmh"] is None
    assert all(state["speed_stale"] for state in states)


def test_future_observation_does_not_extend_fresh_lane_speed_validity():
    states = build_lane_speed_states(
        2,
        [
            observation(1, 80, count=2, age_s=0),
            observation(1, 120, count=2, age_s=-300),
        ],
        lane_order_verified=True,
        now=NOW,
    )

    assert states[0]["speed_kmh"] == 80.0
    assert states[0]["speed_sample_count"] == 1
    assert states[0]["speed_observed_at"] == NOW.isoformat()
    assert states[0]["speed_valid_until"] == (NOW + timedelta(seconds=600)).isoformat()
