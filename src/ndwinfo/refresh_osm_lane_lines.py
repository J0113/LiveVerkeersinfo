"""Rebuild the independent OSM lane centerlines and directed connections."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from shapely import from_wkt
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ndwinfo.db import SessionLocal
from ndwinfo.ingest.base import BATCH_SIZE, bulk_upsert, json_safe, wkt_geom
from ndwinfo.models import (
    OsmLaneCenterline,
    OsmLaneConnection,
    OsmRoad,
)
from ndwinfo.parsers.osm_lane_connections import (
    build_lane_network,
    load_connection_overrides,
)
from ndwinfo.parsers.osm_lane_lines import (
    make_lane_line_rows,
    topology_node_ids,
)

DEFAULT_CONTEXT_DEGREES = 0.0005
DEFAULT_ALL_TILE_DEGREES = 0.2
DEFAULT_OVERRIDES = Path(__file__).with_name("osm_lane_connection_overrides.json")


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        numbers = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("bbox must contain four numbers") from error
    if len(numbers) != 4:
        raise argparse.ArgumentTypeError("bbox must be min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = numbers
    if min_lon >= max_lon or min_lat >= max_lat:
        raise argparse.ArgumentTypeError("bbox minimums must be below maximums")
    return numbers


def parse_ids(value: str) -> list[int]:
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError("road IDs must be comma-separated integers") from error


def parse_segments(value: str) -> list[str]:
    segments = [part.strip() for part in value.split(",") if part.strip()]
    if any(len(segment.split(":")) != 3 for segment in segments):
        raise argparse.ArgumentTypeError(
            "segments must be comma-separated osm_id:start_node_id:end_node_id IDs"
        )
    return segments


def _road_query():
    return select(OsmRoad, func.ST_AsText(OsmRoad.geom).label("geom_wkt"))


def _load_selected_roads(
    session: Session,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    road_ids: Sequence[int] = (),
    segment_ids: Sequence[str] = (),
    all_roads: bool = False,
) -> list[Any]:
    query = _road_query()
    if all_roads:
        return session.execute(query.order_by(OsmRoad.osm_id)).all()
    predicates = []
    if bbox is not None:
        predicates.append(
            func.ST_Intersects(
                OsmRoad.geom,
                func.ST_MakeEnvelope(*bbox, 4326),
            )
        )
    selected_ids = set(road_ids)
    selected_ids.update(int(segment.split(":", 1)[0]) for segment in segment_ids)
    if selected_ids:
        predicates.append(OsmRoad.osm_id.in_(sorted(selected_ids)))
    if not predicates:
        raise ValueError("one of bbox, roads, segments, or all_roads is required")
    return session.execute(query.where(or_(*predicates)).order_by(OsmRoad.osm_id)).all()


def _load_context_roads(
    session: Session,
    selected_rows: Sequence[Any],
    *,
    all_roads: bool,
    context_degrees: float,
) -> list[Any]:
    if all_roads or not selected_rows:
        return list(selected_rows)
    selected_ids = [row.OsmRoad.osm_id for row in selected_rows]
    selected_node_ids = {
        node_id
        for row in selected_rows
        for node_id in (row.OsmRoad.node_refs or ())
    }
    extent = (
        select(func.ST_Envelope(func.ST_Collect(OsmRoad.geom)))
        .where(OsmRoad.osm_id.in_(selected_ids))
        .scalar_subquery()
    )
    predicates = [
        OsmRoad.osm_id.in_(selected_ids),
        func.ST_Intersects(
            OsmRoad.geom,
            func.ST_Expand(extent, context_degrees),
        ),
    ]
    if selected_node_ids:
        predicates.append(OsmRoad.node_refs.overlap(sorted(selected_node_ids)))
    return session.execute(
        _road_query().where(or_(*predicates)).order_by(OsmRoad.osm_id)
    ).all()


def _prepare_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for source in rows:
        row = dict(source)
        row["geom"] = wkt_geom(row["geom"])
        row["raw"] = json_safe(row.get("raw") or {})
        prepared.append(row)
    return prepared


def _override_road_ids(item: dict[str, Any]) -> set[int]:
    road_ids = set()
    for key in ("from", "to"):
        value = str(item.get(key, ""))
        if value.startswith("ll:"):
            value = value[3:]
        try:
            road_ids.add(int(value.split(":", 1)[0]))
        except ValueError:
            continue
    return road_ids


def _insert_batches(session: Session, model, rows: Sequence[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH_SIZE):
        bulk_upsert(session, model, _prepare_rows(rows[start : start + BATCH_SIZE]), ["id"])
        session.flush()


def rebuild(
    session: Session,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    road_ids: Sequence[int] = (),
    segment_ids: Sequence[str] = (),
    all_roads: bool = False,
    context_degrees: float = DEFAULT_CONTEXT_DEGREES,
    overrides_path: Path | str = DEFAULT_OVERRIDES,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Rebuild selected roads plus the topology/junction context they need."""
    selected_rows = _load_selected_roads(
        session,
        bbox=bbox,
        road_ids=road_ids,
        segment_ids=segment_ids,
        all_roads=all_roads,
    )
    requested_road_ids = {row.OsmRoad.osm_id for row in selected_rows}
    all_overrides = load_connection_overrides(overrides_path)
    relevant_overrides = (
        all_overrides
        if all_roads
        else [
            item
            for item in all_overrides
            if _override_road_ids(item) & requested_road_ids
        ]
    )
    context_rows = _load_context_roads(
        session,
        selected_rows,
        all_roads=all_roads,
        context_degrees=context_degrees,
    )
    context_ids = {row.OsmRoad.osm_id for row in context_rows}
    override_context_ids = {
        road_id
        for item in relevant_overrides
        for road_id in _override_road_ids(item)
        if road_id not in context_ids
    }
    if override_context_ids:
        context_rows = [
            *context_rows,
            *session.execute(
                _road_query()
                .where(OsmRoad.osm_id.in_(sorted(override_context_ids)))
                .order_by(OsmRoad.osm_id)
            ).all(),
        ]
    if not context_rows:
        return {"source_roads": 0, "lane_lines": 0, "connections": 0}, []

    shared_nodes = topology_node_ids(row.OsmRoad.node_refs for row in context_rows)
    lane_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    contexts: dict[int, dict[str, Any]] = {}
    counts = Counter()
    for result_row in context_rows:
        road = result_row.OsmRoad
        line = from_wkt(result_row.geom_wkt)
        produced, failures = make_lane_line_rows(
            road.osm_id,
            road.highway,
            dict(road.raw or {}),
            line,
            node_refs=road.node_refs,
            shared_node_ids=shared_nodes,
        )
        lane_rows.extend(produced)
        diagnostics.extend(failures)
        contexts[road.osm_id] = {
            "highway": road.highway,
            "tags": dict(road.raw or {}),
        }
        if produced:
            sample = produced[0]
            counts[f"count_source_{sample['count_source']}"] += 1
            if sample["oneway_source"] == "roundabout_implied":
                counts["roundabout_implied"] += 1
        counts["shared_single_track"] += sum(row["direction"] == "both" for row in produced)
        counts["ambiguous_lines"] += sum(row["direction"] == "unknown" for row in produced)
    counts["geometry_failures"] = len(diagnostics)
    counts["geometry_empty_or_multipart"] = sum(
        item["reason"] == "empty_or_multipart" for item in diagnostics
    )
    counts["geometry_degenerate_offset"] = sum(
        item["reason"] == "degenerate_offset" for item in diagnostics
    )
    (
        lane_rows,
        all_connection_rows,
        network_diagnostics,
        connection_counts,
        resolved_transition_road_ids,
    ) = build_lane_network(
        lane_rows,
        contexts,
        overrides=relevant_overrides,
    )
    diagnostics.extend(network_diagnostics)

    generated_ids_by_road: dict[int, set[str]] = {}
    for lane_row in lane_rows:
        generated_ids_by_road.setdefault(lane_row["road_id"], set()).add(lane_row["id"])
    existing_rows = session.execute(
        select(OsmLaneCenterline.road_id, OsmLaneCenterline.id).where(
            OsmLaneCenterline.road_id.in_(sorted(contexts))
        )
    ).all()
    existing_ids_by_road: dict[int, set[str]] = {}
    for road_id, lane_id in existing_rows:
        existing_ids_by_road.setdefault(road_id, set()).add(lane_id)
    # Context roads are rewritten only when their derived segment/lane identity
    # differs (including a first build). Otherwise they remain read-only graph
    # context and their farther connections are preserved.
    rewrite_road_ids = sorted(
        requested_road_ids
        | resolved_transition_road_ids
        | {
            road_id
            for road_id, generated_ids in generated_ids_by_road.items()
            if existing_ids_by_road.get(road_id, set()) != generated_ids
        }
    )
    rewrite_lane_rows = [
        lane_row for lane_row in lane_rows if lane_row["road_id"] in rewrite_road_ids
    ]
    session.execute(
        delete(OsmLaneConnection).where(
            or_(
                OsmLaneConnection.from_road_id.in_(rewrite_road_ids),
                OsmLaneConnection.to_road_id.in_(rewrite_road_ids),
            )
        )
    )
    session.execute(
        delete(OsmLaneCenterline).where(OsmLaneCenterline.road_id.in_(rewrite_road_ids))
    )
    session.flush()
    _insert_batches(session, OsmLaneCenterline, rewrite_lane_rows)

    connection_rows = [
        row
        for row in all_connection_rows
        if row["from_road_id"] in rewrite_road_ids or row["to_road_id"] in rewrite_road_ids
    ]
    _insert_batches(session, OsmLaneConnection, connection_rows)

    return {
        "source_roads": len(context_rows),
        "lane_lines": len(rewrite_lane_rows),
        "connections": len(connection_rows),
        "logical_segments": len({row["segment_id"] for row in lane_rows}),
        **dict(counts),
        **connection_counts,
    }, diagnostics


