# OSM speed-lane rollout validation

This is the permanent report for the 2026-07-20 replacement of WEGGEG speed
lanes and the NWB map source with VILD-directed sensors and OpenStreetMap lane
geometry. Validation was report-only; the named correctness regressions remain
mandatory tests.

## Snapshot and method

- Measurement table: 101,578 rows; 20,813 fixed speed/flow sites.
- VILD 6.13.A: 11,938 TMC master rows, including typed `HECTO_DIR` and the
  complete DBF record in `raw`.
- Direction enrichment: 20,164/20,813 fixed sites received a VILD bearing
  (96.9%); unresolved geometry was left unset.
- OSM: local Noord-Holland Geofabrik snapshot, 22,501 major-road ways and
  71,361 lane rows.
- Dual-run viewport: `4.65,52.40,5.05,52.75`, 913 current speed points. The
  previous production WEGGEG matcher and the new OSM matcher were run against
  this identical point snapshot before the legacy tables were dropped.

## Match results

| matcher | matched | unmatched | ambiguous |
|---|---:|---:|---:|
| OSM + VILD | 773 | 140 | 1 |
| previous WEGGEG | 658 | 255 | 0 represented |

WEGGEG did not represent ambiguity: its ranking always selected a candidate.
On the 693 A-road points it matched 656 versus 581 for OSM. That higher coverage
was not treated as a release gate because 220 WEGGEG matches differed from the
VILD travel bearing by more than 45°. The new matcher intentionally leaves
those and other uncertain cases point-only.

### Direction, lane count, and distance

| distribution | p50 | p90 | maximum |
|---|---:|---:|---:|
| OSM–VILD angular difference | 10.4° | 25.5° | 44.9° |
| WEGGEG–VILD angular difference | 18.7° | 168.3° | — |
| OSM sensor distance | 3.4 m | 13.2 m | 24.9 m |
| WEGGEG sensor distance | 2.0 m | 5.6 m | 22.7 m |

OSM lane counts agreed with measurement metadata for 621/773 matches (80.3%).
WEGGEG lane counts agreed for 343/658 matches (52.1%). Lane-count disagreement
is a ranking signal, not a reason to invent or discard a direction.

Matched OSM classes were: motorway 478, motorway_link 99, trunk 33, primary
119, primary_link 1, and secondary 43. Connector-role lane geometry is excluded
from both matching and ribbon output.

## OSM failure analysis

| reason | count |
|---|---:|
| bearing mismatch (>45°) | 78 |
| conflicting normalized road reference | 30 |
| no VILD bearing | 28 |
| indistinguishable non-contiguous candidates | 1 |
| no nearby major-road lane | 3 |

Failures by nearest OSM class (where a candidate existed): motorway bearing 52,
motorway ambiguity 1, motorway road-reference 6; motorway_link bearing 26 and
road-reference 2; primary road-reference 11;
primary_link road-reference 4; trunk road-reference 7. The 31 no-bearing/no-lane
cases have no meaningful candidate class.

Failures by sensor-provider prefix:

| provider | bearing | road ref | ambiguity | no bearing | no lane |
|---|---:|---:|---:|---:|---:|
| RWS01 | 68 | 3 | 1 | 17 | 0 |
| GEO0B | 5 | 17 | 0 | 2 | 1 |
| GEO0K | 5 | 3 | 0 | 0 | 0 |
| PNH02 | 0 | 7 | 0 | 3 | 2 |
| GZS01 | 0 | 0 | 0 | 6 | 0 |

## Map cases

- **N203 opposite pair:** `PNH02_PNHTI516` and `PNH02_PNHTI516r` render as
  separate clickable points with 255.6°/75.6° bearings and distinct OSM
  `fwd`/`bwd` matches.
- **HECTO_DIR=-1:** derived R/L follows the hectometre sign while the signed
  TMC-oriented bearing remains authoritative; disagreement with explicit R/L
  is shown rather than changing the bearing.
- **Exactly colocated directions:** opposite `tmc_direction` values remain
  separate aggregation records and offset to opposite roadsides.
- **Main/parallel carriageways and interchanges:** road reference, lane count,
  angle, and distance rank candidates; ramps and connector geometry are not
  silently substituted for a main carriageway.
- **`oneway=-1`:** stored OSM IDs/geometry remain unchanged while travel bearing
  and effective backward lane numbering are reversed.
- **Unresolved direction:** missing VILD bearings, >45° candidates, road-reference
  conflicts, and non-contiguous ambiguity remain Traffic Speed Points only.

The browser popup exposes the bearing source, TMC direction, explicit and
derived carriageway provenance, disagreement state, OSM match details, and
failure reason so these cases remain inspectable after the legacy sources are
gone.
