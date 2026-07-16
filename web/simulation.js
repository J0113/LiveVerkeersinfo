'use strict'

// Desk-test route over two actually connected northbound A4 segments in the
// active Noord-Holland OSM graph (verified 2026-07-15).  It deliberately feeds
// the normal geolocation handler: no alternate matcher or display path exists.
var simulationActive = false
var simulationTimer = null
var simulationSampleIndex = 0
var simulationLaneSampleCount = 0

const SIMULATION_ROUTE = Object.freeze([
  Object.freeze([4.6988353, 52.2685767]),
  Object.freeze([4.7022850, 52.2709634]),
  Object.freeze([4.7134965, 52.2787813]),
  Object.freeze([4.7257173, 52.2873214])
])
const SIMULATION_SPEED_MPS = 27.8 // about 100 km/h, as reported to the app
const SIMULATION_TICK_MS = 500
const SIMULATION_TIME_SCALE = 6 // complete the 2.77 km desk test in ~17 seconds
const SIMULATION_ACCURACY_M = 6
const SIMULATION_USER_LANE = 3
const SIMULATION_LANE_CONFIRMATION_SAMPLES = 5

function initSimulationDrive () {
  const button = document.getElementById('simulation-btn')
  if (!button || button.dataset.ready === 'true') return
  button.dataset.ready = 'true'
  button.addEventListener('click', () => {
    if (simulationActive) stopSimulationDrive()
    else startSimulationDrive()
  })
  renderSimulationDriveState('idle')
}

function startSimulationDrive () {
  if (simulationActive) return
  if (simulationTimer !== null) clearInterval(simulationTimer)
  simulationTimer = null
  if (gpsState !== GPS_STATES.OFF) setGPSState(GPS_STATES.OFF)
  simulationActive = true
  pauseGPSWatcher()
  setGPSState(GPS_STATES.NAVIGATION)

  const stepM = SIMULATION_SPEED_MPS * (SIMULATION_TICK_MS / 1000) *
    SIMULATION_TIME_SCALE
  const samples = buildSimulationSamples(SIMULATION_ROUTE, stepM)
  simulationSampleIndex = 0
  simulationLaneSampleCount = 0
  renderSimulationDriveState('running')

  const emit = () => {
    if (!simulationActive || document.visibilityState !== 'visible') return
    const sample = samples[simulationSampleIndex]
    if (!sample) {
      clearInterval(simulationTimer)
      simulationTimer = null
      renderSimulationDriveState('complete')
      return
    }
    simulationLaneSampleCount++
    onGeolocationUpdate(simulationPosition(sample, Date.now()))
    simulationSampleIndex++
  }
  emit()
  simulationTimer = setInterval(emit, SIMULATION_TICK_MS)
}

function stopSimulationDrive () {
  if (simulationTimer !== null) clearInterval(simulationTimer)
  simulationTimer = null
  simulationActive = false
  simulationSampleIndex = 0
  simulationLaneSampleCount = 0
  renderSimulationDriveState('idle')
  if (gpsState !== GPS_STATES.OFF) setGPSState(GPS_STATES.OFF)
}

function renderSimulationDriveState (state) {
  const button = typeof document === 'undefined'
    ? null
    : document.getElementById('simulation-btn')
  const status = typeof document === 'undefined'
    ? null
    : document.getElementById('simulation-status')
  if (button) {
    button.classList.toggle('simulation-active', state !== 'idle')
    button.setAttribute('aria-pressed', String(state !== 'idle'))
    const label = state === 'idle' ? 'Start simulatierit op de A4' : 'Stop simulatierit'
    button.title = label
    button.setAttribute('aria-label', label)
  }
  if (status) {
    status.classList.toggle('hidden', state === 'idle')
    status.textContent = state === 'complete'
      ? 'Simulatierit A4 voltooid · klik stop om te resetten'
      : 'Simulatierit A4 · circa 100 km/h'
  }
}

function buildSimulationSamples (route, stepM) {
  if (!Array.isArray(route) || route.length < 2 || !(stepM > 0)) return []
  const samples = []
  for (let index = 0; index < route.length - 1; index++) {
    const start = route[index]
    const end = route[index + 1]
    const length = simulationDistance(start, end)
    const count = Math.max(1, Math.ceil(length / stepM))
    const heading = simulationBearing(start, end)
    for (let part = 0; part < count; part++) {
      const fraction = part / count
      samples.push({
        coords: [
          start[0] + (end[0] - start[0]) * fraction,
          start[1] + (end[1] - start[1]) * fraction
        ],
        heading,
        speedMps: SIMULATION_SPEED_MPS,
        accuracy: SIMULATION_ACCURACY_M
      })
    }
  }
  const last = route[route.length - 1]
  const previous = route[route.length - 2]
  samples.push({
    coords: [...last],
    heading: simulationBearing(previous, last),
    speedMps: SIMULATION_SPEED_MPS,
    accuracy: SIMULATION_ACCURACY_M
  })
  return samples
}

function simulationPosition (sample, timestamp) {
  return {
    coords: {
      latitude: sample.coords[1],
      longitude: sample.coords[0],
      accuracy: sample.accuracy,
      heading: sample.heading,
      speed: sample.speedMps,
      altitude: null,
      altitudeAccuracy: null
    },
    timestamp
  }
}

// Explicit synthetic ground truth for the desk test. This is deliberately not
// derived from the simulated 6 m GPS accuracy and is never available outside
// an active simulation. Production lane highlighting remains fail-closed.
function simulationUserLaneObservation () {
  return buildSimulationUserLaneObservation(
    simulationLaneSampleCount,
    simulationActive
  )
}

function buildSimulationUserLaneObservation (sampleCount, active = true) {
  if (!active || sampleCount < SIMULATION_LANE_CONFIRMATION_SAMPLES) return null
  return {
    number: SIMULATION_USER_LANE,
    status: 'confirmed',
    method: 'lane_lateral',
    confidence: 1,
    sample_count: sampleCount,
    source: 'simulation_ground_truth'
  }
}

function simulationDistance (a, b) {
  const latitude = (a[1] + b[1]) * Math.PI / 360
  const dx = (b[0] - a[0]) * 111320 * Math.cos(latitude)
  const dy = (b[1] - a[1]) * 110540
  return Math.hypot(dx, dy)
}

function simulationBearing (a, b) {
  const latitude = (a[1] + b[1]) * Math.PI / 360
  const dx = (b[0] - a[0]) * Math.cos(latitude)
  const dy = b[1] - a[1]
  return (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    SIMULATION_ROUTE,
    SIMULATION_SPEED_MPS,
    SIMULATION_USER_LANE,
    SIMULATION_LANE_CONFIRMATION_SAMPLES,
    buildSimulationSamples,
    buildSimulationUserLaneObservation,
    simulationBearing,
    simulationDistance,
    simulationPosition
  }
}
