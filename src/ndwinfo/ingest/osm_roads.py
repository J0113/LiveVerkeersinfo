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
from ndwinfo.parsers.osm_lanes import make_lane_rows
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

        for row in parse_roads(result.path):
            lane_batch.extend(
                make_lane_rows(row["osm_id"], row["highway"], row["raw"], from_wkt(row["geom"]))
            )
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
        for lane_row in lane_batch:
            lane_row["geom"] = wkt_geom(lane_row["geom"])
            lane_row["raw"] = json_safe(lane_row["raw"])
        bulk_upsert(session, OsmRoadLane, lane_batch, ["id"])

        session.flush()
        return n
