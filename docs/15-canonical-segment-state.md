# Canonical directed segment state

Status: implemented for speed, MSI and DRIP. Situation/closure binding remains
the next Phase C increment.

## Contract

Every `/api/roads`, `/api/roads/corridor` and `/api/roads/path` feature exposes
one authoritative `properties.segment_state`:

```json
{
  "version": 1,
  "speed": {
    "speed_kmh": 82.0,
    "method": "measured",
    "source": "NDW",
    "sources": ["measurement-site-id"],
    "observed_at": "2026-07-16T08:59:00Z",
    "valid_until": "2026-07-16T09:09:00Z",
    "confidence": 0.82,
    "sample_count": 2,
    "stale": false
  },
  "matrix": [],
  "drips": []
}
```

The nested state is authoritative. An empty list means that no accepted,
current fact applies; the driving HUD must not fall back to a nearest map point.

## Speed coverage

Direct accepted NDW observations produce `measured`. Missing segments may be
filled only within the loaded directed graph slice:

- `interpolated`: bounded between compatible measurements on a complete
  one-to-one chain;
- `propagated`: bounded from one compatible measurement on such a chain;
- `unknown`: every fork, merge, carriageway conflict, incomplete adjacency,
  stale/future observation or insufficient confidence.

For the optional per-lane map overlay, OSM remains the road and direction
authority. Validated WEGGEG supplies physical lane geometry when available;
otherwise equal explicit OSM/NDW lane counts allow schematic OSM lane offsets.
WEGGEG can never promote an ambiguous/rejected binding. The raw point layer
still displays every available measured value neutrally, so observation
availability and safe road activation are not conflated.

The API performs two set-based endpoint queries for complete adjacency. It does
not scan the national graph or all measurement sites per request.

### Measurement-site direction fallback

Persisted OSM binding uses source direction in this strict order:

1. an explicit, finite OpenLR bearing;
2. a local VILD line tangent oriented by the site's exact TMC
   `positive`/`negative` topology;
3. unknown direction, which leaves equal opposite OSM candidates ambiguous.

VILD never overrides OpenLR. The fallback requires the primary and selected
neighbour point to share the same VILD line, distinct topology offsets, a known
textual direction, and a VILD line within 50 metres of the measurement site.
When both topology arms exist they must project to opposite sides. Numeric
direction codes, site-name suffixes and road-number conventions are not
guessed. A nearest measurement-line chord is not exposed as bearing because it
does not prove local travel direction.

The local database audit on 2026-07-16 found that this conservative fallback is
derivable for roughly 23,480 of 99,435 measurement sites without OpenLR. On
1,428 sites that had both references, 95% of derived VILD bearings were within
45 degrees of OpenLR. These are coverage/correctness observations, not a
performance benchmark.

## MSI and DRIP

Physical source locations are prebound to the active graph with distance,
bearing, road/carriageway metadata, confidence margin, graph version and
algorithm version. Ambiguous and rejected bindings never enter segment state.
Common L/R spelling variants are normalized. Other NDW DVK letters are kept as
distinct carriageway identities and require an exact, case-insensitive OSM
`carriageway_ref`; they are never guessed from proximity alone.

- MSI is direction/carriageway scoped and may also carry `lane`. NDW officially
  numbers ordinary lanes from the median: lane 1 is leftmost in the travel
  direction. This matches the OSM directional `left_to_right` schema. Because
  real NDW lettering/numbering is not always consistent, canonical lane scope
  is emitted only when the complete gantry has unique contiguous lanes
  `1..OSM lane_count`; otherwise only `source_lane` is retained diagnostically.
- DRIP is direction/carriageway scoped only. A DRIP fact deliberately has no
  `lane` property and is selected only when its bound segment is under the user
  or on the backend-confirmed common path ahead.

Latest-snapshot ingest removes disappeared DRIPs and MSI states. The API also
applies configured ingest-age validity windows, so stale objects disappear even
when a feed stops updating.

Official lane references: [NDW Matrix Traffic Signs](https://docs.ndw.nu/en/producten/msi/)
and [NDW Explorer](https://docs.ndw.nu/en/handleidingen/DEXTER/verkenner/).

## Main implementation locations

| Responsibility | Module |
|---|---|
| Speed model | `src/ndwinfo/osm/speed_model.py` |
| Measurement-site binding | `src/ndwinfo/matching/source_binding.py` |
| MSI/DRIP matcher | `src/ndwinfo/matching/live_objects.py` |
| Persisted spatial binding job | `src/ndwinfo/matching/live_object_job.py` |
| Segment-state API | `src/ndwinfo/api/routers/roads.py` |
| Browser normalization | `web/canonical-segment-state.js` |
| Confirmed-path HUD selection | `web/road-match.js`, `web/hud.js` |

No latency or resource improvement is claimed without a repeatable benchmark.
