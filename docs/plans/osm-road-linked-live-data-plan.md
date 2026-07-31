# OSM road-linked live data and road-scoped HUD plan

Status: implementation-ready for the Matrix-first foundation; later stages
remain gated by their documented dry-run and acceptance evidence.

Revised after code and local PostGIS verification on 2026-07-31.

Scope: link the following existing layers to directed OpenStreetMap road
segments, in this order:

1. **Matrix Signs**, then **DRIPs / VMS**
2. **ANWB Speedcamera's**, then **Speedcamera's** from flitspalen.nl
3. **ANWB Jams**, then **ANWB Roadworks**

Primary outcome: the driving HUD asks for data on the current directed road and
ahead of the vehicle, instead of independently downloading GPS-shaped bounding
boxes and deciding relevance in the browser.

This plan deliberately rolls out one source at a time. A later source may reuse
the shared matching and road-query infrastructure, but it must not be required
to release an earlier source.

## 1. Why this change

The current frontend uses two different relevance models:

- traffic speed is already road-scoped: the browser map-matches the current OSM
  lane, resolves an NDW road/carriageway context, and queries speed sites for
  that road and carriageway;
- Matrix Signs and DRIPs are fetched from a forward-biased GPS bounding box and
  filtered in `web/lib.js` using heading, along-track distance, and cross-track
  distance;
- cameras, ANWB jams, and ANWB roadworks are map layers. Except for the existing
  trajectcontrole route/progress feature, they do not participate in the HUD;
- the current trajectcontrole route follows `osm_road`, but the HUD still finds
  an active route by testing the GPS position against route geometry.

The GPS corridor is a useful fallback, but it cannot distinguish parallel
carriageways, frontage roads, an exit beside a mainline, stacked roads, or
another road that happens to be inside the same forward rectangle. It also
requires every HUD source to repeat the same spatial fetch and browser-side
selection.

After this work the relevance chain becomes:

```text
GPS position + heading
  -> one server-validated current OSM traversal
  -> directed OSM graph ahead
  -> source records linked to those traversals
  -> road distance and relevance calculated by the API
  -> HUD renders already-scoped results
```

GPS remains necessary to establish and refresh the current position. “Rely less
on GPS” means that GPS is no longer the data-query scope for every individual
layer. It does not mean inventing a position when location is unavailable.

## 2. Current repository facts

These facts constrain the design:

- `osm_road` contains the selected major-road network:
  `motorway|trunk|primary|secondary` and their `_link` variants.
- `osm_lane_centerline` splits OSM ways into logical segments and stores
  directed, travel-ordered lane geometry.
- `osm_lane_connection` is the directed topology between those lanes.
- Logical segment IDs are
  `<osm_way_id>:<start_node_id>:<end_node_id>`. They are more precise than an
  OSM way ID when node references are available, but the builder deliberately
  emits the degenerate `<osm_way_id>:0:0` form when they are absent or do not
  align with the geometry. IDs can also change when OSM topology changes or a
  way is re-noded.
- `/api/osm/lane-lines` already returns the properties the browser uses to
  identify the current lane: lane ID, segment ID, parent road ID, direction,
  road reference, highway class, lane tags, and geometry. It does **not**
  return a carriageway reference.
- `/api/traffic/road-context` does not map-match OSM. It receives a road
  reference already selected by the browser and adds the NDW carriageway and
  hectometre anchor needed by the traffic-speed feed.
- `/api/signs/matrix` and `/api/signs/drips` require `bbox`.
- `/api/anwb` and `/api/flitspalen` require `bbox`.
- map layers must remain viewport-scoped. Road scoping is primarily for the
  driving HUD, not a replacement for browsing a map.
- the frontend is plain ordered JavaScript globals and is tested in the existing
  `web/tests` VM harness; no build step should be introduced.
- backend tests should remain DB-free where practical, with pure matcher/graph
  helpers and endpoint functions exercised through fake sessions.

### Source-specific geometry already available

| Source | Current geometry | Direction/road evidence | Refresh |
|---|---|---|---:|
| Matrix Signs | NDW MSI shapefile point | road, carriageway, lane, km, shapefile bearing | state 60 s; geometry daily |
| DRIPs / VMS | DATEX point | bearing, description; no normalized road column | 60 s |
| ANWB radars | point | road, HM, `codeDirection`, segment ID | 5 min |
| flitspalen.nl cameras | point | street, enforcement bearing, camera type; raw `drehbar` is non-discriminating | weekly |
| ANWB jams | decoded polyline, chord fallback | road, `codeDirection`, from/to, distance | 5 min |
| ANWB roadworks | decoded polyline, chord fallback | road, `codeDirection`, from/to, distance | 5 min |
| trajectcontrole | precomputed `osm_road` route | SC/SCE pairing, street | weekly |

Source geometry is evidence and an audit trail. It must never be overwritten by
matched OSM geometry.

### Verified local baseline (2026-07-31)

The implementation must remeasure these values against its deployment snapshot,
but the current database already invalidates several guessed defaults.

Distinct logical-segment lengths, measured from one representative lane per
`segment_id`:

| Class | Segments | p05 | p50 | p95 |
|---|---:|---:|---:|---:|
| all selected classes | 172,087 | 7.6 m | 49.3 m | 569.7 m |
| motorway | 13,796 | 19.1 m | 223.1 m | 1,366.7 m |
| primary | 50,272 | 7.2 m | 44.2 m | 545.1 m |
| secondary | 78,030 | 7.2 m | 40.6 m | 447.7 m |

A cache or identity keyed to one traversal would therefore churn every few
seconds while driving. Traversal and fraction are position, not stable route
identity.

Nearest stored OSM lane-centerline distances use geography distance after a
KNN candidate lookup:

| Source | Rows profiled | p50 | p90 | p99 | Beyond proposed radius | Beyond 500 m |
|---|---:|---:|---:|---:|---:|---:|
| MSI with geometry | 18,315 | 1.07 m | 1.68 m | 1.75 m | 0 beyond 20 m; max 17.59 m | 0 |
| DRIP | 870 | 1.42 m | 59.54 m | 809.63 m | 86 beyond 60 m | 14 |
| flitspalen.nl | 994 | 1.15 m | 129.57 m | 1,294.11 m | 108 beyond 40 m | 52 |

Other completeness facts:

- `msi_sign` has 18,458 rows; the same 18,315 rows have both geometry and
  bearing, while 143 have neither.
- 790/870 DRIPs have a bearing.
- all 994 active flitspalen rows have `bearing_deg`.
- all 994 also carry raw `drehbar="1"` and are parsed as `rotatable=true`.
  `rotatable` therefore has no discriminating value in this snapshot.
- only 23 current logical segments use the degenerate `:0:0` identity, but the
  fallback still has to be supported.
- `measurement_site.effective_carriageway` is `L`/`R` and is far from complete:
  27,884/42,707 A-road sites (65%) resolve a carriageway, but only 3,385/21,339
  N-road sites (16%) do, and 23,602 sites resolve no road at all. The NDW
  carriageway is therefore a useful semantic `route_key` component on motorways
  and a minority signal elsewhere; §9's graph corridor is the normal case on
  N-roads, not an edge case.

## 3. Goals

### Data and map goals

- Persist an explainable, confidence-scored relationship between a source
  record and one or more directed OSM logical segments.
- Snap point features to an OSM traversal without losing their original point.
- Render line features on ordered OSM segment geometry when the match is
  confident.
- Keep unmatched and ambiguous records available through their original
  geometry on map endpoints.
- Recompute links safely after source-location changes and OSM refreshes.
- Expose diagnostics that make a wrong road, wrong direction, or stale match
  visible during review.

### API and HUD goals

- Resolve one current-road context on the server.
- Query linked features by the current directed road graph and distance ahead.
- Return road distance and match confidence from the API; do not recompute them
  independently for each HUD channel.
- Stop using forward GPS bounding boxes for a migrated HUD channel after its
  road-scoped rollout is accepted.
- Continue supporting `bbox` for map layers, diagnostics, and rollback.
- Keep the last valid road context briefly through an ordinary GPS wobble, but
  invalidate it immediately on a confident road or direction change.

### Safety goals

- A false negative is preferable to a wrong-road HUD alert.
- Only `high` confidence links may drive the production HUD initially.
- `medium` links may be displayed in a diagnostic map mode, but not used as
  alerts until reviewed and explicitly promoted.
- A source feature with no directional evidence must not silently be assigned
  to one of two opposite carriageways.
- The absence of an OSM match must not delete or hide the source record from its
  ordinary map endpoint.

## 4. Non-goals

