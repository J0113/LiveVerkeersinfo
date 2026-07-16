'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const LaneTopology = require('../../web/lane-topology.js')

class FakeElement {
  constructor (name, ownerDocument) {
    this.name = name
    this.ownerDocument = ownerDocument
    this.attributes = {}
    this.children = []
    this.textContent = ''
  }

  setAttribute (key, value) { this.attributes[key] = value }
  appendChild (node) { this.children.push(node); return node }
  replaceChildren (...nodes) { this.children = nodes }
}

const fakeDocument = {
  createElementNS: (_namespace, name) => new FakeElement(name, fakeDocument)
}

function target () { return new FakeElement('div', fakeDocument) }

test('loads the shared topology contract before map and HUD adapters', () => {
  const html = fs.readFileSync(path.resolve(__dirname, '../../web/index.html'), 'utf8')
  const topology = html.indexOf('lane-topology.js')
  assert.ok(topology > 0)
  assert.ok(topology < html.indexOf('map.js'))
  assert.ok(topology < html.indexOf('hud.js'))
  assert.ok(topology < html.indexOf('road-match.js'))
})

test('normalizes and lays out five explicitly known through lanes', () => {
  const input = {
    lane_schema: {
      count: 5,
      lanes: Array.from({ length: 5 }, (_, index) => ({
        number: index + 1,
        role: 'through',
        confidence: 0.95
      }))
    }
  }
  const model = LaneTopology.normalize(input)
  const geometry = LaneTopology.layout(model)

  assert.equal(model.count, 5)
  assert.deepEqual(model.lanes.map(lane => lane.number), [1, 2, 3, 4, 5])
  assert.equal(LaneTopology.group(model).main.length, 5)
  assert.ok(geometry.lanes.every((lane, index) => index === 0 || lane.x > geometry.lanes[index - 1].x))
})

test('keeps an explicit 2-to-3 split separate from its two main lanes', () => {
  const model = LaneTopology.normalize({
    lane_schema: {
      count: 3,
      lanes: [
        { number: 1, role: 'through' },
        { number: 2, role: 'through' },
        { number: 3, role: 'exit', destination: 'A44' }
      ]
    }
  })
  const groups = LaneTopology.group(model)
  const geometry = LaneTopology.layout(model)

  assert.deepEqual(groups.main.map(lane => lane.number), [1, 2])
  assert.deepEqual(groups.exit.map(lane => lane.number), [3])
  assert.ok(geometry.lanes[2].x - geometry.lanes[1].x > geometry.lanes[1].x - geometry.lanes[0].x)
  assert.notEqual(geometry.lanes[2].path, geometry.lanes[1].path)
})

test('does not invent an exit or entry for an uncertain extra lane', () => {
  const model = LaneTopology.normalize({
    lane_schema: {
      count: 3,
      lanes: [
        { number: 1, role: 'through' },
        { number: 2, role: 'through' },
        { number: 3, role: 'possible_exit', turn: 'slight_right', confidence: 0.3 }
      ]
    }
  })
  const groups = LaneTopology.group(model)

  assert.equal(model.lanes[2].role, 'unknown')
  assert.deepEqual(groups.unknown.map(lane => lane.number), [3])
  assert.equal(groups.exit.length, 0)
  assert.equal(groups.entry.length, 0)
})

test('preserves per-lane speed and matrix state in the SVG renderer', () => {
  const root = target()
  const rendered = LaneTopology.render(root, {
    lane_schema: {
      count: 2,
      lanes: [{ number: 1, role: 'through' }, { number: 2, role: 'through' }]
    },
    lane_states: [
      { lane_number: 1, speed: 87, matrix: '70', confidence: 0.8 },
      { lane_number: 2, speed: 91, matrix: 'X', confidence: 0.8 }
    ]
  })

  const stateText = rendered.svg.children.flatMap(group => group.children)
    .filter(child => child.attributes.class === 'lane-topology__state')
    .map(child => child.textContent)
  assert.deepEqual(stateText, ['87 km/u · 70', '91 km/u · X'])
  assert.equal(root.children[0], rendered.svg)
})

test('marks the user lane only for explicit high-confidence input', () => {
  const base = {
    lane_schema: {
      count: 2,
      lanes: [{ number: 1, role: 'through' }, { number: 2, role: 'through' }]
    }
  }
  const confirmed = {
    number: 2,
    confidence: 0.8,
    status: 'confirmed',
    method: 'lane_lateral',
    sample_count: 5
  }
  assert.ok(LaneTopology.layout({ ...base, user_lane: confirmed }).lanes[1].isUserLane)
  assert.equal(LaneTopology.layout({
    ...base,
    user_lane: { ...confirmed, confidence: 0.79 }
  }).lanes[1].isUserLane, false)
  assert.equal(LaneTopology.layout({
    ...base,
    user_lane: { number: 2, confidence: 0.99 }
  }).lanes[1].isUserLane, false)
  assert.equal(LaneTopology.layout(base).lanes.some(lane => lane.isUserLane), false)
})

