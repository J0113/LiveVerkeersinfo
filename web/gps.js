'use strict'

// ─── GPS & Geolocation Logic ──────────────────────────────────────────────────

function initGPS() {
  const gpsBtn = document.getElementById('gps-btn')
  const recenterBtn = document.getElementById('recenter-btn')
  const compassBtn = document.getElementById('compass-btn')

  // Click handler to toggle GPS state machine
  gpsBtn.addEventListener('click', () => {
    let nextState
    if (gpsState === GPS_STATES.OFF) {
      nextState = GPS_STATES.FOLLOW
    } else if (gpsState === GPS_STATES.FOLLOW) {
      nextState = GPS_STATES.NAVIGATION
    } else {
      nextState = GPS_STATES.OFF
    }
    
    isTrackingSuspended = false
    recenterBtn.classList.add('hidden')
    // This click is a user gesture — the only moment iOS Safari will grant
    // DeviceOrientation (compass) permission, needed for heading-up rotation.
    enableCompass()
    setGPSState(nextState)
  })

  // Re-center button click handler
  recenterBtn.addEventListener('click', () => {
    isTrackingSuspended = false
    recenterBtn.classList.add('hidden')
    updateCameraToUser()
  })

  // Compass button resets bearing and pitch
  compassBtn.addEventListener('click', () => {
    map.easeTo({
      bearing: 0,
      pitch: 0,
      duration: 800
    })

    if (gpsState === GPS_STATES.NAVIGATION) {
      setGPSState(GPS_STATES.FOLLOW)
    }
  })

  // Rotate map event updates compass needle
  map.on('rotate', () => {
    const bearing = map.getBearing()
    document.getElementById('compass-needle').style.setProperty('--bearing', `${-bearing}deg`)
  })

  // Map interaction events to detect user manual manipulation.
  // Guard on e.originalEvent so our own programmatic follow (jumpTo/easeTo)
  // does not trip these — that was silently kicking navigation back to follow
  // and cancelling tracking on every camera update.
  map.on('dragstart', (e) => {
    if (!e.originalEvent) return
    if (gpsState !== GPS_STATES.OFF) {
      isTrackingSuspended = true
      recenterBtn.classList.remove('hidden')
    }
  })

  map.on('rotatestart', (e) => {
    if (!e.originalEvent) return
    // If manually rotating while in dynamic Navigation mode, suspend dynamic auto-rotation
    // but keep following user's center position.
    if (gpsState === GPS_STATES.NAVIGATION) {
      setGPSState(GPS_STATES.FOLLOW)
    }
  })

  // The map is now a driving HUD by default. Start in follow mode immediately;
  // the GPS control still cycles follow → navigation → off when desired.
  setGPSState(GPS_STATES.FOLLOW)
}

function setGPSState(state) {
  gpsState = state
  const gpsBtn = document.getElementById('gps-btn')
  const recenterBtn = document.getElementById('recenter-btn')

  gpsBtn.classList.remove('state-off', 'state-follow', 'state-navigation')

  if (state === GPS_STATES.OFF) {
    gpsBtn.classList.add('state-off')
    recenterBtn.classList.add('hidden')
    isTrackingSuspended = false
    userSpeedMps = null
    userLocationStatus = 'off'
    userCoords = null
    prevCoords = null
    userHeading = null
    movementHeading = null
    lastMovingAt = null
    lastFixAt = null
    userAccuracy = 0
    renderCoords = null
    renderBearing = 0
    lastFollowFrameAt = null
    pendingZoom = null
    stopFollowLoop()
    setFollowPadding(false)
    // Reset the heading-up rotation and 3D tilt back to a flat, north-up map.
    map.easeTo({ bearing: 0, pitch: 0, duration: 500 })
    stopGPSWatcher()
    clearRoadSignHud()
  } else if (state === GPS_STATES.FOLLOW) {
    gpsBtn.classList.add('state-follow')
    if (isTrackingSuspended) recenterBtn.classList.remove('hidden')
    if (!userCoords && userLocationStatus !== 'denied') userLocationStatus = 'waiting'
    pendingZoom = Math.max(map.getZoom(), 15)
    setFollowPadding(true)
    // Follow is north-up; drop any nav pitch/bearing left over from a previous state.
    if (map.getPitch() !== 0) map.easeTo({ pitch: 0, duration: 400 })
    startGPSWatcher()
    startFollowLoop()
    renderRoadSignHud()
  } else if (state === GPS_STATES.NAVIGATION) {
    gpsBtn.classList.add('state-navigation')
    if (isTrackingSuspended) recenterBtn.classList.remove('hidden')
    if (!userCoords && userLocationStatus !== 'denied') userLocationStatus = 'waiting'
    pendingZoom = Math.max(map.getZoom(), 16)
    renderBearing = currentHeading() ?? map.getBearing()
    setFollowPadding(true)
    startGPSWatcher()
    startFollowLoop()
    renderRoadSignHud()
  }
}

