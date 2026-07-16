"""Fail-closed binding of live road objects to the directed OSM graph.

This module intentionally contains no database or API code.  Candidate lookup
belongs in a bounded PostGIS query; the small resulting candidate set can then
be evaluated here.  A source bearing is mandatory because accepting an object
on geometry alone can leak a matrix signal or DRIP to the opposite carriageway.

Lane scope is deliberately asymmetric:

* an MSI may retain its NDW source lane and, only after the source ordering has
  been verified, expose the corresponding canonical lane;
* a DRIP is a carriageway/path object and can never be assigned to a lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping, Sequence

from ndwinfo.config import settings
from ndwinfo.matching.source_binding import (
    angle_diff,
    finite_number,
    normalize_carriageway,
    normalize_road_refs,
)

LiveObjectType = Literal["msi", "drip"]
BindingStatus = Literal["accepted", "ambiguous", "rejected"]
LaneScopeStatus = Literal["not_applicable", "source_only", "canonical"]
ALGORITHM_VERSION = "ndw-osm-live-object-v1"
PERSISTED_SOURCE_TYPES: Mapping[LiveObjectType, str] = {
    "msi": "ndw_msi",
    "drip": "ndw_drip",
}


@dataclass(frozen=True)
class LiveObjectTraits:
    source_type: LiveObjectType
    source_id: str
    bearing: float | None
    road: str | None = None
    carriageway: str | None = None
    lane: int | None = None
    observed_at: datetime | None = None
    ingested_at: datetime | None = None
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LiveRoadCandidate:
    internal_segment_id: str
    road_number: str | None
    carriageway_ref: str | None
    lanes: int | None
    distance_m: float
    bearing: float


@dataclass(frozen=True)
class LiveObjectBindingDecision:
    status: BindingStatus
    source_type: LiveObjectType
    source_id: str
    internal_segment_id: str | None
    distance_m: float | None
    heading_delta_deg: float | None
    score: float | None
    margin: float | None
    confidence: float
    source_lane: int | None
    canonical_lane: int | None
    lane_scope_status: LaneScopeStatus
    observed_at: datetime | None
    ingested_at: datetime | None
    valid_until: datetime | None
    stale: bool
    provenance: Mapping[str, object]

    @property
    def state_usable(self) -> bool:
        """Whether a consumer may expose the live state, not just its location."""
        return self.status == "accepted" and not self.stale


@dataclass(frozen=True)
class DripPathRelevance:
    relevant: bool
    path_index: int | None
    reason: Literal[
        "current_segment", "confirmed_ahead", "unbound", "off_confirmed_path"
    ]


def decide_live_object_binding(
    source: LiveObjectTraits,
    candidates: Sequence[LiveRoadCandidate],
    *,
    lane_order_verified: bool = False,
    reference_time: datetime | None = None,
    stale_after_s: int | None = None,
    max_distance_m: float | None = None,
    max_heading_delta_deg: float | None = None,
    min_confidence: float | None = None,
    min_margin: float | None = None,
) -> LiveObjectBindingDecision:
    """Bind one MSI or DRIP after hard direction and metadata filtering.

    ``candidates`` must already be produced by a bounded spatial lookup.  This
    function repeats the authoritative metre limit and never falls back to an
    undirected nearest segment.  Staleness applies to the live state; it does
    not erase a useful, versioned physical-location binding.
    """
    if source.source_type not in {"msi", "drip"}:
        raise ValueError(f"unsupported live object type: {source.source_type!r}")

    now = _utc(reference_time) or datetime.now(timezone.utc)
    stale_seconds = (
        settings.road_speed_stale_after_s if stale_after_s is None else stale_after_s
    )
    observed_at = _utc(source.observed_at)
    ingested_at = _utc(source.ingested_at)
    valid_until = (
        observed_at + timedelta(seconds=max(0, stale_seconds))
        if observed_at is not None
        else None
    )
    # A missing timestamp is not evidence of a current display state.  Reject
    # excessive future timestamps as unusable as well as expired snapshots.
    stale = (
        observed_at is None
        or observed_at > now + timedelta(seconds=30)
        or valid_until is None
        or valid_until <= now
    )

    source_bearing = finite_number(source.bearing)
    if source_bearing is None:
        return _decision(
            source, observed_at, ingested_at, valid_until, stale, status="rejected"
        )

    max_distance = (
        settings.source_binding_max_distance_m
        if max_distance_m is None
        else max_distance_m
    )
    heading_limit = (
        settings.source_binding_max_heading_delta_deg
        if max_heading_delta_deg is None
        else max_heading_delta_deg
    )
    confidence_limit = (
        settings.source_binding_min_confidence
        if min_confidence is None
        else min_confidence
    )
    margin_limit = (
        settings.source_binding_min_margin if min_margin is None else min_margin
    )
    if max_distance <= 0:
        raise ValueError("max_distance_m must be positive")
    if not 0 <= heading_limit <= 180:
        raise ValueError("max_heading_delta_deg must be between 0 and 180")
    source_refs = normalize_road_refs(source.road)
    source_side = normalize_carriageway(source.carriageway)
    source_lane = _valid_lane_number(source.lane)
    # Formatting variants are normalized, but an explicit label whose meaning
    # is unknown must not silently degrade to "metadata absent".
    if (source.road and not source_refs) or (
        source.carriageway and source_side is None
    ):
        return _decision(
            source, observed_at, ingested_at, valid_until, stale, status="rejected"
        )
    scored: list[tuple[float, LiveRoadCandidate, float, bool, bool]] = []

    for candidate in candidates:
        distance = finite_number(candidate.distance_m)
        candidate_bearing = finite_number(candidate.bearing)
        if (
            distance is None
            or distance < 0
            or distance > max_distance
            or candidate_bearing is None
        ):
            continue

        candidate_refs = normalize_road_refs(candidate.road_number)
        road_match = bool(source_refs and candidate_refs and source_refs & candidate_refs)
        if source_refs and candidate_refs and not road_match:
            continue

        candidate_side = normalize_carriageway(candidate.carriageway_ref)
        side_match = bool(source_side and candidate_side and source_side == candidate_side)
        if source_side not in {None, "L", "R"} and not side_match:
            # A specific DVK carriageway letter carries more information than
            # travel side. It may only bind to the same explicit OSM reference.
            continue
        if source_side and candidate_side and not side_match:
            continue

        delta = angle_diff(source_bearing, candidate_bearing)
        if delta > heading_limit:
            continue

        # An MSI lane outside a known directional carriageway count is an
        # explicit conflict.  Missing lane count still permits segment binding,
        # but cannot produce canonical lane scope.
        if (
            source.source_type == "msi"
            and source_lane is not None
            and candidate.lanes is not None
            and not 1 <= source_lane <= candidate.lanes
        ):
            continue

        score = distance + delta * 0.5
        score -= 20.0 if road_match else 0.0
        score -= 10.0 if side_match else 0.0
        scored.append((score, candidate, delta, road_match, side_match))

    scored.sort(key=lambda row: (row[0], row[1].internal_segment_id))
    if not scored:
        return _decision(
            source, observed_at, ingested_at, valid_until, stale, status="rejected"
        )

    score, candidate, delta, road_match, side_match = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else score + 100.0
    margin = second_score - score
    confidence = _confidence(
        distance_m=float(candidate.distance_m),
        heading_delta_deg=delta,
        max_distance_m=max_distance,
        road_match=road_match,
        carriageway_match=side_match,
        margin=margin,
    )
    accepted = confidence >= confidence_limit and margin >= margin_limit
    status: BindingStatus = "accepted" if accepted else "ambiguous"
    canonical_lane, lane_scope_status = _lane_scope(
        source,
        candidate if accepted else None,
        lane_order_verified=lane_order_verified,
    )
    return _decision(
        source,
        observed_at,
        ingested_at,
        valid_until,
        stale,
        status=status,
        internal_segment_id=candidate.internal_segment_id if accepted else None,
        distance_m=round(float(candidate.distance_m), 2),
        heading_delta_deg=round(delta, 2),
        score=round(score, 3),
        margin=round(margin, 3),
        confidence=confidence,
        canonical_lane=canonical_lane,
        lane_scope_status=lane_scope_status,
    )


def assess_drip_path_relevance(
    binding: LiveObjectBindingDecision,
    current_segment_id: str | None,
    confirmed_ahead_segment_ids: Sequence[str],
) -> DripPathRelevance:
    """Return relevance only for a DRIP on the backend-confirmed directed path."""
    if binding.source_type != "drip":
        raise ValueError("path relevance is only defined for DRIP bindings")
    if binding.status != "accepted" or binding.internal_segment_id is None:
        return DripPathRelevance(False, None, "unbound")
    if binding.internal_segment_id == current_segment_id:
        return DripPathRelevance(True, 0, "current_segment")
    for index, segment_id in enumerate(confirmed_ahead_segment_ids, start=1):
        if binding.internal_segment_id == segment_id:
            return DripPathRelevance(True, index, "confirmed_ahead")
    return DripPathRelevance(False, None, "off_confirmed_path")


def _lane_scope(
    source: LiveObjectTraits,
    candidate: LiveRoadCandidate | None,
    *,
    lane_order_verified: bool,
) -> tuple[int | None, LaneScopeStatus]:
    if source.source_type == "drip":
        return None, "not_applicable"
    source_lane = _valid_lane_number(source.lane)
    if source_lane is None:
        return None, "source_only"
    if (
        candidate is not None
        and lane_order_verified
        and candidate.lanes is not None
        and 1 <= source_lane <= candidate.lanes
    ):
        return source_lane, "canonical"
    return None, "source_only"


def _confidence(
    *,
    distance_m: float,
    heading_delta_deg: float,
    max_distance_m: float,
    road_match: bool,
    carriageway_match: bool,
    margin: float,
) -> float:
    distance = max(0.0, 1.0 - distance_m / max_distance_m)
    heading = max(0.0, 1.0 - heading_delta_deg / 90.0)
    confidence = (
        0.45 * distance
        + 0.30 * heading
        + 0.15 * float(road_match)
        + 0.10 * float(carriageway_match)
    )
    confidence *= min(1.0, 0.55 + max(0.0, margin) / 18.0)
    return round(max(0.0, min(confidence, 1.0)), 3)


def _decision(
    source: LiveObjectTraits,
    observed_at: datetime | None,
    ingested_at: datetime | None,
    valid_until: datetime | None,
    stale: bool,
    *,
    status: BindingStatus,
    internal_segment_id: str | None = None,
    distance_m: float | None = None,
    heading_delta_deg: float | None = None,
    score: float | None = None,
    margin: float | None = None,
    confidence: float = 0.0,
    canonical_lane: int | None = None,
    lane_scope_status: LaneScopeStatus | None = None,
) -> LiveObjectBindingDecision:
    if lane_scope_status is None:
        _, lane_scope_status = _lane_scope(
            source, None, lane_order_verified=False
        )
    return LiveObjectBindingDecision(
        status=status,
        source_type=source.source_type,
        source_id=source.source_id,
        internal_segment_id=internal_segment_id,
        distance_m=distance_m,
        heading_delta_deg=heading_delta_deg,
        score=score,
        margin=margin,
        confidence=confidence,
        source_lane=(
            _valid_lane_number(source.lane) if source.source_type == "msi" else None
        ),
        canonical_lane=canonical_lane if source.source_type == "msi" else None,
        lane_scope_status=(
            lane_scope_status if source.source_type == "msi" else "not_applicable"
        ),
        observed_at=observed_at,
        ingested_at=ingested_at,
        valid_until=valid_until,
        stale=stale,
        provenance=dict(source.provenance),
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _valid_lane_number(value: object) -> int | None:
    # Bool is an int subclass in Python but never a meaningful lane number.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 32 else None
