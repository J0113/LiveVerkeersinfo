"""DB-free Matrix-first matcher proof tests."""

from __future__ import annotations

import json
from pathlib import Path

from ndwinfo.match_matrix import ALGORITHM_VERSION, build_report
from ndwinfo.road_matching.evidence import (
    angular_difference_deg,
    expected_osm_carriageway_refs,
    normalize_road_ref,
)
from ndwinfo.road_matching.points import (
    dedupe_matrix_signs,
    group_matrix_signs,
    match_matrix_gantry,
    matrix_gantry_key,
    traversals_are_continuous,
)
from ndwinfo.road_matching.persistence import (
    matrix_source_fingerprint,
    persist_matrix_matches,
)
from ndwinfo.road_matching.types import LaneCandidate, MatrixSign

FIXTURES = Path(__file__).parent / "fixtures" / "road_matching"


def _sign(uuid: str, *, road="A9", carriageway="R", lane=1, km=12.34, bearing=90, ts=None):
    return MatrixSign(uuid, road, carriageway, lane, km, bearing, state_timestamp=ts)


def _candidate(
    lane_id: str,
    *,
    road_id=100,
    segment_id="100:1:2",
    direction="fwd",
    lane_nr=1,
    lane_count=2,
    ref="A9",
    highway="motorway",
    carriageway_ref=None,
    distance=1.0,
    bearing=90,
):
    return LaneCandidate(
        lane_id=lane_id,
        road_id=road_id,
        segment_id=segment_id,
        direction=direction,
        lane_nr=lane_nr,
        lane_count=lane_count,
        ref=ref,
        highway=highway,
        distance_m=distance,
        bearing_deg=bearing,
        position_fraction=0.5,
        projected=(4.7, 52.5),
        carriageway_ref=carriageway_ref,
    )


def _fixture_case(case):
    sign = MatrixSign(**case["sign"])
    candidates = [
        LaneCandidate(
            lane_id=item["lane_id"],
            road_id=item["road_id"],
            segment_id=item["segment_id"],
            direction=item["direction"],
            lane_nr=item["lane_nr"],
            lane_count=item["lane_count"],
            ref=item["ref"],
            highway=item["highway"],
            distance_m=item["distance_m"],
            bearing_deg=item["bearing_deg"],
            position_fraction=item.get("position_fraction"),
            projected=tuple(item["projected"]) if item.get("projected") else None,
        )
        for item in case["candidates"]
    ]
    return sign, candidates


def test_reference_and_angle_normalization():
    assert normalize_road_ref("A 009") == "A9"
    assert normalize_road_ref("N-203") == "N203"
    assert normalize_road_ref("001") == "1"
    assert angular_difference_deg(359, 1) == 2
    assert angular_difference_deg(90, 270) == 180


def test_manual_fixture_cases_fail_closed_for_opposite_and_ambiguous_roads():
    cases = json.loads((FIXTURES / "matrix_cases.json").read_text())
    for case in cases:
        sign, candidates = _fixture_case(case)
        gantry = group_matrix_signs([sign])[0]
        result = match_matrix_gantry(gantry, {sign.uuid: candidates})[0]
        expected = case["expected"]
        assert result.status == expected["status"], case["name"]
        assert result.direction == expected["direction"], case["name"]
        assert result.confidence == expected["confidence"], case["name"]


def test_gantry_consensus_groups_lanes_and_maps_lane_specific_links():
    signs = [
        _sign("lane-1", lane=1),
        _sign("lane-2", lane=2),
    ]
    candidates = {
        "lane-1": [_candidate("ll:100:1:2:fwd:1", lane_nr=1)],
        "lane-2": [_candidate("ll:100:1:2:fwd:2", lane_nr=2, distance=1.2)],
    }

    matches = match_matrix_gantry(group_matrix_signs(signs)[0], candidates)

    assert [(match.segment_id, match.direction) for match in matches] == [
        ("100:1:2", "fwd"),
        ("100:1:2", "fwd"),
    ]
    assert [match.applies_to_lane_id for match in matches] == [
        "ll:100:1:2:fwd:1",
        "ll:100:1:2:fwd:2",
    ]
    assert all(match.confidence == "high" for match in matches)


