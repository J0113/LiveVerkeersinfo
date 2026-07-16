"""Lightweight, fail-closed speed assignment over a directed OSM graph.

The module is intentionally independent of SQLAlchemy.  Callers provide a
bounded set of directed segments plus their *complete* legal predecessor and
successor ids.  A speed may cross a segment boundary only when both sides say
that boundary is one-to-one and the road/carriageway/direction identity stays
the same.  Missing topology therefore reduces coverage instead of inventing a
route through a fork or merge.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

MAX_SPEED_KMH = 300.0
MAX_FUTURE_CLOCK_SKEW_S = 30


@dataclass(frozen=True, slots=True)
class SpeedSegment:
    internal_segment_id: str
    length_m: float
    road_ref: str | None
    carriageway_ref: str | None
    travel_direction: str
    predecessor_ids: tuple[str, ...] = ()
    successor_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpeedObservation:
    source_id: str
    segment_id: str
    offset_m: float
    speed_kmh: float
    observed_at: datetime
    confidence: float
    source: str = "NDW"
    binding_status: str = "accepted"


@dataclass(frozen=True, slots=True)
class _Anchor:
    segment_id: str
    position_m: float
    speed_kmh: float
    confidence: float
    source: str
    source_ids: tuple[str, ...]
    observed_at: datetime
    valid_until: datetime
    sample_count: int


def assign_speed_states(
    segments: Iterable[SpeedSegment],
    observations: Iterable[SpeedObservation],
    *,
    now: datetime | None = None,
    stale_after_s: int = 600,
    propagation_limit_m: float = 1500.0,
    interpolation_limit_m: float = 5000.0,
) -> dict[str, dict]:
    """Assign a deterministic speed state to every supplied segment.

    Direct observations win.  Interior gaps between two compatible fresh
    anchors are linearly interpolated.  Before the first and after the last
    anchor, a value is propagated only up to ``propagation_limit_m``.  A gap
    between two anchors that exceeds ``interpolation_limit_m`` remains unknown;
    one-sided propagation is never used inside such a two-sided gap.

    Complexity is O(S + O log O), with O(S + O) temporary memory.  In normal
    use observations are already grouped by short, bounded road corridors.
    """
    if stale_after_s <= 0 or propagation_limit_m < 0 or interpolation_limit_m < 0:
        raise ValueError("Speed-model limits must be non-negative and freshness positive")

    segment_by_id = _validate_segments(segments)
    reference_time = _utc(now or datetime.now(timezone.utc))
    states = {segment_id: _unknown_state() for segment_id in segment_by_id}
    if not segment_by_id:
        return states

    fresh_by_segment: dict[str, list[SpeedObservation]] = defaultdict(list)
    stale_by_segment: dict[str, list[SpeedObservation]] = defaultdict(list)
    for observation in observations:
        segment = segment_by_id.get(observation.segment_id)
        if (
            segment is None
            or not _valid_observation(observation)
            or observation.offset_m > segment.length_m
        ):
            continue
        age = reference_time - _utc(observation.observed_at)
        if age < -timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_S):
            continue
        if age <= timedelta(seconds=stale_after_s):
            fresh_by_segment[observation.segment_id].append(observation)
        else:
            stale_by_segment[observation.segment_id].append(observation)

    for chain in _one_to_one_chains(segment_by_id):
        starts: dict[str, float] = {}
        cursor = 0.0
        for segment_id in chain:
            starts[segment_id] = cursor
            cursor += segment_by_id[segment_id].length_m

        anchors = [
            _aggregate_anchor(
                segment_id,
                starts[segment_id],
                segment_by_id[segment_id],
                fresh_by_segment[segment_id],
                stale_after_s,
            )
            for segment_id in chain
            if fresh_by_segment[segment_id]
        ]
        anchors.sort(key=lambda anchor: (anchor.position_m, anchor.segment_id))

        for anchor in anchors:
            states[anchor.segment_id] = _anchor_state(anchor)

        if anchors:
            _fill_chain_states(
                chain,
                segment_by_id,
                starts,
                anchors,
                states,
                propagation_limit_m,
                interpolation_limit_m,
            )

    # Preserve stale provenance only on otherwise unknown direct segments.  It
    # is never used as an anchor and therefore cannot color adjacent roads.
    for segment_id, stale in stale_by_segment.items():
        if states[segment_id]["speed_method"] != "unknown":
            continue
        latest = max(stale, key=lambda item: _utc(item.observed_at))
        states[segment_id] = {
            **_unknown_state(),
            "speed_source": latest.source,
            "speed_source_ids": sorted({item.source_id for item in stale}),
            "speed_observed_at": _iso(_utc(latest.observed_at)),
            "speed_valid_until": _iso(
                _utc(latest.observed_at) + timedelta(seconds=stale_after_s)
            ),
            "speed_sample_count": len(stale),
            "speed_stale": True,
        }
    return states


def _validate_segments(segments: Iterable[SpeedSegment]) -> dict[str, SpeedSegment]:
    result: dict[str, SpeedSegment] = {}
    for segment in segments:
        if not segment.internal_segment_id or segment.internal_segment_id in result:
            raise ValueError("Segment ids must be non-empty and unique")
        if not _finite(segment.length_m) or segment.length_m <= 0:
            raise ValueError("Segment length must be finite and positive")
        if not segment.travel_direction:
            raise ValueError("Directed segments require travel_direction")
        result[segment.internal_segment_id] = segment
    return result


def _one_to_one_chains(segments: dict[str, SpeedSegment]) -> list[tuple[str, ...]]:
    successor: dict[str, str] = {}
    predecessor: dict[str, str] = {}
    for segment_id, segment in segments.items():
        if len(segment.successor_ids) != 1:
            continue
        next_id = segment.successor_ids[0]
        next_segment = segments.get(next_id)
        if (
            next_segment is None
            or len(next_segment.predecessor_ids) != 1
            or next_segment.predecessor_ids[0] != segment_id
            or not _compatible(segment, next_segment)
        ):
            continue
        successor[segment_id] = next_id
        predecessor[next_id] = segment_id

    chains: list[tuple[str, ...]] = []
    visited: set[str] = set()
    for start in sorted(segments):
        if start in predecessor or start in visited:
            continue
        chain: list[str] = []
        current: str | None = start
        while current is not None and current not in visited:
            visited.add(current)
            chain.append(current)
            current = successor.get(current)
        chains.append(tuple(chain))

    # A closed directed cycle has no safe linear origin.  Keep each member
    # isolated instead of choosing an arbitrary seam and propagating across it.
    chains.extend((segment_id,) for segment_id in sorted(segments) if segment_id not in visited)
    return chains


def _compatible(left: SpeedSegment, right: SpeedSegment) -> bool:
    return (
        _identity(left.road_ref) is not None
        and _identity(left.road_ref) == _identity(right.road_ref)
        and _identity(left.carriageway_ref) == _identity(right.carriageway_ref)
        and left.travel_direction == right.travel_direction
    )


def _aggregate_anchor(
    segment_id: str,
    start_m: float,
    segment: SpeedSegment,
    observations: list[SpeedObservation],
    stale_after_s: int,
) -> _Anchor:
    observed = [_utc(item.observed_at) for item in observations]
    oldest = min(observed)
    return _Anchor(
        segment_id=segment_id,
        position_m=start_m + statistics.median(item.offset_m for item in observations),
        speed_kmh=round(statistics.median(item.speed_kmh for item in observations), 1),
        confidence=min(item.confidence for item in observations),
        source=_combined_source(observations),
        source_ids=tuple(sorted({item.source_id for item in observations})),
        observed_at=oldest,
        valid_until=oldest + timedelta(seconds=stale_after_s),
        sample_count=len(observations),
    )


def _fill_chain_states(
    chain: tuple[str, ...],
    segments: dict[str, SpeedSegment],
    starts: dict[str, float],
    anchors: list[_Anchor],
    states: dict[str, dict],
    propagation_limit_m: float,
    interpolation_limit_m: float,
) -> None:
    first, last = anchors[0], anchors[-1]
    for segment_id in chain:
        if states[segment_id]["speed_method"] == "measured":
            continue
        midpoint = starts[segment_id] + segments[segment_id].length_m / 2
        if midpoint < first.position_m:
            distance = first.position_m - midpoint
            if distance <= propagation_limit_m:
                states[segment_id] = _derived_state(
                    first,
                    None,
                    midpoint,
                    "propagated",
                    distance,
                    propagation_limit_m,
                )
            continue
        if midpoint > last.position_m:
            distance = midpoint - last.position_m
            if distance <= propagation_limit_m:
                states[segment_id] = _derived_state(
                    last,
                    None,
                    midpoint,
                    "propagated",
                    distance,
                    propagation_limit_m,
                )
            continue

        left = max(
            (anchor for anchor in anchors if anchor.position_m <= midpoint),
            key=lambda item: item.position_m,
        )
        right = min(
            (anchor for anchor in anchors if anchor.position_m >= midpoint),
            key=lambda item: item.position_m,
        )
        if left.segment_id == right.segment_id:
            continue
        gap = right.position_m - left.position_m
        if gap <= 0 or gap > interpolation_limit_m:
            continue
        states[segment_id] = _derived_state(
            left,
            right,
            midpoint,
            "interpolated",
            gap,
            interpolation_limit_m,
        )


def _anchor_state(anchor: _Anchor) -> dict:
    return {
        "speed_kmh": anchor.speed_kmh,
        "speed_method": "measured",
        "speed_confidence": round(anchor.confidence, 3),
        "speed_source": anchor.source,
        "speed_source_ids": list(anchor.source_ids),
        "speed_observed_at": _iso(anchor.observed_at),
        "speed_valid_until": _iso(anchor.valid_until),
        "speed_sample_count": anchor.sample_count,
        "speed_stale": False,
    }


def _derived_state(
    left: _Anchor,
    right: _Anchor | None,
    position_m: float,
    method: str,
    distance: float,
    limit: float,
) -> dict:
    if right is None:
        speed = left.speed_kmh
        confidence = left.confidence * 0.7 * (1 - 0.5 * distance / max(limit, 1.0))
        sources = left.source_ids
        source = left.source
        observed_at = left.observed_at
        valid_until = left.valid_until
        sample_count = left.sample_count
    else:
        ratio = (position_m - left.position_m) / (right.position_m - left.position_m)
        speed = left.speed_kmh + ratio * (right.speed_kmh - left.speed_kmh)
        confidence = min(left.confidence, right.confidence) * 0.85
        sources = tuple(sorted(set(left.source_ids) | set(right.source_ids)))
        source = left.source if left.source == right.source else "multiple"
        observed_at = min(left.observed_at, right.observed_at)
        valid_until = min(left.valid_until, right.valid_until)
        sample_count = left.sample_count + right.sample_count
    return {
        "speed_kmh": round(speed, 1),
        "speed_method": method,
        "speed_confidence": round(max(0.0, min(confidence, 1.0)), 3),
        "speed_source": source,
        "speed_source_ids": list(sources),
        "speed_observed_at": _iso(observed_at),
        "speed_valid_until": _iso(valid_until),
        "speed_sample_count": sample_count,
        "speed_stale": False,
    }


def _unknown_state() -> dict:
    return {
        "speed_kmh": None,
        "speed_method": "unknown",
        "speed_confidence": 0.0,
        "speed_source": None,
        "speed_source_ids": [],
        "speed_observed_at": None,
        "speed_valid_until": None,
        "speed_sample_count": 0,
        "speed_stale": True,
    }


def _valid_observation(observation: SpeedObservation) -> bool:
    return (
        observation.binding_status == "accepted"
        and bool(observation.source_id)
        and bool(observation.source)
        and isinstance(observation.observed_at, datetime)
        and _finite(observation.offset_m)
        and observation.offset_m >= 0
        and _finite(observation.speed_kmh)
        and 0 <= observation.speed_kmh <= MAX_SPEED_KMH
        and _finite(observation.confidence)
        and 0 <= observation.confidence <= 1
    )


def _combined_source(observations: list[SpeedObservation]) -> str:
    sources = {item.source for item in observations}
    return next(iter(sources)) if len(sources) == 1 else "multiple"


def _identity(value: str | None) -> str | None:
    normalized = str(value).strip().upper() if value is not None else ""
    return normalized or None


def _finite(value: float) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
