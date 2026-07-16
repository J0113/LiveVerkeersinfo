# Implementation Plan — LiveVerkeersinfo

> **Status:** this is the historical plan used to build the current application.
> For the next road-identification, map-matching and live-speed development
> phases, use the [OSM-first production backlog](docs/11-osm-production-backlog.md).
> Existing NDW ingest and non-driving feed work in this document remains useful
> as implementation reference.

Step-by-step build plan for an implementer (Claude Sonnet). Read
[CLAUDE.md](CLAUDE.md) and [docs/](docs/README.md) first — they define every
feed's format and the join keys. This plan is concrete: follow phases in order,
each phase ends with a verifiable check.

## Locked decisions (do not re-decide)
- **Scope**: all NDW feed families (see [docs/README.md](docs/README.md)).
- **Stack**: Python 3.11+, FastAPI, SQLAlchemy 2.x + GeoAlchemy2, psycopg
  (v3), Pydantic v2. DB = PostgreSQL 16 + PostGIS 3. Frontend = MapLibre GL JS
  (vanilla, no framework needed) served as static files.
- **History**: latest snapshot only → **upsert by natural key**. No time-series.
- **Area selection**: bounding box (`minLon,minLat,maxLon,maxLat`) on every list
  endpoint via `ST_Intersects(geom, ST_MakeEnvelope(...,4326))`.
- **CRS in DB**: WGS84 / EPSG:4326 everywhere. Reproject RD (28992) on ingest.

## Conventions
- Package name `ndwinfo`. All code under `src/ndwinfo/`.
- Geometry columns: `geom geometry(Geometry, 4326)` + GiST index, named `ix_<table>_geom`.
- Every feed table has `raw JSONB` (full source record) + typed columns we query on.
- Timestamps stored as `timestamptz` (source values are UTC ISO8601).
- Upsert = `INSERT ... ON CONFLICT (<natural_key>) DO UPDATE`.
- Each ingester: download → parse (streaming) → batch upsert → record run metadata.
- Lint/format: `ruff` + `ruff format`. Type-check optional (`mypy` light).

---

## Phase 0 — Repo scaffold & tooling

1. Create layout:
   ```
   src/ndwinfo/
     __init__.py
     config.py            # env-based settings (Pydantic BaseSettings)
     db.py                # engine, session, Base
     models.py            # SQLAlchemy ORM tables (all feeds)
     download.py          # conditional GET helper (ETag/Last-Modified cache)
     parsers/
       __init__.py
       datex_v2.py        # SOAP d2LogicalModel: measurement table, speed, traveltime, truckparking table
       datex_v3.py        # mc:messageContainer: situations (generic), VMS/DRIP, parking status, emission zones
       ndw_vms.py         # Matrixsignaalinformatie (NDW proprietary XML)
       geojson_ocpi.py    # charging geojson + OCPI json, verkeersborden geojson
       csv_signs.py       # verkeersborden CSV
       shapefile_ref.py   # msi / meetlocaties / VILD shapefiles
     ingest/
       __init__.py
       base.py            # Ingester base: run(), upsert_batch(), feed metadata
       <one module per feed family>
     feeds.py             # registry: feed name -> (url, parser, cadence, ingester)
     api/
       __init__.py
       main.py            # FastAPI app, CORS, routers
       deps.py            # bbox parser dependency, db session dep
       routers/           # one router per domain
     poller.py            # loop: for each feed, if due & changed -> ingest
   web/                   # static MapLibre UI (index.html, app.js, style)
   migrations/            # alembic
   tests/
   docker-compose.yml     # postgis + app
   Dockerfile
   pyproject.toml
   .env.example
   ```
2. `pyproject.toml` deps: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`,
   `geoalchemy2`, `psycopg[binary]`, `pydantic-settings`, `httpx`, `lxml`,
   `shapely`, `pyproj`, `alembic`. Dev: `ruff`, `pytest`, `pytest-asyncio`.
3. `.env.example`: `DATABASE_URL=postgresql+psycopg://ndwinfo:ndwinfo@localhost:5432/ndwinfo`,
   `NDW_BASE_URL=https://opendata.ndw.nu`, `DATA_DIR=./data`.
4. `docker-compose.yml`: service `db` = `postgis/postgis:16-3.4` (volume, port
   5432, env POSTGRES_DB/USER/PASSWORD=ndwinfo); service `app` builds Dockerfile.
