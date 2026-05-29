# 05 — Truck parking

Two files: a static table of parking sites (geometry + capacity) and a live
occupancy/status feed. Joined by parking record id under table `NL-12`.

---

## Truckparking_Parking_Table.xml — parking sites (static)

- **Format**: DATEX II **v2** XML (raw, **not** gzipped). Payload `GenericPublication`
  wrapping `parkingTablePublication`. **Refresh** ~daily. Note `informationStatus=test`.
- **Content**: inventory of (truck) parking sites: name, operator, location, capacity.

### Structure
```
d2LogicalModel
└─ payloadPublication (GenericPublication, genericPublicationName=ParkingTablePublication)
   └─ parkingTablePublication
      └─ parkingTable id="NL-12" version=N
         └─ parkingRecord id="NL-12_421" xsi:type="InterUrbanParkingSite"
            ├─ parkingName               (e.g. "Truckstop Venlo")
            ├─ parkingRecordVersionTime
            ├─ operator (ContactDetails)
            └─ parkingLocation xsi:type="Point…"   (coordinates + capacity fields)
```
- **Postgres**: `truck_parking` (id PK e.g. `NL-12_421`, name, operator,
  capacity, geom POINT, version).

---

## Truckparking_Parking_Status.xml — live occupancy

- **Format**: DATEX II **v3** XML (raw, not gzipped). Payload `ns2:ParkingStatusPublication`. **Refresh** ~60s.
- **Content**: live vacant/occupied counts per parking record; references the
  static table above by id.

### Structure
```
ns3:payload (ParkingStatusPublication)
├─ parkingTableReference id="NL-12" version=97
└─ parkingRecordStatus xsi:type="ParkingSiteStatus"
   ├─ parkingRecordReference id="NL-12_8"            ← join to table
   ├─ parkingStatusOriginTime
   └─ parkingOccupancy
      ├─ parkingNumberOfVacantSpaces     (e.g. 213)
      ├─ parkingNumberOfOccupiedSpaces   (e.g. 17)
      └─ parkingOccupancy                (percent, e.g. 8.0)
   └─ groupOfParkingSpacesStatus…        (per sub-group breakdown)
```
- **Postgres**: `truck_parking_status` (parking_id FK, origin_time, vacant,
  occupied, occupancy_pct) — upsert latest or append timeseries.
- **Join**: `parkingRecordReference/@id` (status) = `parkingRecord/@id` (table).
