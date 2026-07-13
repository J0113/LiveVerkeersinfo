"""Ingester: RWS NWB Wegvakken GeoPackage (whole-country road-section geometry)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, json_safe, wkt_geom
from ndwinfo.models import NwbRoadSegment
from ndwinfo.parsers.nwb_gpkg import parse_wegvakken

UTC = timezone.utc


class NwbWegvakkenIngester(Ingester):
    feed_name = "nwb_wegvakken"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        run_start = datetime.now(UTC)
        total = 0
        batch: list[dict] = []

        for row in parse_wegvakken(result.path):
            row["geom"] = wkt_geom(row.get("geom"))
            row["raw"] = json_safe(row.get("raw"))
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                total += bulk_upsert(session, NwbRoadSegment, batch, ["wvk_id"])
                session.flush()
                batch.clear()

        if batch:
            total += bulk_upsert(session, NwbRoadSegment, batch, ["wvk_id"])
            session.flush()

        # Prune road sections that dropped out of today's national export
        # (renumbered/decommissioned wvk_ids) — this table is a latest snapshot.
        session.execute(delete(NwbRoadSegment).where(NwbRoadSegment.ingested_at < run_start))
        session.flush()

        return total
