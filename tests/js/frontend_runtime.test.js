'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const root = path.resolve(__dirname, '../..')

function storageWith (initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    snapshot: () => Object.fromEntries(values)
  }
}

function loadConfig (storage) {
  const source = fs.readFileSync(path.join(root, 'web/config.js'), 'utf8')
  const context = { localStorage: storage }
  vm.runInNewContext(
    `${source}\nglobalThis.__runtime = { enabled: [...enabled], defaults: [...DEFAULT_ENABLED] }`,
    context
  )
  return context.__runtime
}

test('fresh browser uses the local OSM graph and not external Overpass', () => {
  const storage = storageWith()
  const runtime = loadConfig(storage)
  assert.ok(runtime.enabled.includes('osm_roads'))
  assert.ok(runtime.defaults.includes('osm_roads'))
  assert.equal(runtime.enabled.includes('osm_poc'), false)
  assert.equal(runtime.defaults.includes('osm_poc'), false)
})

test('one-time migration removes the formerly automatic POC activation', () => {
  const storage = storageWith({ layers: JSON.stringify(['osm_poc', 'matrix']) })
  const runtime = loadConfig(storage)
  assert.ok(runtime.enabled.includes('osm_roads'))
  assert.ok(runtime.enabled.includes('matrix'))
  assert.equal(runtime.enabled.includes('osm_poc'), false)
  assert.equal(storage.snapshot().osmLocalRuntimeV1, '1')
})

test('Overpass remains available after a later explicit opt-in', () => {
  const storage = storageWith({
    layers: JSON.stringify(['osm_poc']),
    osmLocalRuntimeV1: '1'
  })
  const runtime = loadConfig(storage)
  assert.equal(runtime.enabled.length, 1)
  assert.equal(runtime.enabled[0], 'osm_poc')
})

test('legacy Overpass refreshes are single-flight and coalesced', async () => {
  const source = fs.readFileSync(path.join(root, 'web/osm-poc.js'), 'utf8')
  const pending = []
  let fetchCount = 0
  const enabled = new Set(['osm_poc'])
  const layer = { key: 'osm_poc', endpoint: '/poc/osm/roads', minZoom: 13 }
  const context = {
    EMPTY_FC: { type: 'FeatureCollection', features: [] },
    enabled,
    map: {
      getZoom: () => 14,
      getBounds: () => ({
        getWest: () => 4.8, getSouth: () => 52.2,
        getEast: () => 4.9, getNorth: () => 52.3
      }),
      getSource: () => ({ setData: () => {} })
    },
    fetch: () => {
      fetchCount++
      return new Promise(resolve => pending.push(resolve))
    },
    setTimeout,
    console,
    userCoords: null,
    movementHeading: null,
    userAccuracy: 0
  }
  vm.runInNewContext(
    `${source}\nsetOsmPocStatus = () => {}; ensureOsmPocInspector = () => ({ classList: { toggle: () => {} } }); globalThis.__poc = { fetchOsmPoc }`,
    context
  )

  context.__poc.fetchOsmPoc(layer)
  context.__poc.fetchOsmPoc(layer)
  context.__poc.fetchOsmPoc(layer)
  assert.equal(fetchCount, 1)

  pending.shift()({ ok: true, json: async () => ({}) })
  await new Promise(resolve => setTimeout(resolve, 5))
  assert.equal(fetchCount, 2)

  context.__poc.fetchOsmPoc(layer)
  assert.equal(fetchCount, 2)
  enabled.delete('osm_poc')
  pending.shift()({ ok: true, json: async () => ({}) })
  await new Promise(resolve => setTimeout(resolve, 5))
  assert.equal(fetchCount, 2)
})

test('pure matcher core loads before the browser adapter', () => {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8')
  assert.ok(html.indexOf('canonical-segment-state.js') < html.indexOf('road-match.js'))
  assert.ok(html.indexOf('road-match-core.js') < html.indexOf('road-match.js'))
})

test('traffic point colours communicate binding reliability instead of raw availability', () => {
  const source = fs.readFileSync(path.join(root, 'web/speed.js'), 'utf8')
  assert.match(source, /CanonicalSegmentState\.pointStatus/)
  assert.match(source, /pointState\.reliable \? speedColor\(kmh\) : '#59636d'/)
  assert.match(source, /binding: pointState\.label/)
  assert.match(source, /box\.textContent = kmh !== null/)
})

