'use strict'

// ─── Shared helpers used by the map page modules ───────────────────────────────
// Plain global script (no modules); load BEFORE the config/map/…/gps modules.

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

// Point reached by travelling `distMeters` from `coord` along `bearingDeg`
// (0-360 from north). coords [lng, lat]. Used to dead-reckon the marker forward
// between GPS fixes so motion glides instead of jumping.
function destinationPoint (coord, bearingDeg, distMeters) {
  const R = 6371000 // in meters
  const [lng, lat] = coord
  const brng = (bearingDeg * Math.PI) / 180
  const dR = distMeters / R
  const lat1 = (lat * Math.PI) / 180
  const lng1 = (lng * Math.PI) / 180
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(dR) +
      Math.cos(lat1) * Math.sin(dR) * Math.cos(brng)
  )
  const lng2 =
    lng1 +
    Math.atan2(
      Math.sin(brng) * Math.sin(dR) * Math.cos(lat1),
      Math.cos(dR) - Math.sin(lat1) * Math.sin(lat2)
    )
  return [(((lng2 * 180) / Math.PI + 540) % 360) - 180, (lat2 * 180) / Math.PI]
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

  // Curve-tolerant corridor: when a slope is given the cross gate widens with
  // distance ahead (capped), so a feature ahead around a bend is accepted early
  // instead of only once it is nearly abeam. Without a slope it is the flat
  // maxCross. Safe to widen because the directed heading gate below still
  // rejects the oncoming carriageway.
  const crossLimit = o.crossSlope
    ? Math.min(o.maxCrossCap ?? Infinity, o.maxCross + Math.max(0, rp.along) * o.crossSlope)
    : o.maxCross
  if (Math.abs(rp.cross) > crossLimit) return null
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

// Group the lane-level matrix feed into physical gantries.
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
      // Curve-tolerant corridor (matches the speed HUD). MSI/DRIP bearing comes
      // straight from NDW (reliable travel heading), so the directed heading gate
      // still rejects the oncoming carriageway while the corridor widens.
      maxCross: 45,
      crossSlope: 0.12,
      maxCrossCap: 130
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

// Direction / corridor tuning for the drive-HUD "next sensor" pick.
const LANE_SPEED_SELECT = {
  sideTol: 70,        // deg; roadside_bearing must be within this of heading+90
  axisTol: 55,        // deg; fallback road-axis (mod 180) tolerance when no roadside
  baseCross: 45,      // m; corridor half-width right at the device
  crossSlope: 0.12,   // corridor widening per metre ahead (~7deg cone)
  maxCross: 130,      // m; corridor half-width cap
}

// True when the sensor is on OUR carriageway (same travel direction), judged
// from the reliable roadside_bearing rather than the flip-prone travel bearing.
// NL is right-hand traffic, so a carriageway's roadside (outward from the median)
// sits ~90deg clockwise of its travel direction: our carriageway has
// roadside_bearing ~= heading+90; the oncoming carriageway is ~180deg away and
// therefore rejected. Falls back to a road-axis (mod 180) match — which only
// rejects crossing roads, not oncoming — when roadside_bearing is absent
// (single-carriageway roads, where there is no median to measure across).
function sameCarriagewayDirection (p, heading) {
  const roadside = Number(p.roadside_bearing)
  if (Number.isFinite(roadside)) {
    return Math.abs(angleDiff(roadside, heading + 90)) <= LANE_SPEED_SELECT.sideTol
  }
  const bearing = Number(p.bearing)
  if (!Number.isFinite(bearing)) return true // no direction info — don't over-filter
  const axis = Math.abs(angleDiff(bearing, heading))
  return Math.min(axis, 180 - axis) <= LANE_SPEED_SELECT.axisTol
}

