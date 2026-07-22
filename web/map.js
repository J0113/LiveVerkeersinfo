'use strict'

// ─── Map ──────────────────────────────────────────────────────────────────────

// Selectable basemaps. Each is a raster tile source; switching swaps the
// 'carto' source + 'basemap' layer while leaving all feed layers on top intact.
const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
const CARTO_ATTR = OSM_ATTR + ' © <a href="https://carto.com/attribution">CARTO</a>'
const BASEMAPS = {
  default: {
    label: 'Standaard',
    tiles: [
      'https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
      'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
      'https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png'
    ],
    tileSize: 256, maxzoom: 19, attribution: CARTO_ATTR
  },
  satellite: {
    label: 'Satelliet',
    tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    tileSize: 256, maxzoom: 19,
    // The imagery itself needs no OSM credit, but the OSM Driving Roads layer
    // (config.js) draws OSM/ODbL-licensed data on top of every basemap
    // including this one, which otherwise carries no OSM attribution at all.
    attribution: '© <a href="https://www.esri.com/">Esri</a>, Maxar, Earthstar Geographics — ' + OSM_ATTR
  },
  light: {
    label: 'Licht',
    tiles: [
      'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
      'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
      'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png'
    ],
    tileSize: 256, maxzoom: 19, attribution: CARTO_ATTR
  },
  dark: {
    label: 'Donker',
    tiles: [
      'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
      'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
      'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'
    ],
    tileSize: 256, maxzoom: 19, attribution: CARTO_ATTR
  }
}

const savedBasemap = (() => {
  try { return localStorage.getItem('basemap') } catch { return null }
})()
let activeBasemap = BASEMAPS[savedBasemap] ? savedBasemap : 'default'

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      carto: {
        type: 'raster',
        tiles: BASEMAPS[activeBasemap].tiles,
        tileSize: BASEMAPS[activeBasemap].tileSize,
        maxzoom: BASEMAPS[activeBasemap].maxzoom,
        attribution: BASEMAPS[activeBasemap].attribution
      }
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'carto' }]
  },
  center: [5.3, 52.1],
  zoom: 7,
  attributionControl: false,
  maplibreLogo: false,
  // Sync view (zoom/lat/lng/bearing/pitch) to the URL hash so a refresh restores it.
  hash: true
})

// Keep the bottom edge intentionally quiet: source credits remain available
// through the standard compact information button.
map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')

// Lane arrow glyphs are generated on demand: turn:lanes tokens combine freely,
// so the set of icons a viewport needs isn't known until its features arrive.
// MapLibre asks for each missing icon-image once and caches what we hand back.
function setupLaneArrowImages () {
  map.on('styleimagemissing', (e) => {
    const id = e.id
    if (!id.startsWith(LANE_ARROW_PREFIX) || map.hasImage(id)) return
    const image = laneArrowImage(id.slice(LANE_ARROW_PREFIX.length).split(';'))
    // Register a blank for token sets we don't draw (`reverse`, or anything new
    // OSM grows) — without an image on the id, MapLibre re-asks every frame.
    map.addImage(id, image || new ImageData(1, 1), { pixelRatio: ARROW_ICON_RATIO })
  })
}

