'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const core = require('../../web/road-match-core.js')

const now = 1_720_000_000_000

function feature (id, from, to, extra = {}) {
  return {
    type: 'Feature',
    properties: {
      internal_segment_id: id,
      from_node_id: from,
      to_node_id: to,
      ...extra
    },
    geometry: { type: 'LineString', coordinates: [[5, 52], [5, 52.001]] }
  }
}

function candidate (road, distance, bearing) {
  return { id: road.properties.internal_segment_id, feature: road, distance, bearing }
}

function movingFix (heading = 0) {
  return {
    fix: { coords: [5, 52.001], heading, accuracy: 8, speedMps: 20, timestamp: now },
    history: [
      { coords: [5, 52], heading, speedMps: 20, timestamp: now - 4_000 },
      { coords: [5, 52.001], heading, speedMps: 20, timestamp: now }
    ]
  }
}

test('strong moving fix acquires a segment with explainable output', () => {
  const road = feature('north', 'a', 'b')
  const drive = movingFix()
  const result = core.matchFix({
    ...drive,
    previousState: core.initialState(),
    candidates: [candidate(road, 2, 0)],
    radius: 30
  })

  assert.equal(result.status, 'ready')
  assert.equal(result.segmentId, 'north')
  assert.equal(result.switchReason, 'acquired-strong')
  assert.ok(result.confidence > 0.8)
  assert.deepEqual(Object.keys(result.scoreBreakdown), [
    'distance', 'heading', 'continuity', 'total', 'radius',
    'sameSegment', 'directedSuccessor', 'gradeConflict'
  ])
})

test('opposite direction is a hard conflict, not a low-scoring candidate', () => {
  const south = feature('south', 'b', 'a')
  const result = core.matchFix({
    ...movingFix(0),
    previousState: core.initialState(),
    candidates: [candidate(south, 1, 180)],
    radius: 30
  })

  assert.equal(result.accepted, false)
  assert.equal(result.segmentId, null)
  assert.equal(result.switchReason, 'opposite-direction-conflict')
  assert.equal(result.alternatives[0].eligible, false)
  assert.equal(result.alternatives[0].rejectionReason, 'opposite-direction')
})

test('stationary fixes cannot acquire a carriageway from compass heading', () => {
  const north = feature('north', 'a', 'b')
  const result = core.matchFix({
    previousState: core.initialState(),
    fix: { coords: [5, 52], heading: 0, speedMps: 0.1, timestamp: now },
    history: [
      { coords: [5, 52], timestamp: now - 4_000 },
      { coords: [5.000001, 52.000001], timestamp: now }
    ],
    candidates: [candidate(north, 1, 0)],
    radius: 30
  })

  assert.equal(result.accepted, false)
  assert.equal(result.state.current, null)
  assert.equal(result.switchReason, 'stationary-no-acquire')
  assert.equal(result.heading, null)
})

test('hysteresis prevents oscillation to a close parallel carriageway', () => {
  const currentRoad = feature('main', 'a', 'b')
  const parallelRoad = feature('parallel', 'x', 'y')
  let state = {
    ...core.initialState(),
    current: { ...candidate(currentRoad, 30, 0), id: 'main', confidence: 0.9 }
  }

  for (let i = 0; i < 6; i++) {
    const result = core.matchFix({
      ...movingFix(),
      previousState: state,
      candidates: [candidate(currentRoad, 30, 0), candidate(parallelRoad, 1, 0)],
      radius: 100
    })
    assert.equal(result.segmentId, 'main')
    assert.equal(result.switchReason, 'held-hysteresis')
    state = result.state
  }
})

test('grade-separated crossing is rejected without directed topology', () => {
  const bridge = feature('bridge', 'a', 'b', { bridge: 'yes', layer: 1 })
  const tunnel = feature('tunnel', 'x', 'y', { tunnel: 'yes', layer: -1 })
  const previous = {
    ...core.initialState(),
    current: { ...candidate(bridge, 6, 0), id: 'bridge', confidence: 0.9 }
  }
  const result = core.matchFix({
    ...movingFix(),
    previousState: previous,
    candidates: [candidate(bridge, 6, 0), candidate(tunnel, 0.5, 0)],
    radius: 30
  })

  assert.equal(result.segmentId, 'bridge')
  const rejected = result.alternatives.find(item => item.id === 'tunnel')
  assert.equal(rejected.eligible, false)
  assert.equal(rejected.rejectionReason, 'grade/topology-conflict')
  assert.equal(rejected.scoreBreakdown.gradeConflict, true)
})

