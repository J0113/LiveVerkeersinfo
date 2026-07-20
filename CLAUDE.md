# LiveVerkeersinfo — project guide

## Goal

Ingest **NDW open traffic data** (real-time, whole Netherlands) into **PostgreSQL
(PostGIS)**, then expose an **API** + **web UI** that returns data for a
**specific area** (bounding box / polygon / municipality) instead of the full
national file.

Pipeline:

```
NDW files (national, gzip/xml/json/geojson)
  → poller (download on cadence)
  → parser (DATEX II v2/v3, GeoJSON, OCPI JSON, CSV, shapefile)
  → PostgreSQL + PostGIS (geometry indexed)
  → API (spatial query: clip to area)
  → Web UI (map; pick area; live layers)
```

## Why this design

NDW publishes only **flat files covering all of NL** — no per-area endpoint, no
history, just the latest snapshot overwritten on a schedule. The value we add is:
**store + spatially index + serve area subsets**. PostGIS `ST_Intersects` /
`ST_DWithin` on indexed geometry is the core operation.

## Data sources

Full catalog: **[docs/README.md](docs/README.md)**. Per-category detail in
`docs/01`…`docs/07`. Summary of feed families:

- **Real-time measurement** (DATEX II v2): `trafficspeed`, `traveltime` reference
  a static **MeasurementSiteTable** (`measurement_current.xml.gz`) for geometry.
  Join by site `id` + value `index`. → [docs/01](docs/01-traffic-realtime.md)
- **Live situations** (DATEX II v3 `SituationPublication`): `actueel_beeld`, SRTI,
  roadworks, bridge openings, closures, temp speed limits — all the **same
  envelope**, differ only by `situationRecord/@xsi:type`. One generic parser
  serves all. → [docs/01](docs/01-traffic-realtime.md), [docs/03](docs/03-roadworks-measures.md)
- **Signs/VMS**: matrix signals (NDW XML), DRIPs (DATEX v3), sign geometry
  (shapefile). → [docs/02](docs/02-signs-vms.md)
- **EV charging**: GeoJSON (best for map) + OCPI JSON locations/tariffs. → [docs/04](docs/04-charging.md)
- **Truck parking**: static table + live status. → [docs/05](docs/05-truckparking.md)
- **Traffic signs (verkeersborden)**: CSV/GeoJSON, **very large (>200M)**. → [docs/06](docs/06-verkeersborden.md)
- **Static reference**: measurement-location & VILD shapefiles. → [docs/07](docs/07-static-reference.md)
- **Road network**: major-road OpenStreetMap ways and per-lane geometry from a
  Geofabrik PBF extract. → [docs/11](docs/11-osm-pbf.md)

## Key technical facts

- **Download**: `https://opendata.ndw.nu/<filename>`. No auth. Most files gzip.
- **Cadence**: real-time feeds ~**60s**; reference tables hourly→daily; big sign
  datasets ~daily. Poll a little slower than publish; skip if `Last-Modified`
  unchanged (use conditional GET / HEAD).
- **Decompressed size is large** (e.g. traveltime 2.5M gz → ~73M XML; verkeersborden
  CSV 235M gz → >1 GB). **Stream-parse** (SAX/iterparse), don't load whole DOM.
- **CRS**: store geometry as WGS84 (EPSG:4326) in PostGIS; RD = EPSG:28992 if a
  source is RD-only. GiST index every geometry column.
- **Geometry vs values are split** in DATEX feeds — always ingest the reference
  table before/with the value feed and join by id+index.
- **DATEX dialects**: v2 = SOAP-wrapped `d2LogicalModel`, ns `…/schema/2/2_0`;
  v3 = `mc:messageContainer`, ns `…/schema/3/*`. Handle both.

## Conventions (to follow as code lands)

- Keep raw downloaded snapshots out of git (`data/` is scratch; add `.gitignore`).
- One ingester per feed family; share the generic DATEX-v3-situation parser.
- API: spatial filter param (bbox or polygon) required on list endpoints; never
  return the full national set unfiltered.
- Document any new feed/field back into `docs/`.

## Decisions (locked 2026-05-29)

- **Scope**: **all** feed families in v1 — core traffic, roadworks/closures/zones,
  EV charging, signs + truck parking (incl. the heavy 200M+ verkeersborden).
