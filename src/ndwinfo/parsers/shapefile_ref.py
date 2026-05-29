"""Shapefile parsers: measurement locations and MSI sign geometry."""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pyogrio


def _extract_shp(zip_path: Path, shp_name: str, tmpdir: str) -> Path:
    """Extract all components of a shapefile from a zip into tmpdir."""
    stem = shp_name.removesuffix(".shp")
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        for member in members:
            basename = member.rsplit("/", 1)[-1]
            if any(basename == f"{stem}{ext}" for ext in (".shp", ".dbf", ".shx", ".prj", ".fix")):
                zf.extract(member, tmpdir)
    found = list(Path(tmpdir).rglob(shp_name))
    if not found:
        raise FileNotFoundError(f"{shp_name} not found in {zip_path}")
    return found[0]


def _row_to_dict(row_data, columns) -> dict:
    return {col: row_data[col] for col in columns}


def parse_meetlocaties(zip_path: Path) -> Iterator[tuple[str, dict]]:
    """Parse ndw_avg_meetlocaties_shapefile.zip.

    Yields ('punt', dict) for Telpunten_WGS84.shp (count points).
    Yields ('vak', dict)  for Meetvakken_WGS84.shp (measurement sections).

    Note: Meetvakken is very large (110M unzipped) — may take time.
    """
    for shp_name, kind in [
        ("Telpunten_WGS84.shp", "punt"),
        ("Meetvakken_WGS84.shp", "vak"),
    ]:
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                shp_path = _extract_shp(zip_path, shp_name, tmpdir)
            except FileNotFoundError:
                continue

            gdf = pyogrio.read_dataframe(str(shp_path))
            data_cols = [c for c in gdf.columns if c != "geometry"]

            for _, row_data in gdf.iterrows():
                geom_obj = row_data.get("geometry")
                geom = geom_obj.wkt if geom_obj is not None else None

                raw = _row_to_dict(row_data, data_cols)

                # Find the ID column (varies by shapefile)
                id_val: str | None = None
                for id_col in ("ID", "id", "MEETPUNTID", "VAKID", "FID", "OBJECTID"):
                    if id_col in raw:
                        id_val = str(raw[id_col])
                        break

                yield kind, {"id": id_val, "geom": geom, "raw": raw}


def parse_msi_shapefile(zip_path: Path) -> Iterator[dict]:
    """Parse ndw_msi_shapefiles_latest.zip → MSI sign geometry.

    Yields one dict per sign with uuid and geom (WKT POINT).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = _extract_shp(zip_path, "shapes.shp", tmpdir)
        gdf = pyogrio.read_dataframe(str(shp_path))
        data_cols = [c for c in gdf.columns if c != "geometry"]

        for _, row_data in gdf.iterrows():
            geom_obj = row_data.get("geometry")
            geom = geom_obj.wkt if geom_obj is not None else None

            raw = _row_to_dict(row_data, data_cols)

            # UUID column name varies; try common names
            uuid: str | None = None
            for col in ("UUID", "uuid", "ID", "id", "OBJECT_ID", "OBJECTID"):
                if col in raw:
                    uuid = str(raw[col])
                    break

            yield {"uuid": uuid, "geom": geom, "raw": raw}
