# OSM × live speed proof of concept

This POC tests one narrow hypothesis: can a small set of directed OpenStreetMap
roads be drawn and identified reliably enough to associate the existing NDW
measurement points and live speed values with them?

It is intentionally separate from the production NWB/WEGGEG path. No database
migration is required and no OSM id is treated as a permanent internal id.

## Flow

```text
small map viewport (zoom 13+)
        ↓
/api/poc/osm/roads
        ↓
bounded Overpass query + 30 minute in-memory grid cache
        ↓
OSM ways → directed edge candidates
        ↓
current /api/traffic/speed measurement points
        ↓
road + carriageway + bearing + lanes + distance candidate ranking
        ↓
OSM road GeoJSON coloured by linked NDW speed
```

The endpoint uses `major` roads at zoom 13–14 and adds detailed drivable roads
from zoom 15. Requested areas are deliberately small. Public Overpass is a POC
dependency only; a production implementation would import a Netherlands PBF and
apply replication diffs locally.

## Directed edge semantics

- `oneway=yes|true|1`: OSM node order is the travel direction.
- `oneway=-1`: coordinates are reversed.
- roundabouts without an explicit oneway tag are treated as one-way.
- normal two-way ways create forward and backward candidates.
- each feature exposes an `edge_id` such as `osm:12345:f`, but this is only a
  POC identifier. OSM way splits/merges require a separate stable surrogate and
  lineage in a production graph.

The inspector also exposes road class, name/ref, `carriageway_ref`, lane counts,
turn/change/destination lane tags, static/conditional maxspeed, access, surface,
width, placement, bridge/tunnel/layer and the complete OSM tag set.

## Measurement matching

Candidate generation uses a small in-memory grid. A candidate is rejected when:

- explicit road references conflict;
- explicit `L/R` carriageway references conflict;
- the source bearing differs by more than 85 degrees;
- it is farther than 80 metres from the road.

Remaining candidates are ranked by projected distance, directed bearing,
road-reference agreement, carriageway agreement and lane-count agreement. A
match is accepted only above a confidence threshold and with a sufficient
best-versus-second margin. Ambiguous points stay visible but do not colour a
road. Zero km/h is retained as a valid live observation.

## Frontend validation

- Click an OSM road to inspect its way/version and directed edge identity.
- Click a measurement point to inspect the binding distance and confidence.
- Enable GPS to match the device against the loaded directed edges. The POC
  uses heading, an accuracy-dependent radius and two-fix switch hysteresis.
- Grey roads have no accepted speed measurement. Coloured roads use the linked
  NDW measurement; white/amber/red points show matched/ambiguous/unmatched.

## Limitations

- Public Overpass has no availability SLA and can return 429/504. The endpoint
  tries a configured fallback and caches successful road responses.
- The binding is recomputed on each POC response and is not persisted.
- Matching operates on OSM ways, not a fully intersection-split routing graph.
- OSM has lane attributes, not authoritative painted-lane centrelines.
- A matched measurement colours the directed OSM way covered by that POC
  feature; topology-aware propagation is deliberately out of scope.
- This demonstrates source association, not production-grade legal or
  lane-level correctness.

## Configuration

```text
OSM_OVERPASS_URL
OSM_OVERPASS_FALLBACK_URL
OSM_POC_CACHE_TTL_S
OSM_POC_MAX_FEATURES
```

The map layer is named **OSM × Live Speed POC** and is enabled once when first
introduced. It can subsequently be toggled normally in the layer panel.

## Production follow-up

The phased implementation and improvement work is tracked in the
[OSM-first production backlog](11-osm-production-backlog.md). The first required
step is a reproducible correctness benchmark; production does not scale this
Overpass request path directly.
