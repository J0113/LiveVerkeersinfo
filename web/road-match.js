'use strict'

const roadMatchCoreApi = typeof RoadMatchCore !== 'undefined'
  ? RoadMatchCore
  : (typeof require === 'function' ? require('./road-match-core.js') : null)
if (!roadMatchCoreApi) throw new Error('road-match-core.js must load before road-match.js')

// ─── OSM road-corridor + stateful client map matching ──────────────────────
//
// The backend returns a deliberately small, direction-aware road graph around
// the vehicle. This module indexes that graph once per response and only scores
// nearby atomic line segments for each GPS fix. It never fetches on manual map
// movement: the corridor is a driving-mode resource, not a viewport layer.

const ROAD_MATCH_SOURCE = 'mvp-road-corridor'
const ROAD_MATCH_LANE_SOURCE = 'mvp-road-lanes'
const ROAD_MATCH_HISTORY_SIZE = 8
const ROAD_MATCH_CELL_DEG = 0.001
const ROAD_MATCH_MIN_CONFIDENCE = 0.62
const ROAD_MATCH_SPEED_MIN_CONFIDENCE = 0.60
const ROAD_MATCH_SPEED_MAX_AGE_MS = 5 * 60_000
const ROAD_MATCH_REFETCH_DISTANCE_M = 200
const ROAD_MATCH_REFETCH_HEADING_DEG = 18
const ROAD_MATCH_REFETCH_MS = 15_000
const ROAD_MATCH_PATH_REFRESH_MS = 60_000
const ROAD_MATCH_EMPTY_FC = typeof EMPTY_FC === 'undefined'
  ? { type: 'FeatureCollection', features: [] }
  : EMPTY_FC

let roadMatchFeatures = ROAD_MATCH_EMPTY_FC
let roadMatchCorridorFeatures = []
let roadMatchPathFeatures = []
let roadMatchFeatureById = new Map()
let roadMatchGrid = new Map()
let roadMatchOutgoing = new Map()
let roadMatchFixHistory = []
let roadMatchCoreState = roadMatchCoreApi.initialState()
let roadMatchCurrent = null
let roadMatchAccepted = false
let roadMatchController = null
let roadMatchPathController = null
let roadMatchTopology = null
let roadMatchPathRequestedId = null
let roadMatchPathFetchedAt = 0
let roadMatchLastFetchCoords = null
let roadMatchLastFetchHeading = null
let roadMatchLastFetchAt = 0
let roadMatchLastFix = null
let roadMatchLoading = false
let roadMatchRoleIds = { under: null, ahead: new Set() }
let roadMatchLaneMapKey = null
let roadMatchLaneHudKey = null

