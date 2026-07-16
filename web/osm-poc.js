'use strict'

// ─── OSM × live NDW speed proof of concept ──────────────────────────────────

const OSM_POC_SOURCE = 'osm_poc'
const OSM_POC_MEASUREMENTS_SOURCE = 'osm_poc_measurements'
const OSM_POC_CELL_DEG = 0.002
let osmPocRoads = EMPTY_FC
let osmPocMetadata = null
let osmPocGrid = new Map()
let osmPocSelectedEdgeId = null
let osmPocCurrentMatch = null
let osmPocCurrentEdgeState = null
let osmPocPendingEdgeId = null
let osmPocPendingCount = 0
let osmPocRequestInFlight = false
let osmPocRefreshQueued = false

function initOsmPoc () {
  ensureOsmPocInspector()
  map.addSource(OSM_POC_SOURCE, {
    type: 'geojson',
    data: EMPTY_FC,
    promoteId: 'edge_id',
    attribution: '© OpenStreetMap contributors, ODbL'
  })
  map.addSource(OSM_POC_MEASUREMENTS_SOURCE, { type: 'geojson', data: EMPTY_FC })

  map.addLayer({
    id: 'osm-poc-casing', type: 'line', source: OSM_POC_SOURCE,
    minzoom: 13,
    paint: {
      'line-color': 'rgba(3, 9, 14, 0.88)',
      'line-width': ['interpolate', ['linear'], ['zoom'], 11, 3.2, 14, 6.5, 17, 12],
      'line-opacity': 0.9,
      'line-offset': ['*', ['coalesce', ['get', 'direction_offset'], 0], 2]
    },
    layout: { visibility: enabled.has('osm_poc') ? 'visible' : 'none', 'line-cap': 'round', 'line-join': 'round' }
  })
  map.addLayer({
    id: 'osm-poc-roads', type: 'line', source: OSM_POC_SOURCE,
    minzoom: 13,
    paint: {
      'line-width': ['interpolate', ['linear'], ['zoom'],
        13, ['case',
          ['boolean', ['feature-state', 'selected'], false], 6,
          ['boolean', ['feature-state', 'current'], false], 5,
          2.6
        ],
        17, ['case',
          ['boolean', ['feature-state', 'selected'], false], 12,
          ['boolean', ['feature-state', 'current'], false], 11,
          8
        ]
      ],
      'line-color': ['case',
        ['boolean', ['feature-state', 'selected'], false], '#6ee7ff',
        ['boolean', ['feature-state', 'current'], false], '#ff66d8',
        ['==', ['get', 'speed_kmh'], null],
        ['match', ['get', 'highway'],
          'motorway', '#73869b', 'motorway_link', '#667b91',
          'trunk', '#70879a', 'primary', '#657d90', '#536b7d'
        ],
        ['interpolate', ['linear'], ['get', 'speed_kmh'],
          0, '#c8324a', 25, '#e34b3f', 45, '#ef8b36',
          65, '#f2d14a', 85, '#62c86b', 110, '#23a96a'
        ]
      ],
      'line-opacity': ['case', ['==', ['get', 'speed_kmh'], null], 0.72, 0.98],
      'line-offset': ['*', ['coalesce', ['get', 'direction_offset'], 0], 2]
    },
    layout: { visibility: enabled.has('osm_poc') ? 'visible' : 'none', 'line-cap': 'round', 'line-join': 'round' }
  })
  map.addLayer({
    id: 'osm-poc-direction', type: 'symbol', source: OSM_POC_SOURCE,
    minzoom: 13,
    layout: {
      'symbol-placement': 'line',
      'symbol-spacing': 95,
      'icon-image': 'tt-arrow',
      'icon-size': 0.65,
      'icon-rotation-alignment': 'map',
      'icon-allow-overlap': false,
      'icon-ignore-placement': true,
      visibility: enabled.has('osm_poc') ? 'visible' : 'none'
    },
    paint: { 'icon-opacity': 0.75 }
  })
  // Wide transparent hit target keeps thin directed edges usable on touch
  // devices without making the visual road stroke artificially thick.
  map.addLayer({
    id: 'osm-poc-hit', type: 'line', source: OSM_POC_SOURCE,
    minzoom: 13,
    paint: { 'line-width': 20, 'line-opacity': 0 },
    layout: { visibility: enabled.has('osm_poc') ? 'visible' : 'none', 'line-cap': 'round' }
  })
  map.addLayer({
    id: 'osm-poc-measurements', type: 'circle', source: OSM_POC_MEASUREMENTS_SOURCE,
    minzoom: 13,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 3, 15, 6],
      'circle-color': ['match', ['get', 'osm_match_status'],
        'matched', '#ffffff', 'ambiguous', '#ffbf3f', '#ef476f'
      ],
      'circle-stroke-color': ['match', ['get', 'osm_match_status'],
        'matched', '#18232e', 'ambiguous', '#5d3c00', '#5f1027'
      ],
      'circle-stroke-width': 1.5,
      'circle-opacity': 0.92
    },
    layout: { visibility: enabled.has('osm_poc') ? 'visible' : 'none' }
  })

  map.on('click', 'osm-poc-hit', e => {
    if (!e.features?.length) return
    const feature = e.features[0]
    selectOsmPocEdge(feature.properties.edge_id, 'Kaartselectie')
    renderOsmPocFeature(feature.properties, null, 'Kaartselectie')
  })
  map.on('click', 'osm-poc-measurements', e => {
    if (!e.features?.length) return
    const props = e.features[0].properties
    if (props.osm_edge_id) selectOsmPocEdge(props.osm_edge_id, 'Meetlocatie')
    renderOsmPocMeasurement(props)
  })
  for (const layerId of ['osm-poc-hit', 'osm-poc-measurements']) {
    map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = '' })
  }
}