// Reserve empty space at the top of the viewport so the followed point (the
// user marker) sits low on screen, leaving the road ahead visible. Padding is
// persistent, so easeTo/jumpTo re-centre respects it.
function setFollowPadding (on) {
  const h = map.getContainer().clientHeight || 0
  const top = on ? Math.round(h * (FOLLOW_BOTTOM_RATIO * 2)) : 0
  map.setPadding({ top, bottom: 0, left: 0, right: 0 })
}

function startFollowLoop () {
  if (followRaf === null) followRaf = requestAnimationFrame(followTick)
}

function stopFollowLoop () {
  if (followRaf !== null) {
    cancelAnimationFrame(followRaf)
    followRaf = null
  }
}

// Interpolate the displayed marker + camera toward the latest GPS fix every
// frame. Exponential smoothing self-corrects toward the newest position, so a
// ~1 Hz fix stream renders as continuous glide rather than per-fix jumps.
function followTick () {
  followRaf = requestAnimationFrame(followTick)
  const nowMs = performance.now()
  const dt = lastFollowFrameAt === null ? 0 : (nowMs - lastFollowFrameAt) / 1000
  lastFollowFrameAt = nowMs
  if (!userCoords) return
  if (!renderCoords) renderCoords = [...userCoords]

  // Keep the displayed heading (and cone) consistent with the moving/stopped
  // policy every frame, so the 10 s compass switch happens without a new fix.
  refreshHeading()

  // Dead-reckon: while moving, advance the smoothing target forward from the
  // last fix along the travel bearing, so the marker glides continuously
  // between ~1 Hz fixes instead of lerping-then-waiting.
  let target = userCoords
  if (userSpeedMps !== null && userSpeedMps > MOVING_SPEED_MPS &&
      movementHeading !== null && lastFixAt !== null) {
    const elapsed = Math.min((Date.now() - lastFixAt) / 1000, DEAD_RECKON_MAX_MS / 1000)
    target = destinationPoint(userCoords, movementHeading, userSpeedMps * elapsed)
  }

  renderCoords[0] += (target[0] - renderCoords[0]) * FOLLOW_POS_LERP
  renderCoords[1] += (target[1] - renderCoords[1]) * FOLLOW_POS_LERP
  // Snap the last sub-metre so a stationary marker settles exactly on target.
  if (Math.abs(target[0] - renderCoords[0]) < 1e-6) renderCoords[0] = target[0]
  if (Math.abs(target[1] - renderCoords[1]) < 1e-6) renderCoords[1] = target[1]

  if (userMarker) userMarker.setLngLat(renderCoords)

  if (gpsState === GPS_STATES.OFF || isTrackingSuspended) return

  // Blue "follow" mode highlights the location but leaves the camera under the
  // user's control — pan and zoom stay free. Only snap to the user on entry or
  // an explicit recenter (a one-shot pendingZoom). Green "navigation" mode
  // re-locks the camera on the user every frame.
  if (gpsState === GPS_STATES.FOLLOW && pendingZoom === null) return

  const cam = { center: renderCoords }
  if (pendingZoom !== null) { cam.zoom = pendingZoom; pendingZoom = null }
  if (gpsState === GPS_STATES.NAVIGATION) {
    const targetBearing = currentHeading()
    if (targetBearing !== null && targetBearing !== undefined) {
      // Deadband: near-straight travel produces tiny target wobble; ignoring it
      // stops the camera micro-oscillating. A real corner is far larger.
      if (Math.abs(angleDiff(renderBearing, targetBearing)) >= BEARING_DEADBAND_DEG) {
        // Frame-rate-independent easing: fraction eased this frame depends on dt,
        // so the rotation feels the same at 30 or 120 fps and never sprints
        // toward each 1 Hz heading step.
        const k = dt > 0 ? 1 - Math.exp(-dt / BEARING_SMOOTH_TAU) : 0
        renderBearing = lerpAngle(renderBearing, targetBearing, k)
      }
      cam.bearing = renderBearing
    }
    cam.pitch = 55
  }
  map.jumpTo(cam)
}

// Heading to display/steer by. While moving — or within STATIONARY_COMPASS_MS of
// the last motion — use the GPS-derived travel bearing; only after standing still
// long enough fall back to the compass.
function currentHeading () {
  const stillMs = lastMovingAt === null ? Infinity : Date.now() - lastMovingAt
  const stoodStill = stillMs >= STATIONARY_COMPASS_MS
  if (!stoodStill && movementHeading !== null) return movementHeading
  if (deviceHeading !== null) return deviceHeading
  return movementHeading // no compass yet — best available
}

