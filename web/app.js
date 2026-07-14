'use strict'

// ─── Layer definitions ────────────────────────────────────────────────────────
//
// geomType 'point'   → MapLibre circle layer
// geomType 'polygon' → MapLibre fill + line layers (paint must have .fill / .line sub-keys)
// minZoom            → only fetch + render when map zoom >= this value

const LAYERS = [
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
    key: 'speed', label: 'Traffic Speed', group: 'traffic',
    endpoint: '/traffic/speed', geomType: 'speed', legendColor: '#00cc44',
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
    endpoint: '/signs/matrix', geomType: 'msi', legendColor: '#4488ff', hudOnly: true,
  },
  {
    key: 'drips', label: 'DRIPs / VMS', group: 'signs',
    endpoint: '/signs/drips', geomType: 'point', legendColor: '#00ccaa', hudOnly: true,
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
const DEFAULT_ENABLED = new Set(['matrix', 'drips'])
const EMPTY_FC = { type: 'FeatureCollection', features: [] }
let bboxTooLarge = false
let nwbTruncated = false

// ─── Runtime state ────────────────────────────────────────────────────────────

const enabled = new Set(DEFAULT_ENABLED)
const controllers = {}  // key → AbortController
let debounceTimer = null
let activePopup = null
let selectedFeature = null  // { source, id } currently highlighted (feature-state)
let speedMarkers = []  // maplibregl.Marker instances for traffic speed sites
const nwbCache = new Map() // viewport/profile key → { expires, data }
const NWB_BROWSER_CACHE_TTL_MS = 5 * 60_000
let publicConfig = { nwbDiagnosticMode: false }
let laneSpeedMarkers = [] // upright numeric labels snapped to WEGGEG lanes

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
const userHeadingHistory = []

// Smooth-follow state. The GPS delivers a fix ~1×/s; a requestAnimationFrame
// loop interpolates the displayed marker + camera toward the latest fix so
// motion glides instead of jumping on each update.
let renderCoords = null     // [lng, lat] currently displayed (smoothed toward userCoords)
let renderBearing = 0       // map bearing currently displayed while navigating
let followRaf = null        // requestAnimationFrame handle for the follow loop
let pendingZoom = null      // one-shot zoom to snap to when (re)entering a follow state
let deviceHeading = null    // compass heading (deg, clockwise from true north) from DeviceOrientation
let orientationBound = false
// How far below the map centre the user marker sits (fraction of viewport height),
// so more of the road ahead is visible — like a car-navigation view.
const FOLLOW_BOTTOM_RATIO = 0.30
// Per-frame smoothing factors (0..1): higher = snappier, lower = smoother.
const FOLLOW_POS_LERP = 0.18
const FOLLOW_BEARING_LERP = 0.18

// ─── Map ──────────────────────────────────────────────────────────────────────

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      carto: {
        type: 'raster',
        tiles: [
          'https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
          'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
          'https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png'
        ],
        tileSize: 256,
        maxzoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/attribution">CARTO</a>'
      }
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'carto' }]
  },
  center: [5.3, 52.1],
  zoom: 7,
  attributionControl: false,
  maplibreLogo: false,
  // Sync view (zoom/lat/lng/bearing/pitch) to the URL hash so a refresh restores it.
  hash: true
})

// Keep the bottom edge intentionally quiet: source credits remain available
// through the standard compact information button.
map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')

// ─── Map load: wire up sources, layers, and UI ────────────────────────────────

map.on('load', () => {
  const attribution = document.querySelector('.maplibregl-ctrl-attrib')
  if (attribution) {
    attribution.removeAttribute('open')
    attribution.classList.remove('maplibregl-compact-show')
  }

  addArrowImage()

  for (const layer of LAYERS) {
    // Road signs are rendered only in the GPS-relative top HUD.
    if (layer.hudOnly) continue

    const srcOpts = { type: 'geojson', data: EMPTY_FC }
    if (layer.promoteId) srcOpts.promoteId = layer.promoteId
    map.addSource(layer.key, srcOpts)
    const vis = enabled.has(layer.key) ? 'visible' : 'none'

    if (layer.geomType === 'road-network') {
      map.addLayer({
        id: `${layer.key}-casing`, type: 'line', source: layer.key,
        paint: layer.paint.casing,
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' },
        minzoom: layer.minZoom
      })
      map.addLayer({
        id: layer.key, type: 'line', source: layer.key,
        paint: layer.paint.line,
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' },
        minzoom: layer.minZoom
      })
      setupNwbDiagnostic(layer.key)
    } else if (layer.geomType === 'speed') {
      map.addLayer({
        id: 'speed-lanes-casing',
        type: 'line',
        source: layer.key,
        paint: {
          'line-color': '#1d3240',
          'line-width': TRAFFIC_LANE_CASING_WIDTH_PX,
          'line-opacity': 0.94
        },
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' }
      })
      map.addLayer({
        id: 'speed-lanes',
        type: 'line',
        source: layer.key,
        paint: {
          'line-color': ['case',
            ['==', ['get', 'speed_kmh'], null], '#777777',
            ['interpolate', ['linear'], ['get', 'speed_kmh'],
              0, '#8a8a8a', 30, '#ff3333', 50, '#ff8800',
              70, '#ffdd00', 90, '#00cc44'
            ]
          ],
          'line-width': TRAFFIC_LANE_FILL_WIDTH_PX,
          'line-opacity': 0.98
        },
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' }
      })
      setupClickPopup('speed-lanes')
    } else if (layer.geomType === 'polygon') {
      map.addLayer({ id: `${layer.key}-fill`, type: 'fill', source: layer.key, paint: layer.paint.fill, layout: { visibility: vis } })
      map.addLayer({ id: `${layer.key}-line`, type: 'line', source: layer.key, paint: layer.paint.line, layout: { visibility: vis } })
      setupClickPopup(`${layer.key}-fill`)
    } else if (layer.geomType === 'line') {
      if (layer.casing) {
        map.addLayer({
          id: `${layer.key}-casing`, type: 'line', source: layer.key,
          paint: layer.casing, layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' }
        })
      }
      map.addLayer({ id: layer.key, type: 'line', source: layer.key, paint: layer.paint, layout: { visibility: vis, 'line-cap': 'round' } })
      if (layer.arrows) {
        map.addLayer({
          id: `${layer.key}-arrows`,
          type: 'symbol',
          source: layer.key,
          layout: {
            'symbol-placement': 'line',
            'symbol-spacing': 80,
            'icon-image': 'tt-arrow',
            'icon-size': 0.9,
            'icon-rotation-alignment': 'map',
            // Push arrows onto the offset line (perpendicular, matches line-offset
            // side). icon-offset rotates with the symbol on a line placement.
            'icon-offset': [0, 9],
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
            visibility: vis
          }
        })
      }
      setupClickPopup(layer.key)
      if (layer.promoteId) setupLineSelection(layer.key)
    } else {
      map.addLayer({ id: layer.key, type: 'circle', source: layer.key, paint: layer.paint, layout: { visibility: vis } })
      setupClickPopup(layer.key)
    }
  }

  // Traffic is the primary visualization; keep it above optional references.
  if (map.getLayer('speed-lanes-casing')) map.moveLayer('speed-lanes-casing')
  if (map.getLayer('speed-lanes')) map.moveLayer('speed-lanes')

  buildLayerPanel()
  setupPanelToggles()
  fetchPublicConfig()
  fetchAll()

  // ─── Geolocation Source & Layers ───────────────────────────────────────────
  map.addSource('user-accuracy', { type: 'geojson', data: EMPTY_FC })
  map.addLayer({
    id: 'user-accuracy-fill',
    type: 'fill',
    source: 'user-accuracy',
    paint: {
      'fill-color': '#3897ff',
      'fill-opacity': 0.12
    }
  })
  map.addLayer({
    id: 'user-accuracy-line',
    type: 'line',
    source: 'user-accuracy',
    paint: {
      'line-color': '#3897ff',
      'line-width': 1.5,
      'line-opacity': 0.35,
      'line-dasharray': [2, 2]
    }
  })

  initGPS()

  setInterval(() => {
    if (document.visibilityState === 'visible') fetchAll()
  }, 60_000)
  setInterval(() => {
    if (document.visibilityState === 'visible' && !document.getElementById('status-body').classList.contains('hidden')) {
      fetchFeedStatus()
    }
  }, 60_000)
})

