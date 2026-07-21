# Plan: ANWB incidents + Flitspalen speed cameras (jams / roadworks / dynamic + static radars)

## Sources

### 1. ANWB incidents (jams / roadworks / dynamic radars)

`GET https://api.anwb.nl/routing/v1/incidents/incidents-desktop` — no auth, plain JSON (not gzip), single payload nationwide. Verified live: `{ success, dateTime, roads: [{ road, type, segments: [{ start, end, jams: [...], roadworks: [...], radars: [...] }] }] }`.

3 categories, one shared envelope per item (`id`, `road`, `from`/`to` + `fromLoc`/`toLoc`, `bounds`, `events[]`, `reason`, `category`):
- **jams** — `incidentType` stationary-traffic/road-closed, has `polyline`, `distance`, `delay`.
- **roadworks** — same shape + `label`, has `polyline`.
- **radars** — point only, `loc` instead of polyline, `HM` (hectometer), no line geometry. These are ANWB's *dynamic* (mobile/temporary) radar reports.

`polyline` confirmed as standard Google encoded-polyline (precision 5) — decoded first point of a sample matched its declared `bounds.southWest` exactly.

Cadence: **5 min** (`cadence_s: 300`, `schedule_class: "realtime"`) — not 60s, per your call below.

### 2. Flitspalen.nl static speed cameras

`POST https://www.flitspalen.nl/karte/` — form-encoded body (`xhr=1&action=all&latMax=53.7&lngMax=7.2&latMin=50.7&lngMin=3.2`, i.e. a bbox covering all of NL plus border), gated behind `X-Requested-With: XMLHttpRequest` + `Accept`/`Origin`/`Referer`/`Cookie: LAN=nl` headers (verified live — a plain GET or a POST missing these headers is not confirmed to work, so treat the header set as required, not decorative). Response: `{ "result": [ {...}, ... ] }`, one flat list, **all 3 Benelux+DE countries mixed together** (verified: 5040 rows returned for the test bbox — 3488 `land:"B"`, 1026 `land:"NL"`, 526 `land:"D"`). **Must filter to `land == "NL"` before storing anything**, per your instruction — this is a hard requirement, not an optimization.

This is the crowdsourced database of **fixed/permanent** speed cameras (as opposed to ANWB's dynamic radar reports above) — verified sample fields per row:

```json
{
  "id": 628, "land": "NL", "status": "A",
  "ort": "Wittem", "strasse": "N278", "beschreibung": "Kreuzung Wittemer Allee",
  "vmax": "80", "art": "GA", "type": "GA80", "richtung": "140", "drehbar": 1,
  "create_time": 1080746115, "edit_time": 1697021147,
  "lat": 50.811607, "lng": 5.915213,
  "bubble": "<div class=\"map-bubble\">...<a href=\"/blitzermeldung/id/628/action/dismantle\">...</a></div>"
}
```

- `status` (confirmed by you — "Kamerastatus" legend): `A` = actief (active, 994 rows in the NL subset), `L` = leeg (empty — housing present but no camera installed, i.e. not currently enforcing), `Z` = vernietigd (destroyed/removed, 6 rows). **Only `status == "A"` is ingested** — `L`/`Z` cameras aren't actually enforcing anything, so they're correctly excluded, now on a confirmed basis rather than a guess.
- `vmax` is a string, occasionally `"/"` or `"?"` instead of a number (109 + 41 occurrences in the NL subset) — parse to int, `NULL` on non-digit.
- `richtung` (0–359 bearing, direction of enforcement) and `drehbar` (rotatable, always `1` in the NL subset) map cleanly to columns. Re-verified against the full NL subset (1026 rows): `richtung` is *always* a plain integer 0–359, never one of the site's own "Gecontroleerde rijrichting" compass-letter codes (`N`/`NO`/`O`/`SO`/`S`/`SW`/`W`/`NW`) or the `bs` (beide zijden — both directions)/`db` (draaibaar — rotatable) markers you listed — those look like the *display* legend for the site's own map icons, derived from this same numeric bearing (and, for `db`, from the separate `drehbar` field), not alternate values of the `richtung` field itself. No special-case parsing needed for `richtung`.
- `bubble` is a raw HTML blob containing links to `/blitzermeldung/id/{id}/action/edit` and `.../action/dismantle` on flitspalen.nl itself — **never render this HTML or fetch those links**; it's third-party markup whose "buttons" mutate *their* site. Drop the field entirely (don't even keep it in `raw`).