// Pick the nearest speed-measurement site AHEAD in the travel direction (the
// "next upcoming sensor") whose lanes carry at least one speed reading. Point
// features come from /traffic/speed/map (data.points), each carrying a `lanes`
// array plus num_lanes / weggeg_lane_count for entry/exit detection.
//
// Direction is filtered on roadside_bearing (reliable) so the oncoming
// carriageway is rejected without depending on the flip-prone travel bearing;
// the lateral corridor then widens with distance so a sensor ahead around a
// curve is picked up early instead of only once you are almost on top of it.
function selectUpcomingLaneSpeeds (pointFc, device, maxDistanceM) {
  const maxAhead = maxDistanceM ?? 2000
  if (!device || !Array.isArray(device.coords) || !Number.isFinite(device.heading)) return null

  let best = null
  for (const feature of (pointFc?.features || [])) {
    if (!feature.geometry || !feature.properties) continue
    const p = feature.properties
    const hasSpeed = (p.lanes || []).some(l => l && l.speed_kmh !== null && l.speed_kmh !== undefined)
    if (!hasSpeed) continue

    const rp = relativePosition(device, feature.geometry.coordinates)
    if (rp.along <= 0 || rp.along > maxAhead) continue
    if (!sameCarriagewayDirection(p, device.heading)) continue
    const corridor = Math.min(
      LANE_SPEED_SELECT.maxCross,
      LANE_SPEED_SELECT.baseCross + rp.along * LANE_SPEED_SELECT.crossSlope
    )
    if (Math.abs(rp.cross) > corridor) continue

    const cls = { status: 'ahead', along: rp.along, cross: rp.cross, dist: rp.dist }
    if (!best || cls.along < best.cls.along) best = { data: p, cls }
  }
  return best
}

// Split a site's lanes into main through-lanes and extra entry/exit/weave lanes.
// NDW numbers lanes 1..N left→right; WEGGEG reports the count of main through
// lanes. When more lanes are measured than WEGGEG's main count, the surplus
// higher-numbered lanes are entry/exit ramps — grouped out so they render with
// a visual gap and never crowd out the main lanes.
function splitSpeedLaneGroups (props) {
  const lanes = (props.lanes || [])
    .filter(l => l && l.lane !== null && l.lane !== undefined)
    .slice()
    .sort((a, b) => Number(a.lane) - Number(b.lane))
  const mainCount = Number(props.weggeg_lane_count)
  if (!Number.isFinite(mainCount) || mainCount <= 0 || mainCount >= lanes.length) {
    return { main: lanes, extra: [] }
  }
  return {
    main: lanes.filter(l => Number(l.lane) <= mainCount),
    extra: lanes.filter(l => Number(l.lane) > mainCount)
  }
}

// Darken/lighten a "#rrggbb" toward black (pct<0) or white (pct>0); pct in -1..1.
function shadeColor (color, pct) {
  const m = /^#?([0-9a-f]{6})$/i.exec(color)
  if (!m) return color
  const n = parseInt(m[1], 16)
  let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255
  const target = pct < 0 ? 0 : 255
  const p = Math.min(1, Math.abs(pct))
  r = Math.round((target - r) * p) + r
  g = Math.round((target - g) * p) + g
  b = Math.round((target - b) * p) + b
  return `rgb(${r},${g},${b})`
}

