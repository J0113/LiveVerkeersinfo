"""ANWB incidents parser: jams / roadworks / dynamic radars.

Single JSON payload: {success, dateTime, roads: [{road, segments: [{jams,
roadworks, radars}]}]}. One generic walk yields rows for all 3 categories.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

UTC = timezone.utc

# Generous NL+border bbox — decoded points outside this are treated as decode
# corruption, not real geometry.
_LAT_MIN, _LAT_MAX = 45.0, 56.0
_LON_MIN, _LON_MAX = 2.0, 9.0

# Defensive caps against a garbled/truncated string spinning the shift loop or
# building an absurd vertex count.
_MAX_ENCODED_LEN = 200_000
_MAX_VERTICES = 20_000
_MAX_SHIFT_BITS = 32


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Google encoded-polyline -> list of (lat, lon).

    Raises ValueError on a truncated/garbled string (mid-coordinate cutoff,
    shift overflow, or vertex/length caps exceeded) rather than silently
    returning a partial prefix — a caller that wants a best-effort fallback
    should catch ValueError itself.
    """
    if len(encoded) > _MAX_ENCODED_LEN:
        raise ValueError(f"encoded polyline exceeds {_MAX_ENCODED_LEN} chars")

    coords: list[tuple[float, float]] = []
    index = lat = lng = 0
    factor = 10**precision
    length = len(encoded)
    while index < length:
        for is_lng in (0, 1):
            shift = result = 0
            while True:
                if index >= length:
                    raise ValueError("polyline ends mid-coordinate-pair")
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if shift > _MAX_SHIFT_BITS:
                    raise ValueError("polyline shift overflow (garbled byte)")
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append((lat / factor, lng / factor))
        if len(coords) > _MAX_VERTICES:
            raise ValueError(f"polyline exceeds {_MAX_VERTICES} vertices")
    return coords


def _in_bounds(lat: float, lon: float) -> bool:
    return _LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX


def polyline_to_wkt(encoded: str | None) -> str | None:
    """Decode + validate; returns None (caller falls back) on any problem."""
    if not encoded:
        return None
    try:
        pts = decode_polyline(encoded)
    except ValueError:
        return None
    if len(pts) < 2 or not all(_in_bounds(lat, lon) for lat, lon in pts):
        return None
    return "LINESTRING(" + ", ".join(f"{lon} {lat}" for lat, lon in pts) + ")"


def _straight_line_wkt(from_loc: dict | None, to_loc: dict | None) -> str | None:
    if not from_loc or not to_loc:
        return None
    try:
        flon, flat = float(from_loc["lon"]), float(from_loc["lat"])
        tlon, tlat = float(to_loc["lon"]), float(to_loc["lat"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (_in_bounds(flat, flon) and _in_bounds(tlat, tlon)):
        return None
    return f"LINESTRING({flon} {flat}, {tlon} {tlat})"


def _point_wkt(loc: dict | None) -> str | None:
    if not loc:
        return None
    try:
        lon, lat = float(loc["lon"]), float(loc["lat"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _in_bounds(lat, lon):
        return None
    return f"POINT({lon} {lat})"


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _line_geom(item: dict) -> str | None:
    wkt = polyline_to_wkt(item.get("polyline"))
    if wkt:
        return wkt
    return _straight_line_wkt(item.get("fromLoc"), item.get("toLoc"))


def _base_row(item: dict, category: str, poll_time: datetime | None) -> dict | None:
    raw_id = item.get("id")
    if raw_id is None:
        return None
    try:
        item_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    row = {
        "record_id": f"{category}:{item_id}",
        "id": item_id,
        "category": category,
        "incident_type": item.get("incidentType"),
        "road": item.get("road"),
        "from_label": item.get("from"),
        "to_label": item.get("to"),
        "reason": item.get("reason"),
        "distance_m": item.get("distance"),
        "delay_s": item.get("delay"),
        "hm": item.get("HM"),
        "code_direction": item.get("codeDirection"),
        "segment_id": item.get("segmentId"),
        "label": item.get("label"),
        "valid_from": _parse_iso(item.get("start")),
        "poll_time": poll_time,
    }
    row["raw"] = {k: v for k, v in item.items() if k not in ("polyline",)}
    return row


def parse_anwb_incidents(payload: dict) -> Iterator[dict]:
    """Walk roads -> segments -> {jams, roadworks, radars}, yield row dicts.

    Raises ValueError on a structurally broken payload (`success` not true, or
    `roads` key missing entirely) so the caller can treat the run as an
    ingest error rather than a real empty snapshot. `roads: []` is a
    legitimate "no incidents" snapshot and yields zero rows without raising.

    Per-category completeness (a payload present but missing one whole
    category vs. previous runs) can't be judged from a single payload alone —
    that comparison against prior state lives in the ingester, not here.
    """
    if payload.get("success") is not True:
        raise ValueError("payload success is not true")
    roads = payload.get("roads")
    if roads is None:
        raise ValueError("payload missing 'roads'")

    poll_time = _parse_iso(payload.get("dateTime")) or datetime.now(UTC)

    for road in roads:
        for segment in road.get("segments") or []:
            for category in ("jams", "roadworks", "radars"):
                for item in segment.get(category) or []:
                    row = _base_row(item, category, poll_time)
                    if row is None:
                        continue
                    if category == "radars":
                        row["geom"] = _point_wkt(item.get("loc"))
                    else:
                        row["geom"] = _line_geom(item)
                    yield row
