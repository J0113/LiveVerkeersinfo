'use strict'

// ─── Popups ───────────────────────────────────────────────────────────────────

function setupClickPopup (mapLayerId) {
  map.on('click', mapLayerId, e => {
    if (!e.features?.length) return
    const props = e.features[0].properties
    if (activePopup) activePopup.remove()
    activePopup = new maplibregl.Popup({ maxWidth: '300px' })
      .setLngLat(e.lngLat)
      .setHTML(buildPopupHtml(props))
      .addTo(map)
  })
  map.on('mouseenter', mapLayerId, () => { map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', mapLayerId, () => { map.getCanvas().style.cursor = '' })
}

// Highlight the clicked feature via feature-state 'selected'. One selection at a
// time across the map; cleared when another feature is clicked.
function setupLineSelection (layerKey) {
  map.on('click', layerKey, e => {
    if (!e.features?.length) return
    const id = e.features[0].id
    if (id === undefined || id === null) return
    if (selectedFeature) {
      map.setFeatureState({ source: selectedFeature.source, id: selectedFeature.id }, { selected: false })
    }
    map.setFeatureState({ source: layerKey, id }, { selected: true })
    selectedFeature = { source: layerKey, id }
  })
}

// feature-state is wiped when a source's data is replaced; re-apply the current
// selection after a refresh so the highlight survives the 60s/​moveend refetch.
function reapplySelection (layerKey) {
  if (selectedFeature && selectedFeature.source === layerKey) {
    map.setFeatureState({ source: layerKey, id: selectedFeature.id }, { selected: true })
  }
}

function buildPopupHtml (props) {
  // Render image_b64 as an inline image if present
  let imageHtml = ''
  if (props.image_b64) {
    const fmt = props.image_format || 'png'
    imageHtml = `<img src="data:image/${esc(fmt)};base64,${props.image_b64}" style="max-width:100%;display:block;margin-bottom:6px;border-radius:3px;image-rendering:pixelated">`
  }

  const rows = Object.entries(props)
    .filter(([k, v]) => k !== 'image_b64' && k !== 'image_format' && v !== null && v !== undefined && v !== '')
    .map(([k, v]) => {
      let display = typeof v === 'object' ? JSON.stringify(v) : String(v)
      if (display.length > 130) display = display.slice(0, 130) + '…'
      return `<tr><td class="pk">${esc(k)}</td><td>${esc(display)}</td></tr>`
    })
  if (!rows.length && !imageHtml) return '<em style="color:#667">No properties</em>'
  return imageHtml + (rows.length ? `<table class="popup-table"><tbody>${rows.join('')}</tbody></table>` : '')
}

// esc moved to lib.js.

// ─── Layer panel ──────────────────────────────────────────────────────────────

function buildLayerPanel () {
  const panelBody = document.getElementById('panel-body')

  // HUD tiles live at the very top — they control the GPS-relative overlay,
  // independently of the map layers below.
  panelBody.appendChild(buildHudSection())

  for (const group of GROUPS) {
    const groupLayers = LAYERS.filter(l => l.group === group.key)
    if (!groupLayers.length) continue

    const section = document.createElement('div')
    section.className = 'group'

    if (groupLayers.length === 1) {
      section.appendChild(makeLayerRow(groupLayers[0], groupLayers, false))
    } else {
      section.appendChild(makeGroupHeader(group, groupLayers))
      for (const layer of groupLayers) {
        section.appendChild(makeLayerRow(layer, groupLayers, true))
      }
    }

    panelBody.appendChild(section)
  }
}

function buildHudSection () {
  const section = document.createElement('div')
  section.className = 'group'

  const header = document.createElement('label')
  header.className = 'group-header'
  const gcb = document.createElement('input')
  gcb.type = 'checkbox'
  gcb.dataset.group = 'hud'
  syncHudGroupCb(gcb)
  gcb.addEventListener('change', () => {
    for (const item of HUD_ITEMS) {
      if (gcb.checked) hudEnabled.add(item.key)
      else hudEnabled.delete(item.key)
      const childCb = document.getElementById(`cb-${item.key}`)
      if (childCb) childCb.checked = gcb.checked
    }
    persistHud()
    fetchRoadSignHud(true)
  })
  header.appendChild(gcb)
  header.append(' HUD')
  section.appendChild(header)

  for (const item of HUD_ITEMS) section.appendChild(makeHudRow(item))
  return section
}

function makeHudRow (item) {
  const label = document.createElement('label')
  label.className = 'layer-row indented'

  const cb = document.createElement('input')
  cb.type = 'checkbox'
  cb.id = `cb-${item.key}`
  cb.checked = hudEnabled.has(item.key)
  cb.addEventListener('change', () => {
    if (cb.checked) hudEnabled.add(item.key)
    else hudEnabled.delete(item.key)
    const parentCb = document.querySelector('input[data-group="hud"]')
    if (parentCb) syncHudGroupCb(parentCb)
    persistHud()
    fetchRoadSignHud(true)
  })

  const dot = document.createElement('span')
  dot.className = 'dot'
  dot.style.background = item.legendColor

  const nameSpan = document.createElement('span')
  nameSpan.textContent = item.label

  label.appendChild(cb)
  label.appendChild(dot)
  label.appendChild(nameSpan)
  return label
}

function syncHudGroupCb (cb) {
  const allOn = HUD_ITEMS.every(i => hudEnabled.has(i.key))
  const anyOn = HUD_ITEMS.some(i => hudEnabled.has(i.key))
  cb.checked = allOn
  cb.indeterminate = anyOn && !allOn
}

function makeGroupHeader (group, groupLayers) {
  const label = document.createElement('label')
  label.className = 'group-header'

  const cb = document.createElement('input')
  cb.type = 'checkbox'
  cb.dataset.group = group.key
  syncGroupCb(cb, groupLayers)

  cb.addEventListener('change', () => {
    for (const layer of groupLayers) {
      const childCb = document.getElementById(`cb-${layer.key}`)
      if (cb.checked) {
        enabled.add(layer.key)
        setLayerVisibility(layer, true)
        fetchLayer(layer)
        if (childCb) childCb.checked = true
      } else {
        enabled.delete(layer.key)
        setLayerVisibility(layer, false)
        map.getSource(layer.key)?.setData(EMPTY_FC)
        controllers[layer.key]?.abort()
        if (childCb) childCb.checked = false
      }
    }
    persistLayers()
    updateZoomHint()
  })

  label.appendChild(cb)
  label.append(` ${group.label}`)
  return label
}

function makeLayerRow (layer, groupLayers, indented) {
  const label = document.createElement('label')
  label.className = 'layer-row' + (indented ? ' indented' : '')

  const cb = document.createElement('input')
  cb.type = 'checkbox'
  cb.id = `cb-${layer.key}`
  cb.checked = enabled.has(layer.key)

  cb.addEventListener('change', () => {
    if (cb.checked) {
      enabled.add(layer.key)
      setLayerVisibility(layer, true)
      fetchLayer(layer)
    } else {
      enabled.delete(layer.key)
      setLayerVisibility(layer, false)
      map.getSource(layer.key)?.setData(EMPTY_FC)
      controllers[layer.key]?.abort()
    }
    // Sync parent group checkbox if this row is nested
    if (indented) {
      const parentCb = document.querySelector(`input[data-group="${layer.group}"]`)
      if (parentCb) syncGroupCb(parentCb, groupLayers)
    }
    persistLayers()
    updateZoomHint()
  })

  const dot = document.createElement('span')
  dot.className = 'dot'
  dot.style.background = layer.legendColor

  const nameSpan = document.createElement('span')
  nameSpan.textContent = layer.label
  if (layer.minZoom) {
    const badge = document.createElement('span')
    badge.className = 'zoom-badge'
    badge.textContent = `z${layer.minZoom}+`
    nameSpan.append(' ', badge)
  }

  label.appendChild(cb)
  label.appendChild(dot)
  label.appendChild(nameSpan)
  return label
}

function syncGroupCb (cb, groupLayers) {
  const allOn = groupLayers.every(l => enabled.has(l.key))
  const anyOn = groupLayers.some(l => enabled.has(l.key))
  cb.checked = allOn
  cb.indeterminate = anyOn && !allOn
}

function setLayerVisibility (layer, visible) {
  if (layer.geomType === 'msi') {
    // Enable path: the change handler calls fetchLayer → fetchMatrixSigns.
    if (!visible) { for (const m of msiMarkers) m.marker.remove(); msiMarkers = [] }
    return
  }
  if (layer.geomType === 'speed') {
    const vis = visible ? 'visible' : 'none'
    if (map.getLayer('speed-lanes')) map.setLayoutProperty('speed-lanes', 'visibility', vis)
    if (!visible) {
      for (const m of laneSpeedMarkers) m.marker.remove()
      laneSpeedMarkers = []
      map.getSource('speed')?.setData(EMPTY_FC)
    }
    return
  }
  if (layer.geomType === 'speed-points') {
    if (!visible) {
      for (const m of speedMarkers) m.marker.remove()
      speedMarkers = []
    }
    return
  }
  const vis = visible ? 'visible' : 'none'
  if (layer.geomType === 'line') {
    for (const fill of layer.fills || []) {
      if (map.getLayer(`${layer.key}-${fill.suffix}`)) {
        map.setLayoutProperty(`${layer.key}-${fill.suffix}`, 'visibility', vis)
      }
    }
    if (map.getLayer(`${layer.key}-casing`)) map.setLayoutProperty(`${layer.key}-casing`, 'visibility', vis)
    if (map.getLayer(layer.key)) map.setLayoutProperty(layer.key, 'visibility', vis)
    for (const ov of layer.overlays || []) {
      if (map.getLayer(`${layer.key}-${ov.suffix}`)) map.setLayoutProperty(`${layer.key}-${ov.suffix}`, 'visibility', vis)
    }
    if (map.getLayer(`${layer.key}-arrows`)) map.setLayoutProperty(`${layer.key}-arrows`, 'visibility', vis)
    if (map.getLayer(`${layer.key}-lane-arrows`)) map.setLayoutProperty(`${layer.key}-lane-arrows`, 'visibility', vis)
    return
  }
  if (layer.geomType === 'polygon') {
    if (map.getLayer(`${layer.key}-fill`)) map.setLayoutProperty(`${layer.key}-fill`, 'visibility', vis)
    if (map.getLayer(`${layer.key}-line`)) map.setLayoutProperty(`${layer.key}-line`, 'visibility', vis)
  } else {
    if (map.getLayer(layer.key)) map.setLayoutProperty(layer.key, 'visibility', vis)
  }
}

// ─── Panel toggles ────────────────────────────────────────────────────────────

function setupPanelToggles () {
  const settingsPanel = document.getElementById('settings-panel')
  const settingsToggle = document.getElementById('settings-toggle')
  const settingsBody = document.getElementById('settings-body')

  const setSettingsOpen = open => {
    settingsBody.classList.toggle('hidden', !open)
    settingsPanel.classList.toggle('open', open)
    settingsToggle.setAttribute('aria-expanded', String(open))
    settingsToggle.setAttribute('aria-label', open ? 'Close settings' : 'Open settings')
  }

  settingsToggle.addEventListener('click', event => {
    event.stopPropagation()
    setSettingsOpen(settingsBody.classList.contains('hidden'))
  })

  document.addEventListener('pointerdown', event => {
    if (!settingsBody.classList.contains('hidden') && !settingsPanel.contains(event.target)) setSettingsOpen(false)
  })
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') setSettingsOpen(false)
  })

  document.getElementById('panel-toggle').addEventListener('click', () => {
    const body = document.getElementById('panel-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('panel-toggle').setAttribute('aria-expanded', String(!nowHidden))
  })

  document.getElementById('status-toggle').addEventListener('click', () => {
    const body = document.getElementById('status-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('status-toggle').setAttribute('aria-expanded', String(!nowHidden))
    if (!nowHidden) fetchFeedStatus()
  })

  document.getElementById('basemap-toggle').addEventListener('click', () => {
    const body = document.getElementById('basemap-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('basemap-toggle').setAttribute('aria-expanded', String(!nowHidden))
  })
  renderBasemapOptions()

  document.getElementById('attribution-toggle').addEventListener('click', () => {
    const body = document.getElementById('attribution-body')
    const nowHidden = body.classList.toggle('hidden')
    document.getElementById('attribution-toggle').setAttribute('aria-expanded', String(!nowHidden))
  })
  renderAttributions()
}

// Lists every data provider's attribution statically, regardless of which
// layers are currently toggled on — attribution is owed per source, not per
// rendered layer.
function renderAttributions () {
  const body = document.getElementById('attribution-body')
  if (!body) return
  body.innerHTML = ATTRIBUTIONS.map(a => `
    <div class="layer-row" style="display:block">
      <a href="${a.url}" target="_blank" rel="noopener noreferrer">${esc(a.label)}</a>
      <div style="color:#7f93a8;font-size:11px">${esc(a.note)}</div>
    </div>`).join('')
}

// Swap the base raster tiles without disturbing feed layers on top. The basemap
// layer is re-added beneath the first non-basemap layer so it stays at the bottom.
function setBasemap (key) {
  const bm = BASEMAPS[key]
  if (!bm) return
  activeBasemap = key
  try { localStorage.setItem('basemap', key) } catch {}

  if (map.getLayer('basemap')) map.removeLayer('basemap')
  if (map.getSource('carto')) map.removeSource('carto')
  map.addSource('carto', {
    type: 'raster', tiles: bm.tiles, tileSize: bm.tileSize,
    maxzoom: bm.maxzoom, attribution: bm.attribution
  })
  const firstOther = map.getStyle().layers.find(l => l.id !== 'basemap')
  map.addLayer({ id: 'basemap', type: 'raster', source: 'carto' }, firstOther ? firstOther.id : undefined)

  renderBasemapOptions()
}

function renderBasemapOptions () {
  const body = document.getElementById('basemap-body')
  if (!body) return
  body.innerHTML = Object.entries(BASEMAPS).map(([key, bm]) => `
    <label class="layer-row">
      <input type="radio" name="basemap" value="${key}"${key === activeBasemap ? ' checked' : ''}>
      <span>${esc(bm.label)}</span>
    </label>`).join('')
  for (const input of body.querySelectorAll('input[name="basemap"]')) {
    input.addEventListener('change', () => setBasemap(input.value))
  }
}

// ─── Feed status ──────────────────────────────────────────────────────────────

function renderFeedStatus (data) {
  if (!data?.feeds) return
  const body = document.getElementById('status-body')
  body.innerHTML = data.feeds.map(f => {
    const dot = f.status === 'ok' ? '🟢'
      : f.status === 'error' ? '🔴'
      : f.status === 'not_modified' ? '🟡'
      : '⚪'
    const ago = f.finished_at ? timeAgo(f.finished_at) : '—'
    return `<div class="feed-row">
      <span>${dot}</span>
      <span class="feed-name">${esc(f.feed)}</span>
      <span class="feed-time">${ago}</span>
    </div>`
  }).join('')
}

function timeAgo (isoStr) {
  const sec = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000)
  if (sec < 5) return 'just now'
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  return `${Math.floor(sec / 3600)}h ago`
}

// ─── Zoom hint for verkeersborden ─────────────────────────────────────────────

function updateZoomHint () {
  const hint = document.getElementById('zoom-hint')
  if (bboxTooLarge) {
    hint.textContent = 'Zoom in — area too large to load data'
    hint.classList.remove('hidden')
  } else if (enabled.has('verkeersborden') && map.getZoom() < 13) {
    hint.textContent = 'Zoom in further to see traffic signs (zoom 13+)'
    hint.classList.remove('hidden')
  } else {
    hint.classList.add('hidden')
  }
}
