# OSM “Lanes” layer plan

## Goal

Add a new map layer named **Lanes** that draws one thin blue centerline per
driving lane, derived directly from the existing OSM **Driving Roads** data.

The first deliverable is deliberately small and verifiable:

- one lane produces one line on the OSM way centerline;
- two or more lanes produce parallel lines whose center-to-center distance is
  exactly **3.5 metres**;
- every line is rendered as `#111171`;
- the feature is a line only, not a lane-width road surface;
- the existing **Lane Detail** layer stays separate and may remain available for
  visual comparison during development.

The second deliverable connects those lane lines across OSM way boundaries,
ramps, and one-way/two-way transitions. Highway entries and exits attach to the
rightmost travel lane unless trustworthy OSM lane tags or a manual override say
otherwise.

## Non-negotiable data boundary

The new layer must have this lineage:

```text
Geofabrik OSM PBF
  -> osm_road (“Driving Roads”)
  -> new lane-centerline builder
  -> logical OSM road segments
  -> osm_lane_centerline + osm_lane_connection
  -> GET /api/osm/lane-lines
  -> “Lanes” map layer
```

It must **not**:

- read `osm_road_lane`;
- call `/api/osm/lanes`;
- import cross-section, direction, merge, or lane-geometry logic from
  `parsers/osm_lanes.py`;
- import lane-allocation or connector-surface logic from
  `parsers/osm_junctions.py`;
- copy old lane or connector rows as a bootstrap;
- derive new geometry in the browser from the already-derived Lane Detail
  response.

It is acceptable to inspect the old implementation and tests to learn which
cases exist. The new implementation must independently derive its geometry from
`OsmRoad.geom` and OSM tags in `OsmRoad.raw`.

This boundary should be enforced structurally: put the new implementation in
new modules and have its API query only `OsmLaneCenterline`,
`OsmLaneConnection`, and, when parent tags are needed, `OsmRoad`.

### Shared mathematics without coupling to Lane Detail

The constraint is on Lane Detail **data and its lane model**, not on plain
mathematics. However, the new implementation must not import private symbols
from `parsers/osm_junctions.py`: that would couple this layer to a large module
whose behavior is allowed to change with Lane Detail.

Move generally useful, lane-model-neutral operations into a small public module,
for example `src/ndwinfo/geometry/directed_lines.py`:

- bearing, unit-vector, and normalized-angle helpers;
- bounded cubic Bézier construction;
- explicitly named junction radius and angle-tolerance constants;
- the turn-token-to-angle lookup.

Pin these helpers with focused unit tests. The old module may later import the
public helpers too, but migrating Lane Detail is not required for this work.
No cross-section, lane allocation, merge, trimming, surface, or old connector
logic moves into the shared module.

## Current repository facts

- `osm_road` is the source behind **Driving Roads** and stores each selected OSM
  way as a WGS84 `LINESTRING`, plus its complete OSM tag dictionary.
- The selected classes are `motorway`, `trunk`, `primary`, and `secondary`,
  including their `_link` variants.
- `osm_road_lane` is the source behind **Lane Detail** and is therefore excluded
  from this design.
- The existing lane implementation varies lane width by road class and draws
  filled lane-width bands, markings, tapers, and connector surfaces. None of
  that rendering belongs in the first Lanes milestone.
- Accurate metric offsets cannot be made in EPSG:4326. Geometry must be
  transformed to RD New / EPSG:28992, offset in metres, and transformed back to
  WGS84.

### Measured tag distribution (national `osm_road`, checked 2026-07-29)

Every rule below is sized against these counts rather than against guessed
frequencies.

| Fact | Count |
|---|---:|
| driving-road ways | 167,192 |
| ways with an explicit `lanes` tag | 142,495 |
| `oneway=yes/true/1` | 115,832 |
| no `oneway` tag at all | 49,414 |
| `oneway=no` | 1,860 |
| `oneway=reversible` / `alternating` | 71 / 14 |
| `oneway=-1` | **1** |
| `junction=roundabout` | 19,442 |
| …of which carry **no** `oneway` tag | **17,388** |
| two-way (non-roundabout), `lanes=1` | 664 |
| two-way, odd `lanes`, no directional tags, non-roundabout | 716 |
| two-way with no `lanes` tag | 15,519 |
| ways with `lanes >= 5` | 1,623 |
| highest `lanes` value in NL | 7 |
| non-integer / malformed `lanes` values | 0 |

Three consequences drive the design:

1. **`junction=roundabout` implies one-way** and 17,388 roundabout ways rely on
   that implication instead of tagging `oneway`. Without normalizing it, a
   roundabout ring is classified two-way: 13,700 of them (`lanes=1`) would
   become direction-unknown and therefore unconnectable, and 3,286 (no `lanes`)
   would be drawn as two parallel rings 3.5 m apart. This is the single largest
   correctness item in Milestone 1.
2. Once roundabouts are normalized, genuinely ambiguous two-way cases collapse
   to a 716-way tail. Rules for them should be simple and safe, not elaborate.
3. `oneway=-1` (1 way), reversible/alternating (85 ways), over-ceiling counts
   and malformed values (0 ways) are correctness guards, not common paths. They
   get a rule and a test each — no more.

### Verified library behaviour

Checked in the app container (shapely 2.1.2, GEOS 3.13.1):

- `LineString.offset_curve()` **preserves** coordinate direction for negative
  (right-side) offsets. Older GEOS reversed them, so this must be pinned by a
  test rather than assumed.
- A negative/inner offset on a tight bend can silently degenerate while staying
  a valid `LineString`: on an 8 m-radius arc a 5.25 m inner offset returned a
  24.8 m source as a 9.0 m line whose endpoints had moved ~5 m inward. Emptiness
  and multipart checks do not catch this; see Milestone 1 §3 step 5.

## Terms and identifiers

- **OSM way**: one `osm_road` row, identified by `osm_id`. An OSM way may pass
  through several junction nodes or form a closed ring, so it is not necessarily
  one connectable segment.
- **Logical segment**: one directed-topology-safe piece of an OSM way between
  consecutive topology nodes. `segment_id` is
  `<osm_id>:<start_node_id>:<end_node_id>`. Before the topology backfill every
  way is one unsplit segment keyed `<osm_id>:0:0`; there is no intermediate
  coordinate-derived node identity.
- **Lane line**: a thin line following the center of one physical driving lane
  on a segment.
- **Connector**: a separate short line from the travel end of one lane line to
  the travel start of another.
- **Direction**:
  - `fwd`: travels from the first coordinate of the logical segment to the last;
  - `bwd`: travels from the last coordinate to the first;
  - `unknown`: the physical line can be drawn, but its travel direction cannot
    be established safely from the available OSM tags.
- **Shared lane**: one physical lane carrying traffic in both directions (a
  single-track two-way road). It is stored and returned **once**, with
  `direction=both`. The connection builder creates two in-memory traversal
  records (`fwd` and `bwd`) over that one centerline. It is not the same as
  `unknown`.
