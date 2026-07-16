'use strict'

// Shared, dependency-free lane model and SVG renderer. External source data is
// normalized here so map and HUD integrations can use exactly the same lane
// ordering and fail-closed semantics.
var LaneTopology = (() => {
  const SVG_NS = 'http://www.w3.org/2000/svg'
  const ROLES = new Set(['through', 'entry', 'exit', 'weave', 'unknown'])
  const DEFAULTS = Object.freeze({ laneWidth: 42, laneGap: 7, groupGap: 15, height: 112 })
  const USER_LANE_CONFIDENCE = 0.8
  const LANE_STATE_MIN_CONFIDENCE = 0.6
  const DISPLAY_LANE_WIDTH_M = 3.5

  function normalize (input = {}) {
    const schema = isObject(input.lane_schema) ? input.lane_schema : input
    const compactCount = positiveInteger(schema.lane_count)
    const rawLanes = Array.isArray(schema.lanes)
      ? schema.lanes
      : compactCount
          ? compactSchemaLanes(schema, compactCount)
          : []
    const explicitCount = positiveInteger(schema.count) || compactCount
    const highestNumber = rawLanes.reduce((highest, lane) => {
      const number = positiveInteger(lane?.number)
      return number ? Math.max(highest, number) : highest
    }, 0)
    const count = Math.min(Math.max(explicitCount || highestNumber, 0), 32)
    const byNumber = new Map()

    for (const raw of rawLanes) {
      const number = positiveInteger(raw?.number)
      if (!number || number > count || byNumber.has(number)) continue
      byNumber.set(number, normalizeLane(raw, number))
    }

    const stateByNumber = normalizeStates(input.lane_states ?? schema.lane_states)
    const lanes = []
    for (let number = 1; number <= count; number++) {
      const lane = byNumber.get(number) || normalizeLane({}, number)
      lanes.push({ ...lane, state: stateByNumber.get(number) || emptyState() })
    }

    const userLane = normalizeUserLane(input.user_lane ?? schema.user_lane, count)
    return Object.freeze({
      count,
      lanes: Object.freeze(lanes.map(Object.freeze)),
      user_lane: userLane
    })
  }

  function compactSchemaLanes (schema, count) {
    const roles = Array.isArray(schema.roles) ? schema.roles : []
    const attributes = isObject(schema.attributes) ? schema.attributes : {}
    return Array.from({ length: count }, (_, index) => ({
      number: index + 1,
      role: roles[index] || 'unknown',
      turn: arrayValue(attributes.turn, index),
      change: arrayValue(attributes.change, index),
      destination: arrayValue(attributes.destination, index),
      maxspeed: arrayValue(attributes.maxspeed, index),
      access: arrayValue(attributes.access, index),
      confidence: roles[index] && roles[index] !== 'unknown' ? 1 : 0
    }))
  }

  function arrayValue (value, index) {
    return Array.isArray(value) && index < value.length ? value[index] : null
  }

  function normalizeLane (raw, number) {
    const rawRole = safeString(raw?.role, 20).toLowerCase()
    const role = ROLES.has(rawRole) ? rawRole : 'unknown'
    return {
      number,
      role,
      turn: safeString(raw?.turn, 80) || null,
      change: safeString(raw?.change, 80) || null,
      destination: safeString(raw?.destination, 120) || null,
      maxspeed: finiteNumber(raw?.maxspeed, 0, 300),
      access: safeString(raw?.access, 40) || null,
      confidence: finiteNumber(raw?.confidence, 0, 1) ?? 0
    }
  }

  function normalizeStates (states) {
    const result = new Map()
    const values = Array.isArray(states)
      ? states
      : isObject(states)
          ? Object.entries(states).map(([number, state]) => ({ number, ...state }))
          : []
    for (const raw of values) {
      const number = positiveInteger(raw?.number ?? raw?.lane_number ?? raw?.lane)
      if (!number || result.has(number)) continue
      const confidence = finiteNumber(raw?.confidence ?? raw?.speed_confidence, 0, 1) ?? 0
      const speed = finiteNumber(raw?.speed ?? raw?.speed_kmh, 0, 300)
      result.set(number, Object.freeze({
        speed: raw?.speed_stale === true || confidence < LANE_STATE_MIN_CONFIDENCE
          ? null
          : speed,
        matrix: safeString(raw?.matrix, 32) || null,
        status: safeString(raw?.status, 32) || null,
        confidence
      }))
    }
    return result
  }

  function normalizeUserLane (raw, count) {
    if (!isObject(raw)) return null
    const number = positiveInteger(raw.number ?? raw.lane_number)
    const confidence = finiteNumber(raw.confidence, 0, 1)
    const sampleCount = positiveInteger(raw.sample_count)
    const method = safeString(raw.method, 32).toLowerCase()
    if (raw.status !== 'confirmed' || method !== 'lane_lateral' ||
        !sampleCount || sampleCount < 5 || !number || number > count ||
        confidence === null || confidence < USER_LANE_CONFIDENCE) return null
    return Object.freeze({ number, confidence, method, sample_count: sampleCount })
  }

  function group (input) {
    const model = isNormalized(input) ? input : normalize(input)
    const result = { main: [], entry: [], exit: [], weave: [], unknown: [] }
    for (const lane of model.lanes) {
      if (lane.role === 'through') result.main.push(lane)
      else result[lane.role].push(lane)
    }
    return Object.freeze(Object.fromEntries(
      Object.entries(result).map(([key, lanes]) => [key, Object.freeze(lanes.slice())])
    ))
  }

  function layout (input, options = {}) {
    const model = isNormalized(input) ? input : normalize(input)
    const config = {
      laneWidth: positiveNumber(options.laneWidth) || DEFAULTS.laneWidth,
      laneGap: nonNegativeNumber(options.laneGap) ?? DEFAULTS.laneGap,
      groupGap: nonNegativeNumber(options.groupGap) ?? DEFAULTS.groupGap,
      height: positiveNumber(options.height) || DEFAULTS.height
    }
    let cursor = config.laneWidth / 2
    const lanes = model.lanes.map((lane, index) => {
      if (index > 0) {
        const previous = model.lanes[index - 1]
        cursor += config.laneWidth + (sameVisualGroup(previous.role, lane.role)
          ? config.laneGap
          : config.groupGap)
      }
      return Object.freeze({
        ...lane,
        x: round(cursor),
        width: config.laneWidth,
        path: lanePath(lane.role, cursor, config.height),
        isUserLane: model.user_lane?.number === lane.number
      })
    })
    const width = lanes.length ? cursor + config.laneWidth / 2 : 0
    return Object.freeze({
      width: round(width),
      height: config.height,
      lanes: Object.freeze(lanes)
    })
  }

  function render (target, input, options = {}) {
    if (!target || typeof target.replaceChildren !== 'function') {
      throw new TypeError('LaneTopology.render requires a DOM target with replaceChildren()')
    }
    const doc = target.ownerDocument || globalThis.document
    if (!doc || typeof doc.createElementNS !== 'function') {
      throw new TypeError('LaneTopology.render requires an SVG-capable document')
    }

    const model = isNormalized(input) ? input : normalize(input)
    const geometry = layout(model, options)
    const svg = element(doc, 'svg', {
      class: `lane-topology lane-topology--${safeCssToken(options.variant || 'hud')}`,
      viewBox: `0 0 ${Math.max(geometry.width, 1)} ${geometry.height}`,
      role: 'img',
      'aria-label': options.label || laneAriaLabel(model)
    })

    for (const lane of geometry.lanes) svg.appendChild(renderLane(doc, lane, geometry.height))
    target.replaceChildren(svg)
    return Object.freeze({ model, layout: geometry, svg })
  }

  // Expand only an already-bounded driving path into visual lane centrelines.
  // These offsets are for map display, never for GPS matching or lane claims.
  function mapFeatures (featureCollection, options = {}) {
    const output = []
    const features = Array.isArray(featureCollection?.features)
      ? featureCollection.features.slice(0, positiveInteger(options.maxSegments) || 16)
      : []
    for (const feature of features) {
      if (!feature?.geometry || !isObject(feature.properties)) continue
      const model = normalize(feature.properties)
      if (!model.count) continue
      for (const lane of model.lanes) {
        const offsetM = ((model.count + 1) / 2 - lane.number) * DISPLAY_LANE_WIDTH_M
        const geometry = offsetGeometry(feature.geometry, offsetM)
        if (!geometry) continue
        output.push({
          type: 'Feature',
          id: `${feature.properties.internal_segment_id || feature.id || 'segment'}:${lane.number}`,
          geometry,
          properties: {
            internal_segment_id: feature.properties.internal_segment_id || feature.id || null,
            lane_number: lane.number,
            lane_count: model.count,
            lane_role: lane.role,
            lane_offset_m: offsetM,
            speed_kmh: lane.state.speed,
            state_confidence: lane.state.confidence,
            is_user_lane: model.user_lane?.number === lane.number,
            client_role: feature.properties.client_role || null
          }
        })
      }
    }
    return { type: 'FeatureCollection', features: output }
  }

  function offsetGeometry (geometry, offsetM) {
    if (geometry.type === 'LineString') {
      const coordinates = offsetLine(geometry.coordinates, offsetM)
      return coordinates ? { type: 'LineString', coordinates } : null
    }
    if (geometry.type === 'MultiLineString') {
      const coordinates = (geometry.coordinates || [])
        .map(line => offsetLine(line, offsetM))
        .filter(Boolean)
      return coordinates.length ? { type: 'MultiLineString', coordinates } : null
    }
    return null
  }

  function offsetLine (line, offsetM) {
    if (!Array.isArray(line) || line.length < 2) return null
    return line.map((coordinate, index) => {
      const previous = line[Math.max(0, index - 1)]
      const next = line[Math.min(line.length - 1, index + 1)]
      if (!validCoordinate(coordinate) || !validCoordinate(previous) || !validCoordinate(next)) {
        return coordinate
      }
      const latitude = coordinate[1] * Math.PI / 180
      const metresPerLon = Math.max(1, 111320 * Math.cos(latitude))
      const east = (next[0] - previous[0]) * metresPerLon
      const north = (next[1] - previous[1]) * 110540
      const length = Math.hypot(east, north)
      if (!length || !Number.isFinite(length)) return coordinate.slice()
      // Directed geometry: left-of-travel normal = (-north, east).
      const offsetEast = -north / length * offsetM
      const offsetNorth = east / length * offsetM
      return [
        coordinate[0] + offsetEast / metresPerLon,
        coordinate[1] + offsetNorth / 110540
      ]
    })
  }

  function renderLane (doc, lane, height) {
    const groupNode = element(doc, 'g', {
      class: `lane-topology__lane lane-topology__lane--${lane.role}${lane.isUserLane ? ' lane-topology__lane--user' : ''}`,
      'data-lane-number': lane.number,
      'data-lane-role': lane.role
    })
    groupNode.appendChild(element(doc, 'path', {
      class: 'lane-topology__path',
      d: lane.path,
      'vector-effect': 'non-scaling-stroke'
    }))

    const label = element(doc, 'text', {
      class: 'lane-topology__number', x: lane.x, y: height - 7,
      'text-anchor': 'middle'
    })
    label.textContent = String(lane.number)
    groupNode.appendChild(label)

    const stateParts = []
    if (lane.state.speed !== null) stateParts.push(`${formatNumber(lane.state.speed)} km/u`)
    if (lane.state.matrix) stateParts.push(lane.state.matrix)
    if (stateParts.length) {
      const state = element(doc, 'text', {
        class: 'lane-topology__state', x: lane.x, y: 17,
        'text-anchor': 'middle'
      })
      state.textContent = stateParts.join(' · ')
      groupNode.appendChild(state)
    }
    return groupNode
  }

  function lanePath (role, x, height) {
    const top = 25
    const bottom = height - 17
    const branch = 11
    if (role === 'entry') return `M ${round(x + branch)} ${bottom} Q ${round(x + branch)} ${round((top + bottom) / 2)} ${round(x)} ${round(top + 15)} L ${round(x)} ${top}`
    if (role === 'exit') return `M ${round(x)} ${bottom} L ${round(x)} ${round(top + 26)} Q ${round(x)} ${round(top + 12)} ${round(x + branch)} ${top}`
    if (role === 'weave') return `M ${round(x - branch / 2)} ${bottom} Q ${round(x + branch)} ${round((top + bottom) / 2)} ${round(x - branch / 2)} ${top}`
    return `M ${round(x)} ${bottom} L ${round(x)} ${top}`
  }

  function laneAriaLabel (model) {
    const descriptions = model.lanes.map(lane => {
      const state = []
      if (lane.state.speed !== null) state.push(`${formatNumber(lane.state.speed)} kilometer per uur`)
      if (lane.state.matrix) state.push(`matrix ${lane.state.matrix}`)
      const user = model.user_lane?.number === lane.number ? ', jouw rijstrook' : ''
      return `rijstrook ${lane.number}${user}, ${lane.role}${state.length ? `, ${state.join(', ')}` : ''}`
    })
    return descriptions.length ? descriptions.join('; ') : 'Geen rijstrookgegevens beschikbaar'
  }

  function element (doc, name, attributes) {
    const node = doc.createElementNS(SVG_NS, name)
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value))
    return node
  }

  function emptyState () {
    return Object.freeze({ speed: null, matrix: null, status: null, confidence: 0 })
  }

  function sameVisualGroup (left, right) {
    return left === right || (left === 'through' && right === 'weave') || (left === 'weave' && right === 'through')
  }

  function isNormalized (value) {
    return isObject(value) && Array.isArray(value.lanes) && value.lanes.every(lane => isObject(lane.state))
  }

  function safeString (value, maxLength) {
    if (typeof value !== 'string' && typeof value !== 'number') return ''
    return String(value).trim().slice(0, maxLength)
  }

  function safeCssToken (value) {
    return safeString(value, 24).toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'hud'
  }

  function finiteNumber (value, minimum, maximum) {
    if (value === '' || value === null || value === undefined || typeof value === 'boolean') return null
    const number = Number(value)
    return Number.isFinite(number) && number >= minimum && number <= maximum ? number : null
  }

  function positiveInteger (value) {
    const number = finiteNumber(value, 1, 1_000_000)
    return number !== null && Number.isInteger(number) ? number : null
  }

  function positiveNumber (value) {
    return finiteNumber(value, Number.EPSILON, 10_000)
  }

  function nonNegativeNumber (value) {
    return finiteNumber(value, 0, 10_000)
  }

  function isObject (value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
  }

  function validCoordinate (value) {
    return Array.isArray(value) && value.length >= 2 &&
      Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))
  }

  function round (value) {
    return Math.round(value * 100) / 100
  }

  function formatNumber (value) {
    return Number.isInteger(value) ? String(value) : value.toFixed(1)
  }

  return Object.freeze({
    normalize,
    group,
    layout,
    render,
    mapFeatures,
    offsetGeometry,
    USER_LANE_CONFIDENCE,
    LANE_STATE_MIN_CONFIDENCE,
    DISPLAY_LANE_WIDTH_M
  })
})()

if (typeof module !== 'undefined' && module.exports) module.exports = LaneTopology
