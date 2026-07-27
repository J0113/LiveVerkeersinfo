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

const SPEED_LIMIT_COLOR_STOPS = [
  [0, '#cc2200'],
  [0.3, '#ff3333'],
  [0.5, '#ff8800'],
  [0.7, '#ffdd00'],
  [0.9, '#00cc44'],
]
const SPEED_LIMIT_UNKNOWN_COLOR = '#777777'

function speedLimitLineColorExpression () {
  return ['case',
    ['>', ['coalesce', ['get', 'maxspeed_kmh'], 0], 0],
    ['interpolate', ['linear'],
      ['/', ['get', 'speed_kmh'], ['get', 'maxspeed_kmh']],
      ...SPEED_LIMIT_COLOR_STOPS.flat()
    ],
    SPEED_LIMIT_UNKNOWN_COLOR
  ]
}

// CSS equivalent of MapLibre's line-color interpolation, so the opaque number
// label and translucent line use the same measured-speed/maxspeed colour.
function speedLimitColor (kmh, maxspeedKmh) {
  if (kmh === null || kmh === undefined || kmh === '') return SPEED_LIMIT_UNKNOWN_COLOR
  const speed = Number(kmh)
  const limit = Number(maxspeedKmh)
  if (!Number.isFinite(speed) || !Number.isFinite(limit) || limit <= 0) {
    return SPEED_LIMIT_UNKNOWN_COLOR
  }
  const ratio = speed / limit
  if (ratio <= SPEED_LIMIT_COLOR_STOPS[0][0]) return SPEED_LIMIT_COLOR_STOPS[0][1]

  for (let i = 1; i < SPEED_LIMIT_COLOR_STOPS.length; i++) {
    const [upperRatio, upperColor] = SPEED_LIMIT_COLOR_STOPS[i]
    if (ratio <= upperRatio) {
      const [lowerRatio, lowerColor] = SPEED_LIMIT_COLOR_STOPS[i - 1]
      const t = (ratio - lowerRatio) / (upperRatio - lowerRatio)
      return interpolateHexColor(lowerColor, upperColor, t)
    }
  }
  return SPEED_LIMIT_COLOR_STOPS[SPEED_LIMIT_COLOR_STOPS.length - 1][1]
}

function interpolateHexColor (from, to, t) {
  const rgb = color => [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16))
  const a = rgb(from)
  const b = rgb(to)
  return '#' + a.map((channel, i) =>
    Math.round(channel + (b[i] - channel) * t).toString(16).padStart(2, '0')
  ).join('')
}

function speedLimitTextColor (kmh, maxspeedKmh) {
  const color = speedLimitColor(kmh, maxspeedKmh)
  const [r, g, b] = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16))
  return (r * 299 + g * 587 + b * 114) / 1000 > 150 ? '#111' : '#fff'
}

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

// Project `point` onto a polyline (array of [lng,lat]). Uses a flat local
// equirectangular projection around the line's own start — fine at the
// few-km scale of a trajectcontrole section, avoids per-segment haversine.
// Returns { distToLine, along, total } (metres) for the closest segment, or
// null for a degenerate (<2 point) line. `along`/`total` are cumulative
// distance from the line's first coordinate, i.e. travel direction = start→end.
function projectPointOnTrajectLine (point, lineCoords) {
  if (!lineCoords || lineCoords.length < 2) return null
  const lat0 = lineCoords[0][1]
  const mPerLng = Math.cos((lat0 * Math.PI) / 180) * 111320
  const mPerLat = 110540
  const toXY = ([lng, lat]) => [(lng - lineCoords[0][0]) * mPerLng, (lat - lineCoords[0][1]) * mPerLat]
  const p = toXY(point)

  let cumulative = 0
  let best = null
  let prev = toXY(lineCoords[0])
  for (let i = 1; i < lineCoords.length; i++) {
    const cur = toXY(lineCoords[i])
    const dx = cur[0] - prev[0]
    const dy = cur[1] - prev[1]
    const segLen = Math.hypot(dx, dy)
    let t = segLen > 0 ? ((p[0] - prev[0]) * dx + (p[1] - prev[1]) * dy) / (segLen * segLen) : 0
    t = Math.max(0, Math.min(1, t))
    const projX = prev[0] + dx * t
    const projY = prev[1] + dy * t
    const distToSeg = Math.hypot(p[0] - projX, p[1] - projY)
    const along = cumulative + segLen * t
    if (best === null || distToSeg < best.distToLine) best = { distToLine: distToSeg, along }
    cumulative += segLen
    prev = cur
  }
  return best ? { distToLine: best.distToLine, along: best.along, total: cumulative } : null
}

