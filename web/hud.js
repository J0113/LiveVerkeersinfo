'use strict'

// ─── Drive HUD: linger / hold ────────────────────────────────────────────────
// When a channel briefly has nothing ahead (gap between sensors/gantries), keep
// the last selection on screen for a grace period instead of flickering off.
const HUD_LINGER_MS = { speed: 5000, speedList: 5000, matrix: 5000, drip: 10000, traject: 4000 }
const roadSignHudHold = { speed: null, speedList: null, matrix: null, drip: null, traject: null } // { data, expiresAt }
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
  for (const ch of ['speed', 'speedList', 'matrix', 'drip', 'traject']) {
    const h = roadSignHudHold[ch]
    if (h && h.expiresAt > now) next = Math.min(next, h.expiresAt)
  }
  if (next !== Infinity) {
    roadSignHudHoldTimer = setTimeout(() => { roadSignHudHoldTimer = null; renderRoadSignHud() }, next - now + 20)
  }
}

function resetHudHolds () {
  roadSignHudHold.speed = null
  roadSignHudHold.speedList = null
  roadSignHudHold.matrix = null
  roadSignHudHold.drip = null
  roadSignHudHold.traject = null
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
    // Lane geometry around the vehicle — current-road map-matching and OSM
    // name/limit enrichment. The upcoming-sensor pool comes from the
    // road+carriageway+hectometre fetch, so this no longer has to reach as far
    // ahead as the sidebar horizon.
    ahead: 1500,
    behind: 500,
    side: 400
  })
  const currentRoadBbox = forwardBiasedBbox(userCoords, userHeading, {
    ahead: 150,
    behind: 100,
    side: 100
  })
  const requests = []
  if (userHeading !== null && hudEnabled.has('hud_matrix')) requests.push(fetchRoadSignHudSource('matrix', bbox, ctrl.signal))
  else roadSignHudCache.matrix = EMPTY_FC
  if (userHeading !== null && hudEnabled.has('hud_drips')) requests.push(fetchRoadSignHudSource('drips', bbox, ctrl.signal))
  else roadSignHudCache.drips = EMPTY_FC
  if (hudEnabled.has('hud_speed') || hudEnabled.has('hud_speed_sidebar')) {
    requests.push(fetchRoadSignHudSpeedSource(speedBbox, currentRoadBbox, ctrl.signal))
  } else {
    roadSignHudCache.speedLanes = EMPTY_FC
    requests.push(fetchRoadSignHudCurrentRoadSource(currentRoadBbox, ctrl.signal))
  }
  requests.push(fetchTrajectPairsSource(currentRoadBbox, ctrl.signal))

  Promise.allSettled(requests).then(results => {
    for (const result of results) {
      if (result.status === 'rejected' && result.reason?.name !== 'AbortError') {
        console.warn('[road-sign-hud]', result.reason?.message || result.reason)
      }
    }
    if (!ctrl.signal.aborted) renderRoadSignHud()
  })
}

async function fetchRoadSignHudSpeedSource (bbox, currentRoadBbox, signal) {
  let speedError = null
  try {
    const response = await fetch(`/api/traffic/speed/map?bbox=${bbox}&include_lanes=true&limit=500`, { signal })
    if (!response.ok) throw new Error(`speed: HTTP ${response.status}`)
    const data = await response.json()
    roadSignHudCache.speedLanes = data.lanes || EMPTY_FC
  } catch (error) {
    if (error.name === 'AbortError') throw error
    speedError = error
    roadSignHudCache.speedLanes = EMPTY_FC
  }

  const current = selectCurrentOsmLane(
    roadSignHudCache.speedLanes,
    { coords: userCoords, heading: userHeading },
    roadSignHudCurrentRoad
  )
  if (current) {
    roadSignHudCache.osmLanes = EMPTY_FC
  } else {
    try {
      await fetchRoadSignHudCurrentRoadSource(currentRoadBbox, signal)
    } catch (error) {
      if (error.name === 'AbortError') throw error
      if (!speedError) throw error
    }
  }
  if (speedError) throw speedError
}

