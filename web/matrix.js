'use strict'

// ─── Matrix sign HTML markers (map render) ───────────────────────────────────

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

    // The national feed also contains fully blank/off gantries. Rendering
    // those produces large black rectangles without actionable information,
    // especially in the navigation view. Keep blank lanes only when another
    // lane on the same physical gantry carries an active signal.
    if (!gantry.lanes.some(matrixLaneHasValue)) continue

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
