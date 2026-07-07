'use strict'

// ─── Shared helpers used by both the map page (app.js) and drive HUD (drive.js) ──
// Plain global script (no modules); load BEFORE app.js / drive.js.

// ─── MSI sign rendering (pure DOM + CSS classes in style.css) ───────────────────

// Build one lane cell for an MSI gantry from its aspect_type.
function buildMsiLane (lane) {
  const box = document.createElement('div')
  const aspect = lane.aspect_type || ''
  const val = lane.value

  if ((aspect === 'speedlimit' || (!aspect && val)) && val) {
    box.className = 'msi-lane'
    const disc = document.createElement('div')
    // Red ring only when red_ring=true (mandatory); otherwise plain disc.
    disc.className = 'msi-speed-disc' + (lane.red_ring ? ' ringed' : '')
    disc.textContent = val
    box.appendChild(disc)
  } else if (aspect === 'lane_open') {
    box.className = 'msi-lane lane-open'
    box.textContent = '↓'
  } else if (aspect === 'merge_left' || aspect === 'lane_closed_ahead') {
    // Lane closes ahead → white diagonal "move over" arrow (default left).
    box.className = 'msi-lane lane-merge'
    box.textContent = '↙'
  } else if (aspect === 'merge_right') {
    box.className = 'msi-lane lane-merge'
    box.textContent = '↘'
  } else if (aspect === 'lane_closed') {
    box.className = 'msi-lane lane-closed'
    box.textContent = '✕'
  } else if (aspect === 'restriction_end' || aspect === 'end_of_restriction') {
    box.className = 'msi-lane'
    const disc = document.createElement('div')
    disc.className = 'msi-end-disc'  // white disc with diagonal slash
    box.appendChild(disc)
  } else {
    box.className = 'msi-lane blank'
  }

  if (lane.flashing) addFlashingLamps(box)
  box.title = [lane.road, lane.carriageway, `lane ${lane.lane ?? '?'}`, aspect || val].filter(Boolean).join(' · ')
  return box
}

// RWS flashing lamps: 4 corner dots cycling top pair → off → bottom pair → off.
function addFlashingLamps (box) {
  for (const [pos, phase] of [['tl', 'top'], ['tr', 'top'], ['bl', 'bottom'], ['br', 'bottom']]) {
    const dot = document.createElement('span')
    dot.className = `msi-lamp ${pos} ${phase}`
    box.appendChild(dot)
  }
}

// ─── Speed color scales ─────────────────────────────────────────────────────────

function speedColor (kmh) {
  if (kmh === null || kmh === undefined) return '#444'
  if (kmh <= 0)   return '#cc2200'
  if (kmh <= 30)  return '#ff5500'
  if (kmh <= 60)  return '#ffaa00'
  if (kmh <= 80)  return '#ffdd00'
  if (kmh <= 100) return '#aaee00'
  if (kmh <= 120) return '#00cc44'
  return '#00ffaa'
}

function speedTextColor (kmh) {
  if (kmh !== null && kmh > 60 && kmh <= 100) return '#111'
  return '#fff'
}

// ─── HTML escape ────────────────────────────────────────────────────────────────

function esc (s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// ─── Geo math ───────────────────────────────────────────────────────────────────

// Haversine distance in meters. coords are [lng, lat].
function calculateDistance (coord1, coord2) {
  const [lng1, lat1] = coord1
  const [lng2, lat2] = coord2
  const R = 6371000 // in meters
  const phi1 = (lat1 * Math.PI) / 180
  const phi2 = (lat2 * Math.PI) / 180
  const dPhi = ((lat2 - lat1) * Math.PI) / 180
  const dLam = ((lng2 - lng1) * Math.PI) / 180

  const a =
    Math.sin(dPhi / 2) * Math.sin(dPhi / 2) +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLam / 2) * Math.sin(dLam / 2)
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
  return R * c
}

// Initial bearing (deg, 0-360 from north) from coord1 to coord2. coords [lng, lat].
function calculateBearing (coord1, coord2) {
  const [lng1, lat1] = coord1
  const [lng2, lat2] = coord2
  const dLon = ((lng2 - lng1) * Math.PI) / 180
  const lat1Rad = (lat1 * Math.PI) / 180
  const lat2Rad = (lat2 * Math.PI) / 180

  const y = Math.sin(dLon) * Math.cos(lat2Rad)
  const x =
    Math.cos(lat1Rad) * Math.sin(lat2Rad) -
    Math.sin(lat1Rad) * Math.cos(lat2Rad) * Math.cos(dLon)
  const brng = (Math.atan2(y, x) * 180) / Math.PI
  return (brng + 360) % 360
}

