# 10 — Carriageway/direction data quality (traffic speed sites)

Findings from a deep-dive into why some `measurement_site` rows can't be shown
with a clear travel direction in the UI (e.g. two co-located sensors on
Provincialeweg N203 rendering as unlabeled stacked tiles). Covers the VILD
line/TMC reference, how `alertCDirectionCoded` works, current carriageway
resolution rate, and a concrete plan to fix it.

---

## Case study: PNH02_PNHTI516 / PNH02_PNHTI516r (N203 Provincialeweg, Uitgeest)

Two loop sensors at (almost) the same point, opposite carriageways:

| | `PNH02_PNHTI516` | `PNH02_PNHTI516r` |
|---|---|---|
| equipment ref | PNH_516-**B** | PNH_516-**A** |
| name | "Castricum N203 Provincialeweg, op-/afrit Rijksweg A9 wz - Middelweg thv hmp 55,3" | "Wormerveer N203 Provincialeweg, Middelweg - op-/afrit Rijksweg A9 wz thv hmp 55,3" |
| lat/lon | 52.518232, 4.71055 | 52.518253, 4.710546 |
| `alertCDirectionCoded` | positive | negative |
| TMC primary `specificLocation` | 10798 | 10800 |
| offset (m) | 600 | 654 |
| lanes | 1 | 1 |

Coordinates are ~2m apart — on a map they render on top of each other, so the
two speed tiles stack with no visual distinction (this is what triggered the
investigation). `carriageway` is `None` for both in the DB today (see below),
so nothing in the API/UI currently disambiguates them either.

### Resolving the actual direction via the VILD TMC chain

`measurement_site.tmc_primary` (10798 / 10800) is a VILD/ALERT-C location code.
Looking it up in the VILD master table (`VILD6.13.A.dbf`, see
[07-static-reference.md](07-static-reference.md)) gives the chain topology:

| LOC_NR | LOC_DES | name | hmp (POS/NEG) | POS_OFF → | NEG_OFF → |
|---|---|---|---|---|---|
| 10798 (`516`, positive) | Afrit | "A9: Uitgeest" / "A9" | 547/550 | 10800 | 10797 |
| 10800 (`516r`, negative) | Kruising | "Uitgeest-Centrum" | 559/559 | 10801 | 10798 |

The chain runs `…10797 → 10798 (A9 exit) → 10800 (Uitgeest-Centrum) → 10801…`
with **increasing hectometrering** in the `POS_OFF` direction (547→559). That
increasing-hmp direction is the literal definition of "positive" here.

So:
- **`PNH02_PNHTI516` (positive)** = traffic driving **away from the A9
  Uitgeest exit**, toward Uitgeest-Centrum/Castricum (hmp increasing).
- **`PNH02_PNHTI516r` (negative)** = traffic driving **toward the A9 Uitgeest
  exit**, coming from Middelweg/Wormerveer (hmp decreasing).

### What `alertCDirectionCoded` actually means

It is **not** an absolute compass direction. It's relative to the
**digitization direction of the VILD line** (`vild_line`/TMC chain) that the
location code sits on: `positive` = same direction as the chain's `POS_OFF`
pointer (increasing hectometrering), `negative` = opposite (`NEG_OFF`). To turn
it into a real-world bearing you have to combine it with the underlying
`vild_line` geometry's coordinate order (see recommendation below) — you can't
read "noord" or "zuid" off the value directly.

---

## GEO0B_R_RWSTI358250 — connector-road (verbindingsweg) example

A second example showing the same mechanism applied to a different site-name
format (RWS "GEO" family — loop sensors on connector/ramp roads):

```
measurementSiteName: 009vwb058082
measurementSiteLocation xsi:type="Point"
  latitude 52.50756, longitude 4.70814
  alertCPoint:
    alertCDirectionCoded: positive
    specificLocation: 10598, offset 39
```

Name matches `\d{3}[a-z]{3}\d{6}` → road_num `009` → **A9**, km `058082` →
**58.082**. The middle 3 letters (`vwb` = verbindingsweg-code) are **not**
parsed for carriageway in this branch — carriageway comes purely from the
`alertCDirectionCoded` fallback (`positive` → `R`). This is exactly the
fallback pattern missing from the PNH branch (see below).

---

## Point vs. multi-point records — trafficSpeed vs travelTime

`alertCDirectionCoded` can appear more than once per `measurementSiteRecord`.
Checked across the full `measurement_current2.xml` (101,487 records):