- Do not link every NDW feed in this plan. Travel time, situations, bridge
  openings, closures, temporary speed limits, charging, truck parking, traffic
  signs, emission zones, and VILD reference layers are out of scope.
- Do not replace the OSM base map or the existing OSM Lanes/Lane Detail layers.
- Do not replace a navigation route. Without a destination, the API can know the
  current road and deterministic continuation, but cannot know that the driver
  intends to take an optional exit.
- Do not make GPS optional for initial current-road resolution.
- Do not discard or mutate source-provided geometry.
- Do not turn source ingest into a synchronous national rematch job.
- Do not expose a full-national unscoped list endpoint.

## 5. Terms

- **OSM logical segment**: the topology-safe part of an OSM way identified by
  `OsmLaneCenterline.segment_id`.
- **Traversal**: one logical segment in one travel direction, written
  `<segment_id>@<direction>`.
- **Lane traversal**: an individual `OsmLaneCenterline.id`, with `both` expanded
  to an in-memory `fwd` or `bwd` traversal when required.
- **Current-road context**: the server-validated OSM lane/traversal, position on
  it, direction, parent OSM road data, and confidence for the vehicle.
- **Walked corridor**: a bounded, ordered set of lane/traversal states produced
  by the deterministic road-ahead policy, owned by a server-issued
  `corridor_key` and a complete per-road revision vector.
- **Source assignment**: the overall result of attempting to match one source
  feature.
- **Segment link**: one ordered OSM traversal covered or anchored by a source
  assignment.
- **Road ahead**: the deterministic, directed continuation from the current
  traversal within a bounded distance. Optional exit branches are excluded
  unless a future navigation route explicitly selects them.
- **Source geometry**: the original NDW/ANWB/flitspalen geometry.
- **Matched geometry**: a projected point or clipped/assembled OSM line created
  by the matcher.

## 6. Locked design principles

1. Match at ingest or background-refresh time; never perform full map matching
   inside a normal list request.
2. Persist source evidence, candidate score components, algorithm version, and
   failure reason.
3. Treat `(segment_id, direction)` as the shared road-level unit. Every accepted
   point link records an anchor lane as its projection geometry; restrict
   applicability to one lane only where lane identity is meaningful, notably
   MSI lane signs.
4. Group candidates by traversal before ranking them. Several lane centerlines
   from the same carriageway must not appear to be several independent roads.
5. Use metric calculations in EPSG:28992 or geography, never degree distances.
6. Use road reference and direction as hard conflict checks when trustworthy,
   not merely as small score bonuses.
7. Never infer a direction solely from which side of a bidirectional road is
   closest.
8. Keep bbox endpoints backward-compatible during every rollout.
9. Keep the road-context protocol independent of a user session. The client
   sends explicit IDs from a context response. A server-owned, short-lived
   shared corridor cache is an optimization and fencing mechanism; a cache miss
   safely rebuilds from the authoritative current lane.
10. Validate links against the current per-road topology revision and live
    segment/lane existence. There is no atomic national OSM graph generation.
11. Make output deterministic: stable ordering, explicit tie-breaking, and no
    unordered `LIMIT`.
12. Release one source in shadow mode before switching its map or HUD behavior.
13. Prove the matcher with Matrix dry-run output before committing to shared
    persistence. Freeze a reusable point-assignment schema only after DRIP has
    exercised it as the second source; design line persistence in Stage 3.

## 7. Matrix-first persistence and topology validity

Do not commit to one six-source table before any matcher has been proven.
Persistence is introduced in three deliberate steps.

### 7.1 Matrix dry-run before schema

The first Matrix matcher returns typed in-memory results and a dry-run report.
It does not require production assignment tables. This proves:

- candidate grouping and gantry consensus;
- direction and lane-number interpretation;
- diagnostics/failure-reason shape;
- the fields that actually need to be queried by the API.

Only after that report and its manual fixtures are accepted does the first
persistence migration land.

### 7.2 Shared point-assignment schema after the first proof

Matrix and DRIP are the two consumers that validate a reusable point model.
Introduce `road_point_assignment` and `road_point_link` for Matrix first, then
freeze/generalize the schema after DRIP has landed. Stage 2 reuses it for ANWB
radars and flitspalen cameras. Do not add line-only fractions, sequence
semantics, or topology-gap fields to this schema.

`road_point_assignment`:

| Column | Purpose |
|---|---|
| `source_kind` | initially `matrix`, then `drip`, `anwb_radar`, `flitspalen_camera` |
| `source_key` | canonical source PK encoded as text |
| `status` | `matched`, `ambiguous`, `unmatched`, `unsupported`, `dirty` |
| `confidence` | `high`, `medium`, `low`, or null |
| `method` | e.g. `point_road_bearing`, `gantry_consensus` |
| `failure_reason` | e.g. `no_major_road`, `road_ref_conflict`, `bearing_ambiguous` |
| `candidate_count` | traversal candidates before final rejection |
| `source_fingerprint` | hash of location/direction fields, not live display state |
| `algorithm_version` | explicit matcher version |
| `matched_at` | timestamp |
| `diagnostics` | bounded score components and runner-up summary |

Primary key: `(source_kind, source_key)`.

`road_point_link`:

| Column | Purpose |
|---|---|
| `source_kind`, `source_key` | FK to point assignment |
| `link_index` | normally zero; permits an explicitly supported second direction |
| `road_id` | soft reference to `osm_road.osm_id`, deliberately **not** an FK |
| `road_revision` | matching/routing revision used when matching this road |
| `segment_id` | OSM logical segment |
| `direction` | `fwd`, `bwd`, or explicitly reviewed `both` |
| `anchor_lane_id` | non-null soft reference to the exact lane geometry used for projection; see §7.4 |
| `applies_to_lane_id` | optional soft reference when the source is proven lane-specific; null means the directed traversal/all lanes |
| `position_fraction` | projected fraction of the travel-ordered `anchor_lane_id` geometry |
| `matched_geom` | projected POINT |
| `source_distance_m` | source-to-OSM distance |
| `bearing_error_deg` | nullable direction error |
| `road_ref_quality` | `exact`, `corridor`, `absent`, or null |
| `confidence` | link-level confidence |

Primary key: `(source_kind, source_key, link_index)`.

Indexes:

- `(segment_id, direction, source_kind)`;
- `(road_id, road_revision, direction, source_kind)`;
- GiST on `matched_geom`;
- `(source_kind, source_key)`;
- `anchor_lane_id`;
- `applies_to_lane_id` where non-null.

Foreign-key policy is explicit:

- `road_id` and `segment_id` are soft references. Plain FKs would block
  extract-scoped road pruning and arbitrary partial lane refreshes.
- `anchor_lane_id` and `applies_to_lane_id` are soft references for the same
  reason (§7.4): both rebuild paths delete and reinsert lane rows with identical
  IDs, so a cascade would erase valid links on refreshes that changed no
  matching or routing semantics. Validity comes from the live-existence check,
  and a reconciliation pass marks a still-`matched` assignment whose anchor or
  applicability lane vanished as `dirty`.
- deleting an assignment cascades its links;
- no OSM-derived delete may cascade into a source table.

The polymorphic source key cannot have one database FK to four source PK
shapes. Each source adapter must transactionally replace its rows and delete
orphans for its own `source_kind`.

Canonical keys:

- Matrix: sign UUID. A gantry is matched together but persisted per source UUID.
- DRIP: JSON encoding of `[controller_id, vms_index]`.
- ANWB radar: category-qualified `record_id`.
- flitspalen.nl: decimal camera ID.

### 7.3 Ordered line persistence is a Stage 3 decision

ANWB jams and roadworks need ordered traversals, first/last fractions, residual
statistics, and matched LINESTRING pieces. Design and migrate
`road_linear_assignment` plus `road_linear_segment` in Stage 3 after point
matching and road-ahead queries have production evidence. Reuse the confidence,
diagnostics, revision, and canonical-key conventions, but do not force lines
into the point schema.

Trajectcontrole keeps its existing route table during Stage 2. Its shared-graph
refactor may inform the Stage 3 line schema, but does not require that schema.

### 7.4 Per-road topology revision and live existence checks

There is no atomic national graph flip:

- OSM roads are upserted and pruned per Geofabrik extract
  (`ingest/osm_roads.py`);
- normal ingest deletes/reinserts lane rows per road batch
  (`osm_roads.py::_flush`), unconditionally;
- `refresh_osm_lane_lines.py` can rewrite an arbitrary road/bbox subset;
- connection rebuilding has different full/partial paths: normal ingest wipes
  the whole `osm_lane_connection` table
  (`osm_roads.py::_flush_lane_line_connections`), while the targeted refresh
  deletes only connections touching rewritten roads. A connection fingerprint
  must therefore be derived from the generated rows, never from "was this table
  written".

