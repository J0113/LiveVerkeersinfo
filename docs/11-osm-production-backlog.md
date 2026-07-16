# OSM-first production backlog

Status: Phase A/B driving-core foundation implemented; remaining items below
continue as the production backlog following the successful
[OSM × live speed POC](10-osm-speed-poc.md). This document supersedes the
road-identification and live-speed-association parts of the historical
[implementation plan](../plan.md). It does not replace the existing NDW ingest,
API, or non-driving product scope.

Implemented through the lane-topology iteration: atomic local PBF import,
an inactive shadow-import mode, directed intersection-split
segments, stable internal IDs, GiST indexes, persistent fail-closed NDW speed
bindings, production bbox/corridor APIs, diagnostics, a bounded eight-fix
client matcher with hysteresis and topology continuity, a deterministic
browser matcher core, synthetic replay fixtures, and a bounded connected-path
API that separates common ahead from unresolved branches. A compact directional
OSM lane schema now powers one shared schematic renderer on the map and in the
HUD. Exact user-lane identification remains fail-closed. NDW lane ordering is
now verified against official documentation and matches the OSM directional
left-to-right contract. The first validated
regional import used Noord-Holland and produced 54,829 directed segments.
Hand-reviewed recorded drives, national accuracy benchmarking, segment
lineage, situation binding and calibrated advanced estimation remain explicit
backlog work; they are not silently claimed by the MVP.

The 2026-07-16 [driver validation audit](16-driver-validation-audit.md) inserted
an immediate control gate before situation binding. Its P0 canonical/legacy HUD
separation and scenario harness take precedence over the older phase order.

## Outcome and architectural boundary

The target is a lightweight driving product that only shows information when it
can be associated with the physical carriageway and travel direction of the
user with sufficient confidence.

```text
OSM Netherlands extract + replication diffs
                    ↓
        normalized directed road graph
                    ↓
     stable internal segment identities
                    ↓
NDW live feeds → persisted source-to-segment bindings
                    ↓
     segment observations + confidence/staleness
                    ↓
       corridor API / spatial candidate query
                    ↓
 GPS history → stateful client map match → driving UI
```

The split between sources is intentional:

| Responsibility | Primary source | Decision |
|---|---|---|
| Physical road geometry and topology | OpenStreetMap | Canonical road layer |
| Direction, oneway, road class and access | OpenStreetMap | Normalize into directed edges |
| Static maxspeed and lane metadata | OpenStreetMap | Use when present; retain provenance and uncertainty |
| Current speed, flow and travel time | NDW | Keep as live observation source |
| Matrix signals and DRIPs | NDW | Keep; bind to directed segments/lane scope |
| Incidents, closures and roadworks | NDW | Keep; bind to directed segments and validity window |
| NWB road rendering | NWB | Candidate for retirement after shadow validation |
| WEGGEG runtime lane overlay | WEGGEG | Validation/fallback only until an explicit retirement gate passes |

OSM does not replace live NDW data. Public Overpass is only a POC dependency and
must not be used in the production request path.

## Correctness invariants

These rules apply to every phase and are release blockers:

1. An explicit road-reference conflict is never auto-corrected by proximity.
2. An explicit carriageway conflict is never accepted.
3. Opposite-direction candidates are rejected when a reliable heading exists.
4. Ambiguous matches remain unbound and do not colour a road or enter the HUD.
5. Zero km/h is a valid observation; missing and stale are separate states.
6. Every value shown to the driver has source, timestamp, derivation method and
   confidence available to the application.
7. OSM absence is unknown, not proof that a road property does not exist.
8. A segment ID exposed by the API is an internal ID, never a raw OSM way ID.

## Backlog conventions

- Priority: P0 = correctness blocker, P1 = required for a useful product,
  P2 = material improvement, P3 = optional optimization.
- Complexity: S = isolated change, M = multiple modules/schema change,
  L = new subsystem or migration.
- Each phase has an exit gate. Later phases may be prototyped early, but do not
  replace an unmet correctness gate.
- Performance improvements require measurements before and after; estimates are
  not recorded as measured gains.

## Phase V — Immediate driver validation and control gate

