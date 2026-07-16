'use strict'

// ─── Drive HUD: linger / hold ────────────────────────────────────────────────
// When a channel briefly has nothing ahead (gap between sensors/gantries), keep
// the last selection on screen for a grace period instead of flickering off.
const HUD_LINGER_MS = { speed: 5000, matrix: 5000, drip: 10000 }
const roadSignHudHold = { speed: null, matrix: null, drip: null } // { data, expiresAt }
let roadSignHudHoldTimer = null

// Return the data to actually render for a channel: the fresh selection when
// present (refreshing its hold), else the previously held selection while still
// inside the linger window, else null (expired → clear).
function holdSelection (channel, current) {
  const now = Date.now()
  if (current) {
    roadSignHudHold[channel] = { data: current, expiresAt: now + HUD_LINGER_MS[channel] }
    return current
  }
  const held = roadSignHudHold[channel]
  if (held && now < held.expiresAt) return held.data
  roadSignHudHold[channel] = null
  return null
}

// Re-render when the nearest hold expires, so a lingering tile clears even if no
// GPS update arrives (e.g. stopped) to drive the loop.
function scheduleHudHoldClear () {
  if (roadSignHudHoldTimer) { clearTimeout(roadSignHudHoldTimer); roadSignHudHoldTimer = null }
  const now = Date.now()
  let next = Infinity
  for (const ch of ['speed', 'matrix', 'drip']) {
    const h = roadSignHudHold[ch]
    if (h && h.expiresAt > now) next = Math.min(next, h.expiresAt)
  }
  if (next !== Infinity) {
    roadSignHudHoldTimer = setTimeout(() => { roadSignHudHoldTimer = null; renderRoadSignHud() }, next - now + 20)
  }
}

function resetHudHolds () {
  roadSignHudHold.speed = null
  roadSignHudHold.matrix = null
  roadSignHudHold.drip = null
  if (roadSignHudHoldTimer) { clearTimeout(roadSignHudHoldTimer); roadSignHudHoldTimer = null }
}

// ─── Drive HUD: "last updated" relative time ─────────────────────────────────
// Last-update ISO per channel; a slow ticker re-renders the label text so the
// relative age stays fresh even between selections.
const roadSignHudTimes = { speed: null, matrix: null, drip: null }
let roadSignHudTimeTimer = null

function setHudUpdated (channel, iso) {
  roadSignHudTimes[channel] = iso || null
  paintHudUpdated(channel)
}

function paintHudUpdated (channel) {
  const el = document.getElementById(`road-sign-hud-${channel}-updated`)
  if (!el) return
  const txt = formatAgeNl(roadSignHudTimes[channel])
  setTextIfChanged(el, txt)
  el.classList.toggle('hidden', !txt)
}

function startHudTimeTicker () {
  if (roadSignHudTimeTimer) return
  roadSignHudTimeTimer = setInterval(() => {
    paintHudUpdated('speed'); paintHudUpdated('matrix'); paintHudUpdated('drip')
  }, 10000)
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
    side: 400
  })
  const requests = []
  const canonicalSigns = typeof roadMatchCanonicalRoadSigns === 'function'
    ? roadMatchCanonicalRoadSigns()
    : null
  if (!canonicalSigns && userHeading !== null && hudEnabled.has('hud_matrix')) requests.push(fetchRoadSignHudSource('matrix', bbox, ctrl.signal))
  else roadSignHudCache.matrix = EMPTY_FC
  if (!canonicalSigns && userHeading !== null && hudEnabled.has('hud_drips')) requests.push(fetchRoadSignHudSource('drips', bbox, ctrl.signal))
  else roadSignHudCache.drips = EMPTY_FC
  // The production driving matcher supplies fail-closed segment/lane state.
  // Do not run the legacy nearest-point lane picker alongside it: that fallback
  // cannot distinguish every opposite carriageway when source bearing is absent.
  if (!canonicalLanePipelineAvailable() && hudEnabled.has('hud_speed')) {
    requests.push(fetchRoadSignHudSpeedSource(speedBbox, ctrl.signal))
  } else {
    roadSignHudCache.speedPoints = EMPTY_FC
  }

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
  const imageParam = source === 'drips' ? '&include_image=true' : ''
  return fetch(`/api/signs/${source}?bbox=${bbox}&limit=${limit}${imageParam}`, { signal })
    .then(response => {
      if (!response.ok) throw new Error(`${source}: HTTP ${response.status}`)
      return response.json()
    })
    .then(fc => { roadSignHudCache[source] = fc })
}