Use a small `osm_road_topology_state` table:

| Column | Purpose |
|---|---|
| `road_id` | OSM road ID |
| `revision` | monotonically changed when that road's matching/routing fingerprint changes |
| `topology_fingerprint` | canonical hash of all matching- and routing-relevant derived state |
| `rebuilt_at` | diagnostic timestamp |

Every lane rebuild path compares the generated fingerprint and updates affected
road revisions in the same transaction only when matching or routing semantics
actually changed. A source link stores the revision for its road.

Identity alone is insufficient: lane IDs are derived from way/node/direction/
lane number and can survive a geometry, offset, lane-count, road-tag, or
connection-property change. Build the fingerprint from a deterministic,
versioned serialization of:

- normalized OSM road fields used by matching/routing (`ref`, `highway`,
  relevant direction/lane tags, and node references);
- every derived lane's ID, segment, direction, lane attributes, persisted
  `length_m`, and canonicalized travel-ordered geometry;
- every incident connection's from/to IDs, segment/direction endpoints,
  `connection_type`, confidence, and canonicalized geometry.

Canonicalize numeric precision before hashing so harmless WKT formatting or
floating-point serialization changes do not churn revisions. A connection is
part of the fingerprint of **both** its from-road and to-road; adding, removing,
or changing one bumps both endpoint-road revisions. Store a fingerprint format
version (or include it in the digest input) so a deliberate canonicalization
change produces an explainable one-time revision update.

The normal OSM ingester currently deletes/reinserts lanes for every way batch
even when derived rows are identical. With cascading lane FKs that would
erase every lane-specific assignment on each weekly refresh. Refactor that path
to compare the generated lane fingerprint first and skip the delete/reinsert
for unchanged roads, as the targeted refresh command already does by identity
(`refresh_osm_lane_lines.py` builds `generated_ids_by_road` and rewrites only
roads whose lane-ID set differs). Connection fingerprints must still detect a
changed adjacency even when lane IDs stayed the same.

That refactor alone does not close the cascade hole. The targeted refresh still
rewrites `requested_road_ids | resolved_transition_road_ids` unconditionally,
and deleting then reinserting a row with an identical `id` still fires
`ON DELETE CASCADE`. Any operator running a targeted refresh over a corridor
would silently drop the lane-specific MSI links for exactly those roads, with
no topology change to explain it.

Resolve it by making both lane references soft, with no FK and no cascade:

- §7.4 already requires the API to check that each referenced lane still exists, so
  the cascade adds churn without adding safety;
- reconciliation marks a `matched` assignment whose anchor or applicability
  lane vanished as `dirty`, which is the same outcome the cascade was supposed
  to produce;
- both rebuild paths then stay free to delete/reinsert as they see fit.

If the cascade is kept instead, both paths — not only the batch ingester — must
skip the delete when the generated lane-ID set is unchanged, and that exemption
needs its own test.

API validity requires all of:

- the referenced road still exists;
- the referenced `(segment_id, direction)` still exists;
- the non-null `anchor_lane_id` and optional `applies_to_lane_id` still exist;
- stored and current road revisions match.

For a multi-road line assignment, one stale segment invalidates the complete
HUD assignment until rematched; do not return a silently shortened incident.

Revision changes enqueue only affected assignments for background rematch.
ANWB records are also rematched on the next five-minute ingest. During a stale
window, bbox map responses use source geometry and the road-scoped HUD omits
the assignment.

### 7.5 Source geometry provenance and forced backfill

Add explicit geometry provenance where it is currently lost:

- ANWB line rows need `geometry_source=polyline|endpoint_chord|null`.
- Matched geometry identifies point projection, clipped traversal, or topology
  bridge.
- DRIP direction interpretation is persisted rather than hidden in a score.
- Camera links record whether `bearing_deg` was enforced or absent.

Conditional GET can skip a changed parser/model when the upstream
`Last-Modified` is unchanged. Every new persisted source field therefore needs
an explicit, documented backfill step: reingest the existing local snapshot or
perform a controlled forced re-fetch, then verify row completeness. Never
assume deployment alone repopulates it.

## 8. Shared matching library

Create a new, source-neutral package, for example:

```text
src/ndwinfo/road_matching/
├── candidates.py       # PostGIS candidate retrieval and grouping
├── evidence.py         # normalization and score components
├── points.py           # point/gantry matching
├── lines.py            # ordered line-to-graph matching, added in Stage 3
├── graph.py            # traversal adjacency and bounded road-ahead walk
├── persistence.py      # assignment/link replacement
├── rematch.py          # per-source background orchestration
└── types.py            # dataclasses/enums, no ORM dependency where possible
```

Generally useful angle and directed-line mathematics should reuse
`ndwinfo.geometry.directed_lines`; source-specific parsers must not duplicate
those formulas.

### 8.1 Candidate retrieval

For point features:

1. query directed `OsmLaneCenterline` rows within a source-specific metric
   radius;
2. exclude `unknown` direction and connection geometry;
3. join parent `OsmRoad` tags;
4. project the point onto each lane;
5. calculate travel bearing at the projection;
6. group lanes by `(segment_id, traversal_direction)`;
7. retain lane-level candidates only when the source has meaningful lane data.

Use source-specific, evidence-aware search bounds rather than one permissive
radius:

- Matrix: search 20 m. The verified p99 is 1.75 m and maximum 17.59 m; expanding
  to 35 m adds competing carriageways without recovering another current row.
  Candidate ranking still has to distinguish lanes only about 1 m apart.
- DRIP: use a tight primary search for ordinary panels, then an extended search
  up to 500 m only when bearing plus a unique road/direction candidate can
  justify it. The current graph has 86/870 beyond 60 m and 14 beyond 500 m, so
  60 m cannot be a silent completeness cutoff.
- flitspalen cameras: likewise use a tight primary search plus an evidence-gated
  extension to 500 m. The current graph has 108/994 beyond 40 m and 52 beyond
  500 m.
- ANWB polyline samples: start at 35 m, measure residuals, and allow bounded
  graph bridging rather than inflating every sample radius.

All bounds are configuration values. Records beyond the extended major-road
search become `unsupported/no_major_road` and retain source geometry.

### 8.2 Evidence and conflicts

Every candidate exposes the same score components:

- metric distance;
- source-vs-traversal bearing error;
- normalized road-reference quality;
- source carriageway/direction compatibility with the candidate traversal and
  optional NDW road context; `/api/osm/lane-lines` itself has no carriageway
  reference;
- mainline vs `_link` compatibility;
- lane-count and lane-number compatibility where applicable;
- topology continuity for multi-segment matches;
- ambiguity margin over the runner-up.

Hard rejection:

- trustworthy road references conflict;
- trustworthy bearing is outside the source-specific tolerance;
- source explicitly identifies a mainline while the candidate is a link, or
  vice versa;
- the best two different traversals remain indistinguishable inside the
  ambiguity margin;
- line matching requires a non-topological jump;
- the candidate road revision changed or its segment/lane no longer exists.

Confidence policy:

- `high`: no hard conflict, all required directional evidence agrees, and the
  runner-up is clearly worse;
- `medium`: road and topology agree but one optional evidence field is missing;
- `low`: proximity-only or uncertain direction;
- only `high` enters the HUD initially.

### 8.3 Point matching

The point matcher returns:

- selected traversal and optional lane;
- projected fraction and point;
- distance and bearing error;
- confidence/method;
- runner-up diagnostics or failure reason.

It must support matching a group atomically. Matrix signs at the same gantry
must agree on one segment and direction; one lane sign cannot independently
jump to a nearby carriageway.

### 8.4 Directed line matching

This module is designed now so the persistence contract can support ordered
links, but it is implemented in Stage 3. Stage 1 and Stage 2 do not wait for the
line matcher. ANWB polylines need an ordered graph match, not a set of nearby
roads:

1. transform to RD New;
2. reject/flag endpoint-chord geometry separately from decoded polylines;
3. simplify only below a measured tolerance, then sample/densify at a bounded
   interval;
4. obtain traversal candidates for each sample;
5. use dynamic programming/Viterbi scoring with distance and bearing emissions;
6. permit transitions only through the directed OSM lane-connection graph;
7. bridge a short candidate gap only through a bounded, real graph path;
8. collapse the winning path to ordered unique traversals;
9. calculate first/last fractions and matched OSM geometry;
10. calculate coverage ratio, residual distance, topology gaps, and competing
    path margin;
11. reject loops, impossible reversals, and jumps to a parallel road.