// Speed-camera marker: a colour-coded dot large enough to hold a camera glyph,
// drawn to a canvas per legendColor (same on-demand-sprite approach as the lane
// arrows/tt-arrow above — no sprite sheet needed for a raster-tile style).
function cameraIconId (color) { return `camera-icon-${color.replace('#', '')}` }
function ensureCameraIcon (color) {
  const id = cameraIconId(color)
  if (map.hasImage(id)) return
  const s = 44
  const c = document.createElement('canvas')
  c.width = c.height = s
  const x = c.getContext('2d')

  x.beginPath()
  x.arc(s / 2, s / 2, s * 0.46, 0, Math.PI * 2)
  x.fillStyle = color
  x.fill()
  x.lineWidth = 2.5
  x.strokeStyle = '#ffffff'
  x.stroke()

  x.fillStyle = '#ffffff'
  const bodyW = s * 0.52
  const bodyH = s * 0.30
  const bodyX = s / 2 - bodyW / 2
  const bodyY = s / 2 - bodyH / 2 + s * 0.05
  x.fillRect(bodyX, bodyY, bodyW, bodyH)
  const bumpW = s * 0.16
  const bumpH = s * 0.08
  x.fillRect(s / 2 - bumpW * 0.9, bodyY - bumpH + 1, bumpW, bumpH)

  x.beginPath()
  x.arc(s / 2, bodyY + bodyH / 2, s * 0.135, 0, Math.PI * 2)
  x.fillStyle = color
  x.fill()
  x.beginPath()
  x.arc(s / 2, bodyY + bodyH / 2, s * 0.065, 0, Math.PI * 2)
  x.fillStyle = '#ffffff'
  x.fill()

  map.addImage(id, x.getImageData(0, 0, s, s), { pixelRatio: 2 })
}

// Charger marker: same colour-coded-dot approach as the speedcamera icon
// above, but a lightning-bolt glyph (the universal EV-charger symbol — a
// plug shape gets muddy at marker size) and pre-baked in 3 fixed colors
// since charging color varies per-feature (availability), not per-layer.
const CHARGER_GREEN = '#00cc44'
const CHARGER_RED = '#ff3333'
const CHARGER_GREY = '#888888'
function chargerIconId (color) { return `charger-icon-${color.replace('#', '')}` }
function ensureChargerIcon (color) {
  const id = chargerIconId(color)
  if (map.hasImage(id)) return
  const s = 44
  const c = document.createElement('canvas')
  c.width = c.height = s
  const x = c.getContext('2d')

  x.beginPath()
  x.arc(s / 2, s / 2, s * 0.46, 0, Math.PI * 2)
  x.fillStyle = color
  x.fill()
  x.lineWidth = 2.5
  x.strokeStyle = '#ffffff'
  x.stroke()

  // Bolt, drawn on a 24x24 grid centered in the circle then scaled to fit.
  const scale = s / 24
  x.save()
  x.translate(s / 2 - 12 * scale, s / 2 - 12 * scale)
  x.scale(scale, scale)
  x.beginPath()
  x.moveTo(13, 2)
  x.lineTo(4.5, 13.5)
  x.lineTo(11, 13.5)
  x.lineTo(9.5, 22)
  x.lineTo(19.5, 9.5)
  x.lineTo(13, 9.5)
  x.lineTo(13, 2)
  x.closePath()
  x.fillStyle = '#ffffff'
  x.fill()
  x.restore()

  map.addImage(id, x.getImageData(0, 0, s, s), { pixelRatio: 2 })
}

// ─── Map load: wire up sources, layers, and UI ────────────────────────────────

