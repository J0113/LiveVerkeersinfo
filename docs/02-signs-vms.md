# 02 — Signs & Variable Message Signs (VMS)

Live state of overhead matrix signals and dynamic route panels, plus the static
geometry of matrix signs.

---

## Matrixsignaalinformatie.xml.gz — matrix signal events (MSI)

- **Format**: NDW-proprietary XML, SOAP-wrapped. Root `ndw:NdwVms`, body namespace
  `http://variable_message_sign.trafficmanagementinfo.publicatie.hwn.rws.nl/1.1`.
  **Not** standard DATEX.
- **Decompressed** ~9M. **Refresh** ~60s (`updateMethod=snapshot`).
- **Content**: per-lane overhead matrix signs above motorways (speed limits,
  lane closes/arrows, blanks).

### Structure
```
ndw:NdwVms
└─ variable_message_sign_events
   ├─ meta/msg_id/uuid
   └─ event                                  (MANY; two kinds, paired by sign_id)
      ├─ ts_event, ts_state                  (timestamps)
      ├─ sign_id/uuid                         (the physical sign)
      ├─ lanelocation                         (location event)
      │  ├─ road    (e.g. A2)
      │  ├─ carriageway (L/R)
      │  ├─ lane    (lane number)
      │  └─ km      (hectometre, e.g. 134.96)
      └─ display                              (state event)
         ├─ blank flashing="false"
         └─ speedlimit flashing="false" red_ring="true">80<   (and arrows etc.)
```

### Notes
- Each `sign_id` appears as **two event types**: a `lanelocation` (static-ish
  position) and a `display` (current shown aspect). Some location records carry
  stale `ts_event` (2025) while display records are fresh — join by `sign_id/uuid`,
  take latest `display` per sign.
- Geometry (lat/lon) of these signs is in `ndw_msi_shapefiles_latest.zip` (below),
  keyed by sign UUID.
- **Postgres**: `msi_sign` (uuid PK, road, carriageway, lane, km, `bearing` —
  road heading at the sign from the shapefile, used to offset the sign
  perpendicular to the road, geom) + `msi_state` (uuid FK, ts_state,
  aspect_type, value, flashing, red_ring).

---

## dynamische_route_informatie_paneel.xml.gz — DRIPs

- **Format**: DATEX II **v3**, root `mc:messageContainer`, payload `vms:VmsTablePublication`.
- **Decompressed** ~5M. **Refresh** ~60s.
- **Content**: dynamic route information panels (text/graphic roadside boards).

### Structure
```
mc:payload (vms:VmsTablePublication)
└─ vms:vmsControllerTable id="NDW01_VMS_DRIP"
   └─ vms:vmsController id=… version=…
      ├─ vms:numberOfVms
      └─ vms:vms vmsIndex="1"
         └─ vms:vms
            ├─ vms:description           (com:value, e.g. "BD26-09 Burg Matsersingel oost")
            ├─ vms:physicalSupport       (e.g. roadsideMounted)
            ├─ vms:vmsType               (e.g. colourGraphic)
            ├─ vms:vmsConfiguration/numberOfDisplayAreas
            └─ vms:vmsLocation xsi:type="loc:PointLocation"
               └─ loc:pointByCoordinates (bearing, lat, lon)
```

Live display state is **not** nested under the inventory `vms:vms` element —
it comes from a separate top-level branch, joined back to the same controller:
```
vms:vmsControllerTable
└─ vms:vmsControllerStatus                (MANY, one per controller)
   └─ vms:vmsStatus (outer) → vms:vmsStatus (inner, per vmsIndex)
      ├─ vms:workingStatus                (e.g. ok/fault)
      └─ vms:vmsMessage/vms:vmsMessage
         └─ …message lines… → joined into display_text
```
- **Postgres**: `drip` (`controller_id` + `vms_index` PK, `description`,
  `vms_type`, `physical_support`, `bearing`, `num_display_areas`,
  `display_text` — joined text from the `vmsControllerStatus` branch above,
  `message` JSONB, `geom` POINT, `raw` JSONB).

---

## ndw_msi_shapefiles_latest.zip — MSI sign geometry (shapefile)

- **Format**: ESRI **Shapefile** set in `MSI/` (`shapes.shp/.shx/.dbf/.prj`) + a
  CSV `msi_not_converted_to_shapefile.csv` (signs lacking geometry) — **not**
  currently parsed; `src/ndwinfo/parsers/shapefile_ref.py`'s `parse_msi_shapefile`
  only reads `shapes.shp` (via `pyogrio`), so signs listed only in that CSV
  never get an `msi_sign` row.
- **Refresh**: ~weekly. **CRS**: per `shapes.prj`.
- **Role**: static geometry/attributes for the matrix signs whose live aspects
  come from `Matrixsignaalinformatie.xml.gz`. Join on sign UUID.
- **Load**: `src/ndwinfo/ingest/signs.py` reads the shapefile records directly
  into `msi_sign` (no `shp2pgsql`/`ogr2ogr` step).

Zip contents:
```
MSI/shapes.shp  (513K)  MSI/shapes.dbf (6.5M)  MSI/shapes.shx  MSI/shapes.prj
msi_not_converted_to_shapefile.csv (11K)
```