An endpoint chord can be used to find candidates, but it cannot achieve `high`
confidence for a long or curved incident without independent road/direction
evidence and topology validation.

### 8.5 Rematch execution

Provide a resumable command:

```text
python -m ndwinfo.rematch_road_features \
  --source matrix|drip|anwb_radar|flitspalen_camera|anwb_jam|anwb_roadwork|all \
  --only-stale \
  --dry-run \
  --report path.json
```

Requirements:

- bounded batches and transactions;
- deterministic results;
- skip unchanged `source_fingerprint` when every linked road revision is still
  current and every referenced segment/lane still exists;
- report counts by status, confidence, method, road class, distance bucket, and
  failure reason;
- report the largest residuals and narrowest winner margins;
- no raw national data committed to git;
- source ingest remains successful even if background matching fails, while the
  failure is visible in feed/health metadata.

## 9. Current-road context API

Create `GET /api/road/context` as the single server-side OSM map-matching entry
point for the driving UI.

Request (`previous_*` fields are omitted on the first call):

```text
GET /api/road/context
  ?lon=4.7105
  &lat=52.5182
  &heading=12
  &previous_road_id=123
  &previous_road_revision=42
  &previous_direction=fwd
  &previous_lane_id=ll:...
  &previous_corridor_key=ctx_...
```

Response:

```json
{
  "route_key": "<semantic current-road+travel-direction label>",
  "corridor_key": "ctx_<server-owned bounded-corridor identity>",
  "position_key": "<traversal+fraction bucket>",
  "lane_id": "ll:<way>:<start>:<end>:fwd:2",
  "segment_id": "<way>:<start>:<end>",
  "traversal_id": "<segment>@fwd",
  "road_id": 123,
  "road_revision": 42,
  "corridor_road_revisions": [
    {"road_id": 123, "revision": 42},
    {"road_id": 456, "revision": 7}
  ],
  "corridor_traversal_ids": ["<segment>@fwd", "<next-segment>@bwd"],
  "corridor_reliable_ahead_m": 4200,
  "direction": "fwd",
  "position_fraction": 0.42,
  "snapped": [4.7106, 52.5183],
  "distance_m": 3.4,
  "heading_error_deg": 7.1,
  "ref": "A9",
  "name": null,
  "ndw_carriageway": "R",
  "anchor_km": 12.4,
  "highway": "motorway",
  "maxspeed_kmh": 100,
  "confidence": "high",
  "resolved_at": "..."
}
```

Rules:

- use the same directed candidate and hysteresis principles already proven by
  `selectCurrentOsmLane`, moved into shared/testable backend logic;
- hysteresis identity is `(road_id, direction)`, matching today's browser
  granularity. `previous_lane_id` is only a tie-break hint; crossing a segment
  boundary or changing lanes must not discard hysteresis;
- return no context rather than a low-confidence context;
- cap position-to-road distance and heading error;
- validate `previous_road_revision` before using any previous road/lane hint;
  reject or ignore stale/nonexistent previous IDs, revisions, and corridor
  keys rather than applying hysteresis from an obsolete graph;
- rate-limit through normal API controls, not user-specific server state;
- include enough diagnostics for a development popup but no enormous raw tags.

`route_key` is a server-derived semantic label, not a cache key or client
authority. For a numbered road it uses the normalized road plus resolved NDW
carriageway/travel direction. Without an NDW carriageway it uses the normalized
road, when available, plus the travel orientation of the deterministic walked
corridor. For an unnumbered road it may fall back to the current way-level
`(road_id,direction)` label. It is useful for diagnostics and detecting a clear
road/direction change, but it is not globally unique and is never trusted to
select graph data.

`corridor_key` is the stability and cache identity. The server creates it from
a bounded, ordered lane/traversal walk, the matcher/graph algorithm version,
and the complete sorted `(road_id, revision)` vector touched by that walk. The
key represents one travel orientation; raw `fwd`/`bwd` labels from adjacent OSM
ways are not treated as a cross-way direction identity. The server stores the
bounded corridor in a short-lived shared cache. On a later context request it
may reuse `previous_corridor_key` only when:

- the key resolves to a server-created corridor;
- the new authoritative traversal is still a member in the same travel
  orientation;
- every road revision in the corridor still matches live state; and
- the vehicle is not near the reliable end of that corridor.

Otherwise `/api/road/context` walks a new corridor from its authoritative GPS
map match and returns a new key. A client-supplied key is only an opaque
lookup/fencing hint: it cannot choose roads, traversals, revisions, or cached
results without all membership/revision checks succeeding. A context-cache miss
is safe and rebuilds from the newly resolved authoritative current lane. A
road-ahead/layer request has no independent GPS map match, so a missing,
expired, or stale corridor returns HTTP 409 and makes the client refresh
`/api/road/context`; it must not silently rebuild from arbitrary request IDs. A
traversal change updates `position_key`; it does not change `corridor_key` while
the reuse conditions remain true.

Per §2, the NDW carriageway is present for 65% of A-road sites but only 16% of
N-road sites, so the deterministic corridor path carries most non-motorway
driving and must be built and tested in Stage 1, not deferred as a rare path.
The corridor—not the OSM way-level diagnostic fallback—must survive the 40–50 m
segment lengths and adjacent way boundaries measured on primary/secondary
roads.

Because `/api/osm/lane-lines` has no carriageway reference, the new endpoint
calls the existing NDW carriageway/hectometre resolver whenever it has a usable
road reference and includes those optional fields from the first Stage 1
rollout. The old `/api/traffic/road-context` remains for compatibility until
the speed HUD is migrated separately.

The server response is authoritative for every migrated channel. The browser
consumes its `lane_id`, road, and direction; it does not independently choose a
second current lane for Matrix/DRIP/camera/incident relevance. The existing
speed HUD may continue fetching lane geometry and running its current selector
because traffic speed is out of this plan, but any disagreement is shadow
telemetry—not an override of the server context used by migrated channels.

### Refresh policy

The frontend refreshes context when any of these occurs:

- no valid context exists;
- the vehicle moved a configurable distance;
- the heading changed materially;
- the current traversal is nearly exhausted;
- the context exceeded its age limit;
- the current road revision changed, any cached corridor road revision changed,
  or the lane/traversal no longer exists.

All source queries use the returned traversal/context. A temporary poor GPS fix
may retain the previous high-confidence context for a short TTL, but distances
must be marked approximate and the context must not survive a clear reversal.

The server returns a bounded walked corridor and its revision vector with the
context or first road-ahead response. As the vehicle crosses the many short
segments in that corridor, the client re-anchors by the new server `lane_id`
without clearing road-ahead data or refetching solely because `position_key`
changed. Refetch when source versions/TTL change, any corridor road revision
changes, or the vehicle approaches the reliable end of the cached corridor.

## 10. Directed road-ahead query

Implement one shared `RoadAheadService` before migrating the first HUD source.

Inputs:

- active `traversal_id`;
- `position_fraction`;
- authoritative current `lane_id` from `/api/road/context`;
- `ahead_m`, capped by configuration;
- small `behind_m` grace;
- source kinds;
- current road revision and server-issued `corridor_key`.

Algorithm:

1. validate the current lane/traversal, road revision, and server-owned corridor
   against live topology and the corridor's complete revision vector;
2. calculate the remaining distance on the current travel-ordered lane using
   its persisted `length_m` and `position_fraction`;
3. walk directed `osm_lane_connection` topology with `lane_id` as the graph
   state and accumulated metres as path state;
4. group parallel lane states by `(segment_id, direction)` only for branch
   classification and feature lookup; do not replace their lane-specific
   lengths with an undefined traversal length;
5. keep deterministic same-road/same-carriageway continuations;
6. reject optional `_link` branches when the current context is mainline;
7. stop at the horizon, an unresolved traversal-level branch, a loop, or an uncertain
   connection;
8. join the visited `(segment_id, direction)` set to current `road_point_link`
   rows and, from Stage 3, `road_linear_segment` rows; require a visited matching
   lane when `applies_to_lane_id` is non-null;
9. calculate distance to each point anchor, line start, line end, and overlap on
   the actual visited lane path. When an all-lanes link's `anchor_lane_id`
   differs from a visited lane, project `matched_geom` onto that visited lane in
   the bounded result set rather than reusing a fraction from another offset
   geometry;
10. return stable nearest-first results with route confidence.

Persist `length_m` on every `osm_lane_centerline` row and populate it in both
full and targeted rebuild paths. `position_fraction` is always measured on the
link's `anchor_lane_id`; the vehicle fraction is always measured on the current
context `lane_id`. Parallel offset lanes are allowed to have different lengths.
If multiple reachable lane states produce materially different distances or
different traversal-level continuations, fail closed at that ambiguity rather
than choosing the shortest path. Deduplicate feature output only after distances
are resolved. Add a normalized traversal-transition table later only if the
measured query plan or latency justifies it; it must not become the owner of an
invented shared length.