// Resolve which carriageway of `road` we are on, and our hectometre on it.
// Refetched on a road change, a direction change, or after moving/waiting far
// enough that the anchor would drift.
function fetchRoadContextIfDue (road, coords, heading) {
  const now = Date.now()
  const previous = roadContextFetch
  const headingDelta = heading !== null && previous.heading !== null
    ? Math.abs(angleDiff(heading, previous.heading))
    : null
  const headingChanged = heading !== null && (
    previous.heading === null ||
    headingDelta >= ROAD_CONTEXT_REFETCH_HEADING_DEG
  )
  const headingReversed = headingDelta !== null &&
    headingDelta >= ROAD_CONTEXT_INVALIDATE_HEADING_DEG
  const moved = previous.coords ? calculateDistance(previous.coords, coords) : Infinity
  const due = previous.road !== road ||
    headingChanged ||
    moved >= ROAD_CONTEXT_REFETCH_DISTANCE_M ||
    now - previous.at >= ROAD_CONTEXT_REFETCH_MS
  if (!due) return

  // A road change or clear reversal invalidates the resolved carriageway
  // immediately. An ordinary bend still triggers a refresh, but retains the
  // current context until the response confirms whether its key changed.
  if (previous.road !== road || headingReversed) invalidateRoadContext()

  const generation = roadContextFetch.generation + 1
  roadContextFetch = { road, at: now, coords: [...coords], heading, generation }

  controllers['road-context']?.abort()
  const ctrl = new AbortController()
  controllers['road-context'] = ctrl

  const params = new URLSearchParams({
    road,
    lon: String(coords[0]),
    lat: String(coords[1]),
  })
  if (heading !== null) params.set('heading', String(((Math.round(heading) % 360) + 360) % 360))

  fetch(`/api/traffic/road-context?${params}`, { signal: ctrl.signal })
    .then(response => {
      if (!response.ok) throw new Error(`road context: HTTP ${response.status}`)
      return response.json()
    })
    .then(data => {
      // Fence a slow response from a superseded request: by the time it lands
      // we may already be on another road or the opposite carriageway.
      if (generation !== roadContextFetch.generation) return
      roadContext = {
        road: data.road || road,
        carriageway: data.carriageway || null,
        anchorKm: Number.isFinite(data.anchor_km) ? data.anchor_km : null,
        anchorDistanceM: Number.isFinite(data.anchor_distance_m) ? data.anchor_distance_m : null,
        coords: [...coords],
        at: Date.now(),
      }
      renderRoadSignHud()
    })
    .catch(error => {
      if (error.name === 'AbortError') return
      console.warn('[road-sign-hud] road context', error.message || error)
    })
}

function invalidateRoadContext () {
  roadContext = null
  roadSignHudCache.speedPointsRoad = EMPTY_FC
  roadScopedSpeedFetch = {
    attemptedKey: null,
    attemptedAt: 0,
    loadedKey: null,
    generation: roadScopedSpeedFetch.generation + 1,
  }
  controllers['road-scoped-speed']?.abort()
}

// The current road context, or null when it isn't (yet) resolved well enough to
// show anything: `road` + `carriageway` name the road we are on and scope the
// sensor pool, which is the minimum both speed displays need.
// `anchorUsable` says whether there is also a hectometre anchor close enough to
// place sensors *along* the road. Without one, selection falls back to
// projecting sensors onto our heading — worse round bends, but the provincial
// network has no hectometrering to offer in the first place.
function buildRoadContext (road) {
  if (!road || !roadContext || roadContext.road !== road || !roadContext.carriageway) return null
  if (Date.now() - roadContext.at > ROAD_CONTEXT_MAX_AGE_MS) return null
  const anchorUsable = Number.isFinite(roadContext.anchorKm) &&
    Number.isFinite(roadContext.anchorDistanceM) &&
    roadContext.anchorDistanceM <= ROAD_CONTEXT_MAX_ANCHOR_DISTANCE_M
  return {
    road,
    carriageway: roadContext.carriageway,
    anchorKm: anchorUsable ? roadContext.anchorKm : null,
    anchorDistanceM: roadContext.anchorDistanceM,
    coords: roadContext.coords,
    anchorUsable,
  }
}

function roadScopedSpeedKey (context) {
  return `${context.road}|${context.carriageway}`
}

// Square of `radiusM` around a position, as the API's bbox string.
function radiusBbox (coords, radiusM) {
  const [lon, lat] = coords
  const dLat = radiusM / 110540
  const dLon = radiusM / (111320 * Math.cos((lat * Math.PI) / 180))
  return [lon - dLon, lat - dLat, lon + dLon, lat + dLat]
    .map(value => value.toFixed(6))
    .join(',')
}

