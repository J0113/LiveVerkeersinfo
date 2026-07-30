"""Lane-model-neutral helpers for directed lines and junction curves."""

from __future__ import annotations

import math
from collections.abc import Sequence

from shapely.geometry import LineString

JUNCTION_BOX_RADIUS_M = 25.0
MAX_TURN_ANGLE_DEG = 135.0
AMBIGUOUS_ANGLE_DELTA_DEG = 7.5
BEZIER_SAMPLES = 12

TURN_TOKEN_ANGLES: dict[str, tuple[float, float]] = {
    # Bearings use a Cartesian east=0/north=90 frame, so counter-clockwise
    # (driver-left) turns are positive and driver-right turns are negative.
    "sharp_left": (55.0, 135.0),
    "left": (20.0, 135.0),
    "slight_left": (5.0, 60.0),
    "through": (-25.0, 25.0),
    "slight_right": (-60.0, -5.0),
    "right": (-135.0, -20.0),
    "sharp_right": (-135.0, -55.0),
    "reverse": (135.0, 180.0),
}


def unit_vector(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Return the unit vector from *a* to *b*."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        raise ValueError("cannot derive a direction from coincident points")
    return dx / length, dy / length


def bearing_deg(a: Sequence[float], b: Sequence[float]) -> float:
    """Planar compass-like bearing: east=0, north=90, in [-180, 180)."""
    return normalize_angle_deg(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])))


def normalize_angle_deg(angle: float) -> float:
    """Normalize an angle to [-180, 180)."""
    return (angle + 180.0) % 360.0 - 180.0


def angle_delta_deg(incoming: float, outgoing: float) -> float:
    """Signed smallest turn from incoming bearing to outgoing bearing."""
    return normalize_angle_deg(outgoing - incoming)


def turn_token_matches(token: str, angle: float) -> bool:
    """Whether a normalized turn token is compatible with a signed angle."""
    normalized = token.strip().lower()
    if normalized == "reverse":
        return abs(angle) >= TURN_TOKEN_ANGLES[normalized][0]
    bounds = TURN_TOKEN_ANGLES.get(normalized)
    return bool(bounds and bounds[0] <= angle <= bounds[1])


def bounded_cubic_bezier(
    start: Sequence[float],
    start_direction: Sequence[float],
    end: Sequence[float],
    end_direction: Sequence[float],
    *,
    samples: int = BEZIER_SAMPLES,
    max_handle_m: float = 15.0,
    start_handle_m: float | None = None,
    end_handle_m: float | None = None,
) -> LineString:
    """Build a smooth cubic curve with handles bounded by endpoint span."""
    if samples < 2:
        raise ValueError("samples must be at least 2")
    span = math.hypot(end[0] - start[0], end[1] - start[1])
    if span == 0:
        return LineString([tuple(start[:2]), tuple(end[:2])])
    sx, sy = unit_vector((0.0, 0.0), start_direction)
    ex, ey = unit_vector((0.0, 0.0), end_direction)
    default_handle = min(max_handle_m, span * 0.45)
    start_handle = min(
        max_handle_m,
        max(0.0, default_handle if start_handle_m is None else start_handle_m),
    )
    end_handle = min(
        max_handle_m,
        max(0.0, default_handle if end_handle_m is None else end_handle_m),
    )
    p0 = (start[0], start[1])
    p1 = (p0[0] + sx * start_handle, p0[1] + sy * start_handle)
    p3 = (end[0], end[1])
    p2 = (p3[0] - ex * end_handle, p3[1] - ey * end_handle)
    coordinates = []
    for index in range(samples + 1):
        t = index / samples
        u = 1.0 - t
        coordinates.append(
            (
                u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
            )
        )
    return LineString(coordinates)
