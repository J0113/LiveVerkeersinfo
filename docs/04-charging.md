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
- **Postgres (PostGIS)**: `charge_point` (`id` PK, `cpo_id`, `address`, `city`,
  `operator_name`, `owner_name`, `open`, `last_updated`, `geom` POINT, `raw`
  JSONB) + `charge_availability` (`cp_id` FK + `idx` PK, `total`, `available`,
  `power_max`, `power_type`, `connector_type`, `connector_format`, `tariff_ids`
  text[]).

### `GET /api/charging` response fields (derived, not in the raw feed)
Alongside the raw per-connector `availability[]` array, each feature also
carries two summed scalars for cheap client-side use (e.g. driving marker
color without reducing a nested array in a MapLibre style expression):
- `available_count`: sum of `availability[].available` across all connector
  groups; `null` if the station has no availability rows at all (unknown),
  a real int (possibly `0`) otherwise.
- `connector_total`: same sum over `total`.

Note `open` is **not** a reliable availability proxy — the sample above has
`open: false` while reporting 3 available connectors — so the web UI colors
markers off `available_count`/`connector_total`, not `open`.

---

## charging_point_locations_ocpi.json.gz — OCPI locations (full)

> **Not currently ingested.** The `charging_ocpi` feed is registered in
> `src/ndwinfo/feeds.py` (60s cadence, so it's downloaded on schedule) but
> there is no parser or ingester for it — `src/ndwinfo/parsers/geojson_ocpi.py`
> only has `parse_charging_geojson` and `parse_ocpi_tariffs`, and
> `src/ndwinfo/ingest/__init__.py`'s `INGESTERS` registry has no
> `charging_ocpi` entry. The file is fetched and then discarded. The GeoJSON
> feed above already covers the area-query use case (live availability +
> geometry), which is presumably why this was never wired up — but treat the
> rest of this section as a description of the raw feed, not of anything the
> app stores.

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
- **Postgres**: none today (see note above). If this is ever wired up, normalized
  `ocpi_location` / `ocpi_evse` / `ocpi_connector` tables (or raw JSONB with
  projected columns) would be the natural shape. For the area-query app the
  GeoJSON file is usually enough.

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
- **Postgres**: `tariff` (`id` PK, `currency`, `party_id`, `country_code`,
  `elements` JSONB, `last_updated`, `raw` JSONB).