function initRoadMatching () {
  if (map.getSource(ROAD_MATCH_SOURCE)) return
  map.addSource(ROAD_MATCH_SOURCE, {
    type: 'geojson',
    data: EMPTY_FC,
    promoteId: 'internal_segment_id',
    attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>, ODbL'
  })
  map.addLayer({
    id: 'mvp-road-corridor-context', type: 'line', source: ROAD_MATCH_SOURCE,
    paint: {
      'line-color': ['case',
        ['boolean', ['get', 'speed_stale'], false], '#66717e',
        ['==', ['get', 'speed_kmh'], null], '#75879a',
        ['interpolate', ['linear'], ['to-number', ['get', 'speed_kmh']],
          0, '#c8324a', 25, '#e34b3f', 45, '#ef8b36',
          65, '#f2d14a', 85, '#62c86b', 110, '#23a96a'
        ]
      ],
      'line-width': ['interpolate', ['linear'], ['zoom'], 12, 2, 16, 6.5],
      'line-opacity': 0.34
    },
    layout: { 'line-cap': 'round', 'line-join': 'round' }
  })
  map.addLayer({
    id: 'mvp-road-corridor-ahead', type: 'line', source: ROAD_MATCH_SOURCE,
    paint: {
      'line-color': '#5ee7ff',
      'line-width': ['interpolate', ['linear'], ['zoom'], 12, 3.5, 16, 9],
      'line-opacity': ['case', ['boolean', ['feature-state', 'ahead'], false], 0.84, 0]
    },
    layout: { 'line-cap': 'round', 'line-join': 'round' }
  })
  map.addLayer({
    id: 'mvp-road-corridor-under', type: 'line', source: ROAD_MATCH_SOURCE,
    paint: {
      'line-color': '#ff66d8',
      'line-width': ['interpolate', ['linear'], ['zoom'], 12, 5, 16, 12],
      'line-opacity': ['case', ['boolean', ['feature-state', 'under'], false], 1, 0]
    },
    layout: { 'line-cap': 'round', 'line-join': 'round' }
  })
  map.addLayer({
    id: 'mvp-road-corridor-direction', type: 'symbol', source: ROAD_MATCH_SOURCE,
    layout: {
      'symbol-placement': 'line',
      'symbol-spacing': 100,
      'icon-image': 'tt-arrow',
      'icon-size': 0.62,
      'icon-rotation-alignment': 'map',
      'icon-allow-overlap': false,
      'icon-ignore-placement': true
    },
    paint: {
      'icon-opacity': ['case',
        ['any',
          ['boolean', ['feature-state', 'under'], false],
          ['boolean', ['feature-state', 'ahead'], false]
        ],
        0.82,
        0
      ]
    }
  })
  map.addSource(ROAD_MATCH_LANE_SOURCE, { type: 'geojson', data: EMPTY_FC })
  map.addLayer({
    id: 'mvp-road-lanes-casing', type: 'line', source: ROAD_MATCH_LANE_SOURCE,
    minzoom: 15,
    paint: {
      'line-color': ['case',
        ['==', ['get', 'is_user_lane'], true], '#5ee7ff',
        'rgba(4, 10, 16, 0.88)'
      ],
      'line-width': ['case',
        ['==', ['get', 'is_user_lane'], true],
        ['interpolate', ['linear'], ['zoom'], 15, 7, 18, 13],
        ['interpolate', ['linear'], ['zoom'], 15, 4, 18, 9]
      ],
      'line-opacity': 0.94
    },
    layout: { 'line-cap': 'round', 'line-join': 'round' }
  })
  map.addLayer({
    id: 'mvp-road-lanes', type: 'line', source: ROAD_MATCH_LANE_SOURCE,
    minzoom: 15,
    paint: {
      'line-color': ['case',
        ['!=', ['get', 'speed_kmh'], null],
        ['interpolate', ['linear'], ['to-number', ['get', 'speed_kmh']],
          0, '#c8324a', 25, '#e34b3f', 45, '#ef8b36',
          65, '#f2d14a', 85, '#62c86b', 110, '#23a96a'
        ],
        ['match', ['get', 'lane_role'],
          'exit', '#ffbd5a', 'entry', '#62d7a1', 'weave', '#72b9ff', '#8293a1'
        ]
      ],
      'line-width': ['case',
        ['==', ['get', 'is_user_lane'], true],
        ['interpolate', ['linear'], ['zoom'], 15, 3, 18, 7],
        ['interpolate', ['linear'], ['zoom'], 15, 2, 18, 5.5]
      ],
      'line-opacity': ['case', ['==', ['get', 'lane_role'], 'unknown'], 0.72, 0.96]
    },
    layout: { 'line-cap': 'round', 'line-join': 'round' }
  })
  renderRoadMatchHud('waiting')
}

function updateRoadMatching ({ coords, heading = null, accuracy = 0, speedMps = null, timestamp = Date.now() }) {
  if (gpsState === GPS_STATES.OFF || !Array.isArray(coords)) return
  roadMatchLastFix = { coords: [...coords], heading, accuracy, speedMps, timestamp }
  roadMatchBoundedPush(roadMatchFixHistory, roadMatchLastFix, ROAD_MATCH_HISTORY_SIZE)
  if (roadMatchFeatures.features?.length) matchRoadFix(roadMatchLastFix)
  else renderRoadMatchHud(roadMatchLoading ? 'loading' : 'waiting')
  maybeFetchRoadCorridor(roadMatchLastFix)
}