// Build a stylised "zoomed road" SVG: one tilted ribbon per lane coloured by its
// speed, an upright speed pill on each, dashed lane markings, and an extra gap
// separating entry/exit ramp lanes from the main carriageway.
function buildLaneSpeedRoad (props) {
  const NS = 'http://www.w3.org/2000/svg'
  const { main, extra } = splitSpeedLaneGroups(props)
  const groups = extra.length ? [main, extra] : [main]

  const LANE_W = 46, GAP_W = 30, H = 96, PAD = 6
  const shear = Math.round(H * Math.tan(15 * Math.PI / 180))

  const boxes = []
  let x = PAD
  groups.forEach((group, gi) => {
    if (gi > 0) x += GAP_W
    group.forEach((lane, li) => {
      boxes.push({ lane, x, first: li === 0, last: li === group.length - 1, ramp: gi > 0 })
      x += LANE_W
    })
  })
  const totalW = x + shear + PAD

  const svg = document.createElementNS(NS, 'svg')
  svg.setAttribute('viewBox', `0 0 ${totalW} ${H}`)
  svg.setAttribute('width', String(totalW))
  svg.setAttribute('height', String(H))
  svg.setAttribute('class', 'lane-road-svg')

  const line = (x1, y1, x2, y2, stroke, width, dash) => {
    const el = document.createElementNS(NS, 'line')
    el.setAttribute('x1', x1); el.setAttribute('y1', y1)
    el.setAttribute('x2', x2); el.setAttribute('y2', y2)
    el.setAttribute('stroke', stroke); el.setAttribute('stroke-width', width)
    if (dash) el.setAttribute('stroke-dasharray', dash)
    svg.appendChild(el)
  }

  for (const box of boxes) {
    const kmh = box.lane.speed_kmh
    const bl = box.x, br = box.x + LANE_W

    const poly = document.createElementNS(NS, 'polygon')
    poly.setAttribute('points', `${bl},${H} ${br},${H} ${br + shear},0 ${bl + shear},0`)
    poly.setAttribute('fill', shadeColor(speedColor(kmh), -0.5))
    svg.appendChild(poly)

    // Right boundary: solid road edge on the group's outer lane, dashed between lanes.
    if (box.last) line(br, H, br + shear, 0, 'rgba(255,255,255,0.85)', '2')
    else line(br, H, br + shear, 0, 'rgba(255,255,255,0.5)', '2', '7 8')
    if (box.first) line(bl, H, bl + shear, 0, 'rgba(255,255,255,0.85)', '2')

    // Upright speed pill at the lane centre.
    const cx = bl + LANE_W / 2 + shear / 2
    const cy = H / 2
    const pw = 40, ph = 26
    const rect = document.createElementNS(NS, 'rect')
    rect.setAttribute('x', cx - pw / 2); rect.setAttribute('y', cy - ph / 2)
    rect.setAttribute('width', pw); rect.setAttribute('height', ph)
    rect.setAttribute('rx', 8)
    rect.setAttribute('fill', speedColor(kmh))
    rect.setAttribute('stroke', '#ffffff'); rect.setAttribute('stroke-width', '2')
    svg.appendChild(rect)

    const txt = document.createElementNS(NS, 'text')
    txt.setAttribute('x', cx); txt.setAttribute('y', cy + 1)
    txt.setAttribute('text-anchor', 'middle')
    txt.setAttribute('dominant-baseline', 'central')
    txt.setAttribute('fill', speedTextColor(kmh))
    txt.setAttribute('font-size', '15'); txt.setAttribute('font-weight', '800')
    txt.textContent = (kmh !== null && kmh !== undefined) ? String(Math.round(kmh)) : '?'
    svg.appendChild(txt)
  }

  return svg
}