- **Lane number**: numbered from the driver’s left to the driver’s right within
  one direction. Lane `1` is leftmost and lane `lane_count` is rightmost. This
  definition remains true for `bwd` and `oneway=-1`; it is never based merely on
  the stored coordinate order.
- **Stable lane ID**:
  `ll:<osm_id>:<start_node_id>:<end_node_id>:<direction>:<lane_nr>`. A shared
  lane uses `direction=both`.

  The `ll:` prefix is required so IDs are visibly and programmatically separate
  from `OsmRoadLane.id` (`f"{source_id}:{direction}:{lane}"`). The old `lane`
  means physical left-to-right position in the way's coordinate frame, while
  `lane_nr` here is travel-relative and belongs to a logical segment. The layer
  popup must also label which layer an ID came from.

  A directed traversal of a lane is written `<lane_id>@<travel_direction>`.
  Normal lanes have only their stored direction; a shared lane has both
  `@fwd` and `@bwd`. For an unambiguous one-way logical segment, developer input
  in the shorter `segment_id:lane_nr` form can be normalized to the full lane
  and traversal ID.

## Milestone 1: independent lane centerlines

### 0. Preserve Driving Roads topology (data model here, built in Milestone 2)

**Sequencing.** This step belongs to Milestone 1's data model because logical
segment IDs appear in every lane primary key, but it is **not** a prerequisite
for drawing the first blue lines, and it must not gate the Milestone 1 visual
review. Measured against the national dataset:

| Fact | Count |
|---|---:|
| interior vertices across all driving ways | 626,461 |
| …that coincide with another retained way's endpoint (real split points) | **4,896** |
| resulting growth in segment count over 167,192 ways | **+2.9%** |
| closed roundabout ways (ring not already split by OSM) | **242** of 19,442 |
| non-simple (self-touching) ways | **0** |

So topology splitting fixes roughly 4,900 junctions and 242 rings. That is worth
doing and worth doing correctly — a road joining mid-way is otherwise connected
at the wrong end — but it is a 3% structural correction, not the main event.
Schedule it as the first work item of Milestone 2 (see Implementation order),
after lane geometry has been reviewed on screen. Until then `segment_id` is
`<osm_id>:0:0` for every way, meaning "unsplit", and the transition to real node
IDs is a one-time truncate-and-rebuild of both new tables. Do not build a second,
coordinate-derived identity scheme to bridge the gap: it would double the
identity rules and every ID would churn at backfill anyway.

The current `osm_road` row stores the way geometry but discards the ordered OSM
node references after building the WKT. Endpoint coordinates are enough to draw
Milestone 1, but they are not enough for Milestone 2:

- another way can join at an internal vertex of a long OSM way;
- a closed roundabout's first and last coordinate are the same while approaches
  join at internal ring nodes;
- `start_node_id`/`end_node_id` alone would still miss both cases.

Extend the Driving Roads ingest before connection work:

1. Persist every retained way's complete ordered node-ID list in a new
   `osm_road.node_refs bigint[]` column. The parser already holds this tuple
   while constructing the WKT (`parsers/osm_pbf.py`); retain it instead of
   discarding it. Sizing is not a concern: the national total is 960,845
   vertices across 167,192 ways, averaging 5.7 per way — roughly 7.7 MB of
   bigints. Add a GIN index only if a measured query needs it; at this size a
   batched `unnest` scan is likely enough.
2. Keep the full intrinsic list rather than persisting a compact “shared nodes
   in this extract” result. A compact result can differ across overlapping
   Geofabrik extracts and be overwritten when the same `osm_id` is ingested
   from another extract; the OSM way's ordered node list does not have that
   problem.
3. When building lanes, derive **topology nodes** from the current Driving Roads
   set: the first and last node, every node also referenced by another retained
   driving way, and every node repeated within the way (including ring closure).
   Keep each topology node's OSM ID and source vertex index.
4. During a one-time backfill, reingest the PBF so every Driving Roads row has
   `node_refs`. This is still source OSM Driving Roads data; no Lane Detail
   table participates.
5. Until that backfill lands, every way is one unsplit logical segment with
   `segment_id = <osm_id>:0:0`. There is no interim coordinate-derived node
   identity: the pre-topology phase draws correct geometry with simple IDs, and
   switching to real node IDs is a truncate-and-rebuild, not a migration. Manual
   overrides are only accepted after the backfill (see Manual connection
   overrides).
6. Split each OSM way into logical segments between consecutive topology nodes
   before offsetting. A closed ring with attached approaches is therefore split
   at every attached node. A closed ring with no attached retained driving road
   remains one closed drawable segment and produces no external connectors. For
   a split ring, explicitly create the wrap-around segment from the last
   attachment node through the coordinate-array boundary to the first; do not
   drop that arc.

Use the OSM node IDs in logical segment IDs so adding another junction elsewhere
on the same way does not renumber existing segments. A way direction change or
node edit can still invalidate an override; validation must report that
explicitly. For national rebuilds, derive shared-node membership with a batched
SQL `unnest(node_refs)` query or a bounded two-pass stream.

**Assert segment-ID uniqueness rather than engineering around it.**
`<osm_id>:<start_node_id>:<end_node_id>` is ambiguous only if one way traverses
the same ordered node pair twice, which requires a self-touching way — and there
are currently **zero** non-simple driving ways nationally. So do not add an
ordinal or vertex index to the ID. Instead, when segmenting a way, fail loudly
if two of its segments produce the same key. Without that check the duplicate
would be silently swallowed by the upsert and one arc of the road would simply
disappear.

### 1. Add a new persisted model

Add `OsmLaneCenterline` in `src/ndwinfo/models.py` and a new Alembic migration.
Use a new table, not extra rows or flags in `osm_road_lane`.

Proposed columns:

| Column | Purpose |
|---|---|
| `id` text PK | Stable `ll:<segment_id>:<direction>:<lane_nr>` identity |
| `road_id` bigint FK | Parent `osm_road.osm_id`, `ON DELETE CASCADE` |
| `segment_id` text | Stable `<osm_id>:<start_node_id>:<end_node_id>` logical segment; `<osm_id>:0:0` before topology backfill |
| `lane_nr` integer | Travel-relative lane number, starting at 1 |
| `lane_count` integer | Number of lanes in this direction, or total physical count when direction is unknown |
| `physical_lane_index` integer | Left-to-right position across the complete road cross-section |
| `direction` text | `fwd`, `bwd`, `both`, or `unknown` |
| `offset_m` numeric | Signed offset from the source road centerline |
| `count_source` text | `directional_tags`, `lanes`, `turn_lanes`, `assumed`, `conflict`, or `both_ways` |
| `oneway_source` text | `tag` or `roundabout_implied` — records why a way was treated as one-way |
| `geom` geometry(LineString, 4326) | Thin lane centerline |
| `raw` jsonb | Small diagnostics only; do not duplicate every parent OSM tag |
| `ingested_at` timestamptz | Existing upsert convention |

`count_source=conflict` covers lane-count rule 1's disagreeing directional tags;
`both_ways` covers a `lanes:both_ways` centre block. Neither is silently folded
into `lanes`.

