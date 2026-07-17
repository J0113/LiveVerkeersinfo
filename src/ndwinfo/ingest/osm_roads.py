"""Ingester: Geofabrik OSM PBF driving-road extracts.

Configurable by extract_key so additional province feeds (or a later
full-country swap) are just another registry entry, not a schema change.
Pruning is scoped to this instance's extract_key via OsmRoadExtract
membership rows -- never deletes an OsmRoad still claimed by another
extract, unlike a single-timestamp prune (see NwbWegvakkenIngester, which
is safe only because NWB has one national snapshot).
"""

from __future__ import annotations

from datetime import datetime, timezone

from shapely import from_wkt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, json_safe, wkt_geom
from ndwinfo.models import OsmRoad, OsmRoadExtract, OsmRoadLane
from ndwinfo.parsers.osm_junctions import junction_record, make_connector_rows
from ndwinfo.parsers.osm_lanes import has_merge_tokens, make_all_lane_rows, make_lane_rows
from ndwinfo.parsers.osm_pbf import parse_roads

UTC = timezone.utc


class OsmRoadIngester(Ingester):
    def __init__(self, feed_name: str, extract_key: str):
        self.feed_name = feed_name
        self.extract_key = extract_key

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        run_start = datetime.now(UTC)
        total = 0
        batch: list[dict] = []
        lane_batch: list[dict] = []
        # A merging lane's geometry depends on the chain of merge-tagged ways
        # it continues into, which a single streaming pass hasn't seen yet --
        # so those ways (a few hundred per extract) wait until the end. Every
        # other way still streams straight through.
        merge_ways: list[tuple] = []
        # Junction connectors need both sides of a turn, so they also wait for
        # the end -- but only two coordinates per lane are kept, not geometry.
        junctions: dict[int, dict] = {}

        for row in parse_roads(result.path):
            if has_merge_tokens(row["raw"]):
                merge_ways.append(
                    (row["osm_id"], row["highway"], dict(row["raw"]), from_wkt(row["geom"]))
                )
            else:
                rows = make_lane_rows(
                    row["osm_id"], row["highway"], row["raw"], from_wkt(row["geom"])
                )
                self._record_junction(junctions, row["osm_id"], row["raw"], rows)
                lane_batch.extend(rows)
            row["geom"] = wkt_geom(row["geom"])
            row["raw"] = json_safe(row["raw"])
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                total += self._flush(session, batch, lane_batch)
                batch.clear()
                lane_batch.clear()

        if batch:
            total += self._flush(session, batch, lane_batch)

        # A parse that yields nothing (bad/truncated download, upstream
        # schema change) must not be treated as "this extract now has zero
        # roads" and prune away a previously-good layer.
        if total == 0:
            raise RuntimeError(f"{self.feed_name}: parsed 0 road ways, aborting without pruning")

        # Safe after the loop: every batch above already deleted its ways'
        # existing lane rows, and these ways' road rows are all committed.
        self._flush_merge_lanes(session, merge_ways, junctions)
        self._flush_connectors(session, junctions)

        # Extract-scoped prune only -- never touches another extract's ways.
        session.execute(
            delete(OsmRoadExtract)
            .where(OsmRoadExtract.extract_key == self.extract_key)
            .where(OsmRoadExtract.ingested_at < run_start)
        )
        # Drop OsmRoad rows with no remaining membership in any extract.
        session.execute(
            delete(OsmRoad).where(~OsmRoad.osm_id.in_(select(OsmRoadExtract.osm_id)))
        )
        session.flush()

        return total

    def _flush(self, session: Session, batch: list[dict], lane_batch: list[dict]) -> int:
        n = bulk_upsert(session, OsmRoad, batch, ["osm_id"])
        bulk_upsert(
            session,
            OsmRoadExtract,
            [{"extract_key": self.extract_key, "osm_id": row["osm_id"]} for row in batch],
            ["extract_key", "osm_id"],
        )

        # A way's lane count can shrink between runs -- upsert-by-id alone
        # wouldn't remove the now-excess lane rows, so clear this batch's
        # ways' lanes first and reinsert fresh.
        osm_ids = [row["osm_id"] for row in batch]
        session.execute(delete(OsmRoadLane).where(OsmRoadLane.source_id.in_(osm_ids)))
        self._insert_lanes(session, lane_batch)

        session.flush()
        return n

    def _flush_merge_lanes(self, session: Session, merge_ways: list[tuple], junctions: dict) -> None:
        rows = make_all_lane_rows(merge_ways)
        by_way: dict[int, list[dict]] = {}
        for row in rows:
            by_way.setdefault(row["source_id"], []).append(row)
        for osm_id, highway, tags, _line in merge_ways:
            self._record_junction(junctions, osm_id, tags, by_way.get(osm_id, []))
        for start in range(0, len(rows), BATCH_SIZE):
            self._insert_lanes(session, rows[start:start + BATCH_SIZE])
            session.flush()

    def _flush_connectors(self, session: Session, junctions: dict) -> None:
        rows = make_connector_rows(junctions)
        for start in range(0, len(rows), BATCH_SIZE):
            self._insert_lanes(session, rows[start:start + BATCH_SIZE])
            session.flush()

    @staticmethod
    def _record_junction(junctions: dict, osm_id: int, tags: dict, lane_rows: list[dict]) -> None:
        if not lane_rows:
            return
        record = junction_record(osm_id, tags, lane_rows)
        if record is not None:
            junctions[osm_id] = record

    @staticmethod
    def _insert_lanes(session: Session, lane_rows: list[dict]) -> None:
        for lane_row in lane_rows:
            lane_row["geom"] = wkt_geom(lane_row["geom"])
            lane_row["raw"] = json_safe(lane_row["raw"])
        bulk_upsert(session, OsmRoadLane, lane_rows, ["id"])
