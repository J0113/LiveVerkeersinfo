# LiveVerkeersinfo

Real-time Dutch traffic data (NDW open data) stored in PostGIS and served as a spatial API with a MapLibre web map. Filter any feed by bounding box instead of downloading the full national file.

## Quickstart

```bash
cp .env.example .env          # optional — defaults work out of the box
docker compose up --build -d
```

Open **http://localhost:3500** — the map loads live data for whatever area you're viewing.

The `app` container runs `alembic upgrade head` automatically before starting. The `poller` container starts ingesting all feeds immediately.

## Architecture

```
NDW open data (opendata.ndw.nu)
  → poller   — conditional GET, respects ETag/Last-Modified, cadence per feed
  → parsers  — streaming (lxml iterparse / ijson / pyogrio), no full-DOM load
  → PostGIS  — upsert latest snapshot, GiST-indexed geometry (EPSG:4326)
  → API      — FastAPI, bbox filter on every list endpoint
  → Web UI   — MapLibre GL JS, fetches current viewport on every pan/zoom
```

## Services

| Service | Description |
|---------|-------------|
| `db`     | PostgreSQL 16 + PostGIS 3.4 |
| `app`    | FastAPI + uvicorn on port 3500; also serves `web/` as static files |
| `poller` | Background ingest loop — runs each feed on its cadence |

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
| `GET /api/weggeg/lanes` | WEGGEG-derived separate lane centrelines (bbox required; zoom 14+) | monthly |
| `GET /api/feeds/status` | Last run per feed — status, time, rows upserted | — |

All list endpoints return GeoJSON `FeatureCollection`. Optional `?limit=` (default 500, max 2000).

## Web UI

- Dark MapLibre map centred on the Netherlands (zoom 7)
- Layer toggles (top-left panel): traffic speed, 6 situation categories, matrix signs, DRIPs, EV charging, truck parking, emission zones, traffic signs, WEGGEG lanes
- Panning or zooming refetches all enabled layers for the new bbox (300 ms debounce)
- Auto-refreshes every 60 seconds
- Feed status panel (bottom-right): last update time and status per feed
- Traffic signs only fetched at zoom ≥ 13; WEGGEG lanes only at zoom ≥ 14
- At navigation zoom, live speeds are drawn directly on matched WEGGEG lanes;
  unmatched measurements retain the existing roadside marker

## Data sources

Full catalogue: [docs/README.md](docs/README.md). Data comes from
[opendata.ndw.nu](https://opendata.ndw.nu) and the public
[Rijkswaterstaat WEGGEG catalogue](https://downloads.rijkswaterstaatdata.nl/weggeg/);
neither requires authentication.

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