There is deliberately **no** `shared` column. "Shared" is exactly
`direction='both'`, and storing the same fact twice invites the two to drift —
at which point one single-track road renders as two overlapping features. Any
code that wants the predicate compares `direction`.

Indexes:

- GiST on `geom` for viewport queries;
- btree on `road_id` for refresh/delete and parent lookup;
- unique/btree on `(segment_id, direction, lane_nr)`.

The foreign key makes stale lane lines disappear when an `osm_road` is pruned.
Per-road rebuilds must still delete before inserting because its topology or
lane count can shrink.

### 2. Implement a new pure lane planner

Create `src/ndwinfo/parsers/osm_lane_lines.py`. It may import only the new
lane-model-neutral public geometry module described above, never either old lane
module.

The planner should return a normalized cross-section before it creates any
geometry. Keeping tag interpretation separate from geometry makes the
direction and numbering rules directly unit-testable.

**Step 0 — normalize one-wayness before anything else.** A way is one-way when
`oneway` says so or when `junction=roundabout` and no `oneway=no` contradicts
it. OSM treats `junction=roundabout` as implying `oneway=yes`, and 17,388 of
this dataset's 19,442 roundabout ways depend on that implication. Record
`oneway_source=roundabout_implied` when the implication was used, so the effect
is countable and reviewable rather than invisible. An explicit `oneway=no` on a
roundabout (10 ways) is honoured as tagged and reported as a data anomaly.

Do **not** apply the implication to `junction=circular`. Circular junctions are
expected to carry an explicit `oneway` tag and can legally be bidirectional.
All 429 current national `junction=circular` ways explicitly say
`oneway=yes`, so following the tag loses no present coverage and remains correct
if a bidirectional circular junction appears later.

Lane-count priority:

1. On a two-way road, valid `lanes:forward`, `lanes:backward`, and
   `lanes:both_ways` values define the directional blocks. A missing
   `lanes:both_ways` means zero; a single missing forward/backward value may be
   derived only when a valid `lanes` total leaves exactly one non-negative
   remainder. For example, `lanes=3 + lanes:forward=2` resolves to two forward
   and one backward lane. A set whose component sum disagrees with `lanes`
   draws the `lanes` total as direction-unknown lines and records the conflict;
   it does not choose whichever tag happens to be parsed first.
2. Otherwise, a positive integer `lanes` value is the physical lane count.
3. For a one-way road with no `lanes`, a correctly formed `turn:lanes` value
   supplies a non-guessed count because it has one field per lane.
4. An untagged one-way road defaults to one lane and is marked
   `count_source=assumed`.
5. An untagged two-way road defaults to one lane per direction, two physical
   lanes total, and is marked `count_source=assumed`.
6. Invalid values (`0`, negative, non-integer, implausibly large) are not
   coerced. Apply the same one-way/two-way fallback and include the rejected
   value in diagnostics. There are currently zero malformed values in the
   national data, so this is a guard, not a code path to optimize.
7. `oneway=reversible` and `oneway=alternating` (85 ways) may be drawn as
   direction-unknown physical lines, but they must not participate in automatic
   connections.

Use a documented safety ceiling, initially 12 lanes. The highest real value in
NL is 7, so the ceiling exists only to stop a malformed tag allocating unbounded
geometry. Values above it are skipped and reported.

Direction rules:

- `oneway=yes|true|1`, or one-wayness implied by `junction=roundabout`: all
  lanes are `fwd`;
- `oneway=-1` (1 way nationally): all lanes are `bwd`;
- an explicit two-way directional split is used as tagged;
- a two-way road with an even total and no directional split is divided equally
  for Dutch right-hand traffic;
- **a two-way road whose total is 1 is a single-track two-way road**: one
  centerline row at offset `0`, stored as `direction=both` with
  `lane_count=1`. It is *not* `unknown`. The connector builder creates separate
  `fwd` and `bwd` traversal records over that row, so it stays connectable
  without returning two identical map features. After roundabout normalization
  this case still covers 664 ways, most of them primary/secondary through-roads;
- a two-way road with an odd total of 3 or more and no directional split is
  drawn, but its lines are `unknown`. Do not guess which direction owns the
  extra lane. This is a 52-way tail;
- `lanes:both_ways` produces a direction-unknown center block and never receives
  automatic entry/exit connectors.

**Consistency check between rules 2 and 5.** `lanes=1` on a two-way road and an
untagged two-way road describe roughly the same kind of road (both buckets are
dominated by primary/secondary), so they must not disagree about how many lines
appear. With the shared-lane rule above, `lanes=1` draws one line carrying both
directions and the untagged default draws two lines at ±1.75 m. That difference
is now a deliberate, tag-driven statement ("OSM says one physical lane" versus
"OSM says nothing, assume the common two-lane case") rather than an accident of
parity — but it will still be visible where tagging changes mid-route. Add a
counter for adjacent same-`ref` segments that switch between the two, and treat
a high count as a reason to revisit rule 5, not as a geometry bug.

### 3. Generate geometry at a fixed 3.5-metre pitch

Use one constant:

```python
LANE_SPACING_M = 3.5
```

Do not key it by highway class.

Lay out the complete physical cross-section against the OSM way's stored
start-to-end coordinate frame first. For `N` physical lanes ordered
left-to-right in that source frame, the centerline offset for zero-based
physical index `i` is:

```text
offset_m = ((N - 1) / 2 - i) * 3.5
```

Examples:

| Lanes | Offsets from road centerline |
|---:|---|
| 1 | `0.0` |
| 2 | `+1.75`, `-1.75` |
| 3 | `+3.5`, `0.0`, `-3.5` |
| 4 | `+5.25`, `+1.75`, `-1.75`, `-5.25` |

Positive is left of the stored start-to-end line and negative is right. Under
Dutch right-hand traffic, a two-way road's backward block occupies the positive
side and its forward block the negative side. Within each block, assign
travel-relative lane numbers separately:

- forward lane 1 is the forward block's leftmost/centre-side lane;
- backward lane 1 is also the backward driver's leftmost/centre-side lane,
  which is the reverse of the physical source-frame order;
- on `oneway=-1`, lane 1 is on the negative/source-right side because that is
  the driver's left while travelling against the stored line.

This separates two independent questions—physical offset and travel-relative
lane number—and prevents a backward lane from being reflected onto the wrong
side.

Geometry steps:

1. Read `OsmRoad.geom` and split it at the topology-node vertex indexes into
   logical source segments.
2. Transform each logical segment from WGS84 to EPSG:28992.
3. Use Shapely `offset_curve(offset_m)` against the source-frame line.
   Verified on shapely 2.1.2 / GEOS 3.13.1: a negative offset returns the curve
   in the **same** coordinate direction as its input. Older GEOS reversed
   right-side offsets, so a test must assert this rather than trusting it.
4. Reverse the completed offset geometry for `bwd` and `oneway=-1` travel so
   its coordinate order runs from travel-entry to travel-exit. Do not offset a
   reversed line unless the offset sign is also transformed explicitly.
