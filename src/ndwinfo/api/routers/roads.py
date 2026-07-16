"""Production OSM road graph and lightweight driving-corridor API.

Unlike the POC endpoint, these requests only query the locally imported,
active PostGIS graph. Source-to-road matching has already been persisted; a
request never invokes Overpass or scans all source locations.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from geoalchemy2 import Geography, Geometry
from sqlalchemy import and_, cast, func, literal_column, select
from sqlalchemy.orm import aliased

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.config import settings
from ndwinfo.matching.live_objects import (
    ALGORITHM_VERSION as LIVE_OBJECT_ALGORITHM_VERSION,
)
from ndwinfo.matching.live_objects import PERSISTED_SOURCE_TYPES
from ndwinfo.matching.source_binding import ALGORITHM_VERSION, SOURCE_TYPE
from ndwinfo.models import (
    Drip,
    MeasurementCharacteristic,
    MeasurementSite,
    MsiSign,
    MsiState,
    OsmImportRun,
    OsmRoadSegment,
    SourceLocationBinding,
    TrafficMeasurement,
)
from ndwinfo.osm.graph_query import (
    SegmentNotFoundError,
    SqlGraphSegmentProvider,
    find_relevant_path,
)
from ndwinfo.osm.lane_state import build_lane_speed_states
from ndwinfo.osm.lanes import build_lane_schema
from ndwinfo.osm.speed_model import (
    SpeedObservation,
    SpeedSegment,
    assign_speed_states,
)

router = APIRouter(prefix="/roads", tags=["OSM road graph"])

_SEGMENT_COLUMNS = (
    OsmRoadSegment.internal_segment_id,
    OsmRoadSegment.graph_version,
    OsmRoadSegment.osm_way_id,
    OsmRoadSegment.from_node_id,
    OsmRoadSegment.to_node_id,
    OsmRoadSegment.travel_direction,
    OsmRoadSegment.highway,
    OsmRoadSegment.road_number,
    OsmRoadSegment.name,
    OsmRoadSegment.oneway,
    OsmRoadSegment.junction,
    OsmRoadSegment.carriageway_ref,
    OsmRoadSegment.lanes,
    OsmRoadSegment.lane_schema,
    OsmRoadSegment.maxspeed,
    OsmRoadSegment.access,
    OsmRoadSegment.bridge,
    OsmRoadSegment.tunnel,
    OsmRoadSegment.layer,
    OsmRoadSegment.length_m,
    OsmRoadSegment.tags,
)


@router.get("")
def get_roads(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[
        int, Query(ge=1, le=settings.road_api_max_features)
    ] = settings.road_api_max_features,
) -> Response:
    """Return active directed segments intersecting a bounded viewport."""
    graph = _active_graph(db)
    envelope = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows, truncated = _query_segments(
        db,
        graph,
        func.ST_Intersects(OsmRoadSegment.geom, envelope),
        limit,
    )
    return _road_response(db, graph, rows, truncated, "bbox")


@router.get("/corridor")
def get_road_corridor(
    db: DbDep,
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
    heading: Annotated[float, Query(ge=0, le=360)],
    accuracy_m: Annotated[float, Query(ge=0, le=1000)] = 20.0,
    radius_m: Annotated[float | None, Query(gt=0)] = None,
    lookahead_m: Annotated[float, Query(gt=0)] = 2500.0,
    limit: Annotated[
        int, Query(ge=1, le=settings.road_api_max_features)
    ] = settings.road_api_max_features,
) -> Response:
    """Return only indexed road candidates near a projected path ahead.

    The response intentionally contains all directed candidates in the narrow
    corridor. Vehicle-history/topology ranking remains a stateful client task.
    """
    radius = radius_m if radius_m is not None else max(25.0, accuracy_m * 2.0)
    if radius > settings.road_corridor_max_radius_m:
        raise HTTPException(
            400,
            f"radius_m exceeds {settings.road_corridor_max_radius_m:g} m",
        )
    if lookahead_m > settings.road_corridor_max_lookahead_m:
        raise HTTPException(
            400,
            f"lookahead_m exceeds {settings.road_corridor_max_lookahead_m:g} m",
        )

    graph = _active_graph(db)
    origin = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
    projected = cast(
        func.ST_Project(
            cast(origin, Geography(srid=4326)), lookahead_m, func.radians(heading)
        ),
        Geometry("POINT", srid=4326),
    )
    path = func.ST_MakeLine(origin, projected)
    spatial_filter = func.ST_DWithin(
        # This deliberately compiles to the exact expression stored in
        # ``ix_osm_road_segment_geog``.  SQLAlchemy's regular typed CAST is
        # geographically equivalent, but PostgreSQL does not match it to the
        # expression index and otherwise scans the full road graph per fix.
        literal_column("osm_road_segment.geom::geography"),
        cast(path, Geography(srid=4326)),
        radius,
    )
    rows, truncated = _query_segments(db, graph, spatial_filter, limit)
    return _road_response(
        db,
        graph,
        rows,
        truncated,
        "corridor",
        {
            "origin": [lon, lat],
            "heading": heading % 360,
            "accuracy_m": accuracy_m,
            "radius_m": radius,
            "lookahead_m": lookahead_m,
        },
    )


@router.get("/path")
def get_connected_road_path(
    db: DbDep,
    segment_id: Annotated[str, Query(min_length=1, max_length=64)],
    ahead_m: Annotated[
        float, Query(ge=0, le=settings.road_topology_max_ahead_m)
    ] = min(2500.0, settings.road_topology_max_ahead_m),
    behind_m: Annotated[
        float, Query(ge=0, le=settings.road_topology_max_behind_m)
    ] = min(250.0, settings.road_topology_max_behind_m),
    max_edges: Annotated[
        int, Query(ge=1, le=settings.road_topology_max_edges)
    ] = settings.road_topology_max_edges,
) -> Response:
    """Return only directed, topologically connected segments around a match.

    ``common_ahead`` in response metadata is safe while route choice remains
    unknown.  Branch-specific traffic must be withheld while
    ``branch_confidence`` is zero.
    """
    graph = _active_graph(db)
    provider = SqlGraphSegmentProvider(
        db,
        import_run_id=graph.id,
        graph_version=graph.graph_version,
    )
    try:
        path = find_relevant_path(
            provider,
            segment_id,
            ahead_m=ahead_m,
            behind_m=behind_m,
            max_edges=max_edges,
            branch_limit=settings.road_topology_max_branches,
        )
    except SegmentNotFoundError as exc:
        raise HTTPException(
            404,
            "Matched segment is not part of the active OSM graph",
        ) from exc

    segment_ids = path.all_segment_ids
    rows, rows_truncated = _query_segments(
        db,
        graph,
        OsmRoadSegment.internal_segment_id.in_(segment_ids),
        len(segment_ids),
    )
    topology = path.as_dict()
    topology["truncated"] = topology["truncated"] or rows_truncated
    return _road_response(
        db,
        graph,
        rows,
        topology["truncated"],
        "connected_path",
        {
            **topology,
            "ahead_m": ahead_m,
            "behind_m": behind_m,
            "max_edges": max_edges,
            "branch_limit": settings.road_topology_max_branches,
            "db_lookup_count": provider.lookup_count,
            "candidate_rows_loaded": provider.candidate_rows_loaded,
        },
    )


@router.get("/diagnostics/bindings")
def get_binding_diagnostics(
    b: BBoxDep,
    db: DbDep,
    status: Annotated[str | None, Query(pattern="^(accepted|ambiguous|rejected)$")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 250,
) -> Response:
    """Inspect persisted decisions; this endpoint is never a driving layer."""
    graph = _active_graph(db)
    envelope = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    query = (
        select(
            SourceLocationBinding.source_id,
            SourceLocationBinding.internal_segment_id,
            SourceLocationBinding.status,
            SourceLocationBinding.distance_m,
            SourceLocationBinding.heading_delta_deg,
            SourceLocationBinding.score,
            SourceLocationBinding.margin,
            SourceLocationBinding.confidence,
            SourceLocationBinding.graph_version,
            SourceLocationBinding.algorithm_version,
            SourceLocationBinding.evaluated_at,
            func.ST_AsGeoJSON(MeasurementSite.geom, 7).label("geom_json"),
        )
        .join(
            MeasurementSite,
            and_(
                SourceLocationBinding.source_type == SOURCE_TYPE,
                SourceLocationBinding.source_id == MeasurementSite.id,
            ),
        )
        .where(
            SourceLocationBinding.graph_version == graph.graph_version,
            func.ST_Intersects(MeasurementSite.geom, envelope),
        )
        .limit(limit)
    )
    if status is not None:
        query = query.where(SourceLocationBinding.status == status)
    rows = db.execute(query).all()
    features = [
        {
            "type": "Feature",
            "geometry": json.loads(row.geom_json) if row.geom_json else None,
            "properties": {
                "source_type": SOURCE_TYPE,
                "source_id": row.source_id,
                "internal_segment_id": row.internal_segment_id,
                "status": row.status,
                "distance_m": _float(row.distance_m),
                "heading_delta_deg": _float(row.heading_delta_deg),
                "score": _float(row.score),
                "margin": _float(row.margin),
                "confidence": _float(row.confidence),
                "graph_version": row.graph_version,
                "algorithm_version": row.algorithm_version,
                "evaluated_at": _iso(row.evaluated_at),
            },
        }
        for row in rows
    ]
    return _geo_response(features, {"diagnostics": True, "graph_version": graph.graph_version})


def _active_graph(db) -> OsmImportRun:
    graph = db.scalar(select(OsmImportRun).where(OsmImportRun.is_active.is_(True)).limit(1))
    if graph is None:
        raise HTTPException(503, "No active local OSM graph is available")
    return graph


def _query_segments(db, graph: OsmImportRun, spatial_filter, limit: int):
    rows = db.execute(
        select(
            *_SEGMENT_COLUMNS,
            func.ST_AsGeoJSON(OsmRoadSegment.geom, 7).label("geom_json"),
        )
        .where(OsmRoadSegment.import_run_id == graph.id, spatial_filter)
        .order_by(OsmRoadSegment.internal_segment_id)
        .limit(limit + 1)
    ).all()
    return rows[:limit], len(rows) > limit


def _road_response(
    db,
    graph: OsmImportRun,
    rows,
    truncated: bool,
    query_type: str,
    query_metadata: dict[str, Any] | None = None,
) -> Response:
    segment_ids = [row.internal_segment_id for row in rows]
    lane_schemas = {row.internal_segment_id: _lane_schema(row) for row in rows}
    lane_counts = {
        segment_id: schema.get("lane_count") if schema else None
        for segment_id, schema in lane_schemas.items()
    }
    direct_speeds = load_direct_speed_states(
        db,
        graph.graph_version,
        segment_ids,
        lane_counts=lane_counts,
        lane_order_verified=True,
    )
    speeds = assign_corridor_speed_states(db, graph, rows, direct_speeds)
    live_facts = load_live_segment_facts(db, graph.graph_version, segment_ids)
    features = []
    for row in rows:
        speed = speeds.get(row.internal_segment_id, unknown_speed_state())
        segment_state = {
            "version": 1,
            "speed": _canonical_speed_fact(speed),
            "matrix": live_facts[row.internal_segment_id]["matrix"],
            "drips": live_facts[row.internal_segment_id]["drips"],
        }
        features.append(
            {
                "type": "Feature",
                "id": row.internal_segment_id,
                "geometry": json.loads(row.geom_json),
                "properties": {
                    "internal_segment_id": row.internal_segment_id,
                    "graph_version": row.graph_version,
                    "osm_way_id": row.osm_way_id,
                    "from_node_id": row.from_node_id,
                    "to_node_id": row.to_node_id,
                    "travel_direction": row.travel_direction,
                    "highway": row.highway,
                    "road_number": row.road_number,
                    "name": row.name,
                    "oneway": row.oneway,
                    "junction": row.junction,
                    "carriageway_ref": row.carriageway_ref,
                    "lanes": row.lanes,
                    "lane_schema": lane_schemas[row.internal_segment_id],
                    "maxspeed": row.maxspeed,
                    "access": row.access,
                    "bridge": row.bridge,
                    "tunnel": row.tunnel,
                    "layer": row.layer,
                    "length_m": _float(row.length_m),
                    "segment_state": segment_state,
                    **speed,
                },
            }
        )
    metadata = {
        "query": query_type,
        "graph_version": graph.graph_version,
        "graph_source_timestamp": _iso(graph.source_timestamp),
        "segment_count": len(features),
        "truncated": truncated,
        "osm_copyright": "© OpenStreetMap contributors, ODbL",
        **(query_metadata or {}),
    }
    return _geo_response(features, metadata)


def load_direct_speed_states(
    db,
    graph_version: str,
    segment_ids: list[str],
    *,
    lane_counts: dict[str, int | None] | None = None,
    lane_order_verified: bool = False,
) -> dict[str, dict]:
    """Load only accepted, current direct measurements for returned segments."""
    if not segment_ids:
        return {}
    rows = db.execute(
        select(
            SourceLocationBinding.internal_segment_id,
            SourceLocationBinding.source_id,
            SourceLocationBinding.confidence,
            TrafficMeasurement.speed_kmh,
            TrafficMeasurement.measured_at,
            MeasurementCharacteristic.lane,
            MeasurementSite.num_lanes.label("site_lane_count"),
        )
        .join(
            TrafficMeasurement,
            TrafficMeasurement.site_id == SourceLocationBinding.source_id,
        )
        .join(
            MeasurementCharacteristic,
            and_(
                MeasurementCharacteristic.site_id == TrafficMeasurement.site_id,
                MeasurementCharacteristic.index == TrafficMeasurement.index,
            ),
        )
        .join(MeasurementSite, MeasurementSite.id == SourceLocationBinding.source_id)
        .where(
            SourceLocationBinding.source_type == SOURCE_TYPE,
            SourceLocationBinding.status == "accepted",
            SourceLocationBinding.graph_version == graph_version,
            SourceLocationBinding.algorithm_version == ALGORITHM_VERSION,
            SourceLocationBinding.internal_segment_id.in_(segment_ids),
            TrafficMeasurement.value_type == "TrafficSpeed",
            TrafficMeasurement.speed_kmh.isnot(None),
            MeasurementCharacteristic.lane.isnot(None),
            MeasurementCharacteristic.veh_length_min.is_(None),
            MeasurementCharacteristic.veh_length_max.is_(None),
        )
    ).all()

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row.internal_segment_id].append(row)
    now = datetime.now(timezone.utc)
    states = {}
    for segment_id, observations in grouped.items():
        fresh = [
            row
            for row in observations
            if row.measured_at is not None
            and -30 <= (now - _utc(row.measured_at)).total_seconds()
            <= settings.road_speed_stale_after_s
            and 0 <= float(row.speed_kmh) <= 300
        ]
        # Rejected future/outlier rows must not lend their timestamp or
        # validity window to a median calculated from accepted observations.
        timestamp_rows = [
            row
            for row in observations
            if row.measured_at is not None
            and (now - _utc(row.measured_at)).total_seconds() >= -30
        ]
        observed_at = max(
            (_utc(row.measured_at) for row in (fresh or timestamp_rows)),
            default=None,
        )
        stale = not fresh
        values = [float(row.speed_kmh) for row in fresh]
        # ``is not None`` above deliberately retains valid standstill readings.
        value = round(float(statistics.median(values)), 1) if values and not stale else None
        states[segment_id] = {
            "speed_kmh": value,
            "speed_method": "measured" if value is not None else "unknown",
            "speed_source": "NDW",
            "speed_source_ids": sorted({str(row.source_id) for row in fresh}),
            "speed_observed_at": _iso(observed_at),
            "speed_valid_until": (
                _iso(
                    _utc(observed_at)
                    + timedelta(seconds=settings.road_speed_stale_after_s)
                )
                if observed_at
                else None
            ),
            "speed_confidence": round(
                min((float(row.confidence or 0.0) for row in fresh), default=0.0),
                3,
            ),
            "speed_sample_count": len(fresh),
            "speed_stale": stale,
            "lane_states": (
                build_lane_speed_states(
                    lane_counts.get(segment_id),
                    [
                        {
                            "lane": getattr(row, "lane", None),
                            "site_lane_count": getattr(row, "site_lane_count", None),
                            "speed_kmh": row.speed_kmh,
                            "measured_at": row.measured_at,
                            "confidence": row.confidence,
                        }
                        for row in observations
                    ],
                    lane_order_verified=lane_order_verified,
                    now=now,
                    stale_after_s=settings.road_speed_stale_after_s,
                )
                if lane_counts is not None
                else []
            ),
        }
    return states


def load_live_segment_facts(
    db, graph_version: str, segment_ids: list[str]
) -> dict[str, dict[str, list[dict]]]:
    """Load fresh, accepted MSI and DRIP facts for direction-specific segments.

    DRIP is deliberately carriageway scoped. It never receives a lane field.
    NDW and OSM both order ordinary directional lanes from left to right, so a
    valid MSI source lane can be exposed canonically. Special lanes remain
    represented by the OSM lane schema. DRIP never receives a lane field.
    """
    result = {
        segment_id: {"matrix": [], "drips": []} for segment_id in segment_ids
    }
    if not segment_ids:
        return result
    now = datetime.now(timezone.utc)
    segment = aliased(OsmRoadSegment)

    matrix_rows = db.execute(
        select(
            SourceLocationBinding.internal_segment_id,
            SourceLocationBinding.source_id,
            SourceLocationBinding.confidence,
            MsiSign.road,
            MsiSign.carriageway,
            MsiSign.lane.label("source_lane"),
            MsiSign.km,
            segment.lanes.label("segment_lane_count"),
            MsiState.aspect_type,
            MsiState.value,
            MsiState.flashing,
            MsiState.red_ring,
            MsiState.ts_state,
            MsiState.ingested_at,
            (
                func.ST_LineLocatePoint(segment.geom, MsiSign.geom)
                * segment.length_m
            ).label("offset_m"),
        )
        .join(MsiSign, MsiSign.uuid == SourceLocationBinding.source_id)
        .join(MsiState, MsiState.uuid == MsiSign.uuid)
        .join(
            segment,
            and_(
                segment.graph_version == SourceLocationBinding.graph_version,
                segment.internal_segment_id
                == SourceLocationBinding.internal_segment_id,
            ),
        )
        .where(
            SourceLocationBinding.source_type == PERSISTED_SOURCE_TYPES["msi"],
            SourceLocationBinding.status == "accepted",
            SourceLocationBinding.graph_version == graph_version,
            SourceLocationBinding.algorithm_version
            == LIVE_OBJECT_ALGORITHM_VERSION,
            SourceLocationBinding.internal_segment_id.in_(segment_ids),
            MsiState.ingested_at
            >= now - timedelta(seconds=settings.road_matrix_stale_after_s),
        )
    ).all()
    verified_groups = _verified_matrix_lane_groups(matrix_rows)
    for row in matrix_rows:
        if not _matrix_has_active_state(row):
            continue
        observed_at = row.ts_state or row.ingested_at
        fact = {
            "source_id": row.source_id,
            "gantry_id": _gantry_id(row),
            "offset_m": round(float(row.offset_m), 1),
            "source_lane": row.source_lane,
            "lane_scope_status": "source_only",
            "aspect_type": row.aspect_type,
            "value": row.value,
            "flashing": bool(row.flashing),
            "red_ring": bool(row.red_ring),
            "observed_at": _iso(observed_at),
            "valid_until": _iso(
                _utc(row.ingested_at)
                + timedelta(seconds=settings.road_matrix_stale_after_s)
            ),
            "confidence": _float(row.confidence),
        }
        group_key = (row.internal_segment_id, _gantry_id(row))
        if group_key in verified_groups:
            fact["lane"] = row.source_lane
            fact["lane_scope_status"] = "canonical"
        result[row.internal_segment_id]["matrix"].append(fact)

    drip_rows = db.execute(
        select(
            SourceLocationBinding.internal_segment_id,
            SourceLocationBinding.source_id,
            SourceLocationBinding.confidence,
            Drip.description,
            Drip.display_text,
            Drip.vms_type,
            Drip.message,
            Drip.ingested_at,
            (
                func.ST_LineLocatePoint(segment.geom, Drip.geom)
                * segment.length_m
            ).label("offset_m"),
        )
        .join(
            Drip,
            SourceLocationBinding.source_id
            == func.concat(Drip.controller_id, ":", Drip.vms_index),
        )
        .join(
            segment,
            and_(
                segment.graph_version == SourceLocationBinding.graph_version,
                segment.internal_segment_id
                == SourceLocationBinding.internal_segment_id,
            ),
        )
        .where(
            SourceLocationBinding.source_type == PERSISTED_SOURCE_TYPES["drip"],
            SourceLocationBinding.status == "accepted",
            SourceLocationBinding.graph_version == graph_version,
            SourceLocationBinding.algorithm_version
            == LIVE_OBJECT_ALGORITHM_VERSION,
            SourceLocationBinding.internal_segment_id.in_(segment_ids),
            Drip.ingested_at
            >= now - timedelta(seconds=settings.road_drip_stale_after_s),
        )
    ).all()
    for row in drip_rows:
        result[row.internal_segment_id]["drips"].append(
            {
                "source_id": row.source_id,
                "offset_m": round(float(row.offset_m), 1),
                "description": row.description,
                "display_text": row.display_text,
                "vms_type": row.vms_type,
                "message": row.message,
                "updated_at": _iso(row.ingested_at),
                "valid_until": _iso(
                    _utc(row.ingested_at)
                    + timedelta(seconds=settings.road_drip_stale_after_s)
                ),
                "confidence": _float(row.confidence),
            }
        )
    return result


def _matrix_has_active_state(row) -> bool:
    aspect = str(row.aspect_type or "").strip().lower()
    return bool(
        (aspect and aspect not in {"blank", "off", "unknown"})
        or row.value not in (None, "")
        or row.flashing
        or row.red_ring
    )


def _verified_matrix_lane_groups(rows) -> set[tuple[str, str]]:
    """Accept lane scope only for a complete, unambiguous physical portal."""
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        groups[(row.internal_segment_id, _gantry_id(row))].append(row)
    verified = set()
    for key, group in groups.items():
        counts = {row.segment_lane_count for row in group}
        lanes = [row.source_lane for row in group]
        if len(counts) != 1:
            continue
        lane_count = next(iter(counts))
        if (
            isinstance(lane_count, int)
            and lane_count > 0
            and len(lanes) == lane_count
            and all(isinstance(lane, int) for lane in lanes)
            and sorted(lanes) == list(range(1, lane_count + 1))
        ):
            verified.add(key)
    return verified


def _gantry_id(row) -> str:
    km = f"{float(row.km):.2f}" if row.km is not None else "unknown"
    return f"{row.road or 'unknown'}|{row.carriageway or 'unknown'}|{km}"


def assign_corridor_speed_states(db, graph, rows, direct_states: dict[str, dict]) -> dict:
    """Expand direct readings over complete one-to-one topology in ``rows``.

    The adjacency query deliberately includes ids outside the response. The
    pure model treats those missing members as a hard boundary, preventing a
    clipped viewport from looking like a complete chain or hiding a fork.
    Immediate U-turn edges are excluded; all remaining directed alternatives
    count toward fork/merge detection.
    """
    if not rows:
        return {}
    segment_ids = [str(row.internal_segment_id) for row in rows]
    segment_id_set = set(segment_ids)
    current = aliased(OsmRoadSegment)
    neighbor = aliased(OsmRoadSegment)
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)

    outgoing_rows = db.execute(
        select(current.internal_segment_id, neighbor.internal_segment_id)
        .join(
            neighbor,
            and_(
                neighbor.import_run_id == current.import_run_id,
                neighbor.from_node_id == current.to_node_id,
                neighbor.to_node_id != current.from_node_id,
            ),
        )
        .where(
            current.import_run_id == graph.id,
            current.internal_segment_id.in_(segment_ids),
        )
    ).all()
    incoming_rows = db.execute(
        select(current.internal_segment_id, neighbor.internal_segment_id)
        .join(
            neighbor,
            and_(
                neighbor.import_run_id == current.import_run_id,
                neighbor.to_node_id == current.from_node_id,
                neighbor.from_node_id != current.to_node_id,
            ),
        )
        .where(
            current.import_run_id == graph.id,
            current.internal_segment_id.in_(segment_ids),
        )
    ).all()
    for segment_id, next_id in outgoing_rows:
        outgoing[str(segment_id)].append(str(next_id))
    for segment_id, previous_id in incoming_rows:
        incoming[str(segment_id)].append(str(previous_id))

    model_segments = [
        SpeedSegment(
            internal_segment_id=str(row.internal_segment_id),
            length_m=float(row.length_m),
            road_ref=row.road_number,
            carriageway_ref=row.carriageway_ref,
            # OSM forward/backward is relative to each source way, not a
            # cross-way direction identity. Directed endpoints plus removal of
            # immediate U-turns are canonical here.
            travel_direction="directed",
            predecessor_ids=tuple(sorted(set(incoming[str(row.internal_segment_id)]))),
            successor_ids=tuple(sorted(set(outgoing[str(row.internal_segment_id)]))),
        )
        for row in rows
    ]
    length_by_id = {
        str(row.internal_segment_id): float(row.length_m)
        for row in rows
    }
    observations: list[SpeedObservation] = []
    for segment_id, state in direct_states.items():
        if segment_id not in segment_id_set or state.get("speed_kmh") is None:
            continue
        observed_at = _parse_iso_datetime(state.get("speed_observed_at"))
        if observed_at is None:
            continue
        source_ids = state.get("speed_source_ids") or [f"segment:{segment_id}"]
        for source_id in source_ids:
            observations.append(
                SpeedObservation(
                    source_id=str(source_id),
                    segment_id=segment_id,
                    offset_m=length_by_id[segment_id] / 2,
                    speed_kmh=float(state["speed_kmh"]),
                    observed_at=observed_at,
                    confidence=float(state.get("speed_confidence") or 0.0),
                    source=str(state.get("speed_source") or "NDW"),
                )
            )

    assigned = assign_speed_states(
        model_segments,
        observations,
        stale_after_s=settings.road_speed_stale_after_s,
        propagation_limit_m=settings.road_speed_propagation_max_m,
        interpolation_limit_m=settings.road_speed_interpolation_max_m,
    )
    for segment_id in segment_ids:
        direct = direct_states.get(segment_id)
        if direct and direct.get("speed_method") == "measured":
            # Preserve exact direct sample counts and lane states; the pure
            # model receives one normalized anchor per source location.
            assigned[segment_id] = {**direct}
        elif (
            direct
            and direct.get("speed_stale")
            and assigned[segment_id]["speed_method"] == "unknown"
        ):
            assigned[segment_id] = {**direct}
        assigned[segment_id]["lane_states"] = (
            direct.get("lane_states", []) if direct else []
        )
    return assigned


def unknown_speed_state() -> dict:
    return {
        "speed_kmh": None,
        "speed_method": "unknown",
        "speed_source": None,
        "speed_source_ids": [],
        "speed_observed_at": None,
        "speed_valid_until": None,
        "speed_confidence": 0.0,
        "speed_sample_count": 0,
        "speed_stale": False,
        "lane_states": [],
    }


def _canonical_speed_fact(speed: dict) -> dict:
    return {
        "speed_kmh": speed.get("speed_kmh"),
        "method": speed.get("speed_method", "unknown"),
        "source": speed.get("speed_source"),
        "sources": speed.get("speed_source_ids", []),
        "observed_at": speed.get("speed_observed_at"),
        "valid_until": speed.get("speed_valid_until"),
        "confidence": speed.get("speed_confidence", 0.0),
        "sample_count": speed.get("speed_sample_count", 0),
        "stale": speed.get("speed_stale", True),
    }


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _lane_schema(row) -> dict | None:
    """Serve imported schemas and safely hydrate pre-migration graph rows."""
    if row.lane_schema is not None:
        return row.lane_schema
    return build_lane_schema(
        row.tags or {},
        row.travel_direction,
        lane_count=row.lanes,
        oneway=row.oneway,
        highway=row.highway,
    )


def _geo_response(features: list[dict], metadata: dict) -> Response:
    return Response(
        content=json.dumps(
            {"type": "FeatureCollection", "features": features, "metadata": metadata},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        media_type="application/geo+json",
        headers={"X-Roads-Truncated": str(metadata.get("truncated", False)).lower()},
    )


def _float(value):
    return float(value) if value is not None else None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return _utc(value).isoformat()
