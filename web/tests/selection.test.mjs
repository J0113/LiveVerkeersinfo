// Upcoming-sensor selection: road + carriageway scoping and hectometre
// placement (the curve-proof part of the drive HUD).

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { closeTo, featureCollection, ids, loadWeb, offsetCoords, sensor } from './harness.mjs'

const web = loadWeb({ scripts: ['lib.js', 'config.js'] })
const {
  buildSpeedSidebarList,
  normalizeRoadRef,
  osmCarriagewayRef,
  pickNextLaneSpeedSensor,
  roadContextAnchorKm,
  selectRoadScopedSensors,
} = web

const BANDS = web.run('SPEED_SIDEBAR_BANDS_M')
const HERE = [4.7105, 52.5182]
const NORTHBOUND = { coords: HERE, heading: 0 }
const CONTEXT = { road: 'A9', carriageway: 'R', anchorKm: 12.0, coords: HERE }

function select (features, { context = CONTEXT, device = NORTHBOUND, maxDistanceM = 10000, roadway } = {}) {
  return selectRoadScopedSensors(featureCollection(features), context, device, { maxDistanceM, roadway })
}

// ─── road reference normalization ────────────────────────────────────────────

test('normalizeRoadRef matches the API normalization', () => {
  assert.equal(normalizeRoadRef('a02'), 'A2')
  assert.equal(normalizeRoadRef('A9'), 'A9')
  assert.equal(normalizeRoadRef('N201'), 'N201')
  assert.equal(normalizeRoadRef('A9;A200'), 'A9')
  assert.equal(normalizeRoadRef(null), null)
  assert.equal(normalizeRoadRef('Kruisweg'), null)
})

test('osmCarriagewayRef preserves every non-empty OSM carriageway reference', () => {
  assert.equal(osmCarriagewayRef({ carriageway_ref: 'Re' }), 'Re')
  assert.equal(osmCarriagewayRef({ carriageway_ref: 'Li' }), 'Li')
  assert.equal(osmCarriagewayRef({ carriageway_ref: 'a' }), 'a')
  assert.equal(osmCarriagewayRef({ 'carriageway:ref': ' d ' }), 'd')
  assert.equal(osmCarriagewayRef({ carriageway_ref: '' }), null)
  assert.equal(osmCarriagewayRef(null), null)
})

// ─── hectometre placement ────────────────────────────────────────────────────

test('a sensor round a bend is selected despite a large cross-track offset', () => {
  // 2km ahead along the road, but the road has turned: the sensor sits mostly
  // to the east, so its along-track offset is small and its cross-track offset
  // is far outside any straight corridor. Its hectometre still places it.
  const curved = offsetCoords(HERE, { east: 1900, north: 300 })
  const [picked] = select([sensor({ km: 14.0, coords: curved })])
  assert.equal(picked.placement, 'km')
  assert.equal(Math.round(picked.cls.along), 2000)
})

test('distance ahead comes from the hectometre difference, not the straight line', () => {
  const coords = offsetCoords(HERE, { north: 900, east: 600 })
  const [picked] = select([sensor({ km: 13.5, coords })])
  assert.equal(Math.round(picked.cls.along), 1500)
})

test('carriageway L counts hectometres downward', () => {
  const context = { ...CONTEXT, carriageway: 'L' }
  const coords = offsetCoords(HERE, { north: 1000 })
  const [picked] = select([sensor({ km: 10.8, carriageway: 'L', coords })], { context })
  assert.equal(Math.round(picked.cls.along), 1200)
})

test('the N470 carriageway-L example yields the two expected sidebar markers', () => {
  const coords = [4.346025374342275, 51.98617174469409]
  const context = {
    road: 'N470',
    carriageway: 'L',
    anchorKm: 2.1957,
    anchorDistanceM: 99.4,
    coords,
  }
  const features = [
    sensor({
      siteId: 'km-1.295',
      road: 'N470',
      carriageway: 'L',
      km: 1.295,
      coords: [4.33336, 51.98422],
      speeds: [56, 51],
      highway: 'primary',
    }),
    sensor({
      siteId: 'km-1.899',
      road: 'N470',
      carriageway: 'L',
      km: 1.899,
      coords: [4.34183, 51.9855],
      speeds: [73, 68],
      highway: 'primary',
    }),
    sensor({
      siteId: 'km-2.295',
      road: 'N470',
      carriageway: 'L',
      km: 2.295,
      coords: [4.34743, 51.9864],
      speeds: [67, 62],
      highway: 'primary',
    }),
  ]

  const candidates = selectRoadScopedSensors(
    featureCollection(features),
    context,
    { coords, heading: 258 },
    { maxDistanceM: 10000 }
  )
  const list = buildSpeedSidebarList(candidates, { maxDistanceM: 10000, bands: BANDS })

  assert.equal(ids(list), 'km-1.899,km-1.295')
  assert.deepEqual(
    [...list].map(item => [item.data.km, Math.round(item.cls.along), item.fastestKmh]),
    [[1.899, 297, 73], [1.295, 901, 56]]
  )
})

