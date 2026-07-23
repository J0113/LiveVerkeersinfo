'use strict'

// ─── Layer definitions ────────────────────────────────────────────────────────
//
// geomType 'point'   → MapLibre circle layer
// geomType 'polygon' → MapLibre fill + line layers (paint must have .fill / .line sub-keys)
// minZoom            → only fetch + render when map zoom >= this value

// A junction connector is a path across the junction box rather than a lane of
// a carriageway, so it takes the lane band but none of the lane markings.
const NOT_CONNECTOR = ['!=', ['get', 'role'], 'connector']

// Lane rendering: asphalt and the paint on it.
const LANE_ASPHALT = '#8BA5C1'
const LANE_MARKING = '#C7D8F0'
// Neighbouring bands overlap by this much so their shared edge doesn't
// antialias into a visible seam. Well under a marking's width, so it can't
// shift where a divider or the outside line lands.
const LANE_SEAM_OVERLAP_M = 0.06

const LAYERS = [
  // ── Traffic ────────────────────────────────────────────────────────────────
  {
    key: 'speed', label: 'Traffic Speed Lanes', group: 'traffic',
    endpoint: '/traffic/speed', geomType: 'speed', legendColor: '#00cc44',
  },
  {
    // Roadside speed markers. Own data source + toggle so points can stay on at
    // any zoom, independently of the zoom-gated lane lines above.
    key: 'speed_points', label: 'Traffic Speed Points', group: 'traffic',
    endpoint: '/traffic/speed', geomType: 'speed-points', legendColor: '#00cc44',
  },
  {
    // Segment line (start→end), coloured by delay = duration_s / ref_duration_s
    // (free-flow green → congested red). Segments lacking linear coordinates fall
    // back to a point in the API and won't draw on this line layer.
    key: 'traveltime', label: 'Travel Time', group: 'traffic',
    endpoint: '/traffic/traveltime', geomType: 'line', legendColor: '#cc66ff',
    arrows: true,    // direction arrows along the segment line (start→end)
    promoteId: 'fid', // enables per-feature selection state
    paint: {
      // Selected segment overrides to bright cyan + thicker; otherwise delay colour.
      'line-width': ['case', ['boolean', ['feature-state', 'selected'], false], 7, 4],
      'line-opacity': 0.9,
      // Offset to the right of travel direction so A→B and B→A don't overlap.
      'line-offset': 4,
      'line-color': ['case',
        ['boolean', ['feature-state', 'selected'], false], '#00e5ff',
        ['any',
          ['==', ['get', 'ref_duration_s'], null],
          ['==', ['get', 'duration_s'], null],
          ['<=', ['coalesce', ['get', 'ref_duration_s'], 0], 0]
        ],
        '#888888',
        ['interpolate', ['linear'],
          ['/', ['get', 'duration_s'], ['get', 'ref_duration_s']],
          1.0, '#00cc44', 1.3, '#ffdd00', 1.6, '#ff8800', 2.0, '#ff3333'
        ]
      ]
    }
  },
  {
    key: 'anwb_jams', label: 'ANWB Jams', group: 'traffic',
    endpoint: '/anwb?category=jams', geomType: 'line', legendColor: '#ff3333',
    paint: { 'line-width': 4, 'line-color': '#ff3333' }
  },

  // ── Situations ─────────────────────────────────────────────────────────────
  {
    key: 'anwb_radars', label: "ANWB Speedcamera's", group: 'situations',
    endpoint: '/anwb?category=radars', geomType: 'point', legendColor: '#00aaff',
    renderAs: 'camera-icon'
  },
  {
    // Fixed/permanent cameras from flitspalen.nl — distinct legendColor from
    // anwb_radars (dynamic/mobile reports) so both stay distinguishable when on.
    // limit raised above the shared api_default_limit (500): the verified NL
    // subset has 994 active cameras nationwide, which would otherwise silently
    // truncate at a national/zoomed-out viewport.
    key: 'flitspalen_cameras', label: "Speedcamera's", group: 'situations',
    endpoint: '/flitspalen', geomType: 'point', legendColor: '#aa33ff', limit: 1200,
    renderAs: 'camera-icon'
  },
  {
    // Trajectcontrole (SC start / SCE end) sections, precomputed at ingest time
    // by snapping each pair's straight-line gap onto the matching osm_road way
    // (see ingest/flitspalen_route.py) so the line traces the actual carriageway
    // instead of cutting cross-country between the two camera points.
    // linkedTo: no row of its own in the layer panel — rides flitspalen_cameras'
    // checkbox instead, since a trajectcontrole line without its cameras (or
    // vice versa) isn't a state a user would ever want.
    key: 'flitspalen_pairs', label: 'Trajectcontrole', group: 'situations',
    endpoint: '/flitspalen/pairs', geomType: 'line', legendColor: '#aa33ff',
    linkedTo: 'flitspalen_cameras',
    paint: { 'line-width': 2, 'line-color': '#aa33ff', 'line-opacity': 0.7, 'line-dasharray': [2, 2] }
  },
  {
    key: 'anwb_roadworks', label: 'ANWB Roadworks', group: 'situations',
    endpoint: '/anwb?category=roadworks', geomType: 'line', legendColor: '#ffaa00',
    paint: { 'line-width': 4, 'line-color': '#ffaa00' }
  },
  {
    key: 'sit_incident', label: 'Incidents', group: 'situations',
    endpoint: '/situations?category=incident', geomType: 'point', legendColor: '#ff3333',
    paint: { 'circle-radius': 8, 'circle-color': '#ff3333', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' }
  },
  {
    key: 'sit_srti', label: 'SRTI', group: 'situations',
    endpoint: '/situations?category=srti', geomType: 'point', legendColor: '#ff8800',
    paint: { 'circle-radius': 7, 'circle-color': '#ff8800', 'circle-stroke-width': 1, 'circle-stroke-color': '#fff' }
  },
  {
    key: 'sit_roadworks', label: 'Roadworks', group: 'situations',
    endpoint: '/situations?category=roadworks', geomType: 'point', legendColor: '#ffdd00',
    paint: { 'circle-radius': 7, 'circle-color': '#ffdd00', 'circle-stroke-width': 1, 'circle-stroke-color': '#222' }
  },
  {
    key: 'sit_bridge', label: 'Bridge Openings', group: 'situations',
    endpoint: '/situations?category=bridge_opening', geomType: 'point', legendColor: '#00ddff',
    paint: { 'circle-radius': 7, 'circle-color': '#00ddff', 'circle-stroke-width': 1, 'circle-stroke-color': '#fff' }
  },
  {
    key: 'sit_closure', label: 'Closures', group: 'situations',
    endpoint: '/situations?category=closure', geomType: 'point', legendColor: '#ff00aa',
    paint: { 'circle-radius': 8, 'circle-color': '#ff00aa', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' }
  },
  {
    key: 'sit_speed', label: 'Speed Limits', group: 'situations',
    endpoint: '/situations?category=speed_limit', geomType: 'point', legendColor: '#bb44ff',
    paint: { 'circle-radius': 7, 'circle-color': '#bb44ff', 'circle-stroke-width': 1, 'circle-stroke-color': '#fff' }
  },

  // ── Signs & VMS ────────────────────────────────────────────────────────────
  {
    key: 'matrix', label: 'Matrix Signs', group: 'signs',
    endpoint: '/signs/matrix', geomType: 'msi', legendColor: '#4488ff',
  },
  {
    key: 'drips', label: 'DRIPs / VMS', group: 'signs',
    endpoint: '/signs/drips', geomType: 'point', legendColor: '#00ccaa',
    paint: { 'circle-radius': 6, 'circle-color': '#00ccaa', 'circle-stroke-width': 1, 'circle-stroke-color': '#fff' }
  },

  // ── EV Charging ────────────────────────────────────────────────────────────
  {
    key: 'charging', label: 'EV Charging', group: 'charging',
    endpoint: '/charging', geomType: 'point', legendColor: '#00dd44',
    renderAs: 'charger-icon'
  },

  // ── Truck Parking ──────────────────────────────────────────────────────────
  {
    key: 'truckparking', label: 'Truck Parking', group: 'truckparking',
    endpoint: '/truckparking', geomType: 'point', legendColor: '#ffaa00',
    paint: {
      'circle-radius': 8,
      'circle-color': ['interpolate', ['linear'],
        ['coalesce', ['get', 'occupancy_pct'], -1],
        -1, '#888888', 0, '#00cc44', 60, '#ffaa00', 85, '#ff6600', 100, '#ff3333'
      ],
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#fff'
    }
  },

  // ── Zones & Signs ──────────────────────────────────────────────────────────
  {
    key: 'emission_zones', label: 'Emission Zones', group: 'other',
    endpoint: '/emission', geomType: 'polygon', legendColor: '#ff5533',
    paint: {
      fill: { 'fill-color': '#ff5533', 'fill-opacity': 0.18 },
      line: { 'line-color': '#ff5533', 'line-width': 2, 'line-opacity': 0.9 }
    }
  },
  {
    key: 'verkeersborden', label: 'Traffic Signs', group: 'other',
    endpoint: '/verkeersborden', geomType: 'point', minZoom: 13, legendColor: '#ffffff',
    paint: {
      'circle-radius': 5,
      'circle-color': '#ffffff',
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#444444'
    }
  },

  // ── VILD reference geometry ────────────────────────────────────────────────
  {
    key: 'vild_point', label: 'VILD Points', group: 'reference',
    endpoint: '/vild/points', geomType: 'point', legendColor: '#aabbff',
    paint: { 'circle-radius': 4, 'circle-color': '#aabbff', 'circle-stroke-width': 1, 'circle-stroke-color': '#fff' }
  },
  {
    key: 'vild_line', label: 'VILD Road Segments', group: 'reference',
    endpoint: '/vild/lines', geomType: 'line', legendColor: '#6699ff',
    paint: { 'line-color': '#6699ff', 'line-width': 1.5, 'line-opacity': 0.8 }
  },
  {
    key: 'vild_area', label: 'VILD Areas', group: 'reference',
    endpoint: '/vild/areas', geomType: 'polygon', legendColor: '#3366cc',
    paint: {
      fill: { 'fill-color': '#3366cc', 'fill-opacity': 0.12 },
      line: { 'line-color': '#3366cc', 'line-width': 1.5, 'line-opacity': 0.8 }
    }
  },
  // ── OpenStreetMap ──────────────────────────────────────────────────────────
  {
    // Driving-road network from a Geofabrik province extract (currently
    // Noord-Holland). highway=motorway/trunk/primary/secondary + their _link
    // ramp variants only — see docs/11-osm-pbf.md. All OSM tags are stored and
    // shown in the click popup, not a curated subset.
    // sendZoom: the API tiers highway classes by zoom (see api/routers/osm.py)
    // but the generic fetch path only sends bbox unless a layer opts in here.
    key: 'osm_roads', label: 'Driving Roads', group: 'osm',
    endpoint: '/osm/roads', geomType: 'line', minZoom: 7, sendZoom: true, legendColor: '#e8a33d',
    paint: {
      'line-color': ['match', ['get', 'highway'],
        'motorway', '#e8a33d', 'motorway_link', '#e8a33d',
        'trunk', '#d97b3f', 'trunk_link', '#d97b3f',
        'primary', '#c9584a', 'primary_link', '#c9584a',
        'secondary', '#b0455a', 'secondary_link', '#b0455a',
        '#888888'
      ],
      'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1, 12, 2.5, 16, 6],
      'line-opacity': 0.85
    }
  },
  {
    // Individual lane centerlines derived from osm_roads' `lanes` tag — see
    // docs/11-osm-pbf.md for the direction model and how a merging lane
    // converges into its neighbour. Drawn at true ground width (metresWide, in
    // lib.js) so neighbouring lanes touch and read as one carriageway instead
    // of as separate hairlines with gaps between them.
    key: 'osm_lanes', label: 'Lane Detail', group: 'osm',
    endpoint: '/osm/lanes', geomType: 'line', minZoom: 15, legendColor: LANE_ASPHALT,
    // Butt caps. A round cap would fill the small wedge that opens on the
    // outside of a bend where two ways meet (each band is its own feature, so
    // MapLibre can't join across them) — but it also pushes a 1.75m semicircle
    // past every way end, and wherever the next way is narrower or diverges
    // there's nothing to cover it. Measured on this extract that's a bad trade:
    // ~460 bend wedges over 30cm, against ~3.7k lane-count drops that would
    // sprout a visible bulge. See docs/11-osm-pbf.md.
    lineCap: 'butt', lineJoin: 'round',
    // Shared-node continuation patches are lane-width polygons. Rendering the
    // same tiny joins as thick line strings produces a full-width butt cap
    // that sticks out as a blue rectangle whenever the two tangents differ.
    fills: [{
      suffix: 'continuation',
      filter: ['==', ['get', 'continuation'], true],
      paint: { 'fill-color': LANE_ASPHALT, 'fill-opacity': 1 }
    }],
    filter: ['!=', ['get', 'continuation'], true],
    paint: {
      'line-color': LANE_ASPHALT,
      // Slightly over true width so neighbouring bands overlap by a few cm.
      // Butted exactly, their shared edge antialiases against whatever is below
      // and every lane boundary shows a pale hairline from the basemap.
      'line-width': metresWide(['+', ['get', 'width_m'], LANE_SEAM_OVERLAP_M], 15),
      'line-opacity': 1
    },
    // Dividers between lanes, drawn over the bands. Offset to one side rather
    // than stroked on both (line-gap-width strokes both edges at once, which
    // can't tell an inner boundary from the outside of the carriageway).
    // line-offset is relative to the line's own direction, and lane numbering
    // runs left-to-right in that same frame, so negative = left for every case.
    //
    // Which boundaries get one is `divider_left`, decided in the parser: it's a
    // question about a lane's *neighbour* (is the carriageway's outside on my
    // left? is one of us merging across this edge?) and a per-feature filter
    // can't see next door. See _mark_dividers in parsers/osm_lanes.py.
    // Connectors carry no divider_left at all, so they drop out here without
    // needing NOT_CONNECTOR — a junction interior has no lane lines.
    overlays: [
      {
        // Outside road edges are narrow offset strokes, not a wide casing
        // underneath every independent lane feature. A wide casing has a butt
        // cap across the full lane at every OSM way boundary; those caps leak
        // through as transverse seams. Edge strokes only end along the road's
        // perimeter, so there is nothing underneath the asphalt to leak.
        suffix: 'edge-left', filter: ['==', ['get', 'edge_left'], true],
        paint: {
          'line-color': LANE_MARKING,
          'line-offset': metresWide(['*', ['get', 'width_m'], -0.5], 15),
          'line-width': metresWideMin(0.2, 0.9, 15)
        }
      },
      {
        suffix: 'edge-right', filter: ['==', ['get', 'edge_right'], true],
        paint: {
          'line-color': LANE_MARKING,
          'line-offset': metresWide(['*', ['get', 'width_m'], 0.5], 15),
          'line-width': metresWideMin(0.2, 0.9, 15)
        }
      },
      {
        // 0.15m stroke × dasharray [20, 60] (units are line widths) is NL's
        // 3m-line/9m-gap lane marking at true scale, so it lands on the real
        // paint in the satellite basemap. Floor keeps it visible once 0.15m
        // drops under a pixel.
        suffix: 'divider', filter: ['==', ['get', 'divider_left'], true],
        paint: {
          'line-color': LANE_MARKING,
          'line-offset': metresWide(['*', ['get', 'width_m'], -0.5], 15),
          'line-width': metresWideMin(0.15, 0.8, 15),
          'line-dasharray': [20, 60]
        }
      }
    ],
    // Painted arrows: which way the lane runs, and where it's allowed to go.
    // `turn` is the lane's turn:lanes token set, absent when the way doesn't
    // tag one (or tags a count that doesn't match its lanes) — those lanes fall
    // back to a plain through arrow, which still answers "which way does this
    // lane run". Only from zoom 17: below that 4.2m of paint is a few pixels.
    //
    // Connectors are excluded like the markings are — they're short (7.7m
    // average, under two arrow lengths) and their curve already shows the
    // movement the approach lane's arrow announced.
    laneArrows: {
      minZoom: 17,
      filter: ['all', NOT_CONNECTOR,
        // both_ways and undirected lanes have no travel direction to point in.
        ['match', ['get', 'direction'], ['fwd', 'bwd'], true, false]
      ],
      layout: {
        'symbol-placement': 'line',
        'symbol-spacing': metresWide(35, 15),
        'icon-image': ['concat', LANE_ARROW_PREFIX, ['coalesce', ['get', 'turn'], 'through']],
        // Scaled off the lane's own width so a turn arrow always fits between
        // its edge lines; icon-size multiplies the icon's natural size, hence
        // the divide by it.
        'icon-size': metresWide(
          ['*', ['get', 'width_m'], ARROW_SPAN_PER_LANE_WIDTH / (ARROW_ICON_PX / ARROW_ICON_RATIO)],
          15
        ),
        'icon-rotation-alignment': 'map',
        // Lane geometry runs in travel order for fwd lanes, but a two-way way's
        // bwd lanes come back in the way's own coordinate order — so their
        // arrows would point at oncoming traffic without this.
        'icon-rotate': ['case', ['==', ['get', 'direction'], 'bwd'], 180, 0],
        // Every lane keeps its own arrow. The collision box is the glyph's
        // square bounding box, which is wider than the gap between two lanes'
        // centrelines (a 2.75m secondary lane against a ~3.3m box), so leaving
        // placement on drops all but one arrow across the whole carriageway.
        // The glyphs themselves are sized to stay inside their lane, so what
        // the collider is avoiding here isn't real overlap.
        'icon-allow-overlap': true,
        'icon-ignore-placement': true
      }
    }
  }
]

// Per data-provider attribution, decoupled from LAYERS/GROUPS (attribution is
// owed regardless of which layers are currently toggled on).
const ATTRIBUTIONS = [
  { label: 'OpenStreetMap contributors', url: 'https://www.openstreetmap.org/copyright', note: 'basemap tiles, driving-road geometry (ODbL)' },
  { label: 'CARTO', url: 'https://carto.com/attribution', note: 'basemap tiles' },
  { label: 'Esri, Maxar, Earthstar Geographics', url: 'https://www.esri.com/', note: 'satellite basemap' },
  { label: 'Nationaal Dataportaal Wegverkeer (NDW)', url: 'https://opendata.ndw.nu/', note: 'traffic, roadworks, signs, charging, truck parking, verkeersborden' },
  { label: 'ANWB', url: 'https://www.anwb.nl/', note: 'jams, roadworks, dynamic speed cameras' },
  { label: 'Flitspalen.nl', url: 'https://www.flitspalen.nl/', note: 'static speed camera locations' },
]

// UI grouping order + labels
const GROUPS = [
  { key: 'traffic',      label: 'Traffic' },
  { key: 'situations',   label: 'Situations' },
  { key: 'signs',        label: 'Signs & VMS' },
  { key: 'charging',     label: 'EV Charging' },
  { key: 'truckparking', label: 'Truck Parking' },
  { key: 'other',        label: 'Zones & Signs' },
  { key: 'reference',    label: 'Reference' },
  { key: 'osm',          label: 'OpenStreetMap' }
]

// The detailed map overlays remain available in the layer panel, but the clean
// driving view now starts with them off. Their data is fetched separately for
// the HUD, so this is a reversible presentation default rather than a removal.
const DEFAULT_ENABLED = new Set(['matrix', 'drips'])
const EMPTY_FC = { type: 'FeatureCollection', features: [] }
let bboxTooLarge = false

// GPS-relative top HUD tiles. Toggled independently of the map layers via the
// "HUD" section at the top of the layer panel. Only shown while GPS tracks.
const HUD_ITEMS = [
  { key: 'hud_speed',  label: 'Driving speed', legendColor: '#00cc44' },
  { key: 'hud_speed_sidebar', label: 'Upcoming sensors (sidebar)', legendColor: '#00cc44' },
  { key: 'hud_matrix', label: 'Matrix signs',  legendColor: '#4488ff' },
  { key: 'hud_drips',  label: 'DRIP popups',   legendColor: '#00ccaa' }
]
const DEFAULT_HUD_ENABLED = new Set(['hud_speed', 'hud_speed_sidebar', 'hud_matrix', 'hud_drips'])

// Left sidebar: how many upcoming speed sensors ahead to list, and how far.
// Once the current road is known, the sidebar is fed by the road+carriageway
// -scoped /api/traffic/speed?road=... fetch (fetchRoadScopedSpeedIfDue in
// hud.js) instead of the bbox candidate pool, so this horizon is safe to run
// long: the server already guarantees every candidate is on the correct road
// and carriageway, not just geometrically nearby. Falls back to the tighter
// bbox pool (2km ahead, see speedBbox in fetchRoadSignHud) when the road isn't
// resolved yet (cold start, no confident OSM match, on-/off-ramps).
const SPEED_SIDEBAR_MAX_COUNT = 5
const SPEED_SIDEBAR_MAX_DISTANCE_M = 10000
// Corridor cap used only for road-scoped candidates (see maxCrossM in
// selectUpcomingLaneSpeedsList) — looser than the bbox-pool default since
// direction/road are already server-verified, so this only needs to reject
// stray far-off geometry, not disambiguate nearby roads.
const SPEED_SIDEBAR_MAX_CROSS_M = 400
// Cold-start / no-confident-road-match fallback: bbox candidate pool stays
// short-range (unlike the road-scoped horizon above) since a plain bbox has no
// road/carriageway filter — a wide one would pull in stray nearby roads that
// only the tight default cross-corridor gate (LANE_SPEED_SELECT.maxCross)
// then has to reject.
const SPEED_SIDEBAR_FALLBACK_DISTANCE_M = 2000

// Restore a previously saved toggle set from localStorage, keeping only keys
// that still exist (drops renamed/removed layers). Falls back to the defaults
// when nothing is stored yet.
function loadSavedSet (storageKey, validKeys, fallback) {
  try {
    const raw = localStorage.getItem(storageKey)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr)) return new Set(arr.filter(k => validKeys.has(k)))
    }
  } catch {}
  return new Set(fallback)
}

