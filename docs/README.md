# NDW Open Data — Dataset Catalog

Source portal: <https://opendata.ndw.nu/>

NDW (Nationaal Dataportaal Wegverkeer) publishes near-real-time Dutch road
traffic data as flat files covering the **entire Netherlands**. Files are
overwritten in place on a fixed cadence (no API, no history — just the latest
snapshot per file). This catalog documents every published file, its format,
schema, geographic scope, and how the feeds relate to each other.

## How the portal works

- **Delivery**: plain HTTPS GET of a static file at `https://opendata.ndw.nu/<filename>`.
- **Compression**: most files are gzip (`.gz`). A few are raw `.xml`, `.zip`.
- **Refresh**: each file is replaced on its own schedule (see per-file docs).
  Real-time situation/measurement feeds update roughly **every 60 seconds**;
  reference tables hourly→daily; sign/static datasets daily→on-change.
- **Coverage**: national. There is no built-in area filtering — the consumer
  must clip by bounding box / polygon after ingest. (That clipping is the whole
  point of the project in [/CLAUDE.md](../CLAUDE.md).)
- **No auth**: fully open data.

## Common formats & concepts

| Concept | Meaning |
|---|---|
| **DATEX II v2** | EU traffic-data XML standard, namespace `http://datex2.eu/schema/2/2_0`, wrapped in a SOAP envelope (`SOAP:Envelope`). Used by the measurement/speed/traveltime feeds. |
| **DATEX II v3** | Newer EU standard, namespace `http://datex2.eu/schema/3/*`, root `mc:messageContainer`. Used by situation, VMS, parking-status, emission-zone feeds. |
| **SituationPublication** | DATEX collection of `situation` → `situationRecord` items (incidents, roadworks, closures, speed orders…). The `xsi:type` of each record (`VehicleObstruction`, `MaintenanceWorks`, `SpeedManagement`, `RoadOrCarriagewayOrLaneManagement`, …) defines its meaning. |
| **MeasurementSiteTable** | Static-ish table describing every sensor location/lane. Real-time value feeds reference it by `id` + `index` instead of repeating geometry. **Join key** between geometry and live values. |
| **RD (EPSG:28992)** | "Rijksdriehoek", the Dutch national projected CRS (metres). Coordinates `rdX/rdY`. |
| **WGS84 (EPSG:4326)** | lat/lon degrees. Used by GeoJSON `wgs84` variants and most DATEX coordinates. |
| **VILD** | NDW location-code reference (roads/points/areas) shipped as shapefiles + code list. |

## File index

### Real-time traffic measurement → [01-traffic-realtime.md](01-traffic-realtime.md)
| File | Format | ~Size | Refresh |
|---|---|---|---|
| `measurement.xml.gz` | DATEX II v2 MeasurementSiteTable | 11M | hourly |
| `measurement_current.xml.gz` | DATEX II v2 MeasurementSiteTable (current) | 11M | hourly |
| `trafficspeed.xml.gz` | DATEX II v2 MeasuredDataPublication (flow+speed) | 1.1M | ~60s |
| `traveltime.xml.gz` | DATEX II v2 MeasuredDataPublication (travel times) | 2.5M | ~60s |
| `actueel_beeld.xml.gz` | DATEX II v3 SituationPublication (live incidents) | 401K | ~60s |
| `veiligheidsgerelateerde_berichten_srti.xml.gz` | DATEX II v3 SituationPublication (SRTI safety) | 26K | ~60s |

### Signs & VMS → [02-signs-vms.md](02-signs-vms.md)
| File | Format | ~Size | Refresh |
|---|---|---|---|
| `Matrixsignaalinformatie.xml.gz` | NDW VMS XML (matrix signal events) | 1.0M | ~60s |
| `dynamische_route_informatie_paneel.xml.gz` | DATEX II v3 VmsPublication (DRIPs) | 601K | ~60s |
| `ndw_msi_shapefiles_latest.zip` | Shapefile (MSI sign geometry) | 1.0M | ~weekly |

