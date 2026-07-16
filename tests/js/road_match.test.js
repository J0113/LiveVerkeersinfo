'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const {
  roadMatchAngleDiff,
  roadMatchBearing,
  roadMatchBoundedPush,
  roadMatchCommonAhead,
  roadMatchCanonicalFactsFromPath,
  roadMatchDistance,
  roadMatchIsDirectedSuccessor,
  roadMatchIsStationary,
  roadMatchLaneDisplayFeature,
  roadMatchPositionFraction,
  roadMatchProjectSegment,
  roadMatchTrajectoryHeading
} = require('../../web/road-match.js')
global.LaneTopology = require('../../web/lane-topology.js')

test('projection returns metre distance and directed bearing', () => {
  const point = [5.0001, 52.0005]
  const projection = roadMatchProjectSegment(point, [5, 52], [5, 52.001])
  assert.ok(projection.distance > 6 && projection.distance < 8)
  assert.ok(roadMatchAngleDiff(projection.bearing, 0) < 0.01)
  assert.ok(projection.t > 0.45 && projection.t < 0.55)
})

test('position fraction spans a multi-vertex feature instead of one atomic edge', () => {
  const geometry = { type: 'LineString', coordinates: [[5, 52], [5, 52.001], [5, 52.002]] }
  const fraction = roadMatchPositionFraction(geometry, [5, 52.0015])
  assert.ok(fraction > 0.74 && fraction < 0.76)
})

test('opposite direction stays distinguishable on the same geometry', () => {
  assert.ok(roadMatchAngleDiff(
    roadMatchBearing([5, 52], [5, 52.001]),
    roadMatchBearing([5, 52.001], [5, 52])
  ) > 179)
})

test('topology only accepts a directed successor', () => {
  const current = { properties: { from_node_id: 'a', to_node_id: 'b' } }
  const forward = { properties: { from_node_id: 'b', to_node_id: 'c' } }
  const reverse = { properties: { from_node_id: 'c', to_node_id: 'b' } }
  assert.equal(roadMatchIsDirectedSuccessor(current, forward), true)
  assert.equal(roadMatchIsDirectedSuccessor(current, reverse), false)
})

test('GPS history remains bounded to eight fixes', () => {
  const history = []
  for (let i = 0; i < 14; i++) roadMatchBoundedPush(history, i, 8)
  assert.deepEqual(history, [6, 7, 8, 9, 10, 11, 12, 13])
})

test('stationary fixes do not invent a movement heading', () => {
  const now = Date.now()
  const stationary = [
    { coords: [5, 52], timestamp: now - 3000 },
    { coords: [5.00001, 52.00001], timestamp: now }
  ]
  assert.equal(roadMatchIsStationary(stationary, 0.2), true)

  const moving = [
    { coords: [5, 52], timestamp: now - 3000 },
    { coords: [5, 52.001], timestamp: now }
  ]
  assert.equal(roadMatchIsStationary(moving, 15), false)
  assert.ok(roadMatchAngleDiff(roadMatchTrajectoryHeading(moving, null), 0) < 1)
  assert.ok(roadMatchDistance(moving[0].coords, moving[1].coords) > 100)
})

test('ahead fallback stops before an unresolved fork', () => {
  const edge = (id, from, to) => ({
    properties: { internal_segment_id: id, from_node_id: from, to_node_id: to }
  })
  const under = edge('under', 'a', 'b')
  const left = edge('left', 'b', 'c')
  const right = edge('right', 'b', 'd')
  const fork = new Map([['b', [left, right]]])
  assert.deepEqual(roadMatchCommonAhead(under, 4, fork), [])

  const next = edge('next', 'b', 'c')
  const after = edge('after', 'c', 'e')
  const unique = new Map([['b', [next]], ['c', [after]]])
  assert.deepEqual(roadMatchCommonAhead(under, 4, unique), [next, after])
})

test('runtime does not promote corridor-only adjacency before path confirmation', () => {
  const source = require('node:fs').readFileSync(
    require('node:path').resolve(__dirname, '../../web/road-match.js'),
    'utf8'
  )
  assert.match(source, /Until \/roads\/path confirms common_ahead/)
  assert.match(source, /function roadMatchResolvedAhead[\s\S]*return \[\]/)
})

test('HUD selects the first topology-confirmed lane split within the bounded path', () => {
  const feature = (id, count, roles) => ({
    properties: {
      internal_segment_id: id,
      lane_schema: { lane_count: count, roles, attributes: {} }
    }
  })
  const current = feature('under', 3, ['through', 'through', 'through'])
  const unchanged = feature('ahead-1', 3, ['through', 'through', 'through'])
  const split = feature('ahead-2', 4, ['through', 'through', 'unknown', 'exit'])

  assert.equal(
    roadMatchLaneDisplayFeature(current, [unchanged, split]).feature,
    split
  )
})

test('GPS reset explicitly clears stale lane HUD content', () => {
  const source = require('node:fs').readFileSync(
    require('node:path').resolve(__dirname, '../../web/road-match.js'),
    'utf8'
  )
  assert.match(
    source,
    /clearRoadMatchRoles\(\)[\s\S]*renderRoadMatchLaneHud\(null, null\)[\s\S]*renderRoadMatchHud\('waiting'\)/
  )
})

test('lane map cache key changes when the confirmed user lane changes', () => {
  const source = require('node:fs').readFileSync(
    require('node:path').resolve(__dirname, '../../web/road-match.js'),
    'utf8'
  )
  assert.match(
    source,
    /boundedPath\.map\(feature => \[[\s\S]*feature\.properties\.lane_states,[\s\S]*feature\.properties\.user_lane[\s\S]*\]\)/
  )
})

test('canonical sign selection distinguishes two gantries on one segment', () => {
  const feature = {
    properties: {
      internal_segment_id: 'under',
      length_m: 500,
      road_number: 'A4',
      travel_direction: 'forward',
      matrixFacts: [
        { gantry_id: 'passed', offset_m: 80, lane: 1 },
        { gantry_id: 'next', offset_m: 150, lane: 2 },
        { gantry_id: 'next', offset_m: 150, lane: 1 },
        { gantry_id: 'later', offset_m: 320, lane: 1 },
        { gantry_id: null, offset_m: 120, lane: 1 }
      ],
      dripFacts: [
        { source_id: 'passed-drip', offset_m: 50 },
        { source_id: 'next-drip', offset_m: 180 }
      ]
    }
  }
  const api = {
    canonicalFacts: (properties, kind) => kind === 'matrix'
      ? properties.matrixFacts
      : properties.dripFacts
  }
  const result = roadMatchCanonicalFactsFromPath([feature], 100, api)
  assert.equal(result.matrix.data.gantry_id, 'next')
  assert.equal(result.matrix.cls.along, 50)
  assert.deepEqual(result.matrix.data.lanes.map(lane => lane.lane), [1, 2])
  assert.equal(result.drip.data.source_id, 'next-drip')
  assert.equal(result.drip.cls.along, 80)
})