function maybeFetchRoadCorridor (fix) {
  if (gpsState === GPS_STATES.OFF || document.visibilityState !== 'visible') return
  if (!Number.isFinite(fix.heading)) {
    renderRoadMatchHud('waiting', 'Wachten op rijrichting om de corridor te laden.')
    return
  }
  if (Number(fix.accuracy) > 120) {
    renderRoadMatchHud('uncertain', 'GPS-nauwkeurigheid is onvoldoende voor rijbaandetectie.')
    return
  }
  const moved = roadMatchLastFetchCoords
    ? roadMatchDistance(roadMatchLastFetchCoords, fix.coords)
    : Infinity
  const headingChanged = Number.isFinite(fix.heading) && Number.isFinite(roadMatchLastFetchHeading)
    ? roadMatchAngleDiff(fix.heading, roadMatchLastFetchHeading) >= ROAD_MATCH_REFETCH_HEADING_DEG
    : roadMatchLastFetchHeading !== fix.heading
  const elapsed = Date.now() - roadMatchLastFetchAt
  const refreshMs = roadMatchIsStationary(roadMatchFixHistory, fix.speedMps)
    ? 60_000
    : ROAD_MATCH_REFETCH_MS
  if (roadMatchLoading || (moved < ROAD_MATCH_REFETCH_DISTANCE_M && !headingChanged && elapsed < refreshMs)) return

  roadMatchController?.abort()
  roadMatchController = new AbortController()
  roadMatchLoading = true
  roadMatchLastFetchCoords = [...fix.coords]
  roadMatchLastFetchHeading = Number.isFinite(fix.heading) ? fix.heading : null
  roadMatchLastFetchAt = Date.now()
  if (!roadMatchCurrent) renderRoadMatchHud('loading')

  const lookahead = Math.round(Math.max(1800, Math.min(5000, 1800 + (Number(fix.speedMps) || 0) * 75)))
  const params = new URLSearchParams({
    lon: fix.coords[0].toFixed(7),
    lat: fix.coords[1].toFixed(7),
    accuracy_m: Math.max(0, Math.min(100, Number(fix.accuracy) || 0)).toFixed(1),
    lookahead_m: String(lookahead)
  })
  params.set('heading', ((Number(fix.heading) % 360 + 360) % 360).toFixed(1))

  fetch(`/api/roads/corridor?${params}`, { signal: roadMatchController.signal })
    .then(async response => {
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${response.status}`)
      }
      return response.json()
    })
    .then(data => {
      if (gpsState === GPS_STATES.OFF) return
      const fc = data?.type === 'FeatureCollection' ? data : (data?.roads || data?.segments)
      if (!fc || fc.type !== 'FeatureCollection' || !Array.isArray(fc.features)) {
        throw new Error('ongeldig corridorantwoord')
      }
      installRoadCorridor(fc)
      roadMatchLoading = false
      if (roadMatchLastFix) matchRoadFix(roadMatchLastFix)
    })
    .catch(error => {
      if (error.name === 'AbortError') return
      roadMatchLoading = false
      console.warn('[roads/corridor]', error.message)
      renderRoadMatchHud(roadMatchCurrent ? 'uncertain' : 'error', `Corridor niet beschikbaar: ${error.message}`)
    })
}

function installRoadCorridor (fc) {
  roadMatchCorridorFeatures = roadMatchNormalizeFeatures(fc.features, 'context')
  refreshRoadMatchFeatures()
  clearRoadMatchRoles()
  paintRoadMatchRoles([])
}

function roadMatchNormalizeFeatures (features, role) {
  const normalized = []
  for (const raw of features || []) {
    const id = raw?.properties?.internal_segment_id
    if (id === null || id === undefined || !raw.geometry) continue
    const properties = typeof CanonicalSegmentState === 'undefined'
      ? raw.properties
      : CanonicalSegmentState.flattenProperties(raw.properties)
    normalized.push({
      ...raw,
      id: String(id),
      properties: { ...properties, internal_segment_id: String(id), client_role: role }
    })
  }
  return normalized
}

function refreshRoadMatchFeatures () {
  const merged = new Map()
  for (const feature of roadMatchCorridorFeatures) {
    merged.set(String(feature.properties.internal_segment_id), feature)
  }
  // Path responses are newer and may carry fresher speed state for an edge
  // that was also returned by the geometric candidate corridor.
  for (const feature of roadMatchPathFeatures) {
    merged.set(String(feature.properties.internal_segment_id), feature)
  }
  const features = [...merged.values()]
  roadMatchFeatures = { type: 'FeatureCollection', features }
  roadMatchFeatureById = merged
  buildRoadMatchIndex(features)
  map.getSource(ROAD_MATCH_SOURCE)?.setData(roadMatchFeatures)
}

function buildRoadMatchIndex (features) {
  roadMatchGrid = new Map()
  roadMatchOutgoing = new Map()
  for (const feature of features) {
    const props = feature.properties
    const id = String(props.internal_segment_id)
    const from = roadMatchNodeKey(props.from_node_id)
    if (from !== null) {
      if (!roadMatchOutgoing.has(from)) roadMatchOutgoing.set(from, [])
      roadMatchOutgoing.get(from).push(feature)
    }
    for (const line of roadMatchLines(feature.geometry)) {
      for (let i = 0; i < line.length - 1; i++) {
        const a = line[i]
        const b = line[i + 1]
        const minX = Math.floor(Math.min(a[0], b[0]) / ROAD_MATCH_CELL_DEG)
        const maxX = Math.floor(Math.max(a[0], b[0]) / ROAD_MATCH_CELL_DEG)
        const minY = Math.floor(Math.min(a[1], b[1]) / ROAD_MATCH_CELL_DEG)
        const maxY = Math.floor(Math.max(a[1], b[1]) / ROAD_MATCH_CELL_DEG)
        for (let x = minX; x <= maxX; x++) {
          for (let y = minY; y <= maxY; y++) {
            const key = `${x}:${y}`
            if (!roadMatchGrid.has(key)) roadMatchGrid.set(key, [])
            roadMatchGrid.get(key).push({ id, feature, a, b })
          }
        }
      }
    }
  }
}

function matchRoadFix (fix) {
  const radius = Math.max(24, Math.min(100, (Number(fix.accuracy) || 8) * 1.7 + 12))
  const candidates = roadMatchCandidates(fix.coords, radius)
  const outcome = roadMatchCoreApi.matchFix({
    previousState: roadMatchCoreState,
    fix,
    history: roadMatchFixHistory,
    candidates,
    radius,
    options: { minConfidence: ROAD_MATCH_MIN_CONFIDENCE }
  })
  roadMatchCoreState = outcome.state
  roadMatchCurrent = roadMatchCoreState.current
  roadMatchAccepted = outcome.accepted

  if (!roadMatchAccepted || !roadMatchCurrent) {
    paintRoadMatchRoles([])
    const message = {
      'stationary-no-acquire': 'Wachten op beweging om de rijrichting vast te stellen.',
      'stationary-current-outside-candidates': 'Bekende rijbaan ligt niet in de huidige GPS-kandidaten.',
      'opposite-direction-conflict': 'Alle nabije segmenten lopen in de tegengestelde rijrichting.',
      'acquire-pending': 'Wegmatch bevestigen…',
      'acquire-low-confidence': 'Wegmatch heeft nog onvoldoende zekerheid.',
      'connected-switch-pending': 'Volgend verbonden wegsegment bevestigen…',
      'new-road-pending': 'Nieuwe weg of rijbaan nog niet bevestigd.'
    }[outcome.switchReason] || 'Geen directioneel wegsegment met voldoende zekerheid.'
    renderRoadMatchHud('uncertain', message)
    return
  }
  const matchedId = String(roadMatchCurrent.id)
  if (roadMatchTopology && roadMatchTopology.segmentId !== matchedId) {
    clearRoadMatchPath()
  }
  const ahead = roadMatchResolvedAhead(roadMatchCurrent.feature, 4)
  paintRoadMatchRoles(roadMatchAccepted ? [roadMatchCurrent.feature, ...ahead] : [])
  renderRoadMatchHud(roadMatchAccepted ? 'ready' : 'uncertain', null, ahead)
  maybeFetchConnectedRoadPath(matchedId)
}

function roadMatchCandidates (coords, radius) {
  const lat = coords[1]
  const lonCells = Math.ceil(radius / Math.max(1, 111320 * Math.cos(lat * Math.PI / 180) * ROAD_MATCH_CELL_DEG))
  const latCells = Math.ceil(radius / (110540 * ROAD_MATCH_CELL_DEG))
  const cx = Math.floor(coords[0] / ROAD_MATCH_CELL_DEG)
  const cy = Math.floor(coords[1] / ROAD_MATCH_CELL_DEG)
  const byId = new Map()
  for (let dx = -lonCells; dx <= lonCells; dx++) {
    for (let dy = -latCells; dy <= latCells; dy++) {
      for (const segment of roadMatchGrid.get(`${cx + dx}:${cy + dy}`) || []) {
        const projection = roadMatchProjectSegment(coords, segment.a, segment.b)
        if (projection.distance > radius) continue
        const candidate = { ...segment, distance: projection.distance, bearing: projection.bearing }
        const existing = byId.get(segment.id)
        if (!existing || candidate.distance < existing.distance) byId.set(segment.id, candidate)
      }
    }
  }
  return [...byId.values()]
    .sort((a, b) => a.distance - b.distance || a.id.localeCompare(b.id))
    .slice(0, roadMatchCoreApi.DEFAULTS.maxCandidates)
}

function roadMatchCommonAhead (feature, limit, outgoing = roadMatchOutgoing) {
  const result = []
  let current = feature
  const seen = new Set([String(current.properties.internal_segment_id)])
  while (result.length < limit) {
    const node = roadMatchNodeKey(current.properties.to_node_id)
    const options = (node === null ? [] : outgoing.get(node) || [])
      .filter(next => !seen.has(String(next.properties.internal_segment_id)))
    // At a fork there is no route evidence yet.  Choosing the geometrically
    // smoothest branch would make branch-specific traffic look applicable.
    if (options.length !== 1) break
    current = options[0]
    seen.add(String(current.properties.internal_segment_id))
    result.push(current)
  }
  return result
}

function roadMatchResolvedAhead (feature, limit) {
  const segmentId = String(feature.properties.internal_segment_id)
  if (roadMatchTopology?.segmentId === segmentId) {
    return roadMatchTopology.commonAhead
      .slice(0, limit)
      .map(id => roadMatchFeatureById.get(String(id)))
      .filter(Boolean)
  }
  // A geometric corridor is not an authoritative adjacency set: a branch can
  // sit just outside its radius. Until /roads/path confirms common_ahead, only
  // the segment under the vehicle is safe to display.
  return []
}

function maybeFetchConnectedRoadPath (segmentId) {
  if (gpsState === GPS_STATES.OFF || document.visibilityState !== 'visible') return
  const now = Date.now()
  if (roadMatchPathRequestedId === segmentId &&
      (roadMatchPathController || now - roadMatchPathFetchedAt < ROAD_MATCH_PATH_REFRESH_MS)) return

  roadMatchPathController?.abort()
  const controller = new AbortController()
  roadMatchPathController = controller
  roadMatchPathRequestedId = segmentId
  // Also acts as retry backoff when the path endpoint is temporarily
  // unavailable; GPS fixes must never create a request storm.
  roadMatchPathFetchedAt = now
  const lookahead = Math.round(Math.max(
    1800,
    Math.min(5000, 1800 + (Number(roadMatchLastFix?.speedMps) || 0) * 75)
  ))
  const params = new URLSearchParams({
    segment_id: segmentId,
    ahead_m: String(lookahead),
    behind_m: '250'
  })

  fetch(`/api/roads/path?${params}`, { signal: controller.signal })
    .then(async response => {
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${response.status}`)
      }
      return response.json()
    })
    .then(fc => {
      if (controller.signal.aborted || !roadMatchAccepted ||
          String(roadMatchCurrent?.id) !== segmentId) return
      const metadata = fc?.metadata
      if (fc?.type !== 'FeatureCollection' || !Array.isArray(fc.features) ||
          metadata?.under !== segmentId || !Array.isArray(metadata.common_ahead)) {
        throw new Error('ongeldig verbonden-padantwoord')
      }
      roadMatchPathFeatures = roadMatchNormalizeFeatures(fc.features, 'connected-path')
      roadMatchTopology = {
        segmentId,
        commonAhead: metadata.common_ahead.map(String),
        branches: Array.isArray(metadata.branches) ? metadata.branches : [],
        branchConfidence: Number(metadata.branch_confidence),
        truncated: metadata.truncated === true,
        terminalReason: metadata.terminal_reason || null
      }
      roadMatchPathFetchedAt = Date.now()
      roadMatchPathController = null
      refreshRoadMatchFeatures()
      const ahead = roadMatchResolvedAhead(roadMatchCurrent.feature, 4)
      paintRoadMatchRoles([roadMatchCurrent.feature, ...ahead])
      renderRoadMatchHud('ready', null, ahead)
    })
    .catch(error => {
      if (error.name === 'AbortError') return
      roadMatchPathController = null
      console.warn('[roads/path]', error.message)
    })
}

