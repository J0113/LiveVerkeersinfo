"""Regenerate OSM lane geometry for a small database-backed region.

This is a development/verification path: it reuses already-ingested osm_road
ways instead of reparsing the nationwide PBF. The bbox is expanded so junction
topology and merge chains immediately outside the requested view are present.
"""

from __future__ import annotations

import argparse

from shapely import from_wkt
from sqlalchemy import delete, func, select

from ndwinfo.db import SessionLocal
from ndwinfo.ingest.osm_roads import OsmRoadIngester
from ndwinfo.models import OsmRoad, OsmRoadLane
from ndwinfo.parsers.osm_junctions import (
    combine_connector_rows,
    continuation_records,
    junction_record,
    make_connector_rows,
    make_continuation_rows,
)
from ndwinfo.parsers.osm_lanes import make_all_lane_rows


def _bbox(value: str) -> tuple[float, float, float, float]:
    try:
        numbers = tuple(float(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox must contain four numbers") from exc
    if len(numbers) != 4:
        raise argparse.ArgumentTypeError("bbox must be min_lon,min_lat,max_lon,max_lat")
    return numbers


def refresh(bbox: tuple[float, float, float, float], expand_deg: float = 0.003) -> tuple[int, int]:
    min_lon, min_lat, max_lon, max_lat = bbox
    with SessionLocal() as session:
        envelope = func.ST_MakeEnvelope(
            min_lon - expand_deg,
            min_lat - expand_deg,
            max_lon + expand_deg,
            max_lat + expand_deg,
            4326,
        )
        road_rows = session.execute(
            select(
                OsmRoad,
                func.ST_AsText(OsmRoad.geom).label("geom_wkt"),
            ).where(func.ST_Intersects(OsmRoad.geom, envelope))
        ).all()
        ways = [
            (
                row.OsmRoad.osm_id,
                row.OsmRoad.highway,
                dict(row.OsmRoad.raw or {}),
                from_wkt(row.geom_wkt),
            )
            for row in road_rows
        ]
        lane_rows = make_all_lane_rows(ways)
        by_way: dict[int, list[dict]] = {}
        for row in lane_rows:
            by_way.setdefault(row["source_id"], []).append(row)

        junctions = {}
        continuations = {}
        for osm_id, _highway, tags, line in ways:
            rows = by_way.get(osm_id, [])
            junction = junction_record(osm_id, tags, rows)
            if junction is not None:
                junctions[osm_id] = junction
            for record in continuation_records(osm_id, tags, line, rows):
                continuations[record["key"]] = record

        lane_rows_by_id = {row["id"]: row for row in lane_rows}
        connector_rows = combine_connector_rows(
            make_connector_rows(junctions),
            make_continuation_rows(continuations, lane_rows_by_id),
        )
        source_ids = [way[0] for way in ways]
        session.execute(
            delete(OsmRoadLane).where(OsmRoadLane.source_id.in_(source_ids))
        )
        ingester = OsmRoadIngester(
            feed_name="osm_netherlands",
            extract_key="netherlands",
        )
        ingester._insert_lanes(session, lane_rows + connector_rows)
        session.commit()
        return len(ways), len(lane_rows) + len(connector_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", required=True, type=_bbox)
    parser.add_argument("--expand-deg", type=float, default=0.003)
    args = parser.parse_args()
    ways, lane_rows = refresh(args.bbox, args.expand_deg)
    print(f"refreshed {lane_rows} lane rows from {ways} OSM ways")


if __name__ == "__main__":
    main()
