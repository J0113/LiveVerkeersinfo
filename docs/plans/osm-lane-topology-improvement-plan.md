# OSM Lanes topology and transition improvement plan

Status: implemented and validated on 2026-07-29

Scope: the independent **Lanes** layer only

Supersedes: the movement-selection, lane-allocation, and connector-geometry
parts of `docs/plans/osm-lane-lines-plan.md` where this plan is more specific

Validated against: local Docker database and
`data/netherlands-latest.osm.pbf` on 2026-07-29

## 1. Purpose

The first Lanes implementation correctly derives thin lane centerlines from
`OsmRoad`, but complex junctions still expose two related defects:

1. the 25 m junction-box search can connect to a road that is reachable only
   after traversing an intermediate short OSM way;
2. lane assignment is based mainly on lane counts and movement class, rather
   than allocating individual `turn:lanes` fields to movements first.

Those defects create duplicate shortcuts, crossing connectors, missing
continuations, and misleading `split`/`join` labels. Smoothing the resulting
curves cannot fix the topology.

This plan makes connection generation topology-first:

1. establish immediate road-to-road adjacency;
2. suppress candidates that skip an intermediate segment;
3. select the complete movement set;
4. allocate source and target lane blocks using lane-level OSM tags;
5. resolve split/join topology;
6. calculate shared trim stations;
7. draw connector geometry last.

The implementation must continue to use only `OsmRoad` / Driving Roads source
data. It must not query or consume `OsmRoadLane`, Lane Detail rows, or old
Lane Detail connector output.

## 2. Reported regression fixture

Primary review bbox:

```text
4.655832,52.476255,4.656677,52.476823
```

Relevant OSM roads:

| Road | Class | Lanes | `turn:lanes` | `placement` | Role |
|---|---|---:|---|---|---|
| `1227426726` | motorway | 3 | `none\|merge_to_left\|slight_right` | `right_of:1` | upstream approach |
| `1096129216` | motorway | 4 | `none\|merge_to_left\|slight_right\|slight_right` | `right_of:1` | short transition |
| `1096129213` | motorway | 2 | `none\|merge_to_left` | — | mainline continuation |
| `1096129217` | motorway_link | 2 | — | `transition` | exit |

The current output contains these wrong shortcuts:

```text
ll:1227426726:0:0:fwd:2@fwd
  > ll:1096129217:0:0:fwd:1@fwd

ll:1227426726:0:0:fwd:3@fwd
  > ll:1096129217:0:0:fwd:2@fwd
```

Both have `confidence=junction_box` and bridge approximately 12.42 m. They
skip road `1096129216`. Correct connectors from `1096129216` to the same exit
also exist, so the two paths overlap and cross.

The source of the defect is not merely missing node IDs. With node IDs
available, the primary continuation would rank correctly, but the current
independent branch pass could still add the non-exact shortcut. The candidate
graph itself must understand intermediate-segment dominance.

### Required movement result

At road level:

```text
1227426726 -> 1096129216
1096129216 -> 1096129213
1096129216 -> 1096129217
```

Forbidden:

```text
1227426726 -> 1096129213
1227426726 -> 1096129217
```

Expected lane families:

| Movement | Expected allocation |
|---|---|
| upstream through/merge block → transition through/merge block | `1→1`, `2→2` |
| upstream `slight_right` block → transition `slight_right` block | source lane `3` splits to target lanes `3` and `4`; matching `placement=right_of:1` anchors confirm lane `4` is added on the right |
| transition through/merge block → mainline | `1→1`, `2→2` |
| transition `slight_right` block → exit | `3→1`, `4→2` |

The `merge_to_left` lane is not labelled `join` at a boundary where it still
maps one-to-one. It becomes a `join` only at the boundary where two source
lanes actually share one target traversal.

### 2.1 Validated local data state

The implementation sequence must account for the database that actually
exists:

- all `167,192` `osm_road` rows have `node_refs IS NULL`;
- all `694` current `osm_lane_centerline` rows use `<osm_id>:0:0` segment IDs;
- the current test-region connection table has `442` rows: `281`
  `junction_box` and `161` `tagged`, with no `exact` rows;
- the two claimed 12.42 m shortcuts and the incorrectly labelled one-to-one
  `join` reproduce exactly;
- `26,261` of `27,587` motorway/motorway-link way ends (95.2%) have a
  byte-identical successor start coordinate in the stored source geometry.

The cached national PBF exists locally. Its most recent ingest took about
12.5 minutes, so a controlled reingest is a practical Phase 0 prerequisite,
not an aspirational deployment step.

## 3. Goals and invariants

### Goals

- Never skip a directly adjacent logical segment to reach a junction-box
  candidate farther along the same path.
- Preserve legitimate junction-box entries/exits that OSM models with
  non-coincident nodes.
- Use the complete, pipe-delimited `turn:lanes*` value lane by lane.
- Support one-to-many splits and many-to-one joins explicitly.
- Keep lane assignments monotonic within each movement; connectors must not
  cross except where two valid movements geometrically cross at a real
  intersection.
- Give every physical lane endpoint one resolved trim station shared by all
  connectors using that endpoint.
- Prevent short transition ways from being consumed by independent trimming at
  both ends.
- Keep output deterministic and debuggable.

### Locked invariants

1. Road adjacency is evaluated using the original logical-segment endpoints,
   not offset lane endpoints.
2. Lane-offset distance is never evidence that two road segments are
   topologically adjacent.
3. Geometry generation cannot create, remove, or change a selected movement.
4. A `join` means at least two source traversals map to one target traversal.
5. A `split` means one source traversal maps to at least two target
   traversals.
6. `merge_to_left/right` alone does not force `connection_type=join`.
7. A non-exact branch dominated by an immediate topological successor is
   rejected even if it is a compatible `_link`.
