from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString, Point
from sqlalchemy.dialects import postgresql

from ndwinfo.api.routers.roads import load_direct_speed_states
from ndwinfo.config import settings
from ndwinfo.matching.source_binding import (
    RoadCandidate,
    SourceTraits,
    _candidate_groups,
    decide_binding,
    derive_vild_bearing,
    normalize_tmc_direction,
    resolve_source_heading,
)


def candidate(
    segment_id="s1",
    *,
    road="A4",
    side=None,
    lanes=2,
    distance=5.0,
    bearing=0.0,
):
    return RoadCandidate(segment_id, road, side, lanes, distance, bearing)


def test_accepts_clear_directional_road_match():
    decision = decide_binding(
        SourceTraits(road="A004", heading=2, lanes=2),
        [
            candidate(distance=4, bearing=1),
            candidate("s2", distance=10, bearing=180),
        ],
    )

    assert decision.status == "accepted"
    assert decision.internal_segment_id == "s1"
    assert decision.heading_delta_deg == 1
    assert decision.confidence >= 0.5


def test_explicit_road_conflict_is_rejected_not_nearest_corrected():
    decision = decide_binding(
        SourceTraits(road="A4", heading=0),
        [candidate(road="A5", distance=0.1, bearing=0)],
    )

    assert decision.status == "rejected"
    assert decision.internal_segment_id is None


def test_explicit_carriageway_conflict_is_rejected():
    decision = decide_binding(
        SourceTraits(road="A4", carriageway="rechts", heading=0),
        [candidate(side="L", bearing=0)],
    )

    assert decision.status == "rejected"


def test_opposite_heading_is_rejected():
    decision = decide_binding(
        SourceTraits(road="A4", heading=0),
        [candidate(bearing=180)],
    )

    assert decision.status == "rejected"


def test_same_geometry_both_directions_without_heading_stays_ambiguous():
    decision = decide_binding(
        SourceTraits(road="N201", heading=None),
        [
            candidate("forward", road="N201"),
            candidate("backward", road="N201", bearing=180),
        ],
    )

    assert decision.status == "ambiguous"
    assert decision.internal_segment_id is None


@pytest.mark.parametrize("value", ["positive", " Positive ", "POSITIEF", "pos", "+"])
def test_normalizes_known_positive_tmc_direction_spellings(value):
    assert normalize_tmc_direction(value) == "positive"


@pytest.mark.parametrize("value", ["negative", " Negative ", "NEGATIEF", "neg", "-"])
def test_normalizes_known_negative_tmc_direction_spellings(value):
    assert normalize_tmc_direction(value) == "negative"


@pytest.mark.parametrize("value", [None, "", "1", "2", "forward", "unknown"])
def test_unknown_or_numeric_tmc_direction_is_not_guessed(value):
    assert normalize_tmc_direction(value) is None


def test_vild_positive_and_negative_use_opposite_local_tangents():
    line = LineString([(4.0, 52.0), (4.0, 52.01)])
    site = Point(4.0001, 52.005)
    primary = Point(4.0, 52.005)
    positive = Point(4.0, 52.009)
    negative = Point(4.0, 52.001)

    positive_bearing = derive_vild_bearing(
        direction="positive",
        site_point=site,
        line=line,
        primary_point=primary,
        positive_point=positive,
        negative_point=negative,
    )
    negative_bearing = derive_vild_bearing(
        direction="negative",
        site_point=site,
        line=line,
        primary_point=primary,
        positive_point=positive,
        negative_point=negative,
    )

    assert positive_bearing == pytest.approx(0.0)
    assert negative_bearing == pytest.approx(180.0)


def test_vild_uses_tangent_nearest_site_instead_of_whole_line_chord():
    line = LineString([(4.0, 52.0), (4.01, 52.0), (4.01, 52.01)])
    bearing = derive_vild_bearing(
        direction="positive",
        site_point=Point(4.0101, 52.007),
        line=line,
        primary_point=Point(4.01, 52.005),
        positive_point=Point(4.01, 52.009),
        negative_point=Point(4.01, 52.001),
    )

    assert bearing == pytest.approx(0.0)


def test_vild_missing_selected_direction_neighbour_fails_closed():
    assert (
        derive_vild_bearing(
            direction="negative",
            site_point=Point(4.0, 52.005),
            line=LineString([(4.0, 52.0), (4.0, 52.01)]),
            primary_point=Point(4.0, 52.005),
            positive_point=Point(4.0, 52.009),
        )
        is None
    )


def test_vild_duplicate_topology_point_fails_closed():
    assert (
        derive_vild_bearing(
            direction="positive",
            site_point=Point(4.0, 52.005),
            line=LineString([(4.0, 52.0), (4.0, 52.01)]),
            primary_point=Point(4.0, 52.005),
            positive_point=Point(4.0, 52.005),
        )
        is None
    )


def test_vild_inconsistent_topology_arms_fail_closed():
    assert (
        derive_vild_bearing(
            direction="positive",
            site_point=Point(4.0, 52.005),
            line=LineString([(4.0, 52.0), (4.0, 52.01)]),
            primary_point=Point(4.0, 52.002),
            positive_point=Point(4.0, 52.008),
            negative_point=Point(4.0, 52.009),
        )
        is None
    )


def test_openlr_bearing_has_strict_precedence_over_vild_fallback():
    assert resolve_source_heading(295.7, 75.0) == pytest.approx(295.7)
    assert resolve_source_heading(None, 75.0) == pytest.approx(75.0)
    assert resolve_source_heading(float("nan"), None) is None