test('sensors behind us are dropped', () => {
  const coords = offsetCoords(HERE, { north: -800 })
  assert.equal(select([sensor({ km: 11.2, coords })]).length, 0)
})

test('sensors past the horizon are dropped', () => {
  const coords = offsetCoords(HERE, { north: 9000 })
  assert.equal(select([sensor({ km: 24.0, coords })], { maxDistanceM: 10000 }).length, 0)
})

test('candidates come back nearest first', () => {
  const far = sensor({ siteId: 'far', km: 15.0, coords: offsetCoords(HERE, { north: 3000 }) })
  const near = sensor({ siteId: 'near', km: 12.5, coords: offsetCoords(HERE, { north: 500 }) })
  assert.equal(ids(select([far, near])), 'near,far')
})

// ─── road and direction scoping ──────────────────────────────────────────────

test('another road is rejected even when it is geometrically close', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  assert.equal(select([sensor({ road: 'N201', km: 13.0, coords })]).length, 0)
})

test('the opposite carriageway is rejected', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  assert.equal(select([sensor({ carriageway: 'L', km: 13.0, coords })]).length, 0)
})

test('a VILD-derived carriageway is honoured when the explicit one is absent', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  const wrong = sensor({
    carriageway: null, km: 13.0, coords, extra: { derived_carriageway: 'L' },
  })
  assert.equal(select([wrong]).length, 0)

  const right = sensor({
    carriageway: null, km: 13.0, coords, extra: { derived_carriageway: 'R' },
  })
  assert.equal(select([right]).length, 1)
})

test('a site with no road reference stays in — the pool is already road-scoped', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  assert.equal(select([sensor({ road: null, km: 13.0, coords })]).length, 1)
})

test('nothing is selected without a resolved carriageway', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  const context = { ...CONTEXT, carriageway: null }
  assert.equal(select([sensor({ km: 13.0, coords })], { context }).length, 0)
})

test('sensors without any speed reading are excluded', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  assert.equal(select([sensor({ km: 13.0, coords, speeds: [null, null] })]).length, 0)
})

// ─── hectometre sanity guards ────────────────────────────────────────────────

test('a hectometre far below the straight-line distance is rejected', () => {
  // 5km away but claiming 100m along the road: not our hectometrering (an N-road
  // reset at a province boundary, or a stray km on a co-located site).
  const coords = offsetCoords(HERE, { north: 5000 })
  assert.equal(select([sensor({ km: 12.1, coords })]).length, 0)
})

test('a hectometre far above the straight-line distance is rejected', () => {
  const coords = offsetCoords(HERE, { north: 200 })
  assert.equal(select([sensor({ km: 21.0, coords })]).length, 0)
})

test('a distant anchor loosens the guard by its own uncertainty', () => {
  // Anchor resolved from a site 800m away, so a nearby sensor's hectometre can
  // be off by that much — rejecting it would blank the display exactly where
  // sensors are sparse. A reset (kilometres out) is still caught.
  const coords = offsetCoords(HERE, { north: 1200 })
  const context = { ...CONTEXT, anchorDistanceM: 800 }
  assert.equal(select([sensor({ km: 12.6, coords })], { context }).length, 1)
  assert.equal(select([sensor({ km: 12.6, coords })]).length, 0)
  assert.equal(select([sensor({ km: 12.05, coords })], { context }).length, 0)
})

test('a curve stays within the guard band', () => {
  // Half a circle of radius ~640m: 2km of road spanning a ~1.3km chord.
  const coords = offsetCoords(HERE, { north: 100, east: 1270 })
  assert.equal(select([sensor({ km: 14.0, coords })]).length, 1)
})

