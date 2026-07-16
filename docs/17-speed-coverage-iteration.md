# Directional speed coverage iteration

Status: phase 1 implemented on `codex/osm-speed-coverage`.
Rollback checkpoint: `6c6b8ef`.

## Product invariant

1. Every fresh NDW measurement with an accepted canonical binding colours its
   direction-specific OSM segment.
2. The backend may mark connected compatible segments as `interpolated` or
   `propagated`; the UI displays these as estimates with lower method opacity.
3. Aggregate carriageway speed and lane detail are independent. Missing or
   conflicting lane metadata never suppresses an accepted carriageway speed.
4. Lane speed is emitted only for exact verified lane numbering/count. OSM is
   road/direction authority; WEGGEG is optional derived display geometry.

## Phase 1 delivered

- removed the stricter second frontend confidence veto for versioned canonical
  states while retaining freshness/value/method validation;
- aligned direct lane-state presentation with the backend acceptance floor;
- included lane-less aggregate observations in carriageway speed;
- changed aggregation from flat lane weighting to per-site then per-segment;
- normalized shared road refs (`A5` and `A5;E19`) for propagation;
- allowed a unique compatible mainline to continue past an incompatible link;
- retained hard stops for two compatible branches, merges, opposite
  carriageways, missing identity and clipped unknown neighbours;
- excluded WEGGEG count-transition lines from full-length lane colouring;
- changed OSM lane fallback from per-segment exclusion to per-lane filling;
- made near WEGGEG ranking prefer matching lane count before distance;
- coalesced Traffic Speed Points and Lanes onto one short-lived superset request
  when both are visible.

## Measured validation

On the active regional graph on 2026-07-16:

- 10,154 persisted measurement bindings were accepted, 15,684 ambiguous and
  6,536 rejected;
- 1,516 direction-specific OSM segments had a direct fresh measurement at the
  measured database snapshot;
- an untruncated A5-area API sample contained 716 segments: 75 measured,
  49 interpolated, 25 propagated and 567 unknown;
- 13 direct and 48 derived segments in that sample had confidence below the
  former browser threshold and are now visible under backend authority.

These counts describe one live snapshot and are not national coverage claims.
The full tests completed with 59 JavaScript tests and 173 Python tests passing;
10 integration tests were skipped because their external services were absent.

## Next implementation phases

### Phase 2 — viewport-stable propagation (P1)

- persist the true source offset on its accepted OSM segment;
- precompute compact compatible speed chains at OSM import time;
- query a bounded chain/measurement halo around returned viewport segments;
- compute state over the halo but return only requested geometry;
- use road-class-specific propagation limits and a shorter derived freshness;
- add held-out-sensor replay to calibrate distance and disagreement decay.

### Phase 3 — binding coverage (P1)

- canonicalize provably identical co-located measurement systems before binding;
- improve metadata-poor sites through OpenLR-first and validated VILD fallback;
- add a coverage dashboard by road class, direction, lane-count agreement and
  rejection reason;
- never resolve an accepted directional conflict by majority vote or proximity.

### Phase 4 — lane service (P1/P2)

- prebind stable WEGGEG sections to OSM outside the request path;
- require local OSM overlap plus runner-up margin for parallel-road safety;
- produce one canonical lane assignment consumed by roads API, HUD and overlay;
- consider bounded lane propagation only after exact schema continuity tests.

### Phase 5 — source simplification (P2)

- keep local OSM and NDW measurement/reference feeds;
- keep VILD point/line/TMC while it adds validated heading/travel-time coverage;
- keep WEGGEG only as measured-benefit lane enrichment;
- move Speed Points, raw WEGGEG, VILD and NWB layers to diagnostics;
- retire unused meetlocaties shapefile and VILD area after dependency checks;
- feature-flag NWB off, validate rollback gates, then stop its heavy ingest;
- remove the public Overpass runtime after local-graph observability is complete.

No source/schema removal belongs in phases 2–4; those changes require separate
coverage and rollback gates.
