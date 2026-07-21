"""Flitspalen.nl static speed camera parser.

Response: {"result": [...]}, one flat list mixing NL/B/D. Hard-filtered to
land == "NL" and status == "A" (actief) — L (leeg/empty housing) and Z
(vernietigd/destroyed) are dropped, per the confirmed site legend.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import datetime, timezone

UTC = timezone.utc

# Requested bbox (see feeds.py) plus a little slack for boundary rounding.
_LAT_MIN, _LAT_MAX = 50.0, 54.0
_LON_MIN, _LON_MAX = 2.5, 7.7


def _parse_vmax(v) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    return int(s) if s.isdigit() else None


def _parse_epoch(v) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(v), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def parse_flitspalen(payload: dict) -> Iterator[dict]:
    """Yield one row dict per active NL camera.

    Raises ValueError if `result` is missing or not a list (structurally
    broken response) — caller treats that as an ingest error, old rows kept.
    Individual malformed rows (bad id/lat/lng/timestamp/richtung) are skipped
    and logged by the caller, not treated as a reason to fail the batch.
    """
    result = payload.get("result")
    if not isinstance(result, list):
        raise ValueError("payload missing 'result' list")

    for item in result:
        if item.get("land") != "NL" or item.get("status") != "A":
            continue

        raw_id = item.get("id")
        try:
            cam_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        try:
            lat, lng = float(item["lat"]), float(item["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lng)):
            continue
        if not (_LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lng <= _LON_MAX):
            continue

        created_at = _parse_epoch(item.get("create_time"))
        edited_at = _parse_epoch(item.get("edit_time"))

        richtung = item.get("richtung")
        bearing_deg: int | None = None
        try:
            b = int(str(richtung).strip())
            if 0 <= b <= 359:
                bearing_deg = b
        except (TypeError, ValueError):
            pass

        raw = {k: v for k, v in item.items() if k != "bubble"}

        yield {
            "id": cam_id,
            "status": item.get("status"),
            "city": item.get("ort"),
            "street": item.get("strasse"),
            "description": item.get("beschreibung"),
            "speed_limit_kmh": _parse_vmax(item.get("vmax")),
            "camera_type": item.get("type"),
            "rotatable": bool(item.get("drehbar")) if item.get("drehbar") is not None else None,
            "bearing_deg": bearing_deg,
            "created_at": created_at,
            "edited_at": edited_at,
            "geom": f"POINT({lng} {lat})",
            "raw": raw,
        }
