"""Ingester for all 6 DATEX v3 situation feeds."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.models import Situation
from ndwinfo.parsers.datex_v3 import parse_situations

UTC = timezone.utc


class SituationIngester(Ingester):
    def __init__(self, feed_name: str, category: str) -> None:
        self.feed_name = feed_name
        self.category = category

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        # Capture time before upserts so we can prune rows not touched in this run
        run_start = datetime.now(UTC)
        total = 0
        batch: list[dict] = []
        saw_any = False

        with open_feed(result.path) as f:
            for row in parse_situations(f, self.category):
                # Typed columns drive filtering/matching. The complete contract
                # remains in JSONB for provenance and future DATEX extensions.
                raw = dict(row.get("raw") or {})
                raw["feed_name"] = self.feed_name
                raw["feed_category"] = self.category
                r = {
                    key: value
                    for key, value in row.items()
                    if key in Situation.__table__.columns.keys()
                }
                r["feed_name"] = self.feed_name
                r["raw"] = raw
                r["geom"] = wkt_geom(r.get("geom"))
                batch.append(r)
                saw_any = True
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(
                        session, Situation, batch, ["record_id", "feed_name"]
                    )
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(
                session, Situation, batch, ["record_id", "feed_name"]
            )
            session.flush()

        # Prune only records last written by this publication. actueel_beeld is
        # mixed-category, while the same record may also occur in a specialized
        # feed, so category alone is not a safe ownership boundary.
        owned_by_feed = Situation.feed_name == self.feed_name
        if saw_any:
            session.execute(
                delete(Situation).where(
                    owned_by_feed,
                    Situation.ingested_at < run_start,
                )
            )
        else:
            session.execute(delete(Situation).where(owned_by_feed))

        session.flush()
        return total


# Named instances wired to feeds.py
ActueleBeeldIngester = SituationIngester("actueel_beeld", "incident")
SrtiIngester = SituationIngester("srti", "srti")
RoadworksIngester = SituationIngester("roadworks", "roadworks")
BridgeOpeningsIngester = SituationIngester("bridge_openings", "bridge_opening")
ClosuresIngester = SituationIngester("closures", "closure")
SpeedLimitsIngester = SituationIngester("speed_limits", "speed_limit")
