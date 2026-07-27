// Load the browser scripts into a sandbox so they can be unit-tested under
// `node --test`. The app is plain ordered <script> globals (see index.html), so
// running the sources in the same order in one context reproduces the browser's
// shared lexical scope exactly — no module wrappers, no source changes.

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const WEB_DIR = join(dirname(fileURLToPath(import.meta.url)), '..')

// ─── Minimal DOM ─────────────────────────────────────────────────────────────
// Only what the HUD render path actually touches: class toggling, text, and
// child replacement. Elements are created on demand and remembered, so a test
// can inspect any id the code asked for.

function createElement (id = '') {
  const element = {
    id,
    textContent: '',
    style: { setProperty () {}, removeProperty () {} },
    dataset: {},
    children: [],
    attributes: {},
    clientWidth: 200,
    clientHeight: 400,
    offsetWidth: 40,
    offsetHeight: 20,
    firstElementChild: null,
    classList: {
      _set: new Set(),
      add (...names) { for (const n of names) this._set.add(n) },
      remove (...names) { for (const n of names) this._set.delete(n) },
      contains (name) { return this._set.has(name) },
      toggle (name, force) {
        const on = force === undefined ? !this._set.has(name) : Boolean(force)
        if (on) this._set.add(name)
        else this._set.delete(name)
        return on
      },
    },
    append (...nodes) { element.children.push(...nodes) },
    appendChild (node) { element.children.push(node); return node },
    replaceChildren (...nodes) { element.children = [...nodes] },
    querySelectorAll () { return [] },
    addEventListener () {},
    setAttribute (name, value) { element.attributes[name] = value },
    removeAttribute (name) { delete element.attributes[name] },
    getBoundingClientRect () { return { top: 0, bottom: 120, height: 300, width: 60 } },
  }
  return element
}

function createDocument () {
  const byId = new Map()
  return {
    byId,
    getElementById (id) {
      if (!byId.has(id)) byId.set(id, createElement(id))
      return byId.get(id)
    },
    createElement () { return createElement() },
    createElementNS () { return createElement() },
    body: createElement('body'),
  }
}

function isHidden (context, id) {
  return context.document.getElementById(id).classList.contains('hidden')
}

// ─── Sandbox ─────────────────────────────────────────────────────────────────

const DEFAULT_SCRIPTS = ['lib.js', 'config.js', 'hud.js']

/**
 * Run the given web scripts in one sandbox.
 * `fetchImpl(url)` stands in for the network; it receives the request URL and
 * should resolve to a plain object (parsed JSON body).
 */
export function loadWeb ({ scripts = DEFAULT_SCRIPTS, fetchImpl } = {}) {
  const document = createDocument()
  const requests = []

  const context = {
    console,
    document,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame (fn) { return setTimeout(fn, 0) },
    AbortController,
    URLSearchParams,
    fetch (url, options) {
      requests.push(String(url))
      if (!fetchImpl) return Promise.reject(new Error(`unexpected fetch: ${url}`))
      return Promise.resolve(fetchImpl(String(url), options)).then(body => ({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
      }))
    },
  }
  context.window = context
  context.globalThis = context
  vm.createContext(context)

  for (const script of scripts) {
    vm.runInContext(readFileSync(join(WEB_DIR, script), 'utf8'), context, { filename: script })
  }

  context.requests = requests
  context.isHidden = id => isHidden(context, id)
  context.textOf = id => document.getElementById(id).textContent
  // `let`/`const` declarations live in the context's lexical scope, not on its
  // global object (exactly as in the browser), so reading or setting the app's
  // mutable state has to happen inside the sandbox.
  context.run = expression => vm.runInContext(expression, context)
  return context
}

// ─── Fixtures ────────────────────────────────────────────────────────────────

/** A speed-sensor point feature as /api/traffic/speed returns it. */
export function sensor ({
  siteId = 'RWS01_MONIBAS_0091hrl0572ra',
  coords = [4.7105, 52.5182],
  road = 'A9',
  carriageway = 'R',
  km = 12.0,
  speeds = [100],
  bearing = 0,
  highway = 'motorway',
  extra = {},
} = {}) {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: coords },
    properties: {
      site_id: siteId,
      road,
      carriageway,
      km,
      bearing,
      osm_highway: highway,
      lanes: speeds.map((speed_kmh, i) => ({ lane: i + 1, speed_kmh, flow_veh_h: 1200 })),
      ...extra,
    },
  }
}

export function featureCollection (features) {
  return { type: 'FeatureCollection', features }
}

// Metres per degree on the sphere lib.js measures distances on (haversine,
// R = 6371km), so a fixture offset of N metres really is N metres to the code
// under test.
const M_PER_DEG = (Math.PI * 6371000) / 180

/** Offset from [lon, lat] by metres east/north. */
export function offsetCoords ([lon, lat], { east = 0, north = 0 }) {
  return [
    lon + east / (M_PER_DEG * Math.cos((lat * Math.PI) / 180)),
    lat + north / M_PER_DEG,
  ]
}

/** Values crossing the sandbox boundary lose reference-equal prototypes. */
export function ids (items) {
  return [...items].map(item => String(item.data.site_id)).join(',')
}

export function closeTo (actual, expected, tolerance) {
  assertOk(
    Math.abs(actual - expected) <= tolerance,
    `${actual} is not within ${tolerance} of ${expected}`
  )
}

function assertOk (value, message) {
  if (!value) throw new Error(message)
}