5. Validate the offset result, in this order:
   - reject empty or non-`LineString` output with a diagnostic; never silently
     choose an arbitrary component of a multipart result;
   - **reject silent degeneracy**: GEOS removes the self-intersection an inner
     offset creates on a tight bend, which shortens the curve and pulls its ends
     inward while still returning a valid `LineString`. Measured case: an
     8 m-radius, 24.8 m arc offset by 5.25 m on the inside came back 9.0 m long
     with both endpoints displaced ~5 m. Require that each offset endpoint lies
     within a small tolerance (start with 0.5 m) of the source endpoint
     displaced along its own normal by `offset_m`, and that the result's length
     is within a sane ratio of the source length. Report failures per segment;
     do not emit a lane line that does not span its segment.

   This check is not optional polish: Milestone 2 anchors every connector on a
   lane line's first and last coordinate, so a quietly shortened lane line
   becomes a connector that starts in the wrong place. Ramps and roundabout
   rings are exactly where tight radii and large offsets coincide, though only
   1,623 ways reach 5+ lanes, so the worst offset in practice is ±10.5 m.

   Do not automatically fall back to naive per-vertex normal displacement: it
   can self-intersect, cut corners, and violate the required 3.5 m spacing at
   sharp bends. If failures are common on real ramps, treat a replacement
   offset algorithm as a separately tested change. It must preserve endpoints,
   remain simple/non-self-intersecting, maintain representative 3.5 m spacing,
   and pass the same validation before it can replace `offset_curve`.
6. Transform the result back to EPSG:4326.
7. Store directed lane geometry in travel order for `fwd` and `bwd` lanes.
   Unknown-direction geometry keeps the source way’s order. A `direction=both`
   lane is stored once in source order; its two directed traversal records
   reverse or retain that coordinate sequence in memory as needed.

   **Consequence for connectors:** stored coordinate order therefore means
   "travel order" for `fwd`/`bwd` but only "source order" for `both`. Nothing
   downstream may assume a lane line's last coordinate is its travel-exit —
   see Milestone 2 §6, which is written in terms of the traversal's travel-exit
   and travel-entry instead.

   **This differs from Lane Detail on purpose.** `osm_road_lane` stores every
   lane in the source way's coordinate order (see `_restore` in
   `parsers/osm_lanes.py`, and the comment in `web/config.js` explaining that
   `bwd` lanes come back in way order). Frontend assumptions written for that
   layer — arrow direction, `line-offset` sign — are therefore not transferable
   to this one. The new layer draws no arrows and applies no offset, so nothing
   depends on it today; the divergence is recorded so nobody later "fixes" one
   layer using the other's convention.

No tapering, merging, lane-width polygons, markings, or turn arrows are part of
this milestone. A one-lane road is exactly the source centerline, represented
as one thin feature.

### 4. Build from existing Driving Roads rows

Add `src/ndwinfo/refresh_osm_lane_lines.py` as a database-backed rebuild command
similar in operational purpose to `refresh_osm_lanes.py`, but with an entirely
independent implementation.

Required modes:

- `--bbox min_lon,min_lat,max_lon,max_lat` for fast development;
- `--all` for the first national backfill and controlled full rebuild;
- a topology-neighbour expansion around a bbox so the connector pass sees every
  road sharing a topology node with a selected road, plus the configured
  junction-box radius;
- `--roads 123,456,...` for selecting parent OSM way IDs;
- `--segments <osm_id>:<start_node_id>:<end_node_id>,...` for reproducing
  user-reported logical segment/lane cases.

The command must query `OsmRoad` (including `node_refs` once it exists) and
`ST_AsText(OsmRoad.geom)` only. It should stream or batch rows and run in one
transaction.

Define rebuild scope precisely. Before the topology backfill every road is one
unsplit segment, so steps 2–4 below collapse to "the requested roads" and the
command is trivially correct; the full scope rules start mattering when
segmentation and connectors arrive in Milestone 2.

1. **Requested roads** are the roads intersecting the bbox, named by `--roads`,
   or owning a logical segment named by `--segments`.
2. **Topology context roads** are every road sharing a topology node with a
   requested road, plus junction-box candidates within the configured radius.
   They are loaded for graph decisions but are not automatically rewritten.
3. **Rewrite roads** are the requested roads plus any context road whose derived
   topology-node set changes because a requested road was added, removed, or
   changed at one of its internal nodes. That context road must be re-segmented;
   rebuilding connectors alone would leave its old unsplit centerline in place.
4. **Affected logical segments** are segments of rewrite roads plus any
   non-rewritten context segment whose connector decision depends on a rewrite
   road.
5. Delete/reinsert centerlines for rewrite roads. Before doing so, explicitly
   delete connections where either `from_road_id` or `to_road_id` is a rewrite
   road; FK cascades are a safety net, not the scope definition.
6. Rebuild connections for the full affected logical-segment set, including
   crossings from a requested road to a context road. This prevents a local
   refresh from removing a boundary connector and never restoring it.

The same scope calculation is used by `--bbox` and `--segments`; only how the
requested-road seed is selected differs. The command must print counts for:

- source roads processed;
- lane lines produced;
- assumed counts;
- roundabout-implied one-ways applied (expect ~17.4k nationally — a near-zero
  count means step 0 is not running);
- shared single-track two-way lanes;
- ambiguous-direction lines (expect a few hundred nationally, not tens of
  thousands — a large count means roundabouts are being misclassified);
- invalid/skipped tag sets;
- geometry failures, split into empty/multipart and degenerate-offset.

After the independent path is verified, wire the same builder into
`OsmRoadIngester` so weekly OSM updates refresh the new table. The old Lane
Detail refresh may continue separately, but neither builder may consume the
other’s output.

## Milestone 2: lane connections

### 1. Add a separate connection model

Add `OsmLaneConnection` with:

| Column | Purpose |
|---|---|
| `id` text PK | Stable `<from_traversal_id>><to_traversal_id>` identity |
| `from_lane_id` text FK | Incoming physical `OsmLaneCenterline.id`, cascade delete |
| `from_direction` text | Direction used to traverse the incoming lane |
| `to_lane_id` text FK | Outgoing physical `OsmLaneCenterline.id`, cascade delete |
| `to_direction` text | Direction used to traverse the outgoing lane |
| `from_road_id` bigint | Indexed rebuild/debug key |
| `to_road_id` bigint | Indexed rebuild/debug key |
| `from_segment_id` text | Incoming logical segment |
| `to_segment_id` text | Outgoing logical segment |
| `connection_type` text | `continuation`, `exit`, `entry`, `split`, `join`, or `manual` |
| `confidence` text | `exact`, `tagged`, `junction_box`, `heuristic`, or `manual` |
| `geom` geometry(LineString, 4326) | Short connecting curve |
| `raw` jsonb | Candidate scores and applied override, when relevant |

Use a GiST index on `geom`, btree indexes on both road IDs, and btree indexes on
both logical segment IDs. Keeping connectors separate means centerline geometry
remains simple and inspectable.

### 2. Build a directed endpoint graph

