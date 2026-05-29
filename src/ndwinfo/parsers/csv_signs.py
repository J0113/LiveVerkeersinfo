"""CSV parser for verkeersborden (Dutch traffic signs inventory)."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import date


def _date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _int(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _json_field(s: str | None):
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def parse_signs_csv(fileobj) -> Iterator[dict]:
    """Parse verkeersborden_actueel_beeld.csv.gz.

    Yields one dict per sign. Skips rows without a valid lat/lon.
    Extra fields (trafficOrderUrl, etc.) are captured in `raw` only.
    """
    reader = csv.DictReader(io.TextIOWrapper(fileobj, encoding="utf-8"))
    for row in reader:
        lat_s = row.get("latitude", "")
        lon_s = row.get("longitude", "")
        if not lat_s or not lon_s:
            continue
        try:
            lat, lon = float(lat_s), float(lon_s)
        except ValueError:
            continue

        out = {
            "id": row.get("id"),
            "rvv_code": row.get("rvvCode"),
            "status": row.get("status"),
            "placement": row.get("placement"),
            "side": row.get("side"),
            "bearing": _int(row.get("bearing")),
            "driving_direction": row.get("drivingDirection"),
            "fraction": _float(row.get("fraction")),
            "road_name": row.get("roadName"),
            "road_section_id": _int(row.get("roadSectionId")),
            "nwb_version": row.get("nwbVersion"),
            "county_code": row.get("countyCode"),
            "county_name": row.get("countyName"),
            "town_name": row.get("townName"),
            "image_url": row.get("imageUrl"),
            "text_signs": _json_field(row.get("textSigns")),
            "first_seen": _date(row.get("firstSeenOn")),
            "last_seen": _date(row.get("lastSeenOn")),
            "placed_on": _date(row.get("placedOn")),
            "removed_on": _date(row.get("removedOn")),
            "geom": f"POINT({lon} {lat})",
        }
        # Fields not in typed columns — preserved in raw
        out["raw"] = {
            **{k: v for k, v in out.items() if k != "raw"},
            "trafficOrderUrl": row.get("trafficOrderUrl"),
            "bgtCode": row.get("bgtCode"),
            "roadType": row.get("roadType"),
            "roadNumber": row.get("roadNumber"),
            "externalId": row.get("externalId"),
            "expectedPlacedOn": row.get("expectedPlacedOn"),
            "expectedRemovedOn": row.get("expectedRemovedOn"),
        }
        yield out
