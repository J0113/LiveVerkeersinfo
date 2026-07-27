"""Helpers for interpreting OpenStreetMap road tags."""

import re

OSM_MAXSPEED_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(mph|km/?h|kph)?\s*$", re.I)


def osm_carriageway_ref(tags: dict | None) -> str | None:
    """Return OSM's official carriageway reference without inventing a mapping."""
    tags = tags or {}
    value = tags.get("carriageway_ref")
    if value is None:
        value = tags.get("carriageway:ref")
    if value is None:
        return None
    label = str(value).strip()
    return label or None


def osm_maxspeed_kmh(tags: dict | None, direction: str | None) -> float | None:
    """Return the applicable numeric OSM maxspeed in km/h.

    Directional tags override the general value. For ``oneway=-1`` our lane
    geometry is reversed into travel order but still carries ``direction=fwd``,
    so the OSM backward tag is the applicable one.
    """
    tags = tags or {}
    osm_direction = direction
    if tags.get("oneway") == "-1" and direction == "fwd":
        osm_direction = "bwd"

    directional_key = {
        "fwd": "maxspeed:forward",
        "bwd": "maxspeed:backward",
    }.get(osm_direction)
    value = tags.get(directional_key) if directional_key in tags else tags.get("maxspeed")
    if value is None:
        return None

    match = OSM_MAXSPEED_RE.fullmatch(str(value))
    if not match:
        return None
    speed = float(match.group(1).replace(",", "."))
    if match.group(2) and match.group(2).lower() == "mph":
        speed *= 1.609344
    return round(speed, 1)
