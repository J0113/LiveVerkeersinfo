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
  + metadata join table. `measurement_current` is the live/current version;
  `measurement` is effectively the same snapshot.

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

### Postgres model (suggested)
- `measurement_site` (id PK, name, equipment_type, num_lanes, side, geom POINT/LINESTRING, version)
- `measurement_characteristic` (site_id FK, index, lane, period_s, value_type, veh_length_min, veh_length_max)

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
- **Ingest tip**: stream-parse (SAX/iterparse); the decompressed doc is large.

### Postgres model
`traffic_measurement` (site_id, index, measured_at, value_type, value) — append
per cycle or upsert latest; partition/retain as needed.

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
- **Postgres**: `situation` (id, type, severity, prob, safety, start, end,
  source, lat, lon, bearing, raw JSONB) — generic table keyed by `xsi:type`.

---

## veiligheidsgerelateerde_berichten_srti.xml.gz — SRTI safety messages

- **Format**: identical DATEX II **v3** `sit:SituationPublication` schema as `actueel_beeld`.
- **Decompressed** ~0.6M. **Refresh** ~60s.
- **Subset**: only **Safety-Related Traffic Information** (`safetyRelatedMessage=true`)
  — the EU-mandated SRTI categories (obstructions, hazards, etc.). Same fields as
  above; reuse the same parser and `situation` table (flag `srti=true`).