For every directional traversal of every logical segment, record:

- travel-entry and travel-exit endpoint;
- first and last tangent/bearing;
- lane count and lane IDs in driver-left-to-right order;
- source topology-node IDs and endpoint coordinates;
- `oneway`, `highway`, `ref`, `name`, `junction`, and trusted lane-turn tags.

Connection candidates are created only from an incoming travel-exit to an
outgoing travel-entry. This directed test is what prevents the common
one-way/two-way bug where a lane attaches to the wrong carriageway.

A normal `fwd`/`bwd` lane contributes one traversal. A `direction=both` shared
lane contributes two traversal records over the same stored centerline, one in
each coordinate order. Traversal records are graph objects, not duplicate
GeoJSON lane features.

Candidate discovery is staged, but the two stages serve **different** cases and
neither is a fallback for the other:

1. **Exact topology-node identity — for continuations and internally noded
   junctions.** Match logical-segment ends by retained OSM node ID. Connection
   work starts after the topology backfill (see Implementation order), so node
   IDs are always available here and there is no coordinate-matching mode.
2. **Junction-box matching — for junctions, entries and exits.** OSM routinely
   models one intersection as several nodes metres apart, so at a real junction
   the exit way usually does *not* start at the approach's end node. This repo
   has already measured it (`parsers/osm_junctions.py` module docstring): exact
   shared-node matching finds a left/through/right set for **18 of 4,706**
   turn-tagged ways, while taking exits whose start lies within a 25 m junction
   box finds a left target for **2,368** of them and both a left and a right for
   **1,219**.

   Treating the junction box as a last-resort fallback would therefore reduce
   Milestone 2's headline goal — entries and exits attaching to the right lane —
   to roughly 0.4% coverage. It is a first-class, separately scored and
   separately tested path. What keeps it safe is not its ordering but its
   filters: a box candidate must still pass the directed
   travel-exit→travel-entry test and turn-angle limit, plus one of the explicit
   eligibility classes below. Proximity alone is never enough.

Junction-box eligibility classes:

- same `ref` or same non-empty `name`;
- a recognized non-link↔`_link` highway entry or exit;
- approach→roundabout-ring or roundabout-ring→exit, where the ring segment has
  `junction=roundabout` and the angle follows the ring's one-way direction;
- an explicitly compatible `turn:lanes*` token;
- a manual override.

The roundabout class is necessary because an ordinary approach and ring often
have different or absent `ref`/`name` values and neither is a `_link`. For a
closed roundabout, topology splitting from Milestone 1 §0 supplies an actual
logical ring endpoint at every attached approach; never attach every approach
to the closed way's arbitrary first coordinate.

Report coverage for both stages separately (see the counters below). A build
where junction-box movements are near zero is broken, not conservative.

Reject:

- U-turn candidates;
- a candidate entered through its travel-exit;
- reversible/unknown directions — note this means `direction=unknown`, not
  `direction=both`: a shared single-track lane has a definite direction per
  traversal and connects normally;
- connections whose turn angle exceeds the configured limit;
- ambiguous candidates whose best score is not clearly better than the next;
- lane mappings that cross one another.

Unresolved cases are preferable to confidently drawing a wrong connection.

### 3. Choose the road-to-road movement set first

Lane assignment happens only after the outgoing movement **set** is known. A
fork can have one primary continuation plus one or more outgoing branches; it
must not be reduced to one winning road.

Build the set in two passes:

1. Select at most one **primary continuation** using same `ref`, same non-empty
   `name`, compatible highway class, and smallest heading change. If the best
   two primary candidates are effectively tied, leave the primary continuation
   unresolved.
2. Add independently eligible **branch movements**:
   - explicit movements named by compatible `turn:lanes*` tokens;
   - recognized right-side outgoing `_link` exits;
   - roundabout entry/exit movements;
   - manual connections.

Within each candidate class, evidence ranks from strongest to weakest:

1. manual override;
2. compatible `turn:lanes*`;
3. exact topology-node identity;
4. junction-box match, with distance folded into the score.

Road identity and angle qualify/rank a candidate; proximity or similar bearing
alone never does. A tie suppresses only the ambiguous movement class, not an
independent, confidently identified mainline or `_link` movement at the same
node.

### 4. Assign lanes without crossings

Base rules:

- equal-count straight continuation: lane `i -> i`;
- all assignments preserve left-to-right order;
- a source lane may connect to more than one target only for an explicit
  `turn:lanes*` split **or a recognized highway exit/roundabout branch**;
- multiple incoming lanes may converge onto one target only for an explicit
  join/merge **or a recognized highway entry**;
- do not infer a left-side entry or exit under the default policy.

Highway exit:

- detect an outgoing `_link` branch at the source lane’s travel-exit;
- for a one-lane exit, connect the source mainline’s rightmost lane
  (`lane_count`) to link lane `1`;
- for a `K`-lane exit, use explicit turn-lane allocation when valid; otherwise
  allocate the `K` rightmost mainline lanes to the link in order;
- keep the primary mainline continuation mapped to its left-to-right lane
  block. The rightmost source lane may therefore have both a mainline connector
  and an exit connector even when `turn:lanes` is absent; recognition of the
  outgoing `_link` is the controlled exception authorizing that split.

Highway entry:

- detect an incoming `_link` branch feeding a non-link target;
- a one-lane ramp connects to the target mainline’s rightmost lane;
- a `K`-lane ramp maps to the `K` rightmost target lanes in order;
- the incoming mainline keeps the left block, so ramp and mainline mappings do
  not cross;
- when the target does not add a lane, the ramp and the incoming mainline may
  both feed the same rightmost target lane. Recognition of the incoming `_link`
  is the controlled exception authorizing that join.

Lane-count change without a link:

- preserve lane numbers for the shared left block;
- use trustworthy `turn:lanes` merge/split tokens when present;
- if the unexplained extra or missing lane would require guessing which side
  changed, leave that lane unresolved and report it.

The “left block” convention above is intentional: with right-side entries and
exits, existing through lanes retain their numbers while the rightmost lane is
added or removed.

### 5. Handle one-way/two-way transitions explicitly

This case must have dedicated tests and not fall through generic junction-box
scoring.

For a two-way logical segment:

- its `fwd` lanes leave from the logical segment’s last coordinate and enter at
  its first;
- its `bwd` lanes leave from the first coordinate and enter at the last;
- each direction presents a separate directed endpoint record;
- a **shared** single-track centerline produces two in-memory traversals over
  its one stored row. A through-route therefore connects normally in both
  directions without duplicate lane features. A traversal may never connect to
  the opposite traversal of the same physical lane on the same logical segment;
  that is a U-turn.

Roundabouts, after step 0 normalization and topology splitting, are directed
one-way logical ring segments. They use the explicit roundabout eligibility
class from §2; they are not forced through the same-name/`_link` filter used by
ordinary junction-box candidates.

When one two-way road meets two separated one-way carriageways:

- the two-way `fwd` record can connect only to the one-way whose travel-entry
  is at that node and whose bearing continues the `fwd` movement;
