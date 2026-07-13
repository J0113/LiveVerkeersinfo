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
