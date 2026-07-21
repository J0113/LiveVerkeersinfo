# Flitspalen.nl static speed cameras

Not an NDW file — a crowdsourced database of **fixed/permanent** speed cameras
from flitspalen.nl, complementing ANWB's *dynamic* radar reports
([12-anwb-incidents.md](12-anwb-incidents.md)).

- **Endpoint**: `POST https://www.flitspalen.nl/karte/`, form-encoded body
  (`xhr=1&action=all` + a bbox covering all of NL/Benelux/DE), gated behind
  `X-Requested-With`/`Accept`/`Origin`/`Referer`/`Cookie: LAN=nl` headers —
  see the `flitspalen_cameras` entry in `feeds.py` for the exact set.
- **Shape**: `{ "result": [ {...} ] }`, one flat list mixing NL/B/D — **hard
  filtered to `land == "NL"` and `status == "A"`** in `parsers/flitspalen.py`.
- **Status codes** (site's own "Kamerastatus" legend): `A` = actief (ingested),
  `L` = leeg (empty housing, not enforcing — excluded), `Z` = vernietigd
  (destroyed/removed — excluded).
- **`richtung`** (enforcement bearing) is always a plain integer 0–359 across
  the full NL subset — never the site's own compass-letter/`bs`/`db` display
  legend, which describes map-icon rendering, not the raw field.
- **`bubble`** (raw HTML with edit/dismantle links back to flitspalen.nl) is
  dropped entirely — never stored, never rendered.
- **Cadence**: weekly (`cadence_s: 604800`, `schedule_class: "background"`).
- **Storage**: `flitspalen_camera` table, `id` used directly as PK (globally
  unique across all 3 countries in the source).
- **API**: `GET /api/flitspalen?bbox=...`.
- **Web UI**: `flitspalen_cameras`, labeled "Speedcamera's", directly below
  "ANWB Speedcamera's" in the Situations group. Its `LAYERS` entry sets an
  explicit `limit: 1200` — the NL subset has ~994 active cameras, above the
  shared `api_default_limit` (500), which would otherwise silently truncate.
- Disabled by default via `DISABLED_FEEDS` (`docker-compose.yml`) — enable by
  removing `flitspalen_cameras` from that list.
