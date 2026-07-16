from datetime import datetime, timedelta, timezone

import pytest

from ndwinfo.osm.speed_model import (
    SpeedObservation,
    SpeedSegment,
    assign_speed_states,
)

NOW = datetime(2026, 7, 16, 12, tzinfo=timezone.utc)


def segment(
    segment_id,
    *,
    previous=(),
    following=(),
    road="A4",
    side="R",
    direction="forward",
    length=100,
):
    return SpeedSegment(
        segment_id, length, road, side, direction, tuple(previous), tuple(following)
    )


def observation(
    segment_id,
    speed,
    *,
    offset=50,
    age_s=30,
    confidence=0.9,
    status="accepted",
    source_id=None,
):
    return SpeedObservation(
        source_id or f"site-{segment_id}", segment_id, offset, speed,
        NOW - timedelta(seconds=age_s), confidence, binding_status=status,
    )


def linear_segments(count=5):
    return [
        segment(
            f"s{index}",
            previous=(() if index == 0 else (f"s{index - 1}",)),
            following=(() if index == count - 1 else (f"s{index + 1}",)),
        )
        for index in range(count)
    ]


def test_direct_measurement_preserves_zero_and_contract_metadata():
    state = assign_speed_states(
        [segment("s0")], [observation("s0", 0)], now=NOW
    )["s0"]
    assert state == {
        "speed_kmh": 0.0,
        "speed_method": "measured",
        "speed_confidence": 0.9,
        "speed_source": "NDW",
        "speed_source_ids": ["site-s0"],
        "speed_observed_at": "2026-07-16T11:59:30+00:00",
        "speed_valid_until": "2026-07-16T12:09:30+00:00",
        "speed_sample_count": 1,
        "speed_stale": False,
    }


def test_interpolates_by_distance_between_fresh_compatible_measurements():
    states = assign_speed_states(
        linear_segments(),
        [observation("s0", 100), observation("s4", 60)],
        now=NOW,
    )
    assert [states[f"s{i}"]["speed_method"] for i in range(5)] == [
        "measured", "interpolated", "interpolated", "interpolated", "measured"
    ]
    assert states["s2"]["speed_kmh"] == 80.0
    assert states["s2"]["speed_confidence"] == 0.765
    assert states["s2"]["speed_sample_count"] == 2


def test_one_sided_propagation_is_distance_limited():
    states = assign_speed_states(
        linear_segments(5), [observation("s0", 88)], now=NOW, propagation_limit_m=250
    )
    assert states["s1"]["speed_method"] == "propagated"
    assert states["s2"]["speed_method"] == "propagated"
    assert states["s3"]["speed_method"] == "unknown"
    assert states["s4"]["speed_method"] == "unknown"


@pytest.mark.parametrize(
    "changed",
    [
        {"road": "A5"},
        {"side": "L"},
        {"direction": "reverse"},
    ],
)
def test_identity_change_stops_propagation(changed):
    first = segment("s0", following=("s1",))
    second = segment("s1", previous=("s0",), **changed)
    states = assign_speed_states([first, second], [observation("s0", 90)], now=NOW)
    assert states["s1"]["speed_method"] == "unknown"


def test_non_unique_fork_and_merge_stop_assignment():
    segments = [
        segment("approach", following=("main", "exit")),
        segment("main", previous=("approach",)),
        segment("exit", previous=("approach",)),
    ]
    states = assign_speed_states(segments, [observation("approach", 75)], now=NOW)
    assert states["main"]["speed_kmh"] is None
    assert states["exit"]["speed_kmh"] is None

    merge = [
        segment("a", following=("after",)),
        segment("b", following=("after",)),
        segment("after", previous=("a", "b")),
    ]
    states = assign_speed_states(merge, [observation("a", 70)], now=NOW)
    assert states["after"]["speed_kmh"] is None


def test_missing_connected_segment_is_fail_closed():
    states = assign_speed_states(
        [segment("s0", following=("not-in-corridor",))],
        [observation("s0", 80)],
        now=NOW,
    )
    assert list(states) == ["s0"]


def test_stale_rejected_future_and_outlier_observations_never_propagate():
    segments = linear_segments(2)
    invalid = [
        observation("s0", 80, age_s=601),
        observation("s0", 80, age_s=-31),
        observation("s0", 301),
        observation("s0", 80, offset=101),
        observation("s0", 80, status="ambiguous"),
    ]
    states = assign_speed_states(segments, invalid, now=NOW)
    assert states["s0"]["speed_kmh"] is None
    assert states["s0"]["speed_stale"] is True
    assert states["s1"]["speed_kmh"] is None


def test_large_two_sided_gap_is_not_filled_by_one_sided_propagation():
    segments = linear_segments(5)
    states = assign_speed_states(
        segments,
        [observation("s0", 100), observation("s4", 50)],
        now=NOW,
        propagation_limit_m=1000,
        interpolation_limit_m=200,
    )
    assert all(states[f"s{i}"]["speed_method"] == "unknown" for i in (1, 2, 3))


def test_cycle_is_not_linearized_for_propagation():
    segments = [
        segment("a", previous=("c",), following=("b",)),
        segment("b", previous=("a",), following=("c",)),
        segment("c", previous=("b",), following=("a",)),
    ]
    states = assign_speed_states(segments, [observation("a", 65)], now=NOW)
    assert states["a"]["speed_method"] == "measured"
    assert states["b"]["speed_method"] == "unknown"
    assert states["c"]["speed_method"] == "unknown"


def test_missing_road_identity_never_crosses_segment_boundary():
    segments = [
        segment("a", following=("b",), road=None),
        segment("b", previous=("a",), road=None),
    ]
    states = assign_speed_states(segments, [observation("a", 65)], now=NOW)
    assert states["b"]["speed_method"] == "unknown"


def test_validation_rejects_duplicate_or_invalid_segments():
    with pytest.raises(ValueError):
        assign_speed_states([segment("same"), segment("same")], [], now=NOW)
    with pytest.raises(ValueError):
        assign_speed_states([segment("bad", length=0)], [], now=NOW)
