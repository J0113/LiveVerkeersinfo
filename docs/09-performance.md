# 09 — Performance and resource profile

The application is optimized around viewport-bounded work and independently
scaled responsibilities.

## Containers and dependencies

A single Dockerfile builds one image, used by `migrate`, `poller`, and `app`
with different commands (CI/CD pipelines expect one image to publish).

The library default database pool is 20 persistent connections with 10
overflow (`db_pool_size`/`db_max_overflow` in `src/ndwinfo/config.py`).
`docker-compose.yml` overrides this down to 4/2 per service
(`DB_POOL_SIZE:-4`/`DB_MAX_OVERFLOW:-2`) — that smaller figure is a deployment
choice for this compose file, not the code's own default.

## API and matching

GeoJSON responses go through FastAPI's `GZipMiddleware` (min size 1024 bytes,
compresslevel 5). **There is no response caching layer** — no bounded cache on
the live lane endpoint, no NWB/WEGGEG TTL/LRU cache, no keyed lock coalescing
downloads: those all belonged to an earlier NWB/WEGGEG-based road-matching
design that has since been replaced (see [docs/10](10-carriageway-direction-quality.md)'s
"Implemented 2026-07-20" banner) by the OSM-PBF pipeline in [docs/11](11-osm-pbf.md).
There is no `/lane-speeds` route and no PDOK dependency anywhere in `src/`.

Speed-sensor-to-lane matching (`_attach_osm_matches` in
`src/ndwinfo/api/routers/traffic.py`) is a single raw-SQL query per request:
it geography-casts candidate `osm_road_lane` rows and the sensor points and
uses `ST_DWithin`/`ST_Distance` against the `osm_road_lane` GiST index, with a
25m candidate radius (`OSM_MATCH_MAX_DISTANCE_M`). `_pick_osm_candidate` then
ranks candidates by road-reference/lane-count agreement ahead of angle and
distance (see [docs/01](01-traffic-realtime.md#map-driving-hud)). There is no
Python-side Shapely/STRtree step — the whole distance query runs in Postgres.

## Browser lifecycle

- map movements are debounced and stale requests are aborted;
- `viewportBbox()` (`web/fetch.js`) pads the visible viewport by 75% of its
  span (min 0.015°/0.010°) and sources are retained while the map stays inside
  that padded box, avoiding source replacement on small pans and zoom-ins;
- background tabs do not poll and refresh once when they become active
  (`web/map.js`'s two 60-second `setInterval` timers, gated on
  `document.visibilityState`);
- zoom-gated requests (e.g. the verkeersborden zoom-fetch) run on the
  continuous `zoom` event (`map.on('zoom', …)` in `web/map.js`), **not**
  `zoomend`, so they fire throughout a zoom gesture rather than once at the end;
- marker rotation/layout (`updateMatrixLayout`, `updateSpeedLayout`) runs
  synchronously inside the `zoom`/`rotate` handlers — there is no
  `requestAnimationFrame` coalescing there. The one rAF loop in the app is the
  GPS follow-camera smoothing loop (`web/gps.js`), unrelated to marker layout;
- dense legacy speed and matrix-marker layers remain available but default off.

The layer panel collapses by default on screens up to 720 px. The responsive
legend, safe-area spacing, reduced-motion support, and non-overlapping panels
keep the map usable without adding another frontend framework or asset bundle.

## Measuring

Use a representative motorway viewport and measure both cold and warm calls:

```bash
curl --compressed -o /dev/null -w '%{size_download} %{time_total}\n' \
  'http://localhost:3500/api/traffic/speed/map?bbox=4.74,52.30,4.82,52.35'
docker stats --no-stream
docker images 'liveverkeersinfo-*'
```

The matching query uses the `osm_road_lane` geography GiST index for its 25m
candidate search. Optimize using representative bounded viewports and query
plans, not nationwide unbounded requests or synthetic frontend-only timings.