// ─── geometric placement (roads without hectometrering) ─────────────────────

test('a sensor without a hectometre is placed by projection instead', () => {
  const coords = offsetCoords(HERE, { north: 800 })
  const [picked] = select([sensor({ km: null, coords })])
  assert.equal(picked.placement, 'geo')
  assert.equal(Math.round(picked.cls.along), 800)
})

test('no anchor hectometre puts every sensor on the geometric path', () => {
  const context = { ...CONTEXT, anchorKm: null }
  const coords = offsetCoords(HERE, { north: 800 })
  const [picked] = select([sensor({ km: 13.0, coords })], { context })
  assert.equal(picked.placement, 'geo')
})

test('geometric placement rejects what is behind, off to the side, or too far', () => {
  const geo = north => select([sensor({ km: null, coords: offsetCoords(HERE, { north }) })])
  assert.equal(geo(-800).length, 0, 'behind us')
  assert.equal(geo(5000).length, 0, 'past the geometric horizon')
  // 800m ahead allows a corridor of 100 + 20% = 260m; 400m to the side is a
  // parallel road, not ours.
  const aside = offsetCoords(HERE, { north: 800, east: 400 })
  assert.equal(select([sensor({ km: null, coords: aside })]).length, 0)
})

test('geometric placement needs a heading, and honours the site bearing', () => {
  const coords = offsetCoords(HERE, { north: 800 })
  const device = { coords: HERE, heading: null }
  assert.equal(select([sensor({ km: null, coords })], { device }).length, 0)
  // A site facing back down the road is not one we are driving towards.
  assert.equal(select([sensor({ km: null, coords, bearing: 180 })]).length, 0)
  assert.equal(select([sensor({ km: null, coords, bearing: 10 })]).length, 1)
})

test('hectometre placement still wins wherever a hectometre exists', () => {
  // Mixed road: the km site is placed by hectometre round the bend, the km-less
  // one by projection. Both appear, nearest first.
  const curved = offsetCoords(HERE, { east: 1900, north: 300 })
  const straight = offsetCoords(HERE, { north: 600 })
  const picked = select([
    sensor({ siteId: 'km-site', km: 14.0, coords: curved }),
    sensor({ siteId: 'no-km', km: null, coords: straight }),
  ])
  assert.equal(
    picked.map(item => `${item.data.site_id}:${item.placement}`).join(','),
    'no-km:geo,km-site:km'
  )
})

// ─── anchor advance between fetches ──────────────────────────────────────────

test('the anchor advances with distance travelled since it was resolved', () => {
  const moved = offsetCoords(HERE, { north: 300 })
  closeTo(roadContextAnchorKm(CONTEXT, moved), 12.3, 0.005)
})

test('the anchor advances downward on carriageway L', () => {
  const context = { ...CONTEXT, carriageway: 'L' }
  const moved = offsetCoords(HERE, { north: 300 })
  closeTo(roadContextAnchorKm(context, moved), 11.7, 0.005)
})

test('no anchor stays no anchor', () => {
  assert.equal(roadContextAnchorKm({ ...CONTEXT, anchorKm: null }, HERE), null)
  assert.equal(roadContextAnchorKm(null, HERE), null)
})

// ─── tile and sidebar shaping ────────────────────────────────────────────────

test('the tile takes the nearest candidate within its own shorter horizon', () => {
  const near = sensor({ siteId: 'near', km: 13.0, coords: offsetCoords(HERE, { north: 1000 }) })
  const far = sensor({ siteId: 'far', km: 20.0, coords: offsetCoords(HERE, { north: 7500 }) })
  const candidates = select([far, near])
  assert.equal(pickNextLaneSpeedSensor(candidates, 2500).data.site_id, 'near')
  // Only the far one left: past the tile horizon, so the tile shows nothing
  // even though the sidebar still lists it.
  assert.equal(pickNextLaneSpeedSensor(select([far]), 2500), null)
  assert.equal(buildSpeedSidebarList(select([far]), { bands: BANDS }).length, 1)
})

test('the sidebar merges co-located gantries and keeps the fastest reading', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  const a = sensor({ siteId: 'RWS01_MONIBAS_0091hrl0130ra', km: 13.0, coords, speeds: [96] })
  const b = sensor({ siteId: 'RWS01_MONIBAS_0091vwh0130ra', km: 13.0, coords, speeds: [104] })
  const list = buildSpeedSidebarList(select([a, b]), { bands: BANDS })
  assert.equal(list.length, 1)
  assert.equal(list[0].fastestKmh, 104)
})