### Roadworks, closures & zones → [03-roadworks-measures.md](03-roadworks-measures.md)
| File | Format | ~Size | Refresh |
|---|---|---|---|
| `planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz` | DATEX II v3 (roadworks+events) | 21M | ~daily/15min |
| `planningsfeed_brugopeningen.xml.gz` | DATEX II v3 (bridge openings) | 123K | ~60s |
| `tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz` | DATEX II v3 (temp closures) | 271K | ~60s |
| `tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz` | DATEX II v3 (temp speed limits) | 21K | ~60s |
| `emissiezones.xml.gz` | DATEX II v3 ControlledZoneTable | 122K | ~daily |

### EV charging → [04-charging.md](04-charging.md)
| File | Format | ~Size | Refresh |
|---|---|---|---|
| `charging_point_locations.geojson.gz` | GeoJSON (charge points + availability) | 3.3M | ~60s |
| `charging_point_locations_ocpi.json.gz` | OCPI JSON (locations/EVSEs/connectors) | 17M | ~60s |
| `charging_point_tariffs_ocpi.json.gz` | OCPI JSON (tariffs) | 3.2M | ~hourly |

### Truck parking → [05-truckparking.md](05-truckparking.md)
| File | Format | ~Size | Refresh |
|---|---|---|---|
| `Truckparking_Parking_Table.xml` | DATEX II v2 ParkingTable (static) | 49K | ~daily |
| `Truckparking_Parking_Status.xml` | DATEX II v3 ParkingStatus (occupancy) | 18K | ~60s |

### Traffic signs (verkeersborden) → [06-verkeersborden.md](06-verkeersborden.md)
| File | Format | ~Size | Refresh |
|---|---|---|---|
| `verkeersborden_actueel_beeld.csv.gz` | CSV (all road signs NL) | 235M | ~daily |
| `verkeersborden_actueel_beeld_rd.geojson.gz` | GeoJSON RD (EPSG:28992) | 228M | ~daily |
| `verkeersborden_actueel_beeld_wgs84.geojson.gz` | GeoJSON WGS84 | 236M | ~daily |

### Static reference → [07-static-reference.md](07-static-reference.md)
| File | Format | ~Size | Refresh |
|---|---|---|---|
| `ndw_avg_meetlocaties_shapefile.zip` | Shapefile (count points + measurement segments) | 71M | ~weekly |
| `VILD6.13.A.zip` | Shapefiles + code list + docs (location reference) | 40M | on release |
| `VILD6.12.A.zip` | Previous VILD release | 40M | archived |

### Runtime performance → [09-performance.md](09-performance.md)

Container dependency split, API compression/cache policy, spatial matching,
browser scheduling, and operational resource tuning.

### Carriageway/direction data quality → [10-carriageway-direction-quality.md](10-carriageway-direction-quality.md)

How `alertCDirectionCoded` + the VILD TMC chain establish travel direction,
how explicit and derived R/L remain separate, and how the implemented local
tangent enrichment handles unresolved cases.

### ANWB incidents (non-NDW source) → [12-anwb-incidents.md](12-anwb-incidents.md)

Jams, roadworks, and dynamic speed camera reports from ANWB's own JSON API.

### Flitspalen static speed cameras (non-NDW source) → [13-flitspalen-speedcameras.md](13-flitspalen-speedcameras.md)

Crowdsourced fixed/permanent speed camera locations from flitspalen.nl.

### OpenStreetMap driving roads (non-NDW source) → [11-osm-pbf.md](11-osm-pbf.md)

| File | Format | ~Size | Refresh |
|---|---|---|---|
| Geofabrik `netherlands-latest.osm.pbf` | OSM PBF, ODbL-licensed | ~1.3G | ~weekly |

`highway=motorway,trunk,primary,secondary` (+ `_link` variants) only, all
OSM tags stored. This is the production road and lane source used for speed
matching; it is not part of the NDW catalog above.

> Sizes are compressed download sizes from the portal listing on 2026-05-29.
> Decompressed sizes are much larger — e.g. `traveltime.xml.gz` 2.5M → ~73M XML,
> `trafficspeed.xml.gz` 1.1M → ~52M XML.
