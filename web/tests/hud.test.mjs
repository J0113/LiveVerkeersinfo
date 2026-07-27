// Drive-HUD wiring: road-context lifecycle (staleness, direction reversal) and
// the visibility rules for the two speed displays.

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { featureCollection, loadWeb, offsetCoords, sensor } from './harness.mjs'

const HERE = [4.7105, 52.5182]

// One deferred response per URL prefix, so a test can control exactly when (and
// in which order) each request resolves.
function deferredFetch () {
  const pending = []
  const impl = url => new Promise(resolve => pending.push({ url, resolve }))
  impl.pending = pending
  const resolveAt = (index, body) => {
    assert.notEqual(index, -1, 'no matching pending request')
    const [entry] = pending.splice(index, 1)
    entry.resolve(body)
    // Let the promise chain in the app settle.
    return new Promise(resolve => setTimeout(resolve, 0))
  }
  impl.resolveNext = (match, body) =>
    resolveAt(pending.findIndex(entry => entry.url.includes(match)), body)
  impl.resolveLast = (match, body) =>
    resolveAt(pending.findLastIndex(entry => entry.url.includes(match)), body)
  return impl
}

function bootHud ({ fetchImpl } = {}) {
  const web = loadWeb({ scripts: ['lib.js', 'config.js', 'hud.js'], fetchImpl })
  web.run(`
    gpsState = GPS_STATES.NAVIGATION
    userCoords = [${HERE[0]}, ${HERE[1]}]
    userHeading = 0
    userSpeedMps = 25
    hudEnabled.add('hud_speed')
    hudEnabled.add('hud_speed_sidebar')
    // Fetch callbacks re-render, which re-runs map-matching against lane caches
    // no unit test has geometry for (and would then correctly discard the very
    // context under test). Rendering has its own tests below.
    renderRoadSignHud = function () {}
  `)
  return web
}

const CONTEXT_BODY = { road: 'A9', carriageway: 'R', anchor_km: 12.0, anchor_distance_m: 40 }

function speedBody (features) {
  return { ...featureCollection(features), count: features.length, truncated: false }
}

const AHEAD = sensor({ km: 13.0, coords: offsetCoords(HERE, { north: 1000 }) })

// ─── road context lifecycle ──────────────────────────────────────────────────

test('a resolved context drives a road+carriageway+hectometre scoped fetch', async () => {
  const fetchImpl = deferredFetch()
  const web = bootHud({ fetchImpl })

  web.run(`fetchRoadContextIfDue('A9', userCoords, userHeading)`)
  assert.match(web.requests[0], /\/api\/traffic\/road-context\?road=A9&lon=4\.7105&lat=52\.5182&heading=0/)
  await fetchImpl.resolveNext('road-context', CONTEXT_BODY)

  web.run(`fetchRoadScopedSpeedIfDue(buildRoadContext('A9'), userCoords)`)
  const speedUrl = web.requests.find(url => url.includes('/api/traffic/speed?'))
  assert.match(speedUrl, /road=A9/)
  assert.match(speedUrl, /carriageway=R/)
  // 10km ahead, 0.5km behind, counted upward because we are on carriageway R.
  assert.match(speedUrl, /km_min=11\.5/)
  assert.match(speedUrl, /km_max=22/)
  // No bbox: a straight corridor is exactly what the hectometre window replaces.
  assert.ok(!speedUrl.includes('bbox'))
})

test('a low-confidence anchor is not usable for selection', () => {
  const web = bootHud()
  web.run(`roadContext = {
    road: 'A9', carriageway: 'R', anchorKm: 12,
    anchorDistanceM: ROAD_CONTEXT_MAX_ANCHOR_DISTANCE_M + 1,
    coords: userCoords, at: Date.now(),
  }`)
  assert.equal(web.run(`buildRoadContext('A9').usableForSelection`), false)
  // The carriageway is still known, so the road label can still show it.
  assert.equal(web.run(`buildRoadContext('A9').carriageway`), 'R')
})