// Fetch every speed sensor on our carriageway within a hectometre window ahead.
// Scoping by km_min/km_max instead of a forward bbox is what makes the selection
// curve-proof: hectometrering follows the road, a bbox does not.
// Debounced: immediate on a road/carriageway change, otherwise no more than once
// per normal HUD refetch cycle.
function fetchRoadScopedSpeedIfDue (context, coords) {
  const anchorKm = roadContextAnchorKm(context, coords)
  const sign = carriagewayKmSign(context.carriageway)

  const key = roadScopedSpeedKey(context)
  const now = Date.now()
  const changed = roadScopedSpeedFetch.attemptedKey !== key
  if (!changed && now - roadScopedSpeedFetch.attemptedAt < ROAD_SCOPED_SPEED_REFETCH_MS) return
  if (changed) {
    // Never let the previous carriageway's sensors render against the new key.
    roadSignHudCache.speedPointsRoad = EMPTY_FC
    roadScopedSpeedFetch.loadedKey = null
  }
  const generation = roadScopedSpeedFetch.generation + 1
  roadScopedSpeedFetch = {
    attemptedKey: key,
    attemptedAt: now,
    loadedKey: roadScopedSpeedFetch.loadedKey,
    generation,
  }

  controllers['road-scoped-speed']?.abort()
  const ctrl = new AbortController()
  controllers['road-scoped-speed'] = ctrl

  // The bbox bounds the sites that carry no hectometre — most of the provincial
  // network — which the km window can say nothing about. A site within
  // SPEED_SIDEBAR_MAX_DISTANCE_M *along the road* is never further than that in
  // a straight line, so squaring that radius cannot cut the km window short.
  const params = new URLSearchParams({
    road: context.road,
    carriageway: context.carriageway,
    bbox: radiusBbox(coords, SPEED_SIDEBAR_MAX_DISTANCE_M),
    limit: '1000',
  })
  if (sign !== null && Number.isFinite(anchorKm)) {
    const aheadKm = (SPEED_SIDEBAR_MAX_DISTANCE_M / 1000) * sign
    const behindKm = (SPEED_SCOPE_BEHIND_M / 1000) * sign
    params.set('km_min', String(Math.min(anchorKm - behindKm, anchorKm + aheadKm)))
    params.set('km_max', String(Math.max(anchorKm - behindKm, anchorKm + aheadKm)))
  }
  fetch(`/api/traffic/speed?${params}`, { signal: ctrl.signal })
    .then(response => {
      if (!response.ok) throw new Error(`speed by road: HTTP ${response.status}`)
      return response.json()
    })
    .then(fc => {
      if (generation !== roadScopedSpeedFetch.generation) return
      roadSignHudCache.speedPointsRoad = fc || EMPTY_FC
      // Only start rendering from this pool once a fetch for this exact
      // road+carriageway succeeded — never against another key's points.
      roadScopedSpeedFetch.loadedKey = key
      if (fc?.truncated) {
        console.warn(`[road-sign-hud] speed by road truncated for ${key}: showing ${fc.count}`)
      }
      renderRoadSignHud()
    })
    .catch(error => {
      if (error.name === 'AbortError') return
      // Leave loadedKey as-is: if this key had already loaded successfully,
      // keep that (still valid) data through a transient failure.
      console.warn('[road-sign-hud] speed by road', error.message || error)
    })
}

function fetchRoadSignHudCurrentRoadSource (bbox, signal) {
  return fetch(`/api/osm/lane-lines?bbox=${bbox}`, { signal })
    .then(response => {
      if (!response.ok) throw new Error(`current road: HTTP ${response.status}`)
      return response.json()
    })
    .then(fc => { roadSignHudCache.osmLanes = fc || EMPTY_FC })
}

function fetchTrajectPairsSource (bbox, signal) {
  return fetch(`/api/flitspalen/pairs?bbox=${bbox}&limit=20`, { signal })
    .then(response => {
      if (!response.ok) throw new Error(`traject pairs: HTTP ${response.status}`)
      return response.json()
    })
    .then(fc => { roadSignHudCache.trajectPairs = fc || EMPTY_FC })
}