Goal: preserve the strict direction/carriageway safety boundary while making
the product readable, stable and light enough for real mobile driving. This is
the next iteration; Phase C situation work does not start until the P0 items and
the applicable exit gates below pass.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-V00 | Remove legacy nearest-point MSI/DRIP fallback whenever the canonical driving pipeline exists, including acquire/switch/uncertain states | P0 | `web/road-match.js`, `web/hud.js`, JS tests | S | C04/C05 | Unaccepted canonical match returns authoritative empty signs; opposite/parallel legacy signs are never selected |
| OSM-V01 | Add deterministic multi-scenario simulation profiles for A4, N203 two-way and A1/A10 main/link/parallel roads | P0 | `web/simulation.js`, `tests/js/`, matching fixtures | M | B02-B07 | Every profile uses the production GPS handler and includes normal, drift, transient gap and hard-conflict variants |
| OSM-V02 | Execute browser matcher output against hand-reviewed recorded-drive ground truth | P0 | fixtures, benchmark scripts/docs | L | V01 | Zero accepted wrong direction/carriageway; accepted driving time, information availability and HUD churn reported separately by scenario |
| OSM-V03 | Define a driver presentation contract that separates primary action information from diagnostics | P1 | `web/hud.js`, `web/road-match.js`, `web/ui.js` | M | V00 | Primary glance contains at most road/human direction, speed, limit and one urgent ahead instruction; no enums, scores or provenance jargon |
| OSM-V04 | Rebuild responsive HUD sizing and priority for portrait and compact landscape | P1 | `web/style.css`, lane/HUD renderers | M | V03 | 320/390/430 portrait and 667x375/844x390 landscape have no overlap/clipping; one fact tile uses full available width |
| OSM-V05 | Enforce driver readability and touch budgets | P1 | `web/style.css`, visual regression tests | S | V04 | Primary values >=16 px, support text >=12 px, touch targets >=44x44 px; five-lane MSI remains legible |
| OSM-V06 | Add bounded continuity states: confirmed, transient uncertainty and hard conflict | P1 | matcher/HUD state, replay tests | M | V01, V03 | Last confirmed road/speed may hold <=3 s or <=50 m only for transient gaps; no new facts during hold; hard conflicts clear immediately |
| OSM-V07 | Require explicit `Start rijden`, isolate driving layers from saved diagnostic layers and reset simulations cleanly | P1 | `web/gps.js`, `web/config.js`, `web/simulation.js` | S | V03 | No geolocation request on page load; driving mode cannot restore dense diagnostic point layers |
| OSM-V08 | Bound `/traffic/speed/map` before materialization/Python merge and remove avoidable request-path WEGGEG work | P0 | traffic router, SQL indexes/query tests | M | — | A 500-point response does not materialize tens of thousands of grouped rows; before/after plan, latency, rows and bytes are recorded |
| OSM-V09 | Select canonical ahead facts by bounded path distance instead of four segment objects | P1 | `web/road-match.js`, path contract tests | S | B06 | Facts inside configured metres remain visible across short OSM splits; unresolved branch facts remain hidden |
| OSM-V10 | Base safe speed propagation continuity on directed topology and compatible road/carriageway identity, not raw OSM `forward/backward` labels across ways | P1 | `src/ndwinfo/osm/speed_model.py`, fixtures | M | C02 | One-to-one N203-style chains can propagate when safe; opposite/disconnected paths still fail closed |
| OSM-V11 | Add automated mobile visual regression plus API/browser performance budgets | P1 | test tooling, `docs/09-performance.md`, CI | M | V01, V04, V08 | Scenario/viewports fail on overlap, clipping, size or budget regression; browser main-thread and update duration are measured |
| OSM-V12 | Present MSI with `source_only` lane scope without inventing lateral lane order | P1 | roads API, `web/road-match.js`, HUD tests | S | C04 | Equal aspects may be carriageway-wide; differing unverified aspects show one uncertainty warning and never `Rijstrook ?` |
| OSM-V13 | Normalize/evaluate supported conditional OSM maxspeed and qualify unsupported conditions | P1 | OSM tags/speed limit model, HUD | M | B08 | HUD says `Max` only for a currently valid limit; otherwise uses `Basislimiet`, `conditioneel` or unknown with provenance |
| OSM-V14 | Reduce realtime ingest write amplification and isolate live-feed resource budgets | P1 | measurement ingest, poller, DB metrics | M | — | Changed rows are measurable and avoidable identical updates are skipped; parse/merge/commit time, writes and feed latency have budgets |

