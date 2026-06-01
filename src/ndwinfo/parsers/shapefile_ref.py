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


def _extract_shp_wgs84(zip_path: Path, shp_name: str, tmpdir: str) -> Path:
    """Extract shapefile from WGS84/ subfolder in zip."""
    stem = shp_name.removesuffix(".shp")
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            parts = member.replace("\\", "/").split("/")
            if "WGS84" not in parts:
                continue
            basename = parts[-1]
            if any(basename == f"{stem}{ext}" for ext in (".shp", ".dbf", ".shx", ".prj", ".fix")):
                zf.extract(member, tmpdir)
    found = [p for p in Path(tmpdir).rglob(shp_name) if "WGS84" in str(p)]
    if not found:
        raise FileNotFoundError(f"{shp_name} not found in WGS84/ of {zip_path}")
    return found[0]


def parse_vild(zip_path: Path) -> Iterator[tuple[str, dict]]:
    """Parse VILD6.x.A.zip — yields ('point'|'line'|'area', dict) from WGS84/ subfolder."""
    for shp_name, kind in [
        ("vild_point.shp", "point"),
        ("vild_line.shp", "line"),
        ("vild_area.shp", "area"),
    ]:
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                shp_path = _extract_shp_wgs84(zip_path, shp_name, tmpdir)
            except FileNotFoundError:
                continue

            gdf = pyogrio.read_dataframe(str(shp_path))
            data_cols = [c for c in gdf.columns if c != "geometry"]

            for _, row_data in gdf.iterrows():
                geom_obj = row_data.get("geometry")
                geom = geom_obj.wkt if geom_obj is not None else None
                raw = _row_to_dict(row_data, data_cols)

                id_val: str | None = None
                for id_col in ("LOC_NR", "ID", "id", "VILD_ID", "OBJECTID", "FID"):
                    if id_col in raw:
                        id_val = str(raw[id_col])
                        break

                yield kind, {"id": id_val, "geom": geom, "raw": raw}


def parse_vild_tmc(zip_path: Path) -> Iterator[dict]:
    """Parse the VILD TMC location table (VILD6.x.A.dbf at the zip root).

    Yields one dict per location code with the chain topology needed to trace a
    road: lin_ref (→ vild_line.id), pos_off/neg_off (next/previous code), road.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path) as zf:
            dbf_member = None
            for member in zf.namelist():
                base = member.replace("\\", "/").rsplit("/", 1)[-1]
                low = base.lower()
                if low.startswith("vild") and low.endswith(".dbf") and not low.startswith("vild_"):
                    dbf_member = member
                    break
            if dbf_member is None:
                return
            zf.extract(dbf_member, tmpdir)
        dbf_path = next(Path(tmpdir).rglob("*.dbf"))

        gdf = pyogrio.read_dataframe(str(dbf_path), read_geometry=False)
        cols = {c.upper(): c for c in gdf.columns}

        def col(name: str):
            return cols.get(name)

        loc_c = col("LOC_NR")
        if loc_c is None:
            return
        lin_c, pos_c, neg_c, road_c = col("LIN_REF"), col("POS_OFF"), col("NEG_OFF"), col("ROADNUMBER")

        def _int(v):
            try:
                if v is None or (isinstance(v, float) and v != v):  # NaN
                    return None
                return int(v)
            except (TypeError, ValueError):
                return None

        for r in gdf.itertuples(index=False):
            d = r._asdict()
            loc_nr = _int(d.get(loc_c))
            if loc_nr is None or loc_nr <= 0:
                continue
            road = d.get(road_c) if road_c else None
            if isinstance(road, float) and road != road:
                road = None
            yield {
                "loc_nr": loc_nr,
                "lin_ref": _int(d.get(lin_c)) if lin_c else None,
                "pos_off": _int(d.get(pos_c)) if pos_c else None,
                "neg_off": _int(d.get(neg_c)) if neg_c else None,
                "road_number": str(road) if road is not None else None,
            }


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

            # Road heading at sign (deg). Column varies; "bearing" in current shapefile.
            bearing = None
            for col in ("bearing", "BEARING", "hoek", "angle"):
                if col in raw and raw[col] is not None:
                    try:
                        bearing = float(raw[col])
                        if bearing != bearing:  # NaN
                            bearing = None
                    except (TypeError, ValueError):
                        bearing = None
                    break

            yield {"uuid": uuid, "geom": geom, "bearing": bearing, "raw": raw}
