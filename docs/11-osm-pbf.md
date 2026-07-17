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
- **Ingested file**: `netherlands-latest.osm.pbf` (country extract, ~1.3 GB gz)
- **Format**: OSM PBF (protobuf), parsed with `osmium` (pyosmium)'s
  `FileProcessor` streaming iterator — not `SimpleHandler`, whose
  `way()`/`node()` callbacks can't `yield` to an outer generator.
  `with_locations("sparse_mem_array")` resolves way geometry from node
  coordinates in one pass (verified ~910MB peak RSS for this extract's
  ~18.6M nodes; a full-Netherlands extract will need its own RSS check
  before deploying — see "Scaling to the full Netherlands" below).
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

Every driving way gets lanes; the only ways without any are the 33
`oneway=reversible` motorways deliberately skipped below.

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
  centered. `turn:lanes:forward`/`turn:lanes:backward` drive merging.
  Backward lanes are doubly reversed relative to the way's coordinates, and
  the two reversals are independent: tokens are ordered left-to-right from
  the backward driver's perspective, so they map onto physical lane position
  in **reverse** order; and `merge_to_left` means *that driver's* left,
  which is the opposite physical side. A backward merge also completes at
  the way's **start**, not its end. (Only 8 ways in this extract merge on a
  backward block, versus 792 on `turn:lanes` — all of which are `oneway=yes`.)
- Two-way with a `lanes` total but no directional tag: **an even total splits
  down the middle** — NL drives on the right, so the left half of the
  cross-section is oncoming (`bwd`) and the right half `fwd`, roles `normal`.
  No tag is needed to know that, and it's the common case: **1,646 of this
  extract's 1,696 two-way `lanes=2` ways** carry no `lanes:forward`/`:backward`
  (plus 30 more at `lanes=4`). It's the same convention `_assumed_two_way_lanes`
  applies to a road with no `lanes` at all, just with a real count behind it.
- Two-way, **odd** total, no directional tag: `direction='unknown'`,
  `role='unknown'`, **not tapered**. An odd count can't be halved and which
  direction gets the extra lane isn't derivable — `lanes=1` is one lane shared
  both ways (1,304 ways), and `lanes=3` could be 2+1 either way round (9 ways;
  the other 267 tag the split). A generic `turn:lanes` on such a cross-section
  can't be attributed to a physical lane either. (An earlier draft assumed an
  odd total implied a centre-turn lane — checked against real data and found
  wrong: only 1 way in the extract tags `lanes:both_ways`.)
- Cardinality guard: if a `turn:lanes`/`turn:lanes:forward`/`turn:lanes:backward`
  token count doesn't match the lane count it applies to, the tokens are
  ignored for that way (`role='unknown'` for all its lanes) rather than
  risking a token landing on the wrong physical lane.

