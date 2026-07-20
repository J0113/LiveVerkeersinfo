# 10 — Direction data quality for traffic-speed sensors

This document explains why some fixed traffic-speed sensors cannot currently be
shown with a clear travel direction in the UI, and how to resolve that without
mixing up three different concepts:

- **VILD direction**: `positive` or `negative`, relative to the VILD/ALERT-C
  `POS_OFF`/`NEG_OFF` chain;
- **carriageway side**: `R` or `L`, defined by the direction in which road
  hectometrering increases or decreases while driving;
- **compass bearing**: the real-world direction of travel in degrees.

The scope is deliberately limited to fixed **traffic-speed/flow sensors** whose
`measurementSiteLocation` is a DATEX `Point`. Travel-time itineraries and
floating-car data are out of scope.

> **Implemented 2026-07-20.** The investigation and baseline counts below are
> retained as the decision record. Production now stores `HECTO_DIR`, derives a
> signed local VILD bearing, keeps explicit and derived R/L separate with
> provenance/conflict fields, preserves opposite-direction aggregation, and
> matches only confident OSM lanes. The naive direction-to-R/L fallbacks and
> the WEGGEG/NWB production sources have been removed. See
> [the rollout comparison](12-osm-speed-validation.md).

The counts below were reproduced from the complete official
[`measurement_current.xml.gz`](https://opendata.ndw.nu/measurement_current.xml.gz)
snapshot containing 101,487
`measurementSiteRecord`s. After selecting only Point records with traffic-speed
or traffic-flow characteristics, the in-scope population is **20,813 sites**.

---

## Case study: PNH02_PNHTI516 / PNH02_PNHTI516r

Two loop sensors on the N203 Provincialeweg near Uitgeest lie approximately two
metres apart and measure opposite traffic directions:

| | `PNH02_PNHTI516` | `PNH02_PNHTI516r` |
|---|---|---|
| equipment ref | PNH_516-**B** | PNH_516-**A** |
| name | "Castricum N203 Provincialeweg, op-/afrit Rijksweg A9 wz - Middelweg thv hmp 55,3" | "Wormerveer N203 Provincialeweg, Middelweg - op-/afrit Rijksweg A9 wz thv hmp 55,3" |
| lat/lon | 52.518232, 4.710550 | 52.518253, 4.710546 |
| `alertCDirectionCoded` | `positive` | `negative` |
| TMC primary `specificLocation` | 10798 | 10800 |
| offset | 600 m | 654 m |
| lanes | 1 | 1 |

The markers visually overlap at normal map scales. Both rows currently have
`carriageway=None`, and the API does not expose `tmc_direction`, so the frontend
has no explicit direction signal with which to label or separate them.

### Resolving their travel directions through VILD

`measurement_site.tmc_primary` is a VILD/ALERT-C location code. The relevant
records in `VILD6.13.A.dbf` form this chain:

| LOC_NR | LOC_DES | name | HSTART_POS / HSTART_NEG | HECTO_DIR | POS_OFF → | NEG_OFF → |
|---|---|---|---|---:|---|---|
| 10798 | Afrit | "A9: Uitgeest" / "A9" | 547 / 550 | +1 | 10800 | 10797 |
| 10800 | Kruising | "Uitgeest-Centrum" | 559 / 559 | +1 | 10801 | 10798 |

The positive chain runs:

```text
… 10797 → 10798 (A9 exit) → 10800 (Uitgeest-Centrum) → 10801 …
```

Therefore:

- `PNH02_PNHTI516`, with direction `positive`, measures traffic travelling
  away from the A9 exit toward Uitgeest-Centrum/Castricum;
- `PNH02_PNHTI516r`, with direction `negative`, measures traffic travelling
  toward the A9 exit from the Middelweg/Wormerveer direction.

For this particular VILD line, `HECTO_DIR=+1`: hectometrering increases in the
positive VILD direction. Consequently the positive sensor is also carriageway
R and the negative sensor carriageway L. That relationship is case-specific;
it is not the definition of positive and negative VILD direction.

---

## Meaning of `alertCDirectionCoded`

`alertCDirectionCoded` is not an absolute compass direction and is not itself a
carriageway-side code:

- `positive` means traffic travels in the positive VILD/ALERT-C coding
  direction, following `POS_OFF`;
- `negative` means traffic travels in the opposite direction, following
  `NEG_OFF`.

The value is therefore a valid and useful **travel-direction signal**. To turn
it into a compass bearing, it must be combined with an oriented VILD geometry.
To turn it into R/L, it must additionally be combined with the direction of
hectometrering.

### Why positive does not always mean R

VILD defines carriageway R as the carriageway on which hectometrering increases
in the direction of travel. Carriageway L is the carriageway on which it
decreases. VILD explicitly treats this as independent from its positive coding
direction and records the relationship in `HECTO_DIR` (see sections 3.3 and
4.2.11 of the
[official VILD technical handbook](https://docs.ndw.nu/blob/TechnischHandboekVILD620191101.pdf)):

| `HECTO_DIR` | `tmc_direction=positive` | `tmc_direction=negative` |
|---:|---|---|
| `+1` | R | L |
| `-1` | L | R |
| `0` | not safely derivable from this rule | not safely derivable from this rule |

Equivalently, R applies when the travel-direction sign equals `HECTO_DIR`; L
applies when the signs differ.

### Validated against ground truth — the rule helps, but is not exact

The `HECTO_DIR`-adjusted rule above was checked against an independent
ground truth: the 9,503 `RWS01` MONIBAS sites whose carriageway is already
known from an explicit `hrl`/`hrr` code in the site name (not derived from
`alertCDirectionCoded` at all). Comparing that known R/L to what each rule
would have predicted from `tmc_direction` + `HECTO_DIR` alone:

```text
naive rule (positive→R, negative→L, ignoring HECTO_DIR):    76.1% correct
HECTO_DIR-adjusted rule (sign(direction) == sign(HECTO_DIR)): 91.4% correct
  (excluding 160 HECTO_DIR=0 sites, which neither rule can resolve)
```

The `HECTO_DIR` correction is a large, real improvement (76.1% → 91.4%), but
it still mispredicts **804 of 9,343** resolvable cases (8.6%). The cause of
that residual isn't established here — possibly additional VILD attributes
beyond `HECTO_DIR`, or inherent exceptions at ramps/interchanges. Treat the
91.4% figure as the current ceiling for a `tmc_direction`+`HECTO_DIR`-only
rule, not as a solved problem; do not present derived R/L as authoritative
without a similar ground-truth check on the target road network first.

### Unverified citations

The handbook section numbers (3.3, 4.2.11) and the two external URLs above
were supplied by a reviewer pass on this document and have **not** been
opened/confirmed in this session (no PDF-reading tool was available). Before
relying on them — e.g. in a PR description or a support answer — fetch
`Technisch Handboek VILD 6 20191101.pdf` from `data/VILD6.13.A.zip` and check
the cited sections directly.

---

## GEO0B_R_RWSTI358250 example

This RWS loop sensor on an A9 connector road has a different site-name format:

```text
measurementSiteName: 009vwb058082
measurementSiteLocation xsi:type="Point"
  latitude 52.50756, longitude 4.70814
  affectedCarriagewayAndLanes/carriageway: entrySlipRoad
  alertCDirectionCoded: positive
  specificLocation: 10598, offset 39
```

The name matches `\d{3}[a-z]{3}\d{6}`:

- road `009` → A9;
- position `058082` → km 58.082;
- `vwb` identifies a connector-road form, not an R/L carriageway side.

The current parser assigns R solely because the direction is positive. That is
correct for this individual record because VILD location 10598 has
`HECTO_DIR=+1`, but the same shortcut is not valid for every road.
`affectedCarriagewayAndLanes/carriageway=entrySlipRoad` is useful carriageway
**type** information, but likewise does not supply R/L.

---

## In-scope population and direction completeness

The complete measurement-site table has a clean location/value-type split:

| `measurementSiteLocation` type | value types | count | in scope |
|---|---|---:|---|
| `Point` | trafficFlow + trafficSpeed | 20,812 | yes |
| `Point` | trafficFlow only | 1 | yes |
| `ItineraryByIndexedLocations` | travelTimeInformation | 80,674 | no |

For the **20,813 Point speed/flow sites**:

- every record contains exactly one `alertCDirectionCoded`;
- every value is either `positive` or `negative`;
- every record contains a primary VILD `specificLocation`;
- direction is therefore unambiguous for every in-scope sensor.

This establishes 100% availability of a VILD-relative travel direction. It
does **not** establish 100% availability of carriageway R/L or a compass
bearing.

---

## Current carriageway resolution rate

`_parse_site_location()` currently derives road, km and carriageway from a
small set of provider-specific site-name patterns:

1. `GEO*`: parse the 12-character name and currently map VILD direction directly
   to R/L;
2. `RWS01`: parse the MONIBAS name, preferring explicit `hrl`/`hrr`, otherwise
   using the same direct VILD-direction mapping;
3. provincial/regional providers: accept names beginning with patterns such as
   `N457 hmp 4.75 Re` or `N457 km 4.75 Li`;
4. all other name formats remain unresolved.

Measured only over the in-scope Point speed/flow population:

```text
traffic-speed/flow sites:        20,813
carriageway resolved now:        12,273  (58.97%)
carriageway None:                 8,540  (41.03%)
carriageway None with direction:  8,540 (100.00% of missing)
```

Non-zero provider breakdown:

| prefix | speed sites | resolved | % |
|---|---:|---:|---:|
| RWS01 | 14,751 | 9,503 | 64.4% |
| GEO0C | 734 | 642 | 87.5% |
| PZH01 | 646 | 643 | 99.5% |
| GEO0K | 619 | 615 | 99.4% |
| GEO0B | 606 | 522 | 86.1% |
| GEO2A | 291 | 138 | 47.4% |
| GEO1A | 241 | 210 | 87.1% |

All other speed-site prefixes currently resolve 0% through the parser. This
includes the PNH02 case because its free-text name does not begin with the
structured `N… hmp … Re/Li` form.

### Why the missing 8,540 sites are not all recoverable as R/L from direction alone

Joining the missing speed sites to their primary VILD locations gives:

```text
HECTO_DIR is +1 or -1:  7,672
HECTO_DIR is 0:            868
```

Among the 7,672 sites with a usable `HECTO_DIR`, a direct
`positive→R`/`negative→L` fallback would reverse R/L for **2,304 sites
(30.03%)** because their `HECTO_DIR` is -1.

The safe conclusions are therefore:

- all 8,540 missing sites have a usable VILD-relative direction;
- at least 7,672 can receive R/L from `tmc_direction + HECTO_DIR`;
- the remaining 868 require another authoritative signal or should keep
  `carriageway=None`;
- a direction value must never be presented as though it were already R/L.

### This is not just a future risk — it's already shipping

The `GEO*` branch and `RWS01`'s no-`hrl`/`hrr` fallback path are **already
live** and already use the unverified naive rule (`positive→R`,
`negative→L`, no `HECTO_DIR` check) to populate `carriageway` today:

```text
GEO* sites resolved via alc_dir fallback (no ground truth in name): 2,127
RWS01 sites resolved via alc_dir fallback (no hrl/hrr in name):      2,600
                                                            total:   4,727
```

Applying the ~24–30% error rate measured above (naive rule vs. ground truth)
to this population suggests roughly **1,100–1,400 sites currently in the
database likely already carry the wrong `carriageway` value** — not a
hypothetical consequence of a future change, an existing data-quality gap.
The 6,903 `RWS01` sites with an explicit `hrl`/`hrr` code are unaffected
(their carriageway never depended on `alertCDirectionCoded`).

---

## Available fields and remaining plumbing

`MeasurementSite` already stores:

- `tmc_direction`: populated for every speed site, but not selected by
  `/api/traffic/speed` and not used by the frontend;
- `tmc_primary`: populated for every speed site and usable for VILD lookup;
- `carriageway`: available for a derived R/L result;
- `openlr_bearing`: selected by the API and used as a fallback bearing.

`openlr_bearing` is filled for **2,143 / 20,813 = 10.30%** of speed sites. It is
useful when present but too sparse to serve as the primary national direction
source. None of the PNH02, RWS01 MONIBAS or GEO0B sites has it.

The current `vild_tmc` table stores `lin_ref`, `pos_off`, `neg_off` and road
number, but not `HECTO_DIR`. Therefore:

- exposing `tmc_direction` needs no schema change;
- computing a VILD-oriented compass bearing needs no new feed and can use the
  already ingested chain and geometry;
- persistently deriving correct R/L through VILD requires ingesting
  `HECTO_DIR`, or an equivalent authoritative enrichment step.

---

## Computing a real traffic bearing

A VILD-line tangent is a suitable source for a real-world travel bearing, but
the raw coordinate order of `vild_line.geom` must not be assumed to be the
positive direction without verification.

A robust procedure is:

1. Look up `measurement_site.tmc_primary` in `vild_tmc` and obtain `lin_ref`.
2. Load the associated `vild_line` geometry.
3. Select a neighbouring VILD point on the same line through `POS_OFF` (or use a
   same-line `NEG_OFF` neighbour when necessary).
4. Project both VILD points onto the line to establish which local coordinate
   direction corresponds to the positive chain.
5. Project the sensor's own `geom` onto the line and compute a local tangent,
   using neighbouring vertices far enough apart to avoid a zero-length or noisy
   bearing.
6. Orient that tangent in the established positive direction.
7. If `tmc_direction == "negative"`, rotate the bearing by 180 degrees.
8. Handle `MultiLineString` geometry and unresolved/cross-line cases explicitly;
   do not silently use an unrelated longest component.

This can reuse the projection, clipping and orientation principles in
`rebuild_traveltime_geometry()`. That code already orients clipped geometry
between two projected VILD points instead of trusting the source vertex order.

---

## API and frontend implications

Simply adding `tmc_direction` to the output properties is not sufficient. The
speed endpoint merges readings from measurement systems at the same physical
location. When R/L is absent, opposite directions could otherwise be combined.

The API should:

- select and group by `tmc_direction`;
- include it in the physical-location merge key when carriageway is absent;
- preserve it in `/api/traffic/speed` and `/api/traffic/speed/map` point
  properties;
- expose a derived bearing and its source when available;
- expose R/L only when it was derived from an authoritative rule.

The frontend should:

- show direction/bearing in the speed popup for diagnostics;
- use the travel bearing or a reliable roadside bearing to offset overlapping
  opposite-direction markers visibly;
- avoid presenting raw `positive`/`negative` as a user-facing compass label;
- continue to display `carriageway` only when a valid R/L value exists.

For the N203 pair, successful handling means two separately clickable markers
with approximately opposite travel bearings, rather than two indistinguishable
tiles occupying the same screen position.

---

## Implemented action items

1. **Do not add a general `positive→R`/`negative→L` parser fallback.** Keep
   `tmc_direction` as its own direction field.
2. **API:** select `tmc_direction`, include it in speed-site grouping/merging,
   and return it in both speed responses.
3. **Frontend:** display direction diagnostics and visibly separate overlapping
   opposite-direction speed markers.
4. **Bearing enrichment:** compute a VILD tangent whose positive orientation is
   established through neighbouring `POS_OFF`/`NEG_OFF` points, then flip it for
   negative traffic direction.
5. **Optional R/L enrichment:** ingest `HECTO_DIR` and combine it with
   `tmc_direction`; leave R/L unset where `HECTO_DIR=0` and no other
   authoritative carriageway-side field is available.
6. **Tests:** cover the N203 pair, at least one `HECTO_DIR=-1` road, exactly
   co-located opposite-direction sources, and a VILD geometry whose raw vertex
   order cannot be assumed to be positive.
7. **Audit existing data:** the `GEO*` branch and `RWS01`'s no-`hrl`/`hrr`
   fallback already wrote `carriageway` for 4,727 sites using the unverified
   naive rule (see above) — re-derive or null out these values once
   `HECTO_DIR` is ingested, rather than trusting them as-is.
8. **Verify before citing:** confirm the VILD handbook section numbers and
   both external URLs referenced below actually say what this document claims
   before using them outside this investigation.

All eight items above are implemented. Explicit `hrl`/`hrr`, `HRL`/`HRR`, and
`Re`/`Li` remain authoritative. A derived R/L never overrides them or changes
the VILD travel bearing; disagreements are exposed diagnostically. Unresolved
VILD geometry and uncertain OSM matches remain visible as direction-aware
Traffic Speed Points rather than coloring a guessed lane.

---

## References

- [DATEX II Alert-C direction semantics](https://docs.datex2.eu/levels/mastering/location/alertc/)
- [NDW Technical Handbook VILD 6](https://docs.ndw.nu/blob/TechnischHandboekVILD620191101.pdf)
- [Static reference dataset notes](07-static-reference.md)
- [`_parse_site_location()` and measurement-site parsing](../src/ndwinfo/parsers/datex_v2.py)
- [`MeasurementSite` and `VildTmc` models](../src/ndwinfo/models.py)
- [Traffic-speed API aggregation](../src/ndwinfo/api/routers/traffic.py)
- [VILD chain projection and geometry orientation](../src/ndwinfo/ingest/traveltime_geometry.py)
