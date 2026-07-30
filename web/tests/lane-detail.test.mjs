import assert from 'node:assert/strict'
import { test } from 'node:test'

import { loadWeb } from './harness.mjs'

// Every property name the layer paints with must be one /api/osm/lane-lines
// actually returns (see get_osm_lane_lines in api/routers/osm.py) — the point of
// this layer is that the browser styles the payload as it arrives.
const LANE_LINE_PROPERTIES = new Set([
  'kind', 'id', 'road_id', 'segment_id', 'lane_nr', 'lane_count', 'direction',
  'offset_m', 'count_source', 'oneway_source', 'highway', 'name', 'ref',
  'turn:lanes', 'turn:lanes:forward', 'turn:lanes:backward',
  'placement', 'placement:forward', 'placement:backward',
  'placement:start', 'placement:end',
  'destination:lanes', 'destination:ref:lanes', 'change:lanes',
  'turn_lane', 'destination_lane', 'destination_ref_lane', 'change_lane',
  // marking sides, resolved server-side for lanes and connections alike
  'edge_left', 'edge_right', 'divider_left',
  // connection features
  'from', 'to', 'connection_type', 'confidence'
])

function readProperties (node, found = new Set()) {
  if (!Array.isArray(node)) return found
  if (node[0] === 'get' && typeof node[1] === 'string') found.add(node[1])
  for (const child of node) readProperties(child, found)
  return found
}

function layerConfig () {
  const web = loadWeb({ scripts: ['lib.js', 'config.js'] })
  return web.run(`LAYERS.find(layer => layer.key === 'lane_detail_v2')`)
}

// Arrays built inside the sandbox have that realm's Array prototype, so
// deepStrictEqual rejects them against a literal written here.
function sameExpression (actual, expected) {
  assert.equal(JSON.stringify(actual), JSON.stringify(expected))
}

test('Lane Detail reads /osm/lane-lines and is off by default', () => {
  const web = loadWeb({ scripts: ['lib.js', 'config.js'] })
  const layer = web.run(`LAYERS.find(layer => layer.key === 'lane_detail_v2')`)
  assert.equal(layer.label, 'Lane Detail')
  assert.equal(layer.endpoint, '/osm/lane-lines')
  // The retired osm_road_lane endpoint must not come back as a fallback.
  assert.notEqual(layer.endpoint, '/osm/lanes')
  assert.equal(layer.group, 'osm')
  assert.equal(layer.geomType, 'line')
  assert.equal(layer.minZoom, 15)
  assert.equal(layer.promoteId, 'id')
  assert.equal(layer.lineCap, 'butt')
  assert.equal(web.run(`DEFAULT_ENABLED.has('lane_detail_v2')`), false)
})

test('Lane Detail styles only properties the endpoint returns', () => {
  const layer = layerConfig()
  const used = readProperties([
    layer.paint,
    layer.filter,
    layer.overlays,
    layer.fills,
    layer.laneArrows
  ].map(part => JSON.parse(JSON.stringify(part ?? null))))
  for (const name of used) {
    assert.ok(
      LANE_LINE_PROPERTIES.has(name),
      `${name} is not returned by /api/osm/lane-lines`
    )
  }
  // The one Lane Detail property this layer has to do without.
  assert.ok(!used.has('width_m'))
})

test('Lane Detail draws bands at the lane-line cross-section spacing', () => {
  const web = loadWeb({ scripts: ['lib.js', 'config.js'] })
  assert.equal(web.run('LANE_LINE_SPACING_M'), 3.5)
  const layer = web.run(`LAYERS.find(layer => layer.key === 'lane_detail_v2')`)
  const width = layer.paint['line-width']
  assert.equal(width[0], 'interpolate')
  // metresWide folds the metre value into each zoom stop as ['*', metres, px],
  // so the band is one lane plus the seam overlap at every zoom.
  const [, metres, pixelsPerMetre] = width[4]
  assert.ok(Math.abs(metres - 3.56) < 1e-9, `${metres} is not 3.5 + the seam overlap`)
  assert.equal(pixelsPerMetre, web.run('pxPerMetre(15)'))
  assert.equal(layer.paint['line-color'], web.run('LANE_ASPHALT'))
})

test('Lane Detail paints the marking sides the endpoint resolved', () => {
  const layer = layerConfig()
  const bySuffix = Object.fromEntries(layer.overlays.map(o => [o.suffix, o]))
  assert.deepEqual(Object.keys(bySuffix).sort(), ['divider', 'edge-left', 'edge-right'])
  sameExpression(bySuffix['edge-left'].filter, ['==', ['get', 'edge_left'], true])
  sameExpression(bySuffix['edge-right'].filter, ['==', ['get', 'edge_right'], true])
  sameExpression(bySuffix.divider.filter, ['==', ['get', 'divider_left'], true])
  // No kind test on the markings: a connector that carries a lane straight on
  // across a way boundary is marked exactly like the lane it continues, and the
  // endpoint clears the flags on the junction movements that aren't.
  for (const overlay of layer.overlays) {
    assert.ok(!JSON.stringify(overlay.filter).includes('kind'))
  }
  assert.ok(bySuffix.divider.paint['line-dasharray'])
  // Dividers and the left edge sit on the driver's left; lane-line geometry is
  // stored in travel order, so that is a negative offset for fwd and bwd alike.
  const offsetMetres = overlay => overlay.paint['line-offset'][4][1]
  assert.equal(offsetMetres(bySuffix.divider), -1.75)
  assert.equal(offsetMetres(bySuffix['edge-left']), -1.75)
  assert.equal(offsetMetres(bySuffix['edge-right']), 1.75)
})

test('Lane Detail arrows use turn_lane and no counter-rotation', () => {
  const layer = layerConfig()
  const arrows = layer.laneArrows
  assert.equal(arrows.minZoom, 17)
  sameExpression(arrows.filter[1], ['==', ['get', 'kind'], 'lane'])
  sameExpression(
    arrows.filter[2],
    ['match', ['get', 'direction'], ['fwd', 'bwd'], true, false]
  )
  const iconImage = JSON.stringify(arrows.layout['icon-image'])
  assert.match(iconImage, /lane-arrow-/)
  assert.match(iconImage, /turn_lane/)
  // bwd geometry already runs in travel order (make_lane_line_rows reverses it),
  // flipping the glyph here would point the arrow at oncoming traffic.
  assert.equal(arrows.layout['icon-rotate'], undefined)
})

test('a truncated Lane Detail response still gets the zoom hint', () => {
  const web = loadWeb({ scripts: ['lib.js', 'config.js', 'ui.js', 'fetch.js'] })
  web.run(`setLayerTruncation('lane_detail_v2', { lanes: true, connections: false })`)
  assert.equal(web.isHidden('zoom-hint'), false)
  assert.match(web.textOf('zoom-hint'), /lane lines were truncated/)
  web.run(`setLayerTruncation('lane_detail_v2', null)`)
  assert.equal(web.isHidden('zoom-hint'), true)
})
