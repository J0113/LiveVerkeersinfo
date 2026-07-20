# 07 — Static reference datasets

Geometry & code-list references that change rarely. Load once, refresh
occasionally. Used to give the real-time feeds proper geometry and human labels.

---

## ndw_avg_meetlocaties_shapefile.zip — measurement locations (shapefile)

- **Format**: ESRI Shapefiles in WGS84, zipped. **Refresh** ~weekly.
- **Role**: official geometry for traffic measurement — count points and
  measurement segments. Complements / overlaps the DATEX `measurement` site table
  ([01](01-traffic-realtime.md)); use whichever join is convenient.

Zip contents:
```
Telpunten_WGS84.shp   (573K)   — count points (Point geometry), .dbf 11M attrs
Meetvakken_WGS84.shp  (110M)   — measurement segments (Line geometry), .dbf 45M attrs
Version.txt
```
- `Telpunten` = count/measurement points; `Meetvakken` = measurement sections (road segments).
- **Load**: `shp2pgsql -s 4326` or `ogr2ogr … PG:` → `meetlocatie_punt`,
  `meetlocatie_vak` tables. `Meetvakken_WGS84.shp` is large (110M) — load only if needed.

---

## VILD6.13.A.zip — location reference (VILD) + docs

- **Format**: ZIP bundle of shapefiles (RD + WGS84), a master DBF, code-list
  reports (`.md`/`.txt`), and PDFs. **Refresh**: on VILD release (versioned).
- **Role**: NDW's **VILD** (locatie-referentie) — the canonical road/point/area
  location reference many NDW location codes resolve against.

Zip contents (per CRS folder `RD/` and `WGS84/`):
```
vild_point.shp (276K)  — reference points
vild_line.shp  (1.1M)  — reference lines (road sections)
vild_area.shp  (23.5M) — reference areas
*.dbf/.shx/.prj/.fix    — shapefile sidecars
VILD6.13.A.dbf (3.5M)   — master attribute table
vrijgaverapport-6.13.A.md / .txt        — release report
wijzigingsrapport-6.13.A-tov-6.12.A.md  — changelog vs previous
Belangrijkste wijzigingen VILD6.13.A.txt
Productbeschrijving VILD 20200402.pdf   — product description
Technisch Handboek VILD 6 20191101.pdf  — technical handbook
```
- `RD/` = EPSG:28992, `WGS84/` = EPSG:4326 (identical features, different CRS).
- **`VILD6.12.A.zip`** = previous release, same structure — archive/reference only.
- **Load**: usually only needed if you must resolve VILD location codes to
  geometry. Load `WGS84/vild_*.shp` into PostGIS if so. Read the PDFs/handbook
  for the code semantics.

The VILD master DBF is stored losslessly in `vild_tmc.raw`: every source field
keeps its original name, typed columns remain duplicated there, and empty or
NaN source values are normalized to JSON `null`. `HECTO_DIR` also has a typed
column for direction enrichment.

Refreshing either VILD or the measurement-site table rebuilds fixed-site
bearings and derived carriageway diagnostics. The bearing follows a short
local tangent oriented through same-line `POS_OFF`/`NEG_OFF` neighbours;
multipart ambiguity and unrelated components remain unresolved.
