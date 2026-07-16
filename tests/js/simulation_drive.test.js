'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const {
  SIMULATION_ROUTE,
  SIMULATION_SPEED_MPS,
  SIMULATION_USER_LANE,
  SIMULATION_LANE_CONFIRMATION_SAMPLES,
  buildSimulationSamples,
  buildSimulationUserLaneObservation,
  simulationBearing,
  simulationDistance,
  simulationPosition
} = require('../../web/simulation.js')

test('A4 simulation creates a bounded motorway trajectory', () => {
  const routeBefore = JSON.stringify(SIMULATION_ROUTE)
  const samples = buildSimulationSamples(SIMULATION_ROUTE, 83.4)

  assert.ok(samples.length > 25 && samples.length < 50)
  assert.deepEqual(samples[0].coords, SIMULATION_ROUTE[0])
  assert.deepEqual(samples.at(-1).coords, SIMULATION_ROUTE.at(-1))
  assert.equal(JSON.stringify(SIMULATION_ROUTE), routeBefore)

  for (const sample of samples) {
    assert.ok(sample.heading >= 0 && sample.heading < 360)
    assert.equal(sample.speedMps, SIMULATION_SPEED_MPS)
    assert.equal(sample.accuracy, 6)
  }
  for (let index = 1; index < samples.length; index++) {
    assert.ok(simulationDistance(samples[index - 1].coords, samples[index].coords) <= 84)
  }
})

test('simulation confirms explicit lane 3 only after five samples', () => {
  assert.equal(SIMULATION_USER_LANE, 3)
  assert.equal(SIMULATION_LANE_CONFIRMATION_SAMPLES, 5)
  assert.equal(buildSimulationUserLaneObservation(4, true), null)
  assert.equal(buildSimulationUserLaneObservation(8, false), null)
  assert.deepEqual(buildSimulationUserLaneObservation(5, true), {
    number: 3,
    status: 'confirmed',
    method: 'lane_lateral',
    confidence: 1,
    sample_count: 5,
    source: 'simulation_ground_truth'
  })
})

test('simulation route travels north-east and spans about 2.8 kilometres', () => {
  let distance = 0
  for (let index = 1; index < SIMULATION_ROUTE.length; index++) {
    distance += simulationDistance(SIMULATION_ROUTE[index - 1], SIMULATION_ROUTE[index])
  }

  const bearing = simulationBearing(SIMULATION_ROUTE[0], SIMULATION_ROUTE.at(-1))
  assert.ok(distance > 2700 && distance < 2850)
  assert.ok(bearing > 30 && bearing < 50)
})

test('synthetic fix follows the GeolocationPosition contract consumed by GPS', () => {
  const sample = buildSimulationSamples(SIMULATION_ROUTE, 100)[0]
  const position = simulationPosition(sample, 123456)

  assert.equal(position.coords.longitude, sample.coords[0])
  assert.equal(position.coords.latitude, sample.coords[1])
  assert.equal(position.coords.speed, SIMULATION_SPEED_MPS)
  assert.equal(position.coords.heading, sample.heading)
  assert.equal(position.coords.altitude, null)
  assert.equal(position.timestamp, 123456)
})