5. `config.py`: Pydantic `Settings` reading env. `db.py`: create engine from
   `DATABASE_URL`, `SessionLocal`, declarative `Base`.

**Check**: `docker compose up -d db`; `python -c "import ndwinfo"` works;
`psql ... -c 'CREATE EXTENSION IF NOT EXISTS postgis;'` succeeds.

---

## Phase 1 — Database schema (models.py + alembic)

Create tables below. All `geom` are EPSG:4326 with GiST index. `raw JSONB`
nullable. Add `ingested_at timestamptz default now()`.

### Reference / static
- `measurement_site` — PK `id` (text, e.g. `PZH01_MST_0065_00`). Cols: `name`,
  `equipment_type`, `num_lanes int`, `side`, `version int`, `record_version_time`,
  `geom`, `raw`.
- `measurement_characteristic` — PK (`site_id`, `index`). Cols: `lane`,
  `period_s int`, `value_type`, `veh_length_min numeric`, `veh_length_max numeric`.
  FK `site_id` → `measurement_site`.
- `meetlocatie_punt`, `meetlocatie_vak` — from shapefiles; keep DBF attrs in `raw`,
  natural key from shapefile id column, `geom` Point/LineString.
- `vild_point`, `vild_line`, `vild_area` — optional (load only if needed); id +
  `geom` + `raw`.

### Real-time measurement (latest per site+index)
- `traffic_measurement` — PK (`site_id`,`index`). Cols: `measured_at timestamptz`,
  `value_type` (trafficFlow|trafficSpeed), `flow_veh_h numeric`, `speed_kmh numeric`
  (null when -1 / no input). FK `site_id`.
- `travel_time` — PK (`segment_id`,`index`). Cols: `measured_at`, `duration_s numeric`,
  `ref_duration_s numeric`, `accuracy numeric`, `n_inputs int`, `travel_time_type`.

### Situations (generic — one table for 6 feeds)
- `situation` — PK `id` (text, situation id). Cols: `record_id`, `category`
  (enum-ish text: `incident`|`srti`|`roadworks`|`bridge_opening`|`closure`|`speed_limit`),
  `record_type` (xsi:type), `severity`, `probability`, `safety_related bool`,
  `source`, `valid_from timestamptz`, `valid_to timestamptz`,
  `version_time timestamptz`, `speed_limit_kmh int` (nullable, for SpeedManagement),
  `geom`, `raw`. Index on `category`.

### Signs & VMS
- `msi_sign` — PK `uuid`. Cols: `road`, `carriageway`, `lane int`, `km numeric`,
  `geom`, `raw` (geometry from shapefile, location from feed — merge).
- `msi_state` — PK `uuid` (latest only). Cols: `ts_state timestamptz`,
  `aspect_type` (blank|speedlimit|arrow|...), `value`, `flashing bool`,
  `red_ring bool`. FK → `msi_sign`.
- `drip` — PK (`controller_id`,`vms_index`). Cols: `description`, `vms_type`,
  `physical_support`, `bearing int`, `message JSONB`, `geom`, `raw`.

### EV charging
- `charge_point` — PK `id`. Cols: `cpo_id`, `address`, `city`, `operator_name`,
  `owner_name`, `open bool`, `last_updated timestamptz`, `geom`, `raw`.
- `charge_availability` — PK (`cp_id`,`idx`). Cols: `total int`, `available int`,
  `power_max numeric`, `power_type`, `connector_type`, `connector_format`,
  `tariff_ids text[]`. FK `cp_id`.
- `tariff` — PK `id`. Cols: `currency`, `party_id`, `country_code`,
  `elements JSONB`, `last_updated`.

### Truck parking
- `truck_parking` — PK `id` (e.g. `NL-12_421`). Cols: `name`, `operator`,
  `capacity int`, `geom`, `raw`.
- `truck_parking_status` — PK `parking_id`. Cols: `origin_time timestamptz`,
  `vacant int`, `occupied int`, `occupancy_pct numeric`. FK → `truck_parking`.