8. Unknown/reversible directions remain unconnected automatically.
9. Manual overrides remain the final reviewed authority.
10. No new module may import the old `osm_lanes` or `osm_junctions` parsers.

## 4. Data required by the graph

### 4.1 Retain original segment endpoints

Each generated `OsmLaneCenterline.raw` already retains logical start/end node
IDs. Add the original, non-offset logical-segment endpoints:

```json
{
  "start_node_id": 123,
  "end_node_id": 456,
  "source_start": [4.656202, 52.476592],
  "source_end": [4.656115, 52.476501]
}
```

These coordinates come from `LogicalSegment.line` before applying any lane
offset. Store them once per lane row for now; normalization into a separate
segment table is unnecessary unless measurement shows JSON duplication is a
problem.

Use EPSG:28992 for all endpoint-distance comparisons.

### 4.2 Adjacency evidence

Every road-movement candidate gets one of these evidence classes:

| Evidence | Definition | Default confidence |
|---|---|---|
| `node_exact` | non-zero source exit node ID equals target entry node ID | `exact` |
| `endpoint_exact` | original source exit and target entry are within 0.5 m in RD | `exact` |
| `junction_box` | directed target entry is within 25 m and passes explicit eligibility filters | `junction_box` |
| `manual` | committed override | `manual` |

`endpoint_exact` is a normal adjacency mechanism derived from the original
source geometry. In the current database it is the only exact-adjacency
mechanism available: 95.2% of stored
motorway/motorway-link way ends have a byte-identical successor start.
It must compare original road endpoints, never offset lane endpoints.

Phase 0 backfills node IDs before topology thresholds, dominance behavior, or
manual overrides are tuned. After that reingest, `node_exact` is authoritative
when present and `endpoint_exact` remains a first-class fallback for
independently noded but coordinate-coincident OSM ways. Report disagreement
between node and coordinate evidence; do not silently choose coordinate
evidence over conflicting non-zero node IDs.

Do not overload the existing `exact` boolean. Use an enum-like field such as
`adjacency_evidence` so diagnostics preserve why a movement was accepted.

### 4.3 Directed immediate-successor graph

Build a graph keyed by `(segment_id, direction)`. An edge is an immediate
successor only when its evidence is `node_exact`, `endpoint_exact`, or
`manual`.

The graph is directed in travel order:

- a source traversal exits at its travel-exit;
- a target traversal enters at its travel-entry;
- `direction=both` contributes separate `@fwd` and `@bwd` traversals;
- opposite traversals of the same physical segment are never successors.

This graph is constructed before junction-box movement selection.

### 4.4 Available lane-position evidence

All OSM way tags are already stored verbatim in `OsmRoad.raw`; the following
signals need parsing, not a source-schema change:

| Signal | National stored coverage | Initial use |
|---|---:|---|
| bare `placement` | 20,381 driving ways; 4,282 of 13,788 motorway ways and 3,782 of 13,799 motorway links | strongest widening-side/reference-line evidence |
| `placement:forward` / `placement:backward` | 399 / 59 ways | directional placement override |
| `placement:start` / `placement:end` | 320 / 321 ways | placement at the relevant segment boundary |
| `destination:lanes` | 2,271 ways | per-lane destination signature |
| `destination:ref:lanes` | 1,059 ways | per-lane signed-route signature |
| `change:lanes` plus directional variants | 2,937 bare, 683 forward, 675 backward | cross-lane reachability constraint and diagnostic |

For motorway only, 764 ways have at least one of `destination:lanes` or
`destination:ref:lanes`. These tags are sparse, so missing values never reject
an otherwise valid movement. Present, cardinality-valid values are positive
or conflicting evidence that must be reported.

The current national PBF also contains 389 `type=connectivity` relations. They
carry literal lane mappings such as `2:1|3:2` with `from`, `via`, and `to`
members. `parsers/osm_pbf.py` currently discards every non-way object, so these
relations are unavailable to production code. In the first patch, extract a
small relation-backed validation fixture with relation IDs and raw mapping
strings; do not make runtime generation depend on this sparse source and do
not add a production table solely for the test oracle.

## 5. Candidate discovery and anti-skip dominance

### 5.1 Keep junction-box discovery

The 25 m search remains necessary for genuine offset OSM junction modelling.
Keep the STRtree implementation and current broad eligibility classes:

- same non-empty `ref`;
- same non-empty `name`;
- non-link ↔ `_link` transition;
- compatible turn token;
- roundabout transition;
- manual override.

Proximity alone is still insufficient.

### 5.2 Mark candidates dominated by an intermediate segment

For every junction-box candidate `S -> T`:

1. Find all immediate successors of `S`.
2. Starting from those successors, perform a bounded directed search using
   only immediate-successor edges.
3. Stop when:
   - `T` is reached;
   - accumulated original segment length exceeds the junction-box radius plus
     a small numeric tolerance;
   - more than two intermediate segments are traversed; or
   - direction/highway eligibility fails.
4. If `T` is reachable after at least one intermediate segment, reject the
   direct `S -> T` candidate with:

   ```json
   {
     "reason": "intermediate_segment_dominates",
     "via": ["1096129216:..."],
     "skipped_distance_m": 12.42
   }
   ```

This is path dominance, not “exact always beats junction box.” A legitimate
non-exact exit may coexist with an exact mainline continuation when the exit
cannot be reached through that continuation’s immediate-successor chain.

If `S` has no immediate successor because OSM deliberately models a gore or
offset junction with non-coincident nodes, dominance has no path to prove.
Keep the eligible junction-box candidate and mark the dominance result
`not_proven_no_successor`. This conservative fallback is intentional: absence
of an immediate edge must not by itself erase a legitimate exit or entry.

