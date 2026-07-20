'use strict'

// ─── Traffic speed HTML markers ───────────────────────────────────────────────

// ── Traffic speed — lanes (zoom-gated line source + per-lane labels) ──────────
function fetchSpeedLanes () {
  controllers['speed']?.abort()
  const ctrl = new AbortController()
  controllers['speed'] = ctrl

  // Lane geometry only exists (and is only legible) when zoomed in. Below that,
  // clear the source so the lanes toggle simply shows nothing until you zoom.
  if (map.getZoom() < 14) {
    map.getSource('speed')?.setData(EMPTY_FC)
    for (const m of laneSpeedMarkers) m.marker.remove()
    laneSpeedMarkers = []
    return
  }

  const bbox = viewportBbox(false)
  fetch(`/api/traffic/speed/map?bbox=${bbox}&include_lanes=true`, { signal: ctrl.signal })
    .then(r => {
      if (r.status === 400) return r.json().then(body => Promise.reject(Object.assign(new Error(body.detail || 'Bad Request'), { isBboxError: /bbox area/i.test(body.detail || '') })))
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(data => {
      setBboxTooLargeHint(false)
      // Colour the section as a gradient between the sensors covering it; the raw
      // lanes (one feature per section, with a `sensors` list) drive the labels.
      const laneFc = data.lanes || EMPTY_FC
      map.getSource('speed')?.setData(buildGradientLanes(laneFc))
      for (const m of laneSpeedMarkers) m.marker.remove()
      laneSpeedMarkers = []
      if (enabled.has('speed')) renderLaneSpeedLabels(laneFc)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn('[speed]', e.message)
    })
}

// ── Traffic speed — points (roadside markers, shown at any zoom) ──────────────
function fetchSpeedPoints () {
  controllers['speed_points']?.abort()
  const ctrl = new AbortController()
  controllers['speed_points'] = ctrl

  // Pad the query only when zoomed in (small viewport) so edge markers show;
  // when zoomed out the raw viewport already covers a large area.
  const bbox = viewportBbox(map.getZoom() >= 14)
  fetch(`/api/traffic/speed/map?bbox=${bbox}&include_lanes=false`, { signal: ctrl.signal })
    .then(r => {
      if (r.status === 400) return r.json().then(body => Promise.reject(Object.assign(new Error(body.detail || 'Bad Request'), { isBboxError: /bbox area/i.test(body.detail || '') })))
      if (!r.ok) return Promise.reject(new Error(`HTTP ${r.status}`))
      return r.json()
    })
    .then(data => {
      setBboxTooLargeHint(false)
      renderSpeedPoints(data.points || EMPTY_FC)
    })
    .catch(e => {
      if (e.name === 'AbortError') return
      if (e.isBboxError) { setBboxTooLargeHint(true); return }
      console.warn('[speed-points]', e.message)
    })
}

function renderSpeedPoints (fc) {
  for (const m of speedMarkers) m.marker.remove()
  speedMarkers = []

  if (!enabled.has('speed_points')) return

  for (const f of fc.features) {
    if (!f.geometry) continue
    const p = f.properties
    const lanes = p.lanes || []
    if (!lanes.length) continue

    // Outer wrapper for maplibre positioning; inner row gets our rotate+scale.
    const wrapper = document.createElement('div')
    const el = document.createElement('div')
    el.className = 'speed-site'
    wrapper.appendChild(el)

    for (const lane of lanes) {
      const box = document.createElement('div')
      const kmh = lane.speed_kmh
      box.className = 'speed-lane'
      box.style.background = speedColor(kmh)
      box.style.color = speedTextColor(kmh)
      box.textContent = kmh !== null ? Math.round(kmh) : '?'
      box.title = `Lane ${lane.lane} · ${kmh !== null ? Math.round(kmh) + ' km/h' : 'no data'}${lane.flow_veh_h !== null ? ' · ' + Math.round(lane.flow_veh_h) + ' veh/h' : ''}`
      el.appendChild(box)
    }

    el.addEventListener('click', e => {
      e.stopPropagation()
      if (activePopup) activePopup.remove()
      const roadLabel = p.road ? `${esc(p.road)} ${esc(p.carriageway || '')} km ${p.km ?? ''}` : esc(p.site_id)
      const header = `<div style="font-size:11px;color:#6688aa;margin-bottom:6px">${roadLabel}</div>`
      const meta = buildPopupHtml({
        ...(p.road ? { road: p.road } : {}),
        ...(p.carriageway ? { carriageway: p.carriageway } : {}),
        ...(p.km != null ? { km: p.km } : {}),
        ...(p.measured_at ? { measured: p.measured_at } : {}),
        ...(p.bearing != null ? { bearing: p.bearing + '°' } : {}),
        ...(p.bearing_source ? { bearing_source: p.bearing_source } : {}),
        ...(p.tmc_direction ? { vild_direction: p.tmc_direction } : {}),
        ...(p.carriageway_source ? { carriageway_source: p.carriageway_source } : {}),
        ...(p.derived_carriageway ? { derived_carriageway: p.derived_carriageway } : {}),
        ...(p.derived_carriageway_source ? { derived_carriageway_source: p.derived_carriageway_source } : {}),
        ...(p.vild_hecto_dir != null ? { vild_hecto_dir: p.vild_hecto_dir } : {}),
        ...(p.carriageway_direction_conflict != null
          ? { carriageway_direction_conflict: p.carriageway_direction_conflict }
          : {}),
        ...(p.osm_source_id != null ? { osm_source_id: p.osm_source_id } : {}),
        ...(p.osm_direction ? { osm_direction: p.osm_direction } : {}),
        ...(p.osm_lane_count != null ? { osm_lane_count: p.osm_lane_count } : {}),
        ...(p.osm_distance_m != null ? { osm_distance_m: p.osm_distance_m } : {}),
        ...(p.osm_match_method ? { osm_match_method: p.osm_match_method } : {}),
        ...(p.osm_match_failure ? { osm_match_failure: p.osm_match_failure } : {}),
        ...(p.osm_highway ? { osm_highway: p.osm_highway } : {}),
        ...(p.osm_bearing != null ? { osm_bearing: `${p.osm_bearing}°` } : {}),
        ...(p.side ? { side: p.side } : {}),
      })
      const lanesHtml = lanes.map(l =>
        `<b style="color:#6688aa;font-size:11px">Lane ${l.lane ?? '?'}</b>` +
        buildPopupHtml({
          speed_kmh: l.speed_kmh !== null ? Math.round(l.speed_kmh) + ' km/h' : '—',
          flow_veh_h: l.flow_veh_h !== null ? Math.round(l.flow_veh_h) + ' veh/h' : '—',
        })
      ).join('<hr style="border-color:#2a2a40;margin:5px 0">')
      activePopup = new maplibregl.Popup({ maxWidth: '300px', offset: [0, -8] })
        .setLngLat(f.geometry.coordinates)
        .setHTML(header + meta + lanesHtml)
        .addTo(map)
    })

    // NL traffic keeps right: place the marker on the roadside to the right of
    // its VILD-oriented travel bearing. Opposite directions move apart.
    const offsetCompass = p.bearing != null ? (p.bearing + 90) % 360 : null

    const marker = new maplibregl.Marker({ element: wrapper, anchor: 'center' })
      .setLngLat(f.geometry.coordinates)
      .addTo(map)
    speedMarkers.push({ marker, el, offsetCompass })
  }

  updateSpeedLayout()
}

function renderLaneSpeedLabels (laneFc) {
  if (map.getZoom() < 16) return

  const bounds = currentBoundsBox()

  for (const feature of laneFc.features || []) {
    const p = feature.properties || {}
    if (!feature.geometry) continue

    // One label per sensor covering this section, so both readings show; fall
    // back to the winning speed for sections with no per-sensor list.
    const specs = (Array.isArray(p.sensors) && p.sensors.length)
      ? p.sensors
      : [{ measurement_coords: p.measurement_coords, speed_kmh: p.speed_kmh, flow_veh_h: p.flow_veh_h, measured_at: p.measured_at }]

    for (const s of specs) {
      const kmh = s.speed_kmh
      if (kmh === null || kmh === undefined) continue
      if (!Array.isArray(s.measurement_coords)) continue

      const best = projectPointOnLine(feature.geometry, s.measurement_coords)
      if (!best) continue

      // Stagger labels longitudinally so adjacent 3.5m lanes remain readable.
      const centerLane = ((p.lane_count || 1) + 1) / 2
      const shiftM = ((p.lane || 1) - centerLane) * 22
      // Pin the label at the sensor's own position on the section. Do NOT slide
      // it into the viewport: with several sensors per lane, clamping collapses
      // them all onto one spot (stacked speeds). Off-screen labels are skipped;
      // MapLibre re-positions the rest as the map pans.
      const basePosition = Math.max(0, Math.min(best.total, best.position + shiftM))
      const coords = coordAtDistance(best, basePosition)
      if (!coords) continue
      if (coords[0] < bounds.west || coords[0] > bounds.east ||
          coords[1] < bounds.south || coords[1] > bounds.north) continue

      const el = document.createElement('div')
      el.className = 'lane-speed-label'
      el.style.background = speedLimitColor(kmh, p.maxspeed_kmh)
      el.style.color = speedLimitTextColor(kmh, p.maxspeed_kmh)
      el.textContent = Math.round(kmh)
      el.title = `${p.road || p.road_number || ''} ${p.carriageway || ''} · lane ${p.lane} · ${Math.round(kmh)} km/h${p.maxspeed_kmh ? ` · limit ${p.maxspeed_kmh} km/h` : ''}`

      const flow = s.flow_veh_h
      el.addEventListener('click', e => {
        e.stopPropagation()
        if (activePopup) activePopup.remove()
        activePopup = new maplibregl.Popup({ maxWidth: '280px', offset: [0, -8] })
          .setLngLat(marker.getLngLat())
          .setHTML(buildPopupHtml({
            road: p.road,
            carriageway: p.carriageway,
            km: p.km,
            lane: p.lane,
            speed_kmh: Math.round(kmh) + ' km/h',
            maxspeed_kmh: p.maxspeed_kmh != null ? p.maxspeed_kmh + ' km/h' : '—',
            flow_veh_h: flow != null ? Math.round(flow) + ' veh/h' : '—',
            measured: s.measured_at || p.measured_at,
          }))
          .addTo(map)
      })

      const marker = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(coords).addTo(map)
      laneSpeedMarkers.push({ marker })
    }
  }
}

function currentBoundsBox () {
  const b = map.getBounds()
  return { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() }
}

// Find the point on `geometry` closest to `target`, and the running distances
// (metres) needed to walk to any other point along the same line.
function projectPointOnLine (geometry, target) {
  const lines = geometry.type === 'LineString'
    ? [geometry.coordinates]
    : geometry.type === 'MultiLineString' ? geometry.coordinates : []
  const latScale = 110540
  const lonScale = 111320 * Math.cos(target[1] * Math.PI / 180)
  let best = null

  for (const line of lines) {
    if (!line || line.length < 2) continue
    const lengths = []
    let total = 0
    for (let i = 0; i < line.length - 1; i++) {
      const dx = (line[i + 1][0] - line[i][0]) * lonScale
      const dy = (line[i + 1][1] - line[i][1]) * latScale
      const length = Math.hypot(dx, dy)
      lengths.push(length)
      total += length
    }

    let before = 0
    for (let i = 0; i < lengths.length; i++) {
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
      const distanceSq = px * px + py * py
      if (!best || distanceSq < best.distanceSq) {
        best = { line, lengths, total, position: before + t * lengths[i], distanceSq }
      }
      before += lengths[i]
    }
  }

  return best
}

// Coordinate at `distance` metres along the line described by a
// projectPointOnLine() result.
function coordAtDistance (best, distance) {
  let wanted = Math.max(0, Math.min(best.total, distance))
  for (let i = 0; i < best.lengths.length; i++) {
    if (wanted <= best.lengths[i] || i === best.lengths.length - 1) {
      const t = best.lengths[i] ? wanted / best.lengths[i] : 0
      return [
        best.line[i][0] + (best.line[i + 1][0] - best.line[i][0]) * t,
        best.line[i][1] + (best.line[i + 1][1] - best.line[i][1]) * t,
      ]
    }
    wanted -= best.lengths[i]
  }
  return null
}

// Sub-linestring of `best.line` between along-line distances d0..d1 (metres):
// the start point, every vertex strictly inside the span, then the end point.
function sliceLineCoords (best, d0, d1) {
  const start = Math.max(0, Math.min(best.total, d0))
  const end = Math.max(start, Math.min(best.total, d1))
  const out = [coordAtDistance(best, start)]
  let cum = 0
  for (let i = 0; i < best.lengths.length; i++) {
    cum += best.lengths[i]
    if (cum > start && cum < end) out.push(best.line[i + 1])
  }
  out.push(coordAtDistance(best, end))
  const dedup = []
  for (const c of out) {
    if (!c) continue
    const last = dedup[dedup.length - 1]
    if (!last || last[0] !== c[0] || last[1] !== c[1]) dedup.push(c)
  }
  return dedup
}

// Turn each matched OSM lane into short pieces whose speed is interpolated
// between the sensors covering the section, so line-color fades from one
// sensor's speed to the next along the road. Sections with <2 usable sensors
// (or non-LineString geometry) pass through unchanged.
function buildGradientLanes (laneFc) {
  const PIECES_PER_SPAN = 8
  const out = []
  for (const f of laneFc.features || []) {
    const p = f.properties || {}
    const geom = f.geometry
    const sensors = (Array.isArray(p.sensors) ? p.sensors : []).filter(
      s => s && s.speed_kmh !== null && s.speed_kmh !== undefined && Array.isArray(s.measurement_coords)
    )
    // Defensive client-side guard for older/cached API responses. Missing
    // readings belong only in the independently toggleable point layer.
    if ((p.speed_kmh === null || p.speed_kmh === undefined) && !sensors.length) continue
    if (!geom || geom.type !== 'LineString' || geom.coordinates.length < 2 || sensors.length < 2) {
      out.push(f)
      continue
    }

    const placed = []
    for (const s of sensors) {
      const best = projectPointOnLine(geom, s.measurement_coords)
      if (best) placed.push({ pos: best.position, speed: s.speed_kmh, best })
    }
    if (placed.length < 2) { out.push(f); continue }
    placed.sort((a, b) => a.pos - b.pos)
    // Merge sensors that project onto (nearly) the same point — averages the
    // co-located-flip pair instead of drawing a zero-length span between them.
    const nodes = [placed[0]]
    for (let i = 1; i < placed.length; i++) {
      const prev = nodes[nodes.length - 1]
      if (placed[i].pos - prev.pos < 1) {
        prev.speed = (prev.speed + placed[i].speed) / 2
      } else {
        nodes.push(placed[i])
      }
    }
    if (nodes.length < 2) { out.push(f); continue }

    const best = nodes[0].best  // same underlying line for every projection
    const props = { ...p }
    delete props.sensors  // keep per-piece features small
    const pushPiece = (d0, d1, speed) => {
      if (d1 - d0 < 0.5) return
      const coords = sliceLineCoords(best, d0, d1)
      if (coords.length < 2) return
      out.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: coords },
        properties: { ...props, speed_kmh: Math.round(speed * 10) / 10 },
      })
    }

    pushPiece(0, nodes[0].pos, nodes[0].speed)  // head: constant to first sensor
    for (let i = 0; i < nodes.length - 1; i++) {
      const a = nodes[i]
      const b = nodes[i + 1]
      const span = b.pos - a.pos
      for (let k = 0; k < PIECES_PER_SPAN; k++) {
        const t0 = k / PIECES_PER_SPAN
        const t1 = (k + 1) / PIECES_PER_SPAN
        const speed = a.speed + (b.speed - a.speed) * ((t0 + t1) / 2)
        pushPiece(a.pos + span * t0, a.pos + span * t1, speed)
      }
    }
    const tail = nodes[nodes.length - 1]
    pushPiece(tail.pos, best.total, tail.speed)  // tail: constant past last sensor
  }
  return { type: 'FeatureCollection', features: out }
}

