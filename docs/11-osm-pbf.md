# OpenStreetMap driving roads (Geofabrik) — `osm_road`

Live feed, distinct from the NDW catalog (`docs/README.md`) — a non-NDW
source ingested the same way `nwb_wegvakken` and `weggeg_rijstroken` are
(absolute `url` override in `feeds.py`, see [docs/08](08-nwb-road-network.md)).
Serves `highway=motorway,trunk,primary,secondary` (+ their `_link` ramp/
interchange variants — without them, motorways would show gaps at every
on/off-ramp) as a "OpenStreetMap" map layer, with **every OSM tag stored and
shown**, not a curated subset. The tag-stats sections below are the
exploratory survey that motivated the scope decision; they remain accurate
background but describe the *source data*, not the (narrower) ingested set.

## License

OpenStreetMap data is **ODbL-licensed** (Open Database License) — attribution
is required wherever it's rendered. The web UI credits OpenStreetMap on every
basemap, including the satellite view (`web/map.js`'s `OSM_ATTR`), regardless
of which basemap is active, since the driving-roads layer itself is
OSM-sourced independent of the basemap underneath it.

## Source

- **Provider**: Geofabrik, https://download.geofabrik.de/europe/netherlands.html
- **Ingested file**: `noord-holland-latest.osm.pbf` (province extract, ~180 MB)
- **Format**: OSM PBF (protobuf), parsed with `osmium` (pyosmium)'s
  `FileProcessor` streaming iterator — not `SimpleHandler`, whose
  `way()`/`node()` callbacks can't `yield` to an outer generator.
  `with_locations("sparse_mem_array")` resolves way geometry from node
  coordinates in one pass (verified ~910MB peak RSS for this extract's
  ~18.6M nodes; a full-Netherlands extract will need its own RSS check
  before deploying — see "Scaling to the full Netherlands" below).
- **Update cadence**: Geofabrik regenerates ~daily; ingested on the same
  cadence (`cadence_s: 86400` in `feeds.py`).
- **CRS**: WGS84 (EPSG:4326) — matches project convention
- **No live data** — snapshot per extract, upserted; see "Extract model"
  below for how staleness is pruned.

## Extract model (`osm_road` + `osm_road_extract`)

`osm_road.osm_id` (the OSM way id) is the primary key — globally unique
across all of OSM, so a way that happens to cross a province boundary and
gets kept whole in two overlapping Geofabrik extracts is still one row.
Which extract(s) currently confirm seeing a way is tracked separately in
`osm_road_extract` (`extract_key`, `osm_id`, `ingested_at`). Each ingest run
(`ndwinfo.ingest.osm_roads.OsmRoadIngester`, configured with a `feed_name`
and `extract_key`) upserts both tables, then prunes only **its own**
extract's stale memberships, and only deletes an `osm_road` row once it has
**no** remaining membership in any extract. This is deliberately not the
single-timestamp prune `NwbWegvakkenIngester` uses ([docs/08](08-nwb-road-network.md))
— that's safe there only because NWB is one national file; reusing it here
would let ingesting one province delete roads "owned" by another province's
last run. A zero-row parse raises instead of pruning, so a bad/truncated
download can't silently erase the layer.

Adding another province is just another `feeds.py` entry + `INGESTERS`
registration with a different `extract_key` — no schema change.

## Per-lane geometry (`osm_road_lane`)

Individual lane centerlines derived from each `osm_road` way's `lanes` tag,
at **3.5m** width for motorway/trunk/primary(+`_link`) and **2.75m** for
secondary(+`_link`) — the `_link` classes inherit their parent class's
width. Computed in the same PBF pass as `osm_road` (`ndwinfo.parsers.osm_lanes.make_lane_rows`,
called from `OsmRoadIngester._flush`) — no second file read.

**CRS**: `osm_road.geom` is WGS84 degrees, not metres — offsetting a lane
there would offset by degrees. The parser transforms WGS84 → RD (EPSG:28992),
offsets/tapers in RD (metres, via `shapely.offset_curve`/`shapely.ops.substring`,
the same technique `parsers/weggeg.py` uses for WEGGEG lanes), then transforms
back to WGS84.

**Direction model** — deliberately conservative, built from what's actually
tagged rather than guessed. Of the 4,952 two-way (`oneway` ≠ `yes`)
lanes-tagged ways in the Noord-Holland extract, only ~350 have an explicit
`lanes:forward`/`lanes:backward`/`lanes:both_ways` split; the rest carry
only a combined `lanes=N` total with no directional breakdown:

- `oneway=yes|true|1`: single directional block, lane 1 = leftmost in
  travel direction.
- `oneway=-1`: way's coordinates reversed first, then treated as the case
  above.
- `oneway=reversible|alternating` (33 ways in this extract, all `motorway`):
  **skipped** — physical lane-to-direction mapping changes by time of day;
  not guessed.
- Two-way with `lanes:forward`/`lanes:backward`/`lanes:both_ways` present:
  used directly. Forward lanes on the right half of the centerline (NL
  drives right), backward on the left, a `both_ways` lane (if tagged)
  centered. `turn:lanes:forward`/`turn:lanes:backward` drive tapering —
  backward tokens are ordered left-to-right from the backward driver's
  perspective, so they map onto physical lane position in **reverse**
  order, and a backward merge trims the **start** of the way, not the end.
- Two-way with no directional tag (the majority of two-way roads): N
  generic symmetric lanes are still drawn, but `direction='unknown'`,
  `role='unknown'`, and **not tapered** — a generic `turn:lanes` on an
  undirected two-way cross-section can't be reliably attributed to one
  physical lane. (An earlier draft of this feature assumed an odd `lanes`
  total implied a center-turn lane — checked against real data and found
  wrong: only 1 way in the whole extract has `lanes:both_ways` tagged.)
- Cardinality guard: if a `turn:lanes`/`turn:lanes:forward`/`turn:lanes:backward`
  token count doesn't match the lane count it applies to, the tokens are
  ignored for that way (`role='unknown'` for all its lanes) rather than
  risking a token landing on the wrong physical lane.

**Tapering scope**: only `turn:lanes` tokens `merge_to_left`/`merge_to_right`
shorten a lane (by `min(150m, way_length * 0.5)`, trimmed from the offset
line in RD space, not the source line). Turn-only tokens (`left`/`right`/
`through`/`slight_right`/`slight_left` alone) leave the lane full length —
a turn lane at a junction still exists up to the stop line, and
`slight_right` alone is often just a normal lane on a curving road, not
necessarily an exit. **Some real motorway exits tagged only `slight_right`
will not appear tapered** — an intentional scope limit, not a bug.

`GET /api/osm/lanes` — plain bbox + deterministic-order + cap/`truncated`,
no zoom-based class tiering (unlike `/api/osm/roads` — lanes are already a
detail-zoom-only layer, gated client-side via the `osm_lanes` layer's
`minZoom: 15`). `osm_road_lane.source_id` has `ON DELETE CASCADE` to
`osm_road.osm_id`, so the existing extract-scoped prune on `osm_road`
cleans up lanes automatically — no separate lane-level extract tracking.

## Scaling to the full Netherlands

The plan is to switch from the Noord-Holland extract to the full
`netherlands-latest.osm.pbf` once proven. Before that switch:
**re-benchmark peak RSS** — nationwide node count could push
`with_locations("sparse_mem_array")` into multi-GB territory (Noord-Holland's
18.6M nodes cost ~910MB). If it does, fall back to a two-pass parse
(collect matching ways' referenced node ids first, then resolve only those
coordinates in a second pass) rather than assuming the single-pass approach
scales. `/api/osm/roads` is already zoom-tiered (`_highway_types_for_zoom`
in `api/routers/osm.py`) so the API side doesn't need rework — only the
parser's memory profile needs re-checking.

## Serving

`GET /api/osm/roads?bbox=...&zoom=...` — `ST_Intersects` against the
GiST-indexed `geom` column, bounded by zoom (hidden below 7, motorway-only
7–9, +trunk/primary 9–11, all 8 classes 11+ — NH alone is >10x
`api_max_limit` for the full class set). Feature properties are the full
`raw` tag dict spread verbatim, plus `osm_id`/`highway` — the web popup
(`buildPopupHtml` in `web/ui.js`) already renders every property generically,
so "display all tags" needed no popup code, only the API spreading `raw`
instead of returning a curated field list.

## File stats (Noord-Holland extract)

| Object type | Count |
|---|---|
| Nodes | 18,604,160 |
| Ways | 2,533,112 |
| Relations | 21,825 |

Bounding box (header): `3.90,52.16` – `5.38,53.29` (province-clipped, but the
raw data bbox in the file is unclipped/global due to how Geofabrik extracts —
use the header bbox, not the data bbox, for area sanity-checks).

## Structure

- **Nodes** — point + tags. Bulk of node tags in this extract are **address
  points** (`addr:street`/`addr:housenumber`/`addr:city`/`addr:postcode`,
  ~1.7M each) sourced from Dutch BAG import, not POIs.
- **Ways** — line/polygon + tags. Roads, buildings, land use.
- **Relations** — grouped ways/nodes: multipolygons, routes, turn
  restrictions, boundaries.

## Relevant tag breakdown

### Roads (`highway=*` on ways) — detail

387,929 ways carry a road-class `highway=*` value (excludes the small
point-feature `highway=*` values that live on nodes, see below).

| highway= | count | named | ref | maxspeed | lanes | surface | oneway | lit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| footway | 110,428 | 11,804 | 46 | 114 | 9,315 | 42,205 | 486 | 11,898 |
| service | 63,806 | 11,479 | 266 | 7,949 | 5,964 | 16,916 | 7,492 | 2,994 |
| residential | 52,540 | 51,977 | 18 | 51,922 | 3,021 | 22,415 | 10,907 | 16,998 |
| cycleway | 38,408 | 19,207 | 63 | 708 | 3,036 | 23,141 | 18,429 | 13,754 |
| path | 25,444 | 1,986 | 23 | 42 | 267 | 11,742 | 340 | 1,584 |
| unclassified | 18,727 | 17,515 | 27 | 18,072 | 3,233 | 8,367 | 3,997 | 3,888 |
| track | 17,916 | 679 | 0 | 103 | 170 | 8,311 | 25 | 281 |
| tertiary | 17,332 | 16,322 | 128 | 17,267 | 7,494 | 10,832 | 7,841 | 5,978 |
| secondary | 10,075 | 8,925 | 3,459 | 10,040 | 7,745 | 7,559 | 7,101 | 4,776 |
| pedestrian | 6,559 | 4,308 | 11 | 213 | 152 | 3,580 | 340 | 1,876 |
| steps | 6,269 | 369 | 1 | 4 | 931 | 2,644 | 99 | 969 |
| primary | 5,988 | 5,042 | 5,332 | 5,969 | 5,236 | 4,649 | 4,347 | 2,428 |
| living_street | 4,878 | 4,606 | 7 | 4,861 | 308 | 2,445 | 535 | 1,214 |
| motorway_link | 2,325 | 446 | 2,228 | 2,325 | 2,322 | 2,318 | 2,325 | 2,042 |
| busway | 2,180 | 1,709 | 39 | 1,960 | 1,317 | 1,036 | 1,314 | 489 |
| motorway | 2,047 | 951 | 2,047 | 2,045 | 2,047 | 2,047 | 2,047 | 1,823 |
| trunk | 1,022 | 930 | 969 | 1,021 | 1,001 | 1,013 | 973 | 821 |
| bridleway | 719 | 19 | 3 | 0 | 20 | 328 | 1 | 42 |
| *_link (primary/trunk/secondary/tertiary)* | 1,107 | 548 | 422 | 1,092 | 969 | 892 | 1,062 | 652 |
| corridor / raceway / road | 159 | 56 | 0 | 61 | 26 | 37 | 64 | 45 |

Key read: **motorway/trunk/primary/motorway_link are ~100% tagged** with
maxspeed, lanes, surface, oneway (small, high-value network, well
maintained). **Named coverage drops fast down the hierarchy** —
service/footway/path/track/cycleway are 60-98% *unnamed*, since most are
driveways, parking aisles, or minor paths with no street name to give.
`ref` (route number: A/N-road) is essentially only on motorway/trunk/
primary/secondary — e.g. `A7`, `N99`, `N240`, `G200` (this last a regional
cycle-route ref).

**Attribute coverage across all 387,929 road ways:**

| attribute | tagged | % |
|---|---:|---:|
| surface | 172,477 | 44.5% |
| name | 158,878 | 41.0% |
| maxspeed | 125,707 | 32.4% |
| lit | 74,517 | 19.2% |
| oneway | 69,690 | 18.0% |
| lanes | 54,550 | 14.1% |
| zone:traffic | 51,370 | 13.2% |
| bicycle | 49,495 | 12.8% |
| foot | 42,727 | 11.0% |
| smoothness | 34,756 | 9.0% |
| width | 34,373 | 8.9% |
| access | 33,071 | 8.5% |
| service (subtype) | 31,882 | 8.2% |
| layer | 24,019 | 6.2% |
| sidewalk | 21,978 | 5.7% |
| bridge | 20,606 | 5.3% |
| ref | 15,089 | 3.9% |
| turn:lanes | 7,249 | 1.9% |
| cycleway (subtype) | 4,340 | 1.1% |
| junction | 3,716 | 1.0% |
| tunnel | 3,399 | 0.9% |

**Value distributions worth knowing:**

- `maxspeed=` (km/h, 125,707 tagged): `30` 68,232 · `50` 31,028 · `80` 7,643
  · `15` 6,460 · `60` 6,276 · `100` 2,844 · `70` 1,587 · `130` 283 ·
  `90` 165 — dominated by 30/50 zones (urban), consistent with NL's
  widespread 30 km/h rollout.
- `surface=`: `paving_stones` 65,539, `asphalt` 64,840, `paved` (unspecified)
  11,117, `grass` 4,671, `concrete` 4,392, `unpaved` 4,261, `sand` 2,588,
  `fine_gravel` 2,563, `gravel` 2,132 — paving-stone-heavy because it
  includes footways/cycleways (typical NL red-brick cycle path).
- `oneway=`: `yes` 55,122, `no` 14,475 (explicit override), `reversible` 49,
  `alternating` 44.
- `access=`: `private` 24,736 dominates (driveways/parking aisles),
  `no` 2,408, `customers` 2,346, `destination` 1,375, `permissive` 1,132.
- `bicycle=`: `no` 20,734, `use_sidepath` 18,685 (must use adjacent
  cycleway instead of this way), `yes` 5,325, `designated` 2,780.
- `foot=`: `yes` 14,864, `use_sidepath` 13,653, `no` 9,064,
  `designated` 2,634.
- `lit=`: `yes` 66,675, `no` 7,641 — decent streetlight coverage signal.
- `junction=`: `roundabout` 3,181, `intersection` 434 (multi-way junction
  modeling), `circular` 100.
- `layer=`: mostly `1` (21,659, elevated) and `-1` (1,817, underpass) —
  grade-separation signal, pairs with `bridge=`/`tunnel=`.
- `bridge=`: `yes` 20,050, `movable` 234 (matters for NL — moveable
  bridges), `viaduct` 220.
- `tunnel=`: `building_passage` 2,079 (route under a building), `yes` 1,312.
- `service=` (subtype of `highway=service`): `parking_aisle` 16,264,
  `driveway` 13,974, `alley` 937, `emergency_access` 269.
- `cycleway=` (lane style on a road, not a separate cycleway way):
  `lane` 2,381, `crossing` 840, `shared_lane` 576, `track` 43.
- `sidewalk=`: `right` 7,619, `both` 5,835, `no` 5,633, `left` 2,425.
- `smoothness=`: `good` 16,207, `intermediate` 14,250, `excellent` 2,850,
  `bad` 1,115.
- `zone:traffic=`: `NL:urban` 39,980, `NL:rural` 11,390 — the Dutch
  default-speed-zone tag (affects implicit maxspeed where not explicit).

**Point features on/near roads** (`highway=*` tagged on **nodes**, not
ways — traffic infrastructure, not paths):

`street_lamp` 30,551 · `crossing` 18,868 · `traffic_signals` 8,659 ·
`bus_stop` 6,114 · `give_way` 5,877 · `turning_circle` 452 ·
`motorway_junction` 271 · `stop` 180 · `speed_camera` 171 ·
`passing_place` 161 · `mini_roundabout` 5. Also relevant:
`crossing:markings` tag (17,332 nodes, from earlier general scan) gives
zebra-crossing detail. `traffic_sign=*` only 3,763 nodes total (sparse,
see [docs/06](06-verkeersborden.md) for the actual sign dataset).

**Turn restrictions** (`type=restriction` relations, 2,459 total):
`only_straight_on` 870, `no_u_turn` 822, `no_left_turn` 356,
`no_right_turn` 179, `only_right_turn` 82, `only_left_turn` 58,
`no_straight_on` 50, `no_exit` 4. These reference `from`/`via`/`to` ways —
routing-relevant, not present in NWB Wegvakken.

**Comparison to NWB Wegvakken** ([docs/08](08-nwb-road-network.md)):
OSM adds footways/cycleways/tracks/paths NWB doesn't carry, per-way
maxspeed/surface/lit/access detail, turn restrictions, and updates ~daily
vs NWB's ~30-day cadence. NWB stays authoritative for the official RWS
road-vak model (rijksweg administration, carriageway direction) — OSM's
tagging is crowd-sourced so completeness varies by contributor activity in
an area (visible above: motorway-class near-100% tagged, residential/
footway/track much patchier).

### Buildings

`building=*` on 1,552,970 ways — near-total BAG-derived building footprint
coverage for the province, with `ref:bag`, `building:levels`, `height`,
`start_date` commonly attached.

### Land use (`landuse=*` on ways, 200,467 tagged)

`grass` 98.7k, `forest` 52.7k, `meadow` 24.5k, `farmland` 9.4k,
`residential` 2.6k, `industrial` 727, `retail` 215, etc.

### POIs (`amenity=*`)

Nodes (59,421 tagged) — mostly street furniture, not addresses:
`bench` 17.9k, `waste_basket` 7.7k, `recycling` 6.1k, `restaurant` 3.9k,
`bicycle_parking` 3.0k, **`charging_station` 2,299**, `fast_food` 1.5k,
`cafe` 1.3k, `atm` 856, `fuel` 461.

Ways (96,617 tagged) — mostly parking areas as polygons:
`parking_space` 57.8k, `parking` 31.1k, `bicycle_parking` 3.4k,
`charging_station` 92, `school` 946.

**Overlap with existing NDW feeds**: `amenity=charging_station` (2,299
nodes + 92 ways) could cross-reference [docs/04](04-charging.md) EV
charging data — OSM has broader coverage (any operator) but no live
status/tariff, NDW has authoritative Dutch charge-point network + live
occupancy where available. `amenity=parking` (664 nodes + 31,078 ways)
overlaps conceptually with [docs/05](05-truckparking.md) truck parking but
OSM's parking tag is generic (car/bike/motorcycle), not truck-specific.

### Addresses

`addr:housenumber` on 1,712,191 nodes + 14,187 ways — dense province-wide
BAG-derived address coverage. Useful for geocoding if ever needed, not
currently a project requirement.

### Traffic signs

`traffic_sign=*` on only 3,763 nodes — sparse compared to the NDW
verkeersborden CSV ([docs/06](06-verkeersborden.md), >200M rows nationwide).
OSM sign tagging is incidental/crowd-sourced, not a viable replacement.

### Relations

`type=multipolygon` 8,858 (building/landuse holes), `type=route` 8,162
(bus/cycle/hiking routes, `network`/`ref`/`colour` tags), `type=restriction`
2,459 (turn restrictions — could matter for future routing features),
`type=public_transport` 787, `type=boundary` 598.

## Out of scope (for now)

Everything below was surveyed above but deliberately **not** ingested —
the driving-roads layer covers only `osm_road`/`osm_road_extract`:

- **Buildings, land use** — no project requirement yet; would be its own
  table(s) and a much larger row count nationwide.
- **POIs** (`amenity=*`, incl. `charging_station`/`parking`) — overlaps
  conceptually with the existing NDW charging ([docs/04](04-charging.md))
  and truck-parking ([docs/05](05-truckparking.md)) feeds, which are
  authoritative and carry live status; OSM's copies would be redundant.
- **Addresses** (`addr:*`) — no geocoding requirement in this project.
- **Traffic signs** (`traffic_sign=*`, only 3,763 nodes in NH) — far
  sparser than the dedicated NDW verkeersborden CSV
  ([docs/06](06-verkeersborden.md), >200M rows nationwide).
- **Turn restrictions and route relations** — real routing-relevant data
  NWB lacks, but no routing feature exists yet to consume them. Revisit if
  one is built.
- **Non-driving `highway=*` classes** (footway/cycleway/residential/
  service/track/path/etc.) — the user's scope is specifically the driving
  road network; these stay uningested.
