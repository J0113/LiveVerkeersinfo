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
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from ndwinfo.db import SessionLocal
from ndwinfo.download import DownloadResult
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, json_safe, wkt_geom
from ndwinfo.models import (
    FeedRun,
    OsmLaneCenterline,
    OsmLaneConnection,
    OsmRoad,
    OsmRoadExtract,
)
from ndwinfo.parsers.osm_lane_connections import build_lane_network
from ndwinfo.parsers.osm_lane_lines import make_lane_line_rows
from ndwinfo.parsers.osm_pbf import parse_roads

UTC = timezone.utc


class OsmRoadIngester(Ingester):
    def __init__(
        self,
        feed_name: str,
        extract_key: str,
        *,
        build_independent_lanes: bool = True,
        rebuild_independent_lanes_after_ingest: bool = False,
    ):
        self.feed_name = feed_name
        self.extract_key = extract_key
        self.build_independent_lanes = build_independent_lanes
        self.rebuild_independent_lanes_after_ingest = (
            rebuild_independent_lanes_after_ingest
        )

    def run(self) -> None:
        """Run the source ingest, then optionally rebuild Lanes in bounded tiles."""
        with SessionLocal() as session:
            previous_id = session.scalar(
                select(FeedRun.id)
                .where(FeedRun.feed == self.feed_name)
                .order_by(desc(FeedRun.id))
                .limit(1)
            )
        super().run()
        if not self.rebuild_independent_lanes_after_ingest:
            return
        with SessionLocal() as session:
            latest = session.execute(
                select(FeedRun)
                .where(FeedRun.feed == self.feed_name)
                .order_by(desc(FeedRun.id))
                .limit(1)
            ).scalar_one_or_none()
        if latest is None or latest.id == previous_id or latest.status != "ok":
            return
        from ndwinfo.refresh_osm_lane_lines import rebuild_all_tiled

        with SessionLocal() as session:
            rebuild_all_tiled(session)

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        run_start = datetime.now(UTC)
        total = 0
        batch: list[dict] = []
        lane_line_batch: list[dict] = []
        lane_line_rows_by_id: dict[str, dict] = {}
        lane_line_contexts: dict[int, dict] = {}

        for row in parse_roads(result.path):
            line = from_wkt(row["geom"])
            shared_node_ids = set(row.pop("_shared_node_ids", ()))
            if self.build_independent_lanes:
                lane_line_rows, lane_line_failures = make_lane_line_rows(
                    row["osm_id"],
                    row["highway"],
                    row["raw"],
                    line,
                    node_refs=row.get("node_refs"),
                    shared_node_ids=shared_node_ids,
                )
                if lane_line_failures:
                    # Failures are deliberately omitted; the rebuild CLI
                    # provides the detailed per-segment report used in review.
                    pass
                lane_line_batch.extend(lane_line_rows)
                lane_line_rows_by_id.update(
                    (lane_line["id"], lane_line) for lane_line in lane_line_rows
                )
                lane_line_contexts[row["osm_id"]] = {
                    "highway": row["highway"],
                    "tags": dict(row["raw"]),
                }
            row["geom"] = wkt_geom(row["geom"])
            row["raw"] = json_safe(row["raw"])
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                total += self._flush(session, batch, lane_line_batch)
                batch.clear()
                lane_line_batch.clear()

        if batch:
            total += self._flush(session, batch, lane_line_batch)

        # A parse that yields nothing (bad/truncated download, upstream
        # schema change) must not be treated as "this extract now has zero
        # roads" and prune away a previously-good layer.
        if total == 0:
            raise RuntimeError(f"{self.feed_name}: parsed 0 road ways, aborting without pruning")

        # Safe after the loop: every batch above already deleted its ways'
        # existing lane rows, and these ways' road rows are all committed.
        if self.build_independent_lanes:
            self._flush_lane_line_connections(
                session, lane_line_rows_by_id, lane_line_contexts
            )

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

    def _flush(
        self,
        session: Session,
        batch: list[dict],
        lane_line_batch: list[dict] | None = None,
    ) -> int:
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
        if self.build_independent_lanes:
            osm_ids = [row["osm_id"] for row in batch]
            session.execute(
                delete(OsmLaneCenterline).where(
                    OsmLaneCenterline.road_id.in_(osm_ids)
                )
            )
            self._insert_lane_lines(session, lane_line_batch or [])

        session.flush()
        return n

    @staticmethod
    def _insert_lane_lines(session: Session, lane_rows: list[dict]) -> None:
        prepared = []
        for lane_row in lane_rows:
            row = dict(lane_row)
            row["geom"] = wkt_geom(row["geom"])
            row["raw"] = json_safe(row["raw"])
            prepared.append(row)
        bulk_upsert(session, OsmLaneCenterline, prepared, ["id"])

    @staticmethod
    def _flush_lane_line_connections(
        session: Session,
        lane_rows_by_id: dict[str, dict],
        contexts: dict[int, dict],
    ) -> None:
        session.execute(delete(OsmLaneConnection))
        (
            resolved_lane_rows,
            rows,
            _diagnostics,
            _counters,
            resolved_road_ids,
        ) = build_lane_network(
            list(lane_rows_by_id.values()), contexts
        )
        if resolved_road_ids:
            OsmRoadIngester._insert_lane_lines(
                session,
                [
                    row
                    for row in resolved_lane_rows
                    if row["road_id"] in resolved_road_ids
                ],
            )
        for start in range(0, len(rows), BATCH_SIZE):
            prepared = []
            for connection in rows[start : start + BATCH_SIZE]:
                row = dict(connection)
                row["geom"] = wkt_geom(row["geom"])
                row["raw"] = json_safe(row["raw"])
                prepared.append(row)
            bulk_upsert(session, OsmLaneConnection, prepared, ["id"])
            session.flush()