- **Stack**: **Python** backend (FastAPI + SQLAlchemy/GeoAlchemy2) + **MapLibre GL JS** web UI.
- **History**: **latest snapshot only** — upsert; each feed table holds current
  state (no time-series retention in v1).
- **Area selection**: **bounding box** (min/max lat/lon) on all list endpoints.
  `ST_Intersects`/`ST_MakeEnvelope(…, 4326)` against GiST-indexed geometry.

## Status

- [x] Explore & document all NDW files (`docs/`)
- [x] Decide stack, scope, history model, area model
- [x] Postgres + PostGIS schema (all feeds, upsert/latest) — `src/ndwinfo/models.py` + alembic migrations
- [x] Poller (conditional GET on cadence) + parsers (DATEX v2/v3, GeoJSON, OCPI, CSV, shapefile)
- [x] Spatial API (FastAPI, bbox filter) — `src/ndwinfo/api/`
- [x] Web UI (MapLibre, layer per feed, bbox = current viewport) — `web/`
- [x] VILD-directed speed sensors matched conservatively to OSM lane geometry
- [x] Containerization (Docker Compose: db + app + poller)

## Directory structure & key files

```
src/ndwinfo/
├── models.py              # SQLAlchemy ORM for all feed tables (MeasurementSite, TrafficMeasurement, Situation, etc.)
├── feeds.py               # Feed registry: feed name → URL filename, cadence, parser, ingester
├── config.py              # Pydantic settings (DATABASE_URL, NDW_BASE_URL, DATA_DIR, API limits)
├── db.py                  # SQLAlchemy engine and session setup
├── download.py            # HTTP download with Last-Modified caching (conditional GET)
├── poller.py              # Main loop: iterate feeds.FEEDS, download, parse, ingest
├── api/
│   ├── main.py            # FastAPI app init, CORS, static file mount, router includes
│   ├── deps.py            # Dependency injection (BBoxDep, DbDep) + validation
│   ├── geo.py             # ST_AsGeoJSON formatting, make_fc helper
│   └── routers/           # One per feed family:
│       ├── traffic.py     # GET /api/traffic/speed, /api/traffic/traveltime (TrafficMeasurement join MeasurementSite)
│       ├── situations.py  # GET /api/situations (Situation with geometry)
│       ├── signs.py       # GET /api/signs (Sign, SignMessage tables)
│       ├── charging.py    # GET /api/charging (ChargingStation, Tariff tables)
│       ├── truckparking.py # GET /api/truckparking (TruckParkingArea, TruckParkingStatus)
│       ├── verkeersborden.py # GET /api/verkeersborden (Verkeersbord — large CSV)
│       ├── emission.py    # GET /api/emission (EmissionZone)
│       └── feeds.py       # GET /api/feeds (metadata on ingest cadence, last_run, row counts)
├── parsers/               # Feed-format parsers, called by ingesters:
│   ├── datex_v2.py        # DATEX II v2 (SOAP-wrapped) → list of dicts
│   ├── datex_v3.py        # DATEX III (mc:messageContainer) → list of dicts
│   ├── geojson_ocpi.py    # GeoJSON + OCPI JSON (charging)
│   ├── csv_signs.py       # CSV (Verkeersborden large dataset)
│   ├── shapefile_ref.py   # Shapefiles (meetlocaties)
│   └── ndw_vms.py         # NDW XML matrix signs
└── ingest/                # Feed-specific ingesters (called by poller, use parsers):
    ├── base.py            # BaseIngester abstract class (upsert logic)
    ├── measurement.py     # MeasurementSite + TrafficMeasurement (traffic speed/traveltime)
    ├── situations.py      # Situation (all DATEX v3 situation types: roadworks, closures, etc.)
    ├── signs.py           # Sign + SignMessage (matrix + DRIP)
    ├── charging.py        # ChargingStation + Tariff (GeoJSON + OCPI)
    ├── truckparking.py    # TruckParkingArea + TruckParkingStatus
    ├── verkeersborden.py  # Verkeersbord (streaming CSV insert)
    ├── reference.py       # MeetlocatiePunt, MeetlocatieVak, VildPoint (reference geometry)
    └── emission.py        # EmissionZone

migrations/              # Alembic schema migrations (SQLAlchemy tracked)
web/                    # Static frontend:
├── index.html          # MapLibre GL JS canvas (served at / via cache-bust route in api/main.py)
├── lib.js              # Shared helpers: MSI sign rendering, speed colors, geo math (load first)
│                       # App split into ordered plain-global scripts (shared lexical scope,
│                       # load in this order — concatenation == old app.js):
├── config.js           #   layer/group/HUD defs + runtime & GPS state
├── map.js              #   basemaps, map init, map.on(load/move/zoom/rotate) handlers
├── fetch.js            #   fetchAll/fetchLayer/NWB roads, viewportBbox, public config
├── matrix.js           #   MSI gantry HTML markers (map render)
├── hud.js              #   GPS-relative road-sign HUD tiles
├── speed.js            #   speed lanes/points markers, gradient lanes, feed status
├── ui.js               #   popups, layer panel, panel toggles, basemap picker, zoom hint
├── gps.js              #   GPS/compass/follow-loop + geo math helpers
└── style.css           # Map styling
data/                   # Downloaded snapshots (gitignored)
├── .meta/              # Feed metadata JSON (last_modified, etag, download time)
└── samples/            # (Optional) sample files for testing

docs/                   # Feed documentation:
├── README.md           # Catalog & links to feed families
├── 01-traffic-realtime.md
├── 02-signs-vms.md
├── 03-roadworks-measures.md
├── 04-charging.md
├── 05-truckparking.md
├── 06-verkeersborden.md
└── 07-static-reference.md
```