Enforced execution order inside Phase V:

```text
V00 canonical sign safety
  → V01/V02 complete scenario and recorded-drive harness
  → V08 bounded speed endpoint
  → V03-V07 mobile driver contract and lifecycle
  → V09/V10/V12/V13 useful direction-safe coverage
  → V11/V14 performance and regression gates
```

### Reliability versus usability operating policy

Correctness is a constraint, not a weighted score that can be traded away:

- zero tolerance for a displayed known opposite-direction or wrong-carriageway
  fact;
- maximize useful availability within that constraint;
- retain only the last confirmed road and speed during a short transport/GPS
  gap, never a newly discovered fact;
- clear immediately on opposite heading, fork ambiguity, topology conflict or
  stale data;
- calibrate the proposed three-second/50-metre transient hold on recorded
  drives before treating it as final.

### Phase V exit gate

- OSM-V00 has a regression test and no legacy traffic fact can enter an
  unconfirmed canonical driving state.
- A4, N203 and A1/A10 deterministic replays pass the safety benchmark.
- Recorded-drive results report safety and availability separately; no single
  aggregate score hides a wrong-direction match.
- Mobile presentation passes all required portrait/landscape viewports.
- `/traffic/speed`, corridor and path budgets have repeatable baselines;
  browser main-thread/update budgets are instrumented rather than inferred.

## Phase 0 — Freeze the POC baseline and build a benchmark

Goal: turn the current visual result into a reproducible accuracy baseline
before changing the matching rules.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-000 | Preserve representative POC fixtures for motorway, dual carriageway, ramp, parallel lane and urban two-way road | P0 | `tests/fixtures/osm/` (new), `tests/test_osm_poc.py` | M | — | Tests run without Overpass and contain expected accepted, ambiguous and rejected bindings |
| OSM-001 | Define a hand-reviewed Dutch benchmark set covering separated A-roads, parallel carriageways, junctions, tunnels/bridges and ramps | P0 | `tests/fixtures/matching_cases.geojson` (new), `docs/` | M | OSM-000 | Every case records expected road, direction, allowed candidates and reason |
| OSM-002 | Add benchmark metrics by scenario instead of only an aggregate match rate | P0 | `tests/test_osm_matching_benchmark.py` (new) | M | OSM-001 | Reports false-carriageway matches, false-direction matches, accepted, ambiguous and unmatched counts |
| OSM-003 | Record POC latency, payload size, road count and browser render cost on fixed viewports | P1 | `scripts/benchmark_osm_poc.py` (new), `docs/09-performance.md` | S | OSM-000 | Re-runnable baseline; results labelled with hardware, date and dataset version |

### Phase 0 exit gate

- Zero known opposite-carriageway matches in the hand-reviewed benchmark.
- Every matcher change can be compared against the frozen fixtures.
- Current performance is measured rather than inferred.

## Phase A — Correctness: local OSM graph and persistent bindings