function clearRoadMatchPath () {
  roadMatchPathController?.abort()
  roadMatchPathController = null
  roadMatchPathFeatures = []
  roadMatchTopology = null
  roadMatchPathRequestedId = null
  roadMatchPathFetchedAt = 0
  refreshRoadMatchFeatures()
}

function paintRoadMatchRoles (path) {
  const underId = path[0]?.properties?.internal_segment_id == null
    ? null
    : String(path[0].properties.internal_segment_id)
  const aheadIds = new Set(path.slice(1).map(feature => String(feature.properties.internal_segment_id)))
  const changed = new Set([
    roadMatchRoleIds.under,
    underId,
    ...roadMatchRoleIds.ahead,
    ...aheadIds
  ])
  changed.delete(null)
  for (const id of changed) {
    map.setFeatureState(
      { source: ROAD_MATCH_SOURCE, id },
      { under: id === underId, ahead: aheadIds.has(id) }
    )
  }
  roadMatchRoleIds = { under: underId, ahead: aheadIds }
  renderRoadMatchMapLanes(path)
}

function clearRoadMatchRoles () {
  const ids = new Set([roadMatchRoleIds.under, ...roadMatchRoleIds.ahead])
  ids.delete(null)
  for (const id of ids) {
    map.setFeatureState({ source: ROAD_MATCH_SOURCE, id }, { under: false, ahead: false })
  }
  roadMatchRoleIds = { under: null, ahead: new Set() }
  renderRoadMatchMapLanes([])
}

