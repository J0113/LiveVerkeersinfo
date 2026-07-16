'use strict'

// Pure, deterministic road-matching state machine.  The browser adapter in
// road-match.js owns fetching, the spatial grid and rendering; this module only
// ranks a bounded candidate set and decides whether a match may be acquired,
// retained or switched.  Keeping timestamps in the input makes recorded GPS
// drives exactly replayable.
var RoadMatchCore = (() => {
  const DEFAULTS = Object.freeze({
    minConfidence: 0.62,
    strongAcquireConfidence: 0.80,
    strongAcquireMargin: 16,
    oppositeDirectionDeg: 105,
    maxCandidates: 64,
    maxAlternatives: 5
  })

  function initialState () {
    return { current: null, pendingId: null, pendingCount: 0, missCount: 0 }
  }

  function matchFix ({ previousState, fix, history = [], candidates = [], radius = 50, options = {} }) {
    const config = { ...DEFAULTS, ...options }
    const state = normalizeState(previousState)
    const stationary = isStationary(history, fix?.speedMps)
    const heading = stationary ? null : trajectoryHeading(history, fix?.heading, fix?.timestamp)
    const ranked = rankCandidates({
      candidates: candidates.slice(0, config.maxCandidates),
      heading,
      previous: state.current,
      radius,
      oppositeDirectionDeg: config.oppositeDirectionDeg
    })
    const eligible = ranked.filter(candidate => candidate.eligible)

    if (!eligible.length) {
      const missCount = state.missCount + 1
      const next = {
        current: missCount >= 2 ? null : state.current,
        pendingId: null,
        pendingCount: 0,
        missCount
      }
      const onlyOpposite = ranked.length > 0 && ranked.every(candidate => candidate.rejectionReason === 'opposite-direction')
      return result(next, {
        accepted: false,
        status: 'uncertain',
        stationary,
        heading,
        alternatives: ranked,
        switchReason: onlyOpposite ? 'opposite-direction-conflict' : 'no-candidates'
      }, config)
    }

    state.missCount = 0
    let best = eligible[0]
    const currentCandidate = state.current
      ? eligible.find(candidate => candidate.id === state.current.id)
      : null

    // Heading is intentionally ignored while stationary.  Never acquire or
    // switch based on compass noise; retaining an already established segment
    // is the only safe action.
    if (stationary) {
      if (!state.current || !currentCandidate) {
        const next = { ...state, pendingId: null, pendingCount: 0 }
        return result(next, {
          accepted: false,
          status: 'uncertain',
          stationary,
          heading,
          alternatives: ranked,
          switchReason: state.current ? 'stationary-current-outside-candidates' : 'stationary-no-acquire'
        }, config)
      }
      const confidence = candidateConfidence(currentCandidate, eligible, radius, false, state.current)
      const current = { ...currentCandidate, confidence }
      const next = { ...state, current, pendingId: null, pendingCount: 0 }
      return result(next, {
        accepted: confidence >= config.minConfidence,
        status: confidence >= config.minConfidence ? 'ready' : 'uncertain',
        stationary,
        heading,
        alternatives: ranked,
        switchReason: 'retained-stationary'
      }, config)
    }

    const bestConfidence = candidateConfidence(best, eligible, radius, Number.isFinite(heading), state.current)
    if (!state.current) {
      const strong = bestConfidence >= config.strongAcquireConfidence &&
        candidateMargin(best, eligible) >= config.strongAcquireMargin
      if (!strong) {
        const pending = confirmPending(state, best.id, 2)
        if (!pending.confirmed || bestConfidence < config.minConfidence) {
          const next = { ...state, pendingId: pending.pendingId, pendingCount: pending.pendingCount }
          return result(next, {
            accepted: false,
            status: 'uncertain',
            stationary,
            heading,
            alternatives: ranked,
            switchReason: bestConfidence < config.minConfidence ? 'acquire-low-confidence' : 'acquire-pending'
          }, config)
        }
      }
      const current = { ...best, confidence: bestConfidence }
      const next = { ...state, current, pendingId: null, pendingCount: 0 }
      return result(next, {
        accepted: true,
        status: 'ready',
        stationary,
        heading,
        alternatives: ranked,
        switchReason: strong ? 'acquired-strong' : 'acquired-confirmed'
      }, config)
    }

    if (best.id === state.current.id) {
      const current = { ...best, confidence: bestConfidence }
      const next = { ...state, current, pendingId: null, pendingCount: 0 }
      return result(next, {
        accepted: bestConfidence >= config.minConfidence,
        status: bestConfidence >= config.minConfidence ? 'ready' : 'uncertain',
        stationary,
        heading,
        alternatives: ranked,
        switchReason: 'retained-best'
      }, config)
    }

    const connected = isDirectedSuccessor(state.current.feature, best.feature)
    const currentScore = currentCandidate?.score ?? Infinity
    const improvement = currentScore - best.score
    const requiredImprovement = connected ? 3 : 9
    const pending = confirmPending(state, best.id, connected ? 2 : 3)
    const canSwitch = bestConfidence >= config.minConfidence &&
      improvement >= requiredImprovement && pending.confirmed

    if (canSwitch) {
      const current = { ...best, confidence: bestConfidence }
      const next = { ...state, current, pendingId: null, pendingCount: 0 }
      return result(next, {
        accepted: true,
        status: 'ready',
        stationary,
        heading,
        alternatives: ranked,
        switchReason: connected ? 'switched-connected' : 'switched-confirmed'
      }, config)
    }

    if (currentCandidate) {
      const confidence = candidateConfidence(currentCandidate, eligible, radius, Number.isFinite(heading), state.current)
      const current = { ...currentCandidate, confidence }
      const next = {
        ...state,
        current,
        pendingId: pending.pendingId,
        pendingCount: pending.pendingCount
      }
      return result(next, {
        accepted: confidence >= config.minConfidence,
        status: confidence >= config.minConfidence ? 'ready' : 'uncertain',
        stationary,
        heading,
        alternatives: ranked,
        switchReason: 'held-hysteresis'
      }, config)
    }

    const next = {
      ...state,
      pendingId: pending.pendingId,
      pendingCount: pending.pendingCount
    }
    return result(next, {
      accepted: false,
      status: 'uncertain',
      stationary,
      heading,
      alternatives: ranked,
      switchReason: connected ? 'connected-switch-pending' : 'new-road-pending'
    }, config)
  }

  function result (state, details, config) {
    const current = state.current
    const selected = current && details.alternatives.find(candidate => candidate.id === current.id)
    return {
      state,
      status: details.status,
      accepted: details.accepted,
      segmentId: details.accepted && current ? current.id : null,
      confidence: current?.confidence || 0,
      alternatives: details.alternatives.slice(0, config.maxAlternatives).map(publicCandidate),
      scoreBreakdown: selected?.scoreBreakdown || current?.scoreBreakdown || null,
      switchReason: details.switchReason,
      stationary: details.stationary,
      heading: details.heading,
      match: details.accepted ? current : null
    }
  }

  function publicCandidate (candidate) {
    return {
      id: candidate.id,
      score: Number.isFinite(candidate.score) ? candidate.score : null,
      distance: candidate.distance,
      bearing: candidate.bearing,
      headingDelta: candidate.headingDelta,
      eligible: candidate.eligible,
      rejectionReason: candidate.rejectionReason,
      scoreBreakdown: candidate.scoreBreakdown
    }
  }

  function rankCandidates ({ candidates, heading, previous, radius, oppositeDirectionDeg = DEFAULTS.oppositeDirectionDeg }) {
    return candidates.map(candidate => {
      const id = String(candidate.id ?? candidate.feature?.properties?.internal_segment_id)
      const headingDelta = Number.isFinite(heading) ? angleDiff(heading, candidate.bearing) : 0
      const opposite = Number.isFinite(heading) && headingDelta > oppositeDirectionDeg
      const same = previous?.id === id
      const successor = previous && isDirectedSuccessor(previous.feature, candidate.feature)
      const gradeConflict = previous && !same && !successor && hasGradeConflict(previous.feature, candidate.feature)
      const distancePenalty = Number(candidate.distance)
      const headingPenalty = Number.isFinite(heading) ? headingDelta * 0.38 : 13
      const continuityAdjustment = same ? -14 : successor ? -8 : previous ? 12 : 0
      const rejectionReason = opposite
        ? 'opposite-direction'
        : gradeConflict ? 'grade/topology-conflict' : null
      return {
        ...candidate,
        id,
        headingDelta,
        eligible: rejectionReason === null,
        rejectionReason,
        score: rejectionReason === null
          ? distancePenalty + headingPenalty + continuityAdjustment
          : Infinity,
        scoreBreakdown: {
          distance: distancePenalty,
          heading: headingPenalty,
          continuity: continuityAdjustment,
          total: rejectionReason === null
            ? distancePenalty + headingPenalty + continuityAdjustment
            : null,
          radius,
          sameSegment: Boolean(same),
          directedSuccessor: Boolean(successor),
          gradeConflict: Boolean(gradeConflict)
        }
      }
    }).sort((a, b) => {
      if (a.eligible !== b.eligible) return a.eligible ? -1 : 1
      return a.score - b.score || a.distance - b.distance || a.id.localeCompare(b.id)
    })
  }

  function candidateConfidence (candidate, eligible, radius, hasHeading, previous) {
    const distancePart = Math.max(0, 1 - candidate.distance / radius)
    const headingPart = hasHeading ? Math.max(0, 1 - candidate.headingDelta / 90) : 0.42
    const marginPart = Math.min(1, candidateMargin(candidate, eligible) / 18)
    const topologyPart = !previous || candidate.id === previous.id ||
      isDirectedSuccessor(previous.feature, candidate.feature) ? 1 : 0
    return Math.max(0, Math.min(1,
      distancePart * 0.45 + headingPart * 0.27 + marginPart * 0.18 + topologyPart * 0.10
    ))
  }

  function candidateMargin (candidate, eligible) {
    const other = eligible.find(item => item.id !== candidate.id)
    if (!other) return 30
    return Math.max(0, other.score - candidate.score)
  }

  function confirmPending (state, id, required) {
    const pendingCount = state.pendingId === id ? state.pendingCount + 1 : 1
    return { pendingId: id, pendingCount, confirmed: pendingCount >= required }
  }

  function normalizeState (state) {
    return state
      ? {
          current: state.current || null,
          pendingId: state.pendingId || null,
          pendingCount: Number(state.pendingCount) || 0,
          missCount: Number(state.missCount) || 0
        }
      : initialState()
  }

  function hasGradeConflict (from, to) {
    const a = gradeSignature(from?.properties)
    const b = gradeSignature(to?.properties)
    if (!a.explicit || !b.explicit) return false
    return a.layer !== b.layer || a.bridge !== b.bridge || a.tunnel !== b.tunnel
  }

  function gradeSignature (props = {}) {
    const bridge = activeTag(props.bridge)
    const tunnel = activeTag(props.tunnel)
    const layerPresent = props.layer !== null && props.layer !== undefined && props.layer !== ''
    const layer = layerPresent && Number.isFinite(Number(props.layer)) ? Number(props.layer) : 0
    return { bridge, tunnel, layer, explicit: bridge || tunnel || layerPresent }
  }

  function activeTag (value) {
    if (value === null || value === undefined || value === '') return false
    return !['no', 'false', '0'].includes(String(value).toLowerCase())
  }

  function boundedPush (array, value, limit) {
    array.push(value)
    if (array.length > limit) array.splice(0, array.length - limit)
    return array
  }

  function trajectoryHeading (history, reported, referenceTimestamp) {
    const now = Number.isFinite(referenceTimestamp)
      ? referenceTimestamp
      : history[history.length - 1]?.timestamp
    const recent = Number.isFinite(now)
      ? history.filter(fix => now - fix.timestamp <= 12_000)
      : history
    if (recent.length >= 2) {
      const first = recent[0].coords
      const last = recent[recent.length - 1].coords
      if (distance(first, last) >= 7) {
        const trajectory = bearing(first, last)
        return Number.isFinite(reported) ? lerpAngle(trajectory, reported, 0.35) : trajectory
      }
    }
    return Number.isFinite(reported) ? reported : null
  }

  function isStationary (history, speedMps) {
    if (Number.isFinite(speedMps) && speedMps > 1.2) return false
    if (history.length < 2) return true
    const latest = history[history.length - 1]
    const recent = history.filter(fix => latest.timestamp - fix.timestamp <= 8_000)
    return distance(recent[0].coords, recent[recent.length - 1].coords) < 6
  }

  function projectSegment (point, a, b) {
    const metresLon = 111320 * Math.cos(point[1] * Math.PI / 180)
    const metresLat = 110540
    const ax = (a[0] - point[0]) * metresLon
    const ay = (a[1] - point[1]) * metresLat
    const bx = (b[0] - point[0]) * metresLon
    const by = (b[1] - point[1]) * metresLat
    const dx = bx - ax
    const dy = by - ay
    const denom = dx * dx + dy * dy
    const t = denom ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denom)) : 0
    return {
      distance: Math.hypot(ax + t * dx, ay + t * dy),
      bearing: (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360,
      t
    }
  }

  function distance (a, b) {
    const lat = (a[1] + b[1]) * Math.PI / 360
    const dx = (b[0] - a[0]) * 111320 * Math.cos(lat)
    const dy = (b[1] - a[1]) * 110540
    return Math.hypot(dx, dy)
  }

  function bearing (a, b) {
    const lat = (a[1] + b[1]) * Math.PI / 360
    const dx = (b[0] - a[0]) * Math.cos(lat)
    const dy = b[1] - a[1]
    return (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360
  }

  function angleDiff (a, b) {
    return Math.abs(((a - b + 540) % 360) - 180)
  }

  function lerpAngle (a, b, t) {
    const delta = ((b - a + 540) % 360) - 180
    return (a + delta * t + 360) % 360
  }

  function isDirectedSuccessor (from, to) {
    const end = nodeKey(from?.properties?.to_node_id)
    const start = nodeKey(to?.properties?.from_node_id)
    return end !== null && start !== null && end === start
  }

  function nodeKey (value) {
    return value === null || value === undefined || value === '' ? null : String(value)
  }

  return Object.freeze({
    DEFAULTS,
    angleDiff,
    bearing,
    boundedPush,
    candidateConfidence,
    distance,
    hasGradeConflict,
    initialState,
    isDirectedSuccessor,
    isStationary,
    matchFix,
    projectSegment,
    rankCandidates,
    trajectoryHeading
  })
})()

if (typeof module !== 'undefined' && module.exports) module.exports = RoadMatchCore