def test_derived_vild_heading_disambiguates_shared_two_way_geometry():
    heading = derive_vild_bearing(
        direction="negative",
        site_point=Point(4.0, 52.005),
        line=LineString([(4.0, 52.0), (4.0, 52.01)]),
        primary_point=Point(4.0, 52.005),
        positive_point=Point(4.0, 52.009),
        negative_point=Point(4.0, 52.001),
    )
    decision = decide_binding(
        SourceTraits(heading=heading),
        [
            candidate("north", road=None, bearing=0.0),
            candidate("south", road=None, bearing=180.0),
        ],
    )

    assert decision.status == "accepted"
    assert decision.internal_segment_id == "south"


def test_candidate_query_distance_gates_vild_line_fallback():
    class CaptureSession:
        query = None

        def execute(self, query):
            self.query = query
            return []

    session = CaptureSession()
    assert list(_candidate_groups(session, 1, [])) == []

    compiled = session.query.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "bearing_vild_line" in sql
    # One gate bounds OSM candidates and the other independently bounds the
    # VILD direction fallback. A remote VILD reference therefore resolves NULL.
    assert sql.count("ST_DWithin") >= 2
    assert settings.source_binding_max_distance_m in compiled.params.values()
    assert settings.source_binding_vild_max_distance_m in compiled.params.values()


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _query):
        return _Rows(self.rows)


def speed_row(
    speed, measured_at, confidence=0.9, *, lane=1, site_lane_count=2,
    source_id="site-1",
):
    return SimpleNamespace(
        internal_segment_id="s1",
        source_id=source_id,
        confidence=confidence,
        speed_kmh=speed,
        measured_at=measured_at,
        lane=lane,
        site_lane_count=site_lane_count,
    )


def test_direct_speed_preserves_fresh_zero_kmh():
    now = datetime.now(timezone.utc)
    states = load_direct_speed_states(_Db([speed_row(0, now)]), "graph-v1", ["s1"])

    assert states["s1"]["speed_kmh"] == 0.0
    assert states["s1"]["speed_method"] == "measured"
    assert states["s1"]["speed_stale"] is False
    assert states["s1"]["speed_valid_until"] == (
        now + timedelta(seconds=600)
    ).isoformat()


def test_stale_speed_is_not_exposed_as_current():
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    states = load_direct_speed_states(_Db([speed_row(42, old)]), "graph-v1", ["s1"])

    assert states["s1"]["speed_kmh"] is None
    assert states["s1"]["speed_method"] == "unknown"
    assert states["s1"]["speed_stale"] is True


def test_stale_lane_does_not_pollute_fresh_direct_speed():
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=1)
    states = load_direct_speed_states(
        _Db([speed_row(0, now), speed_row(100, old)]), "graph-v1", ["s1"]
    )

    assert states["s1"]["speed_kmh"] == 0.0
    assert states["s1"]["speed_sample_count"] == 1


def test_rejected_future_observation_does_not_extend_fresh_speed_validity():
    now = datetime.now(timezone.utc)
    future = now + timedelta(minutes=5)
    states = load_direct_speed_states(
        _Db([speed_row(80, now), speed_row(120, future)]),
        "graph-v1",
        ["s1"],
    )

    assert states["s1"]["speed_kmh"] == 80.0
    assert states["s1"]["speed_sample_count"] == 1
    assert states["s1"]["speed_observed_at"] == now.isoformat()
    assert states["s1"]["speed_valid_until"] == (
        now + timedelta(seconds=600)
    ).isoformat()


def test_direct_lane_state_requires_matching_explicit_lane_count():
    now = datetime.now(timezone.utc)
    states = load_direct_speed_states(
        _Db([
            speed_row(91, now, lane=1, site_lane_count=2),
            speed_row(73, now, lane=2, site_lane_count=2),
            speed_row(40, now, lane=3, site_lane_count=3),
        ]),
        "graph-v1",
        ["s1"],
        lane_counts={"s1": 2},
        lane_order_verified=True,
    )

    assert [(lane["lane"], lane["speed_kmh"]) for lane in states["s1"]["lane_states"]] == [
        (1, 91.0),
        (2, 73.0),
    ]


def test_direct_lane_state_stays_empty_on_carriageway_count_mismatch():
    now = datetime.now(timezone.utc)
    states = load_direct_speed_states(
        _Db([speed_row(91, now, lane=1, site_lane_count=3)]),
        "graph-v1",
        ["s1"],
        lane_counts={"s1": 2},
        lane_order_verified=True,
    )

    assert states["s1"]["lane_states"] == []


def test_lane_less_measurement_contributes_to_carriageway_but_not_lane_state():
    now = datetime.now(timezone.utc)
    states = load_direct_speed_states(
        _Db([speed_row(72, now, lane=None, site_lane_count=None)]),
        "graph-v1",
        ["s1"],
        lane_counts={"s1": 2},
        lane_order_verified=True,
    )
    assert states["s1"]["speed_kmh"] == 72.0
    assert states["s1"]["lane_states"] == []


def test_carriageway_speed_weights_sites_not_their_lane_counts():
    now = datetime.now(timezone.utc)
    states = load_direct_speed_states(
        _Db([
            speed_row(10, now, lane=1, source_id="three-lane-site"),
            speed_row(20, now, lane=2, source_id="three-lane-site"),
            speed_row(30, now, lane=3, source_id="three-lane-site"),
            speed_row(90, now, lane=None, source_id="aggregate-site"),
        ]),
        "graph-v1",
        ["s1"],
    )
    assert states["s1"]["speed_kmh"] == 55.0
    assert states["s1"]["speed_sample_count"] == 2
