"""Build road-following geometry for travel-time segments from the VILD TMC chain.

A travel-time site references two TMC location codes (primary→secondary). The
VILD TMC table chains consecutive codes via pos_off/neg_off and links each code
to its road line (lin_ref → vild_line.id). Walking the chain between the two
codes and clipping the road line(s) at the endpoints yields the actual road
polyline. Falls back silently (leaves the straight chord) when unresolvable.
"""

from __future__ import annotations

import logging

from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, substring
from sqlalchemy import func, select, update

from ndwinfo.ingest.base import wkt_geom
from ndwinfo.models import MeasurementSite, VildLine, VildPoint, VildTmc

logger = logging.getLogger(__name__)

MAX_HOPS = 400  # safety cap on chain length between two codes


def _walk(start: int, goal: int, chain: dict, attr: str) -> list[int] | None:
    """Follow attr (pos_off/neg_off) from start until goal; None if not reached."""
    cur = start
    seq = [start]
    seen = {start}
    for _ in range(MAX_HOPS):
        node = chain.get(cur)
        if node is None:
            return None
        nxt = node[attr]
        if not nxt:
            return None
        seq.append(nxt)
        if nxt == goal:
            return seq
        if nxt in seen:
            return None
        seen.add(nxt)
        cur = nxt
    return None


def _clip(line: LineString, pa, pb) -> list[tuple]:
    """Coords of line between projections of pa and pb, oriented pa→pb."""
    da = line.project(pa)
    db = line.project(pb)
    lo, hi = (da, db) if da <= db else (db, da)
    coords = list(substring(line, lo, hi).coords)
    if da > db:
        coords.reverse()
    return coords


def _load_lines(session) -> dict[int, LineString]:
    lines: dict[int, LineString] = {}
    for lid, wktxt in session.execute(
        select(VildLine.id, func.ST_AsText(VildLine.geom))
    ).all():
        if not wktxt:
            continue
        try:
            g = shapely_wkt.loads(wktxt)
        except Exception:
            continue
        if isinstance(g, MultiLineString):
            g = linemerge(g)
            if isinstance(g, MultiLineString):  # still disjoint → take longest part
                g = max(g.geoms, key=lambda p: p.length)
        if isinstance(g, LineString) and not g.is_empty:
            try:
                lines[int(lid)] = g
            except (TypeError, ValueError):
                pass
    return lines


def _load_points(session) -> dict[int, object]:
    points: dict[int, object] = {}
    for pid, wktxt in session.execute(
        select(VildPoint.id, func.ST_AsText(VildPoint.geom))
    ).all():
        if not wktxt:
            continue
        try:
            points[int(pid)] = shapely_wkt.loads(wktxt)
        except Exception:
            pass
    return points


def rebuild_traveltime_geometry(session) -> int:
    """Recompute measurement_site.line_geom for travel-time sites. Returns count."""
    chain = {
        row.loc_nr: {"pos_off": row.pos_off, "neg_off": row.neg_off, "lin_ref": row.lin_ref}
        for row in session.execute(
            select(VildTmc.loc_nr, VildTmc.pos_off, VildTmc.neg_off, VildTmc.lin_ref)
        ).all()
    }
    if not chain:
        return 0  # VILD TMC table not loaded yet → keep straight chords

    lines = _load_lines(session)
    points = _load_points(session)

    sites = session.execute(
        select(MeasurementSite.id, MeasurementSite.tmc_primary, MeasurementSite.tmc_secondary)
        .where(
            MeasurementSite.tmc_primary.isnot(None),
            MeasurementSite.tmc_secondary.isnot(None),
        )
    ).all()

    updated = 0
    for site_id, prim, sec in sites:
        if prim == sec:
            continue
        seq = _walk(prim, sec, chain, "pos_off") or _walk(prim, sec, chain, "neg_off")
        if not seq or len(seq) < 2:
            continue

        coords: list[tuple] = []
        for a, b in zip(seq, seq[1:]):
            na, nb = chain.get(a), chain.get(b)
            pa, pb = points.get(a), points.get(b)
            la = na["lin_ref"] if na else None
            lb = nb["lin_ref"] if nb else None
            piece: list[tuple] | None = None
            if la is not None and la == lb and la in lines and pa is not None and pb is not None:
                try:
                    piece = _clip(lines[la], pa, pb)
                except Exception:
                    piece = None
            if piece is None:  # cross-line / missing geometry → straight connect
                piece = []
                if pa is not None:
                    piece.append((pa.x, pa.y))
                if pb is not None:
                    piece.append((pb.x, pb.y))
            for c in piece:
                if not coords or coords[-1] != c:
                    coords.append(c)

        if len(coords) < 2:
            continue
        wkt_line = "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in coords) + ")"
        session.execute(
            update(MeasurementSite)
            .where(MeasurementSite.id == site_id)
            .values(line_geom=wkt_geom(wkt_line))
        )
        updated += 1

    session.flush()
    logger.info("traveltime geometry: rebuilt %d road-following segments", updated)
    return updated