function persistLayers () {
  try { localStorage.setItem('layers', JSON.stringify([...enabled])) } catch {}
}

// A linkedTo layer (e.g. flitspalen_pairs) has no checkbox of its own — it
// rides its parent's enabled state instead.
function layerEnabled (layer) {
  return enabled.has(layer.key) || (layer.linkedTo && enabled.has(layer.linkedTo))
}
function persistHud () {
  try { localStorage.setItem('hudLayers', JSON.stringify([...hudEnabled])) } catch {}
}

// ─── Runtime state ────────────────────────────────────────────────────────────

const enabled = loadSavedSet('layers', new Set(LAYERS.map(l => l.key)), DEFAULT_ENABLED)
const hudEnabled = loadSavedSet('hudLayers', new Set(HUD_ITEMS.map(i => i.key)), DEFAULT_HUD_ENABLED)
const controllers = {}  // key → AbortController
let debounceTimer = null
let activePopup = null
let selectedFeature = null  // { source, id } currently highlighted (feature-state)
let speedMarkers = []  // maplibregl.Marker instances for traffic speed sites
let msiMarkers = []    // { marker, el, bearing } for MSI gantries (map render)
const MATRIX_MIN_ZOOM = 11
let laneSpeedMarkers = [] // upright numeric labels snapped to matched OSM lanes