// Pick the trajectcontrole (flitspalen SC→SCE) line the device currently sits
// on, if any. Each feature's geometry runs SC (start) → SCE (end) in travel
// direction (see ingest/flitspalen_route.py), so `along` from
// projectPointOnTrajectLine is already distance travelled into the section.
function selectTrajectProgress (fc, coords, maxDistM) {
  if (!coords) return null
  let best = null
  for (const feature of (fc?.features || [])) {
    const geom = feature.geometry
    if (!geom || geom.type !== 'LineString') continue
    const proj = projectPointOnTrajectLine(coords, geom.coordinates)
    if (!proj || proj.distToLine > maxDistM || proj.total <= 0) continue
    if (!best || proj.distToLine < best.proj.distToLine) best = { feature, proj }
  }
  if (!best) return null
  const { feature, proj } = best
  return {
    street: feature.properties?.street || null,
    scId: feature.properties?.sc_id,
    travelled: proj.along,
    remaining: Math.max(0, proj.total - proj.along),
    total: proj.total,
  }
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

// ─── Drive HUD: current-road context ────────────────────────────────────────
//
// Everything the HUD shows about the road ahead is scoped by one context:
// which road we are on (OSM lane map-matching, client-side), which carriageway
// of it (NDW measurement sites, server-side) and our own hectometre on that
// carriageway (likewise). See GET /api/traffic/road-context.
//
// Hectometrering runs with the direction of travel on carriageway R and against
// it on L, so a signed hectometre difference is a distance measured *along the
// road*. That is the whole point: a straight-line corridor loses a sensor round
// a bend (its along-track offset collapses and its cross-track offset explodes),
// while a hectometre difference does not care about geometry at all.
const CARRIAGEWAY_KM_SIGN = { R: 1, L: -1 }

// The official reference is carried on the map-matched OSM way. Preserve it
// verbatim: besides Re/Li, Dutch road data also uses letters for parallel,
// connecting and distributor carriageways.
function osmCarriagewayRef (properties) {
  const value = properties?.carriageway_ref ?? properties?.['carriageway:ref']
  if (value === null || value === undefined) return null
  return String(value).trim() || null
}

function carriagewayKmSign (carriageway) {
  return CARRIAGEWAY_KM_SIGN[carriageway] ?? null
}

// Normalize a road reference for comparison — mirrors _normalized_road_ref in
// the API (a02 == A2, "A9;A200" takes the first).
function normalizeRoadRef (value) {
  if (!value) return null
  const m = String(value).toUpperCase().match(/(?<![A-Z0-9])([AN])?\s*0*(\d+)([A-Z]?)(?![A-Z0-9])/)
  if (!m) return null
  return `${m[1] || ''}${parseInt(m[2], 10)}${m[3] || ''}`
}

// Our hectometre now, from the anchor resolved at `context.coords`. Refetched
// every HUD cycle (~100m / 15s), so this only bridges the gap between fetches —
// short enough that straight-line travelled distance is a fine stand-in for
// distance along the road.
function roadContextAnchorKm (context, coords) {
  if (!context || !Number.isFinite(context.anchorKm)) return null
  const sign = carriagewayKmSign(context.carriageway)
  if (sign === null || !Array.isArray(coords) || !Array.isArray(context.coords)) {
    return context.anchorKm
  }
  return context.anchorKm + (sign * calculateDistance(context.coords, coords)) / 1000
}

// Sanity bounds on a hectometre-derived distance, checked against the straight
// line to the sensor. A road can only be longer than the chord it spans, never
// shorter, so a hectometre distance far below the chord means the hectometrering
// is not ours (a reset at a province boundary, a stray km on a co-located site);
// one far above means the same in the other direction.
// The slack term scales with how far away the anchor site was: our hectometre
// is only as good as the site it was derived from, so a 500m-distant anchor can
// legitimately misplace a nearby sensor by that much. The guard is aimed at
// hectometre *resets*, which are kilometres out, so it survives the loosening.
const ROAD_SCOPED_SELECT = {
  minChordRatio: 0.9,       // km-distance below this × chord = not our hectometrering
  chordSlackM: 100,         // absorbs anchor/GPS error at short range
  maxChordRatio: 3,         // a real road rarely trebles the chord over these ranges
  maxChordSlackM: 500,
}

// Placement for sites that carry no hectometre — most of the provincial network
// (PNH/PZH/… sites publish speeds with no km at all), where the hectometre path
// has nothing to work with. Projection onto the heading is not curve-proof, so
// it gets a shorter horizon than the km path and only ever applies per sensor:
// a road with hectometrering keeps using it.
const ROAD_SCOPED_GEOMETRIC = {
  maxDistanceM: 4000,       // beyond this a straight-line projection is guesswork
  corridorBaseM: 100,       // GPS error + lane offset + the road not being straight
  corridorRatio: 0.2,       // …widening with distance, as the road bends away
  maxBearingDiffDeg: 60,    // the site's own travel bearing must still agree
}

function roadScopedRoadMatches (properties, context) {
  if (!context.road) return true
  const ref = normalizeRoadRef(properties.road) || normalizeRoadRef(properties.osm_ref)
  // A site with no road reference at all stays in: the pool it came from was
  // already scoped to this road server-side (effective_road), and dropping
  // unlabelled sites would silently lose legitimate mainline sensors.
  return ref === null || ref === context.road
}

function roadScopedCarriagewayMatches (properties, context) {
  if (!context.carriageway) return true
  const cw = properties.carriageway ?? properties.derived_carriageway ?? null
  return cw === null || cw === context.carriageway
}

function isLinkHighway (highway) {
  return typeof highway === 'string' && highway.endsWith('_link')
}

// Which roadway a sensor sits on, in OSM's carriageway_ref vocabulary ("Li"/
// "Re" for the main carriageways, a letter per slip road). The OSM match is the
// authority; the NDW site name is the fallback for a site OSM could not match
// confidently (`0091hrl0337ra` is the hoofdrijbaan links, `009vwa042571`
// verbindingsweg a — see site_naming.py, which derives it server-side).
function sensorRoadwayRef (properties) {
  return osmCarriagewayRef({ carriageway_ref: properties.osm_carriageway_ref }) ||
    osmCarriagewayRef({ carriageway_ref: properties.ndw_roadway_ref })
}

// Which *roadway* of the road we are on: an exit or entry slip road is a
// physically separate carriageway from the mainline, yet carries the same road
// reference, the same hectometrering and often its own speed sensors. Selecting
// by road+carriageway alone therefore cannot tell them apart.
//
// carriageway_ref does, exactly (an A9 exit is tagged "c" where the mainline is
// "Li"/"Re"), so once we know our own, only sensors on that same roadway are
// shown — including, deliberately, dropping a sensor whose roadway cannot be
// established at all: "unattributable" is not "ours".
//
// Without a carriageway_ref for our own lane there is nothing to compare, so
// fall back to link-ness: from the mainline a motorway_link site measures ramp
// traffic, and from a slip road the mainline's sites are the ones that are not
// ours. A site with no OSM match at all is kept in that fallback — there it is
// the only signal we have.
function roadScopedRoadwayMatches (properties, roadway) {
  const ours = roadway?.carriagewayRef
  const theirs = sensorRoadwayRef(properties)
  if (ours) return theirs !== null && ours.toLowerCase() === theirs.toLowerCase()
  if (!properties.osm_highway) return true
  return isLinkHighway(properties.osm_highway) === Boolean(roadway?.isLink)
}

function hasLaneSpeed (properties) {
  return (properties.lanes || []).some(l => l && l.speed_kmh !== null && l.speed_kmh !== undefined)
}

// Every speed sensor ahead of us on the current road+carriageway, nearest first.
// Feeds both the "snelheid per rijstrook" tile (nearest entry) and the left
// speed bar (deduplicated list) so the two can never disagree about which road
// they are describing.
//
// Placement is by hectometre (`placement: 'km'`) wherever the site carries one
// — curve-proof, and needing no corridor gate at all, since road and
// carriageway are already pinned by the context. Sites without a hectometre
// fall back to projection onto our heading (`placement: 'geo'`, see
// placeGeometrically): the provincial network largely has no hectometrering, so
// failing closed there means showing nothing at all on those roads.
function selectRoadScopedSensors (pointFc, context, device, opts) {
  const maxAhead = opts?.maxDistanceM ?? 2000
  if (!context || !context.carriageway) return []
  if (!device || !Array.isArray(device.coords)) return []

  const anchorKm = roadContextAnchorKm(context, device.coords)
  const sign = carriagewayKmSign(context.carriageway)
  const slack = Math.max(
    ROAD_SCOPED_SELECT.chordSlackM,
    Number.isFinite(context.anchorDistanceM) ? context.anchorDistanceM : 0
  )
  const out = []

  for (const feature of (pointFc?.features || [])) {
    if (!feature.geometry || !feature.properties) continue
    const p = feature.properties
    if (!hasLaneSpeed(p)) continue
    if (!roadScopedRoadMatches(p, context)) continue
    if (!roadScopedCarriagewayMatches(p, context)) continue
    // Only sensors on the same roadway as us: the mainline's while we drive it,
    // the slip road's once we have taken the exit (see roadScopedRoadwayMatches).
    if (!roadScopedRoadwayMatches(p, opts?.roadway)) continue

    const coords = feature.geometry.coordinates
    const dist = calculateDistance(device.coords, coords)
    const placed = Number.isFinite(p.km) && Number.isFinite(anchorKm) && sign !== null
      ? placeByHectometre(p, anchorKm, sign, dist, slack, maxAhead)
      : placeGeometrically(p, coords, dist, device)
    if (!placed) continue
    out.push({
      data: p,
      placement: placed.placement,
      cls: { status: 'ahead', along: placed.along, cross: placed.cross, dist },
    })
  }

  out.sort((a, b) => a.cls.along - b.cls.along)
  return out
}

// Hectometre difference, guarded against a site whose km belongs to some other
// stretch of road (an N-road renumbered at a province boundary, a stray km on a
// co-located site): the along-road distance has to stay in a plausible band
// around the straight-line chord.
function placeByHectometre (p, anchorKm, sign, dist, slack, maxAhead) {
  const along = (p.km - anchorKm) * sign * 1000
  if (along <= 0 || along > maxAhead) return null
  if (along < dist * ROAD_SCOPED_SELECT.minChordRatio - slack) return null
  if (along > dist * ROAD_SCOPED_SELECT.maxChordRatio + ROAD_SCOPED_SELECT.maxChordSlackM + slack) return null
  return { placement: 'km', along, cross: 0 }
}

// Fallback for a site with no hectometre: project it onto the direction of
// travel. `along` is how far ahead it lies, `cross` how far off the axis — the
// road bending away from our current heading, so the corridor widens with
// distance. Everything here is already road- and carriageway-scoped; the
// corridor is only there to reject what lies behind a bend or on a branch,
// and without a heading there is no axis to project onto at all.
function placeGeometrically (p, coords, dist, device) {
  if (device.heading === null || device.heading === undefined) return null
  const { along, cross: signedCross } = relativePosition(device, coords)
  const cross = Math.abs(signedCross)
  if (along <= 0 || along > ROAD_SCOPED_GEOMETRIC.maxDistanceM) return null
  const corridor = ROAD_SCOPED_GEOMETRIC.corridorBaseM + along * ROAD_SCOPED_GEOMETRIC.corridorRatio
  if (cross > corridor) return null
  // The site's own VILD travel bearing is a second opinion on whether it faces
  // the way we are driving. Sites without one are judged on geometry alone.
  if (Number.isFinite(p.bearing) &&
      Math.abs(angleDiff(p.bearing, device.heading)) > ROAD_SCOPED_GEOMETRIC.maxBearingDiffDeg) {
    return null
  }
  return { placement: 'geo', along, cross }
}

// The "next sensor" tile takes the nearest candidate as-is (before the sidebar's
// deduplication), so it keeps that site's own per-lane readings.
function pickNextLaneSpeedSensor (candidates, maxDistanceM) {
  const limit = maxDistanceM ?? Infinity
  return (candidates || []).find(c => c.cls.along <= limit) || null
}

// Add the OSM metadata belonging to an upcoming point. Point and lane features
// share the confidently matched source/direction pair, while maxspeed/name/ref
// live on the lane response returned by include_lanes=true. Road-scoped points
// already carry these directly (see _attach_osm_matches), so a lane match only
// fills gaps — it must not overwrite a known value with an absent one.
function enrichLaneSpeedSelection (selection, laneFc) {
  if (!selection) return null
  const point = selection.data || {}
  const lane = (laneFc?.features || []).find(feature => {
    const p = feature.properties || {}
    return p.osm_source_id === point.osm_source_id &&
      p.osm_direction === point.osm_direction
  })
  if (!lane) return selection
  const p = lane.properties || {}
  return {
    ...selection,
    data: {
      ...point,
      osm_name: p.name ?? point.osm_name,
      osm_ref: p.ref ?? point.osm_ref,
      maxspeed_kmh: p.maxspeed_kmh ?? point.maxspeed_kmh,
    }
  }
}

// The left speed bar over the same candidates, with two sidebar-specific rules:
//  - represent each site by its fastest lane reading, since the sidebar shows
//    one number per sensor rather than a per-lane breakdown.
//  - NDW often reports the same physical gantry as several site_ids (separate
//    loop-detector systems per lane group, e.g. "...vwh0656ra" /
//    "...hrl0656ra"), which share the same road+km — merge those into one
//    entry (fastest reading wins) instead of showing near-duplicate rows.
// Returns entries sorted nearest-first, one per distance band (see
// selectSpeedSidebarBands).
function buildSpeedSidebarList (candidates, opts) {
  const bands = opts?.bands ?? [opts?.maxDistanceM ?? Infinity]
  const maxAhead = opts?.maxDistanceM ?? bands[bands.length - 1] ?? Infinity

  const bySite = new Map()
  for (const candidate of (candidates || [])) {
    const p = candidate.data
    if (candidate.cls.along > maxAhead) continue
    const speeds = (p.lanes || [])
      .map(l => l && l.speed_kmh)
      .filter(v => v !== null && v !== undefined)
    if (!speeds.length) continue

    const item = { ...candidate, fastestKmh: Math.max(...speeds) }
    const key = p.km != null ? `${p.road || ''}|${p.km}` : p.site_id
    const existing = bySite.get(key)
    if (!existing || item.fastestKmh > existing.fastestKmh) bySite.set(key, item)
  }

  const deduped = [...bySite.values()]
  deduped.sort((a, b) => a.cls.along - b.cls.along)
  return selectSpeedSidebarBands(deduped, bands, opts?.keep)
}

// One entry per distance band: the nearest sensor inside it, `bands` being the
// upper bounds in metres (nearest first). A band with no sensor yields nothing
// rather than borrowing from its neighbours — an empty stretch of road is a
// fact worth showing, and filling it would put a pill at a distance it does not
// belong to.
//
// This is what keeps the bar still while driving. The bands are anchored to the
// vehicle, and a sensor's distance only falls, so between refreshes each sensor
// either stays in its band or moves into the next one down. The list therefore
// changes one entry at a time, at predictable moments, instead of being
// re-derived from whatever happens to be in range.
//
// `keep` (site keys already on screen) settles the remaining wobble: when the
// hectometre anchor is re-resolved a sensor's distance can shift by tens of
// metres, so near a boundary two sensors can trade places. Preferring the one
// already displayed makes that a no-op instead of a flicker.
function selectSpeedSidebarBands (list, bands, keep) {
  const out = []
  let lower = 0
  for (const upper of bands) {
    const inBand = list.filter(s => s.cls.along > lower && s.cls.along <= upper)
    lower = upper
    if (!inBand.length) continue
    const held = keep && inBand.find(s => keep.has(speedSidebarKey(s)))
    out.push(held || inBand[0])
  }
  return out
}

// Identity of a sidebar entry across refreshes: the merged site it stands for
// (buildSpeedSidebarList already collapses co-located gantries onto road|km).
function speedSidebarKey (item) {
  const p = item?.data || {}
  return p.km != null ? `${p.road || ''}|${p.km}` : p.site_id
}

// enrichLaneSpeedSelection over a list of selections.
function enrichLaneSpeedSelectionList (selections, laneFc) {
  return selections.map(s => enrichLaneSpeedSelection(s, laneFc))
}

const CURRENT_ROAD_MAX_DISTANCE_M = 35
const CURRENT_ROAD_HEADING_TOLERANCE_DEG = 55
const CURRENT_ROAD_HYSTERESIS_M = 5

// Closest point and local way bearing for LineString/MultiLineString geometry.
// The equirectangular projection is accurate enough for a 35m map-matching gate.
function projectCurrentRoadGeometry (geometry, target) {
  if (!geometry || !Array.isArray(target)) return null
  const lines = geometry.type === 'LineString'
    ? [geometry.coordinates]
    : geometry.type === 'MultiLineString' ? geometry.coordinates : []
  const latScale = 110540
  const lonScale = 111320 * Math.cos(target[1] * Math.PI / 180)
  let best = null

  for (const line of lines) {
    if (!Array.isArray(line) || line.length < 2) continue
    for (let i = 0; i < line.length - 1; i++) {
      const a = line[i]
      const b = line[i + 1]
      const ax = (a[0] - target[0]) * lonScale
      const ay = (a[1] - target[1]) * latScale
      const dx = (b[0] - a[0]) * lonScale
      const dy = (b[1] - a[1]) * latScale
      const denom = dx * dx + dy * dy
      const t = denom ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denom)) : 0
      const px = ax + t * dx
      const py = ay + t * dy
      const distance = Math.hypot(px, py)
      if (!best || distance < best.distance) {
        best = {
          distance,
          bearing: calculateBearing(a, b),
          coords: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t],
        }
      }
    }
  }
  return best
}

