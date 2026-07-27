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

const HERE = [4.7105, 52.5182]
const NORTHBOUND = { coords: HERE, heading: 0 }
const CONTEXT = { road: 'A9', carriageway: 'R', anchorKm: 12.0, coords: HERE }

function select (features, { context = CONTEXT, device = NORTHBOUND, maxDistanceM = 10000 } = {}) {
  return selectRoadScopedSensors(featureCollection(features), context, device, { maxDistanceM })
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

test('a sensor without a hectometre is excluded', () => {
  const coords = offsetCoords(HERE, { north: 800 })
  assert.equal(select([sensor({ km: null, coords })]).length, 0)
})

test('no anchor hectometre leaves every sensor unselected', () => {
  const context = { ...CONTEXT, anchorKm: null }
  const coords = offsetCoords(HERE, { north: 800 })
  assert.equal(select([sensor({ km: 13.0, coords })], { context }).length, 0)
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
  assert.equal(buildSpeedSidebarList(select([far]), { maxCount: 5 }).length, 1)
})

test('the sidebar merges co-located gantries and keeps the fastest reading', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  const a = sensor({ siteId: 'RWS01_MONIBAS_0091hrl0130ra', km: 13.0, coords, speeds: [96] })
  const b = sensor({ siteId: 'RWS01_MONIBAS_0091vwh0130ra', km: 13.0, coords, speeds: [104] })
  const list = buildSpeedSidebarList(select([a, b]), { maxCount: 5 })
  assert.equal(list.length, 1)
  assert.equal(list[0].fastestKmh, 104)
})

test('on/off-ramp sensors are excluded, unmatched ones are kept', () => {
  const coords = offsetCoords(HERE, { north: 1000 })
  const ramp = sensor({ siteId: 'ramp', km: 13.0, coords, highway: 'motorway_link' })
  const unknown = sensor({ siteId: 'unknown', km: 13.2, coords, highway: null })
  const candidates = select([ramp, unknown])
  // Both displays draw from the same pool, so neither can show ramp traffic as
  // if it were the carriageway we are driving.
  assert.equal(ids(candidates), 'unknown')
  assert.equal(ids(buildSpeedSidebarList(candidates, { maxCount: 5 })), 'unknown')
})

test('the sidebar caps its list', () => {
  const features = [1, 2, 3, 4, 5, 6, 7].map(i => sensor({
    siteId: `s${i}`,
    km: 12 + i * 0.5,
    coords: offsetCoords(HERE, { north: i * 500 }),
  }))
  assert.equal(buildSpeedSidebarList(select(features), { maxCount: 5 }).length, 5)
})