function renderRoadSignHud () {
  if (gpsState === GPS_STATES.OFF || !userCoords) {
    resetHudHolds()
    renderRoadSignHudSelection({ matrix: null, drip: null, speed: null, gpsKmh: null })
    return
  }

  const canonical = typeof roadMatchCanonicalRoadSigns === 'function'
    ? roadMatchCanonicalRoadSigns()
    : null
  const selected = canonical || (userHeading === null
    ? { matrix: null, drip: null }
    : selectUpcomingRoadSigns(
        hudEnabled.has('hud_matrix') ? roadSignHudCache.matrix : EMPTY_FC,
        hudEnabled.has('hud_drips') ? roadSignHudCache.drips : EMPTY_FC,
        { coords: userCoords, heading: userHeading },
        ROAD_SIGN_HUD_MAX_DISTANCE_M
      ))

  selected.gpsKmh = Number.isFinite(userSpeedMps) ? userSpeedMps * 3.6 : null
  selected.upcoming = (canonicalLanePipelineAvailable() || userHeading === null ||
    !hudEnabled.has('hud_speed'))
    ? null
    : selectUpcomingLaneSpeeds(roadSignHudCache.speedPoints, { coords: userCoords, heading: userHeading }, 2500)

  // Keep a just-passed selection on screen briefly instead of flickering off in
  // the gap before the next one. Disabled channels hold null (cleared instantly).
  // Canonical state is authoritative and already validity-filtered. Do not
  // linger a legacy nearest-point sign after the path says no fact applies.
  if (canonical) {
    roadSignHudHold.matrix = null
    roadSignHudHold.drip = null
    selected.matrix = hudEnabled.has('hud_matrix') ? selected.matrix : null
    selected.drip = hudEnabled.has('hud_drips') ? selected.drip : null
  } else {
    selected.matrix = holdSelection('matrix', hudEnabled.has('hud_matrix') ? selected.matrix : null)
    selected.drip = holdSelection('drip', hudEnabled.has('hud_drips') ? selected.drip : null)
  }
  selected.upcoming = holdSelection('speed', hudEnabled.has('hud_speed') ? selected.upcoming : null)
  scheduleHudHoldClear()
  startHudTimeTicker()

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
  const speedVisible = gpsState !== GPS_STATES.OFF && hudEnabled.has('hud_speed') &&
    !canonicalLanePipelineAvailable()
  const visibleCount = [speedVisible, selected.matrix, selected.drip].filter(Boolean).length
  const visible = visibleCount > 0
  speedTile.classList.toggle('hidden', !speedVisible)
  hud.classList.remove('road-sign-hud-count-1', 'road-sign-hud-count-2', 'road-sign-hud-count-3')
  document.body.classList.remove('road-sign-hud-count-1', 'road-sign-hud-count-2', 'road-sign-hud-count-3')
  if (visible) hud.classList.add(`road-sign-hud-count-${visibleCount}`)
  if (visible) document.body.classList.add(`road-sign-hud-count-${visibleCount}`)
  hud.classList.toggle('hidden', !visible)
  document.body.classList.toggle('road-sign-hud-visible', visible)
  // First matrix build measures 0 width while the tile is hidden; refit once the
  // HUD is shown and laid out.
  if (visible && selected.matrix) requestAnimationFrame(fitMatrixLanes)
}

function canonicalLanePipelineAvailable () {
  return typeof LaneTopology !== 'undefined' && typeof roadMatchAccepted !== 'undefined'
}

function renderSpeedHudTile (upcoming) {
  const laneLabel = document.getElementById('road-sign-hud-speed-lane')
  const distance = document.getElementById('road-sign-hud-speed-distance')
  const road = document.getElementById('road-sign-hud-speed-road')
  if (!laneLabel || !distance || !road) return

  setHudUpdated('speed', upcoming ? upcoming.data.measured_at : null)

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
    setHudUpdated('matrix', null)
    if (roadSignHudRenderState.matrixKey !== null) {
      lanes.replaceChildren()
      roadSignHudRenderState.matrixKey = null
    }
    return
  }
  tile.classList.remove('hidden')

  const gantry = selection.data
  setHudUpdated('matrix', gantry.lanes.reduce(
    (mx, l) => (l.ts_state && (!mx || l.ts_state > mx)) ? l.ts_state : mx, null))
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
  const track = document.createElement('div')
  track.className = 'road-sign-hud-lanes-track'
  for (const lane of gantry.lanes) {
    const column = document.createElement('div')
    column.className = 'road-sign-hud-lane'
    const label = document.createElement('span')
    label.className = 'road-sign-hud-lane-label'
    label.textContent = `Rijstrook ${lane.lane ?? '?'}`
    column.append(label, buildMsiLane(lane))
    track.appendChild(column)
  }
  lanes.appendChild(track)
  roadSignHudRenderState.matrixKey = matrixKey
  fitMatrixLanes()
}

// Scale the lane row down so wide gantries (4+ lanes) fit the fixed-width matrix
// tile. Measures once laid out; skips while the tile is hidden (clientWidth 0).
function fitMatrixLanes () {
  const container = document.getElementById('road-sign-hud-lanes')
  const track = container?.firstElementChild
  if (!track) return
  const avail = container.clientWidth
  if (!avail) return
  track.style.transform = 'scale(1)'
  const natural = track.scrollWidth
  const scale = natural > avail ? avail / natural : 1
  track.style.transform = `scale(${scale})`
}

function renderDripHudTile (selection) {
  const tile = document.getElementById('road-sign-hud-drip')
  const image = document.getElementById('road-sign-hud-drip-image')
  const text = document.getElementById('road-sign-hud-drip-text')
  if (!selection) {
    tile.classList.add('hidden')
    setHudUpdated('drip', null)
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
  setHudUpdated('drip', data.updated_at)
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
  resetHudHolds()
  roadSignHudCache.matrix = EMPTY_FC
  roadSignHudCache.drips = EMPTY_FC
  roadSignHudCache.speedPoints = EMPTY_FC
  roadSignHudLastFetchCoords = null
  roadSignHudLastFetchAt = 0
  roadSignHudLastFetchHeading = null
  renderRoadSignHud()
}
