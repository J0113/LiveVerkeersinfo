"""Fail-closed lane-level live-state aggregation.

Lane numbering is only transferable from a measurement site to an OSM
carriageway when both sources explicitly report the same lane count *and* the
source lane ordering has been independently verified against OSM's
left-to-right travel-direction order. The carriageway aggregate can still be
used when that stricter condition fails.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_SPEED_KMH = 300.0
MAX_FUTURE_CLOCK_SKEW_S = 30


def build_lane_speed_states(
    lane_count: int | None,
    observations: Iterable[Mapping[str, Any]],
    *,
    lane_order_verified: bool = False,
    now: datetime | None = None,
    stale_after_s: int = 600,
) -> list[dict[str, Any]]:
    """Return one deterministic state per safely transferable lane.

    Required observation keys are ``lane``, ``site_lane_count``,
    ``speed_kmh`` and ``measured_at``. ``confidence`` is optional. Invalid,
    unnumbered, differently counted and out-of-range observations are ignored.
    Zero is a valid measured speed.
    """
    if not lane_order_verified or not isinstance(lane_count, int) or lane_count <= 0:
        return []
    reference_time = _utc(now or datetime.now(timezone.utc))
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observations:
        lane = _positive_int(observation.get("lane"))
        site_lane_count = _positive_int(observation.get("site_lane_count"))
        if lane is None or site_lane_count != lane_count or lane > lane_count:
            continue
        grouped[lane].append(observation)

    states: list[dict[str, Any]] = []
    for lane in sorted(grouped):
        lane_observations = grouped[lane]
        valid_observations = [
            item
            for item in lane_observations
            if _speed_number(item.get("speed_kmh")) is not None
        ]
        fresh = [
            item
            for item in valid_observations
            if isinstance(item.get("measured_at"), datetime)
            and -timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_S)
            <= reference_time - _utc(item["measured_at"])
            <= timedelta(seconds=stale_after_s)
        ]
        timestamp_observations = [
            item
            for item in valid_observations
            if isinstance(item.get("measured_at"), datetime)
            and reference_time - _utc(item["measured_at"])
            >= -timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_S)
        ]
        observed_at = max(
            (_utc(item["measured_at"]) for item in (fresh or timestamp_observations)),
            default=None,
        )
        values = [_speed_number(item.get("speed_kmh")) for item in fresh]
        speed = round(float(statistics.median(values)), 1) if values else None
        confidences = [
            value
            for item in fresh
            if (value := _bounded_confidence(item.get("confidence"))) is not None
        ]
        states.append(
            {
                "lane": lane,
                "speed_kmh": speed,
                "speed_method": "measured" if speed is not None else "unknown",
                "speed_observed_at": _iso(observed_at),
                "speed_valid_until": (
                    _iso(observed_at + timedelta(seconds=stale_after_s))
                    if observed_at
                    else None
                ),
                "speed_confidence": round(min(confidences), 3) if confidences else 0.0,
                "speed_sample_count": len(fresh),
                "speed_stale": not fresh,
            }
        )
    return states


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _bounded_confidence(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and 0 <= parsed <= 1 else None


def _speed_number(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and 0 <= parsed <= MAX_SPEED_KMH else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
