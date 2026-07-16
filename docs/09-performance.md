# 09 — Performance and resource profile

The application is optimized around viewport-bounded work and independently
scaled responsibilities.

## Containers and dependencies

A single Dockerfile builds one image, used by `migrate`, `poller`, and `app`
with different commands (CI/CD pipelines expect one image to publish).

The default database pool is four persistent connections with two overflow
connections. These values can be overridden through the documented
environment variables.

## API and matching

GeoJSON uses compact serialization and FastAPI gzip compression. The live lane
endpoint has a ten-second bounded cache: this coalesces simultaneous clients
without hiding the approximately 60-second NDW update cadence. Monthly NWB and
WEGGEG reference responses retain their longer bounded TTL/LRU caches.
Concurrent cache misses for the same NWB or WEGGEG viewport are coalesced with
a bounded keyed lock, preventing `/roads` and `/lane-speeds` from downloading
the same PDOK geometry simultaneously.

Lane matching transforms visible reference segments to RD New once per
request and uses a Shapely/GEOS `STRtree` distance query. Only nearby segments
are then checked for road number, carriageway, heading, and final distance.
CPU-heavy matching and the synchronous database query run outside the async
event loop.

## Browser lifecycle

- map movements are debounced and stale requests are aborted;
- NWB and lane sources load a 35% buffered viewport and are retained while the
  visible map remains inside it, avoiding source replacement on small pans and
  zoom-ins;
- static NWB/reference layers are excluded from the 60-second live timer;
- background tabs do not poll and refresh once when they become active;
- zoom-gated requests run on `zoomend`, not every animation frame;
- marker rotation/layout is coalesced through `requestAnimationFrame`;
- dense legacy speed and matrix-marker layers remain available but default off.

The layer panel collapses by default on screens up to 720 px. The responsive
legend, safe-area spacing, reduced-motion support, and non-overlapping panels
keep the map usable without adding another frontend framework or asset bundle.

## Measuring

Use a representative motorway viewport and measure both cold and warm calls:

```bash
curl --compressed -o /dev/null -w '%{size_download} %{time_total}\n' \
  'http://localhost:3500/api/nwb/lane-speeds?bbox=4.74,52.30,4.82,52.35&zoom=14'
docker stats --no-stream
docker images 'liveverkeersinfo-*'
```

Cold calls include PDOK reference retrieval when its cache is empty. Warm calls
should report `X-Lane-Response-Cache: HIT`. Optimize using these measurements,
not nationwide unbounded requests or synthetic frontend-only timings.

## Local control baseline — 2026-07-16

Measured on the local regional stack; these are not production SLOs:

| Request/work | Result |
|---|---|
| road corridor, 26 segments | about 57-59 ms median; 8.6 KB gzip |
| connected path, 8 segments | about 48 ms median; 1.8 KB gzip |
| road viewport, 214 segments | about 80-90 ms; 33.5 KB gzip |
| capped road viewport, 2,000 segments | about 390 ms; 293 KB gzip / 2.57 MB JSON |
| national speed-map request, 500 output points | 3.27 s; 438 KB JSON; 35,951 grouped rows materialized |
| full regional source binding | 14.6 s for 16,187 locations |

The corridor geography query itself used the PostGIS index and took about
11.9 ms in the captured plan. The immediate P0 is the speed-map endpoint: its
response cap is applied after the large query result and Python merge. See
[the driver validation audit](16-driver-validation-audit.md) and OSM-V08 in
[the production backlog](11-osm-production-backlog.md).

Browser main-thread duration, heap and battery use were not instrumented in
this run. Do not infer those improvements from API timing; OSM-V11 requires
real mobile traces and explicit budgets.