// Recompute the exposed heading (used by HUD, cone, nav bearing) from the policy.
function refreshHeading () {
  const h = currentHeading()
  if (h !== null) userHeading = h
  updateHeadingCone()
}

function updateHeadingCone () {
  const coneEl = document.getElementById('user-heading-cone-el')
  if (!coneEl) return
  if (userHeading !== null && userHeading !== undefined) {
    // Marker is screen-fixed, so subtract map bearing to keep the cone pointing
    // at the true compass heading even when the map is rotated.
    coneEl.style.setProperty('--heading', `${userHeading - map.getBearing()}deg`)
    coneEl.classList.add('visible')
  } else {
    coneEl.classList.remove('visible')
  }
}

// Interpolate angle a→b along the shortest arc; handles the 0/360 wrap.
function lerpAngle (a, b, t) {
  const delta = ((b - a + 540) % 360) - 180
  return (a + delta * t + 360) % 360
}

// ─── Device compass (heading-up on mobile) ────────────────────────────────────
// iOS reports GPS heading only at speed; DeviceOrientation gives a live compass
// so the map can rotate to face travel direction even when slow or stopped.
function enableCompass () {
  if (orientationBound) return
  const bind = () => {
    window.addEventListener('deviceorientationabsolute', onDeviceOrientation, true)
    window.addEventListener('deviceorientation', onDeviceOrientation, true)
    orientationBound = true
  }
  const D = window.DeviceOrientationEvent
  if (D && typeof D.requestPermission === 'function') {
    D.requestPermission().then(s => { if (s === 'granted') bind() }).catch(() => {})
  } else if (D) {
    bind()
  }
}

function onDeviceOrientation (e) {
  let h = null
  if (typeof e.webkitCompassHeading === 'number' && !isNaN(e.webkitCompassHeading)) {
    h = e.webkitCompassHeading // iOS: degrees clockwise from true north
  } else if (e.absolute && typeof e.alpha === 'number' && !isNaN(e.alpha)) {
    h = (360 - e.alpha) % 360 // Android/absolute: alpha is counter-clockwise from north
  }
  if (h === null) return
  deviceHeading = h
}

function startGPSWatcher() {
  if (geolocationWatchId !== null || document.visibilityState !== 'visible') return

  if (!navigator.geolocation) {
    console.warn('Geolocation is not supported by this browser.')
    return
  }

  geolocationWatchId = navigator.geolocation.watchPosition(
    onGeolocationUpdate,
    onGeolocationError,
    {
      enableHighAccuracy: true,
      timeout: 12000,
      maximumAge: 0
    }
  )
}

function pauseGPSWatcher () {
  if (geolocationWatchId === null) return
  navigator.geolocation.clearWatch(geolocationWatchId)
  geolocationWatchId = null
}

function stopGPSWatcher() {
  pauseGPSWatcher()
  
  if (userMarker) {
    userMarker.remove()
    userMarker = null
  }
  
  const source = map.getSource('user-accuracy')
  if (source) source.setData(EMPTY_FC)
}

function onGeolocationUpdate(position) {
  const { latitude, longitude, accuracy, heading, speed } = position.coords
  
  prevCoords = userCoords
  userCoords = [longitude, latitude]
  userAccuracy = accuracy || 0
  userSpeedMps = Number.isFinite(speed) ? speed : null
  userLocationStatus = 'ready'
  
  const now = Date.now()
  const dist = prevCoords ? calculateDistance(prevCoords, userCoords) : 0
  const isMoving = (userSpeedMps !== null && userSpeedMps > MOVING_SPEED_MPS) ||
                   dist > MOVING_DIST_M

  // Only feed the travel bearing while actually moving, so a stale GPS heading
  // (or jitter) at standstill can't pollute movementHeading.
  if (isMoving) {
    lastMovingAt = now
    let headingSample = null
    if (heading !== null && !isNaN(heading)) {
      headingSample = heading
    } else if (prevCoords) {
      headingSample = calculateBearing(prevCoords, userCoords)
    }
    if (headingSample !== null) {
      // Angle-aware EMA instead of a sliding-window mean: continuous update with
      // no discrete pops when an old sample drops out of the window — the main
      // source of bearing jitter through corners.
      movementHeading = movementHeading === null
        ? headingSample
        : lerpAngle(movementHeading, headingSample, HEADING_EMA_ALPHA)
    }
  }
  lastFixAt = now
  refreshHeading()

  updateUserMarker()
  updateAccuracyCircle()
  startFollowLoop()   // camera + marker are driven by the rAF follow loop
  renderRoadSignHud()
  fetchRoadSignHud()
}

