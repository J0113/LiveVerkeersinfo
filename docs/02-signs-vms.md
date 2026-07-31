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

### Matrix-first road-link profile (2026-07-31 local snapshot)

The first road-linked slice uses a bounded, DB-free-after-load Matrix matcher
(`python -m ndwinfo.match_matrix`).  The local PostGIS snapshot used to verify
the implementation contained 18,458 Matrix rows, of which 18,315 had geometry
and bearing (the same rows — none has one without the other), and 18,448 joined
a current state row.  Grouping by normalized road + carriageway + kilometre
bucket (`round(km, 2)`) produced 6,439 physical gantries over all rows, 6,366
over the rows with geometry.  148 rows are the losing member of a duplicate
ghost slot.  Ghost ranking is now performed before the API result limit, and
every response carries a stable `gantry_id` and `gantry_lane`.

Carriageway codes are compared **case-sensitively** on both sides.  NDW writes
the main carriageways as `R`/`L` and its connectors as single lowercase letters,
and OSM's `carriageway_ref` follows the same convention (`Re`/`Li` for main
carriageways, `a`…`d` for connectors).  Folding the case merges the connector
`r` into the main carriageway `R` at the same road and kilometre — a different
physical roadway.  91 signs sit on a lowercase `r` in the current snapshot.

Nearest *directed* lane centerline (`direction in ('fwd','bwd')`, the population
the matcher actually searches) over all 18,315 signs with geometry: p05 0.06 m,
p50 1.07 m, p95 1.72 m, p99 3.29 m, maximum 21.70 m.  Exactly 4 signs sit
farther than 20 m from any directed lane.  The matcher therefore keeps 20 m as
the Matrix search radius and reports those 4 as `no_major_road` rather than
silently widening the search.

The sample check compared the shapefile `bearing` with the local travel
bearing of directed OSM lanes.  Same-carriageway candidates were generally
within about 15 degrees, while the opposite carriageway was approximately 180
degrees away.  This is the current Matrix-snapshot evidence for the
`matrix-gantry-v6` dry-run assumption; it is not reused as an unverified DRIP
interpretation.  A candidate with a conflicting normalized road reference is
rejected, and close candidates on different traversals become `ambiguous`
rather than entering the HUD.

OSM writes a shared carriageway as a concurrency (`A7;A8`), so the reference
comparison splits the OSM `ref` into a set and accepts a source road that is
any member of it.  Comparing the compacted whole string instead made `A7;A8`
conflict with both routes it carries: nationally that cost 402 gantry-lane
matches and inflated `road_ref_conflict` from 162 to 505.

Run a small area first, for example:

```text
python -m ndwinfo.match_matrix --bbox 4.6,52.3,4.9,52.6 --limit 250
```

The command only reads Matrix and OSM rows and emits aggregate status,
confidence, failure-reason, and distance statistics.  Use
`--include-results` for sanitized fixture/report review; it does not persist
road assignments or alter source geometry.

To make a bounded sample visible in the browser, add `--persist`:

```text
docker compose run --rm -T app python -m ndwinfo.match_matrix \
  --bbox 4.6,52.3,4.9,52.6 --limit 250 --persist
```

This writes explainable outcomes to `road_point_assignment` and successful
point links to `road_point_link`, and drops any assignment left behind by a
different `algorithm_version`. The Matrix API keeps the source sign point by
default and adds `matched_road_*` identity/metadata properties (ref, name,
highway class, segment, direction, lane, residuals), which the marker popup
shows. The linked way's own geometry is deliberately not repeated on every sign
feature — the basemap already draws the road. `geometry=matched` returns the
projected anchor instead of the source point, and `geometry=best` falls back to
the source point where no confident match exists.

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

### DRIP road-link profile (2026-07-31 local snapshot)

The local snapshot contains 870 DRIPs; 790 have a bearing. Nearest directed
OSM-lane distances are p50 1.42 m, p90 59.54 m, and p99 809.63 m. 86 panels
are beyond 60 m and 14 are beyond the controlled 500 m extension. The matcher
therefore uses 60 m as its primary search and accepts the 60–500 m tail only
when the DRIP bearing leaves one compatible directed traversal **and** agrees
with it to within 20 degrees — past its own gantry the panel's proximity stops
being evidence, so a bearing that merely scrapes the 45-degree candidate gate
is not enough (`unsupported/extended_bearing_too_weak`). Records beyond 500 m
remain `unsupported/no_major_road` and keep their source geometry.

Traversals are ranked by residual distance, except inside a 2 m band around the
nearest one, which is ranked by bearing error instead. At a junction both
adjoining traversals pass under the panel, so their residual distances differ by
centimetres — noise, not evidence — and proximity alone would silently take the
worse-aligned one. This is the same evidence the ambiguity check then applies,
so ranking and fail-closed agree.

A real local sample confirms that the DATEX `bearing` follows travel direction:
same-direction OSM lanes were generally within about 15 degrees, while the
opposite direction was approximately 180 degrees away. This interpretation is
stored in assignment diagnostics as `bearing_interpretation=travel`. A route
number extracted from the panel description (for example `A22`) is scored as a
hint only; it is never treated as an unverified hard parser contract — DRIP text
often names the road the panel *informs about* rather than the one it stands
beside, so a hint that contradicts the matched road is recorded as
`road_ref_quality=conflict` rather than rejecting the match or being flattened
into the neutral `corridor`. Missing bearing data cannot choose between opposite
directions.

On this snapshot: 763 matched (706 high, 22 medium, 35 low), 44 ambiguous,
5 unmatched, 58 unsupported.

Run a bounded DRIP report first:

```text
python -m ndwinfo.match_drips --bbox 4.6,52.3,4.9,52.6 --limit 1000 --include-results
```

After review, `--persist` writes one assignment per `[controller_id, vms_index]`
source key to the shared point-link tables. The `/api/signs/drips` endpoint
keeps source geometry by default and exposes `geometry=matched|best` plus
`matched_road_*`, direction, residual distance, confidence, and failure fields.
The DRIP HUD still uses the legacy bbox selector until the shared
`/api/road/context` and `/api/road/ahead` slice is enabled; these persisted links
are the validated input for that next cutover.

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