- the two-way `bwd` record can connect only to the opposite one-way in its own
  travel direction;
- left/right lane numbering is recalculated in each driver’s frame before
  mapping;
- the same rules apply in reverse when two one-ways combine into one two-way
  road.

This directed representation should solve the side choice without hard-coding
“east road”/“west road” or depending on the OSM way digitization direction.

### 6. Draw connector curves

Create connector geometry in EPSG:28992. Every endpoint below is taken from the
**traversal**, never from the stored coordinate array:

1. start at the incoming traversal’s travel-exit coordinate;
2. end at the outgoing traversal’s travel-entry coordinate;
3. use the incoming traversal’s exit tangent and the outgoing traversal’s entry
   tangent as cubic Bézier directions;
4. bound handle length by connector span so short joins do not loop;
5. sample enough points for a smooth MapLibre line, then transform to WGS84;
6. omit a separate connector feature when lane endpoints already coincide
   within a small tolerance.

For a `fwd` or `bwd` lane the travel-exit is the stored line's last coordinate,
because §3 step 7 stores those in travel order. For a `direction=both` lane it
is the last coordinate for the `@fwd` traversal and the **first** coordinate for
the `@bwd` traversal. Reading "the lane line's final coordinate" literally would
anchor every backward connector off a single-track two-way road at the wrong end
of that road — a 664-way class nationally, so the error would be common and
obvious on the map, but only after connectors exist.

Connectors use the same thin `#111171` style as lane lines. They must not be
rendered as filled surfaces or width-sized caps.

## Manual connection overrides

Add a small reviewed JSON file, for example
`src/ndwinfo/osm_lane_connection_overrides.json`, with stable full lane IDs:

```json
[
  {
    "from": "ll:386967467:10001:10002:fwd:3@fwd",
    "to": "ll:7399108:10002:10003:fwd:1@fwd",
    "action": "connect",
    "note": "confirmed from development review"
  },
  {
    "from": "ll:123456:20001:20002:fwd:2@fwd",
    "to": "ll:654321:20002:20003:fwd:1@fwd",
    "action": "block",
    "note": "nearby carriageway is not the continuation"
  }
]
```

Rules:

- accept committed overrides only after node-ID topology metadata has been
  backfilled; the pre-backfill `<osm_id>:0:0` segment keys are not stable
  identities and are rewritten wholesale when splitting begins;
- normalize every endpoint to a full traversal ID. A shared physical lane
  requires an explicit `@fwd` or `@bwd`;
- on `--all`, validate every override. On `--bbox`/`--segments`, select only
  overrides whose from/to road is in the affected scope, and query any referenced
  context lane from the database before declaring it missing. Unrelated
  national overrides must not make a local rebuild fail;
- fail with a clear error when a relevant override references a missing lane or
  an impossible travel direction;
- apply `block` before candidate scoring and `connect` after automatic
  candidates are built;
- never silently reinterpret a full ID after lane counts change;
- include `connection_type=manual` and the note in the connector properties;
- accept `segment_id:lane_nr` as developer input only when that logical segment
  has one unambiguous traversal, then persist the normalized full traversal ID
  in the file.

This gives the requested `segment:lane_nr` feedback loop without embedding
special cases in geometry code.

## API

Add `GET /api/osm/lane-lines` to `src/ndwinfo/api/routers/osm.py`.

The endpoint:

- requires the normal bbox;
- queries `OsmLaneCenterline` and `OsmLaneConnection`, never `OsmRoadLane`;
- joins `OsmRoad` only to expose useful parent tags such as `highway`, `ref`,
  and `name`;
- uses `ST_Intersects` against both new GiST indexes;
- returns one GeoJSON `FeatureCollection` containing centerlines and
  connectors;
- queries the two tables **separately, each with its own cap**, and merges the
  results. A single shared limit ordered by feature ID would let a dense block
  of centerlines consume the whole budget and drop every connector in the
  viewport — which is precisely the thing the layer exists to show. Use
  `osm_lane_line_max_features` and `osm_lane_connection_max_features`
  (following the existing `osm_max_features` / `osm_lane_max_features`
  convention in `config.py`);
- returns one unambiguous truncation contract:

  ```json
  {
    "metadata": {
      "truncated": true,
      "truncated_by_kind": {
        "lanes": false,
        "connections": true
      }
    }
  }
  ```

  `metadata.truncated` is always the aggregate boolean;
- orders each query by stable feature ID before limiting so the result is
  deterministic.

Centerline feature properties:

```json
{
  "kind": "lane",
  "id": "ll:7399108:10002:10003:fwd:1",
  "road_id": 7399108,
  "segment_id": "7399108:10002:10003",
  "lane_nr": 1,
  "lane_count": 1,
  "direction": "fwd",
  "offset_m": 0.0,
  "count_source": "lanes",
  "oneway_source": "tag",
  "highway": "motorway_link",
  "ref": "A9"
}
```

Connector feature properties:

```json
{
  "kind": "connection",
  "id": "ll:386967467:10001:10002:fwd:3@fwd>ll:7399108:10002:10003:fwd:1@fwd",
  "from": "ll:386967467:10001:10002:fwd:3@fwd",
  "to": "ll:7399108:10002:10003:fwd:1@fwd",
  "connection_type": "exit",
  "confidence": "junction_box"
}
```

`confidence` values are `exact`, `tagged`, `junction_box`, `heuristic`, or
`manual`. `junction_box` is named separately from `heuristic` because, per the
measured coverage above, it is the normal way a junction movement is found, not
a degraded guess.

Keep the old `GET /api/osm/lanes` endpoint unchanged while the new layer is
being compared.

## Web layer

Add this independent layer to `web/config.js`:

```javascript
{
  key: 'lanes',
  label: 'Lanes',
  group: 'osm',
  endpoint: '/osm/lane-lines',
  geomType: 'line',
  minZoom: 15,
  promoteId: 'id',
  legendColor: '#111171',
  // map.js defaults line-join to 'miter' and only reads layer.lineCap /
  // layer.lineJoin, so both must be named explicitly. Butt caps once
  // connectors exist, so geometry rather than cap overdraw decides continuity.
  lineCap: 'butt',
  lineJoin: 'round',
  paint: {
    'line-color': '#111171',
    'line-width': 2,
    'line-opacity': 1
  }
}
```

Place it after Lane Detail in `LAYERS`: `map.js` adds one MapLibre layer per
`LAYERS` entry in order, so a later entry draws on top and the thin blue lines
stay visible when both development layers are enabled.

Nothing needs to be added to `DEFAULT_ENABLED` — that set is only the fallback
for a first visit, and `loadSavedSet` filters a returning user's saved set
against the known keys, so a newly added key starts off in both cases. Note the
consequence for review: after deploying, the layer must be switched on by hand
in the layer panel before anything appears.

No `metresWide`, casing, fill, marking, overlay, or lane-arrow configuration is
needed. Spacing is already encoded in geographic geometry; line width is only
the visible stroke.

