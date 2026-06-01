'use strict'

// ─── Layer definitions ────────────────────────────────────────────────────────
//
// geomType 'point'   → MapLibre circle layer
// geomType 'polygon' → MapLibre fill + line layers (paint must have .fill / .line sub-keys)
// minZoom            → only fetch + render when map zoom >= this value

const LAYERS = [
  // ── Traffic ────────────────────────────────────────────────────────────────
  {
    key: 'speed', label: 'Traffic Speed', group: 'traffic',
    endpoint: '/traffic/speed', geomType: 'speed', legendColor: '#00cc44',
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
  { key: 'traffic',      label: 'Traffic' },
  { key: 'situations',   label: 'Situations' },
  { key: 'signs',        label: 'Signs & VMS' },
  { key: 'charging',     label: 'EV Charging' },
  { key: 'truckparking', label: 'Truck Parking' },
  { key: 'other',        label: 'Zones & Signs' },
  { key: 'reference',    label: 'Reference' }
]

const DEFAULT_ENABLED = new Set(['speed', 'matrix', 'drips'])
const EMPTY_FC = { type: 'FeatureCollection', features: [] }
let bboxTooLarge = false

// ─── Runtime state ────────────────────────────────────────────────────────────

const enabled = new Set(DEFAULT_ENABLED)
const controllers = {}  // key → AbortController
let debounceTimer = null
let activePopup = null
let msiMarkers = []    // { marker, el, bearing } for MSI gantries
let speedMarkers = []  // maplibregl.Marker instances for traffic speed sites

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
  for (const layer of LAYERS) {
    if (layer.geomType === 'msi') continue  // rendered as HTML markers, not MapLibre layers

    map.addSource(layer.key, { type: 'geojson', data: EMPTY_FC })
    const vis = enabled.has(layer.key) ? 'visible' : 'none'

    if (layer.geomType === 'polygon') {
      map.addLayer({ id: `${layer.key}-fill`, type: 'fill', source: layer.key, paint: layer.paint.fill, layout: { visibility: vis } })
      map.addLayer({ id: `${layer.key}-line`, type: 'line', source: layer.key, paint: layer.paint.line, layout: { visibility: vis } })
      setupClickPopup(`${layer.key}-fill`)
    } else if (layer.geomType === 'line') {
      map.addLayer({ id: layer.key, type: 'line', source: layer.key, paint: layer.paint, layout: { visibility: vis } })
      setupClickPopup(layer.key)
    } else {
      map.addLayer({ id: layer.key, type: 'circle', source: layer.key, paint: layer.paint, layout: { visibility: vis } })
      setupClickPopup(layer.key)
    }
  }

  buildLayerPanel()
  setupPanelToggles()
  fetchAll()
  fetchFeedStatus()

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

  setInterval(fetchAll, 60_000)
  setInterval(fetchFeedStatus, 60_000)
})

map.on('moveend', () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(fetchAll, 300)
})

// Re-evaluate verkeersborden hint + re-fetch on zoom change
map.on('zoom', () => {
  updateZoomHint()
  updateMatrixLayout()
  updateSpeedLayout()
  // If verkeersborden just crossed zoom 13, trigger a fetch
  const layer = LAYERS.find(l => l.key === 'verkeersborden')
  if (layer && enabled.has('verkeersborden')) fetchLayer(layer)
})

// Keep roadside offsets correct while the map rotates (e.g. navigation mode).
map.on('rotate', () => { updateMatrixLayout(); updateSpeedLayout() })

// ─── Fetch ────────────────────────────────────────────────────────────────────

function fetchAll () {
  bboxTooLarge = false
  for (const layer of LAYERS) {
    if (enabled.has(layer.key)) fetchLayer(layer)
  }
}

function fetchLayer (layer) {
  if (layer.geomType === 'msi') { fetchMatrixSigns(); return }
  if (layer.geomType === 'speed') { fetchSpeedMarkers(); return }

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
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn(`[${layer.key}]`, e.message)
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

// ─── Traffic speed HTML markers ───────────────────────────────────────────────

function fetchSpeedMarkers () {
  controllers['speed']?.abort()
  const ctrl = new AbortController()
  controllers['speed'] = ctrl

  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(v => v.toFixed(6)).join(',')

  fetch(`/api/traffic/speed?bbox=${bbox}`, { signal: ctrl.signal })
    .then(r => {
      if (r.status === 400) return r.json().then(body => Promise.reject(Object.assign(new Error(body.detail || 'Bad Request'), { isBboxError: /bbox area/i.test(body.detail || '') })))
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(fc => {
      setBboxTooLargeHint(false)
      renderSpeedMarkers(fc)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn('[speed]', e.message)
    })
}

function renderSpeedMarkers (fc) {
  for (const m of speedMarkers) m.marker.remove()
  speedMarkers = []

  if (!enabled.has('speed')) return

  for (const f of fc.features) {
    if (!f.geometry) continue
    const p = f.properties
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

// Rotate speed-site rows to the road bearing and offset them roadside, scaled by
// zoom — same treatment as MSI gantries. Recomputed on zoom/rotate, no refetch.
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
    m.el.style.transform = `rotate(${m.bearing - mapBearing}deg) scale(${scale})`
    const screenAngle = ((m.bearing + 90 - mapBearing) * Math.PI) / 180
    const dist = (m.el.offsetWidth * scale) / 2 + 3
    m.marker.setOffset([Math.sin(screenAngle) * dist, -Math.cos(screenAngle) * dist])
  }
}

// speedColor / speedTextColor moved to lib.js (shared with drive.js).

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
  if (layer.geomType === 'msi') {
    if (!visible) { for (const m of msiMarkers) m.marker.remove(); msiMarkers = [] }
    return
  }
  if (layer.geomType === 'speed') {
    if (!visible) { for (const m of speedMarkers) m.marker.remove(); speedMarkers = [] }
    return
  }
  const vis = visible ? 'visible' : 'none'
  if (layer.geomType === 'line') {
    if (map.getLayer(layer.key)) map.setLayoutProperty(layer.key, 'visibility', vis)
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
