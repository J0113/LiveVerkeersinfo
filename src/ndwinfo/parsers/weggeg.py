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
_LANE_TRANSITION_RE = re.compile(r"(?<!\d)(\d+)\s*(?:-+\s*>|=>|→)\s*(\d+)(?!\d)")
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)


def lane_transition_counts(attributes: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Return lane counts at the start and end of the source geometry.

    WEGGEG expresses transitions in increasing-hectometre/source-geometry order,
    independently of ``KANTCODE``. A lone count is treated as stable. Ambiguous
    descriptions are deliberately left unknown instead of inferring a transition
    from unrelated digits.
    """
    description = str(attributes.get("OMSCHR") or "").strip()
    match = _LANE_TRANSITION_RE.search(description)
    if match:
        return int(match.group(1)), int(match.group(2))

    numbers = _LANE_NUMBER_RE.findall(description)
    if len(numbers) == 1:
        count = int(numbers[0])
        return count, count
    return None, None


def lane_count(attributes: Mapping[str, Any]) -> int:
    """Return the maximum lane count while retaining the legacy fallback of one."""
    start_count, end_count = lane_transition_counts(attributes)
    return max((count for count in (start_count, end_count) if count is not None), default=1)


def make_lane_rows(attributes: Mapping[str, Any], geometry) -> list[dict]:
    """Expand one WEGGEG road section into centered, separate lane features."""
    source_id = attributes.get("FK_VELD4")
    if source_id is None or (isinstance(source_id, float) and math.isnan(source_id)):
        return []
    if geometry is None or geometry.is_empty:
        return []
    if not isinstance(geometry, (LineString, MultiLineString)):
        return []

    start_count, end_count = lane_transition_counts(attributes)
    # A display centreline labelled as one physical lane would be a fabricated
    # topology claim. Keep the legacy ``lane_count`` helper for callers that
    # need a numeric fallback, but do not materialize lane rows when WEGGEG did
    # not provide an unambiguous count.
    if start_count is None or end_count is None:
        return []
    count = lane_count(attributes)
    opposes_digitisation = attributes.get("KANTCODE") == "T"
    direction_sign = -1 if opposes_digitisation else 1
    source_transition = _transition_pair(start_count, end_count)
    travel_transition = (
        _transition_pair(end_count, start_count) if opposes_digitisation else source_transition
    )
    rows: list[dict] = []
    for lane in range(1, count + 1):
        # Dutch lane 1 is leftmost in travel direction. Source geometry follows
        # increasing hectometres, so T traffic travels against digitisation.
        offset_m = ((count - 1) / 2 - (lane - 1)) * LANE_WIDTH_M * direction_sign
        lane_presence = _lane_presence(lane, start_count, end_count)
        # WEGGEG declares the count change but not the physical taper station.
        # Keep the full display line so downstream orientation/mirroring remains
        # stable; consumers may render the presence change schematically only.
        lane_geom = _offset_geometry(geometry, offset_m)
        if lane_geom.is_empty:
            continue
        raw = dict(attributes)
        raw["lane_transition"] = {
            "source": source_transition,
            "travel": travel_transition,
            "lane_presence": lane_presence,
            "display": (
                "schematic_only"
                if lane_presence in {"source_start", "source_end"}
                else "full"
            ),
        }
        rows.append({
            "id": f"{source_id}:{lane}",
            "source_id": str(source_id),
            "lane": lane,
            "lane_count": count,
            "road_number": _text(attributes.get("WEGNUMMER")),
            "direction": _text(attributes.get("KANTCODE")),
            "carriageway_side": _text(attributes.get("IZI_SIDE")),
            "geom": transform(_RD_TO_WGS84.transform, lane_geom).wkt,
            "raw": raw,
        })
    return rows


def _transition_pair(start_count: int | None, end_count: int | None) -> list[int] | None:
    if start_count is None or end_count is None:
        return None
    return [start_count, end_count]


def _lane_presence(lane: int, start_count: int | None, end_count: int | None) -> str:
    if start_count is None or end_count is None:
        return "unknown"
    at_start = lane <= start_count
    at_end = lane <= end_count
    if at_start and at_end:
        return "both"
    if at_start:
        return "source_start"
    if at_end:
        return "source_end"
    return "unknown"


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
