# Plan: ANWB incidents feed (jams / roadworks / radars)

## Source

`GET https://api.anwb.nl/routing/v1/incidents/incidents-desktop` — no auth, plain JSON (not gzip), single payload nationwide. Verified live: `{ success, dateTime, roads: [{ road, type, segments: [{ start, end, jams: [...], roadworks: [...], radars: [...] }] }] }`.

3 categories, one shared envelope per item (`id`, `road`, `from`/`to` + `fromLoc`/`toLoc`, `bounds`, `events[]`, `reason`, `category`):
- **jams** — `incidentType` stationary-traffic/road-closed, has `polyline`, `distance`, `delay`.
- **roadworks** — same shape + `label`, has `polyline`.
- **radars** — point only, `loc` instead of polyline, `HM` (hectometer), no line geometry.

`polyline` confirmed as standard Google encoded-polyline (precision 5) — decoded first point of a sample matched its declared `bounds.southWest` exactly.

⚠️ **Legal/ToS flag (needs your call, not decided here):** `api.anwb.nl` is ANWB's app backend, not an open-data endpoint like NDW's `opendata.ndw.nu`. The payload carries copyright text but no explicit open-data license. Polling it every 60s server-side may sit outside ANWB's terms. Confirm you're OK with this before it goes live — happy to lower cadence or add a descriptive `User-Agent` if that helps.

## Schema

One table `anwb_incident` (mirrors the `Situation` single-table-per-category pattern already used for DATEX v3 situations — one table, `category` column, shared generic geometry).

| Column | Type | Source |
|---|---|---|
| `record_id` (PK) | String | `f"{category}:{id}"` — category-qualified since raw `id` isn't guaranteed unique across categories |
| `id` | BigInteger, idx | raw `id` |
| `category` | String, idx | jams / roadworks / radars |
| `incident_type` | String | `incidentType` |
| `road` | String, idx | `road` |
| `from_label` / `to_label` | String | `from` / `to` |
| `reason` | Text | `reason` |
| `distance_m` | Integer | `distance` (jams) |
| `delay_s` | Integer | `delay` (jams) |
| `hm` | Numeric | `HM` (radars) |
| `code_direction` | Integer | `codeDirection` |
| `segment_id` | Integer | `segmentId` |
| `label` | String | `label` (roadworks) |
| `valid_from` | timestamptz | `start` |
| `poll_time` | timestamptz | payload-level `dateTime`, stamped on every row |
| `geom` | Geometry(GEOMETRY, 4326), GiST idx | see below |
| `raw` | JSONB | `events`, `bounds`, `fromLoc`, `toLoc`, `afrc`, `type` — everything not promoted to a column |
| `ingested_at` | timestamptz | set by `bulk_upsert` |

**Geometry**: LineString decoded from `polyline` for jams/roadworks; if polyline missing/empty/undecodable, fall back to a straight `fromLoc`→`toLoc` 2-point line; if that's not buildable either, `geom = NULL` (never a bare Point for these categories — the frontend `anwb_jams`/`anwb_roadworks` layers are `geomType: 'line'`, and MapLibre line layers silently drop Point features, so a lone-point fallback would just vanish instead of degrading visibly). Point from `loc` for radars. One nullable generic `GEOMETRY` column, same as `Situation.geom` — API already renders both `line` and `point` geomTypes on the frontend, so no new rendering primitive needed.