map.on('moveend', (e) => {
  if (document.visibilityState !== 'visible') return
  // The follow loop pans the camera every frame with programmatic jumpTo (no
  // originalEvent). Refetching layers on those would hammer the API, so only
  // user-driven moves trigger a refetch here; while auto-following, layers
  // refresh on the 60s interval and the HUD refetches on each GPS fix.
  if (gpsState !== GPS_STATES.OFF && !e.originalEvent && !isTrackingSuspended) return
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(fetchAll, 300)
})

// Slide lane-speed labels along their line during pan/zoom/rotate so they
// stay on screen between refetches, instead of waiting on the debounced fetch.
map.on('move', updateLaneSpeedLayout)

// Re-evaluate verkeersborden hint + re-fetch on zoom change
map.on('zoom', () => {
  updateZoomHint()
  updateSpeedLayout()
  updateLaneSpeedLayout()
  // If verkeersborden just crossed zoom 13, trigger a fetch
  const layer = LAYERS.find(l => l.key === 'verkeersborden')
  if (layer && enabled.has('verkeersborden')) fetchLayer(layer)
})

// Keep roadside offsets correct while the map rotates (e.g. navigation mode).
map.on('rotate', () => { updateSpeedLayout(); updateLaneSpeedLayout() })

// ─── Fetch ────────────────────────────────────────────────────────────────────

function fetchAll () {
  bboxTooLarge = false
  let needsRoadSignHud = false
  for (const layer of LAYERS) {
    if (!enabled.has(layer.key)) continue
    if (layer.hudOnly) needsRoadSignHud = true
    else fetchLayer(layer)
  }
  if (needsRoadSignHud || gpsState !== GPS_STATES.OFF) fetchRoadSignHud()
  else renderRoadSignHud()
}