The existing generic source, bbox, popup, layer-panel, and visibility code
handles the geometry, but `fetchLayer` currently ignores GeoJSON metadata.
Extend it to inspect `data.metadata?.truncated` before `setData`:

- when true, show the existing zoom/bounds hint with a Lanes-specific message
  and log `truncated_by_kind`;
- clear that state on the next non-truncated Lanes response;
- keep rendering the partial response so development can continue;
- do not overload the existing HTTP-400 `bboxTooLarge` flag with incompatible
  state—store per-layer truncation state or generalize the hint deliberately.

Add frontend tests for this behavior and a configuration regression test so
future refactors cannot accidentally point `Lanes` at `/osm/lanes`.

## Diagnostics for development

The layer popup must show the stable lane or connection ID **as a one-click
copyable value**. A full traversal ID now looks like
`ll:386967467:10001:10002:fwd:3@fwd`; unlike the original `segment:lane_nr`
shorthand it cannot be read off the screen and retyped, so a copy affordance is
what keeps the review loop usable. Keep accepting the short
`segment_id:lane_nr` form as input wherever the segment has one unambiguous
traversal, and keep `--roads <osm_id>` available so a way ID read from the
Driving Roads popup is still actionable on its own.

In addition, the rebuild command should optionally emit a machine-readable
unresolved report:

```json
{
  "node": [4.6501234, 52.4678901],
  "node_id": 10002,
  "from": "ll:386967467:10001:10002:fwd:3@fwd",
  "reason": "ambiguous outgoing movement",
  "candidates": [
    {"segment_id": "7399108:10002:10003", "direction": "fwd", "angle_deg": 21.4},
    {"segment_id": "127572892:10002:10004", "direction": "fwd", "angle_deg": 17.9}
  ]
}
```

Add CLI filters for `--segments` and `--unresolved-only`. This makes a reported
`segment:lane_nr` actionable without searching the national dataset or relying
on screenshots alone.

Useful counters:

- lane endpoints considered;
- exact node-ID topology movements;
- logical segments produced, and how many came from an internal split (expect
  ~4,900 nationally — a zero here means topology splitting is not running, and a
  number far above it means non-junction vertices are being treated as topology
  nodes);
- junction-box movements, and the share of turn-tagged approaches that found a
  target through the box. Compare against the measured baseline in
  `parsers/osm_junctions.py`: exact matching alone reached 18 of 4,706
  turn-tagged ways, the 25 m box reached 2,368. A new build that lands near the
  former has effectively disabled junction connections;
- entry and exit movements;
- one-way/two-way transitions;
- movements crossing a shared single-track lane;
- ambiguous/rejected movements by reason;
- manual connects and blocks;
- connectors omitted because endpoints already touch.

## Test plan

### Pure lane-planner tests

Create `tests/test_osm_lane_lines.py` using real Dutch WGS84 coordinates plus
small synthetic RD-friendly fixtures.

Required cases:

1. one-lane one-way produces one centerline at zero offset;
2. two, three, and four lanes produce adjacent midpoint spacing of
   `3.5m +/- 0.1m`;
3. secondary roads also use 3.5m, preventing the old 2.75m rule from leaking
   in;
4. `oneway=-1` reverses travel but keeps lane 1 on the driver’s left;
5. explicit forward/backward counts produce the correct physical sides under
   Dutch right-hand traffic;
6. even two-way totals split evenly;
7. odd two-way totals of 3+ draw all physical lines but mark direction unknown;
8. missing one-way and two-way counts use flagged defaults;
9. malformed and over-ceiling counts are handled predictably;
10. every generated ID and lane number is stable under repeated builds, and
    every ID carries the `ll:` prefix so it cannot be confused with an
    `OsmRoadLane.id`;
11. source WGS84 geometry is never offset directly in degrees;
12. multipart/empty offset failures are reported rather than partially drawn;
13. **`junction=roundabout` with no `oneway` tag** yields one-way `fwd` lanes
    with `oneway_source=roundabout_implied` — one ring line for `lanes=1`, not
    two lines and not `unknown`. Include the `junction=roundabout` +
    `oneway=no` anomaly and assert the tag wins;
14. **single-track two-way** (`lanes=1`, no `oneway`) yields exactly one
    `direction=both` centerline row at offset `0` — the planner's whole
    responsibility here; the two traversal records belong to the connector
    tests below;
15. **offset orientation is pinned**: a negative `offset_curve` returns the same
    coordinate direction as its input, so a right-hand `fwd` lane is not
    silently reversed. This is a GEOS-version guard and must fail loudly if a
    future image reverses right-side offsets;
16. **degenerate offset detection**: the measured 8 m-radius arc with a 5.25 m
    inner offset is reported as a geometry failure rather than stored as a
    9 m line whose ends have moved;
17. **segment-key uniqueness is asserted**: a synthetic way that traverses the
    same ordered node pair twice raises rather than silently overwriting one of
    the two segments. (No such way exists nationally today — the test guards the
    assertion, not a known case.)

### Connector tests

Create `tests/test_osm_lane_connections.py`.

Required cases:

1. equal-count straight segments map `i -> i`;
2. a one-lane highway exit starts at the rightmost mainline lane;
3. a one-lane highway entry ends at the rightmost mainline lane;
4. multi-lane entry/exit mappings preserve order and do not cross;
5. a two-way road splitting into two one-ways selects the correct side for each
   travel direction;
6. two one-ways joining a two-way road select the correct directional half;
7. `oneway=-1` connects from the true travel end;
8. a nearby parallel/opposite carriageway is rejected;
9. ambiguous candidates produce no connector plus a diagnostic;
10. an explicit manual connection wins and a manual block removes a candidate;
11. generated Bézier curves start/end exactly on their referenced lane lines
    and leave/arrive with the correct tangents;
12. connector assignments are monotonic across the lane cross-section;
13. a junction-box exit whose start node is metres away from the approach's end
    node is still connected, with `confidence=junction_box` — the case exact
    matching misses for all but 18 of 4,706 turn-tagged ways;
14. a `direction=both` centerline yields exactly two traversal records, a route
    across it connects in both directions, and it never connects `@fwd`→`@bwd`
    to itself;
15. **a connector leaving a `direction=both` lane via its `@bwd` traversal
    starts at the stored line's *first* coordinate**, not its last. This is the
    one place where stored coordinate order and travel order disagree, so assert
    the connector's start point equals the traversal's travel-exit for all three
    of `fwd`, `bwd`, and `both@bwd`;
16. an approach onto and an exit off a roundabout ring connect as ordinary
    one-way movements, including when the ring way carries no `oneway` tag;
17. a road joining an internal node of another OSM way connects at that logical
    segment boundary, not at the parent way's first or last coordinate;
18. a closed roundabout way is split at every attached approach node, and each
    connector lands on the corresponding ring segment rather than the closed
    way's arbitrary start coordinate;
19. a recognized exit can produce a mainline continuation and a right-side
    branch from the same source lane without `turn:lanes`;
20. a recognized entry can join a ramp and mainline traversal into the
    rightmost target lane without an explicit merge token;