test('directed successor needs two confirmations before switching', () => {
  const first = feature('first', 'a', 'b')
  const next = feature('next', 'b', 'c')
  let state = {
    ...core.initialState(),
    current: { ...candidate(first, 4, 0), id: 'first', confidence: 0.9 }
  }

  const pending = core.matchFix({
    ...movingFix(), previousState: state,
    candidates: [candidate(next, 2, 0)], radius: 30
  })
  assert.equal(pending.accepted, false)
  assert.equal(pending.switchReason, 'connected-switch-pending')

  const switched = core.matchFix({
    ...movingFix(), previousState: pending.state,
    candidates: [candidate(next, 2, 0)], radius: 30
  })
  assert.equal(switched.segmentId, 'next')
  assert.equal(switched.switchReason, 'switched-connected')
})

test('same replay input produces byte-for-byte equivalent output', () => {
  const road = feature('repeatable', 'a', 'b')
  const input = {
    ...movingFix(),
    previousState: core.initialState(),
    candidates: [candidate(road, 2, 0)],
    radius: 30
  }
  assert.deepEqual(core.matchFix(input), core.matchFix(input))
})

test('candidate work is bounded before ranking', () => {
  const candidates = Array.from({ length: 100 }, (_, index) =>
    candidate(feature(`road-${index}`, `${index}`, `${index + 1}`), index + 1, 0)
  )
  const result = core.matchFix({
    ...movingFix(),
    previousState: core.initialState(),
    candidates,
    radius: 200,
    options: { maxCandidates: 16, maxAlternatives: 16 }
  })
  assert.equal(result.alternatives.length, 16)
})

test('reference fixture replay produces zero wrong carriageway or direction matches', () => {
  const fixture = JSON.parse(fs.readFileSync(
    path.resolve(__dirname, '../fixtures/matching_cases.geojson'),
    'utf8'
  ))
  let acceptedFixes = 0

  for (const scenario of fixture.scenarios) {
    const roads = fixture.features.filter(feature => feature.properties.scenario_id === scenario.id)
    let state = core.initialState()
    const history = []

    for (const rawFix of scenario.fixes) {
      const fix = {
        coords: rawFix.coordinates,
        heading: rawFix.heading,
        accuracy: rawFix.accuracy_m,
        speedMps: rawFix.speed_mps,
        timestamp: rawFix.timestamp_ms
      }
      core.boundedPush(history, fix, 8)
      const radius = Math.max(24, Math.min(100, (rawFix.accuracy_m || 8) * 1.7 + 12))
      const candidates = roads.flatMap(road => {
        let nearest = null
        const lines = road.geometry.type === 'MultiLineString'
          ? road.geometry.coordinates
          : [road.geometry.coordinates]
        for (const line of lines) {
          for (let index = 0; index < line.length - 1; index++) {
            const projection = core.projectSegment(fix.coords, line[index], line[index + 1])
            if (!nearest || projection.distance < nearest.distance) nearest = projection
          }
        }
        return nearest && nearest.distance <= radius
          ? [{
              id: road.properties.internal_segment_id,
              feature: road,
              distance: nearest.distance,
              bearing: nearest.bearing
            }]
          : []
      })
      const result = core.matchFix({ previousState: state, fix, history, candidates, radius })
      state = result.state

      const publicStatus = result.accepted ? 'accepted' : 'ambiguous'
      assert.ok(rawFix.allowed_statuses.includes(publicStatus),
        `${scenario.id}@${rawFix.timestamp_ms}: unexpected ${publicStatus}`)
      if (result.accepted) {
        acceptedFixes++
        assert.ok(rawFix.expected_segment_ids.includes(result.segmentId),
          `${scenario.id}@${rawFix.timestamp_ms}: wrong ${result.segmentId}`)
      }
    }
  }

  assert.ok(acceptedFixes > 0, 'fixture replay should prove at least one accepted match')
})
