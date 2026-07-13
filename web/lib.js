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

// Group the lane-level matrix feed into physical gantries. Kept shared so the
// map HUD and the dedicated /drive view use the same lane ordering and identity.
function groupMatrixGantries (fc) {
  const gantries = new Map()
  for (const feature of (fc?.features || [])) {
    if (!feature.geometry || !feature.properties) continue
    const p = feature.properties
    const key = `${p.road ?? ''}|${p.km ?? ''}|${p.carriageway ?? ''}`
    if (!gantries.has(key)) {
      gantries.set(key, {
        coords: feature.geometry.coordinates,
        bearing: p.bearing,
        road: p.road,
        km: p.km,
        carriageway: p.carriageway,
        lanesByNumber: new Map()
      })
    }
    const lanes = gantries.get(key).lanesByNumber
    const laneKey = p.lane ?? p.uuid
    const current = lanes.get(laneKey)
    // Defensive de-duplication: feeds occasionally contain two states for one
    // physical lane. Prefer the most recently timestamped state.
    if (!current || String(p.ts_state || '') >= String(current.ts_state || '')) lanes.set(laneKey, p)
  }
  for (const gantry of gantries.values()) {
    gantry.lanes = [...gantry.lanesByNumber.values()]
      .sort((a, b) => (a.lane ?? 0) - (b.lane ?? 0))
    delete gantry.lanesByNumber
  }
  return [...gantries.values()]
}

function matrixLaneHasValue (lane) {
  if (!lane) return false
  if (lane.value !== null && lane.value !== undefined && lane.value !== '') return true
  if (lane.aspect_type && lane.aspect_type !== 'blank') return true
  return Array.isArray(lane.aspects) && lane.aspects.some(aspect =>
    aspect && (
      (aspect.type && aspect.type !== 'blank') ||
      (aspect.value !== null && aspect.value !== undefined && aspect.value !== '')
    ))
}

function dripHasValue (properties) {
  if (!properties) return false
  if (properties.working_status && String(properties.working_status).toLowerCase() !== 'working') return false
  return Boolean(properties.image_b64 || String(properties.display_text || '').trim())
}

// Select exactly one upcoming gantry and one upcoming DRIP/VMS. A heading is
// mandatory: showing an opposite-carriageway sign is worse than hiding the HUD
// until the device has established direction of travel.
function selectUpcomingRoadSigns (matrixFc, dripFc, device, maxDistanceM) {
  const maxAhead = maxDistanceM ?? 2000
  if (!device || !Array.isArray(device.coords) || !Number.isFinite(device.heading)) {
    return { matrix: null, drip: null }
  }

  const classifyAhead = (coords, bearing) => {
    const cls = classifyFeature(device, coords, bearing, {
      directed: true,
      maxAhead,
      maxBehind: 0,
      maxCross: 60
    })
    return cls?.status === 'ahead' ? cls : null
  }

  let matrix = null
  for (const gantry of groupMatrixGantries(matrixFc)) {
    if (!gantry.lanes.some(matrixLaneHasValue)) continue
    const cls = classifyAhead(gantry.coords, gantry.bearing)
    if (cls && (!matrix || cls.along < matrix.cls.along)) matrix = { data: gantry, cls }
  }

  let drip = null
  for (const feature of (dripFc?.features || [])) {
    if (!feature.geometry || !dripHasValue(feature.properties)) continue
    const cls = classifyAhead(feature.geometry.coordinates, feature.properties?.bearing)
    if (cls && (!drip || cls.along < drip.cls.along)) {
      drip = { data: feature.properties, coords: feature.geometry.coordinates, cls }
    }
  }

  return { matrix, drip }
}

// Shortest planar distance from a WGS84 point to a LineString/MultiLineString.
// At HUD-scale distances the local metre projection is both stable and more
// than accurate enough to decide which 3.5 m lane centreline contains the GPS.
function distanceToLineGeometry (geometry, target) {
  if (!geometry || !Array.isArray(target)) return Infinity
  const lines = geometry.type === 'LineString'
    ? [geometry.coordinates]
    : geometry.type === 'MultiLineString' ? geometry.coordinates : []
  const latScale = 110540
  const lonScale = 111320 * Math.cos(target[1] * Math.PI / 180)
  let bestSq = Infinity

  for (const line of lines) {
    if (!Array.isArray(line) || line.length < 2) continue
    for (let i = 0; i < line.length - 1; i++) {
      const ax = (line[i][0] - target[0]) * lonScale
      const ay = (line[i][1] - target[1]) * latScale
      const bx = (line[i + 1][0] - target[0]) * lonScale
      const by = (line[i + 1][1] - target[1]) * latScale
      const dx = bx - ax
      const dy = by - ay
      const denom = dx * dx + dy * dy
      const t = denom ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denom)) : 0
      const px = ax + t * dx
      const py = ay + t * dy
      bestSq = Math.min(bestSq, px * px + py * py)
    }
  }
  return Math.sqrt(bestSq)
}

