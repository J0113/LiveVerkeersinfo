# VILD-primary sensor direction

Status: implemented for fixed NDW speed/flow measurement-site binding.

## Decision

VILD is the primary **direction source for fixed sensors**, but is not the main
road-network or carriageway source.

```text
NDW measurement site + exact Alert-C table identity
                         ↓
VILD TMC chain: primary + positive/negative neighbour
                         ↓
local directed VILD tangent
                         ↓ cross-check/fallback
OpenLR bearing ──────────┘
                         ↓
bounded OSM candidate ranking
                         ↓
accepted canonical OSM direction, or fail closed
```

This division is intentionally narrow:

- OSM owns directed road geometry, topology, road identity and the canonical
  `internal_segment_id`.
- VILD orients a fixed NDW sensor along its referenced TMC chain. Its nationally
  broad Alert-C coverage makes this simpler and more complete than making
  optional OpenLR the primary source.
- OpenLR independently checks VILD when present and is the fallback when VILD
  cannot be derived.
- WEGGEG may enrich an already accepted OSM result with lane geometry and road
  attributes. It cannot authorize or override a rejected/ambiguous direction.

Making VILD the whole product's main road source would lose the directed,
lane-tagged, routable geometry needed for GPS map matching, ramps, parallel
carriageways and segment colouring. VILD is a location-reference topology, not
a replacement for that graph.

## Acceptance rules

1. Match the site's primary TMC code using country, table number and table
   version, not location code alone.
2. Use only known textual positive/negative direction values.
3. Require the primary point and selected neighbour to belong to the same VILD
   line and project to distinct positions. If both neighbours exist, they must
   lie on opposite sides of the primary point.
4. Require the VILD line to be within the configured distance of the sensor.
5. Derive a local tangent at the sensor and reverse it when the referenced
   neighbour lies against line digitisation.
6. If OpenLR and VILD differ by more than 45 degrees, reject the binding.
7. Apply normal OSM distance, road, carriageway, road-form, heading, confidence
   and runner-up-margin checks. Only `accepted` activates speed colour.

## Explicit non-rule

Alert-C positive/negative is never translated to carriageway `R`/`L`.
`HECTO_DIR` is retained as diagnostic evidence but is not authoritative R/L;
the audited shortcut was not reliable enough. Deployment clears legacy
carriageway values produced by that shortcut. A subsequent MST refresh restores
only explicitly encoded HRL/HRR or Re/Li evidence and records its provenance.

## Rollout and diagnostics

- Binding algorithm version: `ndw-osm-v4-vild-primary-direction`.
- The poller backfills a missing current binding version even if static source
  feeds return HTTP 304.
- Old algorithm rows remain available for rollback/diagnostics but the live API
  joins only the exact current version.
- `direction_source` records `vild`, `openlr`, `conflict` or unknown provenance;
  traffic-point diagnostics expose this without letting the browser authorize a
  road colour.

Correctness is preferred over uninterrupted but potentially wrong colouring:
during a new-version backfill, unbound measurements remain visible as neutral
points and do not colour an OSM segment.