function fetchOsmPoc (layer) {
  const inspector = ensureOsmPocInspector()
  const visible = enabled.has(layer.key)
  inspector.classList.toggle('hidden', !visible)
  if (!visible) {
    osmPocRefreshQueued = false
    return
  }
  if (map.getZoom() < layer.minZoom) {
    map.getSource(OSM_POC_SOURCE)?.setData(EMPTY_FC)
    map.getSource(OSM_POC_MEASUREMENTS_SOURCE)?.setData(EMPTY_FC)
    setOsmPocStatus('Zoom in tot niveau 13 om een begrensde OSM-corridor te laden.', 'idle')
    return
  }

  // AbortController only cancels the browser side of this legacy synchronous
  // Overpass request; it cannot stop the backend worker. Keep exactly one
  // request in flight and coalesce all pan/zoom refreshes into one latest retry.
  if (osmPocRequestInFlight) {
    osmPocRefreshQueued = true
    setOsmPocStatus('Externe diagnose is bezig; nieuwste kaartpositie staat in de wachtrij…', 'loading')
    return
  }
  osmPocRequestInFlight = true
  osmPocRefreshQueued = false
  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(v => v.toFixed(6)).join(',')
  const profile = map.getZoom() >= 15 ? 'detailed' : 'major'
  setOsmPocStatus(`Externe Overpass-diagnose laden (${profile === 'major' ? 'hoofdwegen' : 'detailwegen'}); dit kan tientallen seconden duren…`, 'loading')

  fetch(`/api${layer.endpoint}?bbox=${bbox}&profile=${profile}&speed_limit=750`)
    .then(async response => {
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${response.status}`)
      }
      return response.json()
    })
    .then(data => {
      // Do not flash stale geometry when the viewport moved during the request;
      // the coalesced request below will fetch the latest bounds.
      if (!enabled.has(layer.key) || osmPocRefreshQueued) return
      osmPocRoads = data.roads || EMPTY_FC
      osmPocMetadata = data.metadata || {}
      map.getSource(OSM_POC_SOURCE)?.setData(osmPocRoads)
      map.getSource(OSM_POC_MEASUREMENTS_SOURCE)?.setData(data.measurements || EMPTY_FC)
      buildOsmPocGrid(osmPocRoads.features || [])
      renderOsmPocSummary(osmPocMetadata)
      if (osmPocSelectedEdgeId) selectOsmPocEdge(osmPocSelectedEdgeId, 'Behouden selectie')
      if (userCoords) updateOsmPocUserMatch(userCoords, movementHeading, userAccuracy)
    })
    .catch(error => {
      setOsmPocStatus(`POC niet geladen: ${error.message}`, 'error')
      console.warn('[osm_poc]', error.message)
    })
    .finally(() => {
      osmPocRequestInFlight = false
      if (!osmPocRefreshQueued) return
      osmPocRefreshQueued = false
      if (enabled.has(layer.key)) setTimeout(() => fetchOsmPoc(layer), 0)
    })
}

function buildOsmPocGrid (features) {
  osmPocGrid = new Map()
  features.forEach((feature, featureIndex) => {
    const coords = feature.geometry?.coordinates || []
    for (let i = 0; i < coords.length - 1; i++) {
      const a = coords[i]
      const b = coords[i + 1]
      const minX = Math.floor(Math.min(a[0], b[0]) / OSM_POC_CELL_DEG)
      const maxX = Math.floor(Math.max(a[0], b[0]) / OSM_POC_CELL_DEG)
      const minY = Math.floor(Math.min(a[1], b[1]) / OSM_POC_CELL_DEG)
      const maxY = Math.floor(Math.max(a[1], b[1]) / OSM_POC_CELL_DEG)
      for (let x = minX; x <= maxX; x++) {
        for (let y = minY; y <= maxY; y++) {
          const key = `${x}:${y}`
          if (!osmPocGrid.has(key)) osmPocGrid.set(key, [])
          osmPocGrid.get(key).push({ featureIndex, a, b })
        }
      }
    }
  })
}

function updateOsmPocUserMatch (coords, heading, accuracy = 0) {
  if (!enabled.has('osm_poc') || !coords || !osmPocRoads.features?.length) return
  const radius = Math.max(25, Math.min(80, Math.max(accuracy || 0, 8) * 2))
  const candidates = osmPocCandidates(coords, heading, radius)
  if (!candidates.length) {
    clearOsmPocCurrentState()
    osmPocCurrentMatch = null
    renderOsmPocNoMatch('Geen OSM-edge binnen de GPS-zoekradius.')
    return
  }

  const best = candidates[0]
  const second = candidates[1]
  const margin = second ? second.score - best.score : 100
  let confidence = 0.55 * Math.max(0, 1 - best.distance / radius)
  confidence += heading === null || heading === undefined
    ? 0.12
    : 0.3 * Math.max(0, 1 - best.headingDelta / 90)
  confidence += 0.15 * Math.min(1, Math.max(0, margin) / 15)
  if (!Number.isFinite(heading)) confidence = Math.min(confidence, 0.48)
  confidence = Math.max(0, Math.min(1, confidence))

  const currentId = osmPocCurrentMatch?.edgeId
  if (currentId && best.edgeId !== currentId) {
    if (osmPocPendingEdgeId === best.edgeId) osmPocPendingCount++
    else { osmPocPendingEdgeId = best.edgeId; osmPocPendingCount = 1 }
    if (osmPocPendingCount < 2 || margin < 4) {
      const retained = candidates.find(c => c.edgeId === currentId)
      if (retained) {
        renderOsmPocGpsMatch(retained, osmPocCurrentMatch.confidence, true)
        return
      }
    }
  }

  osmPocPendingEdgeId = null
  osmPocPendingCount = 0
  osmPocCurrentMatch = { edgeId: best.edgeId, confidence }
  setOsmPocCurrentState(best.edgeId)
  renderOsmPocGpsMatch(best, confidence, false)
}

function osmPocCandidates (coords, heading, radius) {
  const [lon, lat] = coords
  const cx = Math.floor(lon / OSM_POC_CELL_DEG)
  const cy = Math.floor(lat / OSM_POC_CELL_DEG)
  const byFeature = new Map()
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      for (const segment of osmPocGrid.get(`${cx + dx}:${cy + dy}`) || []) {
        const projection = osmPocProjectSegment(coords, segment.a, segment.b)
        if (projection.distance > radius) continue
        const headingDelta = Number.isFinite(heading) ? angleDiff(heading, projection.bearing) : 0
        if (Number.isFinite(heading) && headingDelta > 90) continue
        const feature = osmPocRoads.features[segment.featureIndex]
        const edgeId = feature.properties.edge_id
        let score = projection.distance + (Number.isFinite(heading) ? headingDelta * 0.45 : 18)
        if (osmPocCurrentMatch?.edgeId === edgeId) score -= 10
        const candidate = {
          edgeId,
          feature,
          distance: projection.distance,
          bearing: projection.bearing,
          headingDelta,
          score
        }
        const previous = byFeature.get(edgeId)
        if (!previous || score < previous.score) byFeature.set(edgeId, candidate)
      }
    }
  }
  return [...byFeature.values()].sort((a, b) => a.score - b.score)
}

function osmPocProjectSegment (point, a, b) {
  const lat = point[1]
  const metresLon = 111320 * Math.cos(lat * Math.PI / 180)
  const metresLat = 110540
  const ax = (a[0] - point[0]) * metresLon
  const ay = (a[1] - point[1]) * metresLat
  const bx = (b[0] - point[0]) * metresLon
  const by = (b[1] - point[1]) * metresLat
  const dx = bx - ax
  const dy = by - ay
  const denom = dx * dx + dy * dy
  const t = denom ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denom)) : 0
  const px = ax + t * dx
  const py = ay + t * dy
  return {
    distance: Math.hypot(px, py),
    bearing: (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360
  }
}

function selectOsmPocEdge (edgeId, source) {
  if (!edgeId || !map.getSource(OSM_POC_SOURCE)) return
  if (osmPocSelectedEdgeId) {
    map.setFeatureState({ source: OSM_POC_SOURCE, id: osmPocSelectedEdgeId }, { selected: false })
  }
  osmPocSelectedEdgeId = edgeId
  map.setFeatureState({ source: OSM_POC_SOURCE, id: edgeId }, { selected: true })
  const feature = osmPocRoads.features?.find(f => f.properties.edge_id === edgeId)
  if (feature) renderOsmPocFeature(feature.properties, null, source)
}

function setOsmPocCurrentState (edgeId) {
  clearOsmPocCurrentState()
  if (edgeId) {
    map.setFeatureState({ source: OSM_POC_SOURCE, id: edgeId }, { current: true })
    osmPocCurrentEdgeState = edgeId
  }
}

function clearOsmPocCurrentState () {
  if (osmPocCurrentEdgeState && map.getSource(OSM_POC_SOURCE)) {
    map.setFeatureState({ source: OSM_POC_SOURCE, id: osmPocCurrentEdgeState }, { current: false })
  }
  osmPocCurrentEdgeState = null
}

function ensureOsmPocInspector () {
  let panel = document.getElementById('osm-poc-inspector')
  if (panel) return panel
  panel = document.createElement('aside')
  panel.id = 'osm-poc-inspector'
  panel.className = `osm-poc-inspector panel${enabled.has('osm_poc') ? '' : ' hidden'}`
  panel.innerHTML = `
    <div class="osm-poc-head">
      <div><span class="osm-poc-badge">DIAG</span><strong>Legacy Overpass</strong></div>
      <button id="osm-poc-collapse" type="button" aria-label="POC-paneel inklappen">−</button>
    </div>
    <div id="osm-poc-content" class="osm-poc-content">
      <div id="osm-poc-status" class="osm-poc-status">Handmatige externe diagnose; normale OSM-wegen komen uit de lokale database.</div>
      <div id="osm-poc-detail"></div>
      <a class="osm-poc-attribution" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors, ODbL</a>
    </div>`
  document.body.appendChild(panel)
  panel.querySelector('#osm-poc-collapse').addEventListener('click', event => {
    const content = panel.querySelector('#osm-poc-content')
    const collapsed = content.classList.toggle('hidden')
    event.currentTarget.textContent = collapsed ? '+' : '−'
  })
  return panel
}

function setOsmPocStatus (message, state = '') {
  const el = ensureOsmPocInspector().querySelector('#osm-poc-status')
  el.className = `osm-poc-status ${state}`
  el.textContent = message
}

function renderOsmPocSummary (meta) {
  setOsmPocStatus(
    `${meta.directed_edge_count || 0} gerichte edges · ${meta.matched_count || 0}/${meta.measurement_count || 0} meetpunten gekoppeld${meta.osm_cache_hit ? ' · cache' : ''}`,
    'ready'
  )
  const detail = ensureOsmPocInspector().querySelector('#osm-poc-detail')
  detail.innerHTML = `
    <div class="osm-poc-summary-grid">
      <span>OSM ways<strong>${esc(meta.source_way_count ?? 0)}</strong></span>
      <span>Met snelheid<strong>${esc(meta.roads_with_measurements ?? 0)}</strong></span>
      <span>Ambigu<strong>${esc(meta.ambiguous_count ?? 0)}</strong></span>
      <span>Niet gekoppeld<strong>${esc(meta.unmatched_count ?? 0)}</strong></span>
    </div>
    <p class="osm-poc-help">Klik een weg voor de OSM-identiteit. Activeer GPS om dezelfde gerichte edge met heading en hysterese te matchen.</p>`
}

function renderOsmPocFeature (props, match, source) {
  const detail = ensureOsmPocInspector().querySelector('#osm-poc-detail')
  const speed = props.speed_kmh === null || props.speed_kmh === undefined
    ? 'geen meting'
    : `${Math.round(Number(props.speed_kmh))} km/h`
  detail.innerHTML = `
    <div class="osm-poc-mode">${esc(source || 'OSM-edge')}</div>
    <h3>${esc(props.ref || props.name || props.highway || 'Onbenoemde weg')}</h3>
    <dl class="osm-poc-dl">
      <dt>Interne edge</dt><dd>${esc(props.edge_id)}</dd>
      <dt>OSM way</dt><dd>${esc(props.osm_way_id)} · v${esc(props.osm_version ?? '?')}</dd>
      <dt>Richting</dt><dd>${esc(props.travel_direction)}${match ? ` · ${Math.round(match.bearing)}°` : ''}</dd>
      <dt>Type</dt><dd>${esc(props.highway)}</dd>
      <dt>Rijbaan</dt><dd>${esc(props.carriageway_ref || 'onbekend')}</dd>
      <dt>Rijstroken</dt><dd>${esc(props.lanes ?? 'onbekend')}</dd>
      <dt>Maxspeed</dt><dd>${esc(props.maxspeed || 'onbekend')}${props.maxspeed_conditional ? ` · ${esc(props.maxspeed_conditional)}` : ''}</dd>
      <dt>Live snelheid</dt><dd class="osm-poc-live">${esc(speed)}</dd>
      <dt>Meetlocaties</dt><dd>${esc(props.linked_site_count || 0)}${props.linked_site_ids ? ` · ${esc(props.linked_site_ids)}` : ''}</dd>
      ${match ? `<dt>Afstand</dt><dd>${match.distance.toFixed(1)} m</dd><dt>Heading Δ</dt><dd>${match.headingDelta.toFixed(1)}°</dd>` : ''}
    </dl>
    <details><summary>Alle OSM-tags</summary><pre>${esc(prettyOsmTags(props.osm_tags))}</pre></details>`
}

function renderOsmPocGpsMatch (candidate, confidence, retained) {
  setOsmPocStatus(
    `GPS-match ${Math.round(confidence * 100)}% confidence${retained ? ' · hysterese houdt vorige edge vast' : ''}`,
    confidence >= 0.5 ? 'ready' : 'warning'
  )
  renderOsmPocFeature(candidate.feature.properties, candidate, 'GPS-match')
}

function renderOsmPocMeasurement (props) {
  const detail = ensureOsmPocInspector().querySelector('#osm-poc-detail')
  detail.innerHTML = `
    <div class="osm-poc-mode">NDW-meetlocatie</div>
    <h3>${esc(props.road || props.site_id || 'Meetpunt')}</h3>
    <dl class="osm-poc-dl">
      <dt>Site</dt><dd>${esc(props.site_id)}</dd>
      <dt>Rijbaan</dt><dd>${esc(props.carriageway || props.side || 'onbekend')}</dd>
      <dt>Status</dt><dd>${esc(props.osm_match_status)}</dd>
      <dt>OSM-edge</dt><dd>${esc(props.osm_edge_id || 'niet gekoppeld')}</dd>
      <dt>Afstand</dt><dd>${props.osm_match_distance_m == null ? '—' : `${esc(props.osm_match_distance_m)} m`}</dd>
      <dt>Confidence</dt><dd>${props.osm_match_confidence == null ? '—' : `${Math.round(Number(props.osm_match_confidence) * 100)}%`}</dd>
    </dl>`
}

function renderOsmPocNoMatch (message) {
  setOsmPocStatus(message, 'warning')
}

function prettyOsmTags (raw) {
  try { return JSON.stringify(JSON.parse(raw || '{}'), null, 2) } catch { return raw || '{}' }
}