Goal: remove Overpass from runtime, create real directed graph segments and
associate live source locations once, fail-closed.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-A01 | Add a configurable Netherlands `.osm.pbf` snapshot source, checksum and import-run metadata | P0 | `src/ndwinfo/config.py`, `src/ndwinfo/feeds.py`, `src/ndwinfo/ingest/osm.py` (new) | M | Phase 0 | Import is repeatable and a failed import leaves the active graph unchanged |
| OSM-A02 | Add canonical graph schema: import run, node, directed segment, lineage and source binding | P0 | `src/ndwinfo/models.py`, Alembic migration | L | OSM-A01 | PostGIS contains directed, indexed segments with internal IDs and provenance |
| OSM-A03 | Split OSM ways at graph intersections and material road-attribute changes | P0 | `src/ndwinfo/osm/graph.py` (new), `src/ndwinfo/osm/tags.py` (new) | L | OSM-A02 | Each segment has from/to node, legal direction and homogeneous matching attributes |
| OSM-A04 | Normalize `oneway`, roundabout, `ref`, road class, access, lanes, maxspeed, bridge/tunnel/layer and directional tags | P0 | `src/ndwinfo/osm/tags.py` (new); extract logic from `src/ndwinfo/osm_poc.py` | M | OSM-A03 | Fixture coverage includes `oneway=-1`, bidirectional ways and directional tag variants |
| OSM-A05 | Introduce stable `internal_segment_id` plus split/merge lineage across OSM versions | P0 | `src/ndwinfo/osm/lineage.py` (new), models/migration | L | OSM-A03 | Unchanged segments retain identity; split/merge successors are queryable and audited |
| OSM-A06 | Build GiST geometry indexes plus indexes on road reference, source way and graph endpoints | P1 | models/migration | S | OSM-A02 | Query plans use indexes for viewport/corridor and binding candidate queries |
| OSM-A07 | Move NDW measurement-site association out of the request path into a persisted binding job | P0 | `src/ndwinfo/matching/source_binding.py` (new), `src/ndwinfo/ingest/measurement.py`; extract `link_measurements_to_roads` | L | OSM-A03, OSM-A04 | Binding stores status, distance, heading delta, score, margin, algorithm version and graph version |
| OSM-A08 | Rebind only affected source locations after graph changes and all locations after matcher-version changes | P1 | `src/ndwinfo/matching/source_binding.py`, `src/ndwinfo/poller.py` | M | OSM-A05, OSM-A07 | Incremental run is idempotent and leaves no binding to an inactive segment |
| OSM-A09 | Add production road API backed only by PostGIS, leaving `/api/poc/osm/roads` intact for comparison | P0 | `src/ndwinfo/api/routers/roads.py` (new), `src/ndwinfo/api/main.py` | M | OSM-A06, OSM-A07 | API returns directed internal segments and accepted live bindings without external calls |
| OSM-A10 | Expose ambiguous/unmatched bindings only through diagnostics, never through the driving speed layer | P0 | `src/ndwinfo/api/routers/roads.py`, `web/osm-poc.js`, `web/config.js` | S | OSM-A09 | Normal UI cannot colour a road using an ambiguous binding |
| OSM-A11 | Add licence attribution, import age and graph-version observability | P1 | `web/index.html`, `web/ui.js`, feed/status API | S | OSM-A01 | OSM attribution is visible and stale/import-failure state is inspectable |

### Canonical minimum schema

The exact column names may be refined during the migration, but the model must
represent these concepts:

```text
osm_import_run
  id, source_timestamp, checksum, imported_at, status, active

road_node
  internal_node_id, osm_node_id, geom

road_segment
  internal_segment_id, import_run_id, osm_way_id, osm_version,
  from_node_id, to_node_id, travel_direction, geom,
  road_ref, name, highway, carriageway_ref, access,
  lanes, maxspeed, bridge, tunnel, layer, tags, active

road_segment_lineage
  predecessor_segment_id, successor_segment_id, relation, overlap_ratio

source_location_binding
  source_type, source_id, internal_segment_id, status,
  distance_m, heading_delta_deg, score, margin, confidence,
  graph_version, algorithm_version, evaluated_at
```

### Phase A exit gate

- Production road requests make no Overpass calls.
- The benchmark contains no accepted opposite-carriageway or opposite-direction
  match.
- Every coloured segment refers to an accepted, current persisted binding.
- Graph imports are atomic and rollback-safe.

## Phase B — Accuracy: stateful vehicle matching and relevant segments

