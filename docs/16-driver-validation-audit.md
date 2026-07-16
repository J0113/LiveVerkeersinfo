# Driver validation and control audit

Status: control audit completed on 2026-07-16 against the regional
Noord-Holland OSM graph and local Docker stack. This is a release-planning
gate, not a national accuracy claim.

## Decision

Do not relax the direction/carriageway confidence gates to gain coverage.
Optimize availability subject to the hard constraint that no known
opposite-direction or wrong-carriageway fact is shown. Driver usability should
instead use three explicit states:

1. `confirmed`: show current direction-bound facts;
2. `transient_uncertainty`: hold only the last confirmed road and speed for at
   most three seconds or 50 metres, label it `Signaal controleren`, and add no
   new facts;
3. `hard_conflict`: immediately remove direction-bound facts for an opposite
   heading, unresolved fork, topology conflict or stale source. Device GPS
   speed may remain visible.

The three-second/50-metre hold is a proposed operating point that must be
calibrated with recorded drives. It is not yet a measured optimum.

## Release-blocking finding

`roadMatchCanonicalRoadSigns()` returns `null` while no road match is accepted.
`hud.js` interprets that as permission to fetch and select legacy nearest-point
MSI/DRIP data. During acquisition, a switch or uncertainty, this can therefore
re-introduce a sign from a nearby opposite or parallel carriageway. The speed
fallback is already disabled when the canonical lane pipeline exists.

The contract must distinguish `canonical unavailable` from `canonical present
but currently unconfirmed`. In driving mode, an unconfirmed canonical match
must produce an authoritative empty result and never activate a proximity
fallback.

## Scenario evidence

| Scenario | Method | Result | Remaining gap |
|---|---|---|---|
| A4 happy path | Existing 2.77 km browser simulation, 500 ms fixes, 6 m accuracy | Real GPS/matcher/API path remains stable | Does not exercise drift, forks or degraded signal |
| N203 two-way provincial road | Local corridor/API at `4.710546,52.518253` | Opposite directions have distinct measured states and segment bindings | Same-centreline colours overlap visually; full trajectory replay is still needed |
| A1/A10 main/link/parallel complex | Local corridor/API at `4.9602,52.3482`, heading 32 degrees | 25 bounded candidates, no truncation; main/link and 2/3-lane alternatives present | Must become a deterministic browser replay with branch decisions |
| Synthetic matcher suite | Browser matcher core plus Python fixture evaluator | Zero accepted wrong direction/carriageway in current fixture set | Synthetic set is small; recorded hand-reviewed drives remain mandatory |

The N203 snapshot exposed approximately 46 km/h forward at confidence 0.669
and 45 km/h backward at confidence 0.658 during the audit. Live values change;
the important result is their separate direction-bound state.

The six-scenario synthetic matcher set contains 28 fixes. Thresholds from 0.50
through 0.75 accepted 26/28 without a known wrong match; 0.80 accepted 25/28.
That is insufficient to call the score a calibrated probability. Keep the
current 0.62 vehicle-match and 0.60 live-fact gates provisionally, show only
confirmed/uncertain language to drivers, and calibrate with recorded drives.

## Mobile-first findings

Browser checks used 390x844 portrait and 844x390 landscape. Code and API checks
also covered 320/390/430 portrait and 667x375 landscape requirements.

| Priority | Finding | Evidence |
|---|---|---|
| P1 | Primary HUD is diagnostic rather than glanceable | `forward`, confidence percentages and provenance appear in the driving view |
| P1 | Important lane text is too small | lane-state text is 8 px; supporting text is commonly 9-11 px |
| P1 | A single MSI/DRIP tile may keep half-width grid sizing | five-lane content can be scaled below useful readability on a 320 px screen |
| P1 | Portrait overlays can collide and landscape has no compact layout | simulation/status/settings and HUD occupy competing fixed positions |
| P1 | GPS starts without explicit driver action | `initGPS()` enters FOLLOW and starts high-accuracy geolocation at page load |
| P1 | Saved diagnostic layers can pollute driving mode | persisted `Traffic Speed Points` may override clean defaults |
| P1 | Unverified MSI lane scope can become a fictitious lane portal | differing `source_only` aspects can reach `lane: undefined` / `Rijstrook ?` presentation |
| P1 | `Max` can overstate a conditional OSM limit | 790 regional segments contain conditional maxspeed; the HUD currently reads the base value only |
| P2 | Touch targets are undersized | map controls measure 36x36 px; settings measured 42x42 px |
| P2 | Opposite states on one OSM centreline overlap visually | fixed 1.3 px offsets do not scale with rendered line width |

