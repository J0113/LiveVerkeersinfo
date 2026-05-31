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
  }
]

// UI grouping order + labels
const GROUPS = [
  { key: 'traffic',      label: 'Traffic' },
  { key: 'situations',   label: 'Situations' },
  { key: 'signs',        label: 'Signs & VMS' },
  { key: 'charging',     label: 'EV Charging' },
  { key: 'truckparking', label: 'Truck Parking' },
  { key: 'other',        label: 'Zones & Signs' }
]

const DEFAULT_ENABLED = new Set(['speed', 'sit_incident', 'sit_roadworks', 'sit_closure', 'charging'])
const EMPTY_FC = { type: 'FeatureCollection', features: [] }
let bboxTooLarge = false

// ─── Runtime state ────────────────────────────────────────────────────────────

const enabled = new Set(DEFAULT_ENABLED)
const controllers = {}  // key → AbortController
let debounceTimer = null
let activePopup = null
let msiMarkers = []    // maplibregl.Marker instances for MSI gantries
let speedMarkers = []  // maplibregl.Marker instances for traffic speed sites

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
  zoom: 7
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
    } else {
      map.addLayer({ id: layer.key, type: 'circle', source: layer.key, paint: layer.paint, layout: { visibility: vis } })
      setupClickPopup(layer.key)
    }
  }

  buildLayerPanel()
  setupPanelToggles()
  fetchAll()
  fetchFeedStatus()

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
  // If verkeersborden just crossed zoom 13, trigger a fetch
  const layer = LAYERS.find(l => l.key === 'verkeersborden')
  if (layer && enabled.has('verkeersborden')) fetchLayer(layer)
})

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
  for (const m of msiMarkers) m.remove()
  msiMarkers = []

  if (!enabled.has('matrix')) return

  // Group by road+km+carriageway = same physical gantry
  const gantries = new Map()
  for (const f of fc.features) {
    if (!f.geometry) continue
    const p = f.properties
    const key = `${p.road ?? ''}|${p.km ?? ''}|${p.carriageway ?? ''}`
    if (!gantries.has(key)) gantries.set(key, { coords: f.geometry.coordinates, lanes: [] })
    gantries.get(key).lanes.push(p)
  }

  for (const [, gantry] of gantries) {
    gantry.lanes.sort((a, b) => (a.lane ?? 0) - (b.lane ?? 0))

    const el = document.createElement('div')
    el.className = 'msi-gantry'

    for (const lane of gantry.lanes) {
      const box = document.createElement('div')
      const aspect = lane.aspect_type || ''
      const val = lane.value

      if ((aspect === 'speedlimit' || (!aspect && val)) && val) {
        box.className = 'msi-lane'
        const disc = document.createElement('div')
        disc.className = 'msi-speed-disc'
        disc.textContent = val
        box.appendChild(disc)
      } else if (aspect === 'lane_open' || aspect.includes('arrow')) {
        box.className = 'msi-lane lane-open'
        box.textContent = '▼'
      } else if (aspect === 'lane_closed') {
        box.className = 'msi-lane lane-closed'
        box.textContent = '✕'
      } else if (aspect === 'lane_closed_ahead') {
        box.className = 'msi-lane lane-closed-ahead'
        box.textContent = '✕'
      } else if (aspect === 'restriction_end' || aspect === 'end_of_restriction') {
        box.className = 'msi-lane restriction-end'
        box.textContent = '╱'
      } else {
        box.className = 'msi-lane blank'
      }

      box.title = [lane.road, lane.carriageway, `lane ${lane.lane ?? '?'}`, aspect || val].filter(Boolean).join(' · ')
      el.appendChild(box)
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

    msiMarkers.push(
      new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat(gantry.coords)
        .addTo(map)
    )
  }
}

function msiArrow (aspect) {
  if (aspect.includes('RIGHT')) return '→'
  if (aspect.includes('LEFT'))  return '←'
  return '↑'
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
  for (const m of speedMarkers) m.remove()
  speedMarkers = []

  if (!enabled.has('speed')) return

  for (const f of fc.features) {
    if (!f.geometry) continue
    const p = f.properties
    const lanes = p.lanes || []
    if (!lanes.length) continue

    const el = document.createElement('div')
    el.className = 'speed-site'

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
      const header = `<div style="font-size:11px;color:#6688aa;margin-bottom:6px">${esc(p.site_id)} · ${esc(p.side || '')} · ${p.num_lanes ?? '?'} lanes</div>`
      const table = `<table class="popup-table"><tbody>` +
        (p.measured_at ? `<tr><td class="pk">measured_at</td><td>${esc(p.measured_at)}</td></tr>` : '') +
        lanes.map(l => `<tr><td class="pk">lane ${l.lane}</td><td>${l.speed_kmh !== null ? Math.round(l.speed_kmh) + ' km/h' : '—'}${l.flow_veh_h !== null ? ' · ' + Math.round(l.flow_veh_h) + ' veh/h' : ''}</td></tr>`).join('') +
        `</tbody></table>`
      activePopup = new maplibregl.Popup({ maxWidth: '300px', offset: [0, -8] })
        .setLngLat(f.geometry.coordinates)
        .setHTML(header + table)
        .addTo(map)
    })

    speedMarkers.push(
      new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat(f.geometry.coordinates)
        .addTo(map)
    )
  }
}

function speedColor (kmh) {
  if (kmh === null || kmh === undefined) return '#444'
  if (kmh <= 0)   return '#cc2200'
  if (kmh <= 30)  return '#ff5500'
  if (kmh <= 60)  return '#ffaa00'
  if (kmh <= 80)  return '#ffdd00'
  if (kmh <= 100) return '#aaee00'
  if (kmh <= 120) return '#00cc44'
  return '#00ffaa'
}

function speedTextColor (kmh) {
  if (kmh !== null && kmh > 60 && kmh <= 100) return '#111'
  return '#fff'
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

function esc (s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

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
    if (!visible) { for (const m of msiMarkers) m.remove(); msiMarkers = [] }
    return
  }
  if (layer.geomType === 'speed') {
    if (!visible) { for (const m of speedMarkers) m.remove(); speedMarkers = [] }
    return
  }
  const vis = visible ? 'visible' : 'none'
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