function fetchLayer (layer) {
  if (layer.hudOnly) { fetchRoadSignHud(true); return }
  if (layer.geomType === 'speed') { fetchSpeedMarkers(); return }
  if (layer.geomType === 'road-network') { fetchNwbRoads(layer); return }

  if (layer.minZoom && map.getZoom() < layer.minZoom) {
    map.getSource(layer.key)?.setData(EMPTY_FC)
    return
  }

  controllers[layer.key]?.abort()
  const ctrl = new AbortController()
  controllers[layer.key] = ctrl

  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(v => v.toFixed(6)).join(',')
  const sep = layer.endpoint.includes('?') ? '&' : '?'
  const url = `/api${layer.endpoint}${sep}bbox=${bbox}`

  fetch(url, { signal: ctrl.signal })
    .then(r => {
      if (r.status === 400) return r.json().then(body => Promise.reject(Object.assign(new Error(body.detail || 'Bad Request'), { isBboxError: /bbox area/i.test(body.detail || '') })))
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(data => {
      setBboxTooLargeHint(false)
      map.getSource(layer.key)?.setData(data)
      if (layer.promoteId) reapplySelection(layer.key)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn(`[${layer.key}]`, e.message)
    })
}

function fetchNwbRoads (layer) {
  if (map.getZoom() < layer.minZoom) {
    controllers[layer.key]?.abort()
    map.getSource(layer.key)?.setData(EMPTY_FC)
    nwbTruncated = false
    updateZoomHint()
    return
  }

  controllers[layer.key]?.abort()
  const ctrl = new AbortController()
  controllers[layer.key] = ctrl
  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(v => v.toFixed(5)).join(',')
  const zoom = map.getZoom()
  const profile = zoom < 11 ? 'national' : zoom < 12 ? 'major' : 'detailed'
  const cacheKey = `${profile}:${bbox}`
  const cached = nwbCache.get(cacheKey)
  if (cached && cached.expires > Date.now()) {
    renderNwbData(layer, cached.data)
    return
  }

  fetch(`/api${layer.endpoint}?bbox=${bbox}&zoom=${zoom.toFixed(2)}`, { signal: ctrl.signal })
    .then(r => {
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(data => {
      nwbCache.set(cacheKey, { expires: Date.now() + NWB_BROWSER_CACHE_TTL_MS, data })
      // Bound browser memory during long pan/zoom sessions.
      if (nwbCache.size > 40) nwbCache.delete(nwbCache.keys().next().value)
      renderNwbData(layer, data)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      console.warn('[nwb_roads]', e.message)
      // Preserve the last successful geometry so a transient PDOK failure does
      // not blank the road network while the user is navigating.
    })
}

function renderNwbData (layer, data) {
  map.getSource(layer.key)?.setData(data)
  nwbTruncated = Boolean(data?.metadata?.truncated)
  updateZoomHint()
}

function fetchPublicConfig () {
  fetch('/api/config')
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (data) publicConfig = data
      document.body.classList.toggle('nwb-diagnostic', Boolean(publicConfig.nwbDiagnosticMode))
    })
    .catch(e => console.warn('[config]', e.message))
}

function setupNwbDiagnostic (layerId) {
  map.on('click', layerId, e => {
    if (!publicConfig.nwbDiagnosticMode || !e.features?.length) return
    const feature = e.features[0]
    if (activePopup) activePopup.remove()
    activePopup = new maplibregl.Popup({ maxWidth: '360px' })
      .setLngLat(e.lngLat)
      .setHTML(`<div class="diagnostic-title">NWB road segment</div>${buildPopupHtml(feature.properties)}`)
      .addTo(map)
  })
  map.on('mouseenter', layerId, () => {
    if (publicConfig.nwbDiagnosticMode) map.getCanvas().style.cursor = 'crosshair'
  })
  map.on('mouseleave', layerId, () => {
    if (publicConfig.nwbDiagnosticMode) map.getCanvas().style.cursor = ''
  })
}

// Measurement sources are points, while the WEGGEG road section they drive can
// cross a high-zoom viewport without that source point being inside it. Keep a
// small nearby-data buffer so those speed-lane segments remain stable.
function viewportBbox (includeNearbyPoints = false) {
  const b = map.getBounds()
  let west = b.getWest()
  let south = b.getSouth()
  let east = b.getEast()
  let north = b.getNorth()
  if (includeNearbyPoints) {
    const lonPad = Math.max((east - west) * 0.75, 0.015)
    const latPad = Math.max((north - south) * 0.75, 0.010)
    west -= lonPad
    south -= latPad
    east += lonPad
    north += latPad
  }
  return [west, south, east, north].map(v => v.toFixed(6)).join(',')
}

// ─── GPS-relative road-sign HUD ──────────────────────────────────────────────

function fetchRoadSignHud (force = false) {
  if (gpsState === GPS_STATES.OFF || !userCoords) {
    renderRoadSignHud()
    return
  }

  const moved = roadSignHudLastFetchCoords
    ? calculateDistance(roadSignHudLastFetchCoords, userCoords)
    : Infinity
  const elapsed = Date.now() - roadSignHudLastFetchAt
  const headingChanged = userHeading !== null && (
    roadSignHudLastFetchHeading === null ||
    Math.abs(angleDiff(userHeading, roadSignHudLastFetchHeading)) >= 20
  )
  if (!force && !headingChanged && moved < ROAD_SIGN_HUD_REFETCH_DISTANCE_M && elapsed < ROAD_SIGN_HUD_REFETCH_MS) {
    renderRoadSignHud()
    return
  }

  controllers['road-sign-hud']?.abort()
  const ctrl = new AbortController()
  controllers['road-sign-hud'] = ctrl
  roadSignHudLastFetchCoords = [...userCoords]
  roadSignHudLastFetchAt = Date.now()
  roadSignHudLastFetchHeading = userHeading

  const bbox = forwardBiasedBbox(userCoords, userHeading, {
    ahead: ROAD_SIGN_HUD_MAX_DISTANCE_M + 250,
    behind: 100,
    side: 250
  })
  const speedBbox = forwardBiasedBbox(userCoords, userHeading ?? 0, {
    ahead: 1500,
    behind: 500,
    side: 250
  })
  const requests = []
  if (userHeading !== null && enabled.has('matrix')) requests.push(fetchRoadSignHudSource('matrix', bbox, ctrl.signal))
  else roadSignHudCache.matrix = EMPTY_FC
  if (userHeading !== null && enabled.has('drips')) requests.push(fetchRoadSignHudSource('drips', bbox, ctrl.signal))
  else roadSignHudCache.drips = EMPTY_FC
  requests.push(fetchRoadSignHudSpeedSource(speedBbox, ctrl.signal))

  Promise.allSettled(requests).then(results => {
    for (const result of results) {
      if (result.status === 'rejected' && result.reason?.name !== 'AbortError') {
        console.warn('[road-sign-hud]', result.reason?.message || result.reason)
      }
    }
    if (!ctrl.signal.aborted) renderRoadSignHud()
  })
}

function fetchRoadSignHudSpeedSource (bbox, signal) {
  return fetch(`/api/traffic/speed/map?bbox=${bbox}&include_lanes=false&limit=500`, { signal })
    .then(response => {
      if (!response.ok) throw new Error(`speed: HTTP ${response.status}`)
      return response.json()
    })
    .then(data => { roadSignHudCache.speedPoints = data.points || EMPTY_FC })
}

function fetchRoadSignHudSource (source, bbox, signal) {
  const limit = source === 'matrix' ? 300 : 25
  return fetch(`/api/signs/${source}?bbox=${bbox}&limit=${limit}`, { signal })
    .then(response => {
      if (!response.ok) throw new Error(`${source}: HTTP ${response.status}`)
      return response.json()
    })
    .then(fc => { roadSignHudCache[source] = fc })
}

function renderRoadSignHud () {
  if (gpsState === GPS_STATES.OFF || !userCoords) {
    renderRoadSignHudSelection({ matrix: null, drip: null, speed: null, gpsKmh: null })
    return
  }

  const selected = userHeading === null
    ? { matrix: null, drip: null }
    : selectUpcomingRoadSigns(
        enabled.has('matrix') ? roadSignHudCache.matrix : EMPTY_FC,
        enabled.has('drips') ? roadSignHudCache.drips : EMPTY_FC,
        { coords: userCoords, heading: userHeading },
        ROAD_SIGN_HUD_MAX_DISTANCE_M
      )

  selected.gpsKmh = Number.isFinite(userSpeedMps) ? userSpeedMps * 3.6 : null
  selected.upcoming = userHeading === null
    ? null
    : selectUpcomingLaneSpeeds(roadSignHudCache.speedPoints, { coords: userCoords, heading: userHeading }, 2500)

  renderRoadSignHudSelection(selected)
}

function renderRoadSignHudSelection (selected) {
  const hud = document.getElementById('road-sign-hud')
  const speedTile = document.getElementById('road-sign-hud-speed')
  const matrixTile = document.getElementById('road-sign-hud-matrix')
  const dripTile = document.getElementById('road-sign-hud-drip')
  if (!hud || !speedTile || !matrixTile || !dripTile) return

  renderSpeedHudTile(selected.upcoming)
  renderMatrixHudTile(selected.matrix)
  renderDripHudTile(selected.drip)
  updateGpsSpeedBadge(selected.gpsKmh, selected.upcoming)
  const speedVisible = gpsState !== GPS_STATES.OFF
  const visibleCount = [speedVisible, selected.matrix, selected.drip].filter(Boolean).length
  const visible = visibleCount > 0
  speedTile.classList.toggle('hidden', !speedVisible)
  hud.classList.remove('road-sign-hud-count-1', 'road-sign-hud-count-2', 'road-sign-hud-count-3')
  document.body.classList.remove('road-sign-hud-count-1', 'road-sign-hud-count-2', 'road-sign-hud-count-3')
  if (visible) hud.classList.add(`road-sign-hud-count-${visibleCount}`)
  if (visible) document.body.classList.add(`road-sign-hud-count-${visibleCount}`)
  hud.classList.toggle('hidden', !visible)
  document.body.classList.toggle('road-sign-hud-visible', visible)
}

function renderSpeedHudTile (upcoming) {
  const laneLabel = document.getElementById('road-sign-hud-speed-lane')
  const distance = document.getElementById('road-sign-hud-speed-distance')
  const road = document.getElementById('road-sign-hud-speed-road')
  if (!laneLabel || !distance || !road) return

  const label = upcoming
    ? [upcoming.data.road || upcoming.data.road_number, upcoming.data.carriageway,
       upcoming.data.km != null ? `km ${upcoming.data.km}` : null].filter(Boolean).join(' · ') || 'Meetpunt'
    : !userCoords
        ? (userLocationStatus === 'denied' ? 'GPS-toegang nodig' : 'GPS-signaal zoeken')
        : 'Meetpunt zoeken'

  // Rebuild the road SVG only when the sensor / speeds / distance change.
  const roadKey = laneSpeedRoadKey(upcoming)
  if (roadSignHudRenderState.speedKey !== roadKey) {
    setTextIfChanged(laneLabel, label)
    setTextIfChanged(distance, upcoming ? formatDistance(Math.max(0, upcoming.cls.along)) : '')
    distance.classList.toggle('hidden', !upcoming)
    road.replaceChildren()
    if (upcoming) road.appendChild(buildLaneSpeedRoad(upcoming.data))
    road.classList.toggle('hidden', !upcoming)
    roadSignHudRenderState.speedKey = roadKey
  }
}

// Circular GPS-speed badge (km/h) bottom-left, with the road we are on in the
// centre-bottom label — shown only while tracking.
function updateGpsSpeedBadge (gpsKmh, upcoming) {
  const badge = document.getElementById('gps-speed-badge')
  const value = document.getElementById('gps-speed-value')
  const roadLabel = document.getElementById('current-road-label')
  if (!badge || !value || !roadLabel) return

  const tracking = gpsState !== GPS_STATES.OFF && Boolean(userCoords)
  badge.classList.toggle('hidden', !tracking)
  if (tracking) setTextIfChanged(value, Number.isFinite(gpsKmh) ? String(Math.round(gpsKmh)) : '–')

  const road = upcoming ? (upcoming.data.road || upcoming.data.road_number) : null
  roadLabel.classList.toggle('hidden', !tracking || !road)
  if (tracking && road) setTextIfChanged(roadLabel, road)
}

function renderMatrixHudTile (selection) {
  const tile = document.getElementById('road-sign-hud-matrix')
  const lanes = document.getElementById('road-sign-hud-lanes')
  if (!selection) {
    tile.classList.add('hidden')
    if (roadSignHudRenderState.matrixKey !== null) {
      lanes.replaceChildren()
      roadSignHudRenderState.matrixKey = null
    }
    return
  }
  tile.classList.remove('hidden')

  const gantry = selection.data
  setTextIfChanged(
    document.getElementById('road-sign-hud-matrix-distance'),
    formatDistance(Math.max(0, selection.cls.along))
  )
  const matrixKey = [gantry.road, gantry.carriageway, gantry.km, ...gantry.lanes.flatMap(lane => [
    lane.lane, lane.aspect_type, lane.value, lane.flashing, lane.red_ring,
    JSON.stringify(lane.aspects || null)
  ])].join('|')
  if (roadSignHudRenderState.matrixKey === matrixKey) return

  setTextIfChanged(
    document.getElementById('road-sign-hud-matrix-road'),
    [gantry.road, gantry.carriageway, gantry.km != null ? `km ${gantry.km}` : null]
      .filter(Boolean).join(' · ')
  )

  lanes.replaceChildren()
  for (const lane of gantry.lanes) {
    const column = document.createElement('div')
    column.className = 'road-sign-hud-lane'
    const label = document.createElement('span')
    label.className = 'road-sign-hud-lane-label'
    label.textContent = `Rijstrook ${lane.lane ?? '?'}`
    column.append(label, buildMsiLane(lane))
    lanes.appendChild(column)
  }
  roadSignHudRenderState.matrixKey = matrixKey
}

function renderDripHudTile (selection) {
  const tile = document.getElementById('road-sign-hud-drip')
  const image = document.getElementById('road-sign-hud-drip-image')
  const text = document.getElementById('road-sign-hud-drip-text')
  if (!selection) {
    tile.classList.add('hidden')
    if (roadSignHudRenderState.dripKey !== null) {
      image.removeAttribute('src')
      image.classList.add('hidden')
      text.textContent = ''
      text.classList.add('hidden')
      roadSignHudRenderState.dripKey = null
    }
    return
  }
  tile.classList.remove('hidden')

  const data = selection.data
  setTextIfChanged(
    document.getElementById('road-sign-hud-drip-distance'),
    formatDistance(Math.max(0, selection.cls.along))
  )
  const imageTail = data.image_b64 ? data.image_b64.slice(-24) : ''
  const dripKey = [data.controller_id, data.vms_index, data.description, data.display_text,
    data.image_format, data.image_b64?.length || 0, imageTail].join('|')
  if (roadSignHudRenderState.dripKey === dripKey) return

  setTextIfChanged(document.getElementById('road-sign-hud-drip-name'), data.description || 'DRIP / VMS')
  if (data.image_b64) {
    const requestedFormat = String(data.image_format || 'png')
    const format = /^[a-z0-9.+-]+$/i.test(requestedFormat) ? requestedFormat : 'png'
    image.src = `data:image/${format};base64,${data.image_b64}`
    image.classList.remove('hidden')
    text.textContent = ''
    text.classList.add('hidden')
  } else {
    image.removeAttribute('src')
    image.classList.add('hidden')
    setTextIfChanged(text, data.display_text || '')
    text.classList.toggle('hidden', !String(data.display_text || '').trim())
  }
  roadSignHudRenderState.dripKey = dripKey
}

function setTextIfChanged (element, value) {
  const text = String(value)
  if (element.textContent !== text) element.textContent = text
}

function clearRoadSignHud () {
  controllers['road-sign-hud']?.abort()
  roadSignHudCache.matrix = EMPTY_FC
  roadSignHudCache.drips = EMPTY_FC
  roadSignHudCache.speedPoints = EMPTY_FC
  roadSignHudLastFetchCoords = null
  roadSignHudLastFetchAt = 0
  roadSignHudLastFetchHeading = null
  renderRoadSignHud()
}

// ─── Traffic speed HTML markers ───────────────────────────────────────────────

function fetchSpeedMarkers () {
  controllers['speed']?.abort()
  const ctrl = new AbortController()
  controllers['speed'] = ctrl

  const includeLanes = map.getZoom() >= 14
  const bbox = viewportBbox(includeLanes)
  fetch(`/api/traffic/speed/map?bbox=${bbox}&include_lanes=${includeLanes}`, { signal: ctrl.signal })
    .then(r => {
      if (r.status === 400) return r.json().then(body => Promise.reject(Object.assign(new Error(body.detail || 'Bad Request'), { isBboxError: /bbox area/i.test(body.detail || '') })))
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(data => {
      setBboxTooLargeHint(false)
      map.getSource('speed')?.setData(data.lanes || EMPTY_FC)
      renderSpeedMarkers(data.points || EMPTY_FC, data.lanes || EMPTY_FC)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn('[speed]', e.message)
    })
}

function renderSpeedMarkers (fc, laneFc = EMPTY_FC) {
  for (const m of speedMarkers) m.marker.remove()
  speedMarkers = []
  for (const m of laneSpeedMarkers) m.marker.remove()
  laneSpeedMarkers = []

  if (!enabled.has('speed')) return

  renderLaneSpeedLabels(laneFc)

  // A site is rendered as lane lines when WEGGEG matched and returned geometry.
  // Sites without a usable WEGGEG section keep the original roadside marker.
  const renderedSources = new Set(
    (laneFc.features || []).map(f => f.properties?.source_id).filter(Boolean)
  )

  for (const f of fc.features) {
    if (!f.geometry) continue
    const p = f.properties
    if (p.weggeg_source_id && renderedSources.has(p.weggeg_source_id)) continue
    const lanes = p.lanes || []
    if (!lanes.length) continue

    // Outer wrapper for maplibre positioning; inner row gets our rotate+scale.
    const wrapper = document.createElement('div')
    const el = document.createElement('div')
    el.className = 'speed-site'
    wrapper.appendChild(el)

    for (const lane of lanes) {
      const box = document.createElement('div')
      const kmh = lane.speed_kmh
      box.className = 'speed-lane'
      box.style.background = speedColor(kmh)
      box.style.color = speedTextColor(kmh)
      box.textContent = kmh !== null ? Math.round(kmh) : '?'
      box.title = `Lane ${lane.lane} · ${kmh !== null ? Math.round(kmh) + ' km/h' : 'no data'}${lane.flow_veh_h !== null ? ' · ' + Math.round(lane.flow_veh_h) + ' veh/h' : ''}`
      el.appendChild(box)
    }

    el.addEventListener('click', e => {
      e.stopPropagation()
      if (activePopup) activePopup.remove()
      const roadLabel = p.road ? `${esc(p.road)} ${esc(p.carriageway || '')} km ${p.km ?? ''}` : esc(p.site_id)
      const header = `<div style="font-size:11px;color:#6688aa;margin-bottom:6px">${roadLabel}</div>`
      const meta = buildPopupHtml({
        ...(p.road ? { road: p.road } : {}),
        ...(p.carriageway ? { carriageway: p.carriageway } : {}),
        ...(p.km != null ? { km: p.km } : {}),
        ...(p.measured_at ? { measured: p.measured_at } : {}),
        ...(p.bearing != null ? { bearing: p.bearing + '°' } : {}),
        ...(p.side ? { side: p.side } : {}),
      })
      const lanesHtml = lanes.map(l =>
        `<b style="color:#6688aa;font-size:11px">Lane ${l.lane ?? '?'}</b>` +
        buildPopupHtml({
          speed_kmh: l.speed_kmh !== null ? Math.round(l.speed_kmh) + ' km/h' : '—',
          flow_veh_h: l.flow_veh_h !== null ? Math.round(l.flow_veh_h) + ' veh/h' : '—',
        })
      ).join('<hr style="border-color:#2a2a40;margin:5px 0">')
      activePopup = new maplibregl.Popup({ maxWidth: '300px', offset: [0, -8] })
        .setLngLat(f.geometry.coordinates)
        .setHTML(header + meta + lanesHtml)
        .addTo(map)
    })

    const marker = new maplibregl.Marker({ element: wrapper, anchor: 'center' })
      .setLngLat(f.geometry.coordinates)
      .addTo(map)
    speedMarkers.push({ marker, el, bearing: p.bearing })
  }

  updateSpeedLayout()
}

function renderLaneSpeedLabels (laneFc) {
  if (map.getZoom() < 16) return

  const bounds = currentBoundsBox()

  for (const feature of laneFc.features || []) {
    const p = feature.properties || {}
    if (!feature.geometry || p.speed_kmh === null || p.speed_kmh === undefined) continue
    if (!Array.isArray(p.measurement_coords)) continue

    const best = projectPointOnLine(feature.geometry, p.measurement_coords)
    if (!best) continue

    // Stagger labels longitudinally so adjacent 3.5m lanes remain readable.
    const centerLane = ((p.lane_count || 1) + 1) / 2
    const shiftM = ((p.lane || 1) - centerLane) * 22
    const basePosition = Math.max(0, Math.min(best.total, best.position + shiftM))
    const range = visibleRangeOnLine(best, bounds)
    if (!range) continue  // line doesn't currently cross the viewport at all
    const wanted = Math.max(range.min, Math.min(range.max, basePosition))
    const coords = coordAtDistance(best, wanted)
    if (!coords) continue

    const el = document.createElement('div')
    el.className = 'lane-speed-label'
    el.style.background = speedColor(p.speed_kmh)
    el.style.color = speedTextColor(p.speed_kmh)
    el.textContent = Math.round(p.speed_kmh)
    el.title = `${p.road || p.road_number || ''} ${p.carriageway || ''} · lane ${p.lane} · ${Math.round(p.speed_kmh)} km/h`

    el.addEventListener('click', e => {
      e.stopPropagation()
      if (activePopup) activePopup.remove()
      activePopup = new maplibregl.Popup({ maxWidth: '280px', offset: [0, -8] })
        .setLngLat(marker.getLngLat())
        .setHTML(buildPopupHtml({
          road: p.road,
          carriageway: p.carriageway,
          km: p.km,
          lane: p.lane,
          speed_kmh: Math.round(p.speed_kmh) + ' km/h',
          flow_veh_h: p.flow_veh_h != null ? Math.round(p.flow_veh_h) + ' veh/h' : '—',
          measured: p.measured_at,
        }))
        .addTo(map)
    })

    const marker = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(coords).addTo(map)
    laneSpeedMarkers.push({ marker, best, basePosition })
  }
}

// Slide already-rendered lane-speed labels along their line so they stay on
// screen while panning/zooming, instead of sitting fixed at the sensor's
// physical location (which can scroll out of view at high zoom).
function updateLaneSpeedLayout () {
  if (!laneSpeedMarkers.length) return
  const bounds = currentBoundsBox()
  for (const m of laneSpeedMarkers) {
    const range = visibleRangeOnLine(m.best, bounds)
    const el = m.marker.getElement()
    if (!range) {
      el.style.visibility = 'hidden'
      continue
    }
    el.style.visibility = ''
    const wanted = Math.max(range.min, Math.min(range.max, m.basePosition))
    const coords = coordAtDistance(m.best, wanted)
    if (coords) m.marker.setLngLat(coords)
  }
}

function currentBoundsBox () {
  const b = map.getBounds()
  return { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() }
}

// Find the point on `geometry` closest to `target`, and the running distances
// (metres) needed to walk to any other point along the same line.
function projectPointOnLine (geometry, target) {
  const lines = geometry.type === 'LineString'
    ? [geometry.coordinates]
    : geometry.type === 'MultiLineString' ? geometry.coordinates : []
  const latScale = 110540
  const lonScale = 111320 * Math.cos(target[1] * Math.PI / 180)
  let best = null

  for (const line of lines) {
    if (!line || line.length < 2) continue
    const lengths = []
    let total = 0
    for (let i = 0; i < line.length - 1; i++) {
      const dx = (line[i + 1][0] - line[i][0]) * lonScale
      const dy = (line[i + 1][1] - line[i][1]) * latScale
      const length = Math.hypot(dx, dy)
      lengths.push(length)
      total += length
    }

    let before = 0
    for (let i = 0; i < lengths.length; i++) {
      const ax = (line[i][0] - target[0]) * lonScale
      const ay = (line[i][1] - target[1]) * latScale
      const bx = (line[i + 1][0] - target[0]) * lonScale
      const by = (line[i + 1][1] - target[1]) * latScale
      const dx = bx - ax
      const dy = by - ay
      const denom = dx * dx + dy * dy
      const t = denom ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denom)) : 0
      const px = ax + t * dx
      const py = ay + t * dy
      const distanceSq = px * px + py * py
      if (!best || distanceSq < best.distanceSq) {
        best = { line, lengths, total, position: before + t * lengths[i], distanceSq }
      }
      before += lengths[i]
    }
  }

  return best
}

// Coordinate at `distance` metres along the line described by a
// projectPointOnLine() result.
function coordAtDistance (best, distance) {
  let wanted = Math.max(0, Math.min(best.total, distance))
  for (let i = 0; i < best.lengths.length; i++) {
    if (wanted <= best.lengths[i] || i === best.lengths.length - 1) {
      const t = best.lengths[i] ? wanted / best.lengths[i] : 0
      return [
        best.line[i][0] + (best.line[i + 1][0] - best.line[i][0]) * t,
        best.line[i][1] + (best.line[i + 1][1] - best.line[i][1]) * t,
      ]
    }
    wanted -= best.lengths[i]
  }
  return null
}

// Range of along-line distance (metres) currently inside the viewport, using
// vertex-level containment — dense WEGGEG vertex spacing makes this accurate
// enough without a full line/bbox clip.
function visibleRangeOnLine (best, bounds) {
  const { line, lengths, total } = best
  let cum = 0
  let min = null
  let max = null
  for (let i = 0; i < line.length; i++) {
    const [lng, lat] = line[i]
    const inside = lng >= bounds.west && lng <= bounds.east && lat >= bounds.south && lat <= bounds.north
    if (inside) {
      if (min === null || cum < min) min = cum
      if (max === null || cum > max) max = cum
    }
    if (i < lengths.length) cum += lengths[i]
  }
  if (min === null) return null
  // Small inset so a label doesn't render half-cut on the viewport edge.
  const pad = Math.min(20, (max - min) / 2)
  return { min: Math.max(0, min + pad), max: Math.min(total, max - pad) }
}

// Keep fallback speed rows upright and offset them roadside using the bearing.
// Recomputed on zoom/rotate; no refetch required.
function updateSpeedLayout () {
  if (!speedMarkers.length) return
  const z = map.getZoom()
  const scale = Math.max(0.5, Math.min(1, 0.5 + (z - 11) * 0.125))
  const mapBearing = map.getBearing()

  for (const m of speedMarkers) {
    if (m.bearing === null || m.bearing === undefined) {
      m.el.style.transform = `scale(${scale})`
      m.marker.setOffset([0, 0])
      continue
    }
    // Keep speed text upright; bearing is only used to place the fallback
    // marker beside the road rather than rotate its numbers.
    m.el.style.transform = `scale(${scale})`
    const screenAngle = ((m.bearing + 90 - mapBearing) * Math.PI) / 180
    const dist = (m.el.offsetWidth * scale) / 2 + 3
    m.marker.setOffset([Math.sin(screenAngle) * dist, -Math.cos(screenAngle) * dist])
  }
}

// speedColor / speedTextColor moved to lib.js.

function fetchFeedStatus () {
  controllers['feed-status']?.abort()
  const ctrl = new AbortController()
  controllers['feed-status'] = ctrl
  fetch('/api/feeds', { signal: ctrl.signal })
    .then(r => r.ok ? r.json() : null)
    .then(renderFeedStatus)
    .catch(e => {
      if (e.name !== 'AbortError') console.warn('[feeds/status]', e.message)
    })
}

function setBboxTooLargeHint (show) {
  bboxTooLarge = show
  updateZoomHint()
}

// ─── Popups ───────────────────────────────────────────────────────────────────

function setupClickPopup (mapLayerId) {
  map.on('click', mapLayerId, e => {
    if (!e.features?.length) return
    const props = e.features[0].properties
    if (activePopup) activePopup.remove()
    activePopup = new maplibregl.Popup({ maxWidth: '300px' })
      .setLngLat(e.lngLat)
      .setHTML(buildPopupHtml(props))
      .addTo(map)
  })
  map.on('mouseenter', mapLayerId, () => { map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', mapLayerId, () => { map.getCanvas().style.cursor = '' })
}

// Highlight the clicked feature via feature-state 'selected'. One selection at a
// time across the map; cleared when another feature is clicked.
function setupLineSelection (layerKey) {
  map.on('click', layerKey, e => {
    if (!e.features?.length) return
    const id = e.features[0].id
    if (id === undefined || id === null) return
    if (selectedFeature) {
      map.setFeatureState({ source: selectedFeature.source, id: selectedFeature.id }, { selected: false })
    }
    map.setFeatureState({ source: layerKey, id }, { selected: true })
    selectedFeature = { source: layerKey, id }
  })
}

// feature-state is wiped when a source's data is replaced; re-apply the current
// selection after a refresh so the highlight survives the 60s/​moveend refetch.
function reapplySelection (layerKey) {
  if (selectedFeature && selectedFeature.source === layerKey) {
    map.setFeatureState({ source: layerKey, id: selectedFeature.id }, { selected: true })
  }
}

function buildPopupHtml (props) {
  // Render image_b64 as an inline image if present
  let imageHtml = ''
  if (props.image_b64) {
    const fmt = props.image_format || 'png'
    imageHtml = `<img src="data:image/${esc(fmt)};base64,${props.image_b64}" style="max-width:100%;display:block;margin-bottom:6px;border-radius:3px;image-rendering:pixelated">`
  }

  const rows = Object.entries(props)
    .filter(([k, v]) => k !== 'image_b64' && k !== 'image_format' && v !== null && v !== undefined && v !== '')
    .map(([k, v]) => {
      let display = typeof v === 'object' ? JSON.stringify(v) : String(v)
      if (display.length > 130) display = display.slice(0, 130) + '…'
      return `<tr><td class="pk">${esc(k)}</td><td>${esc(display)}</td></tr>`
    })
  if (!rows.length && !imageHtml) return '<em style="color:#667">No properties</em>'
  return imageHtml + (rows.length ? `<table class="popup-table"><tbody>${rows.join('')}</tbody></table>` : '')
}

// esc moved to lib.js.

// ─── Layer panel ──────────────────────────────────────────────────────────────

function buildLayerPanel () {
  const panelBody = document.getElementById('panel-body')

  for (const group of GROUPS) {
    const groupLayers = LAYERS.filter(l => l.group === group.key)
    if (!groupLayers.length) continue

    const section = document.createElement('div')
    section.className = 'group'

    if (groupLayers.length === 1) {
      section.appendChild(makeLayerRow(groupLayers[0], groupLayers, false))
    } else {
      section.appendChild(makeGroupHeader(group, groupLayers))
      for (const layer of groupLayers) {
        section.appendChild(makeLayerRow(layer, groupLayers, true))
      }
    }

    panelBody.appendChild(section)
  }
}

function makeGroupHeader (group, groupLayers) {
  const label = document.createElement('label')
  label.className = 'group-header'

  const cb = document.createElement('input')
  cb.type = 'checkbox'
  cb.dataset.group = group.key
  syncGroupCb(cb, groupLayers)

  cb.addEventListener('change', () => {
    for (const layer of groupLayers) {
      const childCb = document.getElementById(`cb-${layer.key}`)
      if (cb.checked) {
        enabled.add(layer.key)
        setLayerVisibility(layer, true)
        fetchLayer(layer)
        if (childCb) childCb.checked = true
      } else {
        enabled.delete(layer.key)
        setLayerVisibility(layer, false)
        map.getSource(layer.key)?.setData(EMPTY_FC)
        controllers[layer.key]?.abort()
        if (childCb) childCb.checked = false
      }
    }
    updateZoomHint()
  })

  label.appendChild(cb)
  label.append(` ${group.label}`)
  return label
}

function makeLayerRow (layer, groupLayers, indented) {
  const label = document.createElement('label')
  label.className = 'layer-row' + (indented ? ' indented' : '')

  const cb = document.createElement('input')
  cb.type = 'checkbox'
  cb.id = `cb-${layer.key}`
  cb.checked = enabled.has(layer.key)

  cb.addEventListener('change', () => {
    if (cb.checked) {
      enabled.add(layer.key)
      setLayerVisibility(layer, true)
      fetchLayer(layer)
    } else {
      enabled.delete(layer.key)
      setLayerVisibility(layer, false)
      map.getSource(layer.key)?.setData(EMPTY_FC)
      controllers[layer.key]?.abort()
    }
    // Sync parent group checkbox if this row is nested
    if (indented) {
      const parentCb = document.querySelector(`input[data-group="${layer.group}"]`)
      if (parentCb) syncGroupCb(parentCb, groupLayers)
    }
    updateZoomHint()
  })

  const dot = document.createElement('span')
  dot.className = 'dot'
  dot.style.background = layer.legendColor

  const nameSpan = document.createElement('span')
  nameSpan.textContent = layer.label
  if (layer.minZoom) {
    const badge = document.createElement('span')
    badge.className = 'zoom-badge'
    badge.textContent = `z${layer.minZoom}+`
    nameSpan.append(' ', badge)
  }

  label.appendChild(cb)
  label.appendChild(dot)
  label.appendChild(nameSpan)
  return label
}

function syncGroupCb (cb, groupLayers) {
  const allOn = groupLayers.every(l => enabled.has(l.key))
  const anyOn = groupLayers.some(l => enabled.has(l.key))
  cb.checked = allOn
  cb.indeterminate = anyOn && !allOn
}

function setLayerVisibility (layer, visible) {
  if (layer.hudOnly) {
    if (!visible) roadSignHudCache[layer.key] = EMPTY_FC
    renderRoadSignHud()
    return
  }
  if (layer.geomType === 'speed') {
    const vis = visible ? 'visible' : 'none'
    if (map.getLayer('speed-lanes-casing')) map.setLayoutProperty('speed-lanes-casing', 'visibility', vis)
    if (map.getLayer('speed-lanes')) map.setLayoutProperty('speed-lanes', 'visibility', vis)
    if (!visible) {
      for (const m of speedMarkers) m.marker.remove()
      speedMarkers = []
      for (const m of laneSpeedMarkers) m.marker.remove()
      laneSpeedMarkers = []
      map.getSource('speed')?.setData(EMPTY_FC)
    }
    return
  }
  const vis = visible ? 'visible' : 'none'
  if (layer.geomType === 'road-network') {
    if (map.getLayer(`${layer.key}-casing`)) map.setLayoutProperty(`${layer.key}-casing`, 'visibility', vis)
    if (map.getLayer(layer.key)) map.setLayoutProperty(layer.key, 'visibility', vis)
    return
  }
  if (layer.geomType === 'line') {
    if (map.getLayer(`${layer.key}-casing`)) map.setLayoutProperty(`${layer.key}-casing`, 'visibility', vis)
    if (map.getLayer(layer.key)) map.setLayoutProperty(layer.key, 'visibility', vis)
    if (map.getLayer(`${layer.key}-arrows`)) map.setLayoutProperty(`${layer.key}-arrows`, 'visibility', vis)
    return
  }
  if (layer.geomType === 'polygon') {
    if (map.getLayer(`${layer.key}-fill`)) map.setLayoutProperty(`${layer.key}-fill`, 'visibility', vis)
    if (map.getLayer(`${layer.key}-line`)) map.setLayoutProperty(`${layer.key}-line`, 'visibility', vis)
  } else {
    if (map.getLayer(layer.key)) map.setLayoutProperty(layer.key, 'visibility', vis)
  }
}

// ─── Panel toggles ────────────────────────────────────────────────────────────

function setupPanelToggles () {
  const settingsPanel = document.getElementById('settings-panel')
  const settingsToggle = document.getElementById('settings-toggle')
  const settingsBody = document.getElementById('settings-body')

  const setSettingsOpen = open => {
    settingsBody.classList.toggle('hidden', !open)
    settingsPanel.classList.toggle('open', open)
    settingsToggle.setAttribute('aria-expanded', String(open))
    settingsToggle.setAttribute('aria-label', open ? 'Close settings' : 'Open settings')
  }

  settingsToggle.addEventListener('click', event => {
    event.stopPropagation()
    setSettingsOpen(settingsBody.classList.contains('hidden'))
  })

  document.addEventListener('pointerdown', event => {
    if (!settingsBody.classList.contains('hidden') && !settingsPanel.contains(event.target)) setSettingsOpen(false)
  })
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') setSettingsOpen(false)
  })

  document.getElementById('panel-toggle').addEventListener('click', () => {
    const body = document.getElementById('panel-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('panel-toggle').setAttribute('aria-expanded', String(!nowHidden))
  })

  document.getElementById('status-toggle').addEventListener('click', () => {
    const body = document.getElementById('status-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('status-toggle').setAttribute('aria-expanded', String(!nowHidden))
    if (!nowHidden) fetchFeedStatus()
  })
}

// ─── Feed status ──────────────────────────────────────────────────────────────

function renderFeedStatus (data) {
  if (!data?.feeds) return
  const body = document.getElementById('status-body')
  body.innerHTML = data.feeds.map(f => {
    const dot = f.status === 'ok' ? '🟢'
      : f.status === 'error' ? '🔴'
      : f.status === 'not_modified' ? '🟡'
      : '⚪'
    const ago = f.finished_at ? timeAgo(f.finished_at) : '—'
    return `<div class="feed-row">
      <span>${dot}</span>
      <span class="feed-name">${esc(f.feed)}</span>
      <span class="feed-time">${ago}</span>
    </div>`
  }).join('')
}

function timeAgo (isoStr) {
  const sec = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000)
  if (sec < 5) return 'just now'
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  return `${Math.floor(sec / 3600)}h ago`
}

// ─── Zoom hint for verkeersborden ─────────────────────────────────────────────

function updateZoomHint () {
  const hint = document.getElementById('zoom-hint')
  if (bboxTooLarge) {
    hint.textContent = 'Zoom in — area too large to load data'
    hint.classList.remove('hidden')
  } else if (nwbTruncated && enabled.has('nwb_roads')) {
    hint.textContent = 'NWB viewport reached the feature cap — zoom in for complete road detail'
    hint.classList.remove('hidden')
  } else if (enabled.has('verkeersborden') && map.getZoom() < 13) {
    hint.textContent = 'Zoom in further to see traffic signs (zoom 13+)'
    hint.classList.remove('hidden')
  } else {
    hint.classList.add('hidden')
  }
}

// ─── GPS & Geolocation Logic ──────────────────────────────────────────────────

function initGPS() {
  const gpsBtn = document.getElementById('gps-btn')
  const recenterBtn = document.getElementById('recenter-btn')
  const compassBtn = document.getElementById('compass-btn')

  // Click handler to toggle GPS state machine
  gpsBtn.addEventListener('click', () => {
    let nextState
    if (gpsState === GPS_STATES.OFF) {
      nextState = GPS_STATES.FOLLOW
    } else if (gpsState === GPS_STATES.FOLLOW) {
      nextState = GPS_STATES.NAVIGATION
    } else {
      nextState = GPS_STATES.OFF
    }
    
    isTrackingSuspended = false
    recenterBtn.classList.add('hidden')
    // This click is a user gesture — the only moment iOS Safari will grant
    // DeviceOrientation (compass) permission, needed for heading-up rotation.
    enableCompass()
    setGPSState(nextState)
  })

  // Re-center button click handler
  recenterBtn.addEventListener('click', () => {
    isTrackingSuspended = false
    recenterBtn.classList.add('hidden')
    updateCameraToUser()
  })

  // Compass button resets bearing and pitch
  compassBtn.addEventListener('click', () => {
    map.easeTo({
      bearing: 0,
      pitch: 0,
      duration: 800
    })

    if (gpsState === GPS_STATES.NAVIGATION) {
      setGPSState(GPS_STATES.FOLLOW)
    }
  })

  // Rotate map event updates compass needle
  map.on('rotate', () => {
    const bearing = map.getBearing()
    document.getElementById('compass-needle').style.setProperty('--bearing', `${-bearing}deg`)
  })

  // Map interaction events to detect user manual manipulation.
  // Guard on e.originalEvent so our own programmatic follow (jumpTo/easeTo)
  // does not trip these — that was silently kicking navigation back to follow
  // and cancelling tracking on every camera update.
  map.on('dragstart', (e) => {
    if (!e.originalEvent) return
    if (gpsState !== GPS_STATES.OFF) {
      isTrackingSuspended = true
      recenterBtn.classList.remove('hidden')
    }
  })

  map.on('rotatestart', (e) => {
    if (!e.originalEvent) return
    // If manually rotating while in dynamic Navigation mode, suspend dynamic auto-rotation
    // but keep following user's center position.
    if (gpsState === GPS_STATES.NAVIGATION) {
      setGPSState(GPS_STATES.FOLLOW)
    }
  })

  // The map is now a driving HUD by default. Start in follow mode immediately;
  // the GPS control still cycles follow → navigation → off when desired.
  setGPSState(GPS_STATES.FOLLOW)
}

function setGPSState(state) {
  gpsState = state
  const gpsBtn = document.getElementById('gps-btn')
  const recenterBtn = document.getElementById('recenter-btn')

  gpsBtn.classList.remove('state-off', 'state-follow', 'state-navigation')

  if (state === GPS_STATES.OFF) {
    gpsBtn.classList.add('state-off')
    recenterBtn.classList.add('hidden')
    isTrackingSuspended = false
    userSpeedMps = null
    userLocationStatus = 'off'
    userCoords = null
    prevCoords = null
    userHeading = null
    userAccuracy = 0
    userHeadingHistory.length = 0
    renderCoords = null
    renderBearing = 0
    pendingZoom = null
    stopFollowLoop()
    setFollowPadding(false)
    // Reset the heading-up rotation and 3D tilt back to a flat, north-up map.
    map.easeTo({ bearing: 0, pitch: 0, duration: 500 })
    stopGPSWatcher()
    clearRoadSignHud()
  } else if (state === GPS_STATES.FOLLOW) {
    gpsBtn.classList.add('state-follow')
    if (isTrackingSuspended) recenterBtn.classList.remove('hidden')
    if (!userCoords && userLocationStatus !== 'denied') userLocationStatus = 'waiting'
    pendingZoom = Math.max(map.getZoom(), 15)
    setFollowPadding(true)
    // Follow is north-up; drop any nav pitch/bearing left over from a previous state.
    if (map.getPitch() !== 0) map.easeTo({ pitch: 0, duration: 400 })
    startGPSWatcher()
    startFollowLoop()
    renderRoadSignHud()
  } else if (state === GPS_STATES.NAVIGATION) {
    gpsBtn.classList.add('state-navigation')
    if (isTrackingSuspended) recenterBtn.classList.remove('hidden')
    if (!userCoords && userLocationStatus !== 'denied') userLocationStatus = 'waiting'
    pendingZoom = Math.max(map.getZoom(), 16)
    renderBearing = deviceHeading ?? userHeading ?? map.getBearing()
    setFollowPadding(true)
    startGPSWatcher()
    startFollowLoop()
    renderRoadSignHud()
  }
}

// Reserve empty space at the top of the viewport so the followed point (the
// user marker) sits low on screen, leaving the road ahead visible. Padding is
// persistent, so easeTo/jumpTo re-centre respects it.
function setFollowPadding (on) {
  const h = map.getContainer().clientHeight || 0
  const top = on ? Math.round(h * (FOLLOW_BOTTOM_RATIO * 2)) : 0
  map.setPadding({ top, bottom: 0, left: 0, right: 0 })
}

function startFollowLoop () {
  if (followRaf === null) followRaf = requestAnimationFrame(followTick)
}

function stopFollowLoop () {
  if (followRaf !== null) {
    cancelAnimationFrame(followRaf)
    followRaf = null
  }
}

// Interpolate the displayed marker + camera toward the latest GPS fix every
// frame. Exponential smoothing self-corrects toward the newest position, so a
// ~1 Hz fix stream renders as continuous glide rather than per-fix jumps.
function followTick () {
  followRaf = requestAnimationFrame(followTick)
  if (!userCoords) return
  if (!renderCoords) renderCoords = [...userCoords]

  renderCoords[0] += (userCoords[0] - renderCoords[0]) * FOLLOW_POS_LERP
  renderCoords[1] += (userCoords[1] - renderCoords[1]) * FOLLOW_POS_LERP
  // Snap the last sub-metre so the marker settles exactly on a fix.
  if (Math.abs(userCoords[0] - renderCoords[0]) < 1e-6) renderCoords[0] = userCoords[0]
  if (Math.abs(userCoords[1] - renderCoords[1]) < 1e-6) renderCoords[1] = userCoords[1]

  if (userMarker) userMarker.setLngLat(renderCoords)

  if (gpsState === GPS_STATES.OFF || isTrackingSuspended) return

  const cam = { center: renderCoords }
  if (pendingZoom !== null) { cam.zoom = pendingZoom; pendingZoom = null }
  if (gpsState === GPS_STATES.NAVIGATION) {
    const targetBearing = deviceHeading ?? userHeading
    if (targetBearing !== null && targetBearing !== undefined) {
      renderBearing = lerpAngle(renderBearing, targetBearing, FOLLOW_BEARING_LERP)
      cam.bearing = renderBearing
    }
    cam.pitch = 55
  }
  map.jumpTo(cam)
}

// Interpolate angle a→b along the shortest arc; handles the 0/360 wrap.
function lerpAngle (a, b, t) {
  const delta = ((b - a + 540) % 360) - 180
  return (a + delta * t + 360) % 360
}

// ─── Device compass (heading-up on mobile) ────────────────────────────────────
// iOS reports GPS heading only at speed; DeviceOrientation gives a live compass
// so the map can rotate to face travel direction even when slow or stopped.
function enableCompass () {
  if (orientationBound) return
  const bind = () => {
    window.addEventListener('deviceorientationabsolute', onDeviceOrientation, true)
    window.addEventListener('deviceorientation', onDeviceOrientation, true)
    orientationBound = true
  }
  const D = window.DeviceOrientationEvent
  if (D && typeof D.requestPermission === 'function') {
    D.requestPermission().then(s => { if (s === 'granted') bind() }).catch(() => {})
  } else if (D) {
    bind()
  }
}

function onDeviceOrientation (e) {
  let h = null
  if (typeof e.webkitCompassHeading === 'number' && !isNaN(e.webkitCompassHeading)) {
    h = e.webkitCompassHeading // iOS: degrees clockwise from true north
  } else if (e.absolute && typeof e.alpha === 'number' && !isNaN(e.alpha)) {
    h = (360 - e.alpha) % 360 // Android/absolute: alpha is counter-clockwise from north
  }
  if (h === null) return
  deviceHeading = h
  if (userHeading === null) userHeading = h
}

function startGPSWatcher() {
  if (geolocationWatchId !== null || document.visibilityState !== 'visible') return

  if (!navigator.geolocation) {
    console.warn('Geolocation is not supported by this browser.')
    return
  }

  geolocationWatchId = navigator.geolocation.watchPosition(
    onGeolocationUpdate,
    onGeolocationError,
    {
      enableHighAccuracy: true,
      timeout: 12000,
      maximumAge: 0
    }
  )
}

function pauseGPSWatcher () {
  if (geolocationWatchId === null) return
  navigator.geolocation.clearWatch(geolocationWatchId)
  geolocationWatchId = null
}

function stopGPSWatcher() {
  pauseGPSWatcher()
  
  if (userMarker) {
    userMarker.remove()
    userMarker = null
  }
  
  const source = map.getSource('user-accuracy')
  if (source) source.setData(EMPTY_FC)
}

function onGeolocationUpdate(position) {
  const { latitude, longitude, accuracy, heading, speed } = position.coords
  
  prevCoords = userCoords
  userCoords = [longitude, latitude]
  userAccuracy = accuracy || 0
  userSpeedMps = Number.isFinite(speed) ? speed : null
  userLocationStatus = 'ready'
  
  let headingSample = null
  if (heading !== null && !isNaN(heading)) {
    headingSample = heading
  } else if (prevCoords) {
    const dist = calculateDistance(prevCoords, userCoords)
    // Suppress jitter by only calculating direction when moving > 2 meters
    if (dist > 2) {
      headingSample = calculateBearing(prevCoords, userCoords)
    }
  } else if (deviceHeading !== null) {
    // No GPS heading yet (common on iOS when slow/stopped) — fall back to compass.
    headingSample = deviceHeading
  }
  if (headingSample !== null) {
    userHeadingHistory.push(headingSample)
    if (userHeadingHistory.length > 5) userHeadingHistory.shift()
    userHeading = circularMeanDegrees(userHeadingHistory)
  }

  updateUserMarker()
  updateAccuracyCircle()
  startFollowLoop()   // camera + marker are driven by the rAF follow loop
  renderRoadSignHud()
  fetchRoadSignHud()
}

function onGeolocationError(err) {
  console.warn('[geolocation]', err.message)
  userLocationStatus = err.code === 1 ? 'denied' : 'error'
  renderRoadSignHud()
}

function circularMeanDegrees (headings) {
  let x = 0
  let y = 0
  for (const heading of headings) {
    const radians = heading * Math.PI / 180
    x += Math.cos(radians)
    y += Math.sin(radians)
  }
  if (!headings.length || (x === 0 && y === 0)) return null
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    if (gpsState !== GPS_STATES.OFF) pauseGPSWatcher()
    stopFollowLoop()
    for (const controller of Object.values(controllers)) controller?.abort()
    return
  }

  if (gpsState !== GPS_STATES.OFF) { startGPSWatcher(); startFollowLoop() }
  fetchAll()
  if (!document.getElementById('status-body').classList.contains('hidden')) fetchFeedStatus()
})

function updateUserMarker() {
  if (!userCoords) return

  if (!userMarker) {
    const el = document.createElement('div')
    el.className = 'user-marker-container'
    
    const cone = document.createElement('div')
    cone.className = 'user-heading-cone'
    cone.id = 'user-heading-cone-el'
    
    const pulse = document.createElement('div')
    pulse.className = 'user-pulse'
    
    const dot = document.createElement('div')
    dot.className = 'user-dot'
    
    el.appendChild(cone)
    el.appendChild(pulse)
    el.appendChild(dot)
    
    userMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat(userCoords)
      .addTo(map)
  } else {
    userMarker.setLngLat(userCoords)
  }

  const coneEl = document.getElementById('user-heading-cone-el')
  if (coneEl) {
    if (userHeading !== null && userHeading !== undefined) {
      // Marker is screen-fixed, so subtract map bearing to keep the cone
      // pointing at the true compass heading even when the map is rotated.
      coneEl.style.setProperty('--heading', `${userHeading - map.getBearing()}deg`)
      coneEl.classList.add('visible')
    } else {
      coneEl.classList.remove('visible')
    }
  }
}

function updateAccuracyCircle() {
  const source = map.getSource('user-accuracy')
  if (!userCoords || !source) return
  
  if (userAccuracy > 5) {
    source.setData(makeCirclePolygon(userCoords[0], userCoords[1], userAccuracy))
  } else {
    source.setData(EMPTY_FC)
  }
}

// Recenter after the user manually panned away (tracking suspended). Clearing
// the suspend flag lets the follow loop resume; snap the smoothing origin to
// the current fix so it re-locks immediately instead of gliding across the map.
function updateCameraToUser() {
  if (!userCoords || gpsState === GPS_STATES.OFF) return
  isTrackingSuspended = false
  renderCoords = [...userCoords]
  pendingZoom = gpsState === GPS_STATES.NAVIGATION ? Math.max(map.getZoom(), 16) : Math.max(map.getZoom(), 15)
  startFollowLoop()
}

// ─── Mathematical Geolocation Helpers ────────────────────────────────────────

// Generate a small right-pointing arrow sprite for line symbol layers (travel
// time direction). Drawn to a canvas so we need no glyphs/sprite URL in the
// raster style. Points +x (along line digitisation start→end).
function addArrowImage() {
  if (map.hasImage('tt-arrow')) return
  const s = 24
  const c = document.createElement('canvas')
  c.width = c.height = s
  const x = c.getContext('2d')
  x.beginPath()
  x.moveTo(s * 0.25, s * 0.18)
  x.lineTo(s * 0.85, s * 0.5)
  x.lineTo(s * 0.25, s * 0.82)
  x.closePath()
  x.fillStyle = '#1a1a2a'
  x.strokeStyle = '#ffffff'
  x.lineWidth = 2.5
  x.lineJoin = 'round'
  x.fill()
  x.stroke()
  const img = x.getImageData(0, 0, s, s)
  map.addImage('tt-arrow', img, { pixelRatio: 2 })
}

function makeCirclePolygon(lng, lat, radiusMeters) {
  const steps = 64
  const coordinates = []
  const km = radiusMeters / 1000
  const R = 6378.1 // Earth's radius in km
  const latRad = (lat * Math.PI) / 180
  const lngRad = (lng * Math.PI) / 180
  
  for (let i = 0; i < steps; i++) {
    const theta = (i / steps) * 2 * Math.PI
    const rRad = km / R
    const cLat = Math.asin(
      Math.sin(latRad) * Math.cos(rRad) +
        Math.cos(latRad) * Math.sin(rRad) * Math.cos(theta)
    )
    const cLng =
      lngRad +
      Math.atan2(
        Math.sin(theta) * Math.sin(rRad) * Math.cos(latRad),
        Math.cos(rRad) - Math.sin(latRad) * Math.sin(cLat)
      )
    coordinates.push([(cLng * 180) / Math.PI, (cLat * 180) / Math.PI])
  }
  coordinates.push(coordinates[0])
  return {
    type: 'Feature',
    geometry: {
      type: 'Polygon',
      coordinates: [coordinates]
    },
    properties: {}
  }
}

// calculateDistance / calculateBearing moved to lib.js.
