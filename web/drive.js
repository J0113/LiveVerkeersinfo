'use strict'

// ─── Drive HUD ──────────────────────────────────────────────────────────────────
// Mobile driving view: from live GPS position + heading, show matrix signs, DRIPs
// and per-lane measured speed for the road AHEAD in the travel direction only.
// Frontend-only; reuses /api/signs/matrix, /api/signs/drips, /api/traffic/speed
// and the shared helpers in lib.js.

// ─── State ───────────────────────────────────────────────────────────────────────

let watchId = null
let wakeLock = null
let userCoords = null      // [lng, lat]
let prevCoords = null
let speedMps = null
const headingHistory = []  // recent reliable headings (deg) for smoothing
let smoothedHeading = null

const cache = { matrix: null, drips: null, speed: null }
let lastFetchCoords = null
let lastFetchAt = 0
let fetching = false

const REFETCH_DIST_M = 300
const REFETCH_MS = 20000

// ─── GPS lifecycle ────────────────────────────────────────────────────────────────

const gpsBtn = document.getElementById('drive-gps-btn')
gpsBtn.addEventListener('click', () => {
  if (watchId === null) startGPS()
  else stopGPS()
})

function startGPS () {
  if (!navigator.geolocation) {
    showBanner('Geolocation niet ondersteund door deze browser.')
    return
  }
  gpsBtn.classList.remove('state-off')
  gpsBtn.classList.add('state-on')
  requestWakeLock()
  watchId = navigator.geolocation.watchPosition(onPosition, onPositionError, {
    enableHighAccuracy: true,
    timeout: 12000,
    maximumAge: 0,
  })
}

function stopGPS () {
  if (watchId !== null) {
    navigator.geolocation.clearWatch(watchId)
    watchId = null
  }
  releaseWakeLock()
  gpsBtn.classList.remove('state-on')
  gpsBtn.classList.add('state-off')
}

function onPositionError (err) {
  console.warn('[drive/geo]', err.message)
  showBanner('GPS fout: ' + err.message)
}

function onPosition (pos) {
  const { latitude, longitude, heading, speed } = pos.coords
  prevCoords = userCoords
  userCoords = [longitude, latitude]
  speedMps = (speed !== null && !isNaN(speed)) ? speed : null

  // Heading: prefer device heading when moving; else derive from movement (>2m).
  let sample = null
  if (heading !== null && !isNaN(heading) && (speedMps === null || speedMps > 1.5)) {
    sample = heading
  } else if (prevCoords) {
    const moved = calculateDistance(prevCoords, userCoords)
    if (moved > 2) sample = calculateBearing(prevCoords, userCoords)
  }
  if (sample !== null) {
    headingHistory.push(sample)
    if (headingHistory.length > 4) headingHistory.shift()
    smoothedHeading = circularMean(headingHistory)
  }

  updateReadout()
  maybeRefetch()
  render()
}

function circularMean (degs) {
  if (!degs.length) return null
  let x = 0, y = 0
  for (const d of degs) { x += Math.cos(d * Math.PI / 180); y += Math.sin(d * Math.PI / 180) }
  if (x === 0 && y === 0) return null
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360
}

// ─── Fetch (throttled, forward-biased bbox) ────────────────────────────────────────

function maybeRefetch () {
  if (!userCoords || fetching) return
  const moved = lastFetchCoords ? calculateDistance(lastFetchCoords, userCoords) : Infinity
  const elapsed = Date.now() - lastFetchAt
  if (moved < REFETCH_DIST_M && elapsed < REFETCH_MS) return

  fetching = true
  lastFetchCoords = userCoords
  lastFetchAt = Date.now()
  const bbox = forwardBiasedBbox(userCoords, smoothedHeading)

  Promise.all([
    fetchJson(`/api/signs/matrix?bbox=${bbox}`),
    fetchJson(`/api/signs/drips?bbox=${bbox}`),
    fetchJson(`/api/traffic/speed?bbox=${bbox}`),
  ]).then(([m, d, s]) => {
    if (m) cache.matrix = m
    if (d) cache.drips = d
    if (s) cache.speed = s
    render()
  }).finally(() => { fetching = false })
}