### 5.3 First-frontier rule

Movement selection operates on the first reachable road frontier:

- all immediate successors are on the frontier;
- an undominated junction-box candidate may be added to that frontier;
- a candidate behind another frontier road is never selected directly.

Primary and branch classification happens only after this pruning. Branches are
therefore independent movement choices, but they are no longer independent of
topological adjacency.

### 5.4 Ambiguity behavior

If two undominated candidates in the same class remain tied:

- omit only that movement class;
- emit candidate IDs, evidence, angle, distance, and dominance path;
- do not remove a separate exact continuation or separate unambiguous branch.

The reported fixture must no longer lose `1227426726 -> 1096129216` merely
because roads beyond `1096129216` are also inside 25 m.

## 6. Lane-level tag evidence model

### 6.1 Preserve fields, not only a flattened token set

Parse the applicable directional tag into an ordered tuple:

```python
(
    frozenset({"none"}),
    frozenset({"merge_to_left"}),
    frozenset({"slight_right"}),
)
```

Rules:

- for `fwd`, `turn:lanes:forward` overrides bare `turn:lanes`; bare
  `turn:lanes` is the forward fallback;
- for `bwd`, use `turn:lanes:backward`; do **not** fall back to bare
  `turn:lanes` on a bidirectional way;
- only a one-way `oneway=-1` traversal may use bare `turn:lanes` as its
  backward fallback when `turn:lanes:backward` is absent;
- split fields on `|`, then tokens on `;`;
- normalize whitespace and case;
- validate field count against the travel-direction lane count before the
  value can influence **any** group-level eligibility, candidate kind,
  movement-family selection, or lane allocation;
- a cardinality-invalid value has no topological effect, but its raw value,
  expected count, and actual count remain in diagnostics and the popup;
- retain `none` as an explicit field value rather than dropping it.

Delete or replace the current flattened `_turn_tokens` path. It must consume
the same cardinality-validated parsed object as lane-level allocation, so an
invalid value cannot make an otherwise ineligible road candidate eligible.

### 6.2 Separate movement tokens from cross-section tokens

Normalize tokens into two independent concepts.

Movement intent:

- `through`;
- `left`, `slight_left`, `sharp_left`;
- `right`, `slight_right`, `sharp_right`;
- `reverse`;
- `unmarked`, represented by `none`.

Cross-section intent:

- `merge_to_left`;
- `merge_to_right`.

A merge token does not itself name another road movement. Unless other tokens
say otherwise, that lane belongs to the primary continuation family while also
carrying a pending merge operation.

### 6.3 Allocate lane families before lane counts

For each selected road movement:

1. Determine movement angle and class.
2. Select compatible source lanes:
   - primary continuation: `through`, `none`, and merge-only lanes;
   - tagged branch: lanes containing a compatible turn token;
   - recognized untagged right exit: rightmost unclaimed source block;
   - recognized entry: all ramp lanes.
3. A semicolon field such as `through;slight_right` may participate in both the
   primary and branch movement.
4. Do not let an untagged fallback claim lanes already explicitly assigned to a
   different movement.
5. Determine the compatible target block:
   - use target movement signatures only for an immediate successor with a
     valid lane-count-matched tag;
   - otherwise use the monotonic left/right block rules;
   - an exit target without turn tags accepts all its lanes.

This prevents the four-lane transition’s `slight_right` lanes from being
offered to the mainline movement.

### 6.4 Cross-check lane families with destinations

Parse `destination:lanes` and `destination:ref:lanes` as ordered,
pipe-delimited lane fields with the same direction-selection and cardinality
rules as `turn:lanes`. Preserve semicolon-separated destinations inside one
lane as a set; normalize whitespace but retain the raw spelling for display.

Use the pair `(destination signature, destination-ref signature)` as follows:

1. matching non-empty signatures are strong positive evidence that source
   lanes belong to the same branch family;
2. a signature matching the selected target road `ref` or advertised branch
   destination is preferred over monotonic fallback;
3. conflicting explicit signatures prevent an inferred lane from being
   silently assigned to that movement and produce an unresolved diagnostic;
4. missing or cardinality-invalid destination tags never reject a movement;
5. destination evidence cross-checks allocation only—it cannot create road
   adjacency by itself.

This evidence is evaluated before the untagged rightmost-block fallback and
after road-level movement selection. It is particularly useful when
`turn:lanes` is valid but multiple same-direction branches would otherwise
share the same movement token.

### 6.5 Constrain inferred lateral moves with `change:lanes`

Parse the applicable `change:lanes*` value into ordered per-lane fields and
apply the same cardinality guard. Normalize the standard values (`yes`, `no`,
`not_left`, `not_right`, `only_left`, `only_right`) into allowed lateral
directions in driver-relative lane order.

- An inferred split/join edge that requires a lateral move explicitly
  forbidden by `change:lanes` is unresolved rather than forced.
- A compatible value strengthens, but does not independently create, an edge.
- Mandatory `merge_to_left/right` and `change:lanes` conflicts are surfaced in
  diagnostics instead of silently choosing one tag.
- Missing or invalid `change:lanes` does not constrain allocation.

The first patch does not derive solid/dashed rendering from `change:lanes`.
This layer currently renders lane centerlines, while solid/dashed markings are
properties of lane boundaries. Boundary styling is a separate future feature.

### 6.6 Match lane families across short transition ways

When both adjacent segments have valid turn fields, group contiguous lanes by
normalized movement family:

```text
1227426726:
  primary = [1, 2]
  slight_right = [3]

1096129216:
  primary = [1, 2]
  slight_right = [3, 4]
```