### Traffic signs (verkeersborden)
- `traffic_sign` — PK `id` (uuid). Cols: `rvv_code`, `status`, `placement`,
  `side`, `bearing int`, `driving_direction`, `fraction numeric`, `road_name`,
  `road_section_id bigint`, `nwb_version`, `county_code`, `county_name`,
  `town_name`, `image_url`, `text_signs JSONB`, `first_seen date`, `last_seen date`,
  `placed_on date`, `removed_on date`, `geom`, `raw`.

### Operational
- `feed_run` — PK serial. Cols: `feed text`, `started_at`, `finished_at`,
  `status`, `http_status int`, `etag`, `last_modified`, `rows_upserted int`,
  `error text`. Used by poller for conditional GET + observability.

Set up alembic; generate initial migration; `alembic upgrade head`.

**Check**: `\dt` shows all tables; `SELECT PostGIS_Version();` ok; every geom
table has a GiST index.

---

## Phase 2 — Download helper & feed registry

1. `download.py`: `fetch(feed) -> DownloadResult` using `httpx`.
   - Send conditional `If-None-Match`/`If-Modified-Since` from last `feed_run`.
   - On `304`: return `not_modified` (skip ingest).
   - On `200`: stream to `DATA_DIR/<filename>` (do **not** hold in memory).
   - Transparent gunzip when reading (`.gz`): expose a file-like that
     decompresses on the fly (`gzip.GzipFile`).
2. `feeds.py`: registry list of dicts:
   `{name, filename, url, parser_fn, ingester_cls, cadence_s}`.
   Cadences: real-time feeds 60s; charging 60s; tariffs 3600; measurement table
   3600; emission zones / truckparking table / verkeersborden / shapefiles 86400.

**Check**: `python -m ndwinfo.download trafficspeed` downloads + reports size, and a
second run returns 304.

---

## Phase 3 — Parsers (streaming)

Each parser yields dicts ready for upsert. **Use `lxml.etree.iterparse`** for XML
(clear elements after use — feeds decompress to tens/hundreds of MB). Handle
namespaces via the maps in the docs.

1. **`datex_v2.py`**
   - `parse_measurement_site_table(fileobj)` → site dicts + characteristic dicts.
     Geometry from `measurementSiteLocation` (point or linear → take point/centroid).
     Yield `(site, [characteristics])`. ns `http://datex2.eu/schema/2/2_0`.
   - `parse_trafficspeed(fileobj)` → per `siteMeasurements`/`measuredValue@index`:
     map `basicData/@xsi:type` TrafficFlow→`flow_veh_h`, TrafficSpeed→`speed_kmh`
     (null if speed == -1 or `numberOfInputValuesUsed=0`). Key (site_id,index).
   - `parse_traveltime(fileobj)` → duration + reference duration per index.
   - `parse_truckparking_table(fileobj)` → parking sites (GenericPublication →
     parkingTablePublication). Geometry from `parkingLocation`.
2. **`datex_v3.py`** (root `mc:messageContainer`, ns `…/schema/3/*`)
   - `parse_situations(fileobj, category)` — **generic**. For each `sit:situation`/
     `sit:situationRecord`: extract id, `@xsi:type` (strip `sit:`), severity,
     probability, safetyRelatedMessage, source name, validity start/end, version
     time, point geometry from `loc:pointByCoordinates`. For `SpeedManagement`
     pull the ordered speed → `speed_limit_kmh`. Reuse for: actueel_beeld
     (`incident`), srti (`srti`), roadworks (`roadworks`), brugopeningen
     (`bridge_opening`), afsluitingen (`closure`), max_snelheden (`speed_limit`).
   - `parse_drip(fileobj)` — `vms:vmsControllerTable` → controller/vms with
     description, type, support, location/bearing, message areas → `message` JSON.
   - `parse_parking_status(fileobj)` — `ParkingStatusPublication` → per
     `parkingRecordStatus`: ref id, origin time, vacant/occupied/occupancy.
   - `parse_emission_zones(fileobj)` — `cz:ControlledZoneTablePublication` →
     zone id, name, type, status, authority, info url, geometry (polygon),
     conditions → store under `situation`? No — store in dedicated table:
     **add `emission_zone`** table (id, name, type, status, authority, info_url,
     geom POLYGON, raw). (Add to Phase 1 models if not already.)
3. **`ndw_vms.py`** — `parse_matrix_signs(fileobj)`: group `event` by
   `sign_id/uuid`; emit location dict (road/carriageway/lane/km) and latest
   `display` state dict (aspect type + value + flashing + red_ring).
