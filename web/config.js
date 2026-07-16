'use strict'

// ─── Layer definitions ────────────────────────────────────────────────────────
//
// geomType 'point'   → MapLibre circle layer
// geomType 'polygon' → MapLibre fill + line layers (paint must have .fill / .line sub-keys)
// minZoom            → only fetch + render when map zoom >= this value

const LAYERS = [
  // ── Local OSM road graph ─────────────────────────────────────────────────
  // Normal runtime path: geometry and accepted speed bindings are read from
  // the local PostGIS graph. This never contacts Overpass.
  {
    key: 'osm_roads', label: 'OSM-wegen + gekoppelde snelheid', group: 'traffic',
    endpoint: '/roads', geomType: 'local-osm-roads', minZoom: 12,
    legendColor: '#5ba4d6', promoteId: 'internal_segment_id', arrows: true,
    arrowOffset: [0, 3],
    casing: {
      'line-color': 'rgba(8, 20, 31, 0.86)',
      'line-width': ['interpolate', ['linear'], ['zoom'], 12, 3, 15, 6, 18, 12],
      'line-opacity': 0.88,
      'line-offset': ['match', ['get', 'travel_direction'], 'reverse', -1.3, 1.3]
    },
    paint: {
      'line-color': ['case',
        ['all',
          ['==', ['get', 'speed_usable'], true],
          ['!=', ['get', 'speed_kmh'], null],
          ['!=', ['get', 'speed_stale'], true]
        ],
        ['interpolate', ['linear'], ['to-number', ['get', 'speed_kmh']],
          0, '#c8324a', 25, '#e34b3f', 45, '#ef8b36',
          65, '#f2d14a', 85, '#62c86b', 110, '#23a96a'
        ],
        '#73869b'
      ],
      'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1.6, 15, 3.6, 18, 8],
      'line-opacity': ['case',
        ['!=', ['get', 'speed_usable'], true], 0.52,
        ['==', ['get', 'speed_method'], 'measured'], 0.98,
        ['==', ['get', 'speed_method'], 'interpolated'], 0.76,
        0.58
      ],
      'line-offset': ['match', ['get', 'travel_direction'], 'reverse', -1.3, 1.3]
    }
  },

  // ── Legacy external Overpass proof of concept ───────────────────────────
  // Explicit diagnostics only. The normal road layer above is local and fast.
  {
    key: 'osm_poc', label: 'Legacy Overpass-diagnose (traag)', group: 'poc',
    endpoint: '/poc/osm/roads', geomType: 'osm-poc', minZoom: 13,
    legendColor: '#6ee7ff'
  },

  // ── Road network foundation ───────────────────────────────────────────────
  // Added first so all existing traffic layers and interactive markers remain
  // above the reference geometry.
  {
    key: 'nwb_roads', label: 'NWB Road Network', group: 'reference',
    endpoint: '/nwb/roads', geomType: 'road-network', minZoom: 9,
    legendColor: '#3f78a8', promoteId: 'segment_id',
    paint: {
      casing: {
        'line-color': 'rgba(8, 20, 31, 0.82)',
        'line-width': ['interpolate', ['linear'], ['zoom'], 9, 2.2, 12, 3.8, 16, 8.5],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 9, 0.5, 12, 0.72, 16, 0.9]
      },
      line: {
        'line-color': ['match', ['get', 'road_class'],
          'motorway', '#5ba4d6',
          'primary', '#4b87b4',
          '#507084'
        ],
        'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.1, 12, 2.1, 16, 5.2],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 9, 0.68, 12, 0.82, 16, 0.92]
      }
    }
  },

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

  // ── Situations ─────────────────────────────────────────────────────────────
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
    // 'open' property proxies for availability — green when open, grey otherwise
    paint: {
      'circle-radius': 6,
      'circle-color': ['case', ['==', ['get', 'open'], true], '#00dd44', '#666666'],
      'circle-stroke-width': 1,
      'circle-stroke-color': 'rgba(0,0,0,0.35)'
    }
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
  {
    // Separate, 3.5m-offset lane centrelines derived from WEGGEG Rijstroken.
    // A future speed matcher can set `speed_kmh` and use this existing palette.
    key: 'weggeg_lanes', label: 'WEGGEG Lanes', group: 'reference',
    endpoint: '/weggeg/lanes', geomType: 'line', minZoom: 14, legendColor: '#dbe8ef',
    casing: {
      'line-color': '#24465b',
      'line-width': ['interpolate', ['linear'], ['zoom'], 14, 2.5, 17, 5.5, 20, 10],
      'line-opacity': 0.94
    },
    paint: {
      'line-color': ['case',
        ['has', 'speed_kmh'],
        ['interpolate', ['linear'], ['coalesce', ['get', 'speed_kmh'], 0],
          0, '#8a8a8a', 30, '#ff3333', 50, '#ff8800', 70, '#ffdd00', 90, '#00cc44'
        ],
        '#dbe8ef'
      ],
      'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1, 17, 3, 20, 7],
      'line-opacity': 0.98
    }
  }
]

