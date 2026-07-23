'use strict'

// ─── Hectometer sign HTML markers (map render) ───────────────────────────────
//
// One marker per hectometer_point row (see ingest/hectometer.py): a green
// NL-style sign with a red (A-road) or yellow (N-road) shield, the
// carriageway (Re/Li) and the km value. Upright at all times — unlike MSI
// gantries these don't need a road-bearing rotation to read correctly.

function fetchHectometerSigns () {
  controllers['hectometers']?.abort()
  const ctrl = new AbortController()
  controllers['hectometers'] = ctrl

  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(v => v.toFixed(6)).join(',')

  fetch(`/api/hectometers?bbox=${bbox}`, { signal: ctrl.signal })
    .then(r => {
      if (r.status === 400) return r.json().then(body => Promise.reject(Object.assign(new Error(body.detail || 'Bad Request'), { isBboxError: /bbox area/i.test(body.detail || '') })))
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(fc => {
      setBboxTooLargeHint(false)
      renderHectometerMarkers(fc)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn('[hectometers]', e.message)
    })
}

function _hectoShieldClass (road) {
  if (!road) return 'hecto-shield-other'
  if (road.startsWith('A')) return 'hecto-shield-a'
  if (road.startsWith('N')) return 'hecto-shield-n'
  return 'hecto-shield-other'
}

function renderHectometerMarkers (fc) {
  for (const m of hectometerMarkers) m.remove()
  hectometerMarkers = []

  if (!enabled.has('hectometers')) return

  for (const f of fc.features) {
    if (!f.geometry) continue
    const p = f.properties

    const el = document.createElement('div')
    el.className = 'hecto-sign'

    const shield = document.createElement('span')
    shield.className = `hecto-shield ${_hectoShieldClass(p.road)}`
    shield.textContent = p.road ?? ''

    const cw = document.createElement('span')
    cw.className = 'hecto-carriageway'
    cw.textContent = p.carriageway ?? ''

    const km = document.createElement('span')
    km.className = 'hecto-km'
    km.textContent = p.km != null ? String(p.km).replace('.', ',') : ''

    el.append(shield, cw, km)

    const marker = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat(f.geometry.coordinates)
      .addTo(map)
    hectometerMarkers.push(marker)
  }
}