test('newer accepted speed points refresh a stale cached OSM road state', () => {
  const fetchSource = require('node:fs').readFileSync(
    require('node:path').resolve(__dirname, '../../web/fetch.js'),
    'utf8'
  )
  const speedSource = require('node:fs').readFileSync(
    require('node:path').resolve(__dirname, '../../web/speed.js'),
    'utf8'
  )
  assert.match(fetchSource, /newerPointSegments\(points, cached\.data\)/)
  assert.match(fetchSource, /osmRoadCache\.delete\(bbox\)[\s\S]*fetchLocalOsmRoads\(layer\)/)
  assert.equal((speedSource.match(/refreshLocalOsmRoadStateForMeasurements\(data\.points \|\| EMPTY_FC\)/g) || []).length, 2)
})

test('WEGGEG lane colour is subordinate to accepted OSM authority', () => {
  const source = fs.readFileSync(path.join(root, 'web/speed.js'), 'utf8')
  const context = { console }
  vm.runInNewContext(
    `${source}\nglobalThis.__buildGradientLanes = buildGradientLanes`,
    context
  )
  const base = {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: [[4.7, 52.2], [4.71, 52.2]] },
    properties: {
      binding_status: 'accepted',
      measurement_stale: false,
      internal_segment_id: 'osm:203:f:0',
      road_authority: 'osm',
      speed_kmh: 72,
      sensors: []
    }
  }

  const accepted = context.__buildGradientLanes({ features: [base] })
  const ambiguous = context.__buildGradientLanes({
    features: [{ ...base, properties: { ...base.properties, binding_status: 'ambiguous' } }]
  })
  const stale = context.__buildGradientLanes({
    features: [{ ...base, properties: { ...base.properties, measurement_stale: true } }]
  })

  assert.equal(accepted.features.length, 1)
  assert.equal(ambiguous.features.length, 0)
  assert.equal(stale.features.length, 0)
})

test('OSM lane fallback receives display-only physical offsets', () => {
  const laneTopology = fs.readFileSync(path.join(root, 'web/lane-topology.js'), 'utf8')
  const speed = fs.readFileSync(path.join(root, 'web/speed.js'), 'utf8')
  const context = { console }
  vm.runInNewContext(
    `${laneTopology}\n${speed}\nglobalThis.__materialize = materializeSpeedLaneGeometry`,
    context
  )
  const feature = {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: [[4.7, 52.2], [4.71, 52.2]] },
    properties: { geometry_source: 'osm_schematic', lane_offset_m: 3.5 }
  }
  const output = context.__materialize({ features: [feature] })

  assert.notDeepEqual(output.features[0].geometry.coordinates, feature.geometry.coordinates)
  assert.deepEqual(feature.geometry.coordinates, [[4.7, 52.2], [4.71, 52.2]])
})

test('canonical path signs supersede legacy corridor selection without lingering', () => {
  const hud = fs.readFileSync(path.join(root, 'web/hud.js'), 'utf8')
  const road = fs.readFileSync(path.join(root, 'web/road-match.js'), 'utf8')
  assert.match(hud, /roadMatchCanonicalRoadSigns/)
  assert.match(hud, /Canonical state is authoritative/)
  assert.match(road, /Presence of a versioned segment_state[\s\S]*nearest-point lookup/)
})

test('driving sign classification fails closed when source bearing is absent', () => {
  const source = fs.readFileSync(path.join(root, 'web/lib.js'), 'utf8')
  const context = { console }
  vm.runInNewContext(`${source}\nglobalThis.__classify = classifyFeature`, context)
  const device = { coords: [5, 52], heading: 0 }
  const ahead = [5, 52.001]

  assert.equal(context.__classify(device, ahead, null, { directed: true }), null)
  assert.ok(context.__classify(device, ahead, 0, { directed: true }))
  assert.equal(context.__classify(device, ahead, 180, { directed: true }), null)
})

test('blank matrix gantries do not create map markers', () => {
  const libSource = fs.readFileSync(path.join(root, 'web/lib.js'), 'utf8')
  const matrixSource = fs.readFileSync(path.join(root, 'web/matrix.js'), 'utf8')
  const context = { console }
  vm.runInNewContext(
    `${libSource}\nglobalThis.__hasValue = matrixLaneHasValue`,
    context
  )

  assert.equal(context.__hasValue({ aspect_type: 'blank', aspects: [] }), false)
  assert.equal(context.__hasValue({ value: 70, aspect_type: 'speed' }), true)
  assert.match(matrixSource, /gantry\.lanes\.some\(matrixLaneHasValue\)/)
})

test('legacy nearest-point speed HUD is gated behind canonical lane pipeline', () => {
  const source = fs.readFileSync(path.join(root, 'web/hud.js'), 'utf8')
  assert.match(source, /!canonicalLanePipelineAvailable\(\)/)
  assert.match(source, /roadSignHudCache\.speedPoints = EMPTY_FC/)
})
