'use strict';

// Small, dependency-free adapter between the versioned backend segment-state
// contract and MapLibre/HUD presentation.  Keeping this normalization in one
// place prevents the viewport map and driving corridor from applying different
// freshness or confidence rules.
(function (root, factory) {
  const api = factory()
  if (typeof module === 'object' && module.exports) module.exports = api
  if (root) root.CanonicalSegmentState = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const MIN_CONFIDENCE = 0.60
  const MAX_FALLBACK_AGE_MS = 5 * 60_000
  const METHODS = new Set(['measured', 'interpolated', 'propagated', 'historical', 'user_observed', 'unknown'])

  function finite (value, min = -Infinity, max = Infinity) {
    if (value === null || value === undefined || value === '') return null
    const number = Number(value)
    return Number.isFinite(number) && number >= min && number <= max ? number : null
  }

  function object (value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null
  }

  function nestedState (properties) {
    const raw = properties?.segment_state
    if (typeof raw === 'string') {
      try { return object(JSON.parse(raw)) }
      catch { return null }
    }
    return object(raw)
  }

  function speedState (properties, now = Date.now()) {
    const state = nestedState(properties)
    const canonical = object(state?.speed)
    const source = canonical || properties || {}
    const value = finite(source.value_kmh ?? source.speed_kmh ?? source.value, 0, 300)
    const rawMethod = String(source.method ?? source.speed_method ?? (value === null ? 'unknown' : 'measured')).toLowerCase()
    const method = METHODS.has(rawMethod) ? rawMethod : 'unknown'
    const confidence = finite(source.confidence ?? source.speed_confidence, 0, 1)
    const observedAt = source.observed_at ?? source.measured_at ?? source.speed_observed_at ?? null
    const validUntil = source.valid_until ?? source.speed_valid_until ?? null
    const observedMs = Date.parse(observedAt)
    const validUntilMs = Date.parse(validUntil)
    const observedSafe = Number.isFinite(observedMs) && now - observedMs >= -30_000
    const fresh = observedSafe && (Number.isFinite(validUntilMs)
      ? now <= validUntilMs
      : now - observedMs <= MAX_FALLBACK_AGE_MS)
    const stale = source.stale === true || source.speed_stale === true || !fresh
    // A versioned canonical segment_state has already passed the backend's
    // accepted direction/carriageway binding and derivation policy. Do not
    // silently impose a second, stricter confidence threshold in presentation:
    // that used to hide valid direct readings and most propagated coverage.
    // Legacy flat properties retain the conservative client-side threshold.
    const confidenceAccepted = canonical
      ? confidence !== null
      : confidence !== null && confidence >= MIN_CONFIDENCE
    const usable = value !== null && method !== 'unknown' && !stale && confidenceAccepted
    const provenance = source.provenance ?? source.sources ?? source.source ??
      source.speed_source_ids ?? source.speed_source ?? null
    const sampleCount = finite(source.sample_count ?? source.speed_sample_count ?? source.source_count, 0, 100000)
    return Object.freeze({ value, method, confidence, observedAt, validUntil, stale, usable, provenance, sampleCount })
  }

  function flattenProperties (properties, now = Date.now()) {
    const speed = speedState(properties, now)
    return {
      ...properties,
      speed_kmh: speed.value,
      speed_method: speed.method,
      speed_confidence: speed.confidence,
      speed_observed_at: speed.observedAt,
      speed_valid_until: speed.validUntil,
      speed_stale: speed.stale,
      speed_usable: speed.usable,
      speed_display_opacity: speed.method === 'measured' ? 0.98 : speed.method === 'interpolated' ? 0.76 : 0.58
    }
  }

  function enrichFeatureCollection (fc, now = Date.now()) {
    if (!fc || fc.type !== 'FeatureCollection' || !Array.isArray(fc.features)) return fc
    return {
      ...fc,
      features: fc.features.map(feature => feature?.properties
        ? { ...feature, properties: flattenProperties(feature.properties, now) }
        : feature)
    }
  }

  function methodLabel (speed) {
    return ({
      measured: 'gemeten',
      interpolated: 'geïnterpoleerd',
      propagated: 'doorgezet',
      historical: 'historisch',
      user_observed: 'waargenomen'
    })[speed?.method] || 'onbekend'
  }

  function provenanceLabel (speed) {
    const raw = speed?.provenance
    if (Array.isArray(raw)) return raw.filter(Boolean).slice(0, 2).map(item =>
      typeof item === 'string' ? item : item?.name || item?.source || item?.site_id
    ).filter(Boolean).join(' + ')
    if (raw && typeof raw === 'object') return raw.name || raw.source || raw.site_id || null
    return raw ? String(raw) : null
  }

  function canonicalFacts (properties, kind) {
    const state = nestedState(properties)
    if (!state) return []
    const plural = kind === 'matrix' ? 'matrix' : 'drips'
    const alternate = kind === 'matrix' ? 'matrix_signals' : 'drip'
    const raw = state[plural] ?? state[alternate]
    const now = Date.now()
    return Array.isArray(raw) ? raw.filter(item => {
      if (!object(item) || item.stale === true) return false
      const confidence = finite(item.confidence, 0, 1)
      if (confidence === null || confidence < MIN_CONFIDENCE) return false
      const validUntil = Date.parse(item.valid_until)
      const observedAt = Date.parse(item.observed_at ?? item.updated_at)
      if (!Number.isFinite(observedAt) || now - observedAt < -30_000) return false
      return Number.isFinite(validUntil)
        ? now <= validUntil
        : Number.isFinite(observedAt) && now - observedAt >= -30_000 && now - observedAt <= MAX_FALLBACK_AGE_MS
    }) : []
  }

  function pointStatus (properties, now = Date.now()) {
    const status = String(properties?.binding_status ?? properties?.osm_binding_status ?? 'unbound').toLowerCase()
    const accepted = status === 'accepted'
    const measuredMs = Date.parse(properties?.measured_at)
    const validUntilMs = Date.parse(properties?.valid_until)
    const timestampFresh = Number.isFinite(validUntilMs)
      ? now <= validUntilMs
      : Number.isFinite(measuredMs) && now - measuredMs >= -30_000 && now - measuredMs <= MAX_FALLBACK_AGE_MS
    // Prefer the backend's configured freshness decision when present; the
    // local age window is solely a backward-compatible fallback.
    const observedSafe = Number.isFinite(measuredMs) && now - measuredMs >= -30_000
    const fresh = properties?.measurement_stale === true
      ? false
      : properties?.measurement_stale === false ? observedSafe : timestampFresh
    return Object.freeze({
      status,
      accepted,
      fresh,
      reliable: accepted && fresh,
      label: accepted ? (fresh ? 'gekoppeld' : 'gekoppeld, verouderd')
        : status === 'ambiguous' ? 'rijbaan onzeker' : status === 'rejected' ? 'koppeling afgewezen' : 'niet gekoppeld'
    })
  }

  // The point and road layers are fetched independently. Detect the narrow
  // case where a fresh accepted point is newer than the cached canonical road
  // state, so the caller can refresh that viewport without disabling the
  // geometry cache altogether.
  function newerPointSegments (pointCollection, roadCollection, now = Date.now()) {
    const roadObservedBySegment = new Map()
    for (const feature of roadCollection?.features || []) {
      const properties = feature?.properties || {}
      const segmentId = properties.internal_segment_id
      if (!segmentId) continue
      const observedAt = speedState(properties, now).observedAt
      const observedMs = Date.parse(observedAt)
      roadObservedBySegment.set(String(segmentId), Number.isFinite(observedMs) ? observedMs : -Infinity)
    }

    const newer = new Set()
    for (const feature of pointCollection?.features || []) {
      const properties = feature?.properties || {}
      const segmentId = properties.internal_segment_id
      if (!segmentId || !roadObservedBySegment.has(String(segmentId))) continue
      if (!pointStatus(properties, now).reliable) continue
      const measuredMs = Date.parse(properties.measured_at)
      if (Number.isFinite(measuredMs) && measuredMs > roadObservedBySegment.get(String(segmentId))) {
        newer.add(String(segmentId))
      }
    }
    return [...newer].sort()
  }

  return Object.freeze({
    MAX_FALLBACK_AGE_MS,
    MIN_CONFIDENCE,
    canonicalFacts,
    enrichFeatureCollection,
    flattenProperties,
    methodLabel,
    newerPointSegments,
    pointStatus,
    provenanceLabel,
    speedState
  })
})
