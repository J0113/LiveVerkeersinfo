"""Parser for WEGGEG's monthly ``Rijstroken`` shapefile.

WEGGEG publishes road-section centrelines plus a lane transition such as
``2 -> 3``. It does not ship individual lane centrelines, so this module derives
them in RD (EPSG:28992) before transforming them to WGS84.
"""

from __future__ import annotations

import math
import re
import tempfile
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pyogrio
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.ops import transform

LANE_WIDTH_M = 3.5
_LANE_NUMBER_RE = re.compile(r"\d+")
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)


def lane_count(attributes: Mapping[str, Any]) -> int:
    """Extract the maximum lane count from WEGGEG's ``OMSCHR`` transition."""
    transition = str(attributes.get("OMSCHR") or "")
    numbers = [int(number) for number in _LANE_NUMBER_RE.findall(transition)]
    return max(numbers, default=1)


def make_lane_rows(attributes: Mapping[str, Any], geometry) -> list[dict]:
    """Expand one WEGGEG road section into centered, separate lane features."""
    source_id = attributes.get("FK_VELD4")
    if source_id is None or (isinstance(source_id, float) and math.isnan(source_id)):
        return []
    if geometry is None or geometry.is_empty:
        return []
    if not isinstance(geometry, (LineString, MultiLineString)):
        return []

    count = lane_count(attributes)
    direction_sign = -1 if attributes.get("KANTCODE") == "T" else 1
    rows: list[dict] = []
    for lane in range(1, count + 1):
        # Dutch lane 1 is leftmost in travel direction. Source geometry follows
        # increasing hectometres, so T traffic travels against digitisation.
        offset_m = ((count - 1) / 2 - (lane - 1)) * LANE_WIDTH_M * direction_sign
        lane_geom = _offset_geometry(geometry, offset_m)
        if lane_geom.is_empty:
            continue
        rows.append({
            "id": f"{source_id}:{lane}",
            "source_id": str(source_id),
            "lane": lane,
            "lane_count": count,
            "road_number": _text(attributes.get("WEGNUMMER")),
            "direction": _text(attributes.get("KANTCODE")),
            "carriageway_side": _text(attributes.get("IZI_SIDE")),
            "geom": transform(_RD_TO_WGS84.transform, lane_geom).wkt,
            "raw": dict(attributes),
        })
    return rows


def _offset_geometry(geometry: LineString | MultiLineString, offset_m: float):
    if offset_m == 0:
        return geometry
    if isinstance(geometry, LineString):
        return geometry.offset_curve(offset_m)
    parts: list[LineString] = []
    for part in geometry.geoms:
        offset_part = part.offset_curve(offset_m)
        if isinstance(offset_part, LineString) and not offset_part.is_empty:
            parts.append(offset_part)
        elif isinstance(offset_part, MultiLineString):
            parts.extend(line for line in offset_part.geoms if not line.is_empty)
    if len(parts) == 1:
        return parts[0]
    return MultiLineString(parts)


def _text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value)


def parse_weggeg_lanes(zip_path: Path) -> Iterator[dict]:
    """Yield one WGS84 lane row for every lane in WEGGEG ``Rijstroken``."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                basename = member.rsplit("/", 1)[-1]
                if basename in {
                    "rijstroken.shp", "rijstroken.dbf", "rijstroken.shx", "rijstroken.prj"
                }:
                    zf.extract(member, tmpdir)

        shp_files = list(Path(tmpdir).rglob("rijstroken.shp"))
        if not shp_files:
            raise FileNotFoundError(f"Rijstroken/rijstroken.shp not found in {zip_path}")

        frame = pyogrio.read_dataframe(str(shp_files[0]))
        columns = [column for column in frame.columns if column != "geometry"]
        for _, feature in frame.iterrows():
            attributes = {column: feature[column] for column in columns}
            yield from make_lane_rows(attributes, feature["geometry"])