test('map features expose only the explicitly confirmed user lane', () => {
  const featureCollection = {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[5, 52], [5, 52.01]] },
      properties: {
        internal_segment_id: 'segment-a',
        lane_schema: { lane_count: 3, roles: ['through', 'through', 'through'] },
        user_lane: {
          number: 3, status: 'confirmed', method: 'lane_lateral',
          confidence: 1, sample_count: 5
        }
      }
    }]
  }
  const output = LaneTopology.mapFeatures(featureCollection)
  assert.deepEqual(output.features.map(feature => feature.properties.is_user_lane), [false, false, true])
})

test('normalization and layout are stable and do not mutate source input', () => {
  const input = {
    lane_schema: {
      count: 3,
      lanes: [
        { number: 3, role: 'entry' },
        { number: 1, role: 'through' },
        { number: 2, role: 'weave' }
      ]
    },
    lane_states: { 2: { speed: 63, matrix: '↘' } }
  }
  const before = JSON.stringify(input)
  const first = LaneTopology.layout(input)
  const second = LaneTopology.layout(input)

  assert.deepEqual(first, second)
  assert.equal(JSON.stringify(input), before)
  assert.deepEqual(first.lanes.map(lane => lane.number), [1, 2, 3])
})

test('caps malformed lane counts and safely ignores duplicate or invalid lanes', () => {
  const model = LaneTopology.normalize({
    lane_schema: {
      count: 99,
      lanes: [
        { number: 1, role: 'through' },
        { number: 1, role: 'exit' },
        { number: -1, role: 'entry' },
        { number: 34, role: 'exit' }
      ]
    }
  })
  assert.equal(model.count, 32)
  assert.equal(model.lanes[0].role, 'through')
  assert.ok(model.lanes.slice(1).every(lane => lane.role === 'unknown'))
})

test('expands only a bounded directed path into left-to-right map lanes', () => {
  const source = {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: {
        internal_segment_id: 'north',
        lane_schema: {
          count: 3,
          lanes: [
            { number: 1, role: 'through' },
            { number: 2, role: 'through' },
            { number: 3, role: 'exit' }
          ]
        },
        lane_states: [{ lane: 1, speed_kmh: 90, confidence: 0.8 }]
      },
      geometry: { type: 'LineString', coordinates: [[5, 52], [5, 52.001]] }
    }]
  }
  const before = JSON.stringify(source)
  const result = LaneTopology.mapFeatures(source)

  assert.equal(result.features.length, 3)
  assert.ok(result.features[0].geometry.coordinates[0][0] < 5)
  assert.equal(result.features[1].geometry.coordinates[0][0], 5)
  assert.ok(result.features[2].geometry.coordinates[0][0] > 5)
  assert.equal(result.features[0].properties.speed_kmh, 90)
  assert.equal(result.features[2].properties.lane_role, 'exit')
  assert.equal(JSON.stringify(source), before)
})

test('does not generate map lanes when the source has no explicit count', () => {
  const result = LaneTopology.mapFeatures({
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: { internal_segment_id: 'unknown' },
      geometry: { type: 'LineString', coordinates: [[5, 52], [5, 52.001]] }
    }]
  })
  assert.deepEqual(result.features, [])
})

test('consumes the exact compact lane schema returned by the roads API', () => {
  const payload = {
    lane_schema: {
      version: 1,
      lane_count: 5,
      lane_order: 'left_to_right',
      attributes: {
        turn: ['through', 'through', 'through', 'through;slight_right', 'slight_right'],
        destination: [
          'Amsterdam;Schiphol', 'Amsterdam;Schiphol', 'Amsterdam;Schiphol',
          'Amsterdam;Schiphol', 'Zaanstad;Haarlem'
        ]
      },
      unknown: ['change', 'maxspeed', 'access'],
      roles: ['through', 'through', 'through', 'unknown', 'exit']
    },
    lane_states: [
      { lane: 1, speed_kmh: 101, speed_confidence: 0.72 },
      { lane: 5, speed_kmh: 67, speed_confidence: 0.72 }
    ]
  }
  const model = LaneTopology.normalize(payload)

  assert.equal(model.count, 5)
  assert.deepEqual(model.lanes.map(lane => lane.role), [
    'through', 'through', 'through', 'unknown', 'exit'
  ])
  assert.equal(model.lanes[3].turn, 'through;slight_right')
  assert.equal(model.lanes[4].destination, 'Zaanstad;Haarlem')
  assert.equal(model.lanes[0].state.speed, 101)
  assert.equal(model.lanes[4].state.confidence, 0.72)
})

test('shows backend-accepted lane confidence and still hides stale state', () => {
  const model = LaneTopology.normalize({
    lane_schema: { lane_count: 2, roles: ['through', 'through'], attributes: {} },
    lane_states: [
      { lane: 1, speed_kmh: 88, speed_confidence: 0.5 },
      { lane: 2, speed_kmh: 77, speed_confidence: 0.9, speed_stale: true }
    ]
  })
  assert.deepEqual(model.lanes.map(lane => lane.state.speed), [88, null])
})