function renderRoadMatchMapLanes (path) {
  const source = map.getSource(ROAD_MATCH_LANE_SOURCE)
  if (!source || typeof LaneTopology === 'undefined') return
  const boundedPath = (path || []).slice(0, 5).map(roadMatchWithUserLane)
  const key = JSON.stringify(boundedPath.map(feature => [
    feature.properties.internal_segment_id,
    feature.properties.lane_schema,
    feature.properties.lane_states,
    feature.properties.user_lane
  ]))
  if (key === roadMatchLaneMapKey) return
  roadMatchLaneMapKey = key
  source.setData(LaneTopology.mapFeatures({
    type: 'FeatureCollection',
    features: boundedPath
  }, { maxSegments: 5 }))
}

function roadMatchLaneDisplayFeature (currentFeature, aheadFeatures) {
  if (!currentFeature) return { feature: null, scope: null }
  const changedAhead = (Array.isArray(aheadFeatures) ? aheadFeatures : [aheadFeatures])
    .find(feature => roadMatchHasLaneChange(feature, currentFeature))
  if (changedAhead) {
    return { feature: changedAhead, scope: 'Rijstroken vooruit' }
  }
  return { feature: currentFeature, scope: 'Rijstroken onder je' }
}

function roadMatchHasLaneChange (feature, currentFeature) {
  if (!feature?.properties?.lane_schema) return false
  const model = LaneTopology.normalize(feature.properties)
  const current = LaneTopology.normalize(currentFeature?.properties || {})
  return model.count !== current.count || model.lanes.some(lane =>
    lane.role === 'exit' || lane.role === 'entry' || lane.role === 'weave'
  )
}