// UI grouping order + labels
const GROUPS = [
  { key: 'poc',          label: 'Diagnostiek (handmatig)' },
  { key: 'traffic',      label: 'Traffic' },
  { key: 'situations',   label: 'Situations' },
  { key: 'signs',        label: 'Signs & VMS' },
  { key: 'charging',     label: 'EV Charging' },
  { key: 'truckparking', label: 'Truck Parking' },
  { key: 'other',        label: 'Zones & Signs' },
  { key: 'reference',    label: 'Reference' }
]

// The detailed map overlays remain available in the layer panel, but the clean
// driving view now starts with them off. Their data is fetched separately for
// the HUD, so this is a reversible presentation default rather than a removal.
const DEFAULT_ENABLED = new Set(['osm_roads'])
const EMPTY_FC = { type: 'FeatureCollection', features: [] }
let bboxTooLarge = false
let nwbTruncated = false
let osmRoadsTruncated = false

// GPS-relative top HUD tiles. Toggled independently of the map layers via the
// "HUD" section at the top of the layer panel. Only shown while GPS tracks.
const HUD_ITEMS = [
  { key: 'hud_speed',  label: 'Driving speed', legendColor: '#00cc44' },
  { key: 'hud_matrix', label: 'Matrix signs',  legendColor: '#4488ff' },
  { key: 'hud_drips',  label: 'DRIP popups',   legendColor: '#00ccaa' }
]
const DEFAULT_HUD_ENABLED = new Set(['hud_speed', 'hud_matrix', 'hud_drips'])

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
function persistHud () {
  try { localStorage.setItem('hudLayers', JSON.stringify([...hudEnabled])) } catch {}
}

// ─── Runtime state ────────────────────────────────────────────────────────────

const enabled = loadSavedSet('layers', new Set(LAYERS.map(l => l.key)), DEFAULT_ENABLED)
// One-time runtime migration: remove the old automatically enabled external
// Overpass POC and introduce the local graph. After this migration both toggles
// remain fully user-controlled; Overpass can only be re-enabled explicitly.
try {
  if (!localStorage.getItem('osmLocalRuntimeV1')) {
    enabled.delete('osm_poc')
    enabled.add('osm_roads')
    localStorage.setItem('osmLocalRuntimeV1', '1')
    persistLayers()
  }
} catch {}
const hudEnabled = loadSavedSet('hudLayers', new Set(HUD_ITEMS.map(i => i.key)), DEFAULT_HUD_ENABLED)
const controllers = {}  // key → AbortController
let debounceTimer = null
let activePopup = null
let selectedFeature = null  // { source, id } currently highlighted (feature-state)
let speedMarkers = []  // maplibregl.Marker instances for traffic speed sites
let msiMarkers = []    // { marker, el, bearing } for MSI gantries (map render)
const MATRIX_MIN_ZOOM = 11
const nwbCache = new Map() // viewport/profile key → { expires, data }
const NWB_BROWSER_CACHE_TTL_MS = 5 * 60_000
const osmRoadCache = new Map() // local viewport key → { expires, data }
const OSM_ROAD_BROWSER_CACHE_TTL_MS = 60_000
let publicConfig = { nwbDiagnosticMode: false }
let laneSpeedMarkers = [] // upright numeric labels snapped to WEGGEG lanes
const trafficSpeedMapCache = new Map() // request key → { expires, data }
const trafficSpeedMapInflight = new Map() // request key → Promise
const TRAFFIC_SPEED_MAP_CACHE_TTL_MS = 5_000

const ROAD_SIGN_HUD_MAX_DISTANCE_M = 2000
const ROAD_SIGN_HUD_REFETCH_DISTANCE_M = 100
const ROAD_SIGN_HUD_REFETCH_MS = 15000
const roadSignHudCache = { matrix: EMPTY_FC, drips: EMPTY_FC, speedPoints: EMPTY_FC }
let roadSignHudLastFetchCoords = null
let roadSignHudLastFetchAt = 0
let roadSignHudLastFetchHeading = null
const roadSignHudRenderState = { matrixKey: null, dripKey: null, speedKey: null }

// WEGGEG lane centrelines are 3.5 m apart. MapLibre line widths are expressed
// in screen pixels, so a nearly linear zoom interpolation makes lanes look the
// same width on screen while the road beneath them doubles every zoom level.
// These stops approximate 3.5 physical metres at Dutch latitudes (~52° N).
// Exponential interpolation preserves that scale between integer zoom levels.
const TRAFFIC_LANE_FILL_WIDTH_PX = [
  'interpolate', ['exponential', 2], ['zoom'],
  13, 0.75,
  14, 0.75,
  15, 1.02,
  16, 2.05,
  17, 4.10,
  18, 8.20,
  19, 16.39,
  20, 32.79,
  21, 65.58
]
const TRAFFIC_LANE_CASING_WIDTH_PX = [
  'interpolate', ['exponential', 2], ['zoom'],
  13, 1.50,
  14, 1.50,
  15, 2.09,
  16, 3.38,
  17, 5.87,
  18, 10.73,
  19, 20.53,
  20, 39.87,
  21, 78.25
]

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