// ─── Drive HUD: direction filtering ─────────────────────────────────────────────

// Smallest signed angular difference a-b, normalized to (-180, 180].
function angleDiff (a, b) {
  return ((a - b + 540) % 360) - 180
}

// Along/cross-track of a feature point relative to the device.
// device = { coords: [lng,lat], heading: deg }, featCoords = [lng, lat].
// along  > 0 = ahead of device, < 0 = behind (in meters).
// cross signed lateral offset (m): > 0 = feature is to the right of travel.
function relativePosition (device, featCoords) {
  const bearingToFeat = calculateBearing(device.coords, featCoords)
  const dist = calculateDistance(device.coords, featCoords)
  const theta = (angleDiff(bearingToFeat, device.heading) * Math.PI) / 180
  return {
    along: dist * Math.cos(theta),
    cross: dist * Math.sin(theta),
    dist,
    bearingToFeat,
  }
}

const DRIVE_DEFAULTS = {
  headingTol: 60,   // deg; opposite carriageway is ~180° off and rejected
  maxAhead: 2000,   // m
  maxBehind: 500,   // m
  maxCross: 50,     // m; rejects opposite carriageway / parallel roads
}

// Decide whether a feature belongs to the road the device is travelling, in the
// travel direction. Returns null (reject) or { status: 'ahead'|'passed', along, cross, dist }.
// featBearing = feature road heading (deg) or null. When opts.directed is false
// the heading gate is disabled (degraded / heading-unknown mode).
function classifyFeature (device, featCoords, featBearing, opts) {
  const o = Object.assign({ directed: true }, DRIVE_DEFAULTS, opts || {})
  const rp = relativePosition(device, featCoords)

  if (Math.abs(rp.cross) > o.maxCross) return null
  if (rp.along > o.maxAhead || rp.along < -o.maxBehind) return null

  if (o.directed && featBearing !== null && featBearing !== undefined) {
    if (Math.abs(angleDiff(featBearing, device.heading)) > o.headingTol) return null
  }

  return {
    status: rp.along >= 0 ? 'ahead' : 'passed',
    along: rp.along,
    cross: rp.cross,
    dist: rp.dist,
  }
}

// Build a forward-biased bbox string "minLon,minLat,maxLon,maxLat" around the
// device, extending further ahead than behind/sideways. Falls back to a symmetric
// box when heading is null. Distances in meters.
function forwardBiasedBbox (coords, heading, opts) {
  const o = Object.assign({ ahead: 2500, behind: 700, side: 700 }, opts || {})
  const [lng, lat] = coords
  const mPerDegLat = 111320
  const mPerDegLon = 111320 * Math.cos((lat * Math.PI) / 180) || 1

  if (heading === null || heading === undefined) {
    const r = o.ahead
    const dLat = r / mPerDegLat
    const dLon = r / mPerDegLon
    return [lng - dLon, lat - dLat, lng + dLon, lat + dLat].map(v => v.toFixed(6)).join(',')
  }

  // Corners of a forward box in along/cross space, rotated into lng/lat.
  const hRad = (heading * Math.PI) / 180
  const sinH = Math.sin(hRad)
  const cosH = Math.cos(hRad)
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity
  for (const along of [-o.behind, o.ahead]) {
    for (const cross of [-o.side, o.side]) {
      // along points toward heading; cross points to the right of heading.
      const east = along * sinH + cross * cosH
      const north = along * cosH - cross * sinH
      const pLng = lng + east / mPerDegLon
      const pLat = lat + north / mPerDegLat
      minLng = Math.min(minLng, pLng); maxLng = Math.max(maxLng, pLng)
      minLat = Math.min(minLat, pLat); maxLat = Math.max(maxLat, pLat)
    }
  }
  return [minLng, minLat, maxLng, maxLat].map(v => v.toFixed(6)).join(',')
}

// "800 m" / "1.2 km"
function formatDistance (m) {
  if (m === null || m === undefined) return ''
  if (m < 1000) return `${Math.round(m / 10) * 10} m`
  return `${(m / 1000).toFixed(1)} km`
}

// Age of an ISO timestamp as "8s ago" / "2m ago" / "3h ago", relative to now.
function formatAge (isoTs) {
  if (!isoTs) return ''
  const ageS = (Date.now() - new Date(isoTs).getTime()) / 1000
  if (ageS < 0) return '0s ago'
  if (ageS < 60) return `${Math.round(ageS)}s ago`
  if (ageS < 3600) return `${Math.round(ageS / 60)}m ago`
  return `${Math.round(ageS / 3600)}h ago`
}