4. **`geojson_ocpi.py`**
   - `parse_charging_geojson(fileobj)` → charge_point + availability rows from
     GeoJSON features. Use streaming JSON (`ijson`) for the big ones; add `ijson`
     dep. Geometry `[lon,lat]`.
   - `parse_ocpi_tariffs(fileobj)` → tariff rows.
   - (OCPI locations json optional — GeoJSON covers the map need; implement only
     if EVSE/connector detail required.)
   - `parse_signs_geojson(fileobj)` — alternative to CSV for verkeersborden.
5. **`csv_signs.py`** — `parse_signs_csv(fileobj)`: stream `csv.DictReader`,
   build geometry from lat/lon, map columns per [docs/06](docs/06-verkeersborden.md).
   Prefer this over geojson for the 235M file (lighter parse).
6. **`shapefile_ref.py`** — read shapefiles from the zips with `pyogrio`/`fiona`
   (add dep) or shell out to `ogr2ogr`/`shp2pgsql`. Reproject RD→4326 with pyproj
   when needed. Targets: `ndw_msi_shapefiles_latest.zip` → msi_sign geometry;
   `ndw_avg_meetlocaties_shapefile.zip` → meetlocatie_punt/vak; VILD optional.

**Check**: unit test each parser against the sample files in `data/samples/`
(already downloaded). Assert row counts > 0 and required fields present.

---

## Phase 4 — Ingesters

1. `ingest/base.py`: `Ingester` with `run()`:
   - call `download.fetch`; if 304 → record run `not_modified`, return.
   - open (gunzip) file, call parser, batch upsert (e.g. 1000 rows) inside a
     transaction; build PostGIS geom via `ST_SetSRID(ST_MakePoint(lon,lat),4326)`
     or WKB from shapely. Use `insert(...).on_conflict_do_update(...)`.
   - write `feed_run` row (rows_upserted, etag, last_modified, status/error).
2. One thin ingester per feed wiring parser→table(s). Parent/child upserts:
   upsert sites before characteristics; parking before status; charge_point
   before availability; msi_sign (geometry) before msi_state.
3. **Stale handling (latest-snapshot)**: for snapshot feeds, optionally delete
   rows absent from the new snapshot (e.g. situations no longer present). Simplest
   v1: `DELETE FROM situation WHERE category=:c AND id NOT IN (:current_ids)` per
   run, or add `last_seen_run` and prune older. Document choice; default = prune
   by category each run for situation/charging/parking-status/msi_state.

**Check**: run each ingester once against live data; verify row counts and a
`SELECT count(*) ... WHERE geom IS NOT NULL`. Spot-check a known site.

---

## Phase 5 — Poller

`poller.py`: infinite loop (or `--once`): for each feed in registry, if
`now - last_finished >= cadence` → run ingester (catch+log errors per feed, never
let one feed kill the loop). Sleep small tick (e.g. 10s) between scans. Run as a
separate process/service from the API (compose service `poller`). Make it
idempotent and safe to restart (state in `feed_run`).

**Check**: start poller; after a few minutes `feed_run` shows recent successful
runs for the 60s feeds and 304s on unchanged ones.

---

## Phase 6 — API (FastAPI)

1. `api/deps.py`: `bbox` dependency parsing `?bbox=minLon,minLat,maxLon,maxLat`
   (required on list endpoints; 400 if malformed; optional area cap to prevent
   full-NL dumps — reject if area too large, configurable). DB session dep.
2. Endpoints (all GeoJSON `FeatureCollection` output, all bbox-filtered with
   `ST_Intersects(geom, ST_MakeEnvelope(minLon,minLat,maxLon,maxLat,4326))`):
   - `GET /api/traffic/speed` — join `traffic_measurement`+`measurement_site`.
   - `GET /api/traffic/traveltime`.
   - `GET /api/situations?category=` — from `situation` (filter by category).
   - `GET /api/signs/matrix` — `msi_sign`+`msi_state`.
   - `GET /api/signs/drips`.
   - `GET /api/charging` — `charge_point`(+availability), optional `?available=true`.
   - `GET /api/truckparking` — `truck_parking`+status.
   - `GET /api/verkeersborden?rvvCode=` — `traffic_sign` (bbox required; cap count).
   - `GET /api/emission-zones`.
   - `GET /api/feeds/status` — `feed_run` latest per feed (health).