def rebuild_all_tiled(
    session: Session,
    *,
    tile_degrees: float = DEFAULT_ALL_TILE_DEGREES,
    context_degrees: float = DEFAULT_CONTEXT_DEGREES,
    overrides_path: Path | str = DEFAULT_OVERRIDES,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Rebuild the national layer in bounded spatial transactions."""
    if tile_degrees <= 0:
        raise ValueError("tile_degrees must be positive")
    extent = session.execute(
        select(
            func.ST_XMin(func.ST_Extent(OsmRoad.geom)),
            func.ST_YMin(func.ST_Extent(OsmRoad.geom)),
            func.ST_XMax(func.ST_Extent(OsmRoad.geom)),
            func.ST_YMax(func.ST_Extent(OsmRoad.geom)),
        )
    ).one()
    if any(value is None for value in extent):
        return {"source_roads": 0, "lane_lines": 0, "connections": 0}, []
    min_lon, min_lat, max_lon, max_lat = (float(value) for value in extent)
    columns = max(1, math.ceil((max_lon - min_lon) / tile_degrees))
    rows = max(1, math.ceil((max_lat - min_lat) / tile_degrees))
    aggregate = Counter()
    diagnostic_counts = Counter()
    processed_tiles = 0
    for row_index in range(rows):
        south = min_lat + row_index * tile_degrees
        north = min(max_lat, south + tile_degrees)
        for column_index in range(columns):
            west = min_lon + column_index * tile_degrees
            east = min(max_lon, west + tile_degrees)
            counters, diagnostics = rebuild(
                session,
                bbox=(west, south, east, north),
                context_degrees=context_degrees,
                overrides_path=overrides_path,
            )
            session.commit()
            session.expunge_all()
            if counters.get("source_roads", 0):
                processed_tiles += 1
                aggregate.update(counters)
                diagnostic_counts.update(
                    str(item.get("reason", "unknown"))
                    for item in diagnostics
                )

    final_lane_lines = session.scalar(select(func.count()).select_from(OsmLaneCenterline))
    final_connections = session.scalar(select(func.count()).select_from(OsmLaneConnection))
    final_source_roads = session.scalar(select(func.count()).select_from(OsmRoad))
    counters = {
        **dict(aggregate),
        "source_roads": int(final_source_roads or 0),
        "lane_lines": int(final_lane_lines or 0),
        "connections": int(final_connections or 0),
        "processed_tiles": processed_tiles,
        **{
            f"diagnostic_{reason}": count
            for reason, count in sorted(diagnostic_counts.items())
        },
    }
    diagnostics = [
        {"reason": reason, "count": count}
        for reason, count in sorted(diagnostic_counts.items())
    ]
    return counters, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--bbox", type=parse_bbox)
    selection.add_argument("--all", action="store_true", dest="all_roads")
    selection.add_argument("--roads", type=parse_ids, default=[])
    selection.add_argument("--segments", type=parse_segments, default=[])
    parser.add_argument("--context-degrees", type=float, default=DEFAULT_CONTEXT_DEGREES)
    parser.add_argument(
        "--tile-degrees",
        type=float,
        default=DEFAULT_ALL_TILE_DEGREES,
        help="spatial tile size used by --all to bound memory",
    )
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument(
        "--unresolved-only",
        action="store_true",
        help="print unresolved/geometry diagnostics as JSON instead of counters",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        if args.all_roads:
            counters, diagnostics = rebuild_all_tiled(
                session,
                tile_degrees=args.tile_degrees,
                context_degrees=args.context_degrees,
                overrides_path=args.overrides,
            )
        else:
            counters, diagnostics = rebuild(
                session,
                bbox=args.bbox,
                road_ids=args.roads,
                segment_ids=args.segments,
                context_degrees=args.context_degrees,
                overrides_path=args.overrides,
            )
            session.commit()
    if args.unresolved_only:
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(counters, sort_keys=True))


if __name__ == "__main__":
    main()
