import assert from 'node:assert/strict'
import { test } from 'node:test'

import { loadWeb } from './harness.mjs'

test('legacy HUD preferences enable the later-added speed sidebar once', () => {
  const web = loadWeb({
    scripts: ['lib.js', 'config.js'],
    localStorageData: {
      hudLayers: JSON.stringify(['hud_speed', 'hud_matrix', 'hud_drips']),
    },
  })

  assert.equal(web.run(`hudEnabled.has('hud_speed_sidebar')`), true)
  assert.deepEqual(JSON.parse(web.storageValue('hudLayers')), {
    version: 1,
    enabled: ['hud_speed', 'hud_matrix', 'hud_drips', 'hud_speed_sidebar'],
  })
})

test('versioned HUD preferences preserve an explicitly disabled speed sidebar', () => {
  const web = loadWeb({
    scripts: ['lib.js', 'config.js'],
    localStorageData: {
      hudLayers: JSON.stringify({
        version: 1,
        enabled: ['hud_speed', 'hud_matrix', 'hud_drips'],
      }),
    },
  })

  assert.equal(web.run(`hudEnabled.has('hud_speed_sidebar')`), false)
})

test('persistHud writes the versioned preference shape', () => {
  const web = loadWeb({ scripts: ['lib.js', 'config.js'] })
  web.run(`hudEnabled.delete('hud_speed_sidebar'); persistHud()`)

  assert.deepEqual(JSON.parse(web.storageValue('hudLayers')), {
    version: 1,
    enabled: ['hud_speed', 'hud_matrix', 'hud_drips'],
  })
})
