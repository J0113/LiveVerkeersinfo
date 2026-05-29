"""Ingester: traffic signs (verkeersborden) CSV."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, json_safe, wkt_geom
from ndwinfo.models import TrafficSign
from ndwinfo.parsers.csv_signs import parse_signs_csv


class TrafficSignIngester(Ingester):
    feed_name = "verkeersborden_csv"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_signs_csv(f):
                r = json_safe(dict(row))
                r["geom"] = wkt_geom(r.get("geom"))
                batch.append(r)
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, TrafficSign, batch, ["id"])
                    # Commit each batch to avoid one giant transaction on this 200M+ file
                    session.commit()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, TrafficSign, batch, ["id"])
            session.flush()

        return total
