# LiveVerkeersinfo

<img src="docs/logo.png" alt="LiveVerkeersinfo logo" width="120">

Real-time Dutch traffic data (NDW open data) stored in PostGIS and served as a spatial API with a MapLibre web map. Filter any feed by bounding box instead of downloading the full national file.

## Quickstart

```bash
cp .env.example .env          # optional — defaults work out of the box
docker compose up --build -d
```

Open **http://localhost:3500** — the map loads live data for whatever area you're viewing.

The `app` container runs `alembic upgrade head` automatically before starting. The `poller` container starts ingesting all feeds immediately.

### OSM driving MVP

Create the local directed road graph after the NDW measurement-site feed has
been ingested. The default downloads the national Geofabrik extract; for a
smaller development graph, this example uses Noord-Holland:

```bash
OSM_PBF_URL=https://download.geofabrik.de/europe/netherlands/noord-holland-latest.osm.pbf \
  docker compose --profile osm run --rm --build osm-bootstrap
```

The command is safe to repeat: it uses HTTP validators and skips both import and
rebinding when the active graph already has bindings for the current matcher
version. Measurement-site refreshes update affected bindings incrementally. A
new graph is activated atomically after a complete import. Graph and bindings
remain in PostGIS when the regular stack is restarted.

The driving UI requests only a narrow corridor ahead of a valid GPS fix. It
keeps eight recent fixes, ranks nearby directed OSM segments by distance,
heading, continuity and topology, and withholds road identity and speed when
confidence is insufficient. Public Overpass is retained only for the separate
diagnostic POC and is not used by the MVP request path.

The normal viewport layer also reads `/api/roads` from local PostGIS. The legacy
Overpass POC is off by default and explicitly labelled as slow diagnostics.
Existing browser settings are migrated once so an older automatic POC toggle
cannot keep issuing external requests.

At navigation zoom the matched carriageway and its topology-confirmed common
path ahead are expanded into schematic 3.5 m lane centrelines. OSM directional
lane tags drive the shared map/HUD model. These offsets are display geometry,
not surveyed lane centrelines and are never used for GPS matching. The app does
not claim the user's exact lane. NDW lane speeds remain hidden until NDW lane
numbering has been independently verified against OSM's left-to-right order;
the safe aggregate carriageway speed remains available.

### Simulatierit testen

Klik in de kaartbediening rechts op de afspeelknop om zonder echte GPS-beweging
een vaste noordwaartse rit over de A4 af te spelen. De circa 2,8 km lange route
rapporteert ongeveer 100 km/u en is versneld na circa 17 seconden klaar. De
synthetische fixes gaan door dezelfde GPS-, corridor-, matcher- en HUD-code als
een echte rit. Klik nogmaals om de simulatie te stoppen en te resetten.

Realtime speed ingest uses PostgreSQL `COPY` into transactional staging followed
by one set-based merge. The poller prioritizes realtime driving feeds while the
API is active; background and large maintenance imports run only after idle and
use at most one bulk slot. Opening the app therefore no longer launches an
overdue NWB/charging/reference ingest storm.

## Architecture

```
Geofabrik OSM PBF → streaming import → directed, indexed OSM road graph ┐
                                                                     ├→ accepted source bindings
NDW open data → poller → streaming parsers → current observations ────┘
                               ↓
                   PostGIS + corridor API
                               ↓
          bounded stateful GPS matcher → MapLibre driving UI
```

## Services

| Service | Description |
|---------|-------------|
| `db`     | PostgreSQL 16 + PostGIS 3.4 |
| `app`    | FastAPI + uvicorn on port 3500; also serves `web/` as static files |
| `poller` | Background ingest loop — runs each feed on its cadence |
| `osm-bootstrap` | Explicit `osm` profile: download/import OSM and rebuild NDW→OSM bindings |

## API endpoints

All list endpoints require `?bbox=minLon,minLat,maxLon,maxLat`. Max area: 25 deg².

| Endpoint | Feed | Cadence |
|----------|------|---------|
| `GET /api/traffic/speed` | Traffic flow + speed per measurement site | 60 s |
| `GET /api/situations?category=` | Incidents, SRTI, roadworks, bridge openings, closures, speed limits | 60 s |
| `GET /api/signs/matrix` | Matrix signs (MSI) with current state | 60 s |
| `GET /api/signs/drips` | Dynamic road info panels (DRIPs / VMS) | 60 s |
| `GET /api/charging` | EV charging points + connector availability | 60 s |
| `GET /api/truckparking` | Truck parking sites + live occupancy | 60 s |
| `GET /api/emission-zones` | Low-emission zones | daily |
| `GET /api/verkeersborden?rvvCode=` | Traffic signs (bbox required; best above zoom 13) | daily |
| `GET /api/nwb/roads?bbox=&zoom=` | Normalized NWB road sections for the viewport (served from PostGIS) | daily |
| `GET /api/weggeg/lanes` | WEGGEG-derived separate lane centrelines (bbox required; zoom 14+) | monthly |
| `GET /api/roads?bbox=` | Active directed OSM segments plus accepted direct speed state | OSM snapshot + 60 s speed |
| `GET /api/roads/corridor?lon=&lat=&heading=` | Bounded OSM candidates ahead of the vehicle | per accepted GPS corridor refresh |
| `GET /api/roads/path?segment_id=&ahead_m=&behind_m=` | Bounded connected path; separates common ahead from unresolved branches | per accepted matched segment |
| `GET /api/roads/diagnostics/bindings?bbox=` | Accepted, ambiguous and rejected NDW→OSM decisions | graph/site refresh |
| `GET /api/feeds` | Last run per feed — status, time, rows upserted | — |