function fetchRoadSignHudSource (source, bbox, signal) {
  const limit = source === 'matrix' ? 300 : 25
  return fetch(`/api/signs/${source}?bbox=${bbox}&limit=${limit}`, { signal })
    .then(response => {
      if (!response.ok) throw new Error(`${source}: HTTP ${response.status}`)
      return response.json()
    })
    .then(fc => { roadSignHudCache[source] = fc })
}

function renderRoadSignHud () {
  if (gpsState === GPS_STATES.OFF || !userCoords) {
    resetHudHolds()
    renderRoadSignHudSelection({ matrix: null, drip: null, speed: null, gpsKmh: null, traject: null, speedList: [] })
    return
  }

  const selected = userHeading === null
    ? { matrix: null, drip: null }
    : selectUpcomingRoadSigns(
        hudEnabled.has('hud_matrix') ? roadSignHudCache.matrix : EMPTY_FC,
        hudEnabled.has('hud_drips') ? roadSignHudCache.drips : EMPTY_FC,
        { coords: userCoords, heading: userHeading },
        ROAD_SIGN_HUD_MAX_DISTANCE_M
      )

  selected.gpsKmh = Number.isFinite(userSpeedMps) ? userSpeedMps * 3.6 : null

  const currentRoadDevice = { coords: userCoords, heading: userHeading }
  selected.currentRoad = selectCurrentOsmLane(
    roadSignHudCache.speedLanes,
    currentRoadDevice,
    roadSignHudCurrentRoad
  ) || selectCurrentOsmLane(
    roadSignHudCache.osmLanes,
    currentRoadDevice,
    roadSignHudCurrentRoad
  )
  roadSignHudCurrentRoad = selected.currentRoad

  // Everything about the road ahead hangs off one context: the map-matched road,
  // the carriageway of it we are on, and our hectometre along that carriageway.
  const currentRoadRef = normalizeRoadRef(selected.currentRoad?.data?.ref)
  if (currentRoadRef) fetchRoadContextIfDue(currentRoadRef, userCoords, userHeading)
  else if (roadContext) invalidateRoadContext()

  const context = buildRoadContext(currentRoadRef)
  selected.roadContext = context
  if (context) fetchRoadScopedSpeedIfDue(context, userCoords)

  // Sensors are only ever selected from the pool fetched for this exact
  // road+carriageway. No context (or no matching pool yet) means no candidates
  // — deliberately, since the alternative is showing another road's traffic.
  const contextKeyChanged = roadSignHudRenderState.contextKey !==
    (context ? roadScopedSpeedKey(context) : null)
  if (contextKeyChanged) {
    roadSignHudRenderState.contextKey = context ? roadScopedSpeedKey(context) : null
    roadSignHudHold.speed = null
    roadSignHudHold.speedList = null
    speedSidebarShownKeys = new Set()
  }

  // The map-matched lane tells us which roadway of the road we are on — the
  // mainline, or a slip road alongside it carrying the same road number and
  // hectometrering. Sensor selection needs it to stay on our own roadway.
  const currentRoadway = {
    carriagewayRef: osmCarriagewayRef(selected.currentRoad?.data || {}),
    isLink: isLinkHighway(selected.currentRoad?.data?.highway),
  }

  const candidates = (context &&
    roadScopedSpeedFetch.loadedKey === roadScopedSpeedKey(context))
    ? selectRoadScopedSensors(
        roadSignHudCache.speedPointsRoad,
        context,
        { coords: userCoords, heading: userHeading },
        { maxDistanceM: SPEED_SIDEBAR_MAX_DISTANCE_M, roadway: currentRoadway }
      )
    : []

  selected.upcoming = hudEnabled.has('hud_speed')
    ? enrichLaneSpeedSelection(
        pickNextLaneSpeedSensor(candidates, HUD_SPEED_TILE_MAX_DISTANCE_M),
        roadSignHudCache.speedLanes
      )
    : null

  selected.speedList = hudEnabled.has('hud_speed_sidebar')
    ? enrichLaneSpeedSelectionList(
        buildSpeedSidebarList(candidates, {
          maxDistanceM: SPEED_SIDEBAR_MAX_DISTANCE_M,
          bands: SPEED_SIDEBAR_BANDS_M,
          keep: speedSidebarShownKeys,
        }),
        roadSignHudCache.speedLanes
      )
    : []
  if (selected.speedList.length) {
    speedSidebarShownKeys = new Set(selected.speedList.map(speedSidebarKey))
  }

  // Keep a just-passed selection on screen briefly instead of flickering off in
  // the gap before the next one. Disabled channels hold null (cleared instantly),
  // and a context change cleared the speed holds above so a lingering tile can
  // never outlive the carriageway it belongs to.
  selected.matrix = holdSelection('matrix', hudEnabled.has('hud_matrix') ? selected.matrix : null)
  selected.drip = holdSelection('drip', hudEnabled.has('hud_drips') ? selected.drip : null)
  selected.upcoming = holdSelection('speed', hudEnabled.has('hud_speed') ? selected.upcoming : null)
  selected.speedList = holdSelection('speedList', selected.speedList.length ? selected.speedList : null) || []

  const traject = selectTrajectProgress(roadSignHudCache.trajectPairs, userCoords, TRAJECT_MAX_DIST_M)
  selected.traject = holdSelection('traject', traject)
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
  renderSpeedSidebar(selected.speedList)
  updateGpsSpeedBadge(selected.gpsKmh, selected.currentRoad)
  renderTrajectProgressBar(selected.traject)
  // Only shown with an actual reading to show: without a resolved road context
  // there is nothing trustworthy to put in it, and an empty "Meetpunt zoeken"
  // tile just takes HUD space from the channels that do have something.
  const speedVisible = gpsState !== GPS_STATES.OFF &&
    hudEnabled.has('hud_speed') &&
    Boolean(selected.upcoming)
  const visibleCount = [speedVisible, selected.matrix, selected.drip].filter(Boolean).length
  const visible = visibleCount > 0
  speedTile.classList.toggle('hidden', !speedVisible)
  hud.classList.remove('road-sign-hud-count-1', 'road-sign-hud-count-2', 'road-sign-hud-count-3')
  document.body.classList.remove('road-sign-hud-count-1', 'road-sign-hud-count-2', 'road-sign-hud-count-3')
  if (visible) hud.classList.add(`road-sign-hud-count-${visibleCount}`)
  if (visible) document.body.classList.add(`road-sign-hud-count-${visibleCount}`)
  hud.classList.toggle('hidden', !visible)
  document.body.classList.toggle('road-sign-hud-visible', visible)
  // First builds measure 0 width/height while their containers are hidden;
  // refit once the final HUD visibility and dimensions have been laid out.
  requestAnimationFrame(() => {
    if (visible && selected.matrix) fitMatrixLanes()
    layoutSpeedSidebar()
  })
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

// Left sidebar: a single vertical route strip for the road ahead — bottom is
// here/now, top is the furthest upcoming sensor. The strip is filled with a
// gradient built from each sensor's speed colour (so it reads as one road,
// not separate boxes), and each sensor's speed is a pill on the strip at its
// proportional distance — same look as the on-road lane-speed-label markers.
const SPEED_SIDEBAR_MIN_MARKER_GAP_PX = 46

// Height on the strip (0 = here, 100 = the horizon) for a distance ahead.
// Logarithmic around a knee: a linear scale spent four fifths of the strip on
// the 2–10km nobody reads and crushed everything near the vehicle into the
// bottom edge, where the anti-overlap compression then had to move pills
// around. Position now follows distance alone, so pills drift downward
// smoothly and each keeps its own place.
function speedSidebarPct (alongM) {
  const knee = SPEED_SIDEBAR_SCALE_KNEE_M
  const scale = Math.log1p(SPEED_SIDEBAR_MAX_DISTANCE_M / knee)
  return (Math.log1p(Math.max(0, alongM) / knee) / scale) * 100
}

function renderSpeedSidebar (list) {
  const aside = document.getElementById('speed-sidebar')
  const track = document.getElementById('speed-sidebar-track')
  if (!aside || !track) return

  const visible = gpsState !== GPS_STATES.OFF && hudEnabled.has('hud_speed_sidebar') && list.length > 0
  aside.classList.toggle('hidden', !visible)
  if (!visible) {
    if (roadSignHudRenderState.speedListKey !== null) {
      track.replaceChildren()
      roadSignHudRenderState.speedListKey = null
    }
    return
  }

  const key = list.map(s => `${s.data.site_id}:${s.fastestKmh}:${Math.round(s.cls.along / 10)}`).join('|')
  if (roadSignHudRenderState.speedListKey === key) return
  roadSignHudRenderState.speedListKey = key

  const sorted = [...list].sort((a, b) => a.cls.along - b.cls.along)

  // pctFromBottom: 0 = here/now, 100 = SPEED_SIDEBAR_MAX_DISTANCE_M out. When
  // the furthest known sensor is short of that, the strip's top portion holds
  // that sensor's colour (CSS gradients extend the last stop's colour past it)
  // rather than showing an artificial cutoff.
  const stops = sorted.map(s => ({
    pct: speedSidebarPct(s.cls.along),
    color: speedLimitColor(s.fastestKmh, s.data.maxspeed_kmh),
  }))
  track.style.background = `linear-gradient(to top, ${stops.map(s => `${s.color} ${s.pct}%`).join(', ')})`

  track.replaceChildren()
  for (const s of sorted) {
    const p = s.data
    const kmh = s.fastestKmh
    const pctFromBottom = speedSidebarPct(s.cls.along)

    const marker = document.createElement('div')
    marker.className = 'speed-sidebar-marker'
    marker.style.top = `${100 - pctFromBottom}%`
    marker.dataset.pct = String(100 - pctFromBottom)

    const pill = document.createElement('div')
    pill.className = 'speed-sidebar-pill'
    pill.style.background = speedLimitColor(kmh, p.maxspeed_kmh)
    pill.style.color = speedLimitTextColor(kmh, p.maxspeed_kmh)
    pill.textContent = kmh !== null && kmh !== undefined ? String(Math.round(kmh)) : '?'

    const distance = document.createElement('div')
    distance.className = 'speed-sidebar-distance'
    distance.textContent = formatDistance(Math.max(0, s.cls.along))

    marker.append(pill, distance)
    track.appendChild(marker)
  }
}

const SPEED_SIDEBAR_MIN_HEIGHT_PX = 140
const SPEED_SIDEBAR_TOP_GAP_PX = 8
const SPEED_SIDEBAR_MARKER_TOP_PADDING_PX = 20

// Keep the route strip immediately below the actual HUD rather than relying on
// a fixed estimate: the speed SVG and optional matrix/DRIP row make its height
// content-dependent. Hide the strip when a short landscape viewport leaves no
// useful vertical room between the HUD and the bottom driving controls.
function layoutSpeedSidebar () {
  const aside = document.getElementById('speed-sidebar')
  const hud = document.getElementById('road-sign-hud')
  if (!aside) return

  aside.classList.remove('speed-sidebar-no-room')
  if (hud && !hud.classList.contains('hidden')) {
    aside.style.top = `${Math.ceil(hud.getBoundingClientRect().bottom + SPEED_SIDEBAR_TOP_GAP_PX)}px`
  } else {
    aside.style.removeProperty('top')
  }
  if (aside.classList.contains('hidden')) return

  const hasRoom = aside.getBoundingClientRect().height >= SPEED_SIDEBAR_MIN_HEIGHT_PX
  aside.classList.toggle('speed-sidebar-no-room', !hasRoom)
  if (hasRoom) layoutSpeedSidebarMarkers()
}

// Percentage-based marker positions can land closer together than their pills
// are tall. Anchor the nearest marker at its exact position and compress the
// gap only when necessary, keeping every farther marker inside the route strip
// instead of pushing it upward into the road-sign HUD.
function layoutSpeedSidebarMarkers () {
  const track = document.getElementById('speed-sidebar-track')
  if (!track) return
  const H = track.clientHeight
  if (!H) return

  const markers = [...track.querySelectorAll('.speed-sidebar-marker')]
    .map(el => ({ el, y: (parseFloat(el.dataset.pct) / 100) * H }))
    .sort((a, b) => b.y - a.y) // nearest (largest y, bottom) first

  const nearestY = markers[0]?.y ?? 0
  const gap = markers.length > 1
    ? Math.min(
        SPEED_SIDEBAR_MIN_MARKER_GAP_PX,
        Math.max(0, nearestY - SPEED_SIDEBAR_MARKER_TOP_PADDING_PX) / (markers.length - 1)
      )
    : SPEED_SIDEBAR_MIN_MARKER_GAP_PX
  let prevY = Infinity
  for (const m of markers) {
    const y = Math.max(
      SPEED_SIDEBAR_MARKER_TOP_PADDING_PX,
      Math.min(m.y, prevY - gap)
    )
    m.el.style.top = `${y}px`
    prevY = y
  }
}

// Circular GPS-speed badge (km/h) bottom-left, with the road we are on in the
// centre-bottom label — shown only while tracking. Its carriageway reference
// comes directly from the map-matched OSM way ("A9 • Re").
function updateGpsSpeedBadge (gpsKmh, currentRoad) {
  const badge = document.getElementById('gps-speed-badge')
  const value = document.getElementById('gps-speed-value')
  const limitSign = document.getElementById('gps-maxspeed-sign')
  const limitValue = document.getElementById('gps-maxspeed-value')
  const roadLabel = document.getElementById('current-road-label')
  if (!badge || !value || !limitSign || !limitValue || !roadLabel) return

  const tracking = gpsState !== GPS_STATES.OFF && Boolean(userCoords)
  badge.classList.toggle('hidden', !tracking)
  if (tracking) setTextIfChanged(value, Number.isFinite(gpsKmh) ? String(Math.round(gpsKmh)) : '–')

  const data = currentRoad?.data || {}
  const road = data.ref || data.name || null
  const direction = osmCarriagewayRef(data)
  const label = road && direction ? `${road} • ${direction}` : road
  roadLabel.classList.toggle('hidden', !tracking || !label)
  if (tracking && label) setTextIfChanged(roadLabel, label)

  const maxspeed = Number(data.maxspeed_kmh)
  const showLimit = tracking && Number.isFinite(maxspeed) && maxspeed > 0
  limitSign.classList.toggle('hidden', !showLimit)
  if (showLimit) {
    const rounded = String(Math.round(maxspeed))
    setTextIfChanged(limitValue, rounded)
    limitSign.setAttribute('aria-label', `Maximum speed ${rounded} km/h`)
  } else {
    limitSign.removeAttribute('aria-label')
  }
}

// Bottom progress bar for an active trajectcontrole (speed-camera) section:
// section length (km) + how far travelled / how far remains.
function renderTrajectProgressBar (traject) {
  const bar = document.getElementById('traject-progress')
  const street = document.getElementById('traject-progress-street')
  const remaining = document.getElementById('traject-progress-remaining')
  const travelled = document.getElementById('traject-progress-travelled')
  const total = document.getElementById('traject-progress-total')
  const fill = document.getElementById('traject-progress-fill')
  if (!bar || !street || !remaining || !travelled || !total || !fill) return

  bar.classList.toggle('hidden', !traject)
  if (!traject) return

  setTextIfChanged(street, traject.street || 'Trajectcontrole')
  setTextIfChanged(remaining, `${formatDistance(traject.remaining)} te gaan`)
  setTextIfChanged(travelled, formatDistance(traject.travelled))
  setTextIfChanged(total, formatDistance(traject.total))
  const pct = traject.total > 0 ? Math.min(100, (traject.travelled / traject.total) * 100) : 0
  fill.style.width = `${pct}%`
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
  controllers['road-scoped-speed']?.abort()
  controllers['road-context']?.abort()
  resetHudHolds()
  roadSignHudCache.matrix = EMPTY_FC
  roadSignHudCache.drips = EMPTY_FC
  roadSignHudCache.speedLanes = EMPTY_FC
  roadSignHudCache.osmLanes = EMPTY_FC
  roadSignHudCache.trajectPairs = EMPTY_FC
  roadSignHudCache.speedPointsRoad = EMPTY_FC
  roadScopedSpeedFetch = {
    attemptedKey: null,
    attemptedAt: 0,
    loadedKey: null,
    generation: roadScopedSpeedFetch.generation + 1,
  }
  roadContext = null
  roadContextFetch = {
    road: null, at: 0, coords: null, heading: null,
    generation: roadContextFetch.generation + 1,
  }
  roadSignHudRenderState.contextKey = null
  roadSignHudCurrentRoad = null
  roadSignHudLastFetchCoords = null
  roadSignHudLastFetchAt = 0
  roadSignHudLastFetchHeading = null
  renderRoadSignHud()
}
