"""Derive every-100m hectometerpaal markers from measurement-site km anchors.

Not a polled feed — a derivation over already-ingested tables, re-run after
osm_road refreshes (see call site in ingest/osm_roads.py's OsmRoadIngester),
the same pattern as ingest/vild_direction.py's rebuild_speed_site_directions.

v1 tried deriving km by interpolating each vild_tmc chain link's own
HSTART_POS/HEND_POS across the geometric distance to its POS_OFF neighbour.
Verified against real distances at scale (1127 links) and found wrong for
95% of them (median ratio ~6x, worst >1000x) — those fields describe a
node's own short physical footprint (an exit ramp's taper, a bridge's own
length), not the mainline distance to the next chain node. Most usable
entries are such point features; the actual mainline-segment entries
(LOC_TYPE L1.x) are ~95% HECTO_DIR=0 and unusable. Abandoned; see git history
for the discarded VILD-chain version.

v2 (this version) instead anchors on `measurement_site` rows, whose
road/km/carriageway are already decoded independently and reliably by
parsers/datex_v2.py's structured site-id/name parsing (RWS08 encoding,
GEO*/RWSTI encoding, "N457 hmp 4.75 Re" text) — confirmed 19,451 sites
nationwide carry all of road+km+carriageway+geom, at typical 100-800m
spacing on a spot-checked A9 stretch (consistent with real sensor density,
not the earlier catastrophic mismatch). Consecutive same-(road,carriageway)
anchors, sorted by km, are connected by a straight chord and split into
100m steps with km distributed linearly along it — accepted as adequate for
signage placement (an anchor pair 1-3km apart on a gently curving road keeps
along-road error under the ~50m the sign placement can tolerate).

Each anchor point already sits on its own physical carriageway (it's a real
sensor on that carriageway), so — unlike v1 — no separate left/right offset
step is needed: the chord between two same-carriageway anchors already
traces that carriageway. OSM is used only to snap the interpolated point
onto the nearest matching-`ref` osm_road way when one exists nearby, for
cleaner visual placement on the drawn road line.

Known v1/v2-shared limitation: coverage follows sensor density, not the
full road network — a road/carriageway with zero or one resolved anchor
gets no interpolated points. This is a real, expected gap (matches the
physical extent of the NDW sensor network), not a bug. Stale rows from
a since-removed anchor pair are also not pruned here (upsert-only).
"""

from __future__ import annotations

import logging

from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, Point
from shapely.ops import transform
from shapely.strtree import STRtree
from sqlalchemy import func, select

from ndwinfo.ingest.base import BATCH_SIZE, bulk_upsert, wkt_geom
from ndwinfo.models import HectometerPoint, MeasurementSite, OsmRoad

logger = logging.getLogger(__name__)

_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)

STEP_M = 100.0
OSM_MATCH_RADIUS_M = 50.0
_CARRIAGEWAY_LABEL = {"R": "Re", "L": "Li"}


def _load_osm_ref_trees(session) -> dict[tuple[str, str], tuple[STRtree, list, list[int]]]:
    """(road ref, "Li"|"Re") -> (STRtree of RD-projected ways, geoms, osm_ids).

    Keyed by carriageway too, not just ref: OSM's `carriageway_ref` tag marks
    mainline motorway ways "Li"/"Re" and ramps/connectors with a single
    lowercase letter ("a".."z") — snapping a "Li" marker onto a same-ref way
    without checking this tag can land it on a ramp instead of the mainline
    (observed: an "A9 Li" marker snapped onto a `carriageway_ref: c` slip
    road). Only "Li"/"Re"-tagged ways are indexed, so a ramp is never a
    candidate regardless of distance.

    A way's `ref` can be multi-valued ("A9;A4"); registered under every token
    so a lookup by either ref finds it.
    """
    rows = session.execute(
        select(OsmRoad.osm_id, OsmRoad.ref, OsmRoad.raw, func.ST_AsText(OsmRoad.geom)).where(
            OsmRoad.geom.isnot(None), OsmRoad.ref.isnot(None)
        )
    ).all()

    by_key: dict[tuple[str, str], list[tuple[int, LineString]]] = {}
    for osm_id, ref, raw, wkt_text in rows:
        carriageway_ref = (raw or {}).get("carriageway_ref")
        if carriageway_ref not in ("Li", "Re"):
            continue
        try:
            line = transform(_WGS84_TO_RD.transform, shapely_wkt.loads(wkt_text))
        except (TypeError, ValueError):
            continue
        for token in (t.strip() for t in ref.split(";")):
            if token:
                by_key.setdefault((token, carriageway_ref), []).append((osm_id, line))

    trees: dict[tuple[str, str], tuple[STRtree, list, list[int]]] = {}
    for key, items in by_key.items():
        geoms = [line for _, line in items]
        ids = [osm_id for osm_id, _ in items]
        trees[key] = (STRtree(geoms), geoms, ids)
    return trees