// Pick the lane centreline nearest to the current GPS position. When a travel
// heading exists, reject measurements from the opposite carriageway first.
function selectCurrentLaneSpeed (laneFc, device, maxDistanceM) {
  if (!device || !Array.isArray(device.coords)) return null
  const maxDistance = maxDistanceM ?? 20
  const hasHeading = Number.isFinite(device.heading)

  let nearest = null
  for (const feature of (laneFc?.features || [])) {
    if (!feature.geometry || !feature.properties) continue
    const bearing = Number(feature.properties.bearing)
    if (hasHeading && Number.isFinite(bearing) && Math.abs(angleDiff(bearing, device.heading)) > 60) continue
    const distance = distanceToLineGeometry(feature.geometry, device.coords)
    if (distance <= maxDistance && (!nearest || distance < nearest.distance)) {
      nearest = { data: feature.properties, distance }
    }
  }
  return nearest
}

function matrixLaneBlocksRecommendation (lane) {
  if (!lane) return false
  const blockedTypes = new Set([
    'lane_closed', 'lane_closed_ahead', 'merge_left', 'merge_right',
    'red_cross', 'cross', 'closed'
  ])
  if (blockedTypes.has(String(lane.aspect_type || '').toLowerCase())) return true
  return Array.isArray(lane.aspects) && lane.aspects.some(aspect =>
    blockedTypes.has(String(aspect?.type || '').toLowerCase())
  )
}

// Compare the matched current lane with adjacent lanes on the same physical
// WEGGEG section. This returns traffic information, not a manoeuvre command;
// the caller applies dwell time, hysteresis, and lane-change cooldown.
function findLaneSpeedRecommendation (laneFc, currentSelection, matrixSelection, device, opts) {
  const current = currentSelection?.data
  if (!current?.source_id || !device || !Number.isFinite(device.heading)) return null

  const o = Object.assign({
    nowMs: Date.now(),
    maxAgeMs: 120_000,
    minDeltaKmh: 12,
    minPercent: 0.15,
    minTargetKmh: 25,
    maxStdDev: 30
  }, opts || {})
  const currentLane = Number(current.lane)
  const currentKmh = Number(current.speed_kmh)
  if (!Number.isFinite(currentLane) || !Number.isFinite(currentKmh) || currentKmh < 5) return null

  const isFresh = measuredAt => {
    const measuredMs = Date.parse(measuredAt || '')
    return Number.isFinite(measuredMs) && o.nowMs - measuredMs <= o.maxAgeMs && measuredMs - o.nowMs < 30_000
  }
  if (!isFresh(current.measured_at)) return null

  const matrix = matrixSelection?.data
  const matrixApplies = matrix && (
    !matrix.road || !current.road || String(matrix.road) === String(current.road)
  )
  const blockedMatrixLanes = new Set(
    matrixApplies
      ? (matrix.lanes || []).filter(matrixLaneBlocksRecommendation).map(lane => Number(lane.lane))
      : []
  )

  let best = null
  for (const feature of (laneFc?.features || [])) {
    const candidate = feature?.properties
    if (!candidate || candidate.source_id !== current.source_id) continue
    const targetLane = Number(candidate.lane)
    const targetKmh = Number(candidate.speed_kmh)
    if (!Number.isFinite(targetLane) || Math.abs(targetLane - currentLane) !== 1) continue
    if (!Number.isFinite(targetKmh) || targetKmh < o.minTargetKmh || !isFresh(candidate.measured_at)) continue
    if (blockedMatrixLanes.has(targetLane)) continue

    const bearing = Number(candidate.bearing)
    if (Number.isFinite(bearing) && Math.abs(angleDiff(bearing, device.heading)) > 60) continue
    const stdDev = Number(candidate.std_dev)
    if (Number.isFinite(stdDev) && stdDev > o.maxStdDev) continue

    const deltaKmh = targetKmh - currentKmh
    if (deltaKmh < o.minDeltaKmh || deltaKmh < currentKmh * o.minPercent) continue
    const recommendation = {
      key: `${current.source_id}|${currentLane}>${targetLane}`,
      currentLane,
      targetLane,
      currentKmh,
      targetKmh,
      deltaKmh,
      direction: targetLane < currentLane ? 'left' : 'right',
      measuredAt: candidate.measured_at
    }
    if (!best || recommendation.deltaKmh > best.deltaKmh) best = recommendation
  }
  return best
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
