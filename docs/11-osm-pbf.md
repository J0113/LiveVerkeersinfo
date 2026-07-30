# OpenStreetMap driving roads (Geofabrik) — `osm_road`

Live feed, distinct from the NDW catalog (`docs/README.md`), configured through
an absolute `url` override in `feeds.py`.
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
- **Ingested file**: `netherlands-latest.osm.pbf` (country extract, ~1.3 GB gz)
- **Format**: OSM PBF (protobuf), parsed with `osmium` (pyosmium)'s
  `FileProcessor` iterator in two passes. The first retains only selected
  driving ways and their node ids; the second resolves coordinates only for
  those ids. This avoids an in-memory location index for every node in the
  country extract — see "Scaling to the full Netherlands" below.
- **Update cadence**: Geofabrik regenerates ~daily; ingested weekly
  (`cadence_s: 604800` in `feeds.py`) since the full-NL extract is large.
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
**no** remaining membership in any extract. This extract-membership model is
required because ingesting one province must not delete roads last confirmed
by another province. A zero-row parse raises instead of pruning, so a
bad/truncated download cannot silently erase the layer.

Adding another province is just another `feeds.py` entry + `INGESTERS`
registration with a different `extract_key` — no schema change.

`osm_road.node_refs` retains the complete ordered OSM node-ID list for each
way. It is source topology, not derived lane data: the independent Lanes
builder uses shared/internal nodes to split a long way into stable logical
segments and to split closed roundabout rings at their real approaches.

## Independent lane lines (`osm_lane_centerline` + `osm_lane_connection`)

The lane graph derives thin centerlines directly from `osm_road.geom`,
`osm_road.node_refs`, and the parent OSM tags. It is the only lane model:
the earlier per-way `osm_road_lane` table (physical ordering, per-class widths,
its own junction connectors) was retired once the map layer, the drive HUD's
current-road pick, and the speed-sensor match all read this graph instead.

- Every physical lane is one `#111171` line. Adjacent lines have a fixed
  3.5 m centre-to-centre pitch for every supported highway class.
- Ways tagged `access=no` remain available in the source `osm_road`/Driving
  Roads layer, but produce no independent lane centerlines or connections.
  This keeps emergency crossovers and other legally inaccessible
  infrastructure out of the normal-driving lane graph.
- Metric offsets are built in EPSG:28992, validated for endpoint displacement
  and length degeneration, then transformed back to WGS84.
- A tagged single-track two-way road (`lanes=1`) is one stored
  `direction=both` feature. The connector graph traverses that same geometry
  in both directions without duplicating the map line.
- `junction=roundabout` implies one-way unless an explicit `oneway=no`
  contradicts it; `junction=circular` does not.
- Stable lane IDs use
  `ll:<osm_id>:<start_node_id>:<end_node_id>:<direction>:<lane_nr>`.
  Connections separately reference directed traversal IDs with `@fwd` or
  `@bwd`.
- Connection selection is topology-first. Matching OSM node IDs are preferred;
  original unoffset road endpoints within 0.5 m are the coordinate fallback.
  A 25 m junction-box candidate is suppressed when an immediate-successor path
  from that candidate's own source proves that it skips one or two short
  logical segments. An exact predecessor from an unrelated approach never
  suppresses a legitimate turn. Proximity-only candidates are also rejected
  across different `layer` or `bridge`/`tunnel`/`covered` states. A non-exact
  link handover must have an absolute endpoint angle of at most 45 degrees;
  signed right-turn angles cannot bypass that limit. A junction-box exit also
  cannot jump into the middle of a link that already has an exact predecessor,
  and an ordinary road cannot connect directly to/from a motorway without a
  motorway-link. Legacy/malformed lane rows without retained original segment
  endpoints are excluded from automatic adjacency and reported as
  `missing_source_geometry`; offset lane endpoints are never substituted as
  topology evidence.