**No `lanes` tag** (3,205 ways here — motorway is 100% tagged, but 23% of
secondary and 13% of primary aren't): fall back to what OSM's own defaults
imply rather than drawing nothing.

- `oneway=yes|true|1|-1` → **1 lane**.
- anything else → **1 lane each way** (`fwd` + `bwd`, roles `normal`), not
  two `direction='unknown'` lanes. An untagged two-way road is 1+1; that's a
  convention, not the inference the `unknown` case above refuses to make
  (which is *how a tagged N-lane total splits* between directions).
- Except where `turn:lanes` is present on a oneway (7 ways): its token count
  **is** the lane count, one token per lane by definition, so it's used
  instead of defaulting to 1.

Defaulted lanes carry **`lanes_assumed: true`** in their properties, so a
consumer can tell a drawn lane from a counted one. The defaults don't
override the direction model's refusals — `oneway=reversible` is still
skipped.

**Merge scope**: only `turn:lanes` tokens `merge_to_left`/`merge_to_right`
reshape a lane *along its own way*. Turn-only tokens (`left`/`right`/
`through`/`slight_right`/`slight_left` alone) leave the lane a plain
full-length offset — a turn lane at a junction still exists up to the stop
line, and `slight_right` alone is often just a normal lane on a curving
road, not necessarily an exit. **Some real motorway exits tagged only
`slight_right` will not appear to merge** — an intentional scope limit, not
a bug. A merge tagged on an edge lane with no neighbour on that side (6 ways
in this extract, e.g. `lanes=1` + `merge_to_right`) is also left as a plain
offset: there's nothing to converge onto.

Those turn-only tokens do drive junction connectors, though — see
"Junction connectors" below. The two models don't overlap: merges reshape a
lane within its way, connectors add new geometry between ways.

## Merge geometry (`parsers/osm_lanes.build_merge_index`)

Over the last `min(MAX_TAPER_M, chain length)` the **whole cross-section
moves**: the merging lane converges onto its neighbour and the survivors
re-centre onto what's left. The merging lane is not shortened — an earlier
draft trimmed it instead, which read on the map as a lane simply missing
(A200 way 7400291 lost half its left lane).

`MAX_TAPER_M` is **25m**, set against aerial imagery: the paint holds a full
lane's width until close to the merge point and then pinches over a short
taper. An earlier 150m spread the drift so far back that lanes read as too
narrow along most of a ramp.

**Why the survivors move too.** Lane offsets are measured from the way's own
line, and OSM draws that line down the middle of the carriageway it
describes. So when a chain ends, the next way's line is the centre of a
cross-section one lane narrower — and the two lines *meet* at the shared
node. Holding the survivors at their original offsets therefore left every
chain end half a lane width (1.75m) short of meeting the next way's lanes.
That's the common case, not an edge case: of the merge ways with a
non-merging successor, 273 go 2→1 lanes, 140 go 3→2, 52 go 4→3, 15 go 5→4.
So `_resolve_merge_transition` re-numbers the surviving lanes into an
M-lane cross-section and gives each merging lane its target's *final*
position. On the A200 chain, both lanes now finish dead on the shared node
where the 1-lane way `317075524` begins (extract-wide: 825 of 831 survivor
handovers within 0.5m, average 0.05m).

The transition is measured over the **chain**, not the way, because OSM
routinely splits one physical merge across several consecutive ways —
A200 `7400291` (63m) → `1014194650` (47m) are both tagged `merge_to_right|`,
and 230 of the 792 merge ways here have a merge-tagged successor. Converging
per-way would snap the lane back to full offset at every shared node. So
before any lane geometry is built, merge-tagged ways are linked into chains
by shared endpoints, giving each way both its distance to the chain's merge
point and the chain's total length. Ways only chain to a successor with the
**same set of merge roles** — two consecutive ways merging *different* lanes
are two adjacent transitions, not one spread across both.

The chain is keyed per way and per direction (`(osm_id, merge_dir)`), not
per lane: which end of the way the transition completes at is a property of
the way, since every lane re-centres toward the same end of it. A way with
both a forward and a backward merge would have to re-centre toward both ends
at once, so it's left alone.

Chains are really **trees** — two carriageways can flow into the same way —
so the taper length is sized by the longest branch feeding a merge point and
shared by every way in that tree; otherwise the convergence would kink where
the branches join. A fork (one way whose exit is claimed by two merge ways)
is not treated as a chain link and simply terminates there.

Ingest stays single-pass: `has_merge_tokens` is a tag-only test, so
`OsmRoadIngester` streams every non-merging way straight through and buffers
only the few hundred merge ways until the chain index can be built.

**Known residual**: where a chained way's `lanes` count changes across the
shared node (22 of 210 handovers here, worst ~5m on a 6→3 drop), the whole
cross-section shifts and lane lines step sideways. That's inherent to
offsetting each way independently from its own centerline and affects normal
lanes just as much as merging ones — not specific to the merge model.

`GET /api/osm/lanes` — plain bbox + deterministic-order + cap/`truncated`,
no zoom-based class tiering (unlike `/api/osm/roads` — lanes are already a
detail-zoom-only layer, gated client-side via the `osm_lanes` layer's
`minZoom: 15`). `osm_road_lane.source_id` has `ON DELETE CASCADE` to
`osm_road.osm_id`, so the existing extract-scoped prune on `osm_road`
cleans up lanes automatically — no separate lane-level extract tracking.

## Rendering the lane layer (`web/config.js` `osm_lanes`)

Drawn opaque at **true ground width** rather than as hairlines, so
neighbouring lanes touch and read as one carriageway. MapLibre's
`line-width` is screen pixels with no metre unit, so `metresWide()` (in
`web/lib.js`) converts:
the Mercator world is 512·2^zoom px wide, so px-per-metre doubles every zoom
level and an `['exponential', 2]` zoom interpolation reproduces it exactly.
Two gotchas that are easy to hit again:

- The zoom interpolation must be the **outermost** expression — MapLibre
  rejects `['zoom']` nested inside anything else (`"zoom" expression may
  only be used as input to a top-level "step" or "interpolate" expression`),
  and it does so by firing an error event and *silently not adding the
  layer*, not by throwing. So the scale factor is folded into each stop's
  output (`['*', ['get', 'width_m'], pxPerMetre(z)]`), and the divider's
  minimum pixel width is applied per stop (`metresWideMin`) instead of via
  an outer `['max']`.
- Latitude is pinned to NL's midpoint (52.2°): `cos(lat)` varies ~6%
  country-wide, i.e. under 0.2m on a 3.5m lane.

The band is asphalt grey (`LANE_ASPHALT`), drawn a few centimetres over true
width (`LANE_SEAM_OVERLAP_M`) so neighbouring bands overlap. Butted exactly,
their shared edge antialiases against whatever is below and every lane boundary
shows a pale hairline.

**The outside line is a casing under the bands, not a layer per edge.** Every
lane casts a `LANE_MARKING` outline slightly wider than itself; a lane with a
neighbour gets its outline painted over by that neighbour's band. What survives
is exactly the edge no lane sits beyond — the carriageway's outside — with no
filter enumerating which edges qualify. Being *under* is the point: where a lane
tapers away into a merge, the lane it merges into covers the stale edge rather
than leaving it stranded mid-asphalt. Connectors are filtered out (`casingFilter`):
a junction interior has no edge lines.

**Dividers** are an overlay over the band (`overlays:` in the layer config,
rendered by `map.js` as `<key>-<suffix>`), so no divider geometry is generated.
They use **`line-offset`**, not `line-gap-width`: gap-width strokes both edges of
a lane at once and so can't tell an internal boundary from the outside.
`line-offset` is relative to the line's own direction, and lane numbering runs
left-to-right in that same frame, so negative is left in every case. Since
`line-dasharray` is in units of line width, a 0.15m stroke × `[20, 60]` is NL's
3m-line/9m-gap lane marking at true scale, which lands on the actual paint in the
satellite basemap.

Which boundaries get one is **`divider_left`, decided in the parser**
(`_mark_dividers`), because it's a question about a lane's *neighbour* and a
per-feature style filter can't see next door. Each internal boundary is drawn
once, as the left edge of the lane on its right:

- The cross-section's leftmost lane gets none — that edge is the outside, which
  the casing already draws. This is per *cross-section*, not per direction block:
  on a two-way road the forward block's lane 1 is the centreline, not an outside
  edge, so it does get one.
- A boundary two lanes are merging across gets none: the merging lane's edge
  sweeps sideways as it converges, so a line on it would drag a diagonal across
  the asphalt. The merge arrows carry that meaning instead. Checked both ways
  round — `merge_to_left` crosses its own left edge, but `merge_to_right`
  crosses its right-hand neighbour's, an asymmetry a style filter can't express.
- Connectors carry no `divider_left` at all, so they drop out without needing an
  explicit exclusion.

## Junction connectors (`parsers/osm_junctions.py`)

`turn:lanes=left|left|through|right` says where each lane goes; connectors
turn that into curved geometry from each approach lane to the lane it feeds
on the way it turns onto, so a carriageway reads as continuing through a
junction instead of stopping at it. Rows land in `osm_road_lane` with
`role='connector'` and `source_id` = the approach way (so the existing
extract-scoped prune cleans them up), plus `raw.turn` / `raw.to_osm_id` /
`raw.to_lane` recording the movement.

**A junction is a box, not a node.** OSM routinely models one intersection
as several nodes metres apart. At the Provincialeweg junction (way
1267507394, `left|left|through|right`) only the *through* way starts at the
approach's end node — its left target starts **18m away** at a different
node. Measured over this extract's **5,691** turn-tagged ways, requiring an
exit to start at the approach's end node versus taking any exit starting
within `JUNCTION_RADIUS_M` (25m):

| resolves…            | shared node | 25m radius |
| -------------------- | ----------: | ---------: |
| any movement         |       4,998 |      5,233 |
| a `left` target      |         437 |        973 |
| both `left`+`right`  |           4 |        347 |

Shared-node matching already finds most *through* movements — a way's
continuation does start at its end node — so the radius buys little there
(+5%). What it unlocks is turns: 2.2× the `left` targets, and left+right
sets go from negligible to 347.

How a movement resolves:

- Each token has an idealised angle (`left` -90°, `slight_left` -35°,
  `through`/`none` 0°, `slight_right` +35°, `right` +90°, `sharp_*` ±135°).
  The exit whose real turn angle is nearest wins, within
  `ANGLE_TOLERANCE_DEG` (50°). `merge_to_*` is not a junction movement (the
  merge model owns it), and `reverse` isn't attempted — a U-turn's exit is
  indistinguishable from the opposite carriageway by angle alone, which is
  also why anything past `MAX_TURN_DEG` (160°) is rejected outright.
- Lanes turning the same way feed that exit's lanes in the same left-to-right
  order. More turning lanes than the exit has (they merge past the junction)
  all land on its last one.
- A lane feeds an exit once even if two of its tokens point there
  (`left;slight_left` onto one way is one movement).
- Same cardinality guard as the lane model: a token count that doesn't match
  the lane count is ignored rather than misattributed.
- Only `oneway` ways take part, as approach or exit. 5,679 of the extract's
  5,691 `turn:lanes` ways are oneway, so this costs almost nothing on the
  approach side; a two-way approach would need its exits filtered by which
  direction is legal to enter — not worth guessing. (A further 309 ways carry
  `turn:lanes:forward`/`:backward` and are skipped entirely: they'd need a
  record per direction, and a two-way way's lane geometry runs in the way's
  own coordinate order, not travel order, for its `bwd` lanes.)
- If the approach lane and exit lane already touch (under
  `MIN_CONNECTOR_M`), no connector: the bands meet without help.

Geometry is a cubic Bézier in RD metres, leaving tangent to the approach and
arriving tangent to the exit, with handles at a third of the span — so it
draws a corner rather than a chord.

The pass costs no second PBF read and no way buffering: `junction_record`
keeps two coordinates per lane off rows that were computed anyway, and exits
are found through a grid hash rather than an O(ways²) scan.

**Coverage limit.** Split by movement, not by one number: **5,233 of 5,691**
turn-tagged ways (92%) resolve at least one movement, but only **973** find a
`left` target. Straight-on movements nearly always resolve; genuine turns
often don't, because this project ingests motorway/trunk/primary/secondary +
links and junction branches are typically tertiary/unclassified/residential —
out of scope (see "Out of scope" below). At token level ~**4,000 of ~16,000**
turn tokens find no exit, overwhelmingly `left` and `right`.

Of the 5,233 ways that resolve, **3,947** actually emit connector rows
(**8,671** in total); the rest resolve only movements whose exit already
touches the approach, which need no connector.

The Provincialeweg example is exactly this: its two `left` lanes and its
`through` connect, its `right` leads to a road we don't carry, so that lane
gets no connector. Widening the ingested classes is the lever if more
coverage is ever wanted — **not** the oneway restriction below: allowing
two-way ways as exits was measured to resolve only **103** more tokens while
changing 20 existing targets, so it isn't worth the ambiguity it buys.

**Rendering.** Connectors take the lane band but none of the lane markings
(`NOT_CONNECTOR` in `web/config.js`) — a junction interior carries no lane
lines in reality, and a connector is a path across the box rather than a lane
of a carriageway. The band still draws, so the asphalt reads as continuous.

## Where lanes don't join up (and why)

Each way's lanes are their own features, so MapLibre can't join across a
shared node — that's a data-model limit, not a paint one. Two distinct
symptoms, neither currently fixed:

**Bend wedges.** Where consecutive ways kink (~22° at the A200 chain's
shared node), each way's offset lane ends perpendicular to *its own* end
segment, so the outside of the bend opens a small wedge and the markings
step across it. Measured over the 9,012 lane handovers between ways with
matching cross-sections: average 0.10m, but 460 over 30cm and 220 over 1m
(the worst are near-hairpin link geometry, up to 4.5m).

`line-cap: round` on the band closes these — and was tried and reverted.
It also pushes a 1.75m semicircle past *every* way end, and wherever the
next way is narrower or diverges there's nothing to cover it, so ~3.7k
lane-count drops sprout a visible bulge into the gore. Fixing it properly
means mitering lane ends against the next way's, which needs every
lane-carrying way buffered rather than just the merge ways.

**Gores.** 7,539 way pairs change lane count across a shared node with no
merge tag to say which lane went. This is the shape in a diverge — a 3-lane
motorway ending at one node where a 2-lane motorway *and* a 1-lane link both
begin. OSM models the split as a point, so at that node the mainline's lanes
and the link's lane would all have to occupy the same spot while the real
carriageways separate gradually. No lane geometry derived from OSM way
centrelines can render a gore faithfully; renderers that do it well use road
polygons or dedicated lane data, not way geometry.

**Carriageway splits** are the tractable subset of that, and not rare: at
**817** nodes a two-way way meets exactly two oneway ways with the lane count
conserved (2 = 1+1) — a two-way road becoming a dual carriageway. Provincialeweg
way 565536411 → 6627417 + 565536408 is one. Nothing is lost here, so it isn't a
gore: each branch continues one of the parent's lanes, and the only reason it
doesn't join up is that OSM starts both branch centrelines *at* the shared node.
Their bands then cover ~3.5m where the parent covers 7m, leaving a half-lane
(~1.75m) step at the node.

The fix is the merge taper run backwards: start each branch's lane at the
parent's matching lane offset and settle it onto its own centreline over a short
distance. Unlike a true gore that's well defined. It needs what the merge index
needs and then some — node ids off the PBF (`parse_roads` doesn't surface them
today) and every lane-carrying way buffered rather than just the merge-tagged
ones. A further 512 such nodes are missing a `lanes` tag on some way (OSM's 1+1
default would cover most), and only **196** genuinely don't conserve their lane
count.

## Scaling to the full Netherlands

Switched from the Noord-Holland extract to `netherlands-latest.osm.pbf`
(`config.py`'s `osm_netherlands_url`, feed `osm_netherlands`). **Peak RSS on
this extract is not yet re-benchmarked** — nationwide node count could push
`with_locations("sparse_mem_array")` into multi-GB territory (Noord-Holland's
18.6M nodes cost ~910MB; NL has ~10x the population/road density). Watch the
first real ingest's memory use; if it blows up, fall back to a two-pass parse
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
