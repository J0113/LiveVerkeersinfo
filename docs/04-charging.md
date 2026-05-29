# 04 — EV charging infrastructure

Three views of the same domain: a lightweight GeoJSON for mapping, a full OCPI
locations dump, and OCPI tariffs. Linked by `tariff_ids`.

---

## charging_point_locations.geojson.gz — charge points + live availability

- **Format**: GeoJSON `FeatureCollection`, gzip. **Decompressed** ~tens of MB. **Refresh** ~60s.
- **Geometry**: `Point`, WGS84 `[lon, lat]`.
- **Best file for the map/area-query use case** (compact, has live availability).

### Feature properties
```json
{
  "open": false,
  "cpo_id": "LMS",
  "address": "Hadewijchlaan 65",
  "country": "NLD",
  "owner_name": "EQUANS",
  "operator_name": "EQUANS",
  "suboperator_name": "EQUANS",
  "last_updated": "2026-05-29T08:20:04Z",
  "availabilities": [
    { "total": 6, "available": 3, "power_max": 11040.0, "power_type": "AC3",
      "tariff_ids": ["471297504"], "connector_type": "IEC_62196_T2",
      "connector_format": "SOCKET" }
  ]
}
```
- `id` e.g. `NL-LMS-91161551`. `availabilities[]` gives live `available`/`total`
  per connector group; `tariff_ids` → tariffs file.
- **Postgres (PostGIS)**: `charge_point` (id PK, geom POINT, cpo_id, address,
  operator, last_updated) + `charge_availability` (cp_id FK, total, available,
  power_max, power_type, connector_type, connector_format, tariff_ids text[]).

---

## charging_point_locations_ocpi.json.gz — OCPI locations (full)

- **Format**: JSON **array** of OCPI `Location` objects, gzip. **Decompressed** large (17M gz). **Refresh** ~60s.
- **Content**: full OCPI model — locations → EVSEs → connectors, richer than the GeoJSON.

### Shape
```json
[{
  "id": "2ba055a4-…", "city": "Stellendam", "name": "DWB - … - Stellendam",
  "evses": [{
    "uid": "8aad6100-…", "status": "AVAILABLE", "evse_id": "NLBLKEVm0p010001",
    "connectors": [{ "id":"1","format":"SOCKET","standard":"IEC_62196_T2",
       "power_type":"AC_3_PHASE","tariff_ids":["0b2e71df…"],
       "max_voltage":260,"max_amperage":32,"max_electric_power":null,
       "last_updated":"2026-02-09T11:04:11Z" }],
    "capabilities":["REMOTE_START_STOP_CAPABLE","RFID_READER","UNLOCK_CAPABLE"],
    "physical_reference":"0259-CO02-000147", "last_updated":"…"
  }]
}]
```
- Note: top-level `coordinates` can be `null` here (geometry better in the GeoJSON file).
- Use only if you need full EVSE/connector detail or live `status` per EVSE.
- **Postgres**: `ocpi_location` / `ocpi_evse` / `ocpi_connector` normalized tables,
  or store raw JSONB and project columns. For the area-query app the GeoJSON file
  is usually enough.

---

## charging_point_tariffs_ocpi.json.gz — OCPI tariffs

- **Format**: JSON **array** of OCPI `Tariff` objects, gzip. **Refresh** ~hourly.

### Shape
```json
[{
  "id": "t-681a16af…-1", "country_code": "NL", "party_id": "EFL",
  "currency": "EUR",
  "elements": [{ "price_components": [
     {"type":"FLAT","price":0.0,"step_size":1},
     {"type":"TIME","price":0.0,"step_size":1},
     {"type":"ENERGY","price":0.4,"step_size":1}], "restrictions": null }],
  "last_updated": "2026-05-28T11:12:06.854Z"
}]
```
- Join: connector/availability `tariff_ids[]` → `tariff.id`.
- **Postgres**: `tariff` (id PK, currency, party_id, elements JSONB, last_updated).