Goal: identify the segment under the vehicle and a short connected path ahead
without running a heavyweight route engine.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-B01 | Add a corridor candidate endpoint using position, accuracy radius, heading and look-ahead distance | P0 | `src/ndwinfo/api/routers/roads.py`, `src/ndwinfo/api/deps.py` | M | Phase A | Response contains a bounded set of nearby/ahead directed segments, not a full viewport |
| OSM-B02 | Replace POC two-fix matching with a scored state containing recent fixes, previous segment and confidence | P0 | `web/osm-poc.js`, `web/gps.js`; later rename to `web/road-match.js` | M | OSM-B01 | GPS drift does not immediately switch carriageway and valid exits remain reachable |
| OSM-B03 | Rank candidates by distance, heading, road class, prior match, connected topology and plausible transition distance | P0 | `web/road-match.js` (new), shared fixture tests | M | OSM-B02 | Ranking reasons are diagnostic and all hard conflicts are applied before scoring |
| OSM-B04 | Add confidence hysteresis: retain a plausible previous match and require a score margin plus consecutive evidence to switch | P0 | `web/road-match.js` | S | OSM-B03 | Parallel-road and low-speed drift fixtures do not oscillate |
| OSM-B05 | Define stationary/slow mode where device heading is ignored and history/topology dominate | P1 | `web/gps.js`, `web/road-match.js` | S | OSM-B03 | Stopped traffic does not flip direction because of compass noise |
| OSM-B06 | Compute `behind`, `under`, and connected `ahead` segments with branch uncertainty | P0 | `src/ndwinfo/osm/graph_query.py` (new) or bounded client graph traversal | M | OSM-B01 | API/UI never labels an unconnected nearby road as ahead |
| OSM-B07 | At forks, show common-path information until route evidence selects a branch | P1 | graph query, HUD | M | OSM-B06 | Branch-specific objects are withheld while the branch is ambiguous |
| OSM-B08 | Establish static speed-limit precedence: current lane signal, temporary order, signed/static source, derived/default, unknown | P1 | `src/ndwinfo/speed_model.py` (new), HUD | M | OSM-A09 | UI exposes value, source class and confidence; unknown is never rendered as a legal limit |
| OSM-B09 | Validate bridge/tunnel/layer crossings and grade-separated junctions in benchmark | P0 | benchmark fixtures/tests | S | OSM-B03 | Geometric crossing without graph connectivity cannot become a transition |

### Recommended lightweight matcher

For each accepted GPS fix, query only indexed candidates within an
accuracy-dependent radius. Apply hard direction/reference/access constraints,
then score the remaining candidates:

```text
score = distance_cost
      + heading_cost_if_moving
      + road_class_transition_cost
      + topology_transition_cost
      + implausible_travel_distance_cost
      - previous_segment_continuity_bonus
```

Keep a history window of approximately five to ten fixes, not an unbounded
trajectory. This is a stateful candidate ranker with hysteresis, not a full HMM.
Escalate to Viterbi/HMM only if the benchmark shows systematic errors that the
bounded matcher cannot solve.

### Phase B exit gate

- Recorded-drive replay passes the benchmark for carriageway, direction and
  topology continuity.
- Uncertain branch choice withholds branch-specific traffic information.
- Per-fix work is bounded by corridor candidates and short history, not total
  loaded roads.

## Phase C — Live object association and speed model

Goal: put every dynamic value into a common directed-segment observation model.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-C01 | Add `segment_observation` or equivalent normalized latest-state model | P0 | `src/ndwinfo/models.py`, migration | M | Phase A | Stores segment, optional lane scope, direction, value, source, timestamps, method and confidence |
| OSM-C02 | **Implemented** — project direct NDW speeds and fill bounded one-to-one chains | P0 | `src/ndwinfo/osm/speed_model.py`, roads API | M | OSM-C01 | Direct measured values retain zero and expire independently |
| OSM-C03 | Define stale handling by source timestamp and feed health; do not refresh age merely by API access | P0 | speed model, feed status | S | OSM-C02 | Stale values change state or disappear according to configured source policy |
| OSM-C04 | **Implemented** — direction-aware matrix binding and gantry grouping | P0 | `src/ndwinfo/matching/live_objects.py`, `live_object_job.py`, roads API | L | Phase A | Opposite-direction fixtures are rejected; lane scope survives to API |
| OSM-C05 | **Implemented** — direction-aware, confirmed-path DRIP binding | P1 | live-object matcher, roads API, HUD | M | OSM-B06 | HUD only shows reachable DRIP; DRIP has no lane scope |
| OSM-C06 | Bind incidents, closures, roadworks and temporary limits to affected directed segments and validity | P1 | `src/ndwinfo/matching/situation_binding.py` (new), situations ingest/API | L | Phase A | Expired and opposite-direction records cannot affect current segment state |
| OSM-C07 | **Partially implemented** — one canonical segment-state contract | P1 | roads API, `web/canonical-segment-state.js`, HUD | M | OSM-C01–C06 | Speed, MSI and DRIP are canonical; situations remain backlog |
| OSM-C08 | Add diagnostics for source coverage, binding status, age and confidence distribution | P1 | API/status UI | M | OSM-C01–C06 | Operators can distinguish missing source data from failed matching |
| OSM-C09 | **Implemented** — OSM-authoritative speed overlay with validated WEGGEG physical geometry and OSM lane fallback | P0 | traffic API, `web/speed.js`, lane topology, regressions | M | OSM-C02 | Every measured point remains visible; only fresh accepted bindings colour lines; WEGGEG cannot override OSM; exact NDW/geometry lane counts are required |