test('an anchor at the 500m confidence boundary remains usable', () => {
  const web = bootHud()
  web.run(`roadContext = {
    road: 'A9', carriageway: 'R', anchorKm: 12,
    anchorDistanceM: ROAD_CONTEXT_MAX_ANCHOR_DISTANCE_M,
    coords: userCoords, at: Date.now(),
  }`)
  assert.equal(web.run(`ROAD_CONTEXT_MAX_ANCHOR_DISTANCE_M`), 500)
  assert.equal(web.run(`buildRoadContext('A9').usableForSelection`), true)
})

test('a context for another road is not used', () => {
  const web = bootHud()
  web.run(`roadContext = {
    road: 'A9', carriageway: 'R', anchorKm: 12, anchorDistanceM: 40,
    coords: userCoords, at: Date.now(),
  }`)
  assert.equal(web.run(`buildRoadContext('N201')`), null)
})

test('a stale context is discarded', () => {
  const web = bootHud()
  web.run(`roadContext = {
    road: 'A9', carriageway: 'R', anchorKm: 12, anchorDistanceM: 40,
    coords: userCoords, at: Date.now() - ROAD_CONTEXT_MAX_AGE_MS - 1,
  }`)
  assert.equal(web.run(`buildRoadContext('A9')`), null)
})

test('reversing direction on the same road drops the cached sensors', async () => {
  const fetchImpl = deferredFetch()
  const web = bootHud({ fetchImpl })

  web.run(`fetchRoadContextIfDue('A9', userCoords, 0)`)
  await fetchImpl.resolveNext('road-context', CONTEXT_BODY)
  web.run(`fetchRoadScopedSpeedIfDue(buildRoadContext('A9'), userCoords)`)
  await fetchImpl.resolveNext('/api/traffic/speed?', speedBody([AHEAD]))
  assert.equal(web.run(`roadScopedSpeedFetch.loadedKey`), 'A9|R')

  // U-turn: same road, opposite carriageway.
  web.run(`userHeading = 180; fetchRoadContextIfDue('A9', userCoords, 180)`)
  assert.equal(web.run(`roadContext`), null, 'context cleared immediately')
  assert.equal(web.run(`roadScopedSpeedFetch.loadedKey`), null, 'cached sensors dropped')
  assert.equal(web.run(`roadSignHudCache.speedPointsRoad.features.length`), 0)

  await fetchImpl.resolveNext('road-context', { ...CONTEXT_BODY, carriageway: 'L', anchor_km: 12.0 })
  assert.equal(web.run(`buildRoadContext('A9').carriageway`), 'L')
  web.run(`fetchRoadScopedSpeedIfDue(buildRoadContext('A9'), userCoords)`)
  const speedUrl = web.requests.filter(url => url.includes('/api/traffic/speed?')).pop()
  assert.match(speedUrl, /carriageway=L/)
  // Hectometres now count downward: the window runs below the anchor.
  assert.match(speedUrl, /km_min=2/)
  assert.match(speedUrl, /km_max=12\.5/)
})

test('an ordinary bend refreshes without dropping the current context', async () => {
  const fetchImpl = deferredFetch()
  const web = bootHud({ fetchImpl })

  web.run(`fetchRoadContextIfDue('A9', userCoords, 0)`)
  await fetchImpl.resolveNext('road-context', CONTEXT_BODY)
  web.run(`fetchRoadScopedSpeedIfDue(buildRoadContext('A9'), userCoords)`)
  await fetchImpl.resolveNext('/api/traffic/speed?', speedBody([AHEAD]))

  web.run(`userHeading = 25; fetchRoadContextIfDue('A9', userCoords, 25)`)
  assert.equal(web.run(`roadContext.carriageway`), 'R')
  assert.equal(web.run(`roadScopedSpeedFetch.loadedKey`), 'A9|R')

  await fetchImpl.resolveNext('road-context', CONTEXT_BODY)
  assert.equal(web.run(`roadContext.carriageway`), 'R')
})