function renderRoadMatchLaneHud (currentFeature, aheadFeatures) {
  const target = document.getElementById('road-match-lanes')
  if (!target || typeof LaneTopology === 'undefined') return
  const selection = roadMatchLaneDisplayFeature(currentFeature, aheadFeatures)
  const displayFeature = selection.feature
    ? roadMatchWithUserLane(selection.feature)
    : null
  const model = displayFeature ? LaneTopology.normalize(displayFeature.properties) : null
  if (!selection.feature || !model?.count) {
    if (roadMatchLaneHudKey === null && target.classList.contains('hidden')) return
    roadMatchLaneHudKey = null
    target.replaceChildren()
    target.classList.add('hidden')
    target.removeAttribute('data-scope-label')
    return
  }
  const key = JSON.stringify([
    selection.scope,
    displayFeature.properties.internal_segment_id,
    displayFeature.properties.lane_schema,
    displayFeature.properties.lane_states,
    displayFeature.properties.user_lane
  ])
  if (key === roadMatchLaneHudKey) return
  roadMatchLaneHudKey = key
  target.dataset.scopeLabel = selection.scope
  target.classList.remove('hidden')
  LaneTopology.render(target, displayFeature.properties, {
    variant: 'driving',
    height: 90,
    laneWidth: 34,
    laneGap: 4,
    groupGap: 12,
    label: `${selection.scope} op ${roadMatchRoadLabel(displayFeature.properties)}`
  })
}

function roadMatchWithUserLane (feature) {
  if (!feature?.properties || typeof LaneTopology === 'undefined') return feature
  const observation = typeof simulationUserLaneObservation === 'function'
    ? simulationUserLaneObservation()
    : null
  if (!observation) return feature
  const laneCount = LaneTopology.normalize(feature.properties).count
  if (!laneCount || observation.number > laneCount) return feature
  return {
    ...feature,
    properties: { ...feature.properties, user_lane: observation }
  }
}

function renderRoadMatchHud (state, message = null, aheadFeatures = []) {
  const hud = document.getElementById('road-match-hud')
  if (!hud) return
  const active = gpsState !== GPS_STATES.OFF
  hud.classList.toggle('hidden', !active)
  document.body.classList.toggle('road-match-active', active)
  if (!active) return

  const status = document.getElementById('road-match-state')
  const road = document.getElementById('road-match-road')
  const live = document.getElementById('road-match-live')
  const limit = document.getElementById('road-match-limit')
  const ahead = document.getElementById('road-match-ahead')
  hud.dataset.state = state
  status.textContent = message || ({
    loading: 'Wegcorridor laden…', waiting: 'Wachten op lokale wegdata…',
    ready: `Rijbaanmatch ${Math.round((roadMatchCurrent?.confidence || 0) * 100)}%`,
    uncertain: 'Rijbaan onzeker — verkeersdata verborgen', error: 'Wegcorridor niet beschikbaar'
  }[state] || '')

  if (!roadMatchAccepted || !roadMatchCurrent) {
    road.textContent = 'Weg en rijrichting nog niet bevestigd'
    live.textContent = 'Live: —'
    limit.textContent = 'Max: —'
    ahead.textContent = 'Vooruit: —'
    renderRoadMatchLaneHud(null, null)
    return
  }
  const props = roadMatchCurrent.feature.properties
  const aheadFeature = Array.isArray(aheadFeatures) ? aheadFeatures[0] : aheadFeatures
  road.textContent = `${roadMatchRoadLabel(props)} · ${props.travel_direction || 'richting bevestigd'}`
  live.textContent = roadMatchSpeedLabel(props, 'Live')
  limit.textContent = `Max: ${roadMatchMaxspeedLabel(props.maxspeed)}`
  ahead.textContent = aheadFeature
    ? `Vooruit: ${roadMatchRoadLabel(aheadFeature.properties)} · ${roadMatchSpeedLabel(aheadFeature.properties, '')}`
    : roadMatchTopology?.segmentId === String(roadMatchCurrent.id) &&
      roadMatchTopology.branchConfidence === 0
        ? 'Vooruit: routekeuze onzeker — takinformatie verborgen'
        : 'Vooruit: einde van bevestigd verbonden pad'
  renderRoadMatchLaneHud(roadMatchCurrent.feature, aheadFeatures)
}

