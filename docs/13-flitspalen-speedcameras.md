# Flitspalen.nl static speed cameras

Not an NDW file — a crowdsourced database of **fixed/permanent** speed cameras
from flitspalen.nl, complementing ANWB's *dynamic* radar reports
([12-anwb-incidents.md](12-anwb-incidents.md)).

- **Endpoint**: `POST https://www.flitspalen.nl/karte/`, form-encoded body
  (`xhr=1&action=all` + a bbox covering all of NL/Benelux/DE), gated behind
  `X-Requested-With`/`Accept`/`Origin`/`Referer`/`Cookie: LAN=nl` headers —
  see the `flitspalen_cameras` entry in `feeds.py` for the exact set.
- **Shape**: `{ "result": [ {...} ] }`, one flat list mixing NL/B/D — **hard
  filtered to `land == "NL"` and `status == "A"`** in `parsers/flitspalen.py`.
- **Status codes** (site's own "Kamerastatus" legend): `A` = actief (ingested),
  `L` = leeg (empty housing, not enforcing — excluded), `Z` = vernietigd
  (destroyed/removed — excluded).
- **`richtung`** (enforcement bearing) is always a plain integer 0–359 across
  the full NL subset — never the site's own compass-letter/`bs`/`db` display
  legend, which describes map-icon rendering, not the raw field.
- **`bubble`** (raw HTML with edit/dismantle links back to flitspalen.nl) is
  dropped entirely — never stored, never rendered.
- **Cadence**: weekly (`cadence_s: 604800`, `schedule_class: "background"`).
- **Storage**: `flitspalen_camera` table, `id` used directly as PK (globally
  unique across all 3 countries in the source).
- **API**: `GET /api/flitspalen?bbox=...`.
- **Web UI**: `flitspalen_cameras`, labeled "Speedcamera's", directly below
  "ANWB Speedcamera's" in the Situations group. Its `LAYERS` entry sets an
  explicit `limit: 1200` — the NL subset has ~994 active cameras, above the
  shared `api_default_limit` (500), which would otherwise silently truncate.
- **Enabled by default** — `docker-compose.yml`'s `DISABLED_FEEDS` only lists
  `verkeersborden_csv`, and `config.py` defaults `disabled_feeds` to `""`.
  Disable by adding `flitspalen_cameras` to `DISABLED_FEEDS` if needed.

## Trajectcontrole (SC/SCE route pairing)

A `camera_type` of `SC` (entry gantry) or `SCE` (exit gantry) marks one end of
a section-control enforcement stretch. `src/ndwinfo/ingest/flitspalen_route.py`
pairs them and precomputes a road-snapped route line at ingest time (once per
weekly flitspalen ingest, not per API request):

- SCE ids encode their SC id (`sce_id = 1_000_000_000 + sc_id*1000 + N`, `N`
  an unpredictable 1–3 digit variant index) — floor-division recovers the SC
  id; this matched 69/69 live SCE cameras against the full NL dataset.
- Each camera point is snapped onto its nearest `osm_road` way, then the two
  ways are connected over a graph of every candidate way's shared endpoints
  via Dijkstra — not an OSM route-relation lookup — because a motorway ref is
  rarely one continuous way between interchanges.
- Result is stored in `flitspalen_camera_route` (`sce_id` PK — not `sc_id`,
  since one `sc_id` can have multiple paired SCE lane cameras; `sc_id`,
  `street`, `geom` LINESTRING).

**API**: `GET /api/flitspalen/pairs?bbox=...` (`api/routers/flitspalen.py`)
returns these precomputed routes as GeoJSON.

**Web UI**: `flitspalen_pairs` (label "Trajectcontrole", Situations group) has
no checkbox of its own — it's a `linkedTo: 'flitspalen_cameras'` layer
(`web/config.js`), so it rides the visibility of the main speed-camera layer
rather than being toggled independently.