Primary driving values should be at least 16 px, supporting text at least 12 px
and interactive targets at least 44x44 px. These are backlog acceptance targets,
not claims about the current UI.

## Direction-safe usefulness gaps

- Canonical sign selection reads the current segment plus four ahead segment
  objects. On the tested N203 directions, five segments fit inside 318 metres
  and seven inside 566 metres. Selection must therefore use path metres with a
  separate object cap, not a fixed segment count.
- In a fixed A4-area viewport of 1,750 segments, the model produced 48 measured,
  5 interpolated and 35 propagated speed states. Thirty measured, three
  interpolated and zero propagated states passed the generic 0.60 UI gate.
  Do not lower that gate globally: calibrate by method and correct chain
  identity first.
- Safe propagation currently requires a non-empty road reference and equal raw
  OSM `forward/backward` labels. Those labels describe source-way coordinate
  order and can change across a physically continuous directed path. Use
  directed topology plus trustworthy road/carriageway identity instead.
- Lane-specific MSI requires verified canonical lane scope. With only
  `source_only`, equal aspects may be summarized carriageway-wide; differing
  aspects must report uncertain lane layout rather than invent lane order.

## Performance evidence

Measurements were taken against the running local Docker stack. They are
machine- and dataset-specific and should be treated as the first repeatable
baseline.

| Operation | Observed result |
|---|---|
| `/roads/corridor`, 26 segments | about 57-59 ms median; 8.6 KB gzip / 46.6 KB JSON |
| `/roads/path`, 8 segments | about 48 ms in the audit; root warm check 66 ms |
| Small `/roads` viewport, 214 segments | about 80-90 ms; 33.5 KB gzip / about 300 KB JSON |
| Capped large `/roads` viewport, 2,000 segments | about 390 ms; 293 KB gzip / 2.57 MB JSON |
| National persisted `/traffic/speed/map` layer, 500 output points | 3.27 s; 438 KB JSON; 35,951 grouped rows processed internally |
| Complete regional measurement binding rebuild | 14.6 s for 16,187 evaluated locations |
| 100 concurrent corridor calls | about 47 requests/s; roughly 10% lower throughput with poller load in this run |

The corridor and connected-path design is appropriately bounded. The primary
measured bottleneck is `/traffic/speed/map`: its output limit is applied after a
large database result is materialized and merged in Python. The observed query
used a parallel sequential scan of measurement characteristics, tens of
thousands of traffic lookups and a temporary sort. Fix this before adding more
national point-layer work.

Poller samples reached approximately one CPU core during snapshot ingest and
235-419 MiB memory. `trafficspeed` processed about 195,346 upserts per cycle in
6-11 seconds; travel time processed about 80,674 in 16-21 seconds. These are
throughput observations, not proof of a leak. Browser main-thread time and
battery use were not instrumented in this audit and remain an explicit gap.

## Required validation matrix

Every matcher, canonical-state or HUD change must run:

- deterministic A4, N203 and A1/A10 replays through the production GPS handler;
- moving, stationary, drift, temporary network gap, opposite-heading conflict
  and unresolved-fork variants;
- portrait 320x568, 390x844 and 430x932;
- landscape 667x375 and 844x390;
- assertions for zero legacy fallback during canonical uncertainty, zero
  wrong-direction/carriageway facts, no overlap/clipping, minimum typography
  and target sizes;
- cold/warm API latency, compressed/uncompressed payload, candidate count and
  browser update-duration budgets.

Recorded drives with hand-reviewed ground truth remain the production release
gate. Synthetic and desk simulations cannot establish national accuracy.