def test_lane_count_mismatch_keeps_segment_anchor_but_not_lane_scope():
    signs = [_sign("lane-1", lane=1), _sign("lane-2", lane=2)]
    candidates = {
        "lane-1": [_candidate("ll:100:1:2:fwd:1", lane_nr=1, lane_count=1)],
        "lane-2": [_candidate("ll:100:1:2:fwd:1", lane_nr=1, lane_count=1)],
    }

    matches = match_matrix_gantry(group_matrix_signs(signs)[0], candidates)

    assert all(match.status == "matched" for match in matches)
    assert all(match.applies_to_lane_id is None for match in matches)
    assert all(match.failure_reason == "lane_count_mismatch" for match in matches)
    assert all(match.diagnostics["lane_count_mismatch"] is True for match in matches)


def test_a_lowercase_connector_carriageway_is_not_folded_into_the_main_one():
    # NDW writes main carriageways as R/L and connectors as lowercase letters,
    # so 'r' and 'R' at the same road and kilometre are different roadways.
    # Case-folding them merged the gantries and dropped the connector sign as a
    # ghost duplicate -- 91 signs sit on a lowercase 'r' in the live snapshot.
    main = _sign("main-R", road="A2", carriageway="R", km=121.60)
    connector = _sign("conn-r", road="A2", carriageway="r", km=121.60)

    assert matrix_gantry_key(main) != matrix_gantry_key(connector)
    assert {sign.uuid for sign in dedupe_matrix_signs([main, connector])} == {
        "main-R",
        "conn-r",
    }
    assert len(group_matrix_signs([main, connector])) == 2
    assert expected_osm_carriageway_refs(main) == {"Re"}
    assert expected_osm_carriageway_refs(connector) == {"r"}


def test_conflicting_road_reference_is_not_recovered_by_proximity():
    sign = _sign("wrong-road", road="A9")
    candidate = _candidate("ll:100:1:2:fwd:1", ref="A8", distance=0.1)
    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: [candidate]}
    )[0]
    assert match.status == "unmatched"
    assert match.failure_reason == "road_ref_conflict"


def test_nearest_valid_geometry_beats_better_heading_on_a_distant_link():
    sign = _sign("main-road", road="A22", bearing=202)
    candidates = [
        _candidate(
            "ll:6632502:main:fwd:1",
            road_id=6632502,
            segment_id="6632502:main",
            ref="A22",
            distance=1.7,
            bearing=214,
        ),
        _candidate(
            "ll:141700067:link:fwd:1",
            road_id=141700067,
            segment_id="141700067:link",
            ref="A22",
            distance=13.2,
            bearing=199,
        ),
    ]

    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: candidates}
    )[0]

    assert match.status == "matched"
    assert match.road_id == 6632502
    assert match.source_distance_m == 1.7


def test_a_conflicting_mainline_ref_leaves_the_nearby_route_link_selected():
    # The mainline candidate keeps the default A9 ref, which conflicts with the
    # sign's A22 and is rejected outright; the link is close and
    # direction-compatible enough to earn the connector exemption.
    sign = _sign("junction-link", road="A22", bearing=175)
    candidates = [
        _candidate(
            "ll:6632502:main:fwd:1",
            road_id=6632502,
            segment_id="6632502:main",
            distance=1.7,
            bearing=209,
        ),
        _candidate(
            "ll:1096129211:link:fwd:1",
            road_id=1096129211,
            segment_id="1096129211:link",
            distance=2.3,
            bearing=201,
            highway="motorway_link",
        ),
    ]

    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: candidates}
    )[0]

    assert match.status == "matched"
    assert match.road_id == 1096129211