`id` can be missing or, in rare cases, duplicated within one category in a single payload — skip (log, don't crash) rows with no `id` rather than emitting a collision-prone `category:None` key; duplicate ids within a batch collapse safely via `on_conflict_do_update` (last one wins).

Migration: new Alembic revision adding `anwb_incident` with GiST index on `geom`, btree on `category`/`road`/`id`.

## Polyline decoder

Pure stdlib, no new dependency (verified against the live sample):

```python
def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Google encoded-polyline -> list of (lat, lon)."""
    coords: list[tuple[float, float]] = []
    index = lat = lng = 0
    factor = 10 ** precision
    length = len(encoded)
    while index < length:
        for is_lng in (0, 1):
            shift = result = 0
            while True:
                if index >= length:
                    return coords
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append((lat / factor, lng / factor))
    return coords


def polyline_to_wkt(encoded: str) -> str | None:
    pts = decode_polyline(encoded)
    if len(pts) < 2:
        return None
    return "LINESTRING(" + ", ".join(f"{lon} {lat}" for lat, lon in pts) + ")"
```

Validate before trusting a decode: reject (fall back to straight-line) if the result has an implausible vertex count for the segment, or any point falls outside a generous NL+border bbox (`lat 45–56`, `lon 2–9`) — catches silent corruption from a truncated/garbled string, which the raw decoder above returns partial data for rather than raising.

## Ingest pipeline

- `parsers/anwb.py` — walks `roads → segments → {jams, roadworks, radars}`, yields row dicts tagged by category, geometry via decoder/fallback above, `raw` built via existing `json_safe()`. `start` and payload `dateTime` parsed as ISO-8601 (`Z` → `+00:00`) into tz-aware UTC; tolerate missing/null by leaving the column `NULL`.
- `ingest/anwb.py` — `AnwbIncidentIngester(Ingester)`, single `_ingest()`:
  1. `json.load()` the small payload (no streaming needed — a few hundred KB).
  2. Guard: if `success` is not `True`, `roads` key missing, or none of the 3 category keys appear anywhere in the payload (structurally broken response), raise — the run logs as `error` in `FeedRun` and existing rows are left alone. A payload that *is* well-formed but genuinely empty (`roads: []`, `success: true`) is trusted and does prune everything, since that's a legitimate "no incidents" snapshot.
  3. Build rows across all 3 categories, batched through `bulk_upsert(..., ["record_id"])` in `BATCH_SIZE` chunks — same accumulate-and-flush loop as `SituationIngester` (`ingest/situations.py`), not one unbounded insert.
  4. Prune: `DELETE WHERE ingested_at < run_start` (unqualified — one fetch is the authoritative full snapshot of all 3 categories every poll, unlike the per-category `Situation` feeds which each own a slice).
- `feeds.py` — **one** new entry (not three, since it's one URL/one fetch). Ship it **disabled by default** (add to `disabled_feeds` in config, or a code-level flag) until the legal question below is resolved:
  ```python
  {"name": "anwb_incidents", "filename": "anwb_incidents.json",
   "url": "https://api.anwb.nl/routing/v1/incidents/incidents-desktop",
   "cadence_s": 60, "schedule_class": "realtime",
   "parser_fn": None, "ingester_cls": None}
  ```
  Uses the existing `url` override in `download._source_url` (bypasses NDW base join) and `open_feed`'s plain-file path (no `.gz`). Note: `feeds.py`'s `ingester_cls` field is metadata only — the poller actually dispatches through the `INGESTERS` dict in `ingest/__init__.py`, so the real wiring step is adding `"anwb_incidents": AnwbIncidentIngester()` there (every other feed follows this same two-place registration).

## API

`src/ndwinfo/api/routers/anwb.py`, prefix `/anwb`, structured exactly like `situations.py`:
- `VALID_CATEGORIES = {"jams", "roadworks", "radars"}`, `?category=` filter, 400 on unknown value.
- `ST_Intersects(AnwbIncident.geom, bbox_geom)`, typed columns + `ST_AsGeoJSON(geom, 6)`, `geo_response(make_fc(...))`.
- Registered in `api/main.py` alongside the other routers.

## Web UI — 3 layers

Added to `web/config.js` `LAYERS`, new `group: 'anwb'` — and the group itself must also be added to the separate `GROUPS` array (`config.js:321`) or the layer panel won't render a section for it at all (`buildLayerPanel()` iterates `GROUPS`, not `LAYERS`, to build sections):

```js
// in GROUPS:
{ key: 'anwb', label: 'ANWB' },
```

```js
{ key: 'anwb_jams', label: 'ANWB Jams', group: 'anwb',
  endpoint: '/anwb?category=jams', geomType: 'line', legendColor: '#ff3333',
  paint: { 'line-width': 4, 'line-color': '#ff3333' } },
{ key: 'anwb_roadworks', label: 'ANWB Roadworks', group: 'anwb',
  endpoint: '/anwb?category=roadworks', geomType: 'line', legendColor: '#ffaa00',
  paint: { 'line-width': 4, 'line-color': '#ffaa00' } },
{ key: 'anwb_radars', label: 'ANWB Speed Cameras', group: 'anwb',
  endpoint: '/anwb?category=radars', geomType: 'point', legendColor: '#00aaff',
  paint: { 'circle-radius': 6, 'circle-color': '#00aaff', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' } },
```

No new JS logic — `line`/`point` geomTypes and the layer-panel/toggle machinery already exist (`map.js:134`, `ui.js:270`, `fetch.js`).

## Attribution UI overhaul (required — replaces the corner attribution control)

ANWB's data must be credited, and per-layer attribution doesn't scale as sources keep growing (currently NDW + OSM/Geofabrik + CARTO + Esri, soon ANWB). Replacing MapLibre's default bottom-right attribution control with a button that opens a panel listing **every** source's attribution **statically** — regardless of which layers are currently toggled on. This is a small UI feature, not a per-feed one, so it's scoped and built once here rather than repeated for every future source.

**Data**: new `ATTRIBUTIONS` array in `web/config.js`, decoupled from `LAYERS`/`GROUPS` (attribution is per data *provider*, not per rendered layer):

```js
const ATTRIBUTIONS = [
  { label: 'OpenStreetMap contributors', url: 'https://www.openstreetmap.org/copyright', note: 'basemap tiles, driving-road geometry (ODbL)' },
  { label: 'CARTO', url: 'https://carto.com/attribution', note: 'basemap tiles' },
  { label: 'Esri, Maxar, Earthstar Geographics', url: 'https://www.esri.com/', note: 'satellite basemap' },
  { label: 'Nationaal Dataportaal Wegverkeer (NDW)', url: 'https://opendata.ndw.nu/', note: 'traffic, roadworks, signs, charging, truck parking, verkeersborden' },
  { label: 'ANWB', url: 'https://www.anwb.nl/', note: 'jams, roadworks, speed cameras' },
]
```
(Existing per-basemap `attribution` strings in `map.js`'s `BASEMAPS` stay as-is — MapLibre still wants them for its own internal bookkeeping/compact control removal — this array is only for the new always-visible panel.)

**UI**: mirror the existing basemap-picker pattern exactly (`index.html`'s `basemap-panel`/`basemap-toggle`/`basemap-body`, wired in `ui.js`'s `setupPanelToggles`) — a small button positioned bottom-right (replacing the default control's spot) that toggles a `.hidden` body panel listing each `ATTRIBUTIONS` entry as `label — note` with the label linking to `url`.

**map.js changes**:
- Remove `map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')` and the `styleimagemissing`-adjacent block that force-collapses the native control (`map.js:78`, `map.js:97-101` — both become dead code once the control is gone).
- Basemap tile sources keep their `attribution` field (harmless metadata MapLibre doesn't render without a control) so nothing else about the basemap wiring changes.

**index.html**: add an `attribution-panel` section (button + body div) alongside the existing `basemap-panel`/`settings-panel` markup, positioned bottom-right via `style.css`.

**style.css**: reuse existing `.panel`/`.panel-btn`/`.hidden` classes; add layout rules only for bottom-right placement and link styling inside the attribution body.

This replaces the open "attribution" question from the previous round of this plan — it's no longer a per-source afterthought, it's a standing panel that any future feed's attribution gets added to (one line in `ATTRIBUTIONS`, no UI code changes).

## Edge cases handled

- Empty/undecodable/corrupt polyline → straight-line fallback or `NULL` geom (never a bare Point on a line-typed layer), never crashes the ingest.
- Missing `id` → row skipped (logged), not stored under a colliding `category:None` key. Duplicate `id` within one payload → last-write-wins via upsert; category-qualified `record_id` prevents cross-category collision.
- Cross-border coordinates (roads reach the German border) → not clipped; bbox filtering at query time already scopes results. Decoded points outside a generous NL+border bbox are treated as decode corruption (see decoder validation above).
- Payload `success:false`, missing `roads`, or no recognizable category anywhere → ingest raises, old rows preserved, logged as `FeedRun` error (not silently wiped). A structurally valid but genuinely empty snapshot (`roads: []`) is trusted and does prune — that's a real "no incidents" state, not corruption.

## Work breakdown

1. `models.py` — `AnwbIncident` ORM model + Alembic migration.
2. `parsers/anwb.py` — polyline decoder (with validation) + row builder.
3. `ingest/anwb.py` — `AnwbIncidentIngester`.
4. `feeds.py` — register `anwb_incidents` feed entry, **disabled by default**.
5. `ingest/__init__.py` — register `"anwb_incidents": AnwbIncidentIngester()` in `INGESTERS` (the actual poller dispatch table — `feeds.py`'s `ingester_cls` field alone does nothing).
6. `api/routers/anwb.py` — new router, register in `api/main.py`. `raw` JSONB stays out of the response, typed columns only (same as `situations.py`).
7. `web/config.js` — 3 new `LAYERS` entries, an `anwb` entry in `GROUPS`, and the new `ATTRIBUTIONS` array.
8. `web/map.js` — remove the default `AttributionControl` + its forced-collapse block.
9. `web/index.html` + `web/style.css` — new attribution button/panel, positioned where the old control was.
10. `web/ui.js` — wire the attribution toggle into `setupPanelToggles`, render `ATTRIBUTIONS` into the panel body.
11. `docs/` — new `docs/08-anwb-incidents.md` (feed shape, decoder, licensing caveat) + a link from `docs/README.md`'s catalog.
12. Local verification: `docker compose up -d --build app poller`, confirm migration applies cleanly, all 3 ANWB layers render and toggle, `/api/anwb?category=...&bbox=...` returns expected GeoJSON, `FeedRun` shows `ok` status once enabled, and the new attribution panel lists all 5 sources regardless of which layers are on.

## Open question for you

**Legality/ToS of polling `api.anwb.nl`** (see flag above) — this is ANWB's app backend, not a licensed open-data feed like NDW's. Confirm you're OK with this before the feed is flipped on; plan ships it disabled by default either way. Attribution itself is now handled unconditionally by the panel above, independent of that decision.