### Branch policy without navigation

The API must not pretend to know a chosen exit:

- follow exact/high-confidence lane connections that remain on the current
  normalized road reference and carriageway;
- prefer the non-link continuation when the current traversal is non-link;
- do not include features exclusively on an optional exit branch;
- stop the reliable horizon when two equal main continuations cannot be
  distinguished;
- report `route_complete=false` and the distance where certainty ended.

A future navigation route can supply an explicit traversal list and remove this
limitation; that is outside this plan.

### Endpoint shape

Each existing layer endpoint gains mutually exclusive scopes:

```text
bbox=...                                      # map browsing
```

or:

```text
corridor_key=...&lane_id=...&traversal_id=...&road_revision=...&position_fraction=...&ahead_m=...
```

At least one scope is required. A road scope returns additional properties:

```json
{
  "distance_ahead_m": 840,
  "distance_to_end_m": null,
  "is_current": false,
  "matched_segment_id": "...",
  "matched_direction": "fwd",
  "match_confidence": "high",
  "route_confidence": "deterministic"
}
```

Also add a batched HUD endpoint:

```text
GET /api/road/ahead
  ?corridor_key=ctx_...
  &lane_id=ll:...
  &traversal_id=...
  &road_revision=...
  &position_fraction=...
  &ahead_m=10000
  &kinds=matrix,drip
```

It returns typed buckets and walks the graph once. Stage 1 starts with
`matrix,drip`; later stages add cameras and ANWB lines. Existing per-layer
endpoints remain useful for map rendering and diagnostics.

Road-scope dependencies belong in `api/deps.py` and must enforce:

- valid lane/traversal/direction/fraction consistency;
- maximum horizon;
- current road revision and live lane/traversal existence;
- a server-issued corridor key whose traversal membership, travel orientation,
  and complete road-revision vector are still valid;
- derivation of all graph scope from live lane/corridor data—the client key and
  semantic `route_key` never directly select roads or cached results;
- no combination of incompatible bbox and road parameters;
- deterministic errors with HTTP 400 for invalid scope and 409 for stale road
  topology/context.

## 11. Geometry selection in map APIs

Add a temporary development parameter to affected endpoints:

```text
geometry=source|matched|best
```

- `source`: current behavior;
- `matched`: return only confidently matched OSM geometry;
- `best`: matched geometry for accepted matches, source geometry otherwise.

Rollout:

1. default remains `source` during dry-run/shadow validation;
2. reviewers compare source and matched geometry in a debug layer;
3. accepted point layers switch to projected OSM anchors;
4. accepted line layers switch to ordered/clipped OSM geometry;
5. retain `geometry=source` permanently for diagnostics.

Every response includes match method/confidence/failure metadata. Do not put a
large diagnostics JSON object in normal map responses; expose full details only
through a development/detail endpoint.

## 12. Stage 1 — Matrix Signs, then DRIPs / VMS

Stage 1 replaces the existing GPS-corridor relevance logic for the two HUD
channels that already exist.

### 12.1 Stage 1A — profile and fixtures

Before implementing matching:

- rerun and record the verified local baseline from §2 against the deployment
  snapshot; do not replace it with guessed radii;
- measure Matrix geometry count, live-state join count, gantry count, duplicate
  ghost count, road/carriageway/lane/km completeness, bearing completeness, and
  nearest OSM traversal distances;
- measure DRIP point/bearing completeness, display-state completeness, and
  nearest OSM traversal distances;
- verify with real samples whether a DRIP `bearing` describes travel direction,
  panel facing direction, or requires a 180° reversal;
- verify Matrix shapefile bearing against NDW carriageway direction on several
  opposite-carriageway gantries;
- build sanitized fixtures for:
  - opposite motorway carriageways;
  - a gantry beside an exit lane;
  - a wide gantry with more/fewer NDW lanes than OSM;
  - ghost MSI UUIDs at one physical slot;
  - a roadside DRIP far from the carriageway centerline;
  - parallel mainline/frontage road;
  - missing bearing and missing road reference.

Document measured distributions in `docs/02-signs-vms.md`; do not lock bearing
semantics from field names alone.

### 12.2 Stage 1B — Matrix matching

Matrix is the vertical-slice proof. First run the matcher and fixture/report
pipeline without shared persistence. Only after its reviewed output is accepted
should `road_point_assignment`/`road_point_link` be migrated and populated for
Matrix.

Match a physical gantry as a group:

1. reuse the existing physical grouping evidence:
   normalized road, carriageway, and kilometre bucket;
2. apply ghost-sign deduplication before candidate consensus;
3. gather traversal candidates around all points in the group;
4. require road-reference compatibility;
5. use shapefile bearing and carriageway as directional evidence;
6. select one `(segment_id, direction)` for the whole gantry;
7. validate the selected OSM directional lane count against NDW lane numbers;
8. map NDW lane 1..N to travel-relative OSM lane 1..N only when count/order is
   consistent; set both `anchor_lane_id` and `applies_to_lane_id` to that lane;
9. if lane counts disagree, retain a high-confidence segment link when
   justified, persist the highest-evidence candidate as `anchor_lane_id` only
   for projection/distance basis, leave `applies_to_lane_id` null, and record
   `lane_count_mismatch` so the result is never presented as lane-specific;
10. persist one assignment per sign UUID with the shared segment result.

Fix the current lossy API ordering as part of this slice:

- `_dedupe_ghost_signs` currently runs after `.limit(limit)`, so a dense bbox can
  consume its limit with ghosts before deduplication;
- rank/dedupe in SQL (or an equivalent complete bounded subquery) before the
  final result limit;
- give the server response a stable `gantry_id` and grouped lane order;
- `groupMatrixGantries` remains only for the legacy bbox/map path during shadow
  mode, then the HUD consumes server groups. Remove it from HUD relevance after
  cutover rather than maintaining two gantry definitions.

Map rendering:

- anchor the gantry to the matched OSM traversal cross-section;
- keep the existing visual roadside offset and bearing-aware rotation;
- when lane links are valid, align each sign with its matched lane;
- fall back to the existing source point for unmatched map records.

HUD:

- server groups the linked signs back into a gantry;
- return only a gantry reachable on the current directed road;
- preserve existing “has a meaningful aspect” filtering;
- distance is graph distance to the gantry, not browser projection distance.

### 12.3 Stage 1C — DRIP matching

DRIP matching is separate because it has weaker road identity:

1. project the source point with the tight-primary/extended-evidence search from
   §8.1, explicitly classifying the 500 m tail;
2. apply the empirically verified bearing interpretation;
3. extract a road hint from description only as a scored hint, never as an
   unreviewed hard parser contract;
4. account for `roadsideMounted` distance;
5. reject opposite-direction ambiguity;
6. link at segment/direction level; DRIPs do not need lane IDs;
7. store whether bearing, description hint, or both established direction.

DRIP is the second point-source proof. Review its persistence and query needs
against the Matrix schema before declaring the point tables stable for Stage 2.

HUD eligibility additionally requires:

- working status is not a known fault state;
- text or image content is non-empty;
- source status timestamp is within the configured freshness window;
- match and road-route confidence are `high`.

### 12.4 Stage 1 API/UI migration

1. Add road scope to `/api/signs/matrix` and `/api/signs/drips`.
2. Add `matrix,drip` buckets to `/api/road/ahead`.
3. In shadow mode, fetch both:
   - old forward-bbox results;
   - new road-scoped results.
4. Log aggregate comparison only: selected source IDs, wrong-direction
   exclusions, old-only/new-only, and distance difference. Do not log precise
   user coordinates.
5. Add a developer flag to display source anchors and matched anchors together.
6. After validation, make `fetchRoadSignHud` use `/api/road/ahead`.
7. Remove Matrix/DRIP forward-bbox requests from the production HUD path.
8. Retain bbox fetching for explicitly enabled map layers.
9. Make the returned server `lane_id`/corridor identity authoritative for both
   migrated channels. Run `selectCurrentOsmLane` only for shadow comparison,
   never as a competing Matrix/DRIP scope.
10. Keep existing tiles, linger behavior, rendering, and HUD toggles unchanged in
   this stage.

### 12.5 Stage 1 acceptance