def test_carriageway_ref_separates_a_parallel_roadway_inside_the_distance_margin():
    # 0.9 m of distance and 1.5 degrees of heading do not separate two parallel
    # roadways. The source-compatible carriageway ref does, and 99% of motorway
    # ways carry one, so this is the evidence that resolves a real near-tie.
    sign = _sign("junction-main", road="A22", carriageway="R", bearing=19.4)
    candidates = [
        _candidate(
            "ll:511168728:main:fwd:1",
            road_id=511168728,
            segment_id="511168728:main",
            ref="A22",
            carriageway_ref="Re",
            distance=1.559,
            bearing=28.853,
        ),
        _candidate(
            "ll:6632505:parallel:fwd:1",
            road_id=6632505,
            segment_id="6632505:parallel",
            ref="A22",
            carriageway_ref="Li",
            distance=2.414,
            bearing=30.339,
        ),
    ]

    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: candidates}
    )[0]

    assert match.status == "matched"
    assert match.road_id == 511168728


def test_consecutive_segments_of_one_carriageway_are_not_rival_roads():
    # A gantry standing on a segment boundary sees the same ramp corridor on
    # both sides of it. OSM chains the pieces through a shared node
    # (…:7526531055 -> 7526531055:…), so this is one road continuing, and the
    # nearer piece is simply where the sign is.
    sign = _sign("boundary", road="A10", carriageway="L", bearing=186.3)
    candidates = [
        _candidate(
            "ll:1333532311:7526531055:12337587960:fwd:3",
            road_id=1333532311,
            segment_id="1333532311:7526531055:12337587960",
            ref="A10",
            highway="motorway_link",
            carriageway_ref="c",
            lane_nr=3,
            lane_count=3,
            distance=4.13,
            bearing=180.88,
        ),
        _candidate(
            "ll:1333532308:12337587960:6321579209:fwd:3",
            road_id=1333532308,
            segment_id="1333532308:12337587960:6321579209",
            ref="A10",
            highway="motorway_link",
            carriageway_ref="c",
            lane_nr=3,
            lane_count=3,
            distance=4.47,
            bearing=181.48,
        ),
    ]

    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: candidates}
    )[0]

    assert match.status == "matched"
    assert match.road_id == 1333532311


def test_opposite_directions_of_one_segment_are_not_treated_as_continuous():
    # Both directions of a segment share the same two nodes, so a naive
    # node-chaining test would call them continuous. They are the opposite
    # carriageways, which is precisely the choice that must stay ambiguous.
    assert not traversals_are_continuous(("100:1:2", "fwd"), ("100:1:2", "bwd"))
    assert traversals_are_continuous(("100:1:2", "fwd"), ("101:2:3", "fwd"))
    assert not traversals_are_continuous(("100:1:2", "fwd"), ("201:3:4", "fwd"))
    # The degenerate identity carries no topology and must not chain.
    assert not traversals_are_continuous(("100:0:0", "fwd"), ("101:0:0", "fwd"))


def test_a_parallel_roadway_without_separating_evidence_fails_closed():
    sign = _sign("junction-blind", road="A22", bearing=19.4)
    candidates = [
        _candidate(
            "ll:511168728:main:fwd:1",
            road_id=511168728,
            segment_id="511168728:main",
            ref="A22",
            distance=1.559,
            bearing=28.853,
        ),
        _candidate(
            "ll:6632505:parallel:fwd:1",
            road_id=6632505,
            segment_id="6632505:parallel",
            ref="A22",
            distance=2.414,
            bearing=30.339,
        ),
    ]

    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: candidates}
    )[0]

    assert match.status == "ambiguous"
    assert match.failure_reason == "bearing_ambiguous"


def test_source_compatible_main_carriageway_ref_resolves_adjacent_way():
    sign = _sign("main-carriageway", road="A208", carriageway="R", bearing=32)
    candidates = [
        _candidate(
            "ll:1227405269:near:fwd:1",
            road_id=1227405269,
            segment_id="1227405269:near",
            ref="A208",
            distance=1.68,
            bearing=45.37,
            carriageway_ref="Re",
        ),
        _candidate(
            "ll:1096119818:next:fwd:1",
            road_id=1096119818,
            segment_id="1096119818:next",
            ref="A208",
            distance=2.08,
            bearing=43.13,
            carriageway_ref="Re",
        ),
    ]

    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: candidates}
    )[0]

    assert match.status == "matched"
    assert match.road_id == 1227405269


