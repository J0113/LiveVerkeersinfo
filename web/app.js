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
    key: 'nwb_roads', label: 'NWB Road Network', group: 'road_network',
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
    key: 'lane_speeds', label: 'Speed per Lane', group: 'traffic',
    endpoint: '/nwb/lane-speeds', geomType: 'lane-network', minZoom: 13,
    legendColor: '#41d67d', promoteId: 'lane_feature_id',
    paint: {
      // Approximate a 3.5 m physical lane at Dutch latitudes. Above z15 the
      // pixel spacing doubles per zoom level, so the ribbon grows with the road
      // instead of keeping a nearly fixed screen width.
      offset: ['interpolate', ['exponential', 2], ['zoom'],
        13, ['*', ['get', 'lane_offset_index'], 1.1],
        15, ['*', ['get', 'lane_offset_index'], 1.2],
        16, ['*', ['get', 'lane_offset_index'], 2.3],
        17, ['*', ['get', 'lane_offset_index'], 4.7],
        18, ['*', ['get', 'lane_offset_index'], 9.4],
        19, ['*', ['get', 'lane_offset_index'], 18.8],
        20, ['*', ['get', 'lane_offset_index'], 37.6]],
      glow: {
        'line-color': ['case', ['==', ['get', 'speed_kmh'], null], '#26394b', '#23d5ab'],
        'line-width': ['interpolate', ['exponential', 2], ['zoom'],
          13, 3.2, 15, 3.4, 16, 4.5, 17, 6.8, 18, 11.5, 19, 20.8, 20, 39.8],
        'line-opacity': ['case',
          ['==', ['get', 'speed_kmh'], null], 0.08,
          ['==', ['get', 'speed_estimated'], true], 0.12,
          0.18],
        'line-blur': ['interpolate', ['linear'], ['zoom'], 13, 0.5, 18, 1.5, 20, 2.5]
      },
      casing: {
        'line-color': '#07131e',
        'line-width': ['interpolate', ['exponential', 2], ['zoom'],
          13, 2.0, 15, 2.1, 16, 3.1, 17, 5.5, 18, 10.2, 19, 19.6, 20, 38.4],
        'line-opacity': 0.92
      },
      line: {
        'line-color': ['case',
          ['==', ['get', 'speed_kmh'], null], '#627587',
          ['interpolate', ['linear'], ['get', 'speed_kmh'],
            0, '#dc2946', 25, '#ef3e3e', 45, '#ff8a32', 65, '#ffd43b',
            85, '#7bd65c', 105, '#20c997', 130, '#33c7e8'
          ]
        ],
        'line-width': ['interpolate', ['exponential', 2], ['zoom'],
          13, 1.1, 15, 1.2, 16, 2.1, 17, 4.3, 18, 8.6, 19, 17.2, 20, 34.4],
        'line-opacity': ['case',
          ['==', ['get', 'speed_kmh'], null], 0.5,
          ['==', ['get', 'speed_estimated'], true], 0.82,
          0.98]
      }
    }
  },
  {
    key: 'speed', label: 'Traffic Speed', group: 'traffic',
    endpoint: '/nwb/lane-speeds', geomType: 'speed', minZoom: 13, legendColor: '#00cc44',
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
  }
]

// UI grouping order + labels
const GROUPS = [
  { key: 'road_network', label: 'Road Network' },
  { key: 'traffic',      label: 'Traffic' },
  { key: 'situations',   label: 'Situations' },
  { key: 'signs',        label: 'Signs & VMS' },
  { key: 'charging',     label: 'EV Charging' },
  { key: 'truckparking', label: 'Truck Parking' },
  { key: 'other',        label: 'Zones & Signs' },
  { key: 'reference',    label: 'Reference' }
]

// Traffic speed is presented in the pinned driving HUD, so it can remain on by
// default without adding marker clutter above the road-following lane layer.
const DEFAULT_ENABLED = new Set(['nwb_roads', 'lane_speeds', 'speed', 'drips'])
const EMPTY_FC = { type: 'FeatureCollection', features: [] }
let bboxTooLarge = false
let nwbTruncated = false

// ─── Runtime state ────────────────────────────────────────────────────────────

const enabled = new Set(DEFAULT_ENABLED)
const controllers = {}  // key → AbortController
let debounceTimer = null
let liveRefreshTimer = null
let layoutFrame = null
let lastLiveRefresh = 0
let responsivePanelIsMobile = false
let activePopup = null
let selectedFeature = null  // { source, id } currently highlighted (feature-state)
let msiMarkers = []    // { marker, el, bearing } for MSI gantries
let latestLaneCollection = EMPTY_FC
let latestDripCollection = EMPTY_FC
const nwbCache = new Map() // viewport/profile key → { expires, data }
const loadedViewports = new Map() // layer key → buffered successful request bounds
const NWB_BROWSER_CACHE_TTL_MS = 5 * 60_000
let publicConfig = { nwbDiagnosticMode: false }