| `measurementSiteLocation` xsi:type | `specificMeasurementValueType` | count |
|---|---|---|
| `Point` | trafficFlow + trafficSpeed | 20,812 |
| `Point` | trafficFlow only | 1 |
| `ItineraryByIndexedLocations` | travelTimeInformation | 80,674 |

**100% clean split, zero overlap.** Every multi-point record (an itinerary
made of several `location xsi:type="Linear"` segments, each with its own
`alertCDirectionCoded` — sometimes even opposite values within the same
record, since each segment is measured against its own bit of TMC chain) is a
**travel-time** route (e.g. `GUT01_091`, FCD-based). Every simple `Point`
record — including all the sensor examples above — is a **traffic-speed**
site with **exactly 1** `alertCDirectionCoded`. So for the traffic-speed feed
specifically, direction is always unambiguous (1 value per site); the
multi-value complexity only exists in `traveltime.xml`.

### Fill rate

- `alertCDirectionCoded` present at least once: **101,487 / 101,487 = 100%**
  of all `measurementSiteRecord`s (traffic-speed and travel-time combined).
- No empty/garbage values — only ever `positive` or `negative`.

---

## Current carriageway resolution rate (all traffic-speed sites)

`_parse_site_location()` ([src/ndwinfo/parsers/datex_v2.py:81](../src/ndwinfo/parsers/datex_v2.py)) tries, per site-id prefix:

1. **`GEO*`**: name matches `\d{3}[a-z]{3}\d{6}` → carriageway from
   `alertCDirectionCoded` fallback (`positive`→R, `negative`→L). Middle letters ignored.
2. **`RWS01`**: name matches `\d{4}[a-z]{3}\d{4}[a-z]{2}` → carriageway from the
   `hrl`/`hrr` code in the name if present, else same `alertCDirectionCoded` fallback.
3. **`RWS08`**: carriageway from `HRL`/`HRR` in the id itself. **No `alertCDirectionCoded` fallback.**
4. **else** (every provincial/regional prefix: `PNH*`, `POV*`, `PUT*`, `PLB*`,
   `PZH*`, `PGL*`, `PGR*`, `PFR*`, `PFL*`, `GDH*`, `GEH*`, `GAD*`, `GRT*`,
   `GUT*`, `GMS*`, `GZS*`, `RDH*`, `RWS04/09/10`, …): regex `^([AN]\d+)\s+(hmp|km)\s+([\d.,]+)\s+(Re|Li)`
   must match the name (e.g. `"N457 hmp 4.75 Re"`). **No `alertCDirectionCoded` fallback.**

Measured against the real site table (`measurement_current2.xml`, 101,487 sites):

```
total sites:              101,487
with carriageway resolved:  23,686  (23.34%)
```

Per-prefix breakdown (non-zero only):

| prefix | total | resolved | % |
|---|---|---|---|
| RWS08 | 14,254 | 11,413 | 80.1% |
| RWS01 | 22,118 | 9,503 | 43.0% |
| GEO0C | 734 | 642 | 87.5% |
| PZH01 | 646 | 643 | 99.5% |
| GEO0K | 619 | 615 | 99.4% |
| GEO0B | 606 | 522 | 86.1% |
| GEO2A | 291 | 138 | 47.4% |
| GEO1A | 241 | 210 | 87.1% |

**Every other prefix resolves 0%** — including `PNH02` (459 sites, the
Provincialeweg sensors from this investigation), `PNH03`, `POV01`, `PUT01/03`,
`PLB01/02`, `GDH01`, `PZH03/04`, `GEH01`, `GAD03`, `RDH01/05/06`, `PGR02/08`,
`GRT02/03/04/06`, `PFR02/07`, `PFL01/02`, `RTT01`, `GUT01`, `HBR01/04`,
`GMS01`, `GZS01`, `PNB03/05`, `SRR02`, `GAD02`, `PLB05`, `GRT06` — because
their site names don't match the `"N457 hmp 4.75 Re"` pattern (they use a
free-text description like `"Wormerveer N203 Provincialeweg, Middelweg - …"`)
and there's no fallback for the `else` branch.

### The fix is already proven — just not applied everywhere

Of the 76.66% (77,801 sites) with no carriageway, **100% have
`alertCDirectionCoded` filled** (`tmc_direction` in the parsed dict). Zero
sites are truly unresolvable:

```
cw resolved now:                                    23,686  (23.34%)
cw None but tmc_direction present (recoverable):     77,801  (76.66%)
cw None AND no tmc_direction (truly unresolvable):        0  ( 0.00%)
```