Map each family independently. A change from one source lane to two target
lanes inside the same family is an explicit split candidate. A change from two
source lanes to one target lane inside the same family is an explicit join
candidate.

Do not group non-contiguous lanes into one block. If tags imply a
non-contiguous or crossing allocation, emit an unresolved diagnostic.

## 7. Split, join, and continuation semantics

### 7.1 Base monotonic mapping

Within a selected source/target lane block:

- equal count: pair lanes in order;
- source count smaller: preserve the common block and introduce one-to-many
  edges only when a widening side is supported;
- source count larger: preserve the surviving block and introduce many-to-one
  edges only when merge tokens or recognized entry semantics support it.

### 7.2 Confirm widening side

Evidence, strongest first:

1. compatible `placement*` anchors on both adjacent road sections;
2. cardinality-valid destination signatures plus matching movement-family
   blocks;
3. matching movement-family blocks alone;
4. `merge_to_left/right` on the disappearing side;
5. recognized right-side entry/exit convention;
6. otherwise unresolved.

Parse placement into a structured anchor:

```text
right_of:N | middle_of:N | left_of:N | transition
```

`N` is a positive driver-relative lane number. At a boundary, choose the most
specific applicable value:

1. the coordinate-end tag (`placement:end` at a forward source exit and
   `placement:start` at a forward target entry; swap start/end for backward
   travel);
2. the applicable `placement:forward` / `placement:backward`;
3. bare `placement`.

Compare source and target anchors; do not interpret one isolated value as a
complete widening side. Equal concrete anchors preserve the OSM reference-line
position while lane-family boundaries determine where the added/removed lane
lies. `transition` is recorded but is not side evidence by itself. Invalid or
unsupported values produce diagnostics and fall through to weaker evidence.

In the reported fixture, both adjacent roads are `right_of:1`; the reference
line stays anchored while the `slight_right` family grows from `[3]` to
`[3, 4]`. Lane `4` is therefore added on the right and source lane `3`
connects to both target lanes.

The connection-labelling convention is locked:

- the order-preserving `i -> i` edge remains `continuation`;
- additional one-to-many edges are `split`;
- additional many-to-one edges are `join`.

### 7.3 Derive connection type from the final graph

After all lane pairs for one movement are known, select the maximal monotonic
one-to-one backbone first. Classify individual edges with one locked rule:

- backbone edges are `continuation`;
- an additional edge from a source already represented in the backbone is
  `split`;
- an additional edge to a target already represented in the backbone is
  `join`;
- an edge that would simultaneously be an additional split and join is
  unresolved in the first patch rather than assigned a lossy single label.

Store road-level `entry`, `exit`, `roundabout`, or `continuation` only as
`movement_type`; it never replaces the graph-multiplicity
`connection_type`.

Locked schema-compatible representation:

```json
{
  "connection_type": "join",
  "movement_type": "continuation",
  "turn_lane": "merge_to_left"
}
```

For a one-to-one lane carrying `merge_to_left`, keep:

```json
{
  "connection_type": "continuation",
  "movement_type": "continuation",
  "turn_lane": "merge_to_left",
  "merge_state": "pending"
}
```

Do not infer `join` directly from the presence of a token.

## 8. Connector geometry and shared trimming

### 8.1 Two-pass geometry generation

Refactor connection building into:

1. select road movements;
2. allocate lane pairs;
3. create topology-only connection records without geometry;
4. collect desired trim requests per physical lane endpoint;
5. resolve one trim station per physical lane endpoint;
6. generate every curve from those resolved stations;
7. persist connectors and resolved trim metadata.

This ensures all connectors in a split start at the same source station and all
connectors in a join end at the same target station.

### 8.2 Trim budget for short segments

For every physical lane line of length `L`, both constraints are binding:

```text
minimum_visible_length = min(L, max(2.0 m, L * 0.2))
maximum_total_trim = min(
    L * 0.8,
    max(0, L - minimum_visible_length),
)

resolved_start_trim + resolved_end_trim <= maximum_total_trim
```

This keeps at least 2 m visible on ordinary short segments and at least 20% on
longer segments. A lane shorter than 2 m is not trimmed at all. The 80% cap and
the absolute minimum are not alternatives; the stricter resulting budget wins.

If independent desired trims exceed the budget:

1. scale both proportionally;
2. recompute connector control points from the resolved stations;
3. record `trim_scaled=true` and requested/resolved values;
4. never let the API independently choose a larger trim.

For a very short transition where even the minimum visible section produces
poor curvature, allow a reviewed `collapsed_transition` mode:

- omit the short lane section from rendered geometry;
- join the incoming and outgoing connector curves at one shared interior
  station;
- retain the physical lane row and topology IDs for inspection.

Do not implement collapsed transitions in the first patch unless the scaled
trim fixture still fails visually.

### 8.3 Taper eligibility

Keep the existing near-straight lane-count taper only when:

- the selected pair belongs to adjacent road movements;
- the movement angle is within the configured threshold;
- the original road endpoints are adjacent;
- lateral shift is consistent with the lane-count/family change.

Do not apply tapering to a junction-box shortcut. Once path dominance is
implemented, the reported 12.42 m shortcut will not exist.

### 8.4 Viewport-independent rendering

The API must continue to discover trim metadata for every returned lane,
including connectors whose curve falls just outside the requested bbox.
Rendered lane geometry must not change when the same lane is requested through
a slightly different viewport.

Longer term, consider storing resolved `trim_start_m` and `trim_end_m` on the
lane row. Do not add columns until query profiling shows the current
connection-based lookup is a problem.

## 9. Diagnostics and counters

Extend unresolved output and connector raw data with:

- `adjacency_evidence`;
- original endpoint distance;
- offset endpoint distance;
- selected/rejected movement family;
- `suppressed_reason`;
- `dominated_via`;
- source/target lane-family fields;
- applicable placement anchors and evidence rank;
- applicable destination/destination-ref lane signatures;
- applicable change-lane constraint;
- expected/actual tag cardinality for every rejected lane tag;
- requested and resolved trims;
- `trim_scaled`;
- `movement_type`;
- `merge_state`.

Add counters:

- `node_exact_movements`;
- `endpoint_exact_movements`;
- `junction_box_movements`;
- `junction_box_suppressed_intermediate`;
- `junction_box_dominance_not_proven_no_successor`;
- `primary_ambiguities`;
- `invalid_turn_lane_cardinality`;
- `invalid_destination_lane_cardinality`;
- `invalid_change_lane_cardinality`;
- `placement_supported_widenings`;
- `destination_supported_allocations`;
- `change_lane_conflicts`;
- `lane_family_splits`;
- `lane_family_joins`;
- `pending_merge_continuations`;
- `unresolved_lane_family_mismatch`;
- `trim_budget_scaled`;
- `collapsed_short_transitions`.

The rebuild CLI should print these counters for `--bbox`, `--roads`, and
`--all`. `--unresolved-only` should include suppressed shortcuts so a reviewer
can see why an apparently nearby exit was intentionally not connected.

## 10. Implementation steps

### Phase 0: backfill source topology and establish the baseline

Do this before changing movement-selection behavior:

1. Record the validated pre-backfill state from §2.1 and export the focused
   fixture’s current lane/connection rows for comparison.
2. Rebuild the app image so the PBF parser that persists `node_refs` is the
   code executing the ingest.
3. Force a controlled ingest from the cached
   `data/netherlands-latest.osm.pbf`. Do not rely on the scheduled downloader:
   its conditional request may return “not modified” and skip parsing. Add or
   use a targeted local-PBF ingest entry point that still uses
   `OsmRoadIngester` transaction/pruning behavior.
4. Assert:
   - `osm_road.node_refs IS NULL` is zero for successfully parsed roads;
   - regenerated lane segment IDs use real endpoint node IDs rather than
     `<osm_id>:0:0`;
   - road and extract-membership counts remain plausible;
   - the reported four source roads still exist with unchanged raw tags.
5. Run the current, pre-improvement lane algorithm nationally with
   `refresh_osm_lane_lines --all` and persist its counters, elapsed time,
   unresolved diagnostics summary, connection-type counts, and crossing
   benchmark as the **Phase 0 baseline**.
6. Do not add or tune committed manual overrides before this stable-ID
   baseline exists.

The ingest itself may regenerate national lane rows; the explicit `--all` run
is still required so the baseline uses one documented CLI path and captures
the same counters used after implementation.

### Phase 1: freeze regressions and tag oracles

1. Add a synthetic fixture using the exact road IDs, lane counts, tags, and
   `placement` values, plus approximate RD geometry from the reported A22
   junction.
2. Add failing regression assertions for the desired graph: the immediate
   transition exists and both direct upstream-to-exit shortcuts are absent.
3. Add a separate legitimate junction-box exit fixture where:
   - an exact mainline continuation exists;
   - the non-exact exit is not reachable through that continuation;
   - the exit must remain selected.
4. Add a short-segment trim fixture with trim requests at both ends.
5. Extract a small, committed validation fixture from selected
   `type=connectivity` relations in the cached PBF. Store relation ID,
   `connectivity` value, and `from`/`via`/`to` member IDs. Select relations
   whose from/to ways are retained Driving Roads and whose syntax the fixture
   parser explicitly supports, plus one explicit unsupported-syntax negative
   fixture. Use them as expected graph data in tests, not as a production
   dependency.

### Phase 2: original-endpoint adjacency

1. Add `source_start` / `source_end` to new lane rows in
   `parsers/osm_lane_lines.py`.
2. Extend `LaneTraversal` and `MovementCandidate` with adjacency evidence.
3. Build the immediate-successor graph from node IDs or 0.5 m original endpoint
   equality.
4. Prefer matching non-zero node IDs, retain coordinate adjacency as a normal
   fallback, and diagnose conflicting node/coordinate evidence.
5. Add counters comparing node and coordinate adjacency.
6. Unit-test the coordinate fallback with zero-node fixtures, but run live
   bbox validation against the Phase 0 node-backed database.

### Phase 3: dominance pruning

1. Implement bounded reachability through immediate successors.
2. Mark and remove dominated junction-box candidates before
   `choose_movement_set`.
3. Make movement ambiguity operate only on the first frontier.
4. Confirm `1227426726 -> 1096129216` is selected and both direct
   upstream-to-exit edges are suppressed.
5. Confirm the legitimate non-exact exit fixture still passes.

### Phase 4: lane-evidence parsing and family allocation

1. Replace flattened turn-token use with one cardinality-validated ordered
   parser used by eligibility and allocation.
2. Separate movement and merge semantics.
3. Parse `placement*` anchors and use them as the strongest widening-side
   evidence.
4. Parse `destination:lanes*` and `destination:ref:lanes*` as allocation
   cross-checks.
5. Parse `change:lanes*` as a constraint on inferred lateral edges.
6. Allocate source/target blocks per selected movement.
7. Add one-to-many split and many-to-one join graph analysis.
8. Derive connection types from actual graph multiplicity using the locked
   convention in §7.2.
9. Preserve raw/applicable lane tags, `turn_lane`, destination signatures,
   movement type, and pending merge state in API properties and popups.

### Phase 5: shared trim resolution

1. Split topology selection from connector geometry generation.
2. Collect and resolve endpoint trims globally within the affected graph.
3. Enforce the per-lane total trim budget.
4. Generate curves using only resolved stations.
5. Keep API trimming viewport-independent.