function roadMatchSpeedLabel (props, prefix) {
  if (typeof CanonicalSegmentState !== 'undefined') {
    const speed = CanonicalSegmentState.speedState(props)
    const detail = speed.usable
      ? [
          `${Math.round(speed.value)} km/h`,
          CanonicalSegmentState.methodLabel(speed),
          speed.sampleCount > 1 ? `${Math.round(speed.sampleCount)} meetpunten` : null,
          CanonicalSegmentState.provenanceLabel(speed)
        ].filter(Boolean).join(' · ')
      : 'geen betrouwbare meting'
    return prefix ? `${prefix}: ${detail}` : detail
  }
  const hasValue = props.speed_kmh !== null && props.speed_kmh !== undefined && props.speed_kmh !== ''
  const value = Number(props.speed_kmh)
  const confidence = Number(props.speed_confidence)
  const observedAt = Date.parse(props.speed_observed_at)
  const validUntil = Date.parse(props.speed_valid_until)
  const fresh = Number.isFinite(validUntil)
    ? Date.now() <= validUntil
    : Number.isFinite(observedAt) && Date.now() - observedAt <= ROAD_MATCH_SPEED_MAX_AGE_MS
  const usable = hasValue && Number.isFinite(value) && props.speed_stale !== true && fresh &&
    Number.isFinite(confidence) && confidence >= ROAD_MATCH_SPEED_MIN_CONFIDENCE
  const label = usable ? `${Math.round(value)} km/h` : 'geen betrouwbare meting'
  return prefix ? `${prefix}: ${label}` : label
}

// Path-bound live facts are only read from the accepted segment under the
// vehicle plus /roads/path common_ahead. Presence of a versioned segment_state
// is authoritative: an empty facts list means "nothing applicable", not a cue
// to fall back to a nearest-point lookup.
function roadMatchCanonicalRoadSigns () {
  if (!roadMatchAccepted || !roadMatchCurrent || typeof CanonicalSegmentState === 'undefined') return null
  const path = [roadMatchCurrent.feature, ...roadMatchResolvedAhead(roadMatchCurrent.feature, 4)]
  const authoritative = path.some(feature => {
    const state = feature?.properties?.segment_state
    if (state?.version === 1) return true
    if (typeof state !== 'string') return false
    try { return JSON.parse(state)?.version === 1 } catch { return false }
  })
  if (!authoritative) return null
  const length = Number(roadMatchCurrent.feature.properties?.length_m) || 0
  const fraction = roadMatchPositionFraction(roadMatchCurrent.feature.geometry, roadMatchLastFix?.coords)
  const currentOffset = fraction !== null
    ? Math.max(0, Math.min(length, fraction * length))
    : 0
  return roadMatchCanonicalFactsFromPath(path, currentOffset)
}

function roadMatchCanonicalFactsFromPath (path, currentOffsetM, stateApi = (
  typeof CanonicalSegmentState === 'undefined' ? null : CanonicalSegmentState
)) {
  if (!stateApi || !path.length) return { matrix: null, drip: null }
  let along = -Math.max(0, Number(currentOffsetM) || 0)
  let matrix = null
  let drip = null
  for (const feature of path) {
    const props = feature.properties || {}
    const matrixFacts = stateApi.canonicalFacts(props, 'matrix')
    const gantries = new Map()
    for (const fact of matrixFacts) {
      const offset = Number(fact.offset_m)
      const id = fact.gantry_id == null ? '' : String(fact.gantry_id)
      // Without both physical grouping and linear position we cannot establish
      // which portal is next when a short OSM segment contains multiple ones.
      if (!id || !Number.isFinite(offset) || offset < 0) continue
      if (!gantries.has(id)) gantries.set(id, { offset, facts: [] })
      const gantry = gantries.get(id)
      gantry.offset = Math.min(gantry.offset, offset)
      gantry.facts.push(fact)
    }
    for (const [gantryId, gantry] of gantries) {
      const distance = along + gantry.offset
      if (distance < 0 || (matrix && distance >= matrix.cls.along)) continue
      matrix = {
          data: {
            coords: null,
            bearing: null,
            road: props.ref || props.road_number || props.name,
            carriageway: props.travel_direction,
            km: null,
            gantry_id: gantryId,
            lanes: gantry.facts.map(fact => ({
              ...fact,
              lane: fact.lane,
              ts_state: fact.observed_at
            })).sort((left, right) => Number(left.lane) - Number(right.lane))
          },
          cls: { status: 'ahead', along: distance, cross: 0, dist: distance },
          canonical: true
        }
    }
    for (const fact of stateApi.canonicalFacts(props, 'drip')) {
      const offset = Number(fact.offset_m)
      if (!Number.isFinite(offset) || offset < 0) continue
      const distance = along + offset
      if (distance < 0 || (drip && distance >= drip.cls.along)) continue
      drip = {
          data: { ...fact, controller_id: fact.source_id },
          coords: null,
          cls: { status: 'ahead', along: distance, cross: 0, dist: distance },
          canonical: true
        }
    }
    along += Number(props.length_m) || 0
  }
  return { matrix, drip }
}