21. `junction=circular` without `oneway` is not silently treated as a one-way
    roundabout.

### Persistence and API tests

- migrations add ordered `osm_road.node_refs` (index only if measured as needed)
  and create both new tables, foreign keys, and spatial indexes;
- topology metadata retains internal shared nodes and produces stable logical
  segment IDs;
- rebuilding a road from three lanes to two deletes its stale third line on
  every affected logical segment;
- deleting an `osm_road` cascades to new lines and their connections;
- a bbox rebuild restores connectors where one side is a context road outside
  the requested bbox;
- unrelated national overrides do not fail a bbox/segment rebuild;
- bbox queries return both feature kinds and deterministic IDs;
- a `direction=both` lane appears once in the lane FeatureCollection even though
  connectors may reference its `@fwd` and `@bwd` traversals;
- the topology switchover is a clean truncate-and-rebuild: after backfilling
  `node_refs`, no `<osm_id>:0:0` segment ID survives in either new table;
- per-kind truncation metadata is correct, and a viewport whose centerlines
  exceed their cap still returns its connectors;
- the endpoint contains parent OSM properties but no Lane Detail row data;
- a regression test fails if the new endpoint or builder starts querying
  `OsmRoadLane`.

### Frontend tests

- layer label is exactly `Lanes`;
- endpoint is exactly `/osm/lane-lines`, not `/osm/lanes`;
- line and legend colors are exactly `#111171`;
- it is a normal thin line with no metre-wide lane-band expression;
- it is zoom-gated, independently toggleable, and off by default;
- aggregate and per-kind truncation are surfaced visibly and cleared by the
  next complete response;
- its popup exposes the lane/connection IDs needed for manual review, prefixed
  with `ll:` and labelled with the layer they came from, so an ID reported
  during review cannot be mistaken for a Lane Detail lane number;
- `lineCap` is `butt` and `lineJoin` is `round`, since `map.js` would otherwise
  fall back to MapLibre's `miter` default.

### Visual acceptance route

For each selected development area:

1. rebuild with `refresh_osm_lane_lines --bbox ...`;
2. enable satellite, Driving Roads, Lane Detail, and Lanes as needed, then
   compare them independently;
3. confirm one-lane roads have one blue line rather than a filled lane;
4. measure representative adjacent blue lines at 3.5m;
5. inspect a motorway exit, a motorway entry, a same-road lane-count change, a
   roundabout with no `oneway` tag, a single-track two-way road, and a
   two-way-to-separated-one-way transition. Include the one national
   `oneway=-1` way once, then stop spending review time on it;
6. record wrong or missing cases by stable `segment:lane_nr`;
7. turn the old Lane Detail layer off and repeat the check, proving the new
   layer has no rendering or data dependency on it.

## Implementation order

Milestone 1 draws lines; nothing in it waits on a national PBF re-ingest.

1. Add the new centerline/connection models and migration, with `segment_id`
   defaulting to the unsplit `<osm_id>:0:0` form.
2. Extract the small public directed-line geometry helpers.
3. Implement and unit-test the normalized lane planner.
4. Implement metric centerline geometry, the offset validation, and spacing
   tests.
5. Add the bbox/segment rebuild command and populate a small review area from
   existing `osm_road` rows.
6. Add the new API endpoint and its independence regression tests.
7. Add the blue **Lanes** frontend layer, including visible truncation state.
8. **Review Milestone 1 visually.** Geometry, spacing, roundabout direction and
   single-track handling are all reviewable at this point, on unsplit segments.

Milestone 2 opens with the topology work, because connectors — not centerlines —
are what needs real node identity.

9. Add `osm_road.node_refs`, update the PBF parser, and test ordered-node
    retention plus internal/shared-node and closed-ring extraction.
10. Backfill `node_refs` nationally, then implement logical segmentation and
    truncate-and-rebuild both new tables onto node-ID segment IDs. Manual
    overrides become acceptable only from here on.
11. Add the directed endpoint graph and exact same-road continuations,
    including `direction=both` traversals.
12. Add junction-box candidate discovery with its eligibility/angle filters and its
    coverage counters — before entry/exit allocation, because almost every real
    entry and exit is found through the box rather than through a shared node.
13. Add the movement-set builder, right-lane highway exit/entry allocation, and
    roundabout approach/ring eligibility.
14. Add explicit two-way/one-way transition handling and unresolved
    diagnostics.
15. Add and validate manual overrides from supplied `segment:lane_nr` cases.
16. Wire the independent builder into the regular OSM ingest and perform a
    national backfill.

## Definition of done

Milestone 1 is complete when:

- the UI has a separately toggleable layer labeled **Lanes**;
- it fetches only `/api/osm/lane-lines`;
- every visible feature is a thin `#111171` line;
- one-lane roads, including shared two-way roads, have exactly one stored and
  returned centerline feature;
- adjacent lines are 3.5m apart for every supported highway class;
- roundabout rings without an `oneway` tag draw as single one-way rings, and
  the roundabout-implied counter is in the expected ~17.4k range nationally,
  while the circular-implied counter is exactly **0** (all 429
  `junction=circular` ways already tag `oneway=yes`);
- single-track two-way roads draw one line and still carry both travel
  directions;
- no lane line is stored that fails the offset endpoint/length sanity check;
- the new data can be rebuilt from `osm_road` while `osm_road_lane` is empty or
  unavailable;
- automated tests prove the new path does not query Lane Detail data.

Milestone 2 is complete when:

- `node_refs` is backfilled and logical segments split at retained internal
  topology nodes, with node-ID-based IDs and no surviving `<osm_id>:0:0` key;
- exact same-road continuations are connected;
- internal-node branches and closed roundabouts connect at their actual logical
  segment boundary rather than the parent OSM way's endpoint;
- every connector is anchored on its traversal's travel-exit/travel-entry, which
  for a `direction=both` lane's `@bwd` traversal is the stored line's first
  coordinate;
- junction-box movements are found at a rate comparable to the measured
  baseline (thousands of turn-tagged approaches, not tens), and the counter
  proving it is printed by the rebuild command;
- highway entries and exits use the rightmost travel lane;
- recognized exits may branch from a continuing rightmost lane and recognized
  entries may join its target lane without requiring absent `turn:lanes` tags;
- two-way/separated-one-way transitions connect by travel direction to the
  correct carriageway;
- ambiguous connections remain unresolved and are reportable by stable lane
  ID;
- manual connect/block overrides are validated and reproducible;
- bbox rebuilds preserve/recreate cross-boundary connectors and validate only
  relevant manual overrides;
- the full rebuild stays within acceptable ingest time and memory limits;
- any viewport truncation is visibly reported with per-kind detail.

## Explicitly out of scope for these milestones

- filled lane surfaces or asphalt bands;
- lane-edge and divider paint;
- traffic-speed coloring;
- turn-arrow symbols;
- merge tapers that deform a lane line before the segment endpoint;
- silently “fixing” ambiguous OSM lane counts;
- deleting or replacing the existing Lane Detail layer.

Those can be evaluated only after the independent blue-line geometry and
connections are correct.