- `turn:lanes*` is parsed as ordered, cardinality-checked lane fields.
  The source way's fields govern movements at its exit node; a successor's
  fields describe its later junction and are not reused to remap its entry.
  When the angular ranges for `through` and a directional token overlap, an
  actual branch prefers the directional token. A matching explicit directional
  token also outranks a shared route `ref`, so a same-ref slip road cannot be
  mistaken for the primary continuation. A lane carrying only that turn is
  reserved for the branch before remaining lanes are mapped monotonically onto
  the primary continuation; combined tokens such as
  `through;slight_right` remain eligible for both. `placement*` anchors decide
  widening side before inferred lane-family rules;
  `destination:lanes*` and `destination:ref:lanes*` cross-check branch
  allocation; `change:lanes*` can reject an illegal inferred lateral edge. A
  narrowing without cardinality-valid merge tags is reported as
  `unresolved_narrowing_merge` instead of silently dropping a source lane.
  Matching concrete placement anchors may preserve the proven one-to-one
  survivor block, but do not invent the unsupported many-to-one merge.
- Concrete one-way `placement=left_of:N|middle_of:N|right_of:N` values also
  anchor the metric lane offsets to the tagged OSM reference line instead of
  recentering every cross-section. For an unambiguous, one-way, two-node
  `placement=transition` section, a preliminary topology pass inherits each
  lane's start/end anchor from its connected predecessor/successor (with
  explicit `placement:start`/`placement:end` taking precedence), then generates
  tangent-preserving transition curves before connectors are rebuilt.
  Ambiguous, chained, curved, or extreme-angle transitions remain unchanged and
  emit `unresolved_transition_placement` diagnostics rather than being guessed.
- `connection_type` describes actual lane-graph multiplicity
  (`continuation`, `split`, or `join`). Road-level `entry`, `exit`,
  `roundabout`, or `continuation` meaning is exposed separately as
  `movement_type`; explicitly turn-tagged branch candidates are persisted as
  `exit`, not as an extra schema value. A `merge_to_left/right` token alone
  never changes a one-to-one edge into a join.
- Connector trims are resolved once per physical lane endpoint. Both ends of a
  short transition share one budget that retains at least 2 m or 20% of the
  lane, whichever is stricter. Connectors that meet the same physical endpoint
  also share its Bézier handle station. A `_link`/non-link handover may request
  up to 25 m of taper and use up to 80% of the link-side lane as transition
  runway (subject to the same visible-length budget); this distributes a
  one-lane-link/four-lane-mainline offset instead of concentrating it at the
  OSM node. An exact link handover whose endpoint tangents differ by at most
  15 degrees may use twice the normal gap-based runway (up to 50 m), so a
  nearly straight merge is not forced through a visible bend close to the OSM
  node. Exact node/endpoint link handovers up to 45 degrees may be trimmed;
  uncertain junction-box movements retain the 30-degree limit. Only transitions
  at or below 30 degrees qualify for a literal straight connector, while wider
  exact transitions retain a tangent-preserving Bézier. Other near-straight
  count changes retain the 15 m and 40% per-line limits. The same bounded taper
  is used for any equal-count continuation with at least 0.75 m of lateral
  endpoint displacement, including both bidirectional roads whose forward/
  backward lane distribution changes and bidirectional roads that split into
  separately mapped one-way roads. A purely longitudinal gap does not trigger
  this rule. When the lateral transition is near-straight (at most 30 degrees
  and 8 m total endpoint displacement), the trimmed stations are joined by a
  straight segment instead of a Bézier; this avoids an artificial bow between
  the two carriageway reference positions.
- When a real link entry already supplies an added target lane, a redundant
  inferred split from the continuing mainline into that target lane is
  suppressed.
- When two or more exact, near-straight predecessors jointly provide exactly
  the target lane count, they are allocated as one cross-section instead of as
  independent widenings. This includes mixed motorway/link approaches up to
  45 degrees; ordinary road blocks retain the 30-degree limit. Source blocks
  are ordered by their signed lateral position up to 30 m before the node and
  assigned contiguous target blocks from driver-left to driver-right. A
  `placement=transition` source inherits the endpoints of its allocated target
  slice, while other blocks receive a minimum shared transition runway that
  prevents the two inner connectors from crossing. Ambiguous lateral ordering
  remains unchanged and emits a diagnostic.
- `GET /api/osm/lane-lines?bbox=...` queries the two independent tables with
  separate caps and returns per-kind truncation metadata.