- all curated gantry/DRIP fixtures match the reviewed traversal and direction;
- no ambiguous fixture enters the HUD;
- a mainline context does not return an adjacent exit/frontage-road sign;
- a sign behind the vehicle is absent after the configured grace distance;
- Matrix gantry grouping and ghost deduplication remain stable;
- map endpoints still return unmatched source features with `geometry=best`;
- frontend tests assert that migrated HUD refreshes no longer call
  `/api/signs/*?bbox=...`;
- frontend tests assert that a browser/server lane disagreement cannot override
  the server context for Matrix/DRIP;
- the existing speed HUD may still fetch `speedBbox`/lane geometry; acceptance
  is the removal of per-source Matrix/DRIP GPS boxes, not all HUD geometry
  requests;
- source-state refreshes do not trigger a geometry rematch when the location
  fingerprint is unchanged;
- bbox endpoint compatibility tests pass;
- dry-run report and shadow comparison have been reviewed before enabling the
  road-scoped path by default.

## 13. Stage 2 — ANWB Speedcamera's, then Speedcamera's

Stage 2 introduces a camera HUD channel and moves trajectcontrole relevance to
the shared directed-road model.

### 13.1 Stage 2A — profile and fixtures

- establish `codeDirection` semantics from ANWB samples and road geometry;
- measure ANWB radar road/HM/direction/point completeness;
- remeasure flitspalen bearing/street/type completeness and confirm whether raw
  `drehbar` remains uniformly `"1"`; treat it as a source-quality fact, not
  direction evidence;
- distinguish fixed camera, red-light camera, SC, SCE, and other source types;
- verify whether `street` is normally an OSM `ref` or `name`;
- create fixtures for:
  - opposite motorway carriageways;
  - bidirectional N-road;
  - parser fixtures for raw `drehbar="0"` and `"1"`;
  - camera at an intersection;
  - camera near but not on a major road;
  - SC/SCE pair over multiple OSM ways;
  - parallel trajectcontrole carriageways;
  - dynamic and fixed reports at nearly the same location.

Update `docs/12-anwb-incidents.md` and
`docs/13-flitspalen-speedcameras.md` with measured semantics.

### 13.2 Stage 2B — ANWB radar matching

Use:

- normalized ANWB `road` as primary road evidence;
- verified `codeDirection` mapping;
- HM as diagnostics and, where comparable to an NDW road context, supporting
  evidence;
- point distance and local OSM travel bearing;
- ANWB segment ID only as source identity/diagnostics unless its external
  semantics are verified.

Reject a radar from the HUD when direction is missing on an opposing
carriageway pair and no other evidence disambiguates it. It may remain a
source-geometry map marker.

### 13.3 Stage 2C — flitspalen.nl camera matching

Use:

- normalized `street` against OSM `ref` and then `name`;
- `bearing_deg` as enforcement direction after validation;
- `camera_type`;
- optional speed-limit agreement as diagnostics, not a hard rejection because
  OSM limits may be implicit, stale, conditional, or absent.

Do **not** use `rotatable` for matching or multi-direction eligibility:

- all 994 current raw rows contain `drehbar="1"`, so the field cannot
  discriminate cameras;
- the parser currently uses `bool(item.get("drehbar"))`, which also turns the
  non-empty string `"0"` into `True`;
- fix and test boolean parsing for data correctness, then force a controlled
  reingest/backfill, but continue treating the field as non-directional;
- one camera gets a second directed link only if another independently verified
  source field or camera-type rule proves two-direction enforcement. Otherwise
  `bearing_deg` selects one direction or the match fails closed.

### 13.4 Stage 2D — trajectcontrole refactor

Keep the proven SC/SCE pairing logic, but replace the private nearest-way graph
match with shared traversal links:

1. match the SC and SCE anchors independently;
2. require compatible road/direction evidence;
3. route between their directed traversals over the shared lane graph;
4. persist the ordered traversal sequence as segment links;
5. retain `flitspalen_camera_route.geom` during migration for map/API
   compatibility;
6. compare the old route to the new route and reject large unexplained
   deviations;
7. calculate HUD progress from current traversal plus route fractions, not GPS
   distance to a polyline.

This makes an adjacent-carriageway trajectcontrole ineligible even when its line
is within the old distance threshold.

### 13.5 Stage 2 API/UI

- add road scope to `/api/anwb?category=radars` and `/api/flitspalen`;
- add `anwb_radars`, `flitspalen_cameras`, and `trajectcontrole` buckets to
  `/api/road/ahead`;
- add a separately configurable camera HUD item;
- recommended first-run behavior: camera alerts enabled only after the source
  matching validation is accepted; do not silently change the HUD default in
  the same commit as the matcher;
- display source/provider, camera type, enforced limit if known, and distance
  ahead;
- use one visual alert when ANWB and flitspalen likely describe the same
  physical camera, but retain both source identities in details;
- preserve the existing map-layer colors and source attribution;
- use linked route progress for trajectcontrole and fall back to the old route
  geometry only behind a rollout flag.

Do not deduplicate sources in persistence. Cross-provider deduplication is a
presentation decision with a reversible similarity rule.

### 13.6 Stage 2 acceptance

- opposite-direction cameras never alert in reviewed fixtures;
- raw flitspalen boolean parsing is fixed/tested/backfilled, and `rotatable`
  does not affect direction matching;
- current-mainline context excludes nearby cameras on ramps/frontage roads;
- the new trajectcontrole route and progress work across OSM way boundaries;
- SC/SCE direction conflicts fail closed;
- camera alerts are nearest-first by road distance;
- no camera more than the configured behind grace remains eligible;
- map endpoints retain every source record even when unmatched;
- provider attribution remains visible;
- old and new trajectcontrole progress are compared on recorded routes before
  rollout;
- frontend tests prove the camera HUD uses road-ahead data rather than its own
  GPS bbox.

## 14. Stage 3 — ANWB Jams, then ANWB Roadworks

This is the first multi-segment line rollout and must not begin until the point
matcher, graph walk, per-road revision invalidation, and road-scoped API have
operated successfully in production.

### 14.1 Stage 3A — profile and fixtures

- measure decoded-polyline vs endpoint-chord counts by category;
- measure geometry length, ANWB `distance_m`, vertex count, and residual to
  candidate OSM traversals;
- verify line coordinate order against `codeDirection`;
- measure road-reference completeness and same-road topology gaps;
- create fixtures for:
  - congestion on only one carriageway;
  - jam beginning behind and extending ahead;
  - jam entirely ahead;
  - roadworks on an exit beside the mainline;
  - roadworks spanning several OSM ways;
  - endpoint-chord fallback over a curved road;
  - polyline crossing another road at grade;
  - stacked/parallel roads;
  - divided road represented by close separate OSM ways;
  - an incident crossing an OSM update boundary.

### 14.2 Stage 3B — ANWB Jam matching

Run the directed line matcher with strict continuity:

- road ref and verified direction constrain candidates;
- decoded polyline supplies shape evidence;
- `distance_m` is a validation value, not geometry;
- first and last matched traversal fractions define the affected interval;
- intermediate segments must be graph-connected;
- a jam can overlap the current traversal, begin ahead, or end ahead;
- API calculates distance to jam start/end and affected distance remaining;
- an endpoint chord receives lower confidence unless the graph path and other
  evidence make the route unique.

Map rendering uses the matched OSM interval for accepted links, colored with the
existing jam style. Preserve `geometry=source` to inspect ANWB polyline quality.

HUD proposal:

- show one road-condition card/ribbon, separate from the speed sensor tile;
- distinguish “in congestion” from “congestion in 1.2 km”;
- show reported delay and affected length when present;
- merge overlapping records only in the response/presentation layer, retaining
  all source IDs.

### 14.3 Stage 3C — ANWB Roadworks matching

Reuse the line matcher but keep source-specific behavior:

- roadworks may be short, point-like, planned, or located on a ramp;
- do not assume every roadwork affects all lanes or both directions;
- an optional exit branch is not relevant to a mainline context;
- retain reason/from/to/validity fields;
- reject expired records before HUD selection;
- expose whether the vehicle is already inside the affected interval.

Map rendering follows the linked OSM interval with the existing orange style;
unmatched records retain their source geometry.

HUD proposal:

- display distance to start, affected length, reason, and validity where known;
- prioritize an active/current roadwork over a farther one;
- prevent repeated cards for overlapping ANWB records that share the same
  matched interval and reason, while retaining drill-down source records.

### 14.4 Stage 3 API/UI

- add road scope to `/api/anwb` for `jams` and `roadworks`;
- add both buckets to `/api/road/ahead`;
- allow a longer configurable road horizon than Matrix/DRIP while keeping a
  hard server cap;
- add road-condition HUD toggles separately from map layer toggles;
- cache one road-ahead response per server-owned corridor/horizon briefly,
  invalidated by source poll timestamp and the complete corridor road-revision
  vector;
