"""Parser for RWS's Nationaal Wegenbestand Wegvakken GeoPackage.

Source is a whole-country GeoPackage (RD New / EPSG:28992, ~1.6M LineString
road sections) published daily at a fixed URL. Reads happen in windows via
skip_features/max_features — pyogrio has no native batch-streaming API for
read_dataframe — so the file is never held whole in memory. Field names are
RWS's own NWB schema (uppercase), distinct from PDOK's OGC API Features
GeoJSON property names used by the earlier live-proxy implementation.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterator, Mapping

import pandas as pd
import pyogrio

CHUNK_SIZE = 20_000

_COLUMNS = [
    "WVK_ID", "JTE_ID_BEG", "JTE_ID_END", "WEGBEHSRT", "WEGBEHNAAM",
    "STT_NAAM", "RIJRICHTNG", "ADMRICHTNG", "RPE_CODE", "POS_TV_WOL",
    "BST_CODE", "FRC", "FOW", "OPENLR", "BEGINKM", "EINDKM", "LENGTE_WVK",
    "BEGDAT_WRK", "STATUS", "WEGNR_HMP", "WEGNR_FRML", "ROUTELTR", "ROUTENR",
]


def parse_wegvakken(path: Path) -> Iterator[dict[str, Any]]:
    """Stream stable road-segment dicts from a local Wegvakken.gpkg, reprojected to WGS84."""
    info = pyogrio.read_info(str(path))
    total = info["features"]

    for offset in range(0, total, CHUNK_SIZE):
        gdf = pyogrio.read_dataframe(
            str(path),
            columns=_COLUMNS,
            skip_features=offset,
            max_features=CHUNK_SIZE,
        )
        if gdf.crs is None:
            gdf = gdf.set_crs(28992, allow_override=True)
        gdf = gdf.to_crs(4326)

        for row in gdf.itertuples(index=False):
            props = row._asdict()
            geom = props.pop("geometry")
            row_out = _transform_row(props, geom)
            if row_out is not None:
                yield row_out


def _transform_row(props: Mapping[str, Any], geom: Any) -> dict[str, Any] | None:
    wvk_id = _optional_int(props.get("WVK_ID"))
    if wvk_id is None or geom is None or geom.is_empty or len(geom.coords) < 2:
        return None

    frc = _optional_int(props.get("FRC"))
    manager = _optional_str(props.get("WEGBEHSRT"))
    if frc is not None and frc <= 2:
        road_class = "motorway"
    elif frc is not None and frc <= 4:
        road_class = "primary"
    elif manager in {"R", "P"}:
        road_class = "primary"
    else:
        road_class = "local"

    valid_from = props.get("BEGDAT_WRK")
    valid_from = valid_from.date() if isinstance(valid_from, pd.Timestamp) and not pd.isna(valid_from) else None

    return {
        "wvk_id": wvk_id,
        "begin_junction_id": _optional_int(props.get("JTE_ID_BEG")),
        "end_junction_id": _optional_int(props.get("JTE_ID_END")),
        "road_number": _road_number(props),
        "street_name": _optional_str(props.get("STT_NAAM")),
        "road_manager_type": manager,
        "road_manager_name": _optional_str(props.get("WEGBEHNAAM")),
        "direction": _optional_str(props.get("RIJRICHTNG")),
        "administrative_direction": _optional_str(props.get("ADMRICHTNG")),
        "carriageway_position": _optional_str(props.get("RPE_CODE")),
        "position_to_orientation_line": _optional_str(props.get("POS_TV_WOL")),
        "carriageway_type": _optional_str(props.get("BST_CODE")),
        "frc": frc,
        "form_of_way": _optional_int(props.get("FOW")),
        "openlr": _optional_str(props.get("OPENLR")),
        "begin_km": _optional_float(props.get("BEGINKM")),
        "end_km": _optional_float(props.get("EINDKM")),
        "length_m": _optional_float(props.get("LENGTE_WVK")),
        "valid_from": valid_from,
        "status": _optional_str(props.get("STATUS")),
        "road_class": road_class,
        "geom": geom.wkt,
        "raw": dict(props),
    }


def _road_number(props: Mapping[str, Any]) -> str | None:
    for key in ("WEGNR_HMP", "WEGNR_FRML"):
        value = _optional_str(props.get(key))
        if value:
            return value
    letter = _optional_str(props.get("ROUTELTR"))
    number = _optional_int(props.get("ROUTENR"))
    return f"{letter}{number}" if letter and number is not None else None


def _optional_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