function roadMatchMaxspeedLabel (value) {
  if (value === null || value === undefined || value === '') return 'onbekend'
  const text = String(value).trim()
  return /^\d+(?:\.\d+)?$/.test(text) ? `${Math.round(Number(text))} km/h` : text
}

function resetRoadMatching () {
  roadMatchController?.abort()
  roadMatchPathController?.abort()
  roadMatchController = null
  roadMatchPathController = null
  roadMatchLoading = false
  roadMatchLaneMapKey = null
  roadMatchLaneHudKey = null
  roadMatchFeatures = ROAD_MATCH_EMPTY_FC
  roadMatchCorridorFeatures = []
  roadMatchPathFeatures = []
  roadMatchFeatureById = new Map()
  roadMatchGrid = new Map()
  roadMatchOutgoing = new Map()
  roadMatchFixHistory = []
  roadMatchCoreState = roadMatchCoreApi.initialState()
  roadMatchCurrent = null
  roadMatchAccepted = false
  roadMatchTopology = null
  roadMatchPathRequestedId = null
  roadMatchPathFetchedAt = 0
  roadMatchLastFetchCoords = null
  roadMatchLastFetchHeading = null
  roadMatchLastFetchAt = 0
  roadMatchLastFix = null
  clearRoadMatchRoles()
  map.getSource(ROAD_MATCH_SOURCE)?.setData(EMPTY_FC)
  // GPS-off hides the parent HUD before the regular renderer reaches its lane
  // branch. Clear the child explicitly so stale lane labels do not remain in
  // the accessibility tree or survive until a later drive.
  renderRoadMatchLaneHud(null, null)
  renderRoadMatchHud('waiting')
}

function roadMatchBoundedPush (array, value, limit) {
  return roadMatchCoreApi.boundedPush(array, value, limit)
}

function roadMatchTrajectoryHeading (history, reported) {
  return roadMatchCoreApi.trajectoryHeading(history, reported, history[history.length - 1]?.timestamp)
}

function roadMatchIsStationary (history, speedMps) {
  return roadMatchCoreApi.isStationary(history, speedMps)
}

function roadMatchProjectSegment (point, a, b) {
  return roadMatchCoreApi.projectSegment(point, a, b)
}

function roadMatchDistance (a, b) {
  return roadMatchCoreApi.distance(a, b)
}

// Fraction along a travel-directed feature geometry. Candidate projection `t`
// is local to one atomic pair of vertices and must never be multiplied by the
// full OSM segment length.
function roadMatchPositionFraction (geometry, point) {
  if (!Array.isArray(point)) return null
  const parts = []
  let total = 0
  for (const line of roadMatchLines(geometry)) {
    for (let index = 0; index < line.length - 1; index++) {
      const length = roadMatchDistance(line[index], line[index + 1])
      parts.push({ a: line[index], b: line[index + 1], start: total, length })
      total += length
    }
  }
  if (!(total > 0)) return null
  let best = null
  for (const part of parts) {
    const projection = roadMatchProjectSegment(point, part.a, part.b)
    if (!best || projection.distance < best.distance) {
      best = { distance: projection.distance, position: part.start + projection.t * part.length }
    }
  }
  return best ? Math.max(0, Math.min(1, best.position / total)) : null
}

function roadMatchBearing (a, b) {
  return roadMatchCoreApi.bearing(a, b)
}

function roadMatchAngleDiff (a, b) {
  return roadMatchCoreApi.angleDiff(a, b)
}

function roadMatchIsDirectedSuccessor (from, to) {
  return roadMatchCoreApi.isDirectedSuccessor(from, to)
}

function roadMatchConfidence (candidate, margin, radius, hasHeading, previous) {
  const syntheticAlternative = { ...candidate, id: '__margin__', score: candidate.score + margin }
  return roadMatchCoreApi.candidateConfidence(candidate, [candidate, syntheticAlternative], radius, hasHeading, previous)
}

function roadMatchNodeKey (value) {
  return value === null || value === undefined || value === '' ? null : String(value)
}

function roadMatchLines (geometry) {
  if (geometry?.type === 'LineString') return [geometry.coordinates || []]
  if (geometry?.type === 'MultiLineString') return geometry.coordinates || []
  return []
}

function roadMatchRoadLabel (props) {
  return props.road_ref || props.road_number || props.name || props.highway || 'Onbenoemde weg'
}

// Pure helpers are exported only in Node, keeping the browser build dependency-
// free while allowing deterministic matcher tests with node:test.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    roadMatchAngleDiff,
    roadMatchBearing,
    roadMatchBoundedPush,
    roadMatchConfidence,
    roadMatchCommonAhead,
    roadMatchDistance,
    roadMatchIsDirectedSuccessor,
    roadMatchIsStationary,
    roadMatchCanonicalFactsFromPath,
    roadMatchLaneDisplayFeature,
    roadMatchWithUserLane,
    roadMatchProjectSegment,
    roadMatchPositionFraction,
    roadMatchTrajectoryHeading
  }
}
