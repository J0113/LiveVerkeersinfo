"""Ingester: emission zones."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.models import EmissionZone
from ndwinfo.parsers.datex_v3 import parse_emission_zones


class EmissionZoneIngester(Ingester):
    feed_name = "emission_zones"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_emission_zones(f):
                r = dict(row)
                r["geom"] = wkt_geom(r.get("geom"))
                batch.append(r)
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, EmissionZone, batch, ["id"])
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, EmissionZone, batch, ["id"])
            session.flush()

        return total