test('a road-context response that arrives after a direction change is ignored', async () => {
  const fetchImpl = deferredFetch()
  const web = bootHud({ fetchImpl })

  web.run(`fetchRoadContextIfDue('A9', userCoords, 0)`)
  web.run(`userHeading = 180; fetchRoadContextIfDue('A9', userCoords, 180)`)
  assert.equal(fetchImpl.pending.length, 2)

  // The current (southbound) request answers first; the superseded northbound
  // one lands afterwards and must not put us back on carriageway R.
  await fetchImpl.resolveLast('road-context', { ...CONTEXT_BODY, carriageway: 'L' })
  await fetchImpl.resolveNext('road-context', CONTEXT_BODY)
  assert.equal(web.run(`roadContext.carriageway`), 'L')
})

test('a speed response from a superseded key is ignored', async () => {
  const fetchImpl = deferredFetch()
  const web = bootHud({ fetchImpl })

  web.run(`roadContext = {
    road: 'A9', carriageway: 'R', anchorKm: 12, anchorDistanceM: 40,
    coords: userCoords, at: Date.now(),
  }`)
  web.run(`fetchRoadScopedSpeedIfDue(buildRoadContext('A9'), userCoords)`)
  web.run(`roadContext.carriageway = 'L'`)
  web.run(`fetchRoadScopedSpeedIfDue(buildRoadContext('A9'), userCoords)`)

  // The stale carriageway-R reply resolves after the carriageway-L request.
  await fetchImpl.resolveNext('carriageway=R', speedBody([AHEAD]))
  assert.equal(web.run(`roadSignHudCache.speedPointsRoad.features.length`), 0)
  assert.equal(web.run(`roadScopedSpeedFetch.loadedKey`), null)

  await fetchImpl.resolveNext('carriageway=L', speedBody([AHEAD]))
  assert.equal(web.run(`roadScopedSpeedFetch.loadedKey`), 'A9|L')
})

test('losing the road clears the context and its sensors', async () => {
  const fetchImpl = deferredFetch()
  const web = bootHud({ fetchImpl })
  web.run(`fetchRoadContextIfDue('A9', userCoords, 0)`)
  await fetchImpl.resolveNext('road-context', CONTEXT_BODY)
  web.run(`fetchRoadScopedSpeedIfDue(buildRoadContext('A9'), userCoords)`)
  await fetchImpl.resolveNext('/api/traffic/speed?', speedBody([AHEAD]))

  web.run(`invalidateRoadContext()`)
  assert.equal(web.run(`roadContext`), null)
  assert.equal(web.run(`roadScopedSpeedFetch.loadedKey`), null)
  assert.equal(web.run(`roadSignHudCache.speedPointsRoad.features.length`), 0)
})

// ─── visibility ──────────────────────────────────────────────────────────────

function renderWith (web, { context = true, sensors = [AHEAD], loaded = true } = {}) {
  if (context) {
    web.run(`roadContext = {
      road: 'A9', carriageway: 'R', anchorKm: 12, anchorDistanceM: 40,
      coords: userCoords, at: Date.now(),
    }`)
  } else {
    web.run(`roadContext = null`)
  }
  web.run(`roadSignHudCache.speedPointsRoad = ${JSON.stringify(featureCollection(sensors))}`)
  web.run(`roadScopedSpeedFetch.loadedKey = ${loaded ? "'A9|R'" : 'null'}`)
  // Map-match the current road without needing lane geometry in the fixture.
  web.run(`roadSignHudCurrentRoad = { data: { ref: 'A9', maxspeed_kmh: 100 } }`)
  web.run(`renderRoadSignHudFromState()`)
}

