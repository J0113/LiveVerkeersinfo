# 08 — NWB road-network foundation

The map's road geometry comes from Rijkswaterstaat's **Nationaal Wegenbestand
(NWB) — Wegvakken**, ingested whole-country into PostGIS rather than proxied
live per viewport:

- source: `Wegvakken.gpkg` — <https://downloads.rijkswaterstaatdata.nl/nwb-wegen/geogegevens/geopackage/NWB-dagelijks/Wegvakken/Wegvakken.gpkg>
- format: GeoPackage, RD New (EPSG:28992), ~1.6M `LineString` road sections
- license: CC0-equivalent open data, no authentication
- publication frequency: daily (fixed URL, no versioned filename)

RWS also publishes NWB through a PDOK OGC API Features service and monthly
versioned bulk exports. The daily fixed-URL GeoPackage was chosen over both:
it needs no per-viewport upstream request at read time (matching this
project's "store + spatially index + serve area subsets" model, see
[CLAUDE.md](../CLAUDE.md)), and its stable filename means no per-run URL
computation, unlike the monthly `01-MM-YYYY.zip` snapshots.

## Ingest

`src/ndwinfo/parsers/nwb_gpkg.py` streams the GeoPackage in windows (via
pyogrio's `skip_features`/`max_features`, since it has no native streaming
batch API) rather than loading all ~1.6M rows at once. Each window is
reprojected RD→WGS84 with `geopandas.GeoDataFrame.to_crs`. Field names are
RWS's own NWB schema (uppercase: `WVK_ID`, `WEGBEHSRT`, `RIJRICHTNG`, `FRC`,
`OPENLR`, `WEGNR_HMP`, …), not PDOK's lowercase OGC API property names.

`src/ndwinfo/ingest/nwb.py` (`NwbWegvakkenIngester`, feed `nwb_wegvakken`,
daily cadence) upserts into `nwb_road_segment` keyed by `wvk_id` — RWS's own
identifier, used directly as primary key since the bulk export has no
separate synthetic feature UUID. After upserting, it prunes any row not
touched this run (`ingested_at < run_start`) so renumbered/decommissioned
sections drop out, matching the project's latest-snapshot model.

The download itself has been observed to drop mid-stream around ~400MB of the
~1GB file (an intermediate proxy closing a long-lived connection, not a
`Range`-support problem — the server does support `Accept-Ranges: bytes`).
`download.fetch()` resumes such drops with a `Range` request continuing from
the last byte written, up to `MAX_RESUME_ATTEMPTS` (5), rather than restarting
the whole download.

After a full bulk load, run `ANALYZE nwb_road_segment;` — the planner's row
estimate for the fresh GiST index can otherwise be stale enough to pick a bad
plan for the first few queries.

## Geometry, CRS, and level of detail

NWB `wegvakken` are **line centerlines**, not pavement polygons. Official NWB
documentation says the geographic model consists of point and line objects.
Physically separated carriageways are represented as separate road sections
where registered, and attributes such as `BST_CODE`, `RIJRICHTNG`, `RPE_CODE`,
and `POS_TV_WOL` describe carriageway type, direction, and relative position.

NWB does **not** provide one geometry per painted traffic lane. A lane separated
only by road markings must therefore not be rendered as a separate NWB line.
Some physically separated special lanes or carriageways can be separate road
sections, but this must not be interpreted as complete lane-level coverage.
See the official [NWB basis structure](https://docs.ndw.nu/handleidingen/nwb/nwb-basisstructuur/)
and [carriageway subtype rules](https://docs.ndw.nu/handleidingen/nwb/nwb-basisstructuur/baansubsoort/).

`GET /api/nwb/roads` queries `nwb_road_segment` with `ST_Intersects` against
the requested bbox. Map detail is still bounded by zoom — now via a SQL
`WHERE road_manager_type IN (...)` filter plus `LIMIT`, rather than shaping
an upstream request:

| Zoom | Filter | Row cap | Purpose |
|---|---|---|---|
| `< 9` | — | 0 (hidden) | avoid rendering the full national network |
| `9–10` | `road_manager_type = 'R'` | 2500 | national major-road overview |
| `11` | `road_manager_type IN ('R','P')` | 4000 | regional major-road overview |
| `12+` | none | `NWB_MAX_FEATURES` (default 5000) | detailed local network |

If the cap is hit, the response is marked `metadata.truncated: true` and the
UI asks the user to zoom in.

## Configuration

All settings are optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `NWB_WEGVAKKEN_URL` | official RWS `Wegvakken.gpkg` URL | daily bulk-download source, used by the poller |
| `NWB_MAX_FEATURES` | `5000` | per-viewport row cap for `/api/nwb/roads` |
| `NWB_DIAGNOSTIC_MODE` | `false` | enable developer segment inspection |

The browser keeps a bounded five-minute client-side cache so the 60-second
refresh and repeated viewport don't re-fetch unchanged geometry. Map movement
is debounced for 300 ms and an `AbortController` cancels stale browser
requests.

Set `NWB_DIAGNOSTIC_MODE=true` and restart the app to show an **NWB diagnostics**
badge. Clicking a road then displays its stable `wvk_id`, OpenLR, junction ids,
road/direction/carriageway fields, FRC/FOW, and kilometrage. The flag exposes
no secret and is returned through `/api/config`.

## Future live-traffic matching

`src/ndwinfo/nwb.py` defines the `TrafficMatchObservation` extension point,
kept independent of both the ingest pipeline and any particular live-traffic
source. A future matcher should use this order, retaining match provenance
and confidence:

1. exact explicit `wvk_id` when a source supplies it;
2. OpenLR decoding/matching where compatible references exist;
3. road number + kilometre + direction/carriageway metadata;
4. spatial nearest-segment matching constrained by heading, road class, and a
   conservative distance threshold.

The resulting observation can populate the reserved `traffic_state` property
(always `null` today) or a joined live source keyed by `segment_id`, allowing
MapLibre styling to change without replacing the NWB geometry architecture.
NDW's per-lane sensor values must remain observations attached to a road
section unless a separate, authoritative lane geometry source is introduced.