The `GEO*`/`RWS01` branches already prove the fallback works
(`carriageway = "R" if alc_dir == "positive" else "L" if alc_dir == "negative" else None`).
Adding the same fallback to the `RWS08` branch and the catch-all `else`
branch would take carriageway resolution from **23.34% → 100%** with no new
data source — the value is already sitting in the same XML record.

---

## Fields that exist in the DB but aren't surfaced

`MeasurementSite` ([src/ndwinfo/models.py:35](../src/ndwinfo/models.py)) already
has the columns needed — this is a plumbing gap, not a missing-data gap:

- **`tmc_direction`** (`positive`/`negative`, 100% filled for traffic-speed
  sites) — populated by the parser and written to the DB
  ([src/ndwinfo/ingest/measurement.py](../src/ndwinfo/ingest/measurement.py)),
  but **never `SELECT`ed** in `/api/traffic/speed`
  ([src/ndwinfo/api/routers/traffic.py](../src/ndwinfo/api/routers/traffic.py))
  and never read in the frontend (`web/speed.js`, `web/lib.js`). Dead column.
- **`tmc_primary`** (VILD/ALERT-C location code, e.g. 10798) — also populated
  for every site (not just travel-time routes) but unused outside the
  travel-time geometry rebuild.
- **`openlr_bearing`** — already selected by the API and used as a fallback
  bearing ([traffic.py:469](../src/ndwinfo/api/routers/traffic.py)), but only
  filled for **2.11%** of sites (2,143 / 101,487) — too sparse to rely on for
  this problem. None of the sites in this investigation (`PNH02_*`,
  `RWS01_MONIBAS_*`, `GEO0B_*`) have it.

Nothing needs to be added to the schema. `carriageway` needs to be
**computed** (parser fix) and `tmc_direction` needs to be **exposed**
(API + frontend) for what's already ingested.

---

## Recommendation: how to compute a real bearing, not just L/R

`carriageway` (R/L) tells you *which* side of the road a sensor is on, but not
a compass bearing for arrows/rotation in the UI. `openlr_bearing` is too
sparse (2.11%) to be the primary source. A robust bearing can be derived from
data already ingested, no new feed required:

1. `measurement_site.tmc_primary` → look up in `vild_tmc.loc_nr` → get
   `lin_ref` (→ `vild_line.id`).
2. Load that `vild_line` row's `LineString` geometry.
3. Find the point on the line closest to the site's own `geom` (or use
   `HSTART_POS`/`HEND_POS` proportionally along the line's total length from
   the VILD master table) to get a local segment of the line.
4. Compute the **tangent bearing** at that point from the line's coordinate
   sequence (bearing between the two neighbouring vertices).
5. If `tmc_direction == "negative"`, **flip the tangent 180°** — the line's
   own digitization direction is the `positive` direction by definition (same
   logic already established for the `POS_OFF`/`NEG_OFF` chain).

This mirrors what `rebuild_traveltime_geometry()`
([src/ndwinfo/ingest/traveltime_geometry.py](../src/ndwinfo/ingest/traveltime_geometry.py))
already does to build road-following `line_geom` for travel-time segments from
the same VILD chain — the machinery to walk/clip `vild_line` geometry already
exists, it just isn't reused for point-site bearing yet.

Caveat already on record: WEGGEG-derived bearing is known to be unreliable
for offset calculations (see prior memory note) — this VILD-line-tangent
approach is a **different, independent geometry source** and shouldn't
inherit that problem, but should still be spot-checked against a few known
locations before trusting it everywhere.

---

## Action items

1. **Parser fix** ([datex_v2.py](../src/ndwinfo/parsers/datex_v2.py)): add the
   `alertCDirectionCoded` → `R`/`L` fallback to the `RWS08` branch and the
   catch-all `else` branch, matching what `GEO*`/`RWS01` already do. Takes
   carriageway resolution from 23.34% → ~100% (traffic-speed feed only; no
   multi-value ambiguity to handle there, confirmed above).
2. **API**: add `tmc_direction` to the `/api/traffic/speed` response
   ([traffic.py](../src/ndwinfo/api/routers/traffic.py)) so the frontend has a
   direction signal even before/independent of the carriageway fix.
3. **Frontend** (`web/speed.js`, `web/lib.js`): show `tmc_direction` and/or the
   resolved `carriageway` in the marker title/popup so co-located
   opposite-direction sensors (like the N203 Uitgeest pair) are distinguishable.
4. **Optional, larger**: implement the VILD-line-tangent bearing computation
   above for a real compass bearing per site, reusing the chain-walking logic
   from `traveltime_geometry.py`.