// MSI gantries are dense; below this zoom they overlap into noise — skip rendering.
const MATRIX_MIN_ZOOM = 11

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
let userMarker = null      // maplibregl.Marker

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
  // Sync view (zoom/lat/lng/bearing/pitch) to the URL hash so a refresh restores it.
  hash: true
})

// ─── Map load: wire up sources, layers, and UI ────────────────────────────────

map.on('load', () => {
  addArrowImage()

  for (const layer of LAYERS) {
    // Both are rendered as HTML markers and do not need an empty MapLibre layer.
    if (layer.geomType === 'msi' || layer.geomType === 'speed') continue

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
    } else if (layer.geomType === 'lane-network') {
      for (const [suffix, paint] of [['glow', layer.paint.glow], ['casing', layer.paint.casing], ['', layer.paint.line]]) {
        map.addLayer({
          id: suffix ? `${layer.key}-${suffix}` : layer.key,
          type: 'line', source: layer.key,
          paint: { ...paint, 'line-offset': layer.paint.offset },
          // Butt caps make adjacent WEGGEG sections meet without rounded
          // bulges; round joins keep offsets smooth through bends.
          layout: { visibility: vis, 'line-cap': 'butt', 'line-join': 'round' },
          minzoom: layer.minZoom
        })
      }
      setupLaneSpeedPopup(layer.key)
    } else if (layer.geomType === 'polygon') {
      map.addLayer({ id: `${layer.key}-fill`, type: 'fill', source: layer.key, paint: layer.paint.fill, layout: { visibility: vis } })
      map.addLayer({ id: `${layer.key}-line`, type: 'line', source: layer.key, paint: layer.paint.line, layout: { visibility: vis } })
      setupClickPopup(`${layer.key}-fill`)
    } else if (layer.geomType === 'line') {
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

  buildLayerPanel()
  setupPanelToggles()
  syncResponsivePanel()
  window.addEventListener('resize', syncResponsivePanel)
  fetchPublicConfig()
  fetchAll()
  fetchFeedStatus()
  updateLaneLegend()

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

  startRefreshTimers()
})

map.on('moveend', () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(fetchAll, 300)
})

// Re-evaluate verkeersborden hint + re-fetch on zoom change
map.on('zoom', () => {
  updateZoomHint()
  scheduleMarkerLayout()
  updateLaneLegend()
})

map.on('zoomend', () => {
  // Fetch zoom-gated data once after the gesture, not on every animation frame.
  const layer = LAYERS.find(l => l.key === 'verkeersborden')
  if (layer && enabled.has('verkeersborden')) fetchLayer(layer)
})

// Keep roadside offsets correct while the map rotates (e.g. navigation mode).
map.on('rotate', scheduleMarkerLayout)

// ─── Fetch ────────────────────────────────────────────────────────────────────

function fetchAll () {
  if (document.hidden) return
  bboxTooLarge = false
  for (const layer of LAYERS) {
    if (enabled.has(layer.key)) fetchLayer(layer)
  }
}

function fetchLiveLayers () {
  if (document.hidden) return
  lastLiveRefresh = Date.now()
  bboxTooLarge = false
  for (const layer of LAYERS) {
    if (!enabled.has(layer.key) || isReferenceLayer(layer)) continue
    fetchLayer(layer, true)
  }
  fetchFeedStatus()
}

function isReferenceLayer (layer) {
  return layer.geomType === 'road-network' || layer.group === 'reference' ||
    ['emission_zones', 'verkeersborden'].includes(layer.key)
}

function startRefreshTimers () {
  clearInterval(liveRefreshTimer)
  lastLiveRefresh = Date.now()
  liveRefreshTimer = setInterval(fetchLiveLayers, 60_000)
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastLiveRefresh >= 60_000) fetchLiveLayers()
  })
}

function syncResponsivePanel () {
  // The centered driving HUD needs the horizontal space between the controls;
  // collapse the large layer list on tablets as well as phones.
  const isMobile = window.innerWidth <= 1100
  if (isMobile === responsivePanelIsMobile) return
  responsivePanelIsMobile = isMobile
  const body = document.getElementById('panel-body')
  const button = document.getElementById('panel-toggle')
  if (!body || !button) return
  body.classList.toggle('hidden', isMobile)
  button.textContent = isMobile ? 'Layers ▸' : 'Layers ▾'
}

function scheduleMarkerLayout () {
  if (layoutFrame !== null) return
  layoutFrame = requestAnimationFrame(() => {
    layoutFrame = null
    updateMatrixLayout()
  })
}