Cadence: **weekly** (`cadence_s: 604800`, `schedule_class: "background"`) — this is a slow-changing static reference list, matching the plan's polling-cadence conventions for reference data.

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

`id` can be missing or, in rare cases, duplicated within one category in a single payload — skip (log, don't crash) rows with no `id` rather than emitting a collision-prone `category:None` key. **Correction from codex-review**: duplicate `record_id`s within the *same* batch do **not** collapse safely through `ON CONFLICT DO UPDATE` — Postgres rejects a multi-row `INSERT ... ON CONFLICT` batch that contains the same conflict key twice ("ON CONFLICT DO UPDATE command cannot affect row a second time"). Dedupe in Python by `record_id` (last-one-wins) before each `bulk_upsert` call, not after.

Migration: new Alembic revision adding `anwb_incident` with GiST index on `geom`, btree on `category`/`road`/`id`.

### Flitspalen schema

One table `flitspalen_camera`. `id` is already a global int unique across all 3 countries in the source (verified: no collisions observed), so it's used directly as PK — no category-qualified key needed since the table only ever holds NL rows.

| Column | Type | Source |
|---|---|---|
| `id` (PK) | BigInteger | `id` |
| `status` | String | `status` (raw code; only `"A"`/actief rows are ingested — `L`/leeg and `Z`/vernietigd are dropped in the parser, confirmed meaning below) |
| `city` | String, idx | `ort` |
| `street` | String | `strasse` |
| `description` | Text | `beschreibung` |
| `speed_limit_kmh` | Integer, nullable | `vmax` (digits only; `"/"`/`"?"` → `NULL`) |
| `camera_type` | String | `type` (e.g. `G50`, `GA80`, `SC`, `SCE`, `SCM`, `A`, `EK`) |
| `rotatable` | Boolean | `drehbar` |
| `bearing_deg` | Integer, nullable | `richtung` |
| `created_at` | timestamptz | `create_time` (unix epoch seconds) |
| `edited_at` | timestamptz | `edit_time` (unix epoch seconds) |
| `geom` | Geometry(POINT, 4326), GiST idx | `lat`/`lng` (identical to `breitengrad_dezimal`/`laengengrad_dezimal` in every sample checked, so those duplicates aren't stored) |
| `raw` | JSONB | `plz`, `landkreis`, `ortsteil`, `bundesland`, `info1`–`info5`, `gps_status` — everything not promoted. **Explicitly excludes `bubble`** (see security note above). |
| `ingested_at` | timestamptz | set by `bulk_upsert` |

Migration: new Alembic revision adding `flitspalen_camera` with GiST index on `geom`, btree on `city`.

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

**Correction from codex-review**: a truncated/garbled string can still decode to ≥2 valid, in-bounds points (the decoder above returns whatever prefix it managed to parse, silently, rather than signalling truncation) — the bbox/vertex-count check alone won't always catch that case. Tighten the decoder itself: raise (don't `return coords` early) if the string ends mid-coordinate-pair (i.e. `index` doesn't land exactly on `length` after a complete lat+lng pair), and cap accepted input length / vertex count / bit-shift width so a garbled string can't spin the `while True` shift loop indefinitely. Add fixed-sample unit tests (valid string, truncated string, string with an invalid byte) alongside the row-builder tests in the work breakdown below.

## Ingest pipeline

- `parsers/anwb.py` — walks `roads → segments → {jams, roadworks, radars}`, yields row dicts tagged by category, geometry via decoder/fallback above, `raw` built via existing `json_safe()`. `start` and payload `dateTime` parsed as ISO-8601 (`Z` → `+00:00`) into tz-aware UTC; tolerate missing/null by leaving the column `NULL`.
- `ingest/anwb.py` — `AnwbIncidentIngester(Ingester)`, single `_ingest()`:
  1. `json.load()` the small payload (no streaming needed — a few hundred KB).
  2. Guard: if `success` is not `True`, `roads` key missing, or none of the 3 category keys appear anywhere in the payload (structurally broken response), raise — the run logs as `error` in `FeedRun` and existing rows are left alone. A payload that *is* well-formed but genuinely empty (`roads: []`, `success: true`) is trusted and does prune everything, since that's a legitimate "no incidents" snapshot. **Hardened per codex-review**: "well-formed but empty" is too loose a bar on its own — a response missing just one of the 3 categories (e.g. `radars` silently dropped upstream) would still pass this guard and then prune every existing radar row on the next step. Guard per-category: if a category key is present in previous runs' data but entirely absent from this payload while the other categories are non-empty, treat as suspect and skip pruning that category (log a warning) rather than trusting an all-or-nothing prune.
  3. Build rows across all 3 categories, batched through `bulk_upsert(..., ["record_id"])` in `BATCH_SIZE` chunks — same accumulate-and-flush loop as `SituationIngester` (`ingest/situations.py`), not one unbounded insert. Dedupe by `record_id` within each batch first (see correction above).
  4. Prune: `DELETE WHERE ingested_at < run_start` (unqualified — one fetch is the authoritative full snapshot of all 3 categories every poll, unlike the per-category `Situation` feeds which each own a slice).
- `feeds.py` — **one** new entry (not three, since it's one URL/one fetch). Ship it **disabled by default** — concretely: add `anwb_incidents` to the `DISABLED_FEEDS` default in `docker-compose.yml` (`docker-compose.yml:34` currently only lists `verkeersborden_csv`) and to `settings.disabled_feeds`'s default in `config.py` (currently `""`) — until the legal question below is resolved:
  ```python
  {"name": "anwb_incidents", "filename": "anwb_incidents.json",
   "url": "https://api.anwb.nl/routing/v1/incidents/incidents-desktop",
   "cadence_s": 300, "schedule_class": "realtime",
   "parser_fn": None, "ingester_cls": None}
  ```
  (`cadence_s: 300` = 5 min, per your call — not the 60s originally drafted.) Uses the existing `url` override in `download._source_url` (bypasses NDW base join) and `open_feed`'s plain-file path (no `.gz`). Note: `feeds.py`'s `ingester_cls` field is metadata only — the poller actually dispatches through the `INGESTERS` dict in `ingest/__init__.py`, so the real wiring step is adding `"anwb_incidents": AnwbIncidentIngester()` there (every other feed follows this same two-place registration).

### Flitspalen ingest pipeline

- **`download.py` needs a small extension first** — it's currently GET-only (conditional-GET + Range-resume for big files), and this source is a `POST` with a form body and no `Last-Modified`/`ETag` support at all. Add an optional `method` / `form_data` / `extra_headers` set of keys read from the feed dict; when `method == "POST"`, take a separate, simpler non-resumable branch (one-shot `httpx.post(url, data=form_data, headers=extra_headers)`, write the full body, no conditional caching, no Range/resume logic). This only adds a new branch — the existing GET/resume path for every other feed is untouched. Weekly cadence + a one-shot 5.5 MB POST makes resume/conditional-GET unnecessary here. **Hardened per codex-review**: the new POST branch still needs the same operational care as the GET path even though it skips resume — write to the same `.part`-then-atomic-rename pattern as the GET branch (not a partial file left in place on failure), a bounded timeout and a small retry count (transient network failures shouldn't immediately mark the feed `error`), a response-size cap (defensive against the endpoint unexpectedly returning far more than ~5.5 MB), and a check that the response is actually JSON (content-type or a trial `json.loads`) before treating it as success — an HTML error page from the site would otherwise look like a "successful" download and only fail later, confusingly, inside the parser. Also extend `feeds.py`'s `FeedDef` type (`feeds.py:11`-ish) with the new `method`/`form_data`/`extra_headers` keys instead of leaving them as untyped dict entries only one feed uses.
- `parsers/flitspalen.py` — reads `payload["result"]`, filters to `land == "NL"` and `status == "A"`, parses `vmax` (digits → int, else `NULL`), `create_time`/`edit_time` (unix epoch seconds → tz-aware UTC), builds `Point(lng, lat)` geometry, drops `bubble`, builds `raw` from the remaining fields via existing `json_safe()`. Validate scalars defensively, not just happy-path: skip (log) a row whose `id` isn't a plausible integer, whose `lat`/`lng` are non-finite or outside the requested bbox, whose `create_time`/`edit_time` aren't parseable epochs, or whose `richtung` falls outside `0`–`359` — treat any of these as a single malformed row to drop, not a reason to fail the whole batch.
- `ingest/flitspalen.py` — `FlitspalenCameraIngester(Ingester)`, single `_ingest()`:
  1. `json.load()` the payload (~5.5 MB observed for the full 3-country bbox — small enough to load whole, no streaming needed).
  2. Guard: if `result` key missing or not a list, raise — logged as `FeedRun` error, existing rows preserved. **Hardened per codex-review**: also guard against a *structurally valid but implausibly small* response — e.g. if `land == "NL"` rows this poll are a small fraction (say <50%) of the previous successful run's count, that's a sign of a truncated/partial fetch from the source, not a real mass-removal of speed cameras in a week. Treat that case like the malformed-response case (raise, skip pruning, keep old rows) rather than trusting it.
  3. Build rows (NL + status-A only), deduped by `id` within the batch (last-one-wins, see correction above), batched through `bulk_upsert(..., ["id"])` in `BATCH_SIZE` chunks, same pattern as `AnwbIncidentIngester`/`SituationIngester`.
  4. Prune: `DELETE WHERE ingested_at < run_start` (unqualified — one fetch is a full snapshot of all active NL cameras every poll), gated by the row-count sanity check in step 2.
- **Idle-starvation risk (codex-review finding)**: `schedule_class: "background"` feeds only run once the API has been idle for `poller_idle_timeout_s` (`poller.py`'s `_idle_for`/idle-gating logic) — on a deployment that's continuously browsed, a background feed can be deferred indefinitely and never actually hit its weekly cadence. Since Flitspalen data barely changes, occasional multi-week staleness may be an acceptable trade — but that's a call for you, not an assumption baked in silently. If it matters, `schedule_class: "maintenance"` (a separate, typically longer idle threshold, per `poller.py`'s `idle_threshold` branch) or a poller-level max-deferral override is the fix; flagged as an open question below.
- `feeds.py` — one new entry, **disabled by default** — concretely, same mechanism as `anwb_incidents` above (add to `DISABLED_FEEDS` in `docker-compose.yml` and `disabled_feeds`'s default in `config.py`) — until the legal question below is resolved:
  ```python
  {"name": "flitspalen_cameras", "filename": "flitspalen_cameras.json",
   "url": "https://www.flitspalen.nl/karte/",
   "method": "POST",
   "form_data": {"xhr": "1", "action": "all",
                 "latMax": "53.7", "lngMax": "7.2", "latMin": "50.7", "lngMin": "3.2"},
   "extra_headers": {
       "Accept": "application/json, text/javascript, */*; q=0.01",
       "X-Requested-With": "XMLHttpRequest",
       "Origin": "https://www.flitspalen.nl",
       "Referer": "https://www.flitspalen.nl/karte/",
       "Cookie": "LAN=nl",
   },
   "cadence_s": 604800, "schedule_class": "background",
   "parser_fn": None, "ingester_cls": None}
  ```
  Register `"flitspalen_cameras": FlitspalenCameraIngester()` in `ingest/__init__.py`'s `INGESTERS` dict, same two-place registration as every other feed.

## API

`src/ndwinfo/api/routers/anwb.py`, prefix `/anwb`, structured exactly like `situations.py`:
- `VALID_CATEGORIES = {"jams", "roadworks", "radars"}`, `?category=` filter, 400 on unknown value.
- `ST_Intersects(AnwbIncident.geom, bbox_geom)`, typed columns + `ST_AsGeoJSON(geom, 6)`, `geo_response(make_fc(...))`.
- Registered in `api/main.py` alongside the other routers.

`src/ndwinfo/api/routers/flitspalen.py`, prefix `/flitspalen`, same shape minus the category filter (single camera type per row):
- `ST_Intersects(FlitspalenCamera.geom, bbox_geom)`, typed columns + `ST_AsGeoJSON(geom, 6)`, `geo_response(make_fc(...))`.
- `raw` JSONB stays out of the response, same as every other router.
- Registered in `api/main.py` alongside the other routers.

## Web UI — 4 layers, split across "Traffic" and "Situations"

**No new group.** An earlier draft of this plan put the 3 ANWB layers in their own new `group: 'anwb'` — that appended a new section that rendered right below "Traffic" in the panel, which reads wrong. Per your calls: `anwb_radars`, `flitspalen_cameras`, and `anwb_roadworks` go into the **existing** `group: 'situations'`; `anwb_jams` goes into the **existing** `group: 'traffic'` instead (jams are traffic-flow data, not a situation). Both groups already exist (`config.js:322`-`323`) — no `GROUPS` array change needed either way.

**Order matters**: `buildLayerPanel()` (`ui.js:73`) does `LAYERS.filter(l => l.group === group.key)`, and `Array.filter` preserves declaration order — so panel order within each section is exactly `LAYERS` array order.

**Situations** — per your instruction, insert these 3 entries **before** the existing `sit_incident` entry (`config.js:65`), in this order:

1. **ANWB Speedcamera's** (`anwb_radars`) — top
2. **Speedcamera's** (`flitspalen_cameras`) — directly below it, per your instruction
3. **ANWB Roadworks** (`anwb_roadworks`)

```js
// inserted into LAYERS, immediately before the sit_incident entry:
{ key: 'anwb_radars', label: "ANWB Speedcamera's", group: 'situations',
  endpoint: '/anwb?category=radars', geomType: 'point', legendColor: '#00aaff',
  paint: { 'circle-radius': 6, 'circle-color': '#00aaff', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' } },
{ key: 'flitspalen_cameras', label: "Speedcamera's", group: 'situations',
  endpoint: '/flitspalen', geomType: 'point', legendColor: '#aa33ff',
  paint: { 'circle-radius': 6, 'circle-color': '#aa33ff', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' } },
{ key: 'anwb_roadworks', label: 'ANWB Roadworks', group: 'situations',
  endpoint: '/anwb?category=roadworks', geomType: 'line', legendColor: '#ffaa00',
  paint: { 'line-width': 4, 'line-color': '#ffaa00' } },
```

**Traffic** — per your instruction, `anwb_jams` moves here instead of Situations. You didn't specify a position within Traffic, so it's appended after the existing `traveltime` entry (`config.js:37`, the last of the 3 current Traffic layers) — flag if you want it positioned differently (e.g. above `speed`/`speed_points`):

```js
// appended into LAYERS, after the traveltime entry:
{ key: 'anwb_jams', label: 'ANWB Jams', group: 'traffic',
  endpoint: '/anwb?category=jams', geomType: 'line', legendColor: '#ff3333',
  paint: { 'line-width': 4, 'line-color': '#ff3333' } },
```

Distinct `legendColor`/`circle-color` (`#aa33ff` vs ANWB radar's `#00aaff`) so the two speed-camera sources stay visually distinguishable on the map when both are on. No new JS logic otherwise — `line`/`point` geomTypes and the layer-panel/toggle machinery already exist (`map.js:134`, `ui.js:270`, `fetch.js`).

**Missing-step flagged by codex-review — default row limit will silently truncate the Flitspalen layer.** `fetch.js:34` builds `/api${layer.endpoint}${sep}bbox=${bbox}` with no `limit` param, so every layer rides on `settings.api_default_limit` (500 per `CLAUDE.md`/`config.py`). The verified NL subset has **994 active (`status:"A"`) cameras** nationwide — a national/zoomed-out viewport bbox would hit the 500-row cap and silently drop roughly half the cameras with no visual indication. Options: add a per-layer `limit` override in the `flitspalen_cameras` `LAYERS` entry (raised toward `api_max_limit`, since this is a small fixed dataset, not an unbounded feed like verkeersborden), or confirm `fetch.js` surfaces truncation (e.g. a "500 of 994 shown, zoom in" indicator) so it degrades visibly instead of silently. Needs a decision before the layer ships.

## Attribution UI overhaul (required — replaces the corner attribution control)

ANWB's and Flitspalen's data must both be credited, and per-layer attribution doesn't scale as sources keep growing (currently NDW + OSM/Geofabrik + CARTO + Esri, soon ANWB + Flitspalen). Replacing MapLibre's default bottom-right attribution control with a button that opens a panel listing **every** source's attribution **statically** — regardless of which layers are currently toggled on. This is a small UI feature, not a per-feed one, so it's scoped and built once here rather than repeated for every future source.

**Data**: new `ATTRIBUTIONS` array in `web/config.js`, decoupled from `LAYERS`/`GROUPS` (attribution is per data *provider*, not per rendered layer):

```js
const ATTRIBUTIONS = [
  { label: 'OpenStreetMap contributors', url: 'https://www.openstreetmap.org/copyright', note: 'basemap tiles, driving-road geometry (ODbL)' },
  { label: 'CARTO', url: 'https://carto.com/attribution', note: 'basemap tiles' },
  { label: 'Esri, Maxar, Earthstar Geographics', url: 'https://www.esri.com/', note: 'satellite basemap' },
  { label: 'Nationaal Dataportaal Wegverkeer (NDW)', url: 'https://opendata.ndw.nu/', note: 'traffic, roadworks, signs, charging, truck parking, verkeersborden' },
  { label: 'ANWB', url: 'https://www.anwb.nl/', note: 'jams, roadworks, dynamic speed cameras' },
  { label: 'Flitspalen.nl', url: 'https://www.flitspalen.nl/', note: 'static speed camera locations' },
]
```
(Existing per-basemap `attribution` strings in `map.js`'s `BASEMAPS` stay as-is — MapLibre still wants them for its own internal bookkeeping/compact control removal — this array is only for the new always-visible panel.)

**UI**: mirror the existing basemap-picker pattern exactly (`index.html`'s `basemap-panel`/`basemap-toggle`/`basemap-body`, wired in `ui.js`'s `setupPanelToggles`) — a small button positioned bottom-right (replacing the default control's spot) that toggles a `.hidden` body panel listing each `ATTRIBUTIONS` entry as `label — note` with the label linking to `url`.

**map.js changes**:
- Remove `map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')` and the `styleimagemissing`-adjacent block that force-collapses the native control (`map.js:78`, `map.js:97-101` — both become dead code once the control is gone).
- Basemap tile sources keep their `attribution` field (harmless metadata MapLibre doesn't render without a control) so nothing else about the basemap wiring changes.

**Caution flagged by codex-review**: don't remove the native `AttributionControl` until each provider's actual attribution terms are checked — CARTO's and Esri's basemap terms in particular may specify *how* attribution must be displayed (e.g. always-visible vs. behind a click), and a custom panel that requires an extra click to reveal could fall short of "the same visibility" some tile providers require. If that's not already been verified, the safer sequencing is: ship the new attribution panel as an *addition* first, confirm it satisfies every provider's terms, then remove the native control in a follow-up — rather than swapping both in one step.

**index.html**: add an `attribution-panel` section (button + body div) alongside the existing `basemap-panel`/`settings-panel` markup, positioned bottom-right via `style.css`.

**style.css**: reuse existing `.panel`/`.panel-btn`/`.hidden` classes; add layout rules only for bottom-right placement and link styling inside the attribution body.

This replaces the open "attribution" question from the previous round of this plan — it's no longer a per-source afterthought, it's a standing panel that any future feed's attribution gets added to (one line in `ATTRIBUTIONS`, no UI code changes).

## Edge cases handled

**ANWB:**
- Empty/undecodable/corrupt polyline → straight-line fallback or `NULL` geom (never a bare Point on a line-typed layer), never crashes the ingest.
- Missing `id` → row skipped (logged), not stored under a colliding `category:None` key. Duplicate `id` within one payload → last-write-wins via upsert; category-qualified `record_id` prevents cross-category collision.
- Cross-border coordinates (roads reach the German border) → not clipped; bbox filtering at query time already scopes results. Decoded points outside a generous NL+border bbox are treated as decode corruption (see decoder validation above).
- Payload `success:false`, missing `roads`, or no recognizable category anywhere → ingest raises, old rows preserved, logged as `FeedRun` error (not silently wiped). A structurally valid but genuinely empty snapshot (`roads: []`) is trusted and does prune — that's a real "no incidents" state, not corruption.

**Flitspalen:**
- Response mixes 3 countries in one flat list (verified: NL/B/D) → hard-filtered to `land == "NL"` in the parser, never stored otherwise.
- `status` other than `"A"` → row skipped entirely (not stored). Confirmed meaning: `L` = leeg (empty housing, not enforcing), `Z` = vernietigd (destroyed/removed) — both correctly excluded.
- `vmax` non-numeric (`"/"`, `"?"`, observed 109 + 41 times in the NL subset) → `speed_limit_kmh = NULL`, not a crash.
- `bubble` HTML (contains links to flitspalen.nl's own edit/dismantle actions) → dropped entirely, never stored, never rendered — avoids both an XSS surface and any risk of a rendered "dismantle" link being clicked against the source site.
- `result` missing or not a list (structurally broken response) → ingest raises, old rows preserved, logged as `FeedRun` error, same pattern as ANWB.
- Endpoint requires `X-Requested-With`/`Origin`/`Referer`/`Cookie` headers to serve JSON (verified with them present; not verified without) → these are sent unconditionally on every poll, not treated as optional.

## Work breakdown

1. `models.py` — `AnwbIncident` + `FlitspalenCamera` ORM models + one Alembic migration for both.
2. `parsers/anwb.py` — polyline decoder (with validation) + row builder.
3. `parsers/flitspalen.py` — `land`/`status` filtering, `vmax`/timestamp parsing, row builder, `bubble` dropped.
4. `ingest/anwb.py` — `AnwbIncidentIngester`.
5. `ingest/flitspalen.py` — `FlitspalenCameraIngester`.
6. `download.py` — add `method`/`form_data`/`extra_headers` support (POST branch, no resume/conditional-GET) needed by the Flitspalen feed; existing GET/resume path for all other feeds untouched.
7. `feeds.py` — register `anwb_incidents` (`cadence_s: 300`) and `flitspalen_cameras` (`cadence_s: 604800`) feed entries, **both disabled by default**.
8. `ingest/__init__.py` — register `"anwb_incidents": AnwbIncidentIngester()` and `"flitspalen_cameras": FlitspalenCameraIngester()` in `INGESTERS` (the actual poller dispatch table — `feeds.py`'s `ingester_cls` field alone does nothing).
9. `api/routers/anwb.py` + `api/routers/flitspalen.py` — new routers, both registered in `api/main.py`. `raw` JSONB stays out of the response, typed columns only (same as `situations.py`).
10. `web/config.js` — 4 new `LAYERS` entries (`anwb_radars`, `flitspalen_cameras`, `anwb_roadworks` in `group: 'situations'`; `anwb_jams` in `group: 'traffic'`; ordered per above — no `GROUPS` change needed, both groups already exist), and the updated `ATTRIBUTIONS` array (ANWB + Flitspalen).
11. `web/map.js` — remove the default `AttributionControl` + its forced-collapse block.
12. `web/index.html` + `web/style.css` — new attribution button/panel, positioned where the old control was.
13. `web/ui.js` — wire the attribution toggle into `setupPanelToggles`, render `ATTRIBUTIONS` into the panel body.
14. `docs/` — new `docs/08-anwb-incidents.md` (ANWB feed shape, decoder, licensing caveat) + new `docs/09-flitspalen-speedcameras.md` (Flitspalen feed shape, `status`-code caveat, licensing caveat) + links from `docs/README.md`'s catalog.
15. Tests (added per codex-review — the earlier draft had no test step): duplicate-`record_id`/`id` dedup before `bulk_upsert`, the tightened polyline decoder against fixed valid/truncated/invalid-byte samples, partial/empty-snapshot pruning guards (both feeds), `land`/`status` filtering, the new `download.py` POST branch (including a regression check that the existing GET/resume path is unaffected), the new migration, API row limits, and attribution panel rendering.
16. Local verification: `docker compose up -d --build app poller`, confirm migration applies cleanly, 3 layers render/toggle under "Situations" and `anwb_jams` renders/toggles under "Traffic" in the specified order, `/api/anwb?category=...&bbox=...` and `/api/flitspalen?bbox=...` return expected GeoJSON, `FeedRun` shows `ok` status once both feeds are enabled, and the attribution panel lists all 6 sources regardless of which layers are on.

## Decisions

- **Flitspalen `status` codes** — confirmed by you: `A` = actief (ingested), `L` = leeg (empty housing, excluded), `Z` = vernietigd (destroyed, excluded). Plan's original `status == "A"`-only default was correct; now documented as confirmed fact rather than a guess.
- **`richtung` field** — re-verified against the full NL subset (1026 rows): always a plain integer 0–359, never the site's own compass-letter/`bs`/`db` display codes. No parsing change needed.
- **`anwb_jams` placement** — moves to `group: 'traffic'` per your instruction (not `'situations'`), appended after `traveltime`. `anwb_radars`, `flitspalen_cameras`, `anwb_roadworks` stay in `'situations'` at the top, per the earlier ordering.
- **Flitspalen idle-starvation** — accepted as-is; `schedule_class: "background"` stays, no max-deferral override needed.

Attribution itself is handled unconditionally by the panel above, independent of any of these decisions.