function fetchJson (url) {
  return fetch(url)
    .then(r => r.ok ? r.json() : null)
    .catch(e => { console.warn('[drive/fetch]', url, e.message); return null })
}

// ─── Direction filtering ────────────────────────────────────────────────────────────

// Build {device, directed} or null if position unknown.
function driveContext () {
  if (!userCoords) return null
  const directed = smoothedHeading !== null
  return {
    device: { coords: userCoords, heading: smoothedHeading ?? 0 },
    directed,
  }
}

// Group matrix lane features into physical gantries.
function buildGantries (fc) {
  const gantries = new Map()
  for (const f of (fc?.features || [])) {
    if (!f.geometry) continue
    const p = f.properties
    const key = `${p.road ?? ''}|${p.km ?? ''}|${p.carriageway ?? ''}`
    if (!gantries.has(key)) {
      gantries.set(key, { coords: f.geometry.coordinates, bearing: p.bearing, road: p.road, km: p.km, carriageway: p.carriageway, lanes: [] })
    }
    gantries.get(key).lanes.push(p)
  }
  for (const g of gantries.values()) g.lanes.sort((a, b) => (a.lane ?? 0) - (b.lane ?? 0))
  return [...gantries.values()]
}

// Classify one item {coords, bearing}; returns null or {status, along, dist}.
function classify (ctx, coords, bearing) {
  if (ctx.directed) {
    return classifyFeature(ctx.device, coords, bearing, { directed: true })
  }
  // Degraded (no heading yet): distance-only, both directions, no ahead/passed.
  const dist = calculateDistance(ctx.device.coords, coords)
  if (dist > 2000) return null
  return { status: 'near', along: dist, dist }
}

// ─── Render ─────────────────────────────────────────────────────────────────────────

function render () {
  const ctx = driveContext()
  if (!ctx) return

  showBanner(ctx.directed ? null : 'Richting onbekend — beide richtingen getoond. Ga rijden voor filtering.')

  // Gather classified items.
  const gantries = buildGantries(cache.matrix)
    .map(g => ({ kind: 'msi', data: g, cls: classify(ctx, g.coords, g.bearing) }))
    .filter(x => x.cls)

  const drips = (cache.drips?.features || [])
    .filter(f => f.geometry && f.properties.image_b64)
    .map(f => ({ kind: 'drip', data: f.properties, coords: f.geometry.coordinates, cls: classify(ctx, f.geometry.coordinates, f.properties.bearing) }))
    .filter(x => x.cls)

  const speeds = (cache.speed?.features || [])
    .filter(f => f.geometry && (f.properties.lanes || []).length)
    .map(f => ({ data: f.properties, coords: f.geometry.coordinates, cls: classify(ctx, f.geometry.coordinates, f.properties.bearing) }))
    .filter(x => x.cls)

  renderSpeed(speeds, ctx)
  renderUpcoming([...gantries, ...drips], ctx)
  renderPassed([...gantries, ...drips], ctx)
}

function renderSpeed (speeds, ctx) {
  const sec = document.getElementById('hud-speed')
  const strip = document.getElementById('hud-speed-lanes')
  const meta = document.getElementById('hud-speed-meta')

  // Nearest ahead (directed) or nearest overall.
  const ahead = speeds.filter(s => s.cls.status !== 'passed')
  const pool = ahead.length ? ahead : speeds
  pool.sort((a, b) => a.cls.dist - b.cls.dist)
  const pick = pool[0]
  if (!pick) { sec.classList.add('hidden'); return }
  sec.classList.remove('hidden')

  strip.innerHTML = ''
  for (const lane of pick.data.lanes) {
    const tile = document.createElement('div')
    tile.className = 'speed-tile'
    const kmh = lane.speed_kmh
    tile.style.background = speedColor(kmh)
    tile.style.color = speedTextColor(kmh)
    tile.innerHTML = `<b>${kmh !== null && kmh !== undefined ? Math.round(kmh) : '?'}</b>`
    strip.appendChild(tile)
  }
  const dist = ctx.directed ? `in ${formatDistance(pick.cls.along)}` : `${formatDistance(pick.cls.dist)}`
  const age = formatAge(pick.data.measured_at)
  meta.textContent = `${pick.data.systems?.join('+') || ''} · ${dist} · ${age}`
}