- Every feature carries `edge_left`, `edge_right`, and `divider_left`: which
  longitudinal markings bound it, in travel order. For a lane these follow from
  its own cross-section — lane 1 is the leftmost lane of its direction and
  `lane_count` the rightmost. A connection inherits them only when it keeps the
  same lane number at both ends, which is the same lane continuing across a way
  boundary; the right-hand edge additionally requires the lane to be the
  outermost one at both ends. A split opening a lane or a join closing one moves
  the boundary across the connector, so all three stay false and the connector
  reads as junction interior. The connection query reaches both endpoint lanes
  through indexed primary-key joins, so this costs no extra round trip.

Two map layers read this endpoint. **Lanes** draws the thin `#111171` debug
hairlines described above. **Lane Detail** (`lane_detail_v2` in
`web/config.js`) renders the same payload as ground-width asphalt bands with
edge and divider markings and turn arrows, styling only the properties the
endpoint already returns:

- band width is the fixed `LANE_SPACING_M` cross-section pitch (there is no
  per-lane `width_m` here, and none is needed: the geometry is offset by that
  same constant);
- markings are a filter on `edge_left`/`edge_right`/`divider_left`, applied to
  lanes and connections alike, so a lane's markings run on across the connector
  that continues it instead of breaking for the length of the taper;
- turn arrows use the endpoint's resolved `turn_lane` token set, and need no
  counter-rotation for `bwd` lanes because `make_lane_line_rows` already stores
  their geometry in travel order. Arrows stay on `kind='lane'`: a connector is
  short, and the approach lane's arrow already announced the movement.

Both layers start switched off; enable them from the layer panel.

For a local rebuild:

```bash
python -m ndwinfo.refresh_osm_lane_lines \
  --bbox 4.649950,52.466240,4.655949,52.469521
```

The command also accepts `--all`, `--roads`, `--segments`, and
`--unresolved-only`. Reviewed manual connect/block decisions live in
`src/ndwinfo/osm_lane_connection_overrides.json`. The complete design and
validation rules are recorded in `docs/plans/osm-lane-lines-plan.md`.

To force a transactional source-topology backfill from an already-downloaded
PBF without relying on conditional HTTP metadata:

```bash
python -m ndwinfo.reingest_osm_roads \
  data/netherlands-latest.osm.pbf
```

This uses the normal extract-membership upsert/prune behavior while leaving
the independent Lanes tables unchanged, which avoids holding the national
road and lane graphs in memory simultaneously. Follow it with
`refresh_osm_lane_lines --all`; that command processes 0.2° spatial tiles in
separate transactions and reports final national counts. The scheduled
`osm_netherlands` ingester runs the same tiled Lanes rebuild automatically
after a successful changed-PBF ingest; it does not run after a 304/not-modified
check. Both `osm_lane_connection` lane foreign keys are indexed so replacing a
tile's centerlines can cascade through its connectors without scanning the
complete national connection table for every deleted lane.

## Scaling to the full Netherlands

Switched from the Noord-Holland extract to `netherlands-latest.osm.pbf`
(`config.py`'s `osm_netherlands_url`, feed `osm_netherlands`). The original
single-pass `with_locations("sparse_mem_array")` parser exceeded the practical
memory budget: RSS grew from ~0.9 GiB to ~1.6 GiB in 30 seconds while it was
still building the nationwide node index, against a 3.8-GiB Docker limit.

`parse_roads` therefore uses two PBF passes. Pass 1 collects only matching
ways and referenced node ids; pass 2 stores coordinates only for those ids.
The first production country import completed on 2026-07-27 in about 12.5
minutes, with observed RSS around 1.8 GiB during database upserts. It imported
167,169 ways and generated the national lane-line graph. The resulting extent
(3.347–7.241 E, 50.748–53.447 N) covers the Netherlands.

`/api/osm/roads` remains zoom-tiered (`_highway_types_for_zoom` in
`api/routers/osm.py`), so the API side needs no country-scale special case.

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

**Historical comparison to the retired NWB source:** OSM adds per-way
maxspeed/surface/lit/access detail and turn restrictions and updates more
frequently. OSM tagging is crowd-sourced, so completeness still varies by
contributor activity; the fixed speed matcher therefore combines OSM geometry
with VILD direction and declines ambiguous matches.

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
