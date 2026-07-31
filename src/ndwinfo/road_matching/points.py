"""Conservative Matrix point/gantry matcher.

This module intentionally returns typed in-memory results only.  Persistence
and road-ahead queries land after the Matrix dry-run has been reviewed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ndwinfo.road_matching.evidence import (
    bearing_error,
    carriageway_ref_rank,
    normalize_carriageway,
    normalize_road_ref,
    road_ref_quality,
)
from ndwinfo.road_matching.types import (
    LaneCandidate,
    MatrixGantry,
    MatrixSign,
    MatrixSignMatch,
)

DEFAULT_BEARING_TOLERANCE_DEG = 45.0
DEFAULT_AMBIGUITY_DISTANCE_M = 2.0
DEFAULT_AMBIGUITY_BEARING_MARGIN_DEG = 5.0
DEFAULT_NEAR_TIE_DISTANCE_M = 2.0


def _km_bucket(km: float | None) -> float | None:
    return round(float(km), 2) if km is not None else None


def matrix_gantry_key(sign: MatrixSign) -> tuple[str | None, str | None, float | None]:
    return (
        normalize_road_ref(sign.road),
        normalize_carriageway(sign.carriageway),
        _km_bucket(sign.km),
    )


def matrix_gantry_id(key: tuple[str | None, str | None, float | None]) -> str:
    canonical = "|".join("" if value is None else str(value) for value in key)
    digest = hashlib.sha256(f"matrix:{canonical}".encode("utf-8")).hexdigest()[:16]
    return f"matrix-gantry-{digest}"


def _timestamp_sort_key(value) -> tuple[int, str]:
    if value is None:
        return (0, "")
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    return (1, str(value))


def dedupe_matrix_signs(signs: Iterable[MatrixSign]) -> tuple[MatrixSign, ...]:
    """Drop duplicate UUIDs/ghost slots before any result limit is applied."""

    best: dict[tuple[str | None, str | None, int | None, float | None], MatrixSign] = {}
    for sign in signs:
        road, carriageway, km = matrix_gantry_key(sign)
        key = (road, carriageway, sign.lane, km)
        current = best.get(key)
        if current is None or (
            _timestamp_sort_key(sign.state_timestamp), sign.uuid
        ) > (_timestamp_sort_key(current.state_timestamp), current.uuid):
            best[key] = sign
    return tuple(sorted(best.values(), key=lambda item: item.uuid))


def group_matrix_signs(signs: Iterable[MatrixSign]) -> tuple[MatrixGantry, ...]:
    groups: dict[tuple[str | None, str | None, float | None], list[MatrixSign]] = defaultdict(list)
    for sign in dedupe_matrix_signs(signs):
        groups[matrix_gantry_key(sign)].append(sign)
    return tuple(
        MatrixGantry(
            gantry_id=matrix_gantry_id(key),
            key=key,
            signs=tuple(
                sorted(
                    group,
                    key=lambda item: (item.lane is None, item.lane or 0, item.uuid),
                )
            ),
        )
        for key, group in sorted(groups.items(), key=lambda item: str(item[0]))
    )


def _traversal_endpoints(key: tuple[str, str]) -> tuple[str, str] | None:
    """Return the (entry, exit) OSM node of a traversal in travel order.

    Logical segment IDs are ``<way>:<start_node>:<end_node>``, so the nodes
    come straight off the key -- no graph query needed. The degenerate
    ``<way>:0:0`` form carries no topology and yields None.
    """

    parts = key[0].split(":")
    if len(parts) != 3:
        return None
    _way, start, end = parts
    if start == "0" and end == "0":
        return None
    return (start, end) if key[1] == "fwd" else (end, start)


def traversals_are_continuous(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """True when one traversal flows directly into the other.

    OSM splits a carriageway into a chain of ways, so a gantry standing near a
    segment boundary gets candidates from both sides of it. Those are the same
    road continuing, not rival roads, and must never be scored against each
    other as if the sign had to choose between them.

    Two directions of the *same* segment are excluded: they share both nodes
    but are opposite carriageways, which is exactly the choice that has to
    stay ambiguous without directional evidence.
    """

    if a[0] == b[0]:
        return False
    ends_a = _traversal_endpoints(a)
    ends_b = _traversal_endpoints(b)
    if ends_a is None or ends_b is None:
        return False
    return ends_a[1] == ends_b[0] or ends_b[1] == ends_a[0]


@dataclass
class _TraversalEvidence:
    key: tuple[str, str]
    candidates_by_uuid: dict[str, LaneCandidate]
    exact_ref_count: int
    bearing_errors: list[float]

    @property
    def coverage(self) -> int:
        return len(self.candidates_by_uuid)

    @property
    def mean_distance(self) -> float:
        return sum(item.distance_m for item in self.candidates_by_uuid.values()) / self.coverage

    @property
    def mean_bearing_error(self) -> float | None:
        if not self.bearing_errors:
            return None
        return sum(self.bearing_errors) / len(self.bearing_errors)


def _traversal_distance_sort_key(item: _TraversalEvidence) -> tuple:
    return (
        item.mean_distance,
        item.mean_bearing_error if item.mean_bearing_error is not None else 180.0,
        item.key[0],
        item.key[1],
    )


def _traversal_carriageway_sort_key(
    item: _TraversalEvidence,
    signs_by_uuid: dict[str, MatrixSign],
) -> tuple[int, int, int]:
    ranks = [
        carriageway_ref_rank(signs_by_uuid[uuid], candidate)
        for uuid, candidate in item.candidates_by_uuid.items()
        if uuid in signs_by_uuid
    ]
    return (
        -sum(rank == 0 for rank in ranks),
        -sum(rank <= 1 for rank in ranks),
        sum(ranks),
    )


def _traversal_has_exact_carriageway_refs(
    item: _TraversalEvidence,
    signs_by_uuid: dict[str, MatrixSign],
) -> bool:
    ranks = [
        carriageway_ref_rank(signs_by_uuid[uuid], candidate)
        for uuid, candidate in item.candidates_by_uuid.items()
        if uuid in signs_by_uuid
    ]
    return bool(ranks) and all(rank == 0 for rank in ranks)


def _rank_traversals(
    traversals: Iterable[_TraversalEvidence],
    *,
    near_tie_distance_m: float,
    signs_by_uuid: dict[str, MatrixSign],
) -> list[_TraversalEvidence]:
    """Rank compatible traversals while handling junction near-ties.

    Distance is the primary evidence when roads are materially separated. At
    a merge/fork, however, the mainline and connector centerlines can be less
    than a couple of metres apart. In that narrow window the source bearing is
    useful disambiguation, while a bearing difference must not override a
    clearly closer road.
    """

    traversals = list(traversals)
    if not traversals:
        return []
    def quality_key(item):
        return (-item.coverage, -item.exact_ref_count)
    best_quality = min(quality_key(item) for item in traversals)
    best_quality_items = [item for item in traversals if quality_key(item) == best_quality]
    remaining = [item for item in traversals if quality_key(item) != best_quality]
    nearest_distance = min(item.mean_distance for item in best_quality_items)
    near_ties = [
        item
        for item in best_quality_items
        if item.mean_distance <= nearest_distance + near_tie_distance_m
    ]
    far = [item for item in best_quality_items if item not in near_ties]
    near_ties.sort(
        key=lambda item: (
            *_traversal_carriageway_sort_key(item, signs_by_uuid),
            item.mean_distance,
            item.mean_bearing_error if item.mean_bearing_error is not None else 180.0,
            item.key[0],
            item.key[1],
        )
    )
    far.sort(key=_traversal_distance_sort_key)
    remaining.sort(key=lambda item: (quality_key(item), *_traversal_distance_sort_key(item)))
    return near_ties + far + remaining


def _best_lane_candidate(
    sign: MatrixSign,
    candidates: Iterable[LaneCandidate],
    *,
    bearing_tolerance_deg: float,
) -> tuple[LaneCandidate | None, str | None]:
    candidates = list(candidates)
    if not candidates:
        return None, "no_major_road"

    qualities = [(candidate, road_ref_quality(sign, candidate)) for candidate in candidates]
    non_conflicting = [
        (candidate, quality)
        for candidate, quality in qualities
        if quality != "conflict"
    ]
    if not non_conflicting:
        return None, "road_ref_conflict"

    directional = []
    for candidate, quality in non_conflicting:
        error = bearing_error(sign, candidate)
        if error is not None and error > bearing_tolerance_deg:
            continue
        directional.append((candidate, quality, error))
    if not directional:
        return None, "bearing_mismatch"

    def key(item):
        candidate, quality, error = item
        # The source point is the strongest positional evidence.  Bearing is
        # still a hard compatibility guard above, but it must not pull a
        # distant ramp ahead of the lane that the sign is physically on: a
        # Matrix bearing can describe the gantry/road axis rather than the
        # locally curved OSM segment.
        return (
            candidate.distance_m,
            0 if quality == "exact" else 1,
            error if error is not None else 180.0,
            candidate.lane_id,
        )

    selected, _quality, _error = min(directional, key=key)
    return selected, None


def _runner_up_is_ambiguous(
    winner: _TraversalEvidence,
    runner_up: _TraversalEvidence | None,
    *,
    distance_margin_m: float,
    bearing_margin_deg: float,
    signs_by_uuid: dict[str, MatrixSign],
) -> bool:
    if runner_up is None:
        return False
    # The runner-up is the same carriageway continuing across a segment
    # boundary, so there is no road to choose between -- the sign sits on the
    # nearer piece.
    if traversals_are_continuous(winner.key, runner_up.key):
        return False
    if winner.coverage != runner_up.coverage:
        return False
    if winner.exact_ref_count != runner_up.exact_ref_count:
        return False
    if _traversal_carriageway_sort_key(winner, signs_by_uuid) < _traversal_carriageway_sort_key(
        runner_up, signs_by_uuid
    ):
        return False
    # When both traversals carry the source-compatible main-carriageway ref
    # (Re/Li, accepting Le as a left-side spelling), the nearer one is enough
    # to resolve the tie. This covers adjacent OSM ways split at a junction.
    if (
        _traversal_has_exact_carriageway_refs(winner, signs_by_uuid)
        and _traversal_has_exact_carriageway_refs(runner_up, signs_by_uuid)
        and winner.mean_distance < runner_up.mean_distance
    ):
        return False
    distance_close = abs(winner.mean_distance - runner_up.mean_distance) <= distance_margin_m
    winner_bearing = winner.mean_bearing_error
    runner_bearing = runner_up.mean_bearing_error
    # Being merely nearer and no worse on bearing is NOT disambiguation. Two
    # parallel roadways carrying the same route reference sit metres apart with
    # near-identical travel bearings, so a sub-metre distance edge would elect
    # a winner by coin flip. Separation has to come from independent evidence
    # (carriageway ref, route ref, or a distance gap wider than the margin)
    # handled above; otherwise this fails closed.
    bearing_close = (
        winner_bearing is None
        or runner_bearing is None
        or abs(winner_bearing - runner_bearing) <= bearing_margin_deg
    )
    return distance_close and bearing_close


def _failure_match(
    sign: MatrixSign,
    gantry_id: str,
    candidate_count: int,
    reason: str,
    diagnostics: dict,
) -> MatrixSignMatch:
    return MatrixSignMatch(
        uuid=sign.uuid,
        gantry_id=gantry_id,
        status="unmatched" if reason != "bearing_ambiguous" else "ambiguous",
        confidence=None,
        method="gantry_consensus",
        failure_reason=reason,
        candidate_count=candidate_count,
        diagnostics=diagnostics,
    )


def match_matrix_gantry(
    gantry: MatrixGantry,
    candidates_by_uuid: dict[str, Iterable[LaneCandidate]],
    *,
    bearing_tolerance_deg: float = DEFAULT_BEARING_TOLERANCE_DEG,
    ambiguity_distance_m: float = DEFAULT_AMBIGUITY_DISTANCE_M,
    ambiguity_bearing_margin_deg: float = DEFAULT_AMBIGUITY_BEARING_MARGIN_DEG,
) -> tuple[MatrixSignMatch, ...]:
    """Match all signs on one physical gantry to one traversal.

    Lane rows are first grouped by ``(segment_id, direction)``.  This is the
    key safety property: a wide gantry's parallel lane rows cannot compete as
    independent roads, and opposite carriageways remain separate traversals.
    """

    traversal_evidence: dict[tuple[str, str], _TraversalEvidence] = {}
    failure_reasons: dict[str, set[str]] = defaultdict(set)
    all_candidate_count = 0

    for sign in gantry.signs:
        raw_candidates = list(candidates_by_uuid.get(sign.uuid, ()))
        by_traversal: dict[tuple[str, str], list[LaneCandidate]] = defaultdict(list)
        for candidate in raw_candidates:
            by_traversal[(candidate.segment_id, candidate.direction)].append(candidate)
        all_candidate_count = max(all_candidate_count, len(by_traversal))
        for traversal_key, traversal_candidates in by_traversal.items():
            selected, reason = _best_lane_candidate(
                sign,
                traversal_candidates,
                bearing_tolerance_deg=bearing_tolerance_deg,
            )
            if reason:
                failure_reasons[sign.uuid].add(reason)
                continue
            assert selected is not None
            quality = road_ref_quality(sign, selected)
            evidence = traversal_evidence.setdefault(
                traversal_key,
                _TraversalEvidence(traversal_key, {}, 0, []),
            )
            evidence.candidates_by_uuid[sign.uuid] = selected
            if quality == "exact":
                evidence.exact_ref_count += 1
            error = bearing_error(sign, selected)
            if error is not None:
                evidence.bearing_errors.append(error)

    ranked = _rank_traversals(
        traversal_evidence.values(),
        near_tie_distance_m=DEFAULT_NEAR_TIE_DISTANCE_M,
        signs_by_uuid={sign.uuid: sign for sign in gantry.signs},
    )
    winner = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None

    if winner is None:
        outputs = []
        for sign in gantry.signs:
            reasons = failure_reasons.get(sign.uuid, set())
            reason = (
                "road_ref_conflict"
                if reasons == {"road_ref_conflict"}
                else "bearing_mismatch"
                if reasons == {"bearing_mismatch"}
                else "no_major_road"
            )
            outputs.append(
                _failure_match(
                    sign,
                    gantry.gantry_id,
                    all_candidate_count,
                    reason,
                    {"rejected_reasons": sorted(reasons)},
                )
            )
        return tuple(outputs)

    ambiguous = _runner_up_is_ambiguous(
        winner,
        runner_up,
        distance_margin_m=ambiguity_distance_m,
        bearing_margin_deg=ambiguity_bearing_margin_deg,
        signs_by_uuid={sign.uuid: sign for sign in gantry.signs},
    )
    # With no source bearing, a distance-only choice between opposite
    # traversals is not directional evidence.  Keep it out of the HUD even
    # when one lane happens to be a little closer to the source point.
    if not ambiguous and runner_up and any(sign.bearing is None for sign in gantry.signs):
        winner_roads = {candidate.road_id for candidate in winner.candidates_by_uuid.values()}
        runner_roads = {candidate.road_id for candidate in runner_up.candidates_by_uuid.values()}
        if winner_roads & runner_roads and winner.key[1] != runner_up.key[1]:
            ambiguous = True
    selected_candidates = winner.candidates_by_uuid
    osm_lane_counts = {
        candidate.lane_count
        for candidate in selected_candidates.values()
        if candidate.lane_count is not None
    }
    osm_lane_count = max(osm_lane_counts) if len(osm_lane_counts) == 1 else None
    ndw_lanes = sorted({sign.lane for sign in gantry.signs if sign.lane is not None})
    lane_numbers_consistent = bool(ndw_lanes) and ndw_lanes == list(range(1, len(ndw_lanes) + 1))
    lane_count_mismatch = bool(ndw_lanes) and (
        osm_lane_count is None or osm_lane_count != len(ndw_lanes) or not lane_numbers_consistent
    )
    all_have_direction = all(sign.bearing is not None for sign in gantry.signs)
    all_have_exact_ref = all(
        road_ref_quality(sign, selected_candidates[sign.uuid]) == "exact"
        for sign in gantry.signs
        if sign.uuid in selected_candidates
    )
    complete_coverage = len(selected_candidates) == len(gantry.signs)
    confidence = (
        "high"
        if not ambiguous and complete_coverage and all_have_direction and all_have_exact_ref
        else "medium"
        if not ambiguous and complete_coverage
        else "low"
    )
    shared_diagnostics = {
        "coverage": len(selected_candidates),
        "gantry_size": len(gantry.signs),
        "winner": {
            "traversal_id": f"{winner.key[0]}@{winner.key[1]}",
            "mean_distance_m": round(winner.mean_distance, 3),
            "mean_bearing_error_deg": (
                round(winner.mean_bearing_error, 3)
                if winner.mean_bearing_error is not None
                else None
            ),
        },
        "runner_up": (
            {
                "traversal_id": f"{runner_up.key[0]}@{runner_up.key[1]}",
                "mean_distance_m": round(runner_up.mean_distance, 3),
                "mean_bearing_error_deg": (
                    round(runner_up.mean_bearing_error, 3)
                    if runner_up.mean_bearing_error is not None
                    else None
                ),
            }
            if runner_up
            else None
        ),
        "ambiguous": ambiguous,
        "ndw_lane_numbers": ndw_lanes,
        "osm_lane_count": osm_lane_count,
        "lane_count_mismatch": lane_count_mismatch,
    }

    outputs = []
    for sign in gantry.signs:
        candidate = selected_candidates.get(sign.uuid)
        if candidate is None or ambiguous:
            outputs.append(
                _failure_match(
                    sign,
                    gantry.gantry_id,
                    all_candidate_count,
                    "bearing_ambiguous" if ambiguous else "no_major_road",
                    shared_diagnostics,
                )
            )
            continue
        # The source point is often the physical gantry anchor rather than the
        # exact lane centreline.  Once the gantry-wide counts/order agree, use
        # the travel-relative lane number for the anchor and applicability
        # link; nearest-lane distance alone must not silently move lane 5 to
        # lane 3 just because the sign point is offset from the carriageway.
        lane_candidate = None
        if not lane_count_mismatch and sign.lane is not None:
            lane_candidate = min(
                (
                    item
                    for item in candidates_by_uuid.get(sign.uuid, ())
                    if (item.segment_id, item.direction) == winner.key
                    and item.lane_nr == sign.lane
                    and road_ref_quality(sign, item) != "conflict"
                    and (
                        bearing_error(sign, item) is None
                        or bearing_error(sign, item) <= bearing_tolerance_deg
                    )
                ),
                key=lambda item: (item.distance_m, item.lane_id),
                default=None,
            )
        if lane_candidate is not None:
            candidate = lane_candidate
        applies_to_lane_id = None
        lane_mapping_missing = (
            not lane_count_mismatch and sign.lane is not None and lane_candidate is None
        )
        if not lane_count_mismatch and lane_candidate is not None:
            applies_to_lane_id = candidate.lane_id
        outputs.append(
            MatrixSignMatch(
                uuid=sign.uuid,
                gantry_id=gantry.gantry_id,
                status="matched",
                confidence=confidence,
                method="gantry_consensus",
                failure_reason=(
                    "lane_count_mismatch"
                    if lane_count_mismatch
                    else "lane_mapping_missing"
                    if lane_mapping_missing
                    else None
                ),
                candidate_count=all_candidate_count,
                road_id=candidate.road_id,
                segment_id=candidate.segment_id,
                direction=candidate.direction,
                anchor_lane_id=candidate.lane_id,
                applies_to_lane_id=applies_to_lane_id,
                position_fraction=candidate.position_fraction,
                matched_point=candidate.projected,
                source_distance_m=candidate.distance_m,
                bearing_error_deg=bearing_error(sign, candidate),
                road_ref_quality=road_ref_quality(sign, candidate),
                diagnostics=shared_diagnostics,
            )
        )
    return tuple(outputs)