function bufferedViewportRequest (layerKey, profile, force = false) {
  const b = map.getBounds()
  const current = {
    west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth()
  }
  const loaded = loadedViewports.get(layerKey)
  const covered = loaded && loaded.profile === profile &&
    current.west >= loaded.west && current.south >= loaded.south &&
    current.east <= loaded.east && current.north <= loaded.north
  if (covered) {
    return { skip: !force, bbox: loaded.bbox, coverage: loaded }
  }

  // A 35% buffer on each side lets several small pans and zoom-ins reuse the
  // same GeoJSON source. Data is replaced only after the visible map leaves it.
  const padX = (current.east - current.west) * 0.35
  const padY = (current.north - current.south) * 0.35
  const coverage = {
    profile,
    west: current.west - padX,
    south: current.south - padY,
    east: current.east + padX,
    north: current.north + padY
  }
  coverage.bbox = [coverage.west, coverage.south, coverage.east, coverage.north]
    .map(v => v.toFixed(5)).join(',')
  return { skip: false, bbox: coverage.bbox, coverage }
}

function fetchLayer (layer, force = false) {
  if (layer.geomType === 'msi') { fetchMatrixSigns(); return }
  if (layer.geomType === 'speed') { fetchSpeedOverlay(layer, force); return }
  if (layer.geomType === 'road-network') { fetchNwbRoads(layer, force); return }
  if (layer.geomType === 'lane-network') { fetchLaneSpeeds(layer, force); return }

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
      if (layer.key === 'drips') {
        latestDripCollection = data
        renderDripHud(data)
      }
      if (layer.promoteId) reapplySelection(layer.key)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn(`[${layer.key}]`, e.message)
    })
}

function fetchLaneSpeeds (layer, force = false) {
  if (map.getZoom() < layer.minZoom) {
    controllers[layer.key]?.abort()
    map.getSource(layer.key)?.setData(EMPTY_FC)
    latestLaneCollection = EMPTY_FC
    loadedViewports.delete(layer.key)
    updateLaneLegend()
    return
  }
  const viewport = bufferedViewportRequest(layer.key, 'lane-detail', force)
  if (viewport.skip) return
  controllers[layer.key]?.abort()
  const ctrl = new AbortController()
  controllers[layer.key] = ctrl
  const bbox = viewport.bbox
  fetch(`/api${layer.endpoint}?bbox=${bbox}&zoom=${map.getZoom().toFixed(2)}`, { signal: ctrl.signal })
    .then(r => {
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(data => {
      latestLaneCollection = data
      map.getSource(layer.key)?.setData(data)
      loadedViewports.set(layer.key, viewport.coverage)
      updateLaneLegend(data.metadata)
      // The pinned HUD shares this exact segment response; refresh it only
      // after geometry, facility type and current values are all available.
      if (enabled.has('speed')) renderSpeedOverlay(latestLaneCollection)
    })
    .catch(e => {
      if (e.name !== 'AbortError') console.warn('[lane_speeds]', e.message)
    })
}

function setupLaneSpeedPopup (layerId) {
  map.on('click', layerId, e => {
    if (!e.features?.length) return
    const p = e.features[0].properties
    if (activePopup) activePopup.remove()
    const speed = p.speed_kmh == null ? 'Geen actuele meting' : `${Math.round(p.speed_kmh)} km/h`
    const flow = p.flow_veh_h == null ? '—' : `${Math.round(p.flow_veh_h)} voertuigen/uur`
    const reliability = p.speed_estimated === true || p.speed_estimated === 'true'
      ? 'geschat tussen actuele meetpunten'
      : p.match_confidence === 'high' ? 'hoog' : p.match_confidence === 'medium' ? 'gemiddeld' : '—'
    const variable = p.lane_count_variable === true || p.lane_count_variable === 'true'
    activePopup = new maplibregl.Popup({ maxWidth: '330px' })
      .setLngLat(e.lngLat)
      .setHTML(
        `<div class="lane-popup-speed">${esc(speed)}</div>` +
        `<div class="diagnostic-title">${esc(p.road_number || 'Rijksweg')} · rijstrook ${esc(p.lane_number)}</div>` +
        buildPopupHtml({
          rijbaan: p.carriageway_position || '—',
          doorstroming: flow,
          gemeten: p.measured_at || '—',
          meetpunten: p.input_count || p.measurement_count || '—',
          koppeling: reliability,
          ...(p.speed_estimation_method ? {
            schatting: p.speed_estimation_method === 'linear-between-current-measurements'
              ? `lineair over ${p.interpolation_span_km} km`
              : `korte uitloop · ${p.nearest_measurement_distance_m} m tot meting`
          } : {}),
          NWB_wegvak: p.nwb_road_section_id,
          rijstroken: variable ? `${p.lane_count_start} → ${p.lane_count_end}` : p.lane_count,
          geometrie: 'schematische offset op officiële wegas'
        })
      )
      .addTo(map)
  })
  map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = '' })
}