All list endpoints return GeoJSON `FeatureCollection`. Optional `?limit=` (default 500, max 2000).

## Web UI

- Dark MapLibre map centred on the Netherlands (zoom 7)
- Layer toggles (top-left panel): NWB road network, traffic speed, 6 situation categories, matrix signs, DRIPs, EV charging, truck parking, emission zones, traffic signs, WEGGEG lanes
- Panning or zooming refetches all enabled layers for the new bbox (300 ms debounce)
- Auto-refreshes every 60 seconds
- Feed status panel (bottom-right): last update time and status per feed
- Traffic signs only fetched at zoom ≥ 13; WEGGEG lanes only at zoom ≥ 14
- At navigation zoom, an accepted OSM binding is always authoritative. Live
  speeds use validated WEGGEG physical lane geometry where available and an
  OSM schematic lane fallback only when NDW and OSM lane counts agree.
  Ambiguous/rejected measurements remain visible as neutral point values but
  cannot colour either OSM or WEGGEG road geometry.
- Traffic-speed ribbons follow the 3.5 m WEGGEG lane spacing and scale
  exponentially with zoom, keeping them road-aligned while zooming and panning
- Matrix signs and DRIPs/VMS are HUD-only. They first bind fail-closed to the
  accepted directed OSM segment and are then selected on the confirmed path.
  MSI may be lane scoped; DRIP is carriageway scoped and never lane scoped.
- The minimalist driving HUD starts GPS follow mode automatically and shows a
  schematic left-to-right lane split for the accepted carriageway. It does not
  claim an exact user lane without a separately confirmed lateral-position
  observation.
- In GPS follow mode the production road layer comes from the local directed
  OSM graph. Only accepted, fresh NDW bindings may colour a segment or enter
  the current/ahead HUD; ambiguous matches fail closed.
- Segment colour distinguishes direct measured speed from bounded interpolation
  and propagation. Unknown, stale or low-confidence states remain uncoloured;
  measurement points stay visible in grey for binding diagnostics.
- A sustained speed advantage on an adjacent lane appears as a non-directive
  lane-flow suggestion. Stale, opposite-direction, non-adjacent, and
  matrix-restricted lanes are excluded; hysteresis and a post-change cooldown
  prevent noisy or rapidly reversing suggestions
- Detailed traffic-speed and WEGGEG lane overlays remain available in Layers,
  but start disabled so the driving map stays uncluttered
- Background tabs pause GPS and data refreshes; feed status is fetched only
  while its panel is open, and unchanged HUD content is not rebuilt per GPS fix
- Poller concurrency defaults to two workers, charging availability ingestion is
  batch-bounded, and feed-run history is indexed and capped per feed

## Data sources

Full catalogue: [docs/README.md](docs/README.md). Canonical MVP road geometry,
direction and topology come from [OpenStreetMap](https://www.openstreetmap.org/copyright)
through public [Geofabrik extracts](https://download.geofabrik.de/europe/netherlands.html).
Live traffic observations come from [opendata.ndw.nu](https://opendata.ndw.nu);
NWB road geometry is ingested daily
from RWS's [Wegvakken GeoPackage](https://downloads.rijkswaterstaatdata.nl/nwb-wegen/)
and WEGGEG lane centrelines from the public
[Rijkswaterstaat WEGGEG catalogue](https://downloads.rijkswaterstaatdata.nl/weggeg/) —
both ingested into PostGIS and served from there, not proxied per request.
Neither source requires authentication. See
[NWB road-network foundation](docs/08-nwb-road-network.md).

## Local development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# needs a running PostGIS instance — adjust DATABASE_URL in .env
alembic upgrade head
uvicorn ndwinfo.api.main:app --app-dir src --reload

# in a second terminal
python -m ndwinfo.poller
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+psycopg://ndwinfo:ndwinfo@localhost:5432/ndwinfo` | SQLAlchemy connection string |
| `NDW_BASE_URL` | `https://opendata.ndw.nu` | Base URL for NDW downloads |
| `DATA_DIR` | `./data` | Scratch directory for downloaded files |
| `MAX_BBOX_AREA` | `25.0` | Maximum bbox area in deg² for API requests |
| `API_DEFAULT_LIMIT` | `500` | Default feature limit per endpoint |
| `API_MAX_LIMIT` | `2000` | Hard cap on feature limit |
| `POLLER_MAX_WORKERS` | `3` | Total bounded feed workers; more can increase PostGIS contention |
| `POLLER_BULK_MAX_INFLIGHT` | `1` | Maximum concurrent background/maintenance ingests |
| `POLLER_IDLE_TIMEOUT_S` | `300` | Idle time before background feeds may start |
| `POLLER_MAINTENANCE_IDLE_S` | `900` | Idle time before very large maintenance feeds may start |
| `NWB_WEGVAKKEN_URL` | official RWS `Wegvakken.gpkg` URL | Daily bulk-download source (used by the poller) |
| `NWB_MAX_FEATURES` | `5000` | Per-viewport NWB row cap |
| `NWB_DIAGNOSTIC_MODE` | `false` | Enable clickable NWB metadata diagnostics |
| `OSM_PBF_URL` | Geofabrik Netherlands `.osm.pbf` | Snapshot used by the explicit OSM bootstrap |
| `OSM_PBF_PATH` | `./data/netherlands-latest.osm.pbf` | Local snapshot path |
| `OSM_IMPORT_BATCH_SIZE` | `5000` | Bounded database import batch size |
| `ROAD_SPEED_STALE_AFTER_S` | `600` | Hide direct speed after this source-observation age |
