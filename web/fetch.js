'use strict'

// ─── Fetch ────────────────────────────────────────────────────────────────────

function fetchAll () {
  bboxTooLarge = false
  for (const layer of LAYERS) {
    if (!enabled.has(layer.key)) continue
    fetchLayer(layer)
  }
  // Matrix/DRIPs also feed the GPS-relative HUD; refresh it while tracking.
  if (gpsState !== GPS_STATES.OFF) fetchRoadSignHud()
  else renderRoadSignHud()
}

function fetchLayer (layer) {
  if (layer.geomType === 'osm-poc') { fetchOsmPoc(layer); return }
  if (layer.geomType === 'msi') { fetchMatrixSigns(); return }
  if (layer.geomType === 'speed') { fetchSpeedLanes(); return }
  if (layer.geomType === 'speed-points') { fetchSpeedPoints(); return }
  if (layer.geomType === 'road-network') { fetchNwbRoads(layer); return }
  if (layer.geomType === 'local-osm-roads') { fetchLocalOsmRoads(layer); return }

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

// Local OSM viewport rendering is independent from GPS map matching. It only
// runs through fetchAll (initial load, user moveend, 60 s refresh), never for an
// individual GPS fix; driving fixes use /roads/corridor in road-match.js.
function fetchLocalOsmRoads (layer) {
  if (map.getZoom() < layer.minZoom) {
    controllers[layer.key]?.abort()
    map.getSource(layer.key)?.setData(EMPTY_FC)
    osmRoadsTruncated = false
    updateZoomHint()
    return
  }

  controllers[layer.key]?.abort()
  const ctrl = new AbortController()
  controllers[layer.key] = ctrl
  const bbox = viewportBbox()
  const cached = osmRoadCache.get(bbox)
  if (cached && cached.expires > Date.now()) {
    renderLocalOsmRoads(layer, cached.data)
    return
  }

  fetch(`/api${layer.endpoint}?bbox=${bbox}`, { signal: ctrl.signal })
    .then(async response => {
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${response.status}`)
      }
      return response.json()
    })
    .then(data => {
      osmRoadCache.set(bbox, { expires: Date.now() + OSM_ROAD_BROWSER_CACHE_TTL_MS, data })
      if (osmRoadCache.size > 30) osmRoadCache.delete(osmRoadCache.keys().next().value)
      renderLocalOsmRoads(layer, data)
    })
    .catch(error => {
      if (error.name === 'AbortError') return
      console.warn('[osm_roads]', error.message)
    })
}

function renderLocalOsmRoads (layer, data) {
  if (!enabled.has(layer.key)) return
  const rendered = typeof CanonicalSegmentState === 'undefined'
    ? data
    : CanonicalSegmentState.enrichFeatureCollection(data)
  map.getSource(layer.key)?.setData(rendered)
  if (layer.promoteId) reapplySelection(layer.key)
  osmRoadsTruncated = Boolean(data?.metadata?.truncated)
  updateZoomHint()
}

function refreshLocalOsmRoadStateForMeasurements (points) {
  const layer = LAYERS.find(item => item.key === 'osm_roads')
  if (!layer || !enabled.has(layer.key) || typeof CanonicalSegmentState === 'undefined') return
  const bbox = viewportBbox()
  const cached = osmRoadCache.get(bbox)
  if (!cached) return
  const newerSegments = CanonicalSegmentState.newerPointSegments(points, cached.data)
  if (!newerSegments.length) return

  // Geometry and live state currently share one lightweight viewport response.
  // Invalidate only the current viewport when its accepted source observations
  // are newer; unrelated cached viewports remain reusable.
  osmRoadCache.delete(bbox)
  fetchLocalOsmRoads(layer)
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
