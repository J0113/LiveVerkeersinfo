'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const state = require('../../web/canonical-segment-state.js')

const NOW = Date.parse('2026-07-16T12:00:00Z')

test('canonical speed takes precedence and is flattened consistently for MapLibre', () => {
  const properties = {
    speed_kmh: 12,
    speed_method: 'measured',
    segment_state: {
      version: 1,
      speed: {
        speed_kmh: 84,
        method: 'interpolated',
        source: 'NDW',
        observed_at: '2026-07-16T11:59:00Z',
        valid_until: '2026-07-16T12:04:00Z',
        confidence: 0.82,
        sample_count: 2,
        stale: false
      }
    }
  }
  const speed = state.speedState(properties, NOW)
  assert.equal(speed.value, 84)
  assert.equal(speed.method, 'interpolated')
  assert.equal(speed.usable, true)
  assert.equal(speed.sampleCount, 2)
  assert.equal(state.provenanceLabel(speed), 'NDW')
  assert.deepEqual(
    Object.fromEntries(Object.entries(state.flattenProperties(properties, NOW)).filter(([key]) =>
      ['speed_kmh', 'speed_method', 'speed_usable', 'speed_stale'].includes(key))),
    { speed_kmh: 84, speed_method: 'interpolated', speed_stale: false, speed_usable: true }
  )
})

test('canonical derived confidence remains visible while stale state does not', () => {
  const low = state.speedState({ segment_state: { speed: {
    speed_kmh: 70, method: 'propagated', confidence: 0.59,
    observed_at: '2026-07-16T11:59:00Z', valid_until: '2026-07-16T12:04:00Z'
  } } }, NOW)
  assert.equal(low.value, 70)
  assert.equal(low.usable, true)

  const stale = state.speedState({ segment_state: { speed: {
    speed_kmh: 70, method: 'measured', confidence: 0.9,
    observed_at: '2026-07-16T11:00:00Z', valid_until: '2026-07-16T11:05:00Z'
  } } }, NOW)
  assert.equal(stale.stale, true)
  assert.equal(stale.usable, false)
})

test('fresh canonical backend states are usable at accepted backend confidence', () => {
  const now = Date.parse('2026-07-16T12:00:00Z')
  const canonical = state.speedState({
    segment_state: {
      speed: {
        speed_kmh: 84,
        method: 'measured',
        confidence: 0.5,
        observed_at: '2026-07-16T11:59:00Z',
        valid_until: '2026-07-16T12:09:00Z',
        stale: false
      }
    }
  }, now)
  const propagated = state.speedState({
    segment_state: {
      speed: {
        speed_kmh: 84,
        method: 'propagated',
        confidence: 0.35,
        observed_at: '2026-07-16T11:59:00Z',
        valid_until: '2026-07-16T12:09:00Z',
        stale: false
      }
    }
  }, now)
  assert.equal(canonical.usable, true)
  assert.equal(propagated.usable, true)
})

test('legacy flattened speed remains supported during migration', () => {
  const speed = state.speedState({
    speed_kmh: 91,
    speed_method: 'measured',
    speed_confidence: 0.9,
    speed_source: 'NDW',
    speed_sample_count: 3,
    speed_observed_at: '2026-07-16T11:59:00Z',
    speed_valid_until: '2026-07-16T12:04:00Z'
  }, NOW)
  assert.equal(speed.value, 91)
  assert.equal(speed.usable, true)
  assert.equal(speed.sampleCount, 3)
  assert.equal(state.provenanceLabel(speed), 'NDW')
})

test('canonical road-sign facts require confidence and temporal validity', () => {
  const realNow = Date.now
  Date.now = () => NOW
  try {
    const properties = { segment_state: { version: 1, matrix: [
      { source_id: 'fresh', lane: 1, confidence: 0.8, observed_at: '2026-07-16T11:59:00Z', valid_until: '2026-07-16T12:04:00Z' },
      { source_id: 'weak', lane: 2, confidence: 0.4, observed_at: '2026-07-16T11:59:00Z', valid_until: '2026-07-16T12:04:00Z' },
      { source_id: 'stale', lane: 3, confidence: 0.9, observed_at: '2026-07-16T11:00:00Z', valid_until: '2026-07-16T11:05:00Z' }
    ] } }
    assert.deepEqual(state.canonicalFacts(properties, 'matrix').map(f => f.source_id), ['fresh'])
  } finally {
    Date.now = realNow
  }
})

test('newer accepted A5 point invalidates only an older canonical road state', () => {
  const now = Date.parse('2026-07-16T11:15:00Z')
  const road = {
    type: 'FeatureCollection',
    features: [{
      properties: {
        internal_segment_id: 'a5-left-km-7.4',
        speed_kmh: 102,
        speed_method: 'measured',
        speed_confidence: 0.92,
        speed_observed_at: '2026-07-16T11:13:00Z',
        speed_valid_until: '2026-07-16T11:23:00Z'
      }
    }]
  }
  const point = (status, measuredAt, stale = false) => ({
    type: 'Feature',
    properties: {
      internal_segment_id: 'a5-left-km-7.4',
      binding_status: status,
      measured_at: measuredAt,
      measurement_stale: stale
    }
  })

  assert.deepEqual(state.newerPointSegments({
    type: 'FeatureCollection',
    features: [point('accepted', '2026-07-16T11:14:00Z')]
  }, road, now), ['a5-left-km-7.4'])
  assert.deepEqual(state.newerPointSegments({
    type: 'FeatureCollection',
    features: [point('accepted', '2026-07-16T11:13:00Z')]
  }, road, now), [])
  assert.deepEqual(state.newerPointSegments({
    type: 'FeatureCollection',
    features: [point('ambiguous', '2026-07-16T11:14:00Z')]
  }, road, now), [])
  assert.deepEqual(state.newerPointSegments({
    type: 'FeatureCollection',
    features: [point('accepted', '2026-07-16T11:14:00Z', true)]
  }, road, now), [])
})

test('traffic points only receive reliable colour semantics when accepted and fresh', () => {
  assert.equal(state.pointStatus({
    binding_status: 'accepted', measured_at: '2026-07-16T11:59:00Z'
  }, NOW).reliable, true)
  assert.equal(state.pointStatus({
    binding_status: 'ambiguous', measured_at: '2026-07-16T11:59:00Z'
  }, NOW).reliable, false)
  assert.equal(state.pointStatus({
    binding_status: 'accepted', measured_at: '2026-07-16T11:00:00Z'
  }, NOW).reliable, false)
  assert.equal(state.pointStatus({
    binding_status: 'accepted', measurement_stale: false, measured_at: '2026-07-16T12:02:00Z'
  }, NOW).reliable, false)
})