### Phase 6: national comparison and rollout

1. Run a full Lanes rebuild on the unchanged Phase 0 PBF/source-road snapshot.
2. Compare against the named Phase 0 baseline, not against the 694-row focused
   test population.
3. Investigate movement/count changes using the gates in §12.
4. Re-run the connectivity-relation validation corpus and focused bboxes.
5. Only after topology and geometry acceptance, update the normal PBF ingest
   path to use the completed algorithm and perform a Docker smoke ingest.

## 11. Test plan

### Candidate graph tests

1. Exact node adjacency creates an immediate-successor edge.
2. Coincident original endpoints create the same edge when node IDs are zero.
3. Offset lane endpoints are not used for adjacency.
4. A junction-box candidate reachable through one short immediate successor is
   suppressed.
5. The same suppression works through two short successors.
6. Search stops beyond the distance/depth bound.
7. A legitimate non-exact branch not reachable through the successor remains.
8. With no immediate successor, an eligible offset-gore junction-box branch
   remains and reports `not_proven_no_successor`.
9. A nearby opposite carriageway remains rejected.
10. `oneway=-1` builds adjacency at the correct travel endpoints.
11. `direction=both` uses the correct endpoint for each traversal.

### Lane-evidence and allocation tests

1. `none|merge_to_left|slight_right` produces ordered lane fields.
2. `through;slight_right` may feed both movements.
3. Merge-only lanes stay in the primary movement family.
4. `none|merge_to_left|slight_right` to
   `none|merge_to_left|slight_right|slight_right` maps the two primary lanes
   one-to-one and splits the right family without crossings.
5. The four-lane transition maps lanes 1–2 to mainline lanes 1–2.
6. The same transition maps lanes 3–4 to exit lanes 1–2.
7. No upstream lane connects directly to the exit.
8. A merge token on a one-to-one edge remains `continuation`.
9. A real `3 -> 2` `none|none|merge_to_left` change produces
   `1→1`, `2→2`, `3→2`, with only `3→2` labelled `join`.
10. `merge_to_right|none|none` preserves the correct surviving order.
11. A bare `turn:lanes` applies to `fwd` on a bidirectional way but not `bwd`.
12. A bare `turn:lanes` applies to backward travel for `oneway=-1` only when
    the backward-specific tag is absent.
13. Invalid turn-field counts cannot affect group eligibility,
    `_candidate_kind`, family selection, or allocation and emit diagnostics.
14. Matching `right_of:1` placement anchors identify the added right lane in
    the A22 `1 -> 2` movement family.
15. Endpoint-specific and directional placement values follow the precedence
    in §7.2; `transition` alone does not choose a side.
16. Matching `destination:lanes` signatures keep like-destination source lanes
    in one branch family.
17. `destination:ref:lanes` distinguishes two branches with the same turn
    token; a conflicting valid destination yields an unresolved allocation.
18. Cardinality-invalid destination tags do not affect allocation.
19. `change:lanes=not_right` blocks an inferred rightward lateral edge but
    does not delete a one-to-one continuation.
20. A `change:lanes`/mandatory merge conflict is diagnosed.
21. Resulting lane assignments are monotonic in driver-left-to-right order.

### Connectivity-relation oracle tests

1. Fixture extraction retains relation ID, raw `connectivity` mapping, and
   exact `from`/`via`/`to` member IDs.
2. Simple mappings such as `1:1|2:2` match the generated lane graph.
3. A non-identity mapping such as `2:1|3:2` validates driver-relative lane
   numbering; separately selected relation fixtures cover supported
   multiplicity syntax.
4. Unsupported optional-lane syntax remains in the fixture and is reported as
   unsupported rather than misparsed.
5. Production connection generation produces identical output when the
   relation fixture is absent, proving the oracle is not a runtime dependency.

### Geometry tests

1. Every fan-out connector shares exactly one resolved source station.
2. Every join connector shares exactly one resolved target station.
3. Start/end trims never exceed the line-length budget.
4. A 12 m transition with requests at both ends retains the configured minimum
   visible length.
5. Requested and resolved trim values are stored consistently.
6. Connector tangents follow the lane at resolved stations.
7. No generated curve doubles back for the A22 fixture.
8. Non-intersection assertions in RD confirm the A22 connector set does not
   cross.
9. Requesting overlapping bboxes returns identical coordinates for the shared
   portion of every lane.

### Persistence/API/frontend tests

1. New lane rows contain original segment endpoints.
2. Connector properties expose evidence, movement type, turn lane, and
   split/join semantics.
3. Popup continues to show the full pipe-delimited tag and clicked lane value.
4. Popup/API diagnostics show the applicable placement anchor,
   destination/destination-ref lane value, and change-lane value when present.
5. A bbox rebuild restores all affected cross-boundary connectors.
6. No query or import references `OsmRoadLane`.
7. Existing per-kind API caps and truncation metadata remain correct.

## 12. Acceptance procedure

### Data prerequisite

Acceptance is run only after Phase 0 assertions pass. Record the PBF file
timestamp/hash, source-road count, non-null node-ref count, and Phase 0
baseline path alongside every result. A comparison across different PBF
snapshots is informational, not a release gate.

### Focused rebuild

After implementation:

```bash
python -m ndwinfo.refresh_osm_lane_lines \
  --bbox 4.655832,52.476255,4.656677,52.476823
```

Inspect `/api/osm/lane-lines` for the same bbox.

Required assertions:

- `1227426726 -> 1096129216` exists;
- `1227426726 -> 1096129217` does not exist;
- `1227426726 -> 1096129213` does not exist;
- `1096129216 -> 1096129213` exists for the primary lane block;
- `1096129216 -> 1096129217` exists for the right-turn lane block;
- the short transition has a valid trim budget;
- there are no crossing connector geometries in RD;
- every automatic connection explains its adjacency evidence.

