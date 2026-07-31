# Matrix stage 1 dry-run validation

Date: 2026-07-31
Matcher: `matrix-gantry-v7`

This report covers the first gated Matrix slice from the OSM road-linked live
data plan. It is a bounded validation sample; source geometry is unchanged, and
the explicit assignment ledger is populated for the sample area only.

## Snapshot profile

The local PostGIS snapshot contained 18,458 Matrix rows, 18,315 rows with both
geometry and bearing (the same 18,315 rows — no row has one without the other),
and 6,439 physical gantry groups (6,366 over the rows with geometry). Nearest
directed lane centerline across all 18,315: p05 0.06 m, p50 1.07 m, p95 1.72 m,
p99 3.29 m, maximum 21.70 m. Exactly 4 signs lie beyond the 20 m search radius
and are reported as `no_major_road` — matching the 4 seen in the sample run
below.

The Matrix sample supports the assumption that the shapefile bearing follows the
directed carriageway: nearby same-carriageway lanes were within roughly 15
degrees in the inspected sample, while opposing lanes were approximately 180
degrees away. This assumption is deliberately not applied to DRIPs.

`carriageway_ref` on the parent OSM way is the decisive near-tie evidence. It is
present on 99.3% of motorway ways and 93.9% of motorway links, with values `Re`
and `Li` for main carriageways and single lowercase letters for connectors —
the same vocabulary NDW uses. Case is significant on both sides: NDW's lowercase
`r` is a connector, not the main carriageway `R`.

## Bounded dry run

```text
python -m ndwinfo.match_matrix --bbox 4.6,52.3,4.9,52.6 --limit 250
```

| Metric | Result |
|---|---:|
| Physical gantries evaluated | 250 |
| Signs evaluated | 764 |
| Matched signs | 735 |
| High confidence | 727 |
| Medium confidence | 8 |
| Ambiguous (held out) | 0 |
| Unmatched | 29 |
| Lane-count mismatch diagnostics | 253 |
| Matched source distance p50 / p95 / max | 1.748 m / 8.661 m / 19.313 m |

Rejection reasons: 18 bearing mismatch, 7 road-reference conflict, 4 no major
road, 2 lane-mapping missing.

No sign in this area is ambiguous. Every candidate pair that previously looked
ambiguous turned out to be one carriageway continuing across an OSM segment
boundary — see the continuity rule below. The fail-closed path is still live and
unit-tested; this area simply contains no genuine parallel-road tie.

Persisting the same sample:

```text
docker compose run --rm -T app python -m ndwinfo.match_matrix \
  --bbox 4.6,52.3,4.9,52.6 --limit 250 --persist
```

produced 764 assignment rows and 735 point links. The Matrix API returns
the linked road's identity and match metadata with each feature; the map draws
the sign at its source point and shows the link in the popup.

## Known limitations of this sample

- **Lane-count mismatch affects 253 of 735 matched signs (34%).** Those keep a
  high-confidence segment link but no lane-specific link, because the NDW lane
  numbering and the OSM directional lane count disagree. Lane-level MSI
  rendering is therefore unavailable for about a third of matched signs, and the
  underlying cause has not yet been investigated.
- **`road_revision` is written NULL.** Per-road topology revisions are not
  implemented yet, so persisted links have no staleness protection. An OSM lane
  rebuild can leave a link pointing at a segment that no longer exists; the API
  does not currently detect that.
- **Assignments are area-bounded.** A run replaces the rows it covers and drops
  every row written by a different `algorithm_version`, but signs outside the
  sample bbox simply have no assignment.

## Fixtures and checks

The sanitized fixture set covers opposite carriageways, close parallel roads
with and without separating evidence, gantry consensus, lane-count mismatch,
conflicting road reference, connector carriageway case, ghost deduplication, and
missing bearing.

Three safety properties are asserted directly:

- two parallel roadways carrying the same route reference, separated only by
  sub-metre distance and a degree or two of bearing, fail closed as `ambiguous`;
- a lowercase connector carriageway (`r`) is never folded into the main
  carriageway (`R`) at the same road and kilometre;
- consecutive pieces of one carriageway are not scored against each other.

That last rule matters more than it sounds. OSM splits a carriageway into a
chain of ways, so a gantry standing near a segment boundary draws candidates
from both sides of it. Those pieces chain through a shared node — the logical
segment ID `<way>:<start_node>:<end_node>` carries the topology directly, so
continuity is decided without a graph query. Two directions of the *same*
segment share both nodes and are deliberately excluded: they are the opposite
carriageways, which is exactly the choice that must stay ambiguous.

Worked example (A10 km 27.19, left carriageway): the gantry sits between
`1333532311:7526531055:12337587960` at 4.13 m and
`1333532308:12337587960:6321579209` at 4.47 m. Both are `A10`,
`motorway_link`, carriageway `c` — one ramp continuing. It links to the nearer
piece, 1333532311. The right-carriageway gantry 20 m away links to the mainline
way 48748939 at 0.07 m.

Full Python suite: 190 passed. Frontend suite: 73 passed.

## Gate

The matcher, report, assignment persistence, and pre-limit ghost deduplication
are ready for manual review. Per-road topology revisions, the road-scoped API,
and the road-scoped HUD remain unbuilt, as the plan requires for this stage.
