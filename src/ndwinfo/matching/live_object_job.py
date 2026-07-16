"""Persist fail-closed MSI/DRIP bindings to the active directed OSM graph."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import groupby
from typing import Iterable

from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape
from sqlalchemy import cast, delete, func, literal, select, true
from sqlalchemy.orm import Session

from ndwinfo.config import settings
from ndwinfo.matching.live_objects import (
    ALGORITHM_VERSION,
    PERSISTED_SOURCE_TYPES,
    LiveObjectTraits,
    LiveRoadCandidate,
    decide_live_object_binding,
)
from ndwinfo.matching.source_binding import local_line_bearing
from ndwinfo.models import (
    Drip,
    MsiSign,
    MsiState,
    OsmImportRun,
    OsmRoadSegment,
    SourceLocationBinding,
)


def rebuild_live_object_bindings(
    session: Session,
    graph_import_id: int | None = None,
    *,
    kinds: Iterable[str] = ("msi", "drip"),
) -> dict[str, object]:
    """Rebuild bounded spatial bindings for the requested live object kinds."""
    graph = _active_graph(session, graph_import_id)
    requested = tuple(dict.fromkeys(kinds))
    if any(kind not in PERSISTED_SOURCE_TYPES for kind in requested):
        raise ValueError("Live object kinds must be msi and/or drip")
    extent = session.execute(
        select(
            func.ST_XMin(func.ST_Extent(OsmRoadSegment.geom)),
            func.ST_YMin(func.ST_Extent(OsmRoadSegment.geom)),
            func.ST_XMax(func.ST_Extent(OsmRoadSegment.geom)),
            func.ST_YMax(func.ST_Extent(OsmRoadSegment.geom)),
        ).where(OsmRoadSegment.import_run_id == graph.id)
    ).one()
    if any(value is None for value in extent):
        raise RuntimeError(f"OSM graph import {graph.id} contains no road segments")
    margin = settings.source_binding_max_distance_m / 50_000.0
    envelope = func.ST_MakeEnvelope(
        float(extent[0]) - margin,
        float(extent[1]) - margin,
        float(extent[2]) + margin,
        float(extent[3]) + margin,
        4326,
    )
    evaluated_at = datetime.now(timezone.utc)
    result: dict[str, object] = {
        "graph_version": graph.graph_version,
        "algorithm_version": ALGORITHM_VERSION,
    }
    for kind in requested:
        persisted_type = PERSISTED_SOURCE_TYPES[kind]
        session.execute(
            delete(SourceLocationBinding).where(
                SourceLocationBinding.source_type == persisted_type,
                SourceLocationBinding.graph_version == graph.graph_version,
                SourceLocationBinding.algorithm_version == ALGORITHM_VERSION,
            )
        )
        counts = {"accepted": 0, "ambiguous": 0, "rejected": 0}
        stale_after_s = (
            settings.road_matrix_stale_after_s
            if kind == "msi"
            else settings.road_drip_stale_after_s
        )
        for source, candidates in _candidate_groups(
            session, graph.id, kind, envelope
        ):
            decision = decide_live_object_binding(
                source,
                candidates,
                # NDW numbers MSI lanes from the median/left edge, with lane 1
                # leftmost. OSM's directional lane arrays are left-to-right.
                # DRIP remains carriageway scoped and ignores lane entirely.
                lane_order_verified=kind == "msi",
                reference_time=evaluated_at,
                stale_after_s=stale_after_s,
            )
            session.add(
                SourceLocationBinding(
                    source_type=persisted_type,
                    source_id=decision.source_id,
                    internal_segment_id=decision.internal_segment_id,
                    status=decision.status,
                    distance_m=decision.distance_m,
                    heading_delta_deg=decision.heading_delta_deg,
                    score=decision.score,
                    margin=decision.margin,
                    confidence=decision.confidence,
                    graph_version=graph.graph_version,
                    algorithm_version=ALGORITHM_VERSION,
                    evaluated_at=evaluated_at,
                )
            )
            counts[decision.status] += 1
        result[kind] = counts
    session.flush()
    return result


def _candidate_groups(session, graph_id: int, kind: str, envelope):
    source = _source_query(kind, envelope).subquery("live_source")
    source_geography = cast(source.c.geom, Geography)
    distance = func.ST_Distance(
        cast(OsmRoadSegment.geom, Geography), source_geography
    ).label("distance_m")
    candidate = (
        select(
            OsmRoadSegment.internal_segment_id.label("segment_id"),
            OsmRoadSegment.road_number.label("segment_road"),
            OsmRoadSegment.carriageway_ref.label("segment_carriageway"),
            OsmRoadSegment.lanes.label("segment_lanes"),
            OsmRoadSegment.highway.label("segment_highway"),
            distance,
            OsmRoadSegment.geom.label("segment_geom"),
        )
        .where(
            OsmRoadSegment.import_run_id == graph_id,
            OsmRoadSegment.geom.op("&&")(
                func.ST_Expand(
                    source.c.geom,
                    settings.source_binding_max_distance_m / 50_000.0,
                )
            ),
            func.ST_DWithin(
                cast(OsmRoadSegment.geom, Geography),
                source_geography,
                settings.source_binding_max_distance_m,
            ),
        )
        .order_by(distance)
        .limit(settings.source_binding_max_candidates)
        .lateral("candidate")
    )
    query = (
        select(*source.c, *candidate.c)
        .select_from(source)
        .outerjoin(candidate, true())
        .order_by(source.c.source_id, candidate.c.distance_m)
        .execution_options(stream_results=True, yield_per=1000)
    )
    rows = session.execute(query)
    for _, group in groupby(rows, key=lambda row: row.source_id):
        grouped = list(group)
        first = grouped[0]
        point = to_shape(first.geom)
        candidates = [
            LiveRoadCandidate(
                internal_segment_id=row.segment_id,
                road_number=row.segment_road,
                carriageway_ref=row.segment_carriageway,
                lanes=row.segment_lanes,
                distance_m=float(row.distance_m),
                bearing=local_line_bearing(to_shape(row.segment_geom), point),
                highway=row.segment_highway,
            )
            for row in grouped
            if row.segment_id is not None
        ]
        yield (
            LiveObjectTraits(
                source_type=kind,
                source_id=str(first.source_id),
                road=first.road,
                carriageway=first.carriageway,
                lane=first.lane,
                bearing=float(first.bearing) if first.bearing is not None else None,
                # Ingest presence proves the latest snapshot carried this
                # object. Source timestamps remain exposed separately by API.
                observed_at=first.ingested_at,
                ingested_at=first.ingested_at,
                provenance={"feed": "matrix_signs" if kind == "msi" else "drips"},
            ),
            candidates,
        )


def _source_query(kind: str, envelope):
    if kind == "msi":
        return (
            select(
                MsiSign.uuid.label("source_id"),
                MsiSign.road.label("road"),
                MsiSign.carriageway.label("carriageway"),
                MsiSign.lane.label("lane"),
                MsiSign.bearing.label("bearing"),
                func.coalesce(MsiState.ingested_at, MsiSign.ingested_at).label(
                    "ingested_at"
                ),
                MsiSign.geom.label("geom"),
            )
            .outerjoin(MsiState, MsiState.uuid == MsiSign.uuid)
            .where(
                MsiSign.geom.isnot(None),
                MsiSign.geom.op("&&")(envelope),
            )
        )
    return select(
        func.concat(Drip.controller_id, ":", Drip.vms_index).label("source_id"),
        literal(None).label("road"),
        Drip.carriageway.label("carriageway"),
        literal(None).label("lane"),
        Drip.bearing.label("bearing"),
        Drip.ingested_at.label("ingested_at"),
        Drip.geom.label("geom"),
    ).where(Drip.geom.isnot(None), Drip.geom.op("&&")(envelope))


def _active_graph(session: Session, graph_import_id: int | None) -> OsmImportRun:
    query = select(OsmImportRun)
    if graph_import_id is None:
        query = query.where(
            OsmImportRun.is_active.is_(True), OsmImportRun.status == "active"
        )
    else:
        query = query.where(OsmImportRun.id == graph_import_id)
    graph = session.scalar(query.limit(1))
    if graph is None or graph.status != "active" or not graph.is_active:
        raise RuntimeError("No requested active OSM graph import is available")
    return graph