const ROAD_SIGN_HUD_MAX_DISTANCE_M = 2000
const ROAD_SIGN_HUD_REFETCH_DISTANCE_M = 100
const ROAD_SIGN_HUD_REFETCH_MS = 15000
const roadSignHudCache = {
  matrix: EMPTY_FC,
  drips: EMPTY_FC,
  speedPoints: EMPTY_FC,
  speedLanes: EMPTY_FC,
  osmLanes: EMPTY_FC,
  trajectPairs: EMPTY_FC,
  // Road+carriageway-scoped speed points (GET /api/traffic/speed?road=...),
  // covering the whole carriageway instead of just the bbox around the car.
  // Preferred source for the "next sensor" / sidebar selection once a road
  // ref is known; the bbox-based speedPoints above remain the fallback.
  speedPointsRoad: EMPTY_FC,
}
// Debounce for the road-scoped speed fetch: refetch immediately on a road
// change, otherwise no more often than a normal HUD refetch cycle.
// `attemptedRoad`/`attemptedAt` gate when a new request fires; `road` only
// updates on a successful response, so renderRoadSignHud's speedSource stays
// on stale-but-valid data through a transient failure instead of falling
// back to the bbox source every retry.
const ROAD_SCOPED_SPEED_REFETCH_MS = ROAD_SIGN_HUD_REFETCH_MS
let roadScopedSpeedFetch = { attemptedRoad: null, attemptedAt: 0, road: null }
// A GPS fix within this distance (m) of a trajectcontrole line counts as "on"
// that section — wide enough for lane offset / GPS jitter, narrow enough to
// not pick up a parallel carriageway or nearby road.
const TRAJECT_MAX_DIST_M = 35
let roadSignHudLastFetchCoords = null
let roadSignHudLastFetchAt = 0
let roadSignHudLastFetchHeading = null
const roadSignHudRenderState = { matrixKey: null, dripKey: null, speedKey: null, speedListKey: null }
let roadSignHudCurrentRoad = null

