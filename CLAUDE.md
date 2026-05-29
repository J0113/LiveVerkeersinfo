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
- [x] Containerization (Docker Compose: db + app + poller)