// Keep fallback speed rows upright and offset them roadside using the bearing.
// Recomputed on zoom/rotate; no refetch required.
function updateSpeedLayout () {
  if (!speedMarkers.length) return
  const z = map.getZoom()
  const scale = Math.max(0.5, Math.min(1, 0.5 + (z - 11) * 0.125))
  const mapBearing = map.getBearing()

  for (const m of speedMarkers) {
    m.el.style.transform = `scale(${scale})`
    if (m.offsetCompass === null || m.offsetCompass === undefined) {
      m.marker.setOffset([0, 0])
      continue
    }
    // Numbers stay upright; only the marker's screen position shifts, toward
    // offsetCompass. Offset by the box's half-extent *along that direction* so
    // its edge sits at the sensor point (not half the full row width, which
    // overshot across the median on steep roads).
    const rel = ((m.offsetCompass - mapBearing) * Math.PI) / 180
    const ox = Math.sin(rel)
    const oy = -Math.cos(rel)
    const halfW = (m.el.offsetWidth * scale) / 2
    const halfH = (m.el.offsetHeight * scale) / 2
    const dist = Math.abs(ox) * halfW + Math.abs(oy) * halfH + 3
    m.marker.setOffset([ox * dist, oy * dist])
  }
}

// speedColor / speedTextColor moved to lib.js.

function fetchFeedStatus () {
  controllers['feed-status']?.abort()
  const ctrl = new AbortController()
  controllers['feed-status'] = ctrl
  fetch('/api/feeds', { signal: ctrl.signal })
    .then(r => r.ok ? r.json() : null)
    .then(renderFeedStatus)
    .catch(e => {
      if (e.name !== 'AbortError') console.warn('[feeds/status]', e.message)
    })
}

function setBboxTooLargeHint (show) {
  bboxTooLarge = show
  updateZoomHint()
}
