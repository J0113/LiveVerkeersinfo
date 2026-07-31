"""Conservative directed point matching for DRIP/VMS panels."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ndwinfo.road_matching.evidence import (
    angular_difference_deg,
    normalize_road_ref,
    normalize_road_refs,
)
from ndwinfo.road_matching.types import DripSign, DripSignMatch, LaneCandidate

DRIP_BEARING_TOLERANCE_DEG = 45.0
# Away from the panel's own gantry, proximity stops being evidence, so the
# tail is only accepted on a bearing that actually agrees with the traversal.
DRIP_EXTENDED_BEARING_TOLERANCE_DEG = 20.0
DRIP_PRIMARY_RADIUS_M = 60.0
DRIP_EXTENDED_RADIUS_M = 500.0
DRIP_AMBIGUITY_DISTANCE_M = 2.0
DRIP_AMBIGUITY_BEARING_MARGIN_DEG = 5.0

_ROAD_HINT_RE = re.compile(r"(?<![A-Z0-9])([AN]\s*[-/]?\s*\d{1,3})(?!\d)", re.IGNORECASE)


@dataclass(frozen=True)
class _TraversalChoice:
    key: tuple[str, str]
    candidate: LaneCandidate
    bearing_error_deg: float | None
    road_hint_quality: str


def description_road_hint(description: str | None) -> str | None:
    """Extract a route-number hint without treating panel text as a contract."""

    if not description:
        return None
    match = _ROAD_HINT_RE.search(description)
    return normalize_road_ref(match.group(1)) if match else None


def drip_source_key(drip: DripSign) -> str:
    return drip.source_key


def _road_hint_quality(road_hint: str | None, candidate: LaneCandidate) -> str:
    candidate_refs = normalize_road_refs(candidate.ref)
    if not road_hint or not candidate_refs:
        return "absent"
    return "exact" if road_hint in candidate_refs else "conflict"


def _distance_first_key(choice: _TraversalChoice) -> tuple:
    return (
        choice.candidate.distance_m,
        0 if choice.road_hint_quality == "exact" else 1,
        choice.bearing_error_deg if choice.bearing_error_deg is not None else 180.0,
        choice.key[0],
        choice.key[1],
        choice.candidate.lane_id,
    )


def _alignment_first_key(choice: _TraversalChoice) -> tuple:
    return (
        choice.bearing_error_deg if choice.bearing_error_deg is not None else 180.0,
        0 if choice.road_hint_quality == "exact" else 1,
        choice.candidate.distance_m,
        choice.key[0],
        choice.key[1],
        choice.candidate.lane_id,
    )


def _rank_choices(
    choices: list[_TraversalChoice],
    *,
    tie_distance_m: float = DRIP_AMBIGUITY_DISTANCE_M,
) -> list[_TraversalChoice]:
    """Rank by proximity, but let bearing decide inside a distance tie.

    At a junction the two adjoining traversals both pass under the panel, so
    their residual distances differ by centimetres -- noise, not evidence.
    Ranking that band by proximity alone silently prefers the worse-aligned
    traversal, and :func:`_ambiguous` would then read the bearing gap as
    "direction already resolved".  Inside the band bearing decides instead,
    so both steps judge on the same evidence.
    """

    if not choices:
        return []
    nearest = min(choice.candidate.distance_m for choice in choices)
    tied = [c for c in choices if c.candidate.distance_m <= nearest + tie_distance_m]
    rest = [c for c in choices if c.candidate.distance_m > nearest + tie_distance_m]
    return sorted(tied, key=_alignment_first_key) + sorted(rest, key=_distance_first_key)


def _choose_lane(
    drip: DripSign,
    candidates: Iterable[LaneCandidate],
    *,
    road_hint: str | None,
) -> tuple[LaneCandidate | None, str | None]:
    compatible: list[tuple[LaneCandidate, float | None, str]] = []
    for candidate in candidates:
        error = angular_difference_deg(drip.bearing, candidate.bearing_deg)
        if error is not None and error > DRIP_BEARING_TOLERANCE_DEG:
            continue
        compatible.append((candidate, error, _road_hint_quality(road_hint, candidate)))
    if not compatible:
        # A traversal always arrives with at least one lane, so only the
        # bearing filter above can empty it.
        return None, "bearing_mismatch"
    selected, _error, _quality = min(
        compatible,
        key=lambda item: (
            item[0].distance_m,
            0 if item[2] == "exact" else 1,
            item[1] if item[1] is not None else 180.0,
            item[0].lane_id,
        ),
    )
    return selected, None


def _ambiguous(
    drip: DripSign,
    winner: _TraversalChoice,
    runner_up: _TraversalChoice | None,
) -> bool:
    if runner_up is None:
        return False
    if winner.key == runner_up.key:
        return False
    # An explicit route hint can distinguish a named road from a nearby road,
    # but never chooses between two directions of the same named roadway.
    if (
        winner.road_hint_quality == "exact"
        and runner_up.road_hint_quality != "exact"
    ):
        return False
    if drip.bearing is None:
        if winner.candidate.road_id == runner_up.candidate.road_id:
            return True
        return (
            abs(winner.candidate.distance_m - runner_up.candidate.distance_m)
            <= DRIP_AMBIGUITY_DISTANCE_M
        )
    distance_close = abs(
        winner.candidate.distance_m - runner_up.candidate.distance_m
    ) <= DRIP_AMBIGUITY_DISTANCE_M
    winner_error = winner.bearing_error_deg
    runner_error = runner_up.bearing_error_deg
    bearing_close = (
        winner_error is None
        or runner_error is None
        or abs(winner_error - runner_error) <= DRIP_AMBIGUITY_BEARING_MARGIN_DEG
    )
    return distance_close and bearing_close


def _failure(
    drip: DripSign,
    *,
    status: str,
    reason: str,
    candidate_count: int,
    diagnostics: dict,
) -> DripSignMatch:
    return DripSignMatch(
        source_key=drip_source_key(drip),
        controller_id=drip.controller_id,
        vms_index=drip.vms_index,
        status=status,
        confidence=None,
        method="point_road_bearing" if drip.bearing is not None else "point_proximity",
        failure_reason=reason,
        candidate_count=candidate_count,
        diagnostics=diagnostics,
    )


def match_drip(
    drip: DripSign,
    candidates: Iterable[LaneCandidate],
    *,
    primary_radius_m: float = DRIP_PRIMARY_RADIUS_M,
    extended_radius_m: float = DRIP_EXTENDED_RADIUS_M,
) -> DripSignMatch:
    """Match one DRIP to one directed traversal, failing closed on ambiguity.

    Candidate rows are normally loaded through the extended radius.  A row in
    the 60–500 m tail is accepted only when a bearing leaves one compatible
    traversal; proximity-only records in that tail remain unsupported.
    """

    road_hint = description_road_hint(drip.description)
    by_traversal: dict[tuple[str, str], list[LaneCandidate]] = {}
    raw_candidates = list(candidates)
    for candidate in raw_candidates:
        by_traversal.setdefault((candidate.segment_id, candidate.direction), []).append(candidate)

    choices: list[_TraversalChoice] = []
    rejection_reasons: set[str] = set()
    for key, traversal_candidates in by_traversal.items():
        selected, reason = _choose_lane(drip, traversal_candidates, road_hint=road_hint)
        if selected is None:
            if reason:
                rejection_reasons.add(reason)
            continue
        choices.append(
            _TraversalChoice(
                key=key,
                candidate=selected,
                bearing_error_deg=angular_difference_deg(drip.bearing, selected.bearing_deg),
                road_hint_quality=_road_hint_quality(road_hint, selected),
            )
        )

    choices = _rank_choices(choices)
    winner = choices[0] if choices else None
    runner_up = choices[1] if len(choices) > 1 else None
    diagnostics = {
        "bearing_interpretation": "travel",
        "description_road_hint": road_hint,
        "candidate_traversals": len(by_traversal),
        "compatible_traversals": len(choices),
        "primary_radius_m": primary_radius_m,
        "extended_radius_m": extended_radius_m,
        "rejected_reasons": sorted(rejection_reasons),
        "runner_up": (
            {
                "traversal_id": f"{runner_up.key[0]}@{runner_up.key[1]}",
                "distance_m": round(runner_up.candidate.distance_m, 3),
                "bearing_error_deg": (
                    round(runner_up.bearing_error_deg, 3)
                    if runner_up.bearing_error_deg is not None
                    else None
                ),
                "road_hint_quality": runner_up.road_hint_quality,
            }
            if runner_up
            else None
        ),
    }

    if winner is None:
        reason = (
            "bearing_mismatch"
            if "bearing_mismatch" in rejection_reasons
            else "no_major_road"
        )
        status = "unsupported" if not raw_candidates else "unmatched"
        return _failure(
            drip,
            status=status,
            reason=reason,
            candidate_count=len(by_traversal),
            diagnostics=diagnostics,
        )

    if _ambiguous(drip, winner, runner_up):
        diagnostics["ambiguous"] = True
        return _failure(
            drip,
            status="ambiguous",
            reason="bearing_ambiguous" if drip.bearing is not None else "direction_ambiguous",
            candidate_count=len(by_traversal),
            diagnostics=diagnostics,
        )

    distance = winner.candidate.distance_m
    if distance > extended_radius_m:
        return _failure(
            drip,
            status="unsupported",
            reason="no_major_road",
            candidate_count=len(by_traversal),
            diagnostics=diagnostics,
        )
    if distance > primary_radius_m:
        if drip.bearing is None:
            return _failure(
                drip,
                status="unsupported",
                reason="extended_requires_bearing",
                candidate_count=len(by_traversal),
                diagnostics=diagnostics,
            )
        if (
            winner.bearing_error_deg is None
            or winner.bearing_error_deg > DRIP_EXTENDED_BEARING_TOLERANCE_DEG
        ):
            return _failure(
                drip,
                status="unsupported",
                reason="extended_bearing_too_weak",
                candidate_count=len(by_traversal),
                diagnostics=diagnostics,
            )

    confidence = (
        "high"
        if drip.bearing is not None and distance <= primary_radius_m
        else "medium"
        if drip.bearing is not None
        else "low"
    )
    diagnostics["ambiguous"] = False
    diagnostics["search_tier"] = "primary" if distance <= primary_radius_m else "extended"
    diagnostics["road_hint_quality"] = winner.road_hint_quality
    candidate = winner.candidate
    return DripSignMatch(
        source_key=drip_source_key(drip),
        controller_id=drip.controller_id,
        vms_index=drip.vms_index,
        status="matched",
        confidence=confidence,
        method="point_road_bearing" if drip.bearing is not None else "point_proximity",
        failure_reason=None,
        candidate_count=len(by_traversal),
        road_id=candidate.road_id,
        segment_id=candidate.segment_id,
        direction=candidate.direction,
        anchor_lane_id=candidate.lane_id,
        position_fraction=candidate.position_fraction,
        matched_point=candidate.projected,
        source_distance_m=candidate.distance_m,
        bearing_error_deg=winner.bearing_error_deg,
        road_ref_quality=(
            # A conflicting panel route stays visible downstream: DRIP text
            # often names the road it informs about rather than the one it
            # stands beside, and "corridor" would read as unchecked instead.
            winner.road_hint_quality
            if winner.road_hint_quality in {"exact", "conflict"}
            else "corridor"
            if candidate.ref
            else "absent"
        ),
        diagnostics=diagnostics,
    )


def match_drip_results(
    drips: Iterable[DripSign],
    candidates_by_key: dict[str, Iterable[LaneCandidate]],
    *,
    primary_radius_m: float = DRIP_PRIMARY_RADIUS_M,
    extended_radius_m: float = DRIP_EXTENDED_RADIUS_M,
) -> list[DripSignMatch]:
    """Match a loaded DRIP snapshot in stable source-key order."""

    matches = [
        match_drip(
            drip,
            candidates_by_key.get(drip_source_key(drip), ()),
            primary_radius_m=primary_radius_m,
            extended_radius_m=extended_radius_m,
        )
        for drip in drips
    ]
    return sorted(matches, key=lambda match: match.source_key)
