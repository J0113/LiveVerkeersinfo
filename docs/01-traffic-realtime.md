# 01 — Real-time traffic measurement

Core feeds for live traffic state: where the sensors are (site table), and what
they currently measure (flow, speed, travel time). Plus live incident feeds.

---

## measurement.xml.gz / measurement_current.xml.gz — MeasurementSiteTable

- **Format**: DATEX II **v2** XML, SOAP-wrapped. Namespace `http://datex2.eu/schema/2/2_0`.
- **Payload type**: `MeasurementSiteTablePublication`.
- **Decompressed**: ~50M+. **Refresh**: ~hourly (table version increments, e.g. `version="1712"`).
- **Role**: the **static catalog of every measurement location** on the Dutch
  road network. Real-time value feeds (`trafficspeed`, `traveltime`) do NOT
  carry geometry — they reference a row here by `id`. This file is the geometry
  + metadata join table. NDW publishes both `measurement.xml.gz` and
  `measurement_current.xml.gz` as (near-)identical snapshots, but only
  `measurement_current.xml.gz` is actually polled — see the `measurement_site`
  feed in `src/ndwinfo/feeds.py`.

### Structure
```
d2LogicalModel
└─ payloadPublication (MeasurementSiteTablePublication)
   └─ measurementSiteTable id="NDW01_MT" version=N
      └─ measurementSiteRecord id="PZH01_MST_0629_00" version=…   (one per site)
         ├─ measurementSiteRecordVersionTime
         ├─ computationMethod                  (e.g. arithmeticAverageOfSamplesInATimePeriod)
         ├─ measurementEquipmentReference      (e.g. 0629_00)
         ├─ measurementEquipmentTypeUsed       (e.g. "lus" = inductive loop)
         ├─ measurementSiteName                (e.g. "N457 hmp 4.75 Re")
         ├─ measurementSiteNumberOfLanes
         ├─ measurementSide                    (compass bound, e.g. northWestBound)
         ├─ measurementSpecificCharacteristics index="i"   (one per measured index)
         │  ├─ accuracy, period (s, e.g. 60)
         │  ├─ specificLane                    (e.g. lane1)
         │  ├─ specificMeasurementValueType    (trafficFlow | trafficSpeed | …)
         │  └─ specificVehicleCharacteristics  (length class buckets, e.g. <5.6m)
         └─ measurementSiteLocation            (point/linear DATEX location, WGS84)
```

### Join key (critical)
`siteMeasurements/measuredValue[@index="i"]` in the value feeds maps to
`measurementSiteRecord/measurementSpecificCharacteristics[@index="i"]` here, for
the matching `measurementSiteReference/@id`. The `index` tells you which lane +
vehicle-length class + value type the live number belongs to.

### Postgres model
`measurement_site` (`src/ndwinfo/models.py`): `id` PK, `name`, `equipment_type`,
`num_lanes`, `side`, `version`, `record_version_time`, `road`, `carriageway`,
`carriageway_source`, `vild_carriageway(_source)`, `carriageway_direction_conflict`,
`km`, `openlr_bearing`, `vild_bearing`, `geom` (POINT, WGS84), `line_geom`
(LINESTRING — road-following travel-time segment, built from the VILD TMC chain
when resolvable, else the straight start→end chord; see
`ingest/traveltime_geometry.py`).

Three fields carry the **resolved** road/carriageway that API queries actually
filter/index on — `effective_road`, `effective_carriageway`, `effective_source`
— set by `ingest.vild_direction.resolve_effective_road` with precedence
explicit → VILD-derived → inherited from a co-located sibling site (see
[docs/10](10-carriageway-direction-quality.md) and CLAUDE.md). The raw
`road`/`carriageway` columns hold whatever NDW/VILD reported before that
resolution step; don't query them directly.

---

## trafficspeed.xml.gz — live flow & speed

- **Format**: DATEX II **v2** XML, SOAP-wrapped. Payload `MeasuredDataPublication`.
- **Decompressed**: ~52M. **Refresh**: ~60s. **Coverage**: national, all sites.
- References `measurementSiteTableReference id="NDW01_MT"` — geometry via the site table above.