def _snap_to_osm(
    trees: dict[tuple[str, str], tuple[STRtree, list, list[int]]],
    road: str,
    carriageway_label: str,
    point_rd: Point,
) -> tuple[Point, int | None]:
    entry = trees.get((road, carriageway_label))
    if entry is None:
        return point_rd, None
    tree, geoms, ids = entry
    best_dist: float | None = None
    best_idx: int | None = None
    for i in tree.query(point_rd.buffer(OSM_MATCH_RADIUS_M)):
        d = geoms[i].distance(point_rd)
        if d <= OSM_MATCH_RADIUS_M and (best_dist is None or d < best_dist):
            best_dist, best_idx = d, i
    if best_idx is None:
        return point_rd, None
    line = geoms[best_idx]
    snapped = line.interpolate(line.project(point_rd))
    return snapped, ids[best_idx]


def _to_wgs84(point_rd: Point) -> Point:
    return transform(_RD_TO_WGS84.transform, point_rd)


def rebuild_hectometer_points(session) -> int:
    """Recompute every-100m hectometer markers between measurement-site anchors.

    Anchors come from multiple independent NDW providers (RWS08 fixed
    hectometer-post sensors, RWS01 MONIBAS loop detectors, provincial PZH01/
    GEO* systems), each with its own km + geometry decoded independently by
    parsers/datex_v2.py. Spot-checked A9 km 59.5-61.5: mixing providers in one
    sorted-by-km sequence produces geographic inversions (an RWS08 anchor at
    km 60.5 sits *south* of an RWS01 anchor at km 60.4, on the same
    carriageway) — the two providers' own km/position pairs simply don't
    agree at 100m precision. Chording between mismatched neighbours then
    zigzags backwards instead of following the road.

    RWS08 IDs encode the site's own name as `RWS08_<road>_HR{L,R}_<hm>`
    (parsers/datex_v2.py's RWS08 branch) — evenly spaced every 500m on every
    major A-road (11,011 sites nationwide, e.g. 233 on the A9 alone) and,
    unlike the other providers, purpose-built as a hectometer reference
    rather than placed for traffic-monitoring convenience. Restricting a
    (road, carriageway) group to RWS08-only anchors when at least 2 exist
    removes the cross-provider disagreement entirely for the A-road network.
    RWS08 coverage on N-roads is sparse (48 nationwide), so groups without at
    least 2 RWS08 anchors keep the full mixed-provider set — worse precision,
    but still far better than no coverage at all.
    """
    anchor_rows = session.execute(
        select(
            MeasurementSite.id,
            MeasurementSite.road,
            MeasurementSite.carriageway,
            MeasurementSite.km,
            func.ST_AsText(MeasurementSite.geom).label("geom_wkt"),
        ).where(
            MeasurementSite.road.isnot(None),
            MeasurementSite.carriageway.in_(("R", "L")),
            MeasurementSite.km.isnot(None),
            MeasurementSite.geom.isnot(None),
        )
    ).all()

    groups: dict[tuple[str, str], list[tuple[float, Point, bool]]] = {}
    for site_id, road, carriageway, km, geom_wkt in anchor_rows:
        try:
            point_rd = transform(_WGS84_TO_RD.transform, shapely_wkt.loads(geom_wkt))
        except (TypeError, ValueError):
            continue
        is_rws08 = site_id.startswith("RWS08_")
        groups.setdefault((road, carriageway), []).append((float(km), point_rd, is_rws08))

    if not groups:
        return 0

    for key, anchors in groups.items():
        rws08_only = [a for a in anchors if a[2]]
        if len(rws08_only) >= 2:
            groups[key] = rws08_only

    osm_trees = _load_osm_ref_trees(session)
    rows_by_id: dict[str, dict] = {}

    for (road, carriageway_code), anchors in groups.items():
        label = _CARRIAGEWAY_LABEL[carriageway_code]
        anchors.sort(key=lambda a: a[0])

        for (km_a, point_a, _), (km_b, point_b, _) in zip(anchors, anchors[1:]):
            span_m = point_a.distance(point_b)
            if span_m < 1.0:
                continue
            n_steps = max(1, round(span_m / STEP_M))

            for step in range(n_steps + 1):
                frac = step / n_steps
                km = round(km_a + (km_b - km_a) * frac, 1)
                base_point = Point(
                    point_a.x + (point_b.x - point_a.x) * frac,
                    point_a.y + (point_b.y - point_a.y) * frac,
                )
                snapped, matched_osm_id = _snap_to_osm(osm_trees, road, label, base_point)

                point_id = f"{road}:{label}:{km:.1f}"
                rows_by_id[point_id] = {
                    "id": point_id,
                    "road": road,
                    "carriageway": label,
                    "km": km,
                    "matched_osm_id": matched_osm_id,
                    "geom": wkt_geom(_to_wgs84(snapped).wkt),
                }

    all_rows = list(rows_by_id.values())
    total = 0
    for start in range(0, len(all_rows), BATCH_SIZE):
        total += bulk_upsert(session, HectometerPoint, all_rows[start:start + BATCH_SIZE], ["id"])
        session.flush()

    logger.info("hectometer: rebuilt %d points", total)
    return total