### Visual review

1. Enable **Lanes**.
2. Disable **Lane Detail** so only the independent layer is judged.
3. Open the regression bbox at zoom 19–21.
4. Confirm:
   - no loops;
   - no long upstream-to-exit shortcuts;
   - no unexplained butt-ended primary lanes;
   - right-turn lanes enter the exit in order;
   - through lanes remain on the mainline;
   - splits and joins taper smoothly.
5. Click every lane family and verify `turn:lanes` and `turn_lane` in the popup.

Also recheck:

```text
4.652271,52.468226,4.652426,52.468330
4.658651,52.478713,4.658806,52.478817
```

Those earlier 2↔3 transitions must retain their smooth, non-looping output.

### Full regression

Run:

```bash
pytest -q
node --test 'web/tests/*.test.mjs'
ruff check <changed Python files and tests>
git diff --check
```

Then run a full Lanes rebuild and compare counters against the Phase 0 build.
The comparison target is the named Phase 0 national baseline created from the
same PBF, not the 694-row test-region population. Treat these as review gates:

- exits or entries drop by more than `max(50 movements, 5% of baseline)`;
- unresolved movements increase by more than
  `max(100 movements, 10% of baseline)`;
- total selected road movements change by more than 10%;
- zero `junction_box_suppressed_intermediate` nationally;
- node/coordinate evidence conflicts above zero;
- coordinate-only adjacency residue changes by more than 10% from the Phase 0
  node-backed baseline without an explained source-data reason;
- any connector crossing count above the explicitly reviewed intersection
  baseline.

Crossing counts are a diagnostic gate, not a blanket assertion that every
geometric crossing is invalid. Maintain an explicit allowlist/fixture set for
real at-grade intersections; new unreviewed crossings fail acceptance.

## 13. Risks and mitigations

### Risk: suppressing legitimate offset exits

Mitigation: suppress only when the target is reachable through the immediate
successor graph. Do not globally disable junction-box branches when an exact
continuation exists.

### Risk: target `turn:lanes` describes a later junction

Resolution: target `turn:lanes` is not used to allocate lanes at the target
way's entry because it describes that way's later junction. Allocate explicit
source-side branches first, prefer directional tokens over an overlapping
`through` range, reserve lanes carrying only that directional token, and map
the remaining source lanes monotonically onto the primary continuation.
Combined source tokens such as `through;slight_right` deliberately remain
eligible for both movements.

### Risk: one lane widening into two is guessed incorrectly

Mitigation: use compatible placement anchors before inferred movement-family
evidence, then destination signatures and merge/branch conventions. Emit an
unresolved new lane when the widening side remains unknown.

### Risk: sparse or contradictory lane tags overrule sound topology

Mitigation: road adjacency is decided before lane tags. Apply one shared
cardinality guard to eligibility and allocation, use destination/change tags
only as cross-checks or constraints, and report explicit conflicts rather than
forcing an edge.

### Risk: placement syntax is misinterpreted

Mitigation: parse only the documented `right_of:N`, `middle_of:N`,
`left_of:N`, and `transition` forms; require compatible anchors on both sides
before treating placement as decisive; cover start/end and directional
precedence with tests.

### Risk: short segments disappear after trimming

Mitigation: resolve both endpoint trims together with a minimum visible-length
budget. Keep collapsed-transition behavior behind an explicit later decision.

### Risk: topology tuning is performed against unstable pre-backfill IDs

Mitigation: make node-ref PBF reingest Phase 0, establish the national baseline
afterward, and forbid new committed manual overrides against `<osm_id>:0:0`
IDs.

### Risk: connectivity relations expand the first patch excessively

Mitigation: use selected relation-backed fixtures as an offline validation
oracle only. Runtime relation ingest, persistence, and broad syntax support are
separate follow-up work.

### Risk: performance of dominance searches

Mitigation: build adjacency maps once, search only from each source’s immediate
successors, cap depth at two and length near 25 m, and cache results per
`(source_group, target_group)`.

## 14. Definition of done

This improvement is complete when:

- the node-ref backfill and same-PBF Phase 0 national baseline are recorded;
- the reported A22 fixture contains no shortcut around `1096129216`;
- lane movement families match the OSM turn fields;
- placement anchors settle the reported widening side and valid destination /
  change-lane evidence is honored without creating adjacency;
- split/join types reflect actual graph multiplicity;
- no connector in the fixture crosses or doubles back;
- short-segment trims respect a shared length budget;
- legitimate non-exact junction-box exits remain covered by tests;
- the two earlier lane-count transition bboxes remain visually correct;
- focused and full test suites pass;
- selected connectivity-relation oracle fixtures match generated mappings;
- national counter changes pass or receive explicit review against the Phase 0
  baseline;
- Docker rebuild and local API smoke tests pass;
- diagnostics make every accepted, suppressed, or unresolved movement
  explainable without reading geometry by eye.

## 15. Validation record for this revision

The following checks were run before accepting the review edits:

- Database counts confirm `167,192/167,192` null road `node_refs`,
  `694/694` zero-node lane segment IDs, and `442` current connections with no
  exact-confidence row.
- Direct connection queries confirm both 12.42 m shortcuts and the
  `1096129216` lane-2 to `1096129213` lane-2 false `join`.
- Exact WKB endpoint grouping confirms `26,261/27,587` motorway and
  motorway-link forward way ends have an identical stored successor start
  (95.2%).
- Raw-tag queries confirm all fixture tags and placement values. They also
  confirm the national coverage values in §4.4. The review’s “4,282 motorway
  ways” figure excludes motorway links; links add another 3,782 bare
  `placement` values.