def test_nearby_route_link_can_cross_a_source_road_ref_at_a_junction():
    sign = _sign("route-transition", road="A208", carriageway="L", bearing=176)
    candidate = _candidate(
        "ll:511168729:connector:fwd:1",
        road_id=511168729,
        segment_id="511168729:connector",
        distance=1.69,
        bearing=171,
        ref="A22",
        highway="motorway_link",
        carriageway_ref="c",
    )

    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: [candidate]}
    )[0]

    assert match.status == "matched"
    assert match.road_id == 511168729
    assert match.road_ref_quality == "connector"


def test_missing_bearing_does_not_choose_between_opposite_traversals():
    sign = _sign("missing-bearing", bearing=None)
    candidates = [
        _candidate("ll:100:1:2:fwd:1", direction="fwd", distance=1.0, bearing=90),
        _candidate("ll:100:1:2:bwd:1", direction="bwd", distance=10.0, bearing=270),
    ]
    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: candidates}
    )[0]
    assert match.status == "ambiguous"
    assert match.failure_reason == "bearing_ambiguous"


def test_ghost_deduplication_precedes_gantry_grouping_and_limit():
    signs = [
        _sign("old", ts="2026-07-31T10:00:00Z"),
        _sign("new", ts="2026-07-31T10:01:00Z"),
        _sign("other", km=20.0, ts="2026-07-31T10:00:00Z"),
    ]
    deduped = dedupe_matrix_signs(signs)
    assert [sign.uuid for sign in deduped] == ["new", "other"]
    assert len(group_matrix_signs(signs)) == 2


def test_dry_run_report_has_stable_aggregates():
    sign = _sign("report-1")
    report = build_report(
        [sign],
        {sign.uuid: [_candidate("ll:100:1:2:fwd:1")]},
        radius_m=20.0,
    )
    assert report["algorithm_version"] == ALGORITHM_VERSION
    assert report["status_counts"] == {"matched": 1}
    assert report["confidence_counts"] == {"high": 1}
    assert report["source_distance_m"]["p50"] == 1.0


class _RecordingSession:
    """Minimal stand-in that records the statements a persist run issues."""

    def __init__(self):
        self.statements = []

    def execute(self, statement):
        # Inserts carry JSONB/geometry values that will not render as literals;
        # only the deletes need inspecting here.
        try:
            rendered = str(statement.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            rendered = str(statement)
        self.statements.append(" ".join(rendered.split()))
        return None


def test_persisting_drops_assignments_from_a_superseded_matcher_version():
    sign = _sign("persist-1")
    match = match_matrix_gantry(
        group_matrix_signs([sign])[0], {sign.uuid: [_candidate("ll:100:1:2:fwd:1")]}
    )[0]
    session = _RecordingSession()

    persist_matrix_matches(session, [sign], [match], algorithm_version="matrix-test-v9")

    deletes = [sql for sql in session.statements if sql.startswith("DELETE")]
    assert any("road_point_link" in sql for sql in deletes)
    stale = [sql for sql in deletes if "road_point_assignment" in sql]
    assert len(stale) == 1
    assert "algorithm_version != 'matrix-test-v9'" in stale[0]


def test_source_fingerprint_ignores_live_display_state():
    base = _sign("fingerprint-1")
    restated = MatrixSign(
        base.uuid, base.road, base.carriageway, base.lane, base.km, base.bearing,
        state_timestamp="2026-07-31T12:00:00Z", has_value=True,
    )
    moved = MatrixSign(
        base.uuid, base.road, base.carriageway, base.lane, base.km + 0.1, base.bearing,
    )

    assert matrix_source_fingerprint(base) == matrix_source_fingerprint(restated)
    assert matrix_source_fingerprint(base) != matrix_source_fingerprint(moved)
