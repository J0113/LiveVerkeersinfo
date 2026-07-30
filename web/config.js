'use strict'

// ─── Layer definitions ────────────────────────────────────────────────────────
//
// geomType 'point'   → MapLibre circle layer
// geomType 'polygon' → MapLibre fill + line layers (paint must have .fill / .line sub-keys)
// minZoom            → only fetch + render when map zoom >= this value

// /osm/lane-lines returns lanes and the connectors between them in one
// FeatureCollection, tagged by `kind`. Markings apply to both (the endpoint
// says which sides a connector inherits), but arrows are for lanes only: a
// connector is short, and the approach lane's arrow already announced the
// movement its curve makes.
const IS_LANE_LINE = ['==', ['get', 'kind'], 'lane']

// Cross-section spacing the independent lane lines are offset by, mirroring
// LANE_SPACING_M in parsers/osm_lane_lines.py. That endpoint carries no
// per-lane width, and its geometry is built on this constant, so it is also the
// band width that makes neighbouring lanes meet exactly.
const LANE_LINE_SPACING_M = 3.5

// The layers fed by /osm/lane-lines, in draw order. Shared with the truncation
// hint in ui.js, which reports the endpoint's per-kind caps once for both.
const LANE_LINE_LAYER_KEYS = ['lane_detail_v2', 'lanes']

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
    // Ground-width asphalt bands, edge/divider markings and turn arrows over
    // the lane-line graph. Every value it paints with comes straight off the
    // endpoint's properties: no widths, neighbours, or marking sides are worked
    // out in the browser. Lane lines are offset by a fixed cross-section pitch
    // rather than carrying a per-lane width, so LANE_LINE_SPACING_M is the band
    // width; edge_left/edge_right/divider_left come from the endpoint for both
    // kinds of feature.
    //
    // Connections are asphalt too, and the marking flags are what tell the two
    // cases apart: a connector that continues the same lane across a way
    // boundary carries the markings straight through, while a junction movement
    // that opens or closes a lane has none and reads as junction interior.
    key: 'lane_detail_v2', label: 'Lane Detail', group: 'osm',
    endpoint: '/osm/lane-lines', geomType: 'line', minZoom: 15,
    promoteId: 'id', legendColor: LANE_ASPHALT,
    // Butt caps: a round cap would push half a lane width past every segment
    // end, and lane lines are split per logical segment so those ends are
    // frequent.
    lineCap: 'butt', lineJoin: 'round',
    paint: {
      'line-color': LANE_ASPHALT,
      'line-width': metresWide(LANE_LINE_SPACING_M + LANE_SEAM_OVERLAP_M, 15),
      'line-opacity': 1
    },
    // Lane-line geometry is stored in travel order for both fwd and bwd
    // (make_lane_line_rows reverses bwd) and connectors are built in travel
    // order too, so a negative line-offset is the driver's left in every case.
    overlays: [
      {
        suffix: 'edge-left',
        filter: ['==', ['get', 'edge_left'], true],
        paint: {
          'line-color': LANE_MARKING,
          'line-offset': metresWide(-LANE_LINE_SPACING_M / 2, 15),
          'line-width': metresWideMin(0.2, 0.9, 15)
        }
      },
      {
        suffix: 'edge-right',
        filter: ['==', ['get', 'edge_right'], true],
        paint: {
          'line-color': LANE_MARKING,
          'line-offset': metresWide(LANE_LINE_SPACING_M / 2, 15),
          'line-width': metresWideMin(0.2, 0.9, 15)
        }
      },
      {
        // Inner boundary: a same-direction neighbour on the left, so a dashed
        // divider rather than a road edge. 0.15m × [20, 60] is NL's
        // 3m-line/9m-gap marking at true scale.
        suffix: 'divider',
        filter: ['==', ['get', 'divider_left'], true],
        paint: {
          'line-color': LANE_MARKING,
          'line-offset': metresWide(-LANE_LINE_SPACING_M / 2, 15),
          'line-width': metresWideMin(0.15, 0.8, 15),
          'line-dasharray': [20, 60]
        }
      }
    ],
    laneArrows: {
      minZoom: 17,
      filter: ['all', IS_LANE_LINE,
        // 'unknown' lanes (both_ways, or an unresolved oneway) have no travel
        // direction to point in.
        ['match', ['get', 'direction'], ['fwd', 'bwd'], true, false]
      ],
      layout: {
        'symbol-placement': 'line',
        'symbol-spacing': metresWide(35, 15),
        // turn_lane is the endpoint's already-resolved turn:lanes token set for
        // this lane ('left;through', …); the icon generator splits it on ';'.
        // Absent, or an empty field in the tag, means no restriction — a plain
        // through arrow, which still answers "which way does this lane run".
        'icon-image': ['concat', LANE_ARROW_PREFIX,
          ['match', ['coalesce', ['get', 'turn_lane'], ''], '', 'through', ['get', 'turn_lane']]
        ],
        'icon-size': metresWide(
          LANE_LINE_SPACING_M * ARROW_SPAN_PER_LANE_WIDTH / (ARROW_ICON_PX / ARROW_ICON_RATIO),
          15
        ),
        'icon-rotation-alignment': 'map',
        // No counter-rotation for bwd lanes: their geometry is already stored
        // in travel order.
        'icon-allow-overlap': true,
        'icon-ignore-placement': true
      }
    }
  },
  {
    // Thin physical lane centerlines, the development view of the same graph.
    // Listed after Lane Detail so the debug hairlines stay visible on top of its
    // opaque bands when both are switched on.
    key: 'lanes', label: 'Lanes', group: 'osm',
    endpoint: '/osm/lane-lines', geomType: 'line', minZoom: 15,
    promoteId: 'id', legendColor: '#111171',
    lineCap: 'butt', lineJoin: 'round',
    paint: {
      'line-color': '#111171',
      'line-width': 2,
      'line-opacity': 1
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
const layerTruncation = new Map()

// GPS-relative top HUD tiles. Toggled independently of the map layers via the
// "HUD" section at the top of the layer panel. Only shown while GPS tracks.
const HUD_ITEMS = [
  { key: 'hud_speed',  label: 'Driving speed', legendColor: '#00cc44' },
  { key: 'hud_speed_sidebar', label: 'Upcoming sensors (sidebar)', legendColor: '#00cc44' },
  { key: 'hud_matrix', label: 'Matrix signs',  legendColor: '#4488ff' },
  { key: 'hud_drips',  label: 'DRIP popups',   legendColor: '#00ccaa' }
]
const DEFAULT_HUD_ENABLED = new Set(['hud_speed', 'hud_speed_sidebar', 'hud_matrix', 'hud_drips'])
const HUD_SETTINGS_VERSION = 1
// `hud_speed_sidebar` was added after HUD preferences were first persisted.
// A legacy array therefore cannot distinguish "this option did not exist yet"
// from "the user disabled it". Migrate that old shape once, then store a
// versioned object so an explicit disable remains disabled on later loads.
const LEGACY_DEFAULT_HUD_ADDITIONS = new Set(['hud_speed_sidebar'])

// Left sidebar: how many upcoming speed sensors ahead to list, and how far.
// Both speed displays are fed exclusively by the road+carriageway+hectometre
// -scoped /api/traffic/speed fetch (fetchRoadScopedSpeedIfDue in hud.js): the
// server guarantees every candidate is on our road and carriageway, and the
// selection places them by hectometre difference, so the horizon can run long
// without a corridor gate to lose sensors round a bend. There is deliberately
// no geometric fallback pool — without a resolved road context both displays
// show nothing rather than risk another road's sensors.
// One pill per distance band, showing the nearest sensor inside it. Bands are
// fixed distances *from the vehicle*, and a sensor's distance only ever falls
// as we drive, so the list changes one pill at a time as sensors cross a
// boundary — where picking "the best spread over what happens to be in range"
// reshuffled several pills at once on every refresh. Widening bands with
// distance matches how much detail is useful: metres matter just ahead, the
// far end only needs a trend. Last bound is the horizon.
const SPEED_SIDEBAR_BANDS_M = [300, 700, 1200, 2000, 3200, 5000, 7500, 10000]
const SPEED_SIDEBAR_MAX_COUNT = SPEED_SIDEBAR_BANDS_M.length
const SPEED_SIDEBAR_MAX_DISTANCE_M = SPEED_SIDEBAR_BANDS_M[SPEED_SIDEBAR_BANDS_M.length - 1]
// The strip is scaled logarithmically around this knee: pill positions then
// depend on distance alone and drift down smoothly, instead of every pill
// shifting whenever the nearest one is passed and the anti-overlap compression
// re-tightens. Near-field detail is what a driver reads, so it gets the room.
const SPEED_SIDEBAR_SCALE_KNEE_M = 200
// Horizon for the single "next sensor" tile, which is about what is imminent
// rather than what is on the road ahead in general.
const HUD_SPEED_TILE_MAX_DISTANCE_M = 2500
// A little slack behind the anchor in the fetched hectometre window, so a
// sensor we are just passing (or a slightly stale anchor) stays in the pool.
const SPEED_SCOPE_BEHIND_M = 500

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

function loadSavedHudSet (validKeys, fallback) {
  try {
    const raw = localStorage.getItem('hudLayers')
    if (!raw) return new Set(fallback)

    const saved = JSON.parse(raw)
    if (saved?.version === HUD_SETTINGS_VERSION && Array.isArray(saved.enabled)) {
      return new Set(saved.enabled.filter(k => validKeys.has(k)))
    }

    if (Array.isArray(saved)) {
      const migrated = new Set(saved.filter(k => validKeys.has(k)))
      for (const key of LEGACY_DEFAULT_HUD_ADDITIONS) {
        if (validKeys.has(key)) migrated.add(key)
      }
      localStorage.setItem('hudLayers', JSON.stringify({
        version: HUD_SETTINGS_VERSION,
        enabled: [...migrated],
      }))
      return migrated
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
  try {
    localStorage.setItem('hudLayers', JSON.stringify({
      version: HUD_SETTINGS_VERSION,
      enabled: [...hudEnabled],
    }))
  } catch {}
}

// ─── Runtime state ────────────────────────────────────────────────────────────

const enabled = loadSavedSet('layers', new Set(LAYERS.map(l => l.key)), DEFAULT_ENABLED)
const hudEnabled = loadSavedHudSet(new Set(HUD_ITEMS.map(i => i.key)), DEFAULT_HUD_ENABLED)
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
  // Lane geometry around the vehicle: used to map-match the current road and to
  // fill in OSM name/limit for a selected sensor. Not a candidate pool.
  speedLanes: EMPTY_FC,
  osmLanes: EMPTY_FC,
  trajectPairs: EMPTY_FC,
  // The only candidate pool for the speed tile and the speed bar: sensors on
  // our road+carriageway within a hectometre window ahead
  // (GET /api/traffic/speed?road=…&carriageway=…&km_min=…&km_max=…).
  speedPointsRoad: EMPTY_FC,
}

// ─── Current-road context ────────────────────────────────────────────────────
// Road comes from OSM lane map-matching in the client; carriageway and our own
// hectometre come from GET /api/traffic/road-context. Both speed displays stay
// blank until all three are known, so they can never describe another road or
// the opposite carriageway.
const ROAD_CONTEXT_REFETCH_MS = ROAD_SIGN_HUD_REFETCH_MS
const ROAD_CONTEXT_REFETCH_DISTANCE_M = 150
const ROAD_CONTEXT_REFETCH_HEADING_DEG = 20
// Refetch on an ordinary bend, but only discard the current context immediately
// when the heading change is large enough to be a genuine reversal.
const ROAD_CONTEXT_INVALIDATE_HEADING_DEG = 120
// Beyond this the anchor site is too far away for its hectometre to describe
// our position usefully (sparsely instrumented rural stretches).
const ROAD_CONTEXT_MAX_ANCHOR_DISTANCE_M = 500
// A context older than this is stale regardless of distance moved (e.g. the
// vehicle stopped and requests failed).
const ROAD_CONTEXT_MAX_AGE_MS = 120000
// { road, carriageway, anchorKm, anchorDistanceM, coords, at } — coords is the
// position the anchor was resolved for, so it can be advanced between fetches.
let roadContext = null
let roadContextFetch = { road: null, at: 0, coords: null, heading: null, generation: 0 }

// Debounce for the road-scoped speed fetch. Identity is road *and* carriageway:
// a U-turn keeps the road but must never keep the opposite carriageway's
// sensors, so a key change refetches immediately and drops the cached points.
// `loadedKey` only updates on a successful response, so a transient failure
// leaves stale-but-valid data in place instead of blanking the displays.
// `generation` fences a slow response from a superseded key.
const ROAD_SCOPED_SPEED_REFETCH_MS = ROAD_SIGN_HUD_REFETCH_MS
let roadScopedSpeedFetch = { attemptedKey: null, attemptedAt: 0, loadedKey: null, generation: 0 }
// A GPS fix within this distance (m) of a trajectcontrole line counts as "on"
// that section — wide enough for lane offset / GPS jitter, narrow enough to
// not pick up a parallel carriageway or nearby road.
const TRAJECT_MAX_DIST_M = 35
let roadSignHudLastFetchCoords = null
let roadSignHudLastFetchAt = 0
let roadSignHudLastFetchHeading = null
const roadSignHudRenderState = {
  matrixKey: null, dripKey: null, speedKey: null, speedListKey: null,
  // road|carriageway the speed displays currently describe; a change clears
  // their linger holds so nothing survives a U-turn or a road change.
  contextKey: null,
}
let roadSignHudCurrentRoad = null
// Site keys the speed bar currently shows, so a band keeps the sensor already
// on screen when the anchor shifts (see selectSpeedSidebarBands). Cleared with
// the rest of the speed state on a road/carriageway change.
let speedSidebarShownKeys = new Set()

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
