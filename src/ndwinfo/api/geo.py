"""GeoJSON helpers: assemble FeatureCollection from query rows."""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import Response


def make_fc(rows: list, geom_key: str, props_fn: Callable[[Any], dict]) -> dict:
    """Build a GeoJSON FeatureCollection.

    rows: SQLAlchemy Row objects (or named-tuple-like).
    geom_key: attribute name on each row holding ST_AsGeoJSON text (may be None).
    props_fn: callable(row) -> dict of feature properties.
    """
    features = []
    for row in rows:
        geom_json = getattr(row, geom_key, None)
        geometry = json.loads(geom_json) if geom_json else None
        features.append({"type": "Feature", "geometry": geometry, "properties": props_fn(row)})
    return {"type": "FeatureCollection", "features": features}


def geo_response(fc: dict) -> Response:
    return Response(
        content=json.dumps(fc, ensure_ascii=False, separators=(",", ":")),
        media_type="application/geo+json",
    )