3. Return GeoJSON via `ST_AsGeoJSON` aggregated in SQL (fast) or build in Python.
   Add `limit` + count cap per endpoint. Enable CORS for the web UI origin.

**Check**: `curl '.../api/situations?bbox=4.8,52.3,5.0,52.4'` returns valid
GeoJSON with features only inside the box.

---

## Phase 7 — Web UI (MapLibre)

`web/index.html` + `app.js` + style:
1. MapLibre map (free raster/vector basemap, e.g. OSM raster or MapTiler key in
   `.env`; document the key requirement). Center NL (lon 5.3, lat 52.1, zoom 7).
2. Layer toggles (checkboxes) per feed: speed, traveltime, situations (by
   category), matrix signs, drips, charging, truckparking, verkeersborden,
   emission-zones.
3. On `moveend`: read map bounds → call each enabled endpoint with
   `bbox=<W,S,E,N>`; render returned GeoJSON as a source+layer; popups show
   properties. Debounce; cancel in-flight requests on new move.
4. Heavy layers (verkeersborden) only fetch above a min zoom; show a hint
   otherwise.
5. A `/feeds/status` panel showing last update time per feed.
6. Refresh every 60 seconds.

**Check**: open UI, pan to a city, layers populate from the live DB; panning
refetches the new bbox.

---

## Phase 8 — Containerization & docs

1. `Dockerfile` (python:3.11-slim + GDAL libs for shapefile parsing if using
   fiona/pyogrio; or rely on ogr2ogr in image). Install project.
2. `docker-compose.yml` services: `db` (postgis), `app` (uvicorn API + serve
   `web/` static), `poller`. Healthchecks; `app` depends_on `db`.
3. `README.md` (root): quickstart — `cp .env.example .env`, `docker compose up`,
   `alembic upgrade head`, open `http://localhost:3500`.
4. Update [CLAUDE.md](CLAUDE.md) status checkboxes as phases complete.

**Check**: fresh `docker compose up` brings up DB+API+poller; UI loads; after a
few minutes live data appears.

---

## Testing strategy
- **Parsers**: unit tests against `data/samples/*` (committed? no — large; instead
  add a `make samples` target that downloads them, or commit tiny trimmed
  fixtures under `tests/fixtures/`).
- **Ingest**: integration test against a throwaway PostGIS (testcontainers or the
  compose db) — run ingester on a fixture, assert rows + geometry validity.
- **API**: `pytest` + `httpx.AsyncClient` against a seeded test DB; assert bbox
  filtering actually excludes out-of-box features.

## Suggested order of execution
Phase 0 → 1 → 2 → 3 (start with `datex_v2` measurement+speed, prove the
join+geometry path end-to-end) → 4 (speed ingester) → 6 (one endpoint) → 7
(map with one layer). Once that vertical slice works, fan out the remaining
parsers/ingesters/endpoints/layers feed-by-feed, then Phase 5 poller, Phase 8.

## Gotchas (from exploration — heed these)
- DATEX value feeds carry **no geometry**; you MUST ingest the measurement site
  table first and join by `id` + `index`. See [docs/01](docs/01-traffic-realtime.md).
- `speed = -1` / `numberOfInputValuesUsed=0` ⇒ NULL, not zero.
- Matrix-sign feed pairs a stale `lanelocation` event and a fresh `display` event
  per `sign_id`; take latest display, get true geometry from the MSI shapefile.
- Two DATEX dialects (v2 SOAP vs v3 messageContainer) — separate parser modules.
- Decompressed sizes are large; **always stream-parse**, clear lxml elements.
- RD (EPSG:28992) sources must be reprojected to 4326 before insert.
- Truckparking table is `informationStatus=test` and raw (not gzipped); status
  feed is v3 and raw too.
# Current product backlog (2026-07-16)

The active OSM-first speed-coverage backlog, delivered phase 1, measured
validation and phases 2–5 are maintained in
[docs/17-speed-coverage-iteration.md](docs/17-speed-coverage-iteration.md).
That document supersedes the older generic speed/lane sequencing below where
the two conflict. The rollback branch is `codex/osm-speed-coverage` with
checkpoint `6c6b8ef`.