### Initial speed-state contract

```text
internal_segment_id
speed_kmh
speed_method = measured | interpolated | propagated | historical | user_observed | unknown
confidence = 0..1
source
observed_at
valid_until
sample_count
stale
```

The current model also emits bounded `interpolated` and `propagated` states only
over complete one-to-one directed chains. It fails closed at forks, merges,
carriageway changes, missing graph members and stale observations.

### Phase C exit gate

- Speed, matrix, DRIP and situation information are filtered by the same current
  internal segment/direction contract.
- Each displayed item has provenance and staleness.
- Opposite-direction test fixtures pass for every dynamic object family.

## Phase D — Performance and delivery

Goal: keep continuous driving use light on backend, network, browser main thread
and battery.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-D01 | Replace viewport road loading during GPS follow with dynamic ahead corridor loading | P1 | `web/fetch.js`, `web/gps.js`, roads API | M | OSM-B01 | Follow mode processes corridor candidates; manual browse can still use viewport |
| OSM-D02 | Serve simplified geometry by zoom/corridor distance while retaining full geometry for matching | P1 | database views/materialization, roads API | M | Phase A | Payload and render baseline improve without benchmark regression |
| OSM-D03 | Add HTTP cache validators and graph/data version keys | P1 | roads API, `web/fetch.js` | S | OSM-A09 | Unchanged corridor/viewport requests can return 304 or reuse cached data |
| OSM-D04 | Use a bounded client spatial index rebuilt only when corridor data changes | P1 | `web/road-match.js` | S | OSM-B02 | GPS fixes do not scan every loaded GeoJSON feature |
| OSM-D05 | Precompute all source bindings; prohibit source-to-road scans in user-facing requests | P0 | matching jobs, API tests | M | Phase C | Request profiling shows no full measurement/sign/road scan |
| OSM-D06 | Add incremental OSM replication updates with atomic graph activation | P2 | OSM ingest/import state | L | OSM-A05 | Normal update avoids a full country rebuild; failed diff retains active graph |
| OSM-D07 | Evaluate compressed GeoJSON versus vector tiles using measured payload/render results | P2 | API/frontend benchmark | M | OSM-D02 | Choice is documented from measurements; do not add tiles by default |
| OSM-D08 | Evaluate SSE only for changed observations after polling/network measurements | P3 | API/frontend | M | Phase C | SSE is adopted only if it reduces traffic/latency versus conditional polling |
| OSM-D09 | Add production budgets for candidate count, response bytes, query duration and render/update duration | P1 | tests/metrics/docs | S | OSM-003 | CI or operational alerts detect regression against recorded budgets |

### Phase D exit gate

- No O(n) scan over the national graph or all live objects occurs per GPS fix.
- Browser work is bounded by the active corridor and short GPS history.
- Backend and frontend budgets are backed by repeatable measurements.

## Phase E — Advanced speed estimation

Goal: fill measured-speed gaps conservatively without presenting estimates as
measurements.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-E01 | Direction- and topology-aware nearest-measurement propagation with hard distance/time limits | P1 | `src/ndwinfo/speed_model.py` | M | Phase C | Never crosses disconnected roads, carriageways, ramps or configured boundaries |
| OSM-E02 | Distance-weighted interpolation between compatible upstream/downstream observations | P2 | speed model | M | OSM-E01 | Output is labelled interpolated and confidence falls with gap, age and disagreement |
| OSM-E03 | Use travel-time observations where their referenced path can be mapped unambiguously | P2 | travel-time ingest, matching, speed model | L | Phase C | Derived value only covers the referenced connected path |
| OSM-E04 | Add confidence calibration against held-out direct measurements | P1 | benchmark/analysis tooling | M | OSM-E01 | Confidence bands correlate with observed error and are versioned |
| OSM-E05 | Add incident/closure/matrix context as a confidence modifier, not an invented speed | P2 | speed model | M | OSM-C04, OSM-C06 | Context can lower confidence or cap availability; it cannot fabricate a measured value |
| OSM-E06 | Introduce a historical baseline only after approving bounded time-series retention | P3 | new history storage/model | L | explicit product decision | Retention, storage cost and fallback error are measured and documented |
| OSM-E07 | Prototype anonymous device observations only after a privacy design and minimum sample policy | P3 | separate ingest/aggregation service | L | explicit privacy approval | No raw trajectory retention; k-anonymity/minimum sample and anti-spoofing rules are enforced |