// renderRoadSignHud() re-runs map-matching from the lane caches, which a unit
// test has no geometry for; this drives the same selection + render path from
// an already-matched current road.
function installRenderShim (web) {
  web.run(`
    function renderRoadSignHudFromState () {
      const selected = { matrix: null, drip: null, gpsKmh: 90, traject: null,
        currentRoad: roadSignHudCurrentRoad }
      const road = normalizeRoadRef(roadSignHudCurrentRoad?.data?.ref)
      const context = buildRoadContext(road)
      selected.roadContext = context
      const candidates = (context?.usableForSelection &&
        roadScopedSpeedFetch.loadedKey === roadScopedSpeedKey(context))
        ? selectRoadScopedSensors(roadSignHudCache.speedPointsRoad, context,
            { coords: userCoords, heading: userHeading },
            { maxDistanceM: SPEED_SIDEBAR_MAX_DISTANCE_M })
        : []
      selected.upcoming = hudEnabled.has('hud_speed')
        ? pickNextLaneSpeedSensor(candidates, HUD_SPEED_TILE_MAX_DISTANCE_M) : null
      selected.speedList = hudEnabled.has('hud_speed_sidebar')
        ? buildSpeedSidebarList(candidates,
            { maxDistanceM: SPEED_SIDEBAR_MAX_DISTANCE_M, maxCount: SPEED_SIDEBAR_MAX_COUNT })
        : []
      renderRoadSignHudSelection(selected)
    }
  `)
}

test('both speed displays stay hidden without a road context', () => {
  const web = bootHud()
  installRenderShim(web)
  renderWith(web, { context: false })
  assert.equal(web.isHidden('road-sign-hud-speed'), true)
  assert.equal(web.isHidden('speed-sidebar'), true)
})

test('both speed displays stay hidden until the pool for this key has loaded', () => {
  const web = bootHud()
  installRenderShim(web)
  renderWith(web, { loaded: false })
  assert.equal(web.isHidden('road-sign-hud-speed'), true)
  assert.equal(web.isHidden('speed-sidebar'), true)
})

test('both speed displays stay hidden when no sensor carries a reading', () => {
  const web = bootHud()
  installRenderShim(web)
  const silent = sensor({ km: 13.0, coords: offsetCoords(HERE, { north: 1000 }), speeds: [null] })
  renderWith(web, { sensors: [silent] })
  assert.equal(web.isHidden('road-sign-hud-speed'), true)
  assert.equal(web.isHidden('speed-sidebar'), true)
})

test('both speed displays appear once the road ahead has data', () => {
  const web = bootHud()
  installRenderShim(web)
  renderWith(web)
  assert.equal(web.isHidden('road-sign-hud-speed'), false)
  assert.equal(web.isHidden('speed-sidebar'), false)
})

test('a sensor past the tile horizon still fills the sidebar only', () => {
  const web = bootHud()
  installRenderShim(web)
  const far = sensor({ km: 20.0, coords: offsetCoords(HERE, { north: 7000 }) })
  renderWith(web, { sensors: [far] })
  assert.equal(web.isHidden('road-sign-hud-speed'), true)
  assert.equal(web.isHidden('speed-sidebar'), false)
})

// ─── road label ──────────────────────────────────────────────────────────────

test('the road label carries the carriageway when it is resolved', () => {
  const web = bootHud()
  web.run(`updateGpsSpeedBadge(100, { data: { ref: 'A9' } }, { carriageway: 'R' })`)
  assert.equal(web.textOf('current-road-label'), 'A9 • Re')

  web.run(`updateGpsSpeedBadge(100, { data: { ref: 'A9' } }, { carriageway: 'L' })`)
  assert.equal(web.textOf('current-road-label'), 'A9 • Li')
})

test('the road label falls back to the bare road without a carriageway', () => {
  const web = bootHud()
  web.run(`updateGpsSpeedBadge(100, { data: { ref: 'A9' } }, null)`)
  assert.equal(web.textOf('current-road-label'), 'A9')
  assert.equal(web.isHidden('current-road-label'), false)
})

test('the road label is hidden when no road is matched', () => {
  const web = bootHud()
  web.run(`updateGpsSpeedBadge(100, null, null)`)
  assert.equal(web.isHidden('current-road-label'), true)
})