function onGeolocationError(err) {
  console.warn('[geolocation]', err.message)
  userLocationStatus = err.code === 1 ? 'denied' : 'error'
  renderRoadSignHud()
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    if (gpsState !== GPS_STATES.OFF) pauseGPSWatcher()
    stopFollowLoop()
    for (const controller of Object.values(controllers)) controller?.abort()
    return
  }

  if (gpsState !== GPS_STATES.OFF) { startGPSWatcher(); startFollowLoop() }
  fetchAll()
  if (!document.getElementById('status-body').classList.contains('hidden')) fetchFeedStatus()
})

function updateUserMarker() {
  if (!userCoords) return

  if (!userMarker) {
    const el = document.createElement('div')
    el.className = 'user-marker-container'
    
    const cone = document.createElement('div')
    cone.className = 'user-heading-cone'
    cone.id = 'user-heading-cone-el'
    
    const pulse = document.createElement('div')
    pulse.className = 'user-pulse'
    
    const dot = document.createElement('div')
    dot.className = 'user-dot'
    
    el.appendChild(cone)
    el.appendChild(pulse)
    el.appendChild(dot)
    
    userMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat(userCoords)
      .addTo(map)
  } else {
    userMarker.setLngLat(userCoords)
  }

  updateHeadingCone()
}

function updateAccuracyCircle() {
  const source = map.getSource('user-accuracy')
  if (!userCoords || !source) return
  
  if (userAccuracy > 5) {
    source.setData(makeCirclePolygon(userCoords[0], userCoords[1], userAccuracy))
  } else {
    source.setData(EMPTY_FC)
  }
}

// Recenter after the user manually panned away (tracking suspended). Clearing
// the suspend flag lets the follow loop resume; snap the smoothing origin to
// the current fix so it re-locks immediately instead of gliding across the map.
function updateCameraToUser() {
  if (!userCoords || gpsState === GPS_STATES.OFF) return
  isTrackingSuspended = false
  renderCoords = [...userCoords]
  pendingZoom = gpsState === GPS_STATES.NAVIGATION ? Math.max(map.getZoom(), 16) : Math.max(map.getZoom(), 15)
  startFollowLoop()
}

// ─── Mathematical Geolocation Helpers ────────────────────────────────────────

// Generate a small right-pointing arrow sprite for line symbol layers (travel
// time direction). Drawn to a canvas so we need no glyphs/sprite URL in the
// raster style. Points +x (along line digitisation start→end).
function addArrowImage() {
  if (map.hasImage('tt-arrow')) return
  const s = 24
  const c = document.createElement('canvas')
  c.width = c.height = s
  const x = c.getContext('2d')
  x.beginPath()
  x.moveTo(s * 0.25, s * 0.18)
  x.lineTo(s * 0.85, s * 0.5)
  x.lineTo(s * 0.25, s * 0.82)
  x.closePath()
  x.fillStyle = '#1a1a2a'
  x.strokeStyle = '#ffffff'
  x.lineWidth = 2.5
  x.lineJoin = 'round'
  x.fill()
  x.stroke()
  const img = x.getImageData(0, 0, s, s)
  map.addImage('tt-arrow', img, { pixelRatio: 2 })
}

function makeCirclePolygon(lng, lat, radiusMeters) {
  const steps = 64
  const coordinates = []
  const km = radiusMeters / 1000
  const R = 6378.1 // Earth's radius in km
  const latRad = (lat * Math.PI) / 180
  const lngRad = (lng * Math.PI) / 180
  
  for (let i = 0; i < steps; i++) {
    const theta = (i / steps) * 2 * Math.PI
    const rRad = km / R
    const cLat = Math.asin(
      Math.sin(latRad) * Math.cos(rRad) +
        Math.cos(latRad) * Math.sin(rRad) * Math.cos(theta)
    )
    const cLng =
      lngRad +
      Math.atan2(
        Math.sin(theta) * Math.sin(rRad) * Math.cos(latRad),
        Math.cos(rRad) - Math.sin(latRad) * Math.sin(cLat)
      )
    coordinates.push([(cLng * 180) / Math.PI, (cLat * 180) / Math.PI])
  }
  coordinates.push(coordinates[0])
  return {
    type: 'Feature',
    geometry: {
      type: 'Polygon',
      coordinates: [coordinates]
    },
    properties: {}
  }
}

// calculateDistance / calculateBearing moved to lib.js.