- Source inspection confirms lane raw data currently stores start/end node IDs,
  group eligibility consumes flattened turn tokens without a cardinality
  guard, backward per-lane parsing currently falls back too broadly to bare
  `turn:lanes`, and connection type can be forced to `join` by a merge token.
- A direct scan of the cached national PBF confirms 389
  `type=connectivity` relations and representative mappings including
  `1:1|2:2` and `2:1|3:2`. Source inspection confirms the production PBF
  parser currently ignores relations.
- The cached PBF is approximately 1.3 GB and the last successful national
  ingest recorded 167,169 upserts in about 12.5 minutes.

## 16. Implementation record

The plan was implemented on 2026-07-29. The completed patch:

- reingests the cached national PBF through a source-only path so
  `osm_road.node_refs` is populated without attempting an in-memory national
  lane build;
- splits lanes at real OSM topology nodes and retains original, unoffset
  segment endpoints;
- selects node/coordinate-exact immediate successors before junction-box
  candidates and suppresses shortcuts through one or two intermediate ways;
- parses cardinality-valid directional `turn:lanes`, `destination:lanes`,
  `destination:ref:lanes`, `change:lanes`, and `placement` evidence;
- derives `continuation`, `split`, and `join` from final graph multiplicity;
- gives a genuine link-entry movement ownership of an added target lane,
  suppressing a redundant inferred mainline split into that same lane;
- jointly allocates exact near-straight predecessor blocks when their lane
  counts sum to the target count, using measured lateral order to map
  contiguous target lanes and a shared transition runway to avoid crossings;
- aligns concrete one-way `placement=left_of:N|middle_of:N|right_of:N`
  cross-sections to the OSM reference line;
- resolves endpoint trims once across the complete graph and generates curves
  only after topology and trim stations are fixed, using shared connector
  handles at each physical endpoint; a `_link`/non-link handover may use up to
  25 m and 80% of its link-side lane for the transition while retaining the
  global 20% visible-length budget. An exact handover at no more than 15 degrees
  may double its gap-based runway, capped at 50 m, to keep visually straight
  merges from bending close to the OSM node. Exact node/endpoint link handovers
  up to 45 degrees may be trimmed; junction-box guesses remain limited to
  30 degrees, as does the separate threshold for literal straight connectors.
  Normal count changes remain limited to 15 m and 40% per side;
- resolves simple `placement=transition` lane geometry in a topology-aware
  second pass: explicit endpoint placement wins, otherwise connected adjacent
  lane endpoints are inherited, lane spacing is checked at every curve sample,
  and connectors are rebuilt from the resolved geometry;
- exposes full and clicked-lane tag values through the API and popup; and
- rebuilds the national layer in bounded geographic tiles.

Repeat rebuilds use indexes on both lane foreign keys of
`osm_lane_connection`. Without those indexes, deleting a tile's centerlines
requires repeated scans of the national connector table and can leave an
apparently stalled transaction.

The direct pre-change national baseline requested in Phase 0 could not be
created after node-reference backfill without rerunning superseded code. The
available pre-backfill 694-row focused population is retained only as a
historical observation, not used as a national release gate. The source-only
reingest populated `167,169` Netherlands-extract road rows; the remaining
`23` null rows belong to the older `noord-holland` extract.

The reported A22 fixture now has exactly these road movements:

```text
1227426726 -> 1096129216
1096129216 -> 1096129213
1096129216 -> 1096129217
```

Both forbidden upstream shortcuts are absent. Its lane allocation is
`1→1`, `2→2`, `3→3`, `3→4` on the widening, followed by `1→1`, `2→2`
to the mainline and `3→1`, `4→2` to the exit. An RD geometry assertion
confirms zero connector crossings in this fixture.

The adjacent `1096339332` entry regression is also covered explicitly:
the link supplies lane 3 of `411074875`, the mainline continues into lane 2,
and no redundant mainline-to-lane-3 split remains. The two connectors share
the target endpoint's trim and handle station and do not cross. Browser
validation showed that steering the entry tangent sideways avoided the
crossing but left a visible dogleg. The final geometry instead trims both
entry sides to 15 m and uses the ordinary tangent-preserving cubic; its sampled
source and target angle discontinuities are below 5 degrees.

The `1096129211` A22 entry now implements the Placement proposal's consumer
rule for a simple `placement=transition` way. A preliminary topology pass maps
its lanes to `1096129210` lanes 3 and 4, then inherits those target endpoints
and the predecessor endpoints before rebuilding connectors. The two 32 m
transition lanes move laterally by `6.95 m` and `7.09 m`; their measured minimum
spacing is `3.492 m`, neither crosses, and the former `15 m`/`7.31 m` corrective
connector trims reduce to symmetric `5 m` trims with no lateral correction at
the shared endpoint. Browser validation against PDOK aerial imagery shows the
shift distributed across the transition rather than concentrated in a junction
dogleg.

The completed national topology rebuild contains `299,403` lane lines and
`227,864` connections across `167,192` source roads. All `167,169`
Netherlands-extract roads have real node references; the `23` remaining null
rows and `42` legacy `:0:0` lane IDs belong only to the older Noord-Holland
extract. The final focused A22 rebuild has zero connector crossings, zero
forbidden upstream shortcuts, and zero redundant mainline edges into the
entry-owned lane.

Final verification passed `219` backend tests and `66` frontend tests. The
Docker build was inspected in the browser at the A22 fixture and both earlier
reported bboxes; all three render without loops or crossings. Clicking
`ll:411074875:4086469249:2286612624:fwd:3` shows both
`turn:lanes=none|none|merge_to_left` and `turn_lane=merge_to_left`.
