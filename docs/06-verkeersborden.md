# 06 — Traffic signs (verkeersborden)

The complete inventory of road signs in the Netherlands, in three equivalent
formats. **Large files** (228–236M compressed; CSV decompresses to >1 GB).
Pick **one** format. For PostGIS the WGS84 GeoJSON or the CSV (build geometry
from lat/lon) are easiest.

Same dataset, three encodings:

| File | Format | Geometry | Notes |
|---|---|---|---|
| `verkeersborden_actueel_beeld.csv.gz` | CSV | `latitude/longitude` + `rdX/rdY` columns | richest columns; 235M |
| `verkeersborden_actueel_beeld_wgs84.geojson.gz` | GeoJSON | Point WGS84 `[lon,lat]` | 236M |
| `verkeersborden_actueel_beeld_rd.geojson.gz` | GeoJSON | Point RD (EPSG:28992) | 228M |

- **Refresh**: ~daily. **Coverage**: national, every sign.

## CSV columns
```
id, externalId, validated, validatedOn, rvvCode, blackCode, zoneCode, status,
textSigns, latitude, longitude, rdX, rdY, placement, side, bearing,
nenTurningDirection, fraction, drivingDirection, roadName, roadType, roadNumber,
roadSectionId, nwbVersion, countyName, countyCode, townName, bgtCode, imageUrl,
firstSeenOn, lastSeenOn, removedOn, placedOn, expectedPlacedOn,
expectedRemovedOn, trafficOrderUrl
```

### Key fields
- `id` — UUID of the sign.
- `rvvCode` — RVV sign code (e.g. `D3`, `L8`) = the sign type.
- `status` — e.g. `PLACED` / removed (see `removedOn`).
- `latitude/longitude` — WGS84; `rdX/rdY` — RD metres.
- `placement` (L/R), `side` (compass), `bearing` (deg), `drivingDirection` (H/…).
- `roadName`, `roadSectionId`, `nwbVersion` — links to NWB road network.
- `countyName/countyCode` (GM####), `townName`, `bgtCode`.
- `imageUrl` — photo of the sign (`wegkenmerken.ndw.nu`).
- `textSigns` — sub-plates; in GeoJSON it is an array.
- date fields: `firstSeenOn`, `lastSeenOn`, `placedOn`, `removedOn`, expected\*.

## GeoJSON (wgs84 / rd)
Standard `FeatureCollection`; `properties` mirror the CSV columns (omitting empty
ones), `geometry` is a `Point` in the respective CRS. `textSigns` is an array.

## Postgres model (PostGIS)
`traffic_sign` (id PK, rvv_code, status, geom POINT(4326), placement, side,
bearing, driving_direction, road_name, road_section_id, county_code, town_name,
image_url, first_seen, last_seen, removed_on, …). GiST index on `geom` for the
area queries.

### Ingest tips
- These are the heaviest feeds — only ingest if signs are in scope.
- CSV: stream with `COPY` after building `ST_SetSRID(ST_MakePoint(lon,lat),4326)`,
  or `ogr2ogr` directly from the GeoJSON.
- `rd` variant only needed if you want to avoid reprojection from RD source data.
