import assert from 'node:assert/strict'
import { test } from 'node:test'

import { loadWeb } from './harness.mjs'

test('Lanes is an independent thin blue line layer and is off by default', () => {
  const web = loadWeb({ scripts: ['lib.js', 'config.js'] })
  const layer = web.run(`LAYERS.find(layer => layer.key === 'lanes')`)
  assert.equal(layer.label, 'Lanes')
  assert.equal(layer.endpoint, '/osm/lane-lines')
  assert.notEqual(layer.endpoint, '/osm/lanes')
  assert.equal(layer.legendColor, '#111171')
  assert.equal(layer.paint['line-color'], '#111171')
  assert.equal(layer.paint['line-width'], 2)
  assert.equal(layer.lineCap, 'butt')
  assert.equal(layer.lineJoin, 'round')
  assert.equal(web.run(`DEFAULT_ENABLED.has('lanes')`), false)
})

test('Lanes popup labels and exposes copyable stable IDs', () => {
  const web = loadWeb({ scripts: ['lib.js', 'config.js', 'ui.js'] })
  const html = web.buildPopupHtml({
    id: 'll:1:10:11:fwd:1',
    kind: 'lane',
    'turn:lanes': 'none|none|merge_to_left',
    turn_lane: 'merge_to_left',
    placement: 'right_of:1',
    destination_ref_lane: 'A9',
    change_lane: 'not_right'
  })
  assert.match(html, /Lanes ID/)
  assert.match(html, /popup-copy/)
  assert.match(html, /ll:1:10:11:fwd:1/)
  assert.match(html, /turn:lanes/)
  assert.match(html, /none\|none\|merge_to_left/)
  assert.match(html, /merge_to_left/)
  assert.match(html, /right_of:1/)
  assert.match(html, /A9/)
  assert.match(html, /not_right/)
})

test('Lanes truncation gets a visible per-kind hint and clears', () => {
  const web = loadWeb({ scripts: ['lib.js', 'config.js', 'ui.js', 'fetch.js'] })
  web.run(`setLayerTruncation('lanes', { lanes: false, connections: true })`)
  assert.equal(web.isHidden('zoom-hint'), false)
  assert.match(web.textOf('zoom-hint'), /Lanes connections were truncated/)
  web.run(`setLayerTruncation('lanes', null)`)
  assert.equal(web.isHidden('zoom-hint'), true)
})