function updateLaneLegend (metadata) {
  const legend = document.getElementById('lane-speed-legend')
  if (!legend) return
  const visible = enabled.has('lane_speeds') && map.getZoom() >= 13
  legend.classList.toggle('hidden', !visible)
  if (metadata) {
    const measured = metadata.measuredLanes || 0
    const estimated = metadata.estimatedLanes || 0
    const total = metadata.totalLanes || 0
    const coverage = metadata.coveragePct || 0
    legend.querySelector('.lane-legend-count').textContent =
      `${measured} gemeten + ${estimated} geschat van ${total} · ${coverage}% dekking`
  }
}

function fetchNwbRoads (layer, force = false) {
  if (map.getZoom() < layer.minZoom) {
    controllers[layer.key]?.abort()
    map.getSource(layer.key)?.setData(EMPTY_FC)
    loadedViewports.delete(layer.key)
    nwbTruncated = false
    updateZoomHint()
    return
  }

  const zoom = map.getZoom()
  const profile = zoom < 11 ? 'national' : zoom < 12 ? 'major' : 'detailed'
  const viewport = bufferedViewportRequest(layer.key, profile, force)
  if (viewport.skip) return
  const bbox = viewport.bbox
  controllers[layer.key]?.abort()
  const ctrl = new AbortController()
  controllers[layer.key] = ctrl
  const cacheKey = `${profile}:${bbox}`
  const cached = nwbCache.get(cacheKey)
  if (cached && cached.expires > Date.now()) {
    loadedViewports.set(layer.key, viewport.coverage)
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
      loadedViewports.set(layer.key, viewport.coverage)
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

// ─── Matrix sign HTML markers ─────────────────────────────────────────────────

function fetchMatrixSigns () {
  if (map.getZoom() < MATRIX_MIN_ZOOM) {
    for (const m of msiMarkers) m.marker.remove()
    msiMarkers = []
    return
  }

  controllers['matrix']?.abort()
  const ctrl = new AbortController()
  controllers['matrix'] = ctrl

  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(v => v.toFixed(6)).join(',')

  fetch(`/api/signs/matrix?bbox=${bbox}`, { signal: ctrl.signal })
    .then(r => {
      if (r.status === 400) return r.json().then(body => Promise.reject(Object.assign(new Error(body.detail || 'Bad Request'), { isBboxError: /bbox area/i.test(body.detail || '') })))
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(fc => {
      setBboxTooLargeHint(false)
      renderMatrixMarkers(fc)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn('[matrix]', e.message)
    })
}

function renderMatrixMarkers (fc) {
  for (const m of msiMarkers) m.marker.remove()
  msiMarkers = []

  if (!enabled.has('matrix')) return

  // Group by road+km+carriageway = same physical gantry
  const gantries = new Map()
  for (const f of fc.features) {
    if (!f.geometry) continue
    const p = f.properties
    const key = `${p.road ?? ''}|${p.km ?? ''}|${p.carriageway ?? ''}`
    if (!gantries.has(key)) {
      gantries.set(key, { coords: f.geometry.coordinates, bearing: p.bearing, lanes: [] })
    }
    gantries.get(key).lanes.push(p)
  }

  for (const [, gantry] of gantries) {
    gantry.lanes.sort((a, b) => (a.lane ?? 0) - (b.lane ?? 0))

    // Outer wrapper: maplibre owns its transform for positioning.
    // Inner gantry: we apply scale + bearing rotation (won't be clobbered).
    const wrapper = document.createElement('div')
    const el = document.createElement('div')
    el.className = 'msi-gantry'
    wrapper.appendChild(el)

    for (const lane of gantry.lanes) {
      el.appendChild(buildMsiLane(lane))
    }

    el.addEventListener('click', e => {
      e.stopPropagation()
      if (activePopup) activePopup.remove()
      const first = gantry.lanes[0] || {}
      const header = `<div style="font-size:11px;color:#6688aa;margin-bottom:6px">${esc(first.road || '')} ${esc(first.carriageway || '')} km ${esc(String(first.km ?? ''))}</div>`
      const lanesHtml = gantry.lanes.map(l => `<b style="color:#6688aa;font-size:11px">Lane ${l.lane ?? '?'}</b>${buildPopupHtml(l)}`).join('<hr style="border-color:#2a2a40;margin:5px 0">')
      activePopup = new maplibregl.Popup({ maxWidth: '320px', offset: [0, -8] })
        .setLngLat(gantry.coords)
        .setHTML(header + lanesHtml)
        .addTo(map)
    })

    const marker = new maplibregl.Marker({ element: wrapper, anchor: 'center' })
      .setLngLat(gantry.coords)
      .addTo(map)
    msiMarkers.push({ marker, el, bearing: gantry.bearing })
  }

  updateMatrixLayout()
}

// buildMsiLane / addFlashingLamps moved to lib.js (shared with drive.js).

// Scale gantries with zoom and offset them to the roadside (perpendicular to the
// road bearing) so the signs sit beside the carriageway instead of on top of it.
// Recomputed on zoom and rotate; needs no refetch.
function updateMatrixLayout () {
  if (!msiMarkers.length) return
  const z = map.getZoom()
  // ~0.45 at z11 → 1.0 at z15+; keeps signs readable without swamping the map.
  const scale = Math.max(0.45, Math.min(1, 0.45 + (z - 11) * 0.1375))
  const mapBearing = map.getBearing()

  for (const m of msiMarkers) {
    if (m.bearing === null || m.bearing === undefined) {
      m.el.style.transform = `scale(${scale})`
      m.marker.setOffset([0, 0])
      continue
    }
    // Rotate the lane row to the road bearing so it spans across the carriageway.
    // Subtract map bearing so it stays road-aligned when the map rotates.
    m.el.style.transform = `rotate(${m.bearing - mapBearing}deg) scale(${scale})`
    // Shift outward (right of travel = roadside shoulder, NL drives on the right)
    // by half the sign width so the inner edge meets the road centerline and the
    // body extends to the outside. Opposite carriageways flip automatically.
    const screenAngle = ((m.bearing + 90 - mapBearing) * Math.PI) / 180
    const dist = (m.el.offsetWidth * scale) / 2 + 4
    const dx = Math.sin(screenAngle) * dist
    const dy = -Math.cos(screenAngle) * dist
    m.marker.setOffset([dx, dy])
  }
}

// ─── Pinned traffic-speed HUD ────────────────────────────────────────────────

function fetchSpeedOverlay (layer, force = false) {
  if (map.getZoom() < layer.minZoom) {
    renderSpeedOverlay(EMPTY_FC, 'Zoom verder in voor actuele rijstrooksnelheden')
    return
  }
  renderSpeedOverlay(latestLaneCollection)
  // When the map lane layer is off, the HUD still owns the shared lane request.
  if (!enabled.has('lane_speeds')) {
    const laneLayer = LAYERS.find(item => item.key === 'lane_speeds')
    if (laneLayer) fetchLaneSpeeds(laneLayer, force)
  }
}

function renderSpeedOverlay (fc, emptyMessage = 'Geen actuele rijstrookmeting in beeld') {
  const hud = document.getElementById('driving-hud')
  const panel = document.getElementById('traffic-speed-panel')
  const road = document.getElementById('speed-overlay-road')
  const meta = document.getElementById('speed-overlay-meta')
  const lanesEl = document.getElementById('speed-overlay-lanes')
  const empty = document.getElementById('speed-overlay-empty')
  if (!hud || !panel || !road || !meta || !lanesEl || !empty) return

  panel.classList.toggle('hidden', !enabled.has('speed'))
  syncDrivingHudVisibility()
  if (!enabled.has('speed')) return
  const roadContext = currentLaneRoadContext()
  const segmentLanes = roadContext
    ? [...roadContext.features]
        .sort((a, b) => Number(a.properties?.lane_number) - Number(b.properties?.lane_number))
    : []
  const hasData = segmentLanes.some(feature => feature.properties?.speed_kmh != null)
  if (!roadContext || !hasData) {
    road.textContent = roadContext
      ? formatRoadContext(roadContext)
      : 'Geen wegsegment geselecteerd'
    meta.textContent = ''
    lanesEl.replaceChildren()
    empty.textContent = roadContext ? 'Geen data beschikbaar voor dit segment' : emptyMessage
    empty.classList.remove('hidden')
    return
  }

  const lanes = segmentLanes.map(feature => ({
    lane: feature.properties.lane_number,
    speed_kmh: feature.properties.speed_kmh,
    flow_veh_h: feature.properties.flow_veh_h,
    speed_estimated: feature.properties.speed_estimated,
    measured_at: feature.properties.measured_at
  }))
  road.textContent = formatRoadContext(roadContext)
  const timestamps = lanes.map(lane => lane.measured_at).filter(Boolean).sort()
  const measured = timestamps.length ? new Date(timestamps[timestamps.length - 1]) : null
  const time = measured && !Number.isNaN(measured.getTime())
    ? measured.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : 'tijd onbekend'
  const estimates = lanes.filter(lane => lane.speed_estimated === true).length
  meta.textContent = estimates ? `${time} · ${estimates} geschat` : time
  lanesEl.replaceChildren()
  lanesEl.style.setProperty('--lane-count', Math.max(lanes.length, 1))

  for (const lane of lanes) {
    const card = document.createElement('div')
    card.className = 'speed-overlay-lane'
    card.classList.toggle('is-estimated', lane.speed_estimated === true)
    card.style.setProperty('--lane-color', speedColor(lane.speed_kmh))
    const number = document.createElement('span')
    number.className = 'speed-overlay-lane-number'
    number.textContent = `Rijstrook ${lane.lane ?? '?'}`
    const value = document.createElement('strong')
    value.className = 'speed-overlay-value'
    value.textContent = lane.speed_kmh == null ? '—' : `${Math.round(lane.speed_kmh)}`
    const unit = document.createElement('span')
    unit.className = 'speed-overlay-unit'
    unit.textContent = 'km/h'
    const flow = document.createElement('span')
    flow.className = 'speed-overlay-flow'
    flow.textContent = lane.flow_veh_h == null ? 'geen flowdata' : `${Math.round(lane.flow_veh_h)} voertuigen/u`
    card.append(number, value, unit, flow)
    lanesEl.appendChild(card)
  }
  empty.classList.add('hidden')
}

function renderDripHud (fc) {
  const panel = document.getElementById('drip-hud-panel')
  const road = document.getElementById('drip-hud-road')
  const meta = document.getElementById('drip-hud-meta')
  const message = document.getElementById('drip-hud-message')
  const image = document.getElementById('drip-hud-image')
  if (!panel || !road || !meta || !message || !image) return
  panel.classList.toggle('hidden', !enabled.has('drips'))
  syncDrivingHudVisibility()
  if (!enabled.has('drips')) return

  const selected = selectNearestRoadFeature(fc.features || [], false)
  if (!selected) {
    road.textContent = 'Geen DRIP/VMS in beeld'
    meta.textContent = ''
    message.textContent = 'Geen actueel bericht beschikbaar'
    message.classList.add('is-empty')
    image.classList.add('hidden')
    image.removeAttribute('src')
    return
  }
  const p = selected.feature.properties || {}
  road.textContent = p.description || p.controller_id || 'Dynamisch informatiepaneel'
  meta.textContent = `${Math.round(selected.distance)} m`
  const displayText = String(p.display_text || '').trim().replace(/\\n/g, '\n').replace(/\s*\|\s*/g, '\n')
  message.textContent = displayText || 'Paneel actief · geen tekstbericht'
  message.classList.toggle('is-empty', !displayText)
  const format = ['png', 'jpeg', 'jpg', 'gif', 'webp'].includes(String(p.image_format).toLowerCase())
    ? String(p.image_format).toLowerCase()
    : 'png'
  if (p.image_b64) {
    image.src = `data:image/${format};base64,${p.image_b64}`
    image.classList.remove('hidden')
  } else {
    image.classList.add('hidden')
    image.removeAttribute('src')
  }
}

function syncDrivingHudVisibility () {
  const hud = document.getElementById('driving-hud')
  if (!hud) return
  hud.classList.toggle('hidden', !enabled.has('speed') && !enabled.has('drips'))
}

function currentLaneRoadContext () {
  const center = userCoords && gpsState !== GPS_STATES.OFF
    ? userCoords
    : [map.getCenter().lng, map.getCenter().lat]
  return roadContextAtCoordinates(center)
}

function roadContextAtCoordinates (center) {
  const contexts = new Map()
  for (const feature of latestLaneCollection.features || []) {
    const p = feature.properties || {}
    const road = normalizeRoadId(p.road_number)
    const carriageway = normalizeCarriageway(p.carriageway_position)
    const weggegId = p.weggeg_id
    const segmentId = Number(p.nwb_road_section_id)
    const carriagewayType = normalizeCarriageway(p.carriageway_type)
    const formOfWay = Number(p.form_of_way)
    if (!road || !carriageway || !weggegId || !Number.isFinite(segmentId) || !carriagewayType) continue
    const distance = distanceToRoadGeometryMeters(center, feature.geometry)
    const hasData = p.speed_kmh != null
    const key = String(weggegId)
    const current = contexts.get(key)
    if (!current) {
      contexts.set(key, {
        road, carriageway, weggegId, segmentId, carriagewayType,
        formOfWay: Number.isFinite(formOfWay) ? formOfWay : null,
        distance,
        hasData,
        features: [feature]
      })
    } else {
      current.distance = Math.min(current.distance, distance)
      current.hasData ||= hasData
      current.features.push(feature)
    }
  }
  const nearest = [...contexts.values()].sort((a, b) =>
    (a.distance - b.distance) || (Number(b.hasData) - Number(a.hasData)))
  if (!nearest.length || nearest[0].distance > 60) return null
  // Adjacent WEGGEG intervals can share an endpoint. That tie is safe within
  // one NWB facility; a tie between different facilities remains ambiguous.
  if (
    nearest[1] && nearest[1].distance - nearest[0].distance < 1 &&
    (nearest[1].segmentId !== nearest[0].segmentId ||
      nearest[1].carriagewayType !== nearest[0].carriagewayType)
  ) return null
  const { distance, hasData, ...context } = nearest[0]
  return context
}

function formatRoadContext (context) {
  return `${context.road} · rijbaan ${context.carriageway} · ${carriagewayTypeLabel(context.carriagewayType)}`
}

function carriagewayTypeLabel (value) {
  return {
    HR: 'hoofdrijbaan',
    PST: 'parallelbaan',
    PKB: 'parallelbaan',
    OPR: 'oprit',
    AFR: 'afrit',
    VBR: 'verbindingsbaan',
    VBW: 'verbindingsweg',
    RB: 'rotondebaan',
    FP: 'fietspad'
  }[value] || `baantype ${value}`
}

function distanceToRoadGeometryMeters (point, geometry) {
  const lines = geometry?.type === 'MultiLineString'
    ? geometry.coordinates
    : geometry?.type === 'LineString' ? [geometry.coordinates] : []
  const cosLatitude = Math.cos(point[1] * Math.PI / 180)
  let minimum = Infinity
  for (const line of lines) {
    for (let i = 1; i < line.length; i++) {
      const start = {
        x: (line[i - 1][0] - point[0]) * 111320 * cosLatitude,
        y: (line[i - 1][1] - point[1]) * 110540
      }
      const end = {
        x: (line[i][0] - point[0]) * 111320 * cosLatitude,
        y: (line[i][1] - point[1]) * 110540
      }
      minimum = Math.min(minimum, distanceFromOriginToSegment(start, end))
    }
  }
  return minimum
}

function distanceFromOriginToSegment (start, end) {
  const dx = end.x - start.x
  const dy = end.y - start.y
  const lengthSquared = dx * dx + dy * dy
  if (lengthSquared === 0) return Math.hypot(start.x, start.y)
  const ratio = Math.min(Math.max((-(start.x * dx + start.y * dy)) / lengthSquared, 0), 1)
  return Math.hypot(start.x + ratio * dx, start.y + ratio * dy)
}

function normalizeRoadId (value) {
  if (value === null || value === undefined) return null
  let normalized = String(value).trim().toUpperCase().replace(/\s+/g, '')
  if (/^RW\d+$/.test(normalized)) normalized = `A${Number(normalized.slice(2))}`
  if (/^\d+$/.test(normalized)) normalized = String(Number(normalized))
  return normalized || null
}

function normalizeCarriageway (value) {
  if (value === null || value === undefined) return null
  const normalized = String(value).trim().toUpperCase()
  return normalized || null
}

function selectNearestRoadFeature (features, requireLanes) {
  const center = userCoords && gpsState !== GPS_STATES.OFF
    ? userCoords
    : [map.getCenter().lng, map.getCenter().lat]
  let best = null
  for (const feature of features) {
    if (feature.geometry?.type !== 'Point') continue
    if (requireLanes && !(feature.properties?.lanes || []).length) continue
    const distance = calculateDistance(center, feature.geometry.coordinates)
    const bearing = feature.properties.bearing ?? feature.properties.openlr_bearing
    let directionPenalty = 0
    if (userHeading !== null && bearing !== null && bearing !== undefined) {
      const difference = Math.abs(((Number(bearing) - userHeading + 540) % 360) - 180)
      directionPenalty = difference > 90 ? 5000 : difference * 2
    }
    const score = distance + directionPenalty
    if (!best || score < best.score) best = { feature, distance, score }
  }
  return best
}

function fetchFeedStatus () {
  fetch('/api/feeds')
    .then(r => r.ok ? r.json() : null)
    .then(renderFeedStatus)
    .catch(e => console.warn('[feeds/status]', e))
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

// esc moved to lib.js (shared with drive.js).

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
        fetchLayer(layer, true)
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
      fetchLayer(layer, true)
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
  if (layer.geomType === 'msi') {
    if (!visible) { for (const m of msiMarkers) m.marker.remove(); msiMarkers = [] }
    return
  }
  const vis = visible ? 'visible' : 'none'
  if (layer.geomType === 'road-network') {
    if (map.getLayer(`${layer.key}-casing`)) map.setLayoutProperty(`${layer.key}-casing`, 'visibility', vis)
    if (map.getLayer(layer.key)) map.setLayoutProperty(layer.key, 'visibility', vis)
    return
  }
  if (layer.geomType === 'lane-network') {
    for (const id of [`${layer.key}-glow`, `${layer.key}-casing`, layer.key]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis)
    }
    updateLaneLegend()
    return
  }
  if (layer.geomType === 'speed') {
    document.getElementById('traffic-speed-panel')?.classList.toggle('hidden', !visible)
    if (visible) renderSpeedOverlay(latestLaneCollection)
    syncDrivingHudVisibility()
    return
  }
  if (layer.key === 'drips') {
    document.getElementById('drip-hud-panel')?.classList.toggle('hidden', !visible)
    if (visible) renderDripHud(latestDripCollection)
    syncDrivingHudVisibility()
    // Continue so the map point layer also follows the same toggle.
  }
  if (layer.geomType === 'line') {
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
  document.getElementById('panel-toggle').addEventListener('click', () => {
    const body = document.getElementById('panel-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('panel-toggle').textContent = nowHidden ? 'Layers ▸' : 'Layers ▾'
  })

  document.getElementById('status-toggle').addEventListener('click', () => {
    const body = document.getElementById('status-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('status-toggle').textContent = nowHidden ? 'Feed Status ▸' : 'Feed Status ▾'
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

  // Map interaction events to detect user manual manipulation
  map.on('dragstart', () => {
    if (gpsState !== GPS_STATES.OFF) {
      isTrackingSuspended = true
      recenterBtn.classList.remove('hidden')
    }
  })

  map.on('rotatestart', () => {
    // If manually rotating while in dynamic Navigation mode, suspend dynamic auto-rotation
    // but keep following user's center position.
    if (gpsState === GPS_STATES.NAVIGATION) {
      setGPSState(GPS_STATES.FOLLOW)
    }
  })
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
    stopGPSWatcher()
  } else if (state === GPS_STATES.FOLLOW) {
    gpsBtn.classList.add('state-follow')
    if (isTrackingSuspended) recenterBtn.classList.remove('hidden')
    startGPSWatcher()
    
    if (userCoords) {
      map.easeTo({
        center: userCoords,
        bearing: 0,
        pitch: 0,
        zoom: Math.max(map.getZoom(), 15),
        duration: 1000
      })
    }
  } else if (state === GPS_STATES.NAVIGATION) {
    gpsBtn.classList.add('state-navigation')
    if (isTrackingSuspended) recenterBtn.classList.remove('hidden')
    startGPSWatcher()
    
    if (userCoords) {
      map.easeTo({
        center: userCoords,
        bearing: userHeading !== null ? userHeading : 0,
        pitch: 55,
        zoom: Math.max(map.getZoom(), 16),
        duration: 1000
      })
    }
  }
}

function startGPSWatcher() {
  if (geolocationWatchId !== null) return

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

function stopGPSWatcher() {
  if (geolocationWatchId !== null) {
    navigator.geolocation.clearWatch(geolocationWatchId)
    geolocationWatchId = null
  }
  
  if (userMarker) {
    userMarker.remove()
    userMarker = null
  }
  
  const source = map.getSource('user-accuracy')
  if (source) source.setData(EMPTY_FC)
}

function onGeolocationUpdate(position) {
  const { latitude, longitude, accuracy, heading } = position.coords
  
  prevCoords = userCoords
  userCoords = [longitude, latitude]
  userAccuracy = accuracy || 0
  
  if (heading !== null && !isNaN(heading)) {
    userHeading = heading
  } else if (prevCoords) {
    const dist = calculateDistance(prevCoords, userCoords)
    // Suppress jitter by only calculating direction when moving > 2 meters
    if (dist > 2) {
      userHeading = calculateBearing(prevCoords, userCoords)
    }
  }

  updateUserMarker()
  updateAccuracyCircle()
  updateCameraToUser()
  if (enabled.has('speed')) renderSpeedOverlay(latestLaneCollection)
  if (enabled.has('drips')) renderDripHud(latestDripCollection)
}

function onGeolocationError(err) {
  console.warn('[geolocation]', err.message)
}

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
      coneEl.style.setProperty('--heading', `${userHeading}deg`)
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

function updateCameraToUser() {
  if (!userCoords || gpsState === GPS_STATES.OFF || isTrackingSuspended) return

  const targetBearing = gpsState === GPS_STATES.NAVIGATION && userHeading !== null ? userHeading : map.getBearing()
  const targetPitch = gpsState === GPS_STATES.NAVIGATION ? 55 : map.getPitch()
  const targetZoom = gpsState === GPS_STATES.NAVIGATION ? Math.max(map.getZoom(), 16) : Math.max(map.getZoom(), 15)

  map.easeTo({
    center: userCoords,
    zoom: targetZoom,
    bearing: targetBearing,
    pitch: targetPitch,
    duration: 1200,
    essential: true
  })
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

// calculateDistance / calculateBearing moved to lib.js (shared with drive.js).