### Structure
```
payloadPublication (MeasuredDataPublication)
├─ publicationTime
├─ measurementSiteTableReference id="NDW01_MT" version=…
└─ siteMeasurements                                  (one per site, MANY)
   ├─ measurementSiteReference id="PZH01_MST_0065_00" version=…
   ├─ measurementTimeDefault                         (timestamp of the readings)
   └─ measuredValue index="i"                        (one per characteristic index)
      └─ measuredValue/basicData xsi:type=…
         ├─ TrafficFlow → vehicleFlow/vehicleFlowRate   (vehicles/hour)
         └─ TrafficSpeed → averageVehicleSpeed/speed    (km/h; -1 = no data,
                            attr numberOfInputValuesUsed)
```

### Notes
- `speed = -1` or `numberOfInputValuesUsed="0"` ⇒ no valid measurement, treat as null.
- Each site emits several indexed values (per lane × length class × flow/speed).
- Fixed speed sites receive a VILD-derived travel bearing from the local road
  tangent oriented by `tmc_direction`. OSM lane candidates must be directional,
  non-connector major-road lanes within 25m and 45°, with no conflicting road
  reference. Exact road reference and lane-count agreement rank ahead of angle
  and distance; ambiguous candidates remain point-only.
- Opposite directions at exactly the same coordinate remain separate in the
  aggregation key. Point fallbacks are offset to the driver's right of the
  signed bearing, so both remain clickable.
- **Ingest tip**: stream-parse (SAX/iterparse); the decompressed doc is large.

### Map driving HUD

The `Traffic Speed` layer is rendered as a pinned, glanceable HUD and as OSM
lane ribbons. It uses the same signed VILD bearings for direction filtering and
`osm_lane_count` for grouping, while keeping official NDW lane numbers attached
to their speed and flow values. Measured-speed colors are scaled against the
directional OSM `maxspeed`; unknown or symbolic limits use the neutral unknown
color. The GPS dial resolves its current road from the matched lane geometry,
falling back to a small `/api/osm/lanes` query when no live-speed lane is nearby,
and shows a numeric OSM limit as a compact overlapping sign. There is
deliberately no speed-lane fallback for an ambiguous, adjacent, or opposite
road; missing measurement context stays point-only.
The adjacent DRIP/VMS panel uses the same selection rule and displays the
nearest message without changing the road-following lane visualization.

#### Current-road context

The two speed displays — the "snelheid per rijstrook" tile and the left speed
bar — are scoped by one **road context**, resolved in two halves:

| part | source |
|---|---|
| road (`A9`) | OSM lane map-matching in the client (`selectCurrentOsmLane`) |
| carriageway (`R`/`L`) | `GET /api/traffic/road-context` — nearest `measurement_site` on that road, direction-checked against `vild_bearing` |
| our hectometre (`anchor_km`) | same endpoint: the nearest site's `km`, walked back along the direction of travel by its along-track offset, so it describes the **vehicle's** position |

`anchor_distance_m` reports how far away the anchor site was, so the client can
reject a context too weak to place sensors (>500 m, i.e. a sparsely instrumented
stretch). This NDW/VILD context is used only to select speed sensors. The road
label under the speed dial reads the official `carriageway_ref` directly from
the map-matched OSM way, so it can show `A9 • Re`, `A9 • Li`, or other published
letter references without waiting for an NDW measurement-site match.

#### Selecting what is ahead

Upcoming sensors are fetched with `road` + `carriageway` + `km_min`/`km_max`
around the anchor (10 km ahead, 0.5 km behind) — **not** a forward bounding box.
Distance ahead is then the signed hectometre difference: hectometrering runs
with the direction of travel on carriageway `R` and against it on `L`
(see [docs/10](10-carriageway-direction-quality.md)), so it measures distance
*along the road* and neither the fetch nor the selection loses a sensor round a
bend the way a straight corridor does. No bearing or cross-track gate is applied
to hectometre-placed sensors: on a curve the road's bearing legitimately differs
from ours, and road + carriageway already pin the direction.

Guards, because a hectometre is only trustworthy if it is *ours*:

- a road can only be longer than the chord it spans, so a hectometre distance
  far below the straight-line distance (or far above it) is rejected — this
  catches hectometrering resets at province boundaries and stray `km` values on
  co-located sites. The tolerance scales with `anchor_distance_m`, since a
  distant anchor is legitimately less precise;
