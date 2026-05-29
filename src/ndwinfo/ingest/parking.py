"""Ingesters: truck parking table and live status."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.models import TruckParking, TruckParkingStatus
from ndwinfo.parsers.datex_v2 import parse_truckparking_table
from ndwinfo.parsers.datex_v3 import parse_parking_status


class TruckParkingTableIngester(Ingester):
    feed_name = "truckparking_table"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_truckparking_table(f):
                r = dict(row)
                r["geom"] = wkt_geom(r.get("geom"))
                batch.append(r)
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, TruckParking, batch, ["id"])
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, TruckParking, batch, ["id"])
            session.flush()

        return total


class TruckParkingStatusIngester(Ingester):
    feed_name = "truckparking_status"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_parking_status(f):
                batch.append(dict(row))
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, TruckParkingStatus, batch, ["parking_id"])
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, TruckParkingStatus, batch, ["parking_id"])
            session.flush()

        return total
