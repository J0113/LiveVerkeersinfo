# Validated driving core and A4 desk simulation

The production driving path now combines a deterministic browser matcher with
a bounded, directed graph query. This is the implemented foundation for
validating road, carriageway and direction correctness before adding more live
object families.

## Runtime flow

```text
GPS fix or A4 simulation fix
           ↓
accuracy-aware corridor query
           ↓
bounded candidate ranker + eight-fix history + hysteresis
           ↓
accepted internal_segment_id (or fail closed)
           ↓
directed /api/roads/path traversal
           ↓
under + behind + common ahead + unresolved branches
           ↓
only common-path live speed enters the driving display
```

The path endpoint never scans the complete graph. It follows only directed
`from_node_id → to_node_id` edges and is capped by distance, visited-edge count
and branch count. At an unresolved fork, branch-specific information is not
promoted to `common_ahead`.

## Desk simulation

The play button in the right-hand map controls replays a fixed northbound A4
trajectory over two connected OSM segments in the current Noord-Holland graph.
It reports 100 km/h with six-metre accuracy and completes the 2.8 km route in
approximately 17 seconds. It calls the normal `onGeolocationUpdate` function;
there is no simulated matcher result or alternate API response.

This makes the following checks possible at a desk:

- map following and heading rotation;
- corridor fetching and candidate selection;
- stable road/direction matching over a segment transition;
- connected ahead-path fetching;
- speed and uncertainty rendering in the HUD.

The synthetic route proves integration and regression behaviour, not national
accuracy. Recorded drives with hand-reviewed ground truth remain the next
external validation layer.

## Shadow graph import

A graph can be built and source-bound without replacing the active graph:

```bash
ndwinfo-osm-import data/netherlands-latest.osm.pbf --shadow
```

Activation remains a separate atomic operation. This allows a national graph
to be measured and inspected while the validated regional graph continues to
serve users.