- sites with no usable `km` are omitted: the kilometre-bounded request cannot
  retrieve them safely without reintroducing a separate geometric candidate pool;
- `motorway_link` sites are excluded from both displays: they carry the road's
  reference and hectometrering but measure ramp traffic, not our carriageway.

Both displays render **only** with a resolved context and real readings — there
is no geometric fallback pool, since the alternative to showing nothing is
showing another road's traffic. Cached sensors are keyed by road *and*
carriageway and fenced by a request generation, so a U-turn drops them
immediately and a late response from the previous direction is discarded.

### Postgres model
`traffic_measurement` (`site_id` + `index` PK, `measured_at`, `value_type`,
`flow_veh_h`, `speed_kmh`, `n_inputs`, `std_dev`, `raw` JSONB) — upsert latest
per site+index, no history retained.

---

## traveltime.xml.gz — travel times

- **Format**: DATEX II **v2**, `MeasuredDataPublication`. **Decompressed** ~73M. **Refresh** ~60s.
- Sites here are **route segments** (id e.g. `PNB05_BRE_Keizerstraat_N01`), referencing the same `NDW01_MT` table family.

### Structure (per measuredValue)
```
basicData xsi:type="TravelTimeData"
├─ travelTimeType                 (e.g. reconstituted)
├─ travelTime accuracy=… numberOfInputValuesUsed=… supplierCalculatedDataQuality=…
│  └─ duration                    (seconds, float)
└─ measuredValueExtension/…/basicDataReferenceValue
   └─ referenceValueType=staticReferenceValue
      └─ travelTimeData/travelTime/duration   (free-flow/reference duration, s)
```

### Notes
- Compare live `duration` vs the `staticReferenceValue` reference duration to
  derive congestion/delay ratio.
- **Postgres**: `travel_time` (segment_id, measured_at, duration_s, ref_duration_s, accuracy, n_inputs).

---

## actueel_beeld.xml.gz — live situations (incidents / "actueel beeld")

- **Format**: DATEX II **v3** XML, root `mc:messageContainer`. Payload `sit:SituationPublication`.
- **Decompressed** ~3.6M. **Refresh** ~60s. The live national incident picture.

### Structure
```
mc:messageContainer
└─ mc:payload (sit:SituationPublication)
   └─ sit:situation id=…
      ├─ sit:overallSeverity                 (low|medium|high|unknown)
      ├─ sit:situationVersionTime
      └─ sit:situationRecord xsi:type=…       (the typed event)
         ├─ creation/version times
         ├─ sit:probabilityOfOccurrence       (certain|probable|riskOf…)
         ├─ sit:safetyRelatedMessage          (true/false)
         ├─ sit:source/com:sourceName
         ├─ sit:validity/com:validityTimeSpecification/com:overallStartTime[/End]
         └─ sit:locationReference xsi:type="loc:PointLocation"
            └─ loc:pointByCoordinates (bearing, lat, lon) + loc:alertCPoint (TMC)
```
- Record types seen: `VehicleObstruction` and other DATEX situation subtypes.
- **Postgres**: `situation` (`record_id` PK, `id` — situation grouping id,
  `category` — incident|srti|roadworks|…, `record_type` — `xsi:type` stripped,
  `severity`, `probability`, `safety_related`, `source`, `valid_from`,
  `valid_to`, `version_time`, `speed_limit_kmh`, `geom` GEOMETRY (WGS84,
  point or line), `raw` JSONB) — one shared table for all DATEX v3 situation
  feeds, keyed by `category`/`record_type` rather than a plain `type` column.
  There is no separate `lat`/`lon`/`bearing` column; position lives in `geom`.

---

## veiligheidsgerelateerde_berichten_srti.xml.gz — SRTI safety messages

- **Format**: identical DATEX II **v3** `sit:SituationPublication` schema as `actueel_beeld`.
- **Decompressed** ~0.6M. **Refresh** ~60s.
- **Subset**: only **Safety-Related Traffic Information** (`safetyRelatedMessage=true`)
  — the EU-mandated SRTI categories (obstructions, hazards, etc.). Same fields as
  above; reuse the same parser and `situation` table (flag `srti=true`).
