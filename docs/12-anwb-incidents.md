# ANWB incidents (jams / roadworks / dynamic radars)

Not an NDW file — a separate public JSON endpoint from ANWB, added alongside
the NDW catalog to cover jams, roadworks, and dynamic (mobile/temporary) speed
camera reports that NDW's feeds don't carry.

- **Endpoint**: `GET https://api.anwb.nl/routing/v1/incidents/incidents-desktop`
  — no auth, plain JSON, single nationwide payload.
- **Shape**: `{ success, dateTime, roads: [{ road, segments: [{ jams, roadworks, radars }] }] }`.
  All 3 categories share one envelope (`id`, `road`, `from`/`to`, `reason`,
  `events[]`); jams/roadworks additionally carry a Google encoded-polyline
  (precision 5, decoded in `parsers/anwb.py`); radars are point-only (`loc`, `HM`).
- **Cadence**: 5 minutes (`cadence_s: 300`, `schedule_class: "realtime"`).
- **Storage**: one table, `anwb_incident` (see `models.py`), category-qualified
  `record_id` (`f"{category}:{id}"`) since raw `id` isn't unique across categories.
- **Geometry**: LineString for jams/roadworks (decoded polyline, falling back to
  a straight `fromLoc`→`toLoc` chord, else `NULL`); Point for radars.
- **API**: `GET /api/anwb?bbox=...&category=jams|roadworks|radars`.
- **Web UI**: `anwb_jams` (Traffic group), `anwb_radars` + `anwb_roadworks`
  (Situations group, top of the section).
- Disabled by default via `DISABLED_FEEDS` (`docker-compose.yml`) — enable by
  removing `anwb_incidents` from that list.