- preserve bbox map fetches on pan/zoom;
- switch matched map geometry only after shadow layers are reviewed.

### 14.5 Stage 3 acceptance

- every accepted line is an ordered, continuous directed OSM traversal path;
- reviewed opposite-carriageway, exit, crossing-road, and stacked-road fixtures
  fail closed or match correctly;
- endpoint-chord records cannot enter the HUD as high confidence without the
  documented independent evidence;
- partial overlaps return correct start/end/current distances;
- expired roadworks do not alert;
- overlapping records are presentation-deduplicated deterministically;
- bbox endpoints and source geometry remain available;
- performance stays within the measured road-ahead budget at the maximum
  allowed horizon;
- frontend tests contain no per-source GPS bbox fetch for migrated HUD
  channels.

## 15. API migration sequence

The API migration is incremental:

### Phase API-0 — additive

- add per-road topology state/revision updates;
- run the Matrix matcher/report vertical slice before adding shared tables;
- add the point assignment/link tables after Matrix proof;
- add `/api/road/context`;
- add a Matrix-only internal `RoadAheadService` path, then generalize it when
  DRIP lands;
- leave all existing endpoints unchanged.

### Phase API-1 — dual scope

- make bbox optional on a migrated endpoint only when a complete road scope is
  present;
- return identical source properties plus match/road-distance metadata;
- add the source to `/api/road/ahead`;
- keep the frontend on bbox during shadow comparison.

### Phase API-2 — HUD default

- frontend resolves one current road;
- frontend calls the batched road-ahead endpoint;
- migrated HUD selectors consume server distances;
- source-specific GPS bbox calls are disabled by default but retained behind a
  temporary rollback flag.

### Phase API-3 — consolidation

- remove the temporary browser/server current-road shadow comparison for
  migrated channels after parity is demonstrated;
- delete temporary shadow fetches and flags;
- retain bbox map behavior and source-geometry diagnostics permanently.

No existing bbox contract is removed in this plan.
The speed HUD's lane-geometry fetch/selector remains a separate follow-up unless
traffic speed is explicitly brought into scope.

## 16. Frontend state changes

Replace independent source caches for HUD relevance with:

```text
roadContext = {
  routeKey,
  corridorKey,
  positionKey,
  roadRevision,
  corridorRoadRevisions,
  corridorTraversalIds,
  corridorReliableAheadM,
  laneId,
  traversalId,
  positionFraction,
  road,
  direction,
  resolvedAt,
  confidence
}

roadAheadCache = {
  corridorKey,
  corridorRoadRevisions,
  anchorLaneId,
  anchorTraversalId,
  walkedTraversalIds,
  fetchedAt,
  sourceVersion,
  routeComplete,
  matrix,
  drips,
  cameras,
  trajectcontrole,
  jams,
  roadworks
}
```

Rules:

- fence late responses by request generation, requested `corridorKey`, current
  lane/traversal, and the returned complete corridor revision vector;
- clear road-ahead data immediately on a confident road/direction change;
- a traversal/position change inside the cached walked corridor re-anchors
  distances but does not clear/refetch the cache solely because a 40–50 m
  segment boundary was crossed;
- refetch near the cached corridor end, on TTL/source-version change, or when
  the corridor key or any corridor road revision changes;
- existing channel-specific linger timers may remain, but cannot outlive their
  road context;
- distance labels use server road distance;
- browser geometry selectors remain only for shadow diagnostics, map display,
  and the explicitly out-of-scope speed flow; they cannot override the server
  route for migrated channels;
- map-layer toggles and HUD toggles remain independent.

## 17. Observability and validation

### Matching metrics

Expose per source:

- total active source records;
- source geometry present;
- matched high/medium/low;
- ambiguous/unmatched/unsupported;
- failure reason counts;
- distance and bearing-error percentiles;
- exact/corridor/absent road-reference counts;
- stale road-revision/missing-segment assignment count;
- rematch duration and last success.

### Runtime metrics/logs

- current-road resolution success and reason for no context;
- road-ahead request latency and traversals visited;
- deterministic horizon reached vs stopped at ambiguity;
- result counts by source;
- bbox fallback/shadow usage;
- source records excluded for stale state;
- no precise user coordinates in normal logs.

Add these to `/api/feeds` or a dedicated internal diagnostics endpoint without
inflating public GeoJSON responses.

### Validation reports

Each stage produces a checked-in Markdown summary with:

- input snapshot timestamps and road matching/routing revision/fingerprint
  state;
- record counts/completeness;
- match status/confidence distribution;
- distance/bearing residuals;
- manual fixture results;
- known unmatched categories;
- old-vs-new HUD shadow comparison;
- rollout decision and feature-flag state.

Do not check in raw national feed data or precise user traces.

## 18. Testing strategy

### Pure backend unit tests

- road-reference normalization and conflict handling;
- angle/direction normalization, including `oneway=-1` and `both`;
- candidate grouping by traversal;
- ambiguity margin and deterministic tie-breaking;
- gantry consensus and lane-count mismatch;
- point projection/fraction;
- line sampling, topology continuity, clipping, and gap rejection;
- directed lane-state road-ahead distance, including unequal parallel-lane
  lengths and an all-lanes anchor projected onto the visited lane;
- branch/mainline policy;
- stable corridor identity across short segment and adjacent OSM-way
  boundaries, including adjacent ways whose raw `fwd`/`bwd` labels differ;
- way/direction hysteresis with lane ID used only as a hint;
- per-road revision and live-existence invalidation;
- source fingerprint stability;
- canonical composite source keys.

### Parser/ingester tests

- ANWB geometry provenance;
- flitspalen string-boolean parsing for `"0"` and `"1"`;
- source-specific direction fields survive parsing;
- live MSI/DRIP state changes do not change location fingerprints;
- transactional replacement and orphan cleanup;
- failed matching does not roll back valid source ingest;
- unchanged matching/routing fingerprint skips lane deletion and retains
  lane-specific links;
- geometry-only, relevant-tag-only, lane-attribute-only, and
  connection-property-only changes bump the affected road revision;
- a cross-road connection change bumps both endpoint-road revisions;
- a targeted refresh that rewrites a road whose lane IDs are unchanged keeps its
  lane-specific links — the regression the removed cascade would have caused;
- a partial road/lane refresh dirties only assignments linked to affected roads.

### API tests

- bbox remains required unless road scope is complete;
- invalid/stale lane or traversal returns a deterministic error;
- horizon caps;
- direction and behind filtering;
- stable ordering;
- source vs matched vs best geometry;
- road-ahead typed buckets;
- `corridor_key` remains stable while `position_key` changes across traversals
  inside the returned corridor;
- a missing or stale `previous_road_revision` prevents previous-lane hysteresis;
- a forged or wrong-orientation corridor key cannot select graph data;
- an expired/missing/revision-stale key makes road-ahead return 409, while a
  context request rebuilds a new corridor after authoritative map matching;
- changing the revision of a downstream road invalidates a cached corridor even
  when the current road revision is unchanged;
- late/stale context fencing fields;
- unmatched source records remain in bbox responses.

### Frontend tests

- one context request feeds every migrated HUD channel;
- no migrated HUD channel calls its old bbox endpoint;
- a disagreeing browser lane candidate cannot replace the server lane for a
  migrated channel;
- context change clears old-road data;
- same-road refresh retains tiles without flicker;
- stale response cannot overwrite a new context;
- server distance is rendered unchanged;
- road-ahead source buckets respect HUD toggles;
- map layer still uses bbox independently;
- camera and road-condition presentation deduplication;
- trajectcontrole progress follows linked route data.

### Replay and manual QA

Use sanitized synthetic routes plus a non-committed local replay:

- straight motorway;
- curve;
- interchange entry/exit;
- parallel carriageways;
- frontage road;
- stacked crossing;
- N-road changing OSM way IDs;
- partial OSM road/lane revision change mid-session.

For each rollout, drive/replay the same route with old and new selectors in
shadow mode and inspect differences before switching defaults.

## 19. Performance plan

- Candidate search uses the existing geography/GiST lane index.
- Assignment links are precomputed, not matched in list requests.
- Road-ahead graph walks are horizon-bounded and revision-validated.
- Persist lane `length_m` to avoid repeated geographic length calculation.
- Batch all HUD source kinds into one graph walk.
- Cache a short-lived road-ahead result by
  `(corridor_key, horizon, source versions, complete corridor revision vector)`;
  do not key cache lifetime to each short traversal.
- Keep result caps per source and return truncation metadata.
- Use `EXPLAIN (ANALYZE, BUFFERS)` locally for worst-case horizons before each
  stage.