test('on/off-ramp sensors are excluded from the mainline, unmatched ones are kept', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  const ramp = sensor({ siteId: 'ramp', km: 13.0, coords, highway: 'motorway_link' })
  const unknown = sensor({ siteId: 'unknown', km: 13.2, coords, highway: null })
  const candidates = select([ramp, unknown])
  // Both displays draw from the same pool, so neither can show ramp traffic as
  // if it were the carriageway we are driving.
  assert.equal(ids(candidates), 'unknown')
  assert.equal(ids(buildSpeedSidebarList(candidates, { bands: BANDS })), 'unknown')
})

// ─── which roadway of the road we are on ─────────────────────────────────────

const MAINLINE = { carriagewayRef: 'Li', isLink: false }
const SLIP_ROAD = { carriagewayRef: 'c', isLink: true }

function rampSensor (overrides = {}) {
  const { carriagewayRef = 'c', ...rest } = overrides
  return sensor({
    siteId: 'ramp',
    km: 13.0,
    coords: offsetCoords(HERE, { north: 1000 }),
    highway: 'motorway_link',
    extra: { osm_carriageway_ref: carriagewayRef },
    ...rest,
  })
}

function mainlineSensor (overrides = {}) {
  const { carriagewayRef = 'Li', ...rest } = overrides
  return sensor({
    siteId: 'mainline',
    km: 13.2,
    coords: offsetCoords(HERE, { north: 1200 }),
    extra: { osm_carriageway_ref: carriagewayRef },
    ...rest,
  })
}

test('driving the slip road selects its sensors, not the mainline it left', () => {
  const features = [rampSensor(), mainlineSensor()]
  assert.equal(ids(select(features, { roadway: SLIP_ROAD })), 'ramp')
  assert.equal(ids(select(features, { roadway: MAINLINE })), 'mainline')
})

test('carriageway_ref decides even when both roadways carry the same highway class', () => {
  // Parallel carriageways ("parallelbaan") are ordinary motorway, not _link, so
  // link-ness alone cannot separate them — the reference letter has to.
  const parallel = rampSensor({ siteId: 'parallel', highway: 'motorway' })
  const candidates = select([parallel, mainlineSensor()], { roadway: SLIP_ROAD })
  assert.equal(ids(candidates), 'parallel')
})

test('an unmatched site falls back to the roadway its NDW name encodes', () => {
  // What OSM matching misses (bearing_mismatch on a curve, say) the site name
  // still says: 0091hrl… is the hoofdrijbaan links, 0090vwc… slip road c.
  const named = (siteId, ref) => sensor({
    siteId,
    km: 13.0,
    coords: offsetCoords(HERE, { north: 1000 }),
    highway: null,
    extra: { ndw_roadway_ref: ref },
  })
  const features = [named('hrl', 'Li'), named('vwc', 'c')]
  assert.equal(ids(select(features, { roadway: MAINLINE })), 'hrl')
  assert.equal(ids(select(features, { roadway: SLIP_ROAD })), 'vwc')
})

test('the OSM match outranks the name when both are known', () => {
  const conflicted = rampSensor()
  conflicted.properties.ndw_roadway_ref = 'Li'
  assert.equal(ids(select([conflicted], { roadway: SLIP_ROAD })), 'ramp')
  assert.equal(select([conflicted], { roadway: MAINLINE }).length, 0)
})

test('a sensor whose roadway cannot be established is dropped, not guessed', () => {
  const unattributable = sensor({
    siteId: 'unattributable',
    km: 13.0,
    coords: offsetCoords(HERE, { north: 1000 }),
    highway: null,
  })
  assert.equal(select([unattributable], { roadway: SLIP_ROAD }).length, 0)
  assert.equal(select([unattributable], { roadway: MAINLINE }).length, 0)
})