// Stable signature of a lane-speed selection so the HUD only rebuilds the SVG
// when the sensor, its lane speeds, or the rounded distance actually change.
function laneSpeedRoadKey (selection) {
  if (!selection) return null
  const p = selection.data
  const lanes = (p.lanes || []).map(l => `${l.lane}:${l.speed_kmh}`).join(',')
  return [p.site_id, p.weggeg_lane_count, lanes, Math.round(selection.cls.along / 25)].join('|')
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

// Dutch "bijgewerkt"-style age for the drive HUD tiles. Sub-day ages are
// relative ("5 min geleden"); a week or older shows a short date, since DRIP
// status timestamps can be months/years old.
function formatAgeNl (isoTs) {
  if (!isoTs) return ''
  const t = new Date(isoTs).getTime()
  if (!Number.isFinite(t)) return ''
  const ageS = (Date.now() - t) / 1000
  if (ageS < 10) return 'nu'
  if (ageS < 60) return `${Math.round(ageS)} sec geleden`
  if (ageS < 3600) return `${Math.round(ageS / 60)} min geleden`
  if (ageS < 86400) return `${Math.round(ageS / 3600)} uur geleden`
  if (ageS < 7 * 86400) return `${Math.round(ageS / 86400)} dag${Math.round(ageS / 86400) === 1 ? '' : 'en'} geleden`
  return new Date(t).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' })
}

// ─── Ground-scale sizing for MapLibre line widths ──────────────────────────────
// MapLibre line-width is screen pixels and has no metre unit. The Web Mercator
// world is 512·2^zoom px wide, so px-per-metre doubles every zoom level, and an
// ['exponential', 2] zoom interpolation reproduces it exactly rather than
// approximating it. Latitude is pinned to the middle of the Netherlands —
// cos(lat) varies ~6% between Zeeland and Groningen, i.e. under 0.2m on a 3.5m
// lane, which is well inside OSM's own positional accuracy.

const NL_REF_LAT_DEG = 52.2
const EARTH_CIRCUMFERENCE_M = 40075016.686

function pxPerMetre (zoom) {
  return (512 * Math.pow(2, zoom)) / (EARTH_CIRCUMFERENCE_M * Math.cos(NL_REF_LAT_DEG * Math.PI / 180))
}

// Render `metres` (a number, or any expression yielding one — e.g.
// ['get', 'width_m']) at true ground scale. The zoom interpolation has to be
// the outermost expression: MapLibre rejects ['zoom'] nested inside anything
// else, so the scale factor is folded into each stop's output rather than
// multiplied over the whole interpolation. Anchors only need to bracket the
// zooms the layer is visible at.
function metresWide (metres, minZoom = 12, maxZoom = 22) {
  return ['interpolate', ['exponential', 2], ['zoom'],
    minZoom, ['*', metres, pxPerMetre(minZoom)],
    maxZoom, ['*', metres, pxPerMetre(maxZoom)]
  ]
}

// Same, but never thinner than floorPx — for markings whose true width falls
// under a pixel at low zoom. The floor can't be an outer ['max'] for the same
// nesting reason, so it's applied per stop, one stop per integer zoom.
function metresWideMin (metres, floorPx, minZoom = 12, maxZoom = 22) {
  const stops = []
  for (let z = minZoom; z <= maxZoom; z++) stops.push(z, Math.max(floorPx, metres * pxPerMetre(z)))
  return ['interpolate', ['exponential', 2], ['zoom'], ...stops]
}

// `metres` wide plus an edge of `edgeM` on each side, the edge never thinner
// than floorPx. Used to draw a band's outline as a casing under it: only the
// part no band covers survives, which on a carriageway is exactly its outside.
// Same per-stop folding as metresWideMin — ['zoom'] can't nest inside anything.
function metresWidePlusEdge (metres, edgeM, floorPx, minZoom = 12, maxZoom = 22) {
  const stops = []
  for (let z = minZoom; z <= maxZoom; z++) {
    const ppm = pxPerMetre(z)
    stops.push(z, ['+', ['*', metres, ppm], 2 * Math.max(floorPx, edgeM * ppm)])
  }
  return ['interpolate', ['exponential', 2], ['zoom'], ...stops]
}

// ─── Lane turn arrows ─────────────────────────────────────────────────────────

// Road-marking arrows, one glyph per lane's turn:lanes token set. Drawn rather
// than shipped as sprites because the token sets combine freely
// (`left;through`, `through;right`, …) — there's no fixed list to pre-draw, so
// each combination is generated on demand and cached by MapLibre.

// Where each token's arrowhead ends up, in degrees off the travel direction.
// MapLibre's line placement aligns a symbol's +x with the line, so the glyph is
// drawn pointing +x and a left turn bends toward -y (canvas y grows downward,
// and the driver's left is up when travel runs to the right).
//
// `reverse` is absent on purpose: a U-turn needs a looping stem this
// stem-and-bend construction can't draw, and it would fold back over itself.
const TURN_ARROW_DEG = {
  through: 0,
  none: 0,
  slight_left: -35,
  slight_right: 35,
  left: -90,
  right: 90,
  sharp_left: -135,
  sharp_right: 135,
  // A merge is a lateral movement, so it reads as a shallow bend.
  merge_to_left: -30,
  merge_to_right: 30
}

const LANE_ARROW_PREFIX = 'lane-arrow-'
const ARROW_ICON_PX = 64
const ARROW_ICON_RATIO = 2 // => 32px natural size; icon-size scales from that

// Glyph proportions, as fractions of the icon's span. Shared with the sizing
// below, which derives from them rather than repeating a measured constant.
const ARROW_REACH = 0.26 // stem end → arrowhead base
const ARROW_HEAD = 0.15

// How long the glyph is on the ground, per metre of lane width. A 90° turn puts
// its arrowhead (ARROW_REACH + ARROW_HEAD) of the span sideways off the lane's
// centreline, so keeping that inside a lane of width w means
// span ≤ w / (2 × (REACH + HEAD)). Scaling off `width_m` rather than fixing a
// length matters: a secondary lane is 2.75m, not 3.5m, and a fixed length sized
// for motorways hangs the turn arrows over the edge line of every secondary.
// The margin keeps the arrowhead off the edge line instead of exactly on it.
const ARROW_LANE_MARGIN = 0.9
const ARROW_SPAN_PER_LANE_WIDTH = (0.5 / (ARROW_REACH + ARROW_HEAD)) * ARROW_LANE_MARGIN

// White paint with a dark edge, so the glyph holds up on both the orange lane
// band and the satellite basemap's real asphalt.
const ARROW_FILL = '#ffffff'
const ARROW_EDGE = 'rgba(40, 28, 6, 0.62)'

function laneArrowImage (tokens) {
  const branches = tokens
    .map(t => TURN_ARROW_DEG[t])
    .filter(deg => deg !== undefined)
    .map(deg => {
      const a = deg * Math.PI / 180
      return { ux: Math.cos(a), uy: Math.sin(a) }
    })
  if (!branches.length) return null

  const S = ARROW_ICON_PX
  const cy = S * 0.5
  const baseX = S * 0.08 // tail
  const stemX = S * 0.42 // where the branches split off
  const reach = S * ARROW_REACH
  const head = S * ARROW_HEAD
  const stemW = S * 0.11
  const edge = S * 0.045

  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = S
  const ctx = canvas.getContext('2d')
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'

  for (const b of branches) {
    b.tipX = stemX + reach * b.ux
    b.tipY = cy + reach * b.uy
  }

  // Shared stem, then one bend per token. The control point carries on along
  // travel so each branch leaves the stem tangentially instead of kinking.
  const traceStems = () => {
    for (const b of branches) {
      ctx.beginPath()
      ctx.moveTo(baseX, cy)
      ctx.lineTo(stemX, cy)
      ctx.quadraticCurveTo(stemX + reach * 0.55, cy, b.tipX, b.tipY)
      ctx.stroke()
    }
  }
  const traceHeads = () => {
    for (const b of branches) {
      const px = -b.uy
      const py = b.ux
      ctx.beginPath()
      ctx.moveTo(b.tipX + b.ux * head, b.tipY + b.uy * head)
      ctx.lineTo(b.tipX + px * head * 0.62, b.tipY + py * head * 0.62)
      ctx.lineTo(b.tipX - px * head * 0.62, b.tipY - py * head * 0.62)
      ctx.closePath()
      ctx.fill()
      ctx.stroke()
    }
  }

  // Every dark part first: an arrowhead's edge drawn later would cut into a
  // white stem it overlaps.
  ctx.strokeStyle = ARROW_EDGE
  ctx.fillStyle = ARROW_EDGE
  ctx.lineWidth = stemW + edge * 2
  traceStems()
  ctx.lineWidth = edge * 2
  traceHeads()

  ctx.strokeStyle = ARROW_FILL
  ctx.fillStyle = ARROW_FILL
  ctx.lineWidth = stemW
  traceStems()
  ctx.lineWidth = 0.1
  traceHeads()

  return ctx.getImageData(0, 0, S, S)
}
