'use strict'

// ─── Fetch ────────────────────────────────────────────────────────────────────

function fetchAll () {
  bboxTooLarge = false
  for (const layer of LAYERS) {
    if (!layerEnabled(layer)) continue
    fetchLayer(layer)
  }
  // Matrix/DRIPs also feed the GPS-relative HUD; refresh it while tracking.
  if (gpsState !== GPS_STATES.OFF) fetchRoadSignHud()
  else renderRoadSignHud()
}

function fetchLayer (layer) {
  if (layer.geomType === 'msi') { fetchMatrixSigns(); return }
  if (layer.geomType === 'speed') { fetchSpeedLanes(); return }
  if (layer.geomType === 'speed-points') { fetchSpeedPoints(); return }
  if (layer.geomType === 'hectometer-sign') { fetchHectometerSigns(); return }

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
  let url = `/api${layer.endpoint}${sep}bbox=${bbox}`
  if (layer.sendZoom) url += `&zoom=${map.getZoom().toFixed(2)}`
  if (layer.limit) url += `&limit=${layer.limit}`

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

// Measurement sources are points, while the matched OSM way they drive can
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