function renderUpcoming (items, ctx) {
  const list = document.getElementById('hud-upcoming-list')
  const empty = document.getElementById('hud-upcoming-empty')
  const ahead = items.filter(x => x.cls.status !== 'passed')
  ahead.sort((a, b) => a.cls.along - b.cls.along)

  list.innerHTML = ''
  for (const item of ahead) list.appendChild(makeCard(item, ctx, false))
  empty.classList.toggle('hidden', ahead.length > 0)
}

function renderPassed (items, ctx) {
  const sec = document.getElementById('hud-passed')
  const list = document.getElementById('hud-passed-list')
  if (!ctx.directed) { sec.classList.add('hidden'); return }
  const passed = items.filter(x => x.cls.status === 'passed')
  passed.sort((a, b) => Math.abs(a.cls.along) - Math.abs(b.cls.along))

  list.innerHTML = ''
  for (const item of passed.slice(0, 6)) list.appendChild(makeCard(item, ctx, true))
  sec.classList.toggle('hidden', passed.length === 0)
}

function makeCard (item, ctx, passed) {
  const card = document.createElement('div')
  card.className = 'hud-card' + (passed ? ' passed' : '')

  const head = document.createElement('div')
  head.className = 'hud-card-head'
  const distM = ctx.directed ? Math.abs(item.cls.along) : item.cls.dist
  const distTxt = passed ? `${formatDistance(distM)} terug` : (ctx.directed ? `in ${formatDistance(distM)}` : formatDistance(distM))

  if (item.kind === 'msi') {
    const g = item.data
    head.innerHTML = `<span class="hud-road">${esc(g.road || 'Matrix')}</span><span class="hud-dist">${distTxt}</span>`
    card.appendChild(head)
    const gantry = document.createElement('div')
    gantry.className = 'msi-gantry'
    for (const lane of g.lanes) gantry.appendChild(buildMsiLane(lane))
    const wrap = document.createElement('div')
    wrap.className = 'hud-gantry-wrap'
    wrap.appendChild(gantry)
    card.appendChild(wrap)
  } else { // drip
    const d = item.data
    head.innerHTML = `<span class="hud-road">DRIP${d.description ? ' · ' + esc(d.description) : ''}</span><span class="hud-dist">${distTxt}</span>`
    card.appendChild(head)
    const img = document.createElement('img')
    img.className = 'hud-drip-img'
    img.src = `data:image/${esc(d.image_format || 'png')};base64,${d.image_b64}`
    card.appendChild(img)
  }
  return card
}

// ─── UI helpers ────────────────────────────────────────────────────────────────────

function updateReadout () {
  const el = document.getElementById('drive-kmh')
  el.textContent = speedMps !== null ? Math.round(speedMps * 3.6) : '–'
}

function showBanner (msg) {
  const b = document.getElementById('drive-banner')
  if (!msg) { b.classList.add('hidden'); return }
  b.textContent = msg
  b.classList.remove('hidden')
}

// ─── Wake lock ───────────────────────────────────────────────────────────────────

function requestWakeLock () {
  if (!('wakeLock' in navigator)) return
  navigator.wakeLock.request('screen')
    .then(lock => {
      wakeLock = lock
      document.getElementById('drive-wake').classList.add('active')
      lock.addEventListener('release', () => document.getElementById('drive-wake').classList.remove('active'))
    })
    .catch(e => console.warn('[drive/wakeLock]', e.message))
}

function releaseWakeLock () {
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null }
  document.getElementById('drive-wake').classList.remove('active')
}

// Re-acquire wake lock after returning to the tab (browsers drop it on background).
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && watchId !== null && !wakeLock) requestWakeLock()
})
