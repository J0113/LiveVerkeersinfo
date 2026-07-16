from datetime import datetime, timedelta, timezone

import pytest

from ndwinfo.matching.live_objects import (
    LiveObjectTraits,
    LiveRoadCandidate,
    assess_drip_path_relevance,
    decide_live_object_binding,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def source(source_type="msi", **kwargs):
    values = {
        "source_type": source_type,
        "source_id": "object-1",
        "road": "A4",
        "carriageway": "R",
        "bearing": 2,
        "observed_at": NOW - timedelta(seconds=30),
        "ingested_at": NOW - timedelta(seconds=10),
        "provenance": {"feed": "ndw", "event_id": "event-7"},
    }
    values.update(kwargs)
    return LiveObjectTraits(**values)


def candidate(segment_id="segment-1", **kwargs):
    values = {
        "internal_segment_id": segment_id,
        "road_number": "A4",
        "carriageway_ref": "R",
        "lanes": 3,
        "distance_m": 4,
        "bearing": 0,
        "highway": "motorway",
    }
    values.update(kwargs)
    return LiveRoadCandidate(**values)


def decide(obj, candidates, **kwargs):
    return decide_live_object_binding(
        obj,
        candidates,
        reference_time=NOW,
        stale_after_s=120,
        **kwargs,
    )


def test_accepts_clear_directional_msi_and_preserves_state_provenance():
    result = decide(source(lane=2), [candidate()], lane_order_verified=True)

    assert result.status == "accepted"
    assert result.internal_segment_id == "segment-1"
    assert result.heading_delta_deg == 2
    assert result.source_lane == 2
    assert result.canonical_lane == 2
    assert result.lane_scope_status == "canonical"
    assert result.provenance == {"feed": "ndw", "event_id": "event-7"}
    assert result.observed_at == NOW - timedelta(seconds=30)
    assert result.ingested_at == NOW - timedelta(seconds=10)
    assert result.valid_until == NOW + timedelta(seconds=90)
    assert result.stale is False
    assert result.state_usable is True


def test_missing_source_bearing_never_yields_directional_binding():
    result = decide(source(bearing=None), [candidate(distance_m=0.1)])

    assert result.status == "rejected"
    assert result.internal_segment_id is None


def test_unknown_explicit_carriageway_label_fails_closed():
    result = decide(
        source(carriageway="baan-onbekend"),
        [candidate(carriageway_ref="R")],
    )

    assert result.status == "rejected"
    assert result.internal_segment_id is None


def test_specific_dvk_letter_requires_exact_osm_carriageway_reference():
    matching = decide(
        source(carriageway="m"),
        [candidate(carriageway_ref="M")],
    )
    missing = decide(
        source(carriageway="m"),
        [candidate(carriageway_ref=None)],
    )

    assert matching.status == "accepted"
    assert missing.status == "rejected"


@pytest.mark.parametrize(
    "candidate_overrides",
    [
        {"road_number": "A5"},
        {"carriageway_ref": "L"},
        {"bearing": 180},
        {"distance_m": 81},
    ],
)
def test_explicit_conflicts_and_out_of_bounds_candidates_are_rejected(candidate_overrides):
    result = decide(source(), [candidate(**candidate_overrides)], max_distance_m=80)

    assert result.status == "rejected"
    assert result.internal_segment_id is None


def test_close_parallel_candidates_without_clear_margin_stay_ambiguous():
    result = decide(
        source(carriageway=None),
        [
            candidate("one", carriageway_ref=None, distance_m=4),
            candidate("two", carriageway_ref=None, distance_m=5),
        ],
    )

    assert result.status == "ambiguous"
    assert result.internal_segment_id is None
    assert result.margin == 1


def test_msi_source_lane_is_not_canonical_until_order_is_verified():
    result = decide(source(lane=2), [candidate()], lane_order_verified=False)

    assert result.status == "accepted"
    assert result.source_lane == 2
    assert result.canonical_lane is None
    assert result.lane_scope_status == "source_only"


def test_msi_lane_outside_known_carriageway_count_rejects_candidate():
    result = decide(source(lane=4), [candidate(lanes=3)], lane_order_verified=True)

    assert result.status == "rejected"
    assert result.canonical_lane is None


def test_malformed_msi_lane_never_leaks_into_lane_scope():
    result = decide(source(lane=0), [candidate()], lane_order_verified=True)

    assert result.status == "accepted"
    assert result.source_lane is None
    assert result.canonical_lane is None
    assert result.lane_scope_status == "source_only"


def test_drip_can_bind_to_segment_but_never_to_lane():
    result = decide(
        source("drip", lane=2),
        [candidate()],
        lane_order_verified=True,
    )

    assert result.status == "accepted"
    assert result.source_lane is None
    assert result.canonical_lane is None
    assert result.lane_scope_status == "not_applicable"


def test_drip_main_carriageway_rejects_link_road_candidate():
    main = decide(
        source("drip", carriageway="mainCarriageway"),
        [candidate(highway="motorway")],
    )
    link = decide(
        source("drip", carriageway="mainCarriageway"),
        [candidate(highway="motorway_link")],
    )

    assert main.status == "accepted"
    assert main.lane_scope_status == "not_applicable"
    assert link.status == "rejected"


def test_drip_slip_road_scope_only_accepts_osm_link_class():
    link = decide(
        source("drip", carriageway="exitSlipRoad"),
        [candidate(highway="motorway_link")],
    )
    through = decide(
        source("drip", carriageway="exitSlipRoad"),
        [candidate(highway="motorway")],
    )

    assert link.status == "accepted"
    assert link.canonical_lane is None
    assert through.status == "rejected"


def test_stale_state_keeps_location_binding_but_is_not_usable():
    result = decide(
        source(observed_at=NOW - timedelta(minutes=10)),
        [candidate()],
    )

    assert result.status == "accepted"
    assert result.stale is True
    assert result.state_usable is False


def test_missing_or_excessively_future_timestamp_is_not_current():
    missing = decide(source(observed_at=None), [candidate()])
    future = decide(source(observed_at=NOW + timedelta(minutes=5)), [candidate()])

    assert missing.stale is True
    assert missing.valid_until is None
    assert future.stale is True


def test_drip_is_relevant_only_on_current_or_confirmed_ahead_path():
    binding = decide(source("drip"), [candidate("drip-segment")])

    ahead = assess_drip_path_relevance(
        binding, "current", ["next", "drip-segment", "branch"]
    )
    off_path = assess_drip_path_relevance(binding, "current", ["next", "other"])

    assert ahead.relevant is True
    assert ahead.path_index == 2
    assert ahead.reason == "confirmed_ahead"
    assert off_path.relevant is False
    assert off_path.reason == "off_confirmed_path"


def test_path_relevance_rejects_unbound_drip_and_non_drip_contract():
    ambiguous = decide(
        source("drip", carriageway=None),
        [
            candidate("one", carriageway_ref=None, distance_m=4),
            candidate("two", carriageway_ref=None, distance_m=5),
        ],
    )
    assert assess_drip_path_relevance(ambiguous, "one", ["two"]).reason == "unbound"

    msi = decide(source("msi"), [candidate()])
    with pytest.raises(ValueError, match="only defined for DRIP"):
        assess_drip_path_relevance(msi, "segment-1", [])