test('without a carriageway_ref for our own lane link-ness still decides', () => {
  // N-roads and untagged ways leave us nothing to compare, so the coarse rule
  // is all there is — and there an unmatched site is kept rather than lost.
  const ramp = rampSensor({ carriagewayRef: null })
  const mainline = mainlineSensor({ carriagewayRef: null })
  const unmatched = sensor({ siteId: 'unmatched', km: 13.4, coords: offsetCoords(HERE, { north: 1400 }), highway: null })
  const onLink = { carriagewayRef: null, isLink: true }
  const onMainline = { carriagewayRef: null, isLink: false }
  assert.equal(ids(select([ramp, mainline, unmatched], { roadway: onLink })), 'ramp,unmatched')
  assert.equal(ids(select([ramp, mainline, unmatched], { roadway: onMainline })), 'mainline,unmatched')
})

// ─── distance bands (what keeps the bar still while driving) ─────────────────


// A9-like density: a gantry roughly every 500m, more than the bar can show.
function densePool () {
  return [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(i => sensor({
    siteId: `s${i}`,
    km: 12 + i * 0.5,
    coords: offsetCoords(HERE, { north: i * 500 }),
  }))
}

/** Candidates straight from distances, for driving the band logic directly. */
function at (alongBySite) {
  return Object.entries(alongBySite).map(([siteId, along]) => ({
    data: { site_id: siteId, road: 'A9', km: 12 + along / 1000, lanes: [{ lane: 1, speed_kmh: 100 }] },
    cls: { status: 'ahead', along, cross: 0, dist: along },
  }))
}

const listIds = list => list.map(item => item.data.site_id).join(',')

test('each band contributes the nearest sensor inside it', () => {
  const list = buildSpeedSidebarList(select(densePool()), { bands: BANDS })
  // 500m spacing against bands 300/700/1200/2000/3200/5000/…: one pill per
  // band, and the far bands cover more road so they skip more sensors.
  assert.equal(ids(list), 's1,s2,s3,s5,s7')
  assert.ok(list.length <= BANDS.length)
})

test('an empty band yields no pill rather than borrowing from its neighbours', () => {
  const list = buildSpeedSidebarList(at({ near: 120, far: 4000 }), { bands: BANDS })
  assert.equal(listIds(list), 'near,far')
})

test('driving forward changes the list one entry at a time', () => {
  // The same sensors, seen from 100m further along the road each step. A band
  // selection only changes when a sensor crosses a boundary, so consecutive
  // lists never differ by more than one entry.
  const base = { a: 250, b: 520, c: 900, d: 1400, e: 2100, f: 3000, g: 4200, h: 6000, i: 8000 }
  let previous = null
  for (let travelled = 0; travelled <= 1500; travelled += 100) {
    const moved = Object.fromEntries(
      Object.entries(base).map(([id, along]) => [id, along - travelled]).filter(([, along]) => along > 0)
    )
    const current = buildSpeedSidebarList(at(moved), { bands: BANDS }).map(item => item.data.site_id)
    if (previous) {
      const added = current.filter(id => !previous.includes(id))
      const removed = previous.filter(id => !current.includes(id))
      const where = `at ${travelled}m: ${previous} -> ${current}`
      // One band changes occupant at a time: the sensor that crossed out of it
      // leaves, its successor arrives. Nothing else moves.
      assert.ok(added.length <= 1, where)
      assert.ok(removed.length <= 1, where)
    }
    previous = current
  }
})

test('a band keeps the sensor already shown when a nearer one turns up', () => {
  // Sensors come and go between refreshes (a site whose lanes all read null
  // drops out of the pool and returns). Without stickiness that would swap the
  // pill for its band; with it, the displayed sensor stays until it leaves.
  const shown = buildSpeedSidebarList(at({ b: 900 }), { bands: BANDS })
  assert.equal(listIds(shown), 'b')

  const keep = new Set(shown.map(item => web.speedSidebarKey(item)))
  assert.equal(listIds(buildSpeedSidebarList(at({ a: 750, b: 900 }), { bands: BANDS, keep })), 'b')
  // Without that history the nearest wins, and stickiness never reaches across
  // a boundary: once b sits in the next band up, both are shown on their own
  // merits.
  assert.equal(listIds(buildSpeedSidebarList(at({ a: 750, b: 900 }), { bands: BANDS })), 'a')
  assert.equal(listIds(buildSpeedSidebarList(at({ a: 750, b: 1300 }), { bands: BANDS, keep })), 'a,b')
})

test('sensors past the last band are outside the bar', () => {
  const list = buildSpeedSidebarList(at({ near: 500, beyond: 12000 }), { bands: BANDS })
  assert.equal(listIds(list), 'near')
})
