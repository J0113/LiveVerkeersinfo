'use strict'

// ─── Map ──────────────────────────────────────────────────────────────────────

// Selectable basemaps. Each is a raster tile source; switching swaps the
// 'carto' source + 'basemap' layer while leaving all feed layers on top intact.
const CARTO_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/attribution">CARTO</a>'
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
    attribution: '© <a href="https://www.esri.com/">Esri</a>, Maxar, Earthstar Geographics'
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

// ─── Map load: wire up sources, layers, and UI ────────────────────────────────

map.on('load', () => {
  const attribution = document.querySelector('.maplibregl-ctrl-attrib')
  if (attribution) {
    attribution.removeAttribute('open')
    attribution.classList.remove('maplibregl-compact-show')
  }

  addArrowImage()

  for (const layer of LAYERS) {
    // MSI gantries and speed points are HTML markers, not MapLibre layers.
    if (layer.geomType === 'msi' || layer.geomType === 'speed-points' || layer.geomType === 'osm-poc') continue

    const srcOpts = { type: 'geojson', data: EMPTY_FC }
    if (layer.promoteId) srcOpts.promoteId = layer.promoteId
    map.addSource(layer.key, srcOpts)
    const vis = enabled.has(layer.key) ? 'visible' : 'none'

    if (layer.geomType === 'road-network') {
      map.addLayer({
        id: `${layer.key}-casing`, type: 'line', source: layer.key,
        paint: layer.paint.casing,
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' },
        minzoom: layer.minZoom
      })
      map.addLayer({
        id: layer.key, type: 'line', source: layer.key,
        paint: layer.paint.line,
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' },
        minzoom: layer.minZoom
      })
      setupNwbDiagnostic(layer.key)
    } else if (layer.geomType === 'speed') {
      map.addLayer({
        id: 'speed-lanes-casing',
        type: 'line',
        source: layer.key,
        paint: {
          'line-color': '#1d3240',
          'line-width': TRAFFIC_LANE_CASING_WIDTH_PX,
          'line-opacity': 0.94
        },
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' }
      })
      map.addLayer({
        id: 'speed-lanes',
        type: 'line',
        source: layer.key,
        paint: {
          'line-color': ['case',
            ['==', ['get', 'speed_kmh'], null], '#777777',
            ['interpolate', ['linear'], ['get', 'speed_kmh'],
              0, '#8a8a8a', 30, '#ff3333', 50, '#ff8800',
              70, '#ffdd00', 90, '#00cc44'
            ]
          ],
          'line-width': TRAFFIC_LANE_FILL_WIDTH_PX,
          'line-opacity': 0.98
        },
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' }
      })
      setupClickPopup('speed-lanes')
    } else if (layer.geomType === 'polygon') {
      map.addLayer({ id: `${layer.key}-fill`, type: 'fill', source: layer.key, paint: layer.paint.fill, layout: { visibility: vis } })
      map.addLayer({ id: `${layer.key}-line`, type: 'line', source: layer.key, paint: layer.paint.line, layout: { visibility: vis } })
      setupClickPopup(`${layer.key}-fill`)
    } else if (layer.geomType === 'line') {
      if (layer.casing) {
        map.addLayer({
          id: `${layer.key}-casing`, type: 'line', source: layer.key,
          paint: layer.casing, layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' }
        })
      }
      map.addLayer({ id: layer.key, type: 'line', source: layer.key, paint: layer.paint, layout: { visibility: vis, 'line-cap': 'round' } })
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
    } else if (layer.geomType === 'local-osm-roads') {
      map.addLayer({
        id: `${layer.key}-casing`, type: 'line', source: layer.key,
        paint: layer.casing,
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' },
        minzoom: layer.minZoom
      })
      map.addLayer({
        id: layer.key, type: 'line', source: layer.key,
        paint: layer.paint,
        layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' },
        minzoom: layer.minZoom
      })
      map.addLayer({
        id: `${layer.key}-propagated`, type: 'line', source: layer.key,
        filter: ['all', ['==', ['get', 'speed_usable'], true], ['==', ['get', 'speed_method'], 'propagated']],
        paint: {
          'line-color': ['interpolate', ['linear'], ['to-number', ['get', 'speed_kmh']],
            0, '#c8324a', 25, '#e34b3f', 45, '#ef8b36',
            65, '#f2d14a', 85, '#62c86b', 110, '#23a96a'
          ],
          'line-width': layer.paint['line-width'],
          'line-opacity': 0.82,
          'line-dasharray': [1.2, 1.8],
          'line-offset': layer.paint['line-offset']
        },
        layout: { visibility: vis, 'line-cap': 'butt', 'line-join': 'round' },
        minzoom: layer.minZoom
      })
      map.addLayer({
        id: `${layer.key}-arrows`, type: 'symbol', source: layer.key,
        minzoom: Math.max(layer.minZoom, 14),
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 110,
          'icon-image': 'tt-arrow',
          'icon-size': 0.6,
          'icon-rotation-alignment': 'map',
          'icon-offset': layer.arrowOffset || [0, 0],
          'icon-allow-overlap': false,
          'icon-ignore-placement': true,
          visibility: vis
        },
        paint: { 'icon-opacity': 0.68 }
      })
      setupClickPopup(layer.key)
      if (layer.promoteId) setupLineSelection(layer.key)
    } else {
      map.addLayer({ id: layer.key, type: 'circle', source: layer.key, paint: layer.paint, layout: { visibility: vis } })
      setupClickPopup(layer.key)
    }
  }

  // Traffic is the primary visualization; keep it above optional references.
  if (map.getLayer('speed-lanes-casing')) map.moveLayer('speed-lanes-casing')
  if (map.getLayer('speed-lanes')) map.moveLayer('speed-lanes')

  // Diagnostic Overpass and the GPS corridor have dedicated sources. Add the
  // corridor last so its current/ahead highlights remain visible above the
  // lightweight local viewport context without duplicating viewport requests.
  initOsmPoc()
  initRoadMatching()

  buildLayerPanel()
  setupPanelToggles()
  fetchPublicConfig()
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

// Slide lane-speed labels along their line during pan/zoom/rotate so they
// stay on screen between refetches, instead of waiting on the debounced fetch.
map.on('move', () => updateLaneSpeedLayout())

// Re-evaluate verkeersborden hint + re-fetch on zoom change
map.on('zoom', () => {
  updateZoomHint()
  updateMatrixLayout()
  updateSpeedLayout()
  updateLaneSpeedLayout()
  // If verkeersborden just crossed zoom 13, trigger a fetch
  const layer = LAYERS.find(l => l.key === 'verkeersborden')
  if (layer && enabled.has('verkeersborden')) fetchLayer(layer)
})

// Keep roadside offsets correct while the map rotates (e.g. navigation mode).
map.on('rotate', () => { updateMatrixLayout(); updateSpeedLayout(); updateLaneSpeedLayout() })

// Refit the HUD matrix lanes when the viewport width changes (rotate phone, resize).
window.addEventListener('resize', () => fitMatrixLanes())