- If lane-level recursive traversal is too expensive, materialize a deduplicated
  `(segment_id,direction) -> (segment_id,direction)` transition table. Do not
  add it speculatively before measuring the simpler implementation.

Initial performance gates should be set from the current deployment rather than
invented in advance. Record p50/p95 and visited-traversal counts in each stage
report, then lock a regression threshold.

## 20. Rollout and rollback

Use separate flags:

- `ROAD_MATCH_<SOURCE>_ENABLED`: compute/persist links;
- `ROAD_API_<SOURCE>_ENABLED`: expose road-scoped results;
- `HUD_ROAD_SCOPE_<SOURCE>_ENABLED`: consume them in the HUD;
- `MAP_MATCHED_GEOMETRY_<SOURCE>_ENABLED`: draw matched geometry by default.

Rollout order for every source:

1. migration;
2. dry-run report;
3. persist links;
4. expose diagnostics;
5. shadow API;
6. shadow HUD/map comparison;
7. road-scoped HUD;
8. matched map geometry;
9. remove temporary shadow code after an observation period.

Rollback:

- disable HUD road scope to restore the previous selector;
- disable matched map geometry to restore source geometry;
- leave source ingest and bbox APIs running;
- disabling a feature flag does not clear derived tables, and no OSM-side delete
  cascades into them; links whose lane or segment vanished are marked dirty by
  reconciliation and restored by rematching;
- never roll back by deleting source rows or overwriting source geometry.

### Risk register

| Risk | Consequence | Mitigation |
|---|---|---|
| OSM contains only the selected major-road classes | local-road cameras or panels cannot match | mark `unsupported/no_major_road`, keep source geometry, never force a HUD link |
| OSM re-noding changes logical segment IDs | previously valid links become stale | per-road revision, existence checks, targeted rematch, source-geometry fallback |
| OSM geometry/tags/connection semantics change while IDs stay stable | stale fractions, directions, or routing survive an identity-only check | canonical matching/routing fingerprint includes geometry and relevant derived fields; bump both roads for cross-road connections |
| a source bearing means panel facing rather than traffic travel | opposite carriageway assignment | profile each field against real data; version and persist the interpretation |
| GPS jumps between parallel lanes/roads | wrong context and all downstream alerts change | backend confidence gate, `(road_id, direction)` hysteresis with lane as a hint only, immediate conflict invalidation |
| no navigation route exists at a branch | exit-only data may be included or valid data omitted | deterministic-mainline policy, stop at ambiguity, report incomplete horizon |
| a straight ANWB chord crosses unrelated roads | convincing but false line match | provenance flag, lower confidence ceiling, strict graph and road-ref checks |
| source and partial OSM refresh overlap | stale links on only part of the network | transactional per-road revision, live existence checks, targeted dirty queue |
| road-ahead graph query expands too widely | latency or truncation | horizon cap, same-road policy, length persistence, benchmark before materializing transitions |
| matching failure blocks live ingest | stale live source state | decouple source upsert from background rematch and expose matcher health separately |
| shadow comparison logs driving traces | privacy leak | aggregate IDs/outcomes only; do not log precise coordinates or full traces |

## 21. Documentation updates required during implementation

- `docs/02-signs-vms.md`: measured direction semantics, link model, road-scoped
  API, match coverage.
- `docs/12-anwb-incidents.md`: `codeDirection`, geometry provenance, line
  matching, road-scoped API.
- `docs/13-flitspalen-speedcameras.md`: point assignments, raw `drehbar`
  limitation/parser fix, shared trajectcontrole graph.
- `docs/11-osm-pbf.md`: per-road matching/routing revision and canonical
  fingerprint, persisted lane length, downstream link invalidation.
- `CLAUDE.md`: new tables, matcher modules, endpoints, and current-road flow.
- migrations and configuration reference: new flags, radii, freshness windows,
  horizon caps.

## 22. Implementation checklist

### Matrix-first foundation

- [ ] Reproduce and save the baseline queries for current lane topology and
  point-source distances.
- [ ] Implement pure point candidates/evidence/confidence for the Matrix slice.
- [ ] Run the Matrix matcher/fixtures/report without shared persistence.
- [ ] Add per-road matching/routing revision and canonical fingerprint state to
  every full and partial lane rebuild path, including geometry, relevant road
  tags/lane attributes, lengths, and incident connection semantics.
- [ ] Make regular OSM ingest skip lane delete/reinsert when the derived
  fingerprint is unchanged, reusing the targeted refresh's identity comparison.
- [ ] Keep `anchor_lane_id` and `applies_to_lane_id` as soft references (no FK
  cascade) and cover link survival across a no-op targeted refresh with a test.
- [ ] Implement the server-owned corridor cache/key, complete road-revision
  vector, adjacent-way travel orientation, and safe rebuild behavior; treat
  `route_key` only as a semantic diagnostic label. NDW carriageway evidence
  covers 65% of A-road sites and 16% of N-road sites, so exercise the graph
  corridor path as the normal N-road case.
- [ ] Add `length_m` to lane centerlines and populate it in every rebuild path.
- [ ] Add point assignment/link models/migration/indexes after Matrix proof.
- [ ] Add source fingerprint helpers and canonical keys.
- [ ] Implement point persistence and targeted dirty/rematch reconciliation.
- [ ] Implement the bounded lane-state road-ahead graph walk and defer
  traversal-level deduplication until feature lookup/output.
- [ ] Implement `/api/road/context`.
- [ ] Implement dual-scope API dependency.
- [ ] Implement `/api/road/ahead`.
- [ ] Add rematch command, reports, and metrics.

### Stage 1

- [ ] Matrix/DRIP profiling and fixtures.
- [ ] Matrix gantry consensus matcher.
- [ ] Matrix lane-link validation.
- [ ] Move Matrix ghost dedupe before API limit and define one server gantry ID.
- [ ] DRIP bearing interpretation and matcher.
- [ ] Freeze/generalize the point schema only after DRIP has exercised it.
- [ ] Signs API road scope.
- [ ] Source-vs-matched debug geometry.
- [ ] HUD shadow comparison.
- [ ] Matrix HUD cutover.
- [ ] DRIP HUD cutover.
- [ ] Stage 1 validation report and documentation.

### Stage 2

- [ ] ANWB direction profiling.
- [ ] flitspalen bearing/type/raw-`drehbar` profiling and boolean parser fix.
- [ ] Force flitspalen reingest/backfill and verify parsed-field completeness.
- [ ] ANWB radar matcher.
- [ ] flitspalen camera matcher.
- [ ] shared-graph trajectcontrole rebuild.
- [ ] camera road-ahead API bucket.
- [ ] camera HUD and source-aware presentation dedupe.
- [ ] linked trajectcontrole progress cutover.
- [ ] Stage 2 validation report and documentation.

### Stage 3

- [ ] ANWB geometry provenance.
- [ ] Force ANWB reingest/backfill and verify geometry provenance completeness.
- [ ] line-data profiling and fixtures.
- [ ] Implement the ordered directed-line matcher.
- [ ] jam directed-line matching.
- [ ] roadwork directed-line matching.
- [ ] matched/clipped line GeoJSON.
- [ ] jam/roadwork road-ahead API buckets.
- [ ] road-condition HUD.
- [ ] matched map geometry cutover.
- [ ] Stage 3 validation report and documentation.

### Final consolidation

- [ ] Retire the old speed-only context endpoint only in a separately approved
  traffic-speed migration.
- [ ] Remove browser/server shadow current-road comparison for migrated channels
  after parity validation; leave the out-of-scope speed flow explicit.
- [ ] Remove temporary source-specific HUD bbox fallbacks.
- [ ] Retain bbox map APIs and source geometry diagnostics.
- [ ] Lock performance regression thresholds from measured production behavior.

## 23. Definition of done

The implementation described by this plan is complete when:

- all six requested source kinds have explainable, revision-validated OSM
  links using the proven point schema or the later line schema;
- Matrix and DRIP HUD tiles are selected by directed road distance;
- camera alerts and trajectcontrole progress use the current directed road;
- ANWB jams and roadworks render on accepted OSM intervals and alert only on
  the relevant current-road continuation;
- migrated Matrix/DRIP/camera/jam/roadwork HUD channels make no independent
  source-data forward-bbox requests; the out-of-scope speed HUD may still fetch
  lane geometry;
- GPS is used to resolve/refresh one current-road context, not as every layer's
  query scope;
- ambiguous or stale links cannot enter the HUD;
- unmatched source data remains available on the map;
- bbox APIs remain backward-compatible;
- each stage has tests, a validation report, metrics, documented limitations,
  and an independent rollback switch.