### Phase E exit gate

- Estimated values are visibly and programmatically distinct from measurements.
- Each estimation method is validated on held-out measured segments.
- Unknown remains an allowed and preferred result when confidence is too low.

## Phase F — Shadow rollout and source simplification

Goal: migrate safely from the NWB/WEGGEG runtime path and remove redundancy only
after evidence supports it.

| ID | Task | Priority | Files/modules | Complexity | Depends on | Acceptance criterion |
|---|---|---|---|---|---|---|
| OSM-F01 | Run OSM and current NWB/WEGGEG matching in shadow mode on benchmark areas and recorded drives | P0 | diagnostic jobs/API, benchmark tooling | M | Phases A–C | Differences are classified by source, scenario and correctness impact |
| OSM-F02 | Define retirement thresholds for false carriageway/direction, coverage and stale bindings | P0 | this document + benchmark config | S | OSM-F01 | Thresholds are approved before changing default runtime source |
| OSM-F03 | Make OSM graph/segment state the default behind a reversible feature flag | P0 | `src/ndwinfo/config.py`, APIs, `web/config.js` | M | OSM-F02 | Rollback does not require data migration or client release |
| OSM-F04 | Remove NWB viewport rendering after the OSM default passes the observation window | P2 | `src/ndwinfo/api/routers/nwb.py`, `web/fetch.js`, models/feeds later | M | OSM-F03 | No required diagnostic or product capability depends on NWB |
| OSM-F05 | Remove WEGGEG from runtime matching only if it adds no required carriageway/lane correctness | P2 | traffic API, WEGGEG ingest/models later | L | OSM-F01–F03 | Benchmark proves removal does not violate correctness thresholds |
| OSM-F06 | Retain a documented source inventory with owner, update cadence and product responsibility | P1 | `docs/README.md`, feed status | S | OSM-F04/F05 | Every remaining source has one non-duplicated responsibility |

### Phase F exit gate

- OSM is the only production road geometry/runtime graph source.
- Any retained NWB or WEGGEG dependency has a measured, documented capability
  that OSM does not adequately provide.
- Rollback and graph-version diagnostics have been exercised.

## Recommended execution order

```text
Implemented Phase 0/A/B and partial C foundation
  → Phase V immediate safety, mobile and performance control gate
  → Finish Phase C unified live segment state (situations)
  → Phase D remaining measured performance work
  → Phase F shadow rollout and source retirement
  → Phase E estimation, incrementally and only where validated
```

Phase E deliberately follows a correct, observable measured-speed product. Gap
filling must not delay or obscure carriageway correctness.

## First three development increments

### Increment 1 — Reproducible correctness harness

Deliver OSM-000 through OSM-003. This is the smallest investment that protects
all following work from silent matching regressions.

### Increment 2 — Local graph slice

Implement OSM-A01 through OSM-A06 for one bounded Dutch region, using the same
POC UI. Do not ingest the whole country until the graph schema, lineage and
queries pass the regional fixtures.

### Increment 3 — Persisted live binding

Implement OSM-A07 through OSM-A10. The POC and production endpoint should then
render the same roads, while the production endpoint performs no external
request and no source matching in the user request path.

## Explicitly deferred

- Full HMM/Viterbi map matching, unless benchmark evidence requires it.
- Machine-learning speed prediction.
- Raw user trajectory collection.
- Authoritative painted-lane centreline reconstruction from incomplete OSM
  lane tags.
- Vector tiles and SSE until measurements justify their complexity.
- Immediate deletion of NWB/WEGGEG code or data before shadow validation.