map.on('load', () => {
  const attribution = document.querySelector('.maplibregl-ctrl-attrib')
  if (attribution) {
    attribution.removeAttribute('open')
    attribution.classList.remove('maplibregl-compact-show')
  }

  addArrowImage()
  setupLaneArrowImages()

  for (const layer of LAYERS) {
    // MSI gantries and speed points are HTML markers, not MapLibre layers.
    if (layer.geomType === 'msi' || layer.geomType === 'speed-points') continue

    const srcOpts = { type: 'geojson', data: EMPTY_FC }
    if (layer.promoteId) srcOpts.promoteId = layer.promoteId
    map.addSource(layer.key, srcOpts)
    const vis = layerEnabled(layer) ? 'visible' : 'none'

    if (layer.geomType === 'speed') {
      map.addLayer({
        id: 'speed-lanes',
        type: 'line',
        source: layer.key,
        paint: {
          'line-color': speedLimitLineColorExpression(),
          'line-width': metresWide(['coalesce', ['get', 'width_m'], 3.5], 14),
          // The OSM lane markings and direction arrows remain readable below
          // the live-speed tint. HTML number labels are separate and stay opaque.
          'line-opacity': 0.55
        },
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' }
      })
      setupClickPopup('speed-lanes')
    } else if (layer.geomType === 'polygon') {
      map.addLayer({ id: `${layer.key}-fill`, type: 'fill', source: layer.key, paint: layer.paint.fill, layout: { visibility: vis } })
      map.addLayer({ id: `${layer.key}-line`, type: 'line', source: layer.key, paint: layer.paint.line, layout: { visibility: vis } })
      setupClickPopup(`${layer.key}-fill`)
    } else if (layer.geomType === 'line') {
      for (const fill of layer.fills || []) {
        map.addLayer({
          id: `${layer.key}-${fill.suffix}`,
          type: 'fill',
          source: layer.key,
          filter: fill.filter,
          paint: fill.paint,
          layout: { visibility: vis }
        })
      }
      if (layer.casing) {
        map.addLayer({
          id: `${layer.key}-casing`, type: 'line', source: layer.key,
          ...(layer.casingFilter ? { filter: layer.casingFilter } : {}),
          paint: layer.casing,
          // A casing that outlines a metre-scaled band has to end where the band
          // does, so it follows the band's own caps rather than rounding past it.
          layout: {
            visibility: vis,
            'line-cap': layer.lineCap || 'round',
            'line-join': layer.lineJoin || 'round'
          }
        })
      }
      map.addLayer({
        id: layer.key, type: 'line', source: layer.key,
        ...(layer.filter ? { filter: layer.filter } : {}),
        paint: layer.paint,
        // 'miter' is MapLibre's own default for line-join — spelled out here only
        // so a layer can override it; round caps would overshoot a metre-scaled
        // band by half its width past the geometry's end.
        layout: { visibility: vis, 'line-cap': layer.lineCap || 'round', 'line-join': layer.lineJoin || 'miter' }
      })
      for (const ov of layer.overlays || []) {
        map.addLayer({
          id: `${layer.key}-${ov.suffix}`, type: 'line', source: layer.key,
          ...(ov.filter ? { filter: ov.filter } : {}),
          paint: ov.paint, layout: { visibility: vis, 'line-cap': 'butt', 'line-join': 'round' }
        })
      }
      if (layer.laneArrows) {
        map.addLayer({
          id: `${layer.key}-lane-arrows`,
          type: 'symbol',
          source: layer.key,
          minzoom: layer.laneArrows.minZoom,
          filter: layer.laneArrows.filter,
          layout: { ...layer.laneArrows.layout, visibility: vis }
        })
      }
      if (layer.arrows) {
        map.addLayer({
          id: `${layer.key}-arrows`,
          type: 'symbol',
          source: layer.key,
          layout: {
            'symbol-placement': 'line',
            'symbol-spacing': 80,
            'icon-image': 'tt-arrow',
            'icon-size': 0.9,
            'icon-rotation-alignment': 'map',
            // Push arrows onto the offset line (perpendicular, matches line-offset
            // side). icon-offset rotates with the symbol on a line placement.
            'icon-offset': [0, 9],
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
            visibility: vis
          }
        })
      }
      setupClickPopup(layer.key)
      if (layer.promoteId) setupLineSelection(layer.key)
    } else if (layer.renderAs === 'camera-icon') {
      ensureCameraIcon(layer.legendColor)
      map.addLayer({
        id: layer.key, type: 'symbol', source: layer.key,
        layout: {
          'icon-image': cameraIconId(layer.legendColor),
          'icon-size': 1,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          visibility: vis
        }
      })
      setupClickPopup(layer.key)
    } else if (layer.renderAs === 'charger-icon') {
      ensureChargerIcon(CHARGER_GREEN)
      ensureChargerIcon(CHARGER_RED)
      ensureChargerIcon(CHARGER_GREY)
      map.addLayer({
        id: layer.key, type: 'symbol', source: layer.key,
        layout: {
          // Availability-driven, not 'open' — the feed's 'open' flag doesn't
          // reliably track live availability (docs/04-charging.md sample has
          // open:false with 3 connectors free), so it would mislabel stations.
          'icon-image': ['case',
            ['==', ['get', 'available_count'], null], chargerIconId(CHARGER_GREY),
            ['>', ['get', 'available_count'], 0], chargerIconId(CHARGER_GREEN),
            ['>', ['get', 'connector_total'], 0], chargerIconId(CHARGER_RED),
            chargerIconId(CHARGER_GREY)
          ],
          'icon-size': 1,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          visibility: vis
        }
      })
      setupClickPopup(layer.key)
    } else {
      map.addLayer({ id: layer.key, type: 'circle', source: layer.key, paint: layer.paint, layout: { visibility: vis } })
      setupClickPopup(layer.key)
    }
  }

  // Traffic is the primary visualization; keep it above optional references.
  if (map.getLayer('speed-lanes')) map.moveLayer('speed-lanes')

  // Charger icons would otherwise sit under speed-lanes/road fills.
  if (map.getLayer('charging')) map.moveLayer('charging')

  // Speedcameras/trajectcontrole stay on top of everything else — otherwise
  // Traffic Speed Lanes / Driving Roads / Lane Detail (all opaque, ground-scale
  // wide) bury the camera icons and dashed lines under them. moveLayer with no
  // beforeId puts the layer at the very top; order here is back-to-front so
  // flitspalen_cameras ends up highest.
  if (map.getLayer('flitspalen_pairs')) map.moveLayer('flitspalen_pairs')
  if (map.getLayer('flitspalen_cameras')) map.moveLayer('flitspalen_cameras')

  buildLayerPanel()
  setupPanelToggles()
  fetchAll()

  // ─── Geolocation Source & Layers ───────────────────────────────────────────
  map.addSource('user-accuracy', { type: 'geojson', data: EMPTY_FC })
  map.addLayer({
    id: 'user-accuracy-fill',
    type: 'fill',
    source: 'user-accuracy',
    paint: {
      'fill-color': '#3897ff',
      'fill-opacity': 0.12
    }
  })
  map.addLayer({
    id: 'user-accuracy-line',
    type: 'line',
    source: 'user-accuracy',
    paint: {
      'line-color': '#3897ff',
      'line-width': 1.5,
      'line-opacity': 0.35,
      'line-dasharray': [2, 2]
    }
  })

  initGPS()

  setInterval(() => {
    if (document.visibilityState === 'visible') fetchAll()
  }, 60_000)
  setInterval(() => {
    if (document.visibilityState === 'visible' && !document.getElementById('status-body').classList.contains('hidden')) {
      fetchFeedStatus()
    }
  }, 60_000)
})

map.on('moveend', (e) => {
  if (document.visibilityState !== 'visible') return
  // The follow loop pans the camera every frame with programmatic jumpTo (no
  // originalEvent). Refetching layers on those would hammer the API, so only
  // user-driven moves trigger a refetch here; while auto-following, layers
  // refresh on the 60s interval and the HUD refetches on each GPS fix.
  if (gpsState !== GPS_STATES.OFF && !e.originalEvent && !isTrackingSuspended) return
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(fetchAll, 300)
})

// Re-evaluate verkeersborden hint + re-fetch on zoom change
map.on('zoom', () => {
  updateZoomHint()
  updateMatrixLayout()
  updateSpeedLayout()
  // If verkeersborden just crossed zoom 13, trigger a fetch
  const layer = LAYERS.find(l => l.key === 'verkeersborden')
  if (layer && enabled.has('verkeersborden')) fetchLayer(layer)
})

// Keep roadside offsets correct while the map rotates (e.g. navigation mode).
map.on('rotate', () => { updateMatrixLayout(); updateSpeedLayout() })

// Refit responsive HUD elements when the viewport changes (rotate phone, resize).
window.addEventListener('resize', () => {
  fitMatrixLanes()
  layoutSpeedSidebar()
})
