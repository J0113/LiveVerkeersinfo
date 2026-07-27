'use strict'

// ─── Manual GPS override for testing (WASD keyboard control) ─────────────────
// Console utility: manualGPS(speedKmh, turnDegPerSec) starts (or re-tunes) a
// simulated GPS feed. W/S always move forward/backward along the current
// heading; A/D steer it, even while stopped. Fixes are pushed through the same
// onGeolocationUpdate() path real GPS uses, so marker/camera/HUD behave
// identically. manualGPS.stop() ends the simulation and resumes real GPS.

const MANUAL_GPS_DEFAULT_SPEED_KMH = 90
const MANUAL_GPS_DEFAULT_TURN_DEG_S = 60
const MANUAL_GPS_TICK_MS = 100

let manualGpsState = null

function manualGPS (speedKmh = MANUAL_GPS_DEFAULT_SPEED_KMH, turnDegPerSec = MANUAL_GPS_DEFAULT_TURN_DEG_S) {
  if (manualGpsState) {
    manualGpsState.speedMps = speedKmh / 3.6
    manualGpsState.turnDegPerSec = turnDegPerSec
    console.log(`[manualGPS] speed=${speedKmh} km/h, turn=${turnDegPerSec} deg/s`)
    return
  }

  if (typeof pauseGPSWatcher !== 'function' || typeof onGeolocationUpdate !== 'function') {
    console.error('[manualGPS] gps.js is not loaded yet.')
    return
  }

  // Stop the real watcher so it can't overwrite simulated fixes.
  pauseGPSWatcher()

  const isTypingTarget = (el) => el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)

  const state = {
    speedMps: speedKmh / 3.6,
    turnDegPerSec,
    heading: Number.isFinite(movementHeading) ? movementHeading
      : Number.isFinite(deviceHeading) ? deviceHeading : 0,
    coords: userCoords ? [...userCoords] : [map.getCenter().lng, map.getCenter().lat],
    keys: { w: false, a: false, s: false, d: false },
  }

  state.onKeyDown = (e) => {
    if (isTypingTarget(e.target)) return
    const k = e.key.toLowerCase()
    if (k in state.keys) state.keys[k] = true
  }
  state.onKeyUp = (e) => {
    const k = e.key.toLowerCase()
    if (k in state.keys) state.keys[k] = false
  }
  window.addEventListener('keydown', state.onKeyDown)
  window.addEventListener('keyup', state.onKeyUp)

  state.timer = setInterval(() => {
    const dt = MANUAL_GPS_TICK_MS / 1000
    if (state.keys.a) state.heading = (state.heading - state.turnDegPerSec * dt + 360) % 360
    if (state.keys.d) state.heading = (state.heading + state.turnDegPerSec * dt) % 360

    let speed = 0
    if (state.keys.w && !state.keys.s) speed = state.speedMps
    else if (state.keys.s && !state.keys.w) speed = -state.speedMps

    if (speed !== 0) {
      state.coords = destinationPoint(
        state.coords,
        speed > 0 ? state.heading : (state.heading + 180) % 360,
        Math.abs(speed) * dt
      )
    }

    onGeolocationUpdate({
      coords: {
        latitude: state.coords[1],
        longitude: state.coords[0],
        accuracy: 5,
        heading: state.heading,
        speed: Math.abs(speed)
      }
    })

    // Force the travel bearing even while stationary (A/D-only turning), so
    // steering in place shows up immediately instead of waiting on movement.
    movementHeading = state.heading
    lastMovingAt = Date.now()
    refreshHeading()
  }, MANUAL_GPS_TICK_MS)

  manualGpsState = state
  console.log(`[manualGPS] active — W/S move, A/D steer (speed=${speedKmh} km/h, turn=${turnDegPerSec} deg/s). manualGPS.stop() to end.`)
}

manualGPS.stop = function () {
  if (!manualGpsState) return
  clearInterval(manualGpsState.timer)
  window.removeEventListener('keydown', manualGpsState.onKeyDown)
  window.removeEventListener('keyup', manualGpsState.onKeyUp)
  manualGpsState = null
  if (gpsState !== GPS_STATES.OFF) startGPSWatcher()
  console.log('[manualGPS] stopped, real GPS resumed')
}
