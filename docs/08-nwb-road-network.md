# 08 — NWB road-network rendering foundation

The map's road geometry comes from the Rijkswaterstaat **Nationaal Wegenbestand
(NWB) — Wegen** collection published by PDOK:

- OGC API Features landing page: <https://api.pdok.nl/rws/nationaal-wegenbestand-wegen/ogc/v1>
- collection: `wegvakken`
- license: CC0 1.0
- authentication: none
- publication frequency: monthly

The OGC API Features service was selected instead of committing the nationwide
shapefile because it is an official maintained service, accepts a bounding box,
returns GeoJSON, and preserves identifiers and road metadata needed for future
traffic matching. The application proxies PDOK through `/api/nwb/roads` so the
upstream schema, limits, errors, and cache policy remain outside UI code.

## Geometry, CRS, and level of detail

PDOK GeoJSON is explicitly requested in **OGC CRS84**. Its coordinate order is
longitude, latitude and is directly compatible with MapLibre. Invalid or empty
line geometry is discarded by the typed NWB transformation module.

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

Map detail is bounded by zoom:

| Zoom | Upstream subset | Purpose |
|---|---|---|
| `< 9` | none | avoid a national request |
| `9–10` | Rijkswaterstaat-managed roads | national major-road overview |
| `11` | national + provincial managed roads | regional major-road overview |
| `12+` | all road sections in the viewport | detailed local network |

The PDOK Core API exposes server-side equality filtering for road-manager type,
but no FRC range filter. The lower-zoom profiles use that supported server-side
filter instead of downloading all local streets and filtering them in the
browser. Responses are paginated up to the configured cap. If the cap is hit,
the response is marked as truncated and the UI asks the user to zoom in.

## Configuration and caching

All settings are optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `NWB_PDOK_URL` | official PDOK `wegvakken/items` URL | upstream endpoint override |
| `NWB_REQUEST_TIMEOUT_S` | `20` | upstream request timeout |
| `NWB_CACHE_TTL_S` | `3600` | in-process successful-response TTL |
| `NWB_CACHE_MAX_ENTRIES` | `128` | server LRU entry cap |
| `NWB_MAX_FEATURES` | `5000` | maximum features per viewport response |
| `NWB_DIAGNOSTIC_MODE` | `false` | enable developer segment inspection |

Successful normalized responses are cached in a bounded server-side TTL/LRU
cache keyed by rounded bbox and detail profile. The browser keeps a second
bounded five-minute cache so the 60-second refresh and repeated viewport do not
re-fetch monthly reference geometry. Failed responses are never cached. Map
movement is debounced for 300 ms and an `AbortController` cancels stale browser
requests. A transient failure preserves the last successfully rendered roads.

Set `NWB_DIAGNOSTIC_MODE=true` and restart the app to show an **NWB diagnostics**
badge. Clicking a road then displays its stable UUID, numeric `wvk_id`, OpenLR,
junction ids, road/direction/carriageway fields, FRC/FOW, and kilometrage. The
flag exposes no secret and is returned through `/api/config`.

## Future live-traffic matching

`src/ndwinfo/nwb.py` defines the stable `NwbRoadSegment` model and
`TrafficMatchObservation` extension point. A future matcher should use this
order, retaining match provenance and confidence:

1. exact explicit `wvk_id` / stable segment UUID when a source supplies it;
2. OpenLR decoding/matching where compatible references exist;
3. road number + kilometre + direction/carriageway metadata;
4. spatial nearest-segment matching constrained by heading, road class, and a
   conservative distance threshold.

The resulting observation can populate the reserved `traffic_state` property
or a joined live source keyed by `segment_id`, allowing MapLibre styling to
change without replacing the NWB geometry architecture. NDW's per-lane sensor
values must remain observations attached to a road section unless a separate,
authoritative lane geometry source is introduced.

## Lane configuration and current speed rendering

The high-zoom **Speed per Lane** layer uses the official RWS WEGGEG OGC API
Features collection [`wegvak_rijstroken`](https://api.pdok.nl/rws/weggegevens/ogc/v1/collections/wegvak_rijstroken/items?f=html).
WEGGEG is monthly, CC0, requires no authentication, and currently covers
Rijkswegen. Its `wvk_id` is joined directly to NWB `wvk_id`; descriptions such
as `2 -> 2` or `3 -> 4` supply the number of lanes at the beginning and end of
the section.

Crucially, WEGGEG repeats the road-section line and does **not** publish a
surveyed line for each painted lane. At zoom 13+ the UI therefore creates
parallel MapLibre line offsets solely for legibility. Every feature says
`geometry_kind=schematic-lane-offset`; variable configurations retain both
counts and are never presented as surveyed lane geometry. NDW lane 1 is shown
nearest the median (far left in the travel direction), following the official
[NDW lane numbering](https://docs.ndw.nu/locatiereferentie/locatiereferentie-aanvullend/).

Current NDW measurements are attached conservatively. A candidate must have
the same normalized road number and compatible carriageway, lie within 45 m,
and (when a bearing is available) differ by at most 50 degrees from the road
direction. Each site is assigned to only its best candidate. Concurrent values
on one lane are averaged using `n_inputs` as weight. Measurements older than
ten minutes do not colour the road. The popup exposes match confidence,
distance, NWB id, timestamp, sample count, and the schematic-geometry caveat.

This is the most accurate representation the current open sources support. A
future true lane geometry source can replace the visual offsets while keeping
the stable `wvk_id`/lane feature ids and the NDW matching layer. Further
production hardening should add OpenLR decoding or kilometre-chainage matching,
calibrate thresholds against labelled matches, and retain short time windows
if a rolling rather than current-snapshot average is desired.
