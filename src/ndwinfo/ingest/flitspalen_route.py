"""Snap trajectcontrole (SC/SCE) camera pairs onto the matching osm_road way.

A trajectcontrole section is enforced by an entry camera (`camera_type` "SC")
and an exit camera ("SCE") a street-length apart. flitspalen.nl's own SCE ids
encode the SC id they pair with: sce_id = 1_000_000_000 + sc_id*1000 + N,
where N (the last 1-3 digits) is some per-gantry/lane variant index — NOT
always 1, e.g. SCE 1003887005 pairs with SC 3887 (N=5). Floor-dividing
(sce_id - 1_000_000_000) by 1000 and discarding the remainder matched 69/69
live SCE cameras to a real SC id — confirmed against the full NL dataset. The
source documents no such relationship, so a derived id with no live match
just yields no route for that camera.

A motorway ref like "A4" is rarely one continuous osm_road way between two
cameras — interchanges split it into dozens of short segments plus link
ramps, none of which are guaranteed to be collinear/mergeable as a flat
shapely linemerge. So the two camera points are located on their own nearest
way, then routed to each other over a graph of every candidate way's
endpoints (shared OSM nodes = shared coordinates after projection), via
Dijkstra — this is what actually "reconnects the segments", not an OSM
route-relation lookup: relation membership wouldn't add geometry that isn't
already ingested, it would just describe an order shapely's own topology
already gives us once distinct ways share exact endpoint coordinates.

Run once per flitspalen ingest (weekly cadence) rather than per API request:
the two camera points rarely move, and the OSM way lookup + graph build is
too heavy to redo on every map viewport fetch.
"""

from __future__ import annotations

import heapq
import logging

from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, Point
from shapely.ops import substring, transform
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ndwinfo.models import FlitspalenCameraRoute, OsmRoad

logger = logging.getLogger(__name__)

_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)

# A candidate osm_road way is only accepted if both camera points fall within
# this distance (metres) of it — rejects same-ref segments of the same road
# elsewhere in the country while tolerating GPS/digitisation slop.
MAX_MATCH_DIST_M = 60.0
# Degenerate pairs (cameras essentially co-located) aren't worth a route.
MIN_SEGMENT_LEN_M = 20.0
# Coarse pre-filter box around the two camera points, in degrees (~1.5km) —
# generous since it only bounds the osm_road query, not the match itself.
BBOX_PAD_DEG = 0.015
# Sanity cap on the routed path: a shortest path that loops through a messy
# interchange far more than the straight-line gap warrants is more likely a
# graph artefact (e.g. bridging onto the wrong carriageway) than the real
# section, so it's rejected like a non-match rather than stored.
MAX_ROUTE_DETOUR_RATIO = 4.0
MAX_ROUTE_LEN_M = 8000.0
# Endpoint coordinates are compared at this precision (metres) to recognise
# two ways sharing the same OSM node — RD coordinates from the same
# lon/lat via the same transform match exactly, this only guards float noise.
NODE_PRECISION = 3

SCE_ID_OFFSET = 1_000_000_000
SCE_ID_STEP = 1000


def paired_sc_id(sce_id: int) -> int | None:
    rem = sce_id - SCE_ID_OFFSET
    return rem // SCE_ID_STEP if rem >= 0 else None


def _to_rd(point_wgs84: Point) -> Point:
    return transform(_WGS84_TO_RD.transform, point_wgs84)


def _to_wgs84(line_rd: LineString) -> LineString:
    return transform(_RD_TO_WGS84.transform, line_rd)


def _candidate_ways(session: Session, street: str | None, sc: Point, sce: Point) -> list[LineString]:
    min_lon = min(sc.x, sce.x) - BBOX_PAD_DEG
    max_lon = max(sc.x, sce.x) + BBOX_PAD_DEG
    min_lat = min(sc.y, sce.y) - BBOX_PAD_DEG
    max_lat = max(sc.y, sce.y) + BBOX_PAD_DEG
    bbox = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)

    base = select(func.ST_AsText(OsmRoad.geom)).where(func.ST_Intersects(OsmRoad.geom, bbox))
    rows = []
    if street:
        rows = session.execute(base.where(func.lower(OsmRoad.ref) == street.lower())).scalars().all()
        if not rows:
            # Local streets (e.g. "Vogelweg") carry their name, not a route ref.
            rows = session.execute(base.where(func.lower(OsmRoad.name) == street.lower())).scalars().all()
    else:
        rows = session.execute(base).scalars().all()

    # Reprojected to RD immediately — everything downstream compares these
    # against RD camera points, and a degrees-vs-metres mismatch here silently
    # produces garbage (six-figure) "distances" instead of an error.
    return [transform(_WGS84_TO_RD.transform, shapely_wkt.loads(wkt_text)) for wkt_text in rows if wkt_text]


def _node_key(coord: tuple[float, float]) -> tuple[float, float]:
    return (round(coord[0], NODE_PRECISION), round(coord[1], NODE_PRECISION))


def _build_graph(ways: list[LineString]) -> dict:
    """Undirected graph: node -> [(neighbour_node, weight, line_from_node_to_neighbour)].

    A way's own two endpoints ARE the nodes — no relation data, no assumed
    order; two ways are "connected" purely because they share an exact OSM
    node coordinate, which is how OSM already represents a road split across
    many ways.
    """
    graph: dict[tuple, list] = {}
    for way in ways:
        coords = list(way.coords)
        if len(coords) < 2:
            continue
        a, b = _node_key(coords[0]), _node_key(coords[-1])
        if a == b:
            continue  # closed loop (roundabout) — no useful through-edge
        graph.setdefault(a, []).append((b, way.length, LineString(coords)))
        graph.setdefault(b, []).append((a, way.length, LineString(coords[::-1])))
    return graph