## Core flow

**Poller** (`poller.py`):
1. For each feed in `feeds.FEEDS`:
   - Download from NDW (skip if `Last-Modified` unchanged)
   - Route to appropriate parser (DATEX v2/v3, GeoJSON, CSV, shapefile)
   - Pass parsed records to ingester
2. Ingester upserts records into its table (latest snapshot only)
3. Repeat on cadence (real-time feeds ~60s, reference ~hourly, large files ~daily)

**API** (FastAPI):
- All endpoints require `bbox` query param (min_lon, min_lat, max_lon, max_lat)
- Build `ST_MakeEnvelope(…, 4326)`, query with `ST_Intersects` on geometry index
- Return GeoFeatureCollection (features + properties)
- Limits enforced via `settings.api_default_limit`, `api_max_limit`

**Web UI** (MapLibre GL JS):
- Draw base map layer
- Fetch each feed endpoint via API with current viewport bbox
- Toggle layers on/off
- Render as GeoJSON source → symbol layer

## Configuration

Environment variables (`.env` or docker-compose):
- `DATABASE_URL`: PostgreSQL connection string (default in compose: `postgresql+psycopg://ndwinfo:ndwinfo@db:5432/ndwinfo`)
- `NDW_BASE_URL`: Base URL for downloads (default: `https://opendata.ndw.nu`)
- `DATA_DIR`: Local snapshot directory (default: `/app/data`)
- `OSM_NETHERLANDS_URL`: Geofabrik Netherlands PBF source

Python settings (`config.py`):
- `api_default_limit`: Default rows per list endpoint (e.g. 500)
- `api_max_limit`: Max rows allowed (e.g. 5000)
- `db_pool_size`: Connection pool size

## How to extend

**Add a new feed**:
1. Define entry in `feeds.FEEDS` (name, filename, cadence, parser_fn, ingester_cls)
2. Create parser in `src/ndwinfo/parsers/` (return `list[dict]`)
3. Create ingester in `src/ndwinfo/ingest/` (extend `BaseIngester`, implement `ingest(records, db_session)`)
4. Add ORM model(s) in `models.py` with geometry index
5. Create API router in `src/ndwinfo/api/routers/`
6. Import router in `api/main.py`
7. Document in `docs/`

**Run locally**:
```bash
docker-compose up -d
# API: http://localhost:3500
# Web UI: http://localhost:3500
# Schema migrations auto-run on app startup
```

## Notes

- Stream-parse large feeds (XML iterparse, ijson for JSON, CSV reader) — don't load DOM
- Always join reference tables (measurement_site, measurement_characteristics) before ingesting values
- All geometry stored as WGS84 (EPSG:4326) with GiST spatial index
- API never returns unfiltered national dataset — bbox required on all list endpoints
- Latest-snapshot upsert model — no time-series history in v1