function currentRoadSourceId (properties) {
  return properties?.osm_source_id ?? properties?.source_id ?? null
}

function currentRoadDirection (properties) {
  return properties?.osm_direction ?? properties?.direction ?? null
}

function currentRoadIdentity (selection) {
  if (!selection) return null
  const p = selection.data || {}
  return `${currentRoadSourceId(p) ?? ''}|${currentRoadDirection(p) ?? ''}`
}

// Select the lane physically under the device. Directed lanes use their local
// travel bearing (backward lane geometry follows OSM way order, so reverse it).
// Undirected/both-ways lanes use the closest road axis instead.
function selectCurrentOsmLane (laneFc, device, previous) {
  if (!device || !Array.isArray(device.coords)) return null
  const headingKnown = Number.isFinite(device.heading)
  const candidates = []

  for (const feature of (laneFc?.features || [])) {
    if (!feature.geometry || !feature.properties) continue
    const p = feature.properties
    if (p.role === 'connector') continue
    const projected = projectCurrentRoadGeometry(feature.geometry, device.coords)
    if (!projected || projected.distance > CURRENT_ROAD_MAX_DISTANCE_M) continue

    const direction = currentRoadDirection(p)
    let travelBearing = projected.bearing
    if (direction === 'bwd') travelBearing = (travelBearing + 180) % 360
    if (headingKnown) {
      const directed = direction === 'fwd' || direction === 'bwd'
      const headingError = directed
        ? Math.abs(angleDiff(travelBearing, device.heading))
        : Math.min(
            Math.abs(angleDiff(projected.bearing, device.heading)),
            Math.abs(angleDiff((projected.bearing + 180) % 360, device.heading))
          )
      if (headingError > CURRENT_ROAD_HEADING_TOLERANCE_DEG) continue
    }

    candidates.push({
      data: p,
      coords: projected.coords,
      distance: projected.distance,
      bearing: travelBearing,
    })
  }

  candidates.sort((a, b) => a.distance - b.distance)
  const best = candidates[0] || null
  if (!best || !previous) return best

  const previousId = currentRoadIdentity(previous)
  const retained = candidates.find(candidate => currentRoadIdentity(candidate) === previousId)
  return retained && retained.distance <= best.distance + CURRENT_ROAD_HYSTERESIS_M
    ? retained
    : best
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
  const mainCount = Number(props.osm_lane_count)
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
    poly.setAttribute('fill', shadeColor(speedLimitColor(kmh, props.maxspeed_kmh), -0.5))
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
    rect.setAttribute('fill', speedLimitColor(kmh, props.maxspeed_kmh))
    rect.setAttribute('stroke', '#ffffff'); rect.setAttribute('stroke-width', '2')
    svg.appendChild(rect)

    const txt = document.createElementNS(NS, 'text')
    txt.setAttribute('x', cx); txt.setAttribute('y', cy + 1)
    txt.setAttribute('text-anchor', 'middle')
    txt.setAttribute('dominant-baseline', 'central')
    txt.setAttribute('fill', speedLimitTextColor(kmh, props.maxspeed_kmh))
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
  return [p.site_id, p.osm_lane_count, p.maxspeed_kmh, lanes,
    Math.round(selection.cls.along / 25)].join('|')
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
const ARROW_LANE_MARGIN = 0.6
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