// ─── GPS & Geolocation state ──────────────────────────────────────────────────
const GPS_STATES = {
  OFF: 0,
  FOLLOW: 1,
  NAVIGATION: 2
}

let gpsState = GPS_STATES.OFF
let isTrackingSuspended = false
let geolocationWatchId = null
let userCoords = null      // [lng, lat]
let prevCoords = null      // [lng, lat]
let userAccuracy = 0      // in meters
let userHeading = null     // in degrees (0-360)
let userSpeedMps = null    // raw GPS speed in metres/second
let userLocationStatus = 'off' // off | waiting | ready | denied | error
let userMarker = null      // maplibregl.Marker

// Smooth-follow state. The GPS delivers a fix ~1×/s; a requestAnimationFrame
// loop interpolates the displayed marker + camera toward the latest fix so
// motion glides instead of jumping on each update.
let renderCoords = null     // [lng, lat] currently displayed (smoothed toward userCoords)
let renderBearing = 0       // map bearing currently displayed while navigating
let followRaf = null        // requestAnimationFrame handle for the follow loop
let lastFollowFrameAt = null // timestamp (ms) of the previous follow frame, for dt-based bearing easing
let pendingZoom = null      // one-shot zoom to snap to when (re)entering a follow state
let renderZoom = null       // smoothed dynamic zoom currently displayed in navigation mode
let manualZoomActive = false // true while user has a pinch/scroll zoom gesture in progress
let manualZoomResumeAt = 0  // timestamp (ms): dynamic speed-zoom stays suspended until this
let deviceHeading = null    // compass heading (deg, clockwise from true north) from DeviceOrientation
let orientationBound = false
let movementHeading = null  // heading derived from GPS motion only (no compass)
let lastMovingAt = null     // timestamp (ms) of last detected motion; drives the 10 s compass switch
let lastFixAt = null        // timestamp (ms) of the most recent GPS fix, for dead-reckoning
// Standing still ≥ this long → orient by the compass; otherwise steer by the GPS
// travel bearing. Below MOVING_SPEED_MPS / MOVING_DIST_M a fix counts as stopped.
const STATIONARY_COMPASS_MS = 10000
const MOVING_SPEED_MPS = 0.8   // ~2.9 km/h
const MOVING_DIST_M = 3
const DEAD_RECKON_MAX_MS = 2500 // cap forward prediction if fixes stop arriving
// How far below the map centre the user marker sits (fraction of viewport height),
// so more of the road ahead is visible — like a car-navigation view.
const FOLLOW_BOTTOM_RATIO = 0.30
// Per-frame smoothing factor (0..1): higher = snappier, lower = smoother.
const FOLLOW_POS_LERP = 0.18
// Bearing smoothing is time-based (see followTick): the displayed bearing eases
// toward the target with time-constant BEARING_SMOOTH_TAU seconds, so rotation
// speed is independent of frame rate. Larger = smoother/laggier.
const BEARING_SMOOTH_TAU = 0.45
// Skip bearing updates smaller than this (deg) to kill micro-oscillation when
// travelling near-straight; corner turns are far larger and unaffected.
const BEARING_DEADBAND_DEG = 1.5
// Exponential moving average factor for the travel heading (0..1): applied per
// GPS fix. Lower = smoother heading, less corner jitter, slightly more lag.
const HEADING_EMA_ALPHA = 0.4
// Speed (km/h) → zoom breakpoints for navigation mode, closest first. Slower
// driving zooms in for street detail; highway speed zooms out for lookahead.
const ZOOM_SPEED_CURVE = [
  [0, 17.5],
  [20, 17],
  [50, 16],
  [80, 15],
  [120, 14],
  [160, 13.3]
]
// Time constant (s) for easing renderZoom toward the speed-derived target.
const ZOOM_SMOOTH_TAU = 1.2
// After a manual pinch/scroll zoom, wait this long before dynamic zoom resumes.
const ZOOM_RESUME_DELAY_MS = 4000