def _nearest_way(ways: list[LineString], point: Point) -> tuple[float, LineString] | None:
    best: tuple[float, LineString] | None = None
    for way in ways:
        d = way.distance(point)
        if best is None or d < best[0]:
            best = (d, way)
    return best


def _insert_point_node(graph: dict, way: LineString, point: Point, node_label: str) -> tuple:
    """Split `way` at the point's projection, splicing a virtual node into `graph`.

    Returns the node to route from/to: the virtual node, or an existing
    endpoint directly if the point already sits within MIN_SEGMENT_LEN_M/4 of
    one (avoids a near-zero-length stub edge).
    """
    coords = list(way.coords)
    a, b = _node_key(coords[0]), _node_key(coords[-1])
    pos = way.project(point)
    snap = MIN_SEGMENT_LEN_M / 4
    if pos <= snap:
        return a
    if way.length - pos <= snap:
        return b

    before = substring(way, 0, pos)
    after = substring(way, pos, way.length)
    graph.setdefault(node_label, [])
    graph[node_label].append((a, before.length, LineString(list(before.coords)[::-1])))
    graph[node_label].append((b, after.length, after))
    graph.setdefault(a, []).append((node_label, before.length, before))
    graph.setdefault(b, []).append((node_label, after.length, LineString(list(after.coords)[::-1])))
    return node_label


def _shortest_path(graph: dict, start: tuple, goal: tuple) -> tuple[float, list[LineString]] | None:
    dist = {start: 0.0}
    prev: dict = {}
    pq = [(0.0, start)]
    visited = set()
    while pq:
        d, node = heapq.heappop(pq)
        if node in visited:
            continue
        visited.add(node)
        if node == goal:
            path_lines = []
            cur = node
            while cur in prev:
                p, line = prev[cur]
                path_lines.append(line)
                cur = p
            path_lines.reverse()
            return d, path_lines
        for neighbour, weight, line in graph.get(node, []):
            nd = d + weight
            if nd < dist.get(neighbour, float("inf")):
                dist[neighbour] = nd
                prev[neighbour] = (node, line)
                heapq.heappush(pq, (nd, neighbour))
    return None


def _best_route(ways: list[LineString], sc_rd: Point, sce_rd: Point) -> LineString | None:
    if not ways:
        return None
    sc_anchor = _nearest_way(ways, sc_rd)
    sce_anchor = _nearest_way(ways, sce_rd)
    if sc_anchor is None or sce_anchor is None:
        return None
    d_sc, way_sc = sc_anchor
    d_sce, way_sce = sce_anchor
    if d_sc > MAX_MATCH_DIST_M or d_sce > MAX_MATCH_DIST_M:
        return None

    graph = _build_graph(ways)
    start = _insert_point_node(graph, way_sc, sc_rd, "__sc__")
    goal = _insert_point_node(graph, way_sce, sce_rd, "__sce__")
    if start == goal:
        return None

    result = _shortest_path(graph, start, goal)
    if result is None:
        return None
    total, path_lines = result
    if not path_lines or total < MIN_SEGMENT_LEN_M:
        return None

    straight = sc_rd.distance(sce_rd)
    if total > MAX_ROUTE_LEN_M or (straight > 0 and total > straight * MAX_ROUTE_DETOUR_RATIO):
        logger.debug(
            "flitspalen route: rejecting implausible detour (%.0fm vs %.0fm straight-line)",
            total, straight,
        )
        return None

    coords: list[tuple[float, float]] = []
    for line in path_lines:
        pts = list(line.coords)
        if coords and coords[-1] == pts[0]:
            pts = pts[1:]
        coords.extend(pts)
    return LineString(coords) if len(coords) >= 2 else None


def build_pair_routes(session: Session, cameras: list[dict]) -> list[dict]:
    """cameras: parsed flitspalen rows (id, camera_type, street, geom WKT).

    Returns upsert-ready dict rows for FlitspalenCameraRoute — one per SC/SCE
    pair that resolved to a real osm_road match. Pairs with no nearby matching
    way (or a still-unmatched camera_type like "SCM") are silently skipped,
    not filled in with a straight line — a missing route is more honest than
    one that looks road-following but isn't.
    """
    by_id = {c["id"]: c for c in cameras if c.get("camera_type") and c.get("geom")}
    sc_by_id = {c["id"]: c for c in by_id.values() if c["camera_type"] == "SC"}

    routes: list[dict] = []
    for sce in by_id.values():
        if sce["camera_type"] != "SCE":
            continue
        sc = sc_by_id.get(paired_sc_id(sce["id"]))
        if sc is None:
            continue

        sc_point = shapely_wkt.loads(sc["geom"])
        sce_point = shapely_wkt.loads(sce["geom"])
        sc_rd, sce_rd = _to_rd(sc_point), _to_rd(sce_point)

        ways = _candidate_ways(session, sc.get("street"), sc_point, sce_point)
        route_rd = _best_route(ways, sc_rd, sce_rd)
        if route_rd is None:
            logger.debug(
                "flitspalen route: no osm_road match for SC %s <-> SCE %s (street=%r)",
                sc["id"], sce["id"], sc.get("street"),
            )
            continue

        routes.append({
            "sc_id": sc["id"],
            "sce_id": sce["id"],
            "street": sc.get("street"),
            "geom": _to_wgs84(route_rd).wkt,
        })
    return routes
