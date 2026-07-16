# OSM lane topology in map and HUD

Status: lane-visualization MVP implemented. This is a conservative schematic
representation of the accepted directed carriageway, not a claim that OSM
contains surveyed lane centrelines or that the device's exact lane is known.

## Runtime flow

```text
OSM directional tags
  → normalized compact lane_schema on each directed segment
  → /api/roads, /api/roads/corridor and /api/roads/path
  → authoritative current + common-ahead connected path
  → shared LaneTopology model
  → schematic map lane lines + road-match HUD
```

The browser only expands the already bounded path (at most five segments in the
driving integration) and only renders the lane map from zoom 15. A JSON
signature prevents unchanged lane GeoJSON and SVG from being rebuilt on every
GPS fix. The parallel lines use a nominal 3.5 metre spacing solely for display;
road matching continues to use the canonical directed OSM segment geometry.

## Compact contract

Each segment can expose:

```json
{
  "version": 1,
  "lane_count": 5,
  "lane_order": "left_to_right",
  "attributes": {
    "turn": ["through", "through", "through", "through;slight_right", "slight_right"],
    "destination": ["Amsterdam", "Amsterdam", "Amsterdam", "Haarlem", "Haarlem"]
  },
  "unknown": [],
  "roles": ["through", "through", "through", "unknown", "exit"]
}
```

Lane 1 is leftmost in the travel direction. Missing arrays, empty tokens,
length mismatches, unsupported directions and excessive counts remain unknown.
No lane role is inferred from proximity. A motorway link without an explicit
direction and reversible/alternating directions fail closed.

## Safety boundaries

- Only `/api/roads/path` may promote segments to the common path ahead. A
  geometric corridor alone cannot prove that a fork branch is complete.
- A precise user-lane highlight requires an explicit `confirmed` lateral-lane
  observation, at least five samples and confidence of at least 0.8. Production
  GPS does not yet provide such an observation. The A4 simulation injects lane
  3 as test-only ground truth after five samples, so the map and HUD can be
  exercised without presenting simulation inference as production certainty.
- Lane-specific speed requires equal explicit lane counts. NDW documents lane 1
  as the leftmost lane in the travel direction; this equals the directional
  left-to-right ordering used by the OSM schema. Invalid/mismatched counts still
  fail closed; inconsistent or incomplete portal numbering remains
  carriageway-only.
- The traffic-speed overlay follows the same authority boundary. An accepted
  OSM binding selects road and direction. WEGGEG may then replace schematic OSM
  offsets with physical lane geometry only when its road/tangent and comparable
  carriageway identity do not conflict and its lane count equals NDW. An
  ambiguous OSM binding leaves every line uncoloured.
- WEGGEG count transitions are stored as schematic metadata. Geometry is not
  shortened to invent an unobserved taper position.
- Stale, excessive future-dated, non-finite and out-of-range speed observations
  cannot enter the live lane state.

## Deliberately not claimed

- surveyed physical lane centrelines;
- a complete lane-to-lane transition graph at forks;
- exact lane-level GPS positioning;
- exact handling of every special/reversible lane category;
- lane-level device positioning.

The canonical live-state iteration now binds MSI and DRIP objects to accepted
directed segments. MSI may carry lane scope; DRIP is always carriageway scoped.
See [15-canonical-segment-state.md](15-canonical-segment-state.md).

## Main implementation locations

| Responsibility | Module |
|---|---|
| OSM tag normalization and schema | `src/ndwinfo/osm/lanes.py`, `src/ndwinfo/osm/tags.py` |
| Persistence and API | `src/ndwinfo/models.py`, `src/ndwinfo/api/routers/roads.py` |
| WEGGEG transition metadata | `src/ndwinfo/parsers/weggeg.py` |
| Shared browser model/renderer | `web/lane-topology.js` |
| Map and road-match HUD integration | `web/road-match.js`, `web/hud.js` |
| Contract and safety regressions | `tests/test_osm_lanes.py`, `tests/test_osm_lane_state.py`, `tests/js/lane_topology.test.js` |

No performance gain is claimed without measurement. The bounded path, zoom
threshold and render signatures limit work structurally; payload size, query
latency, render time and battery impact still require a repeatable benchmark.
