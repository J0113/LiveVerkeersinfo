"""Ingesters: EV charging points and tariffs."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, json_safe, wkt_geom
from ndwinfo.models import ChargeAvailability, ChargePoint, Tariff
from ndwinfo.parsers.geojson_ocpi import parse_charging_geojson, parse_ocpi_tariffs


class ChargingGeojsonIngester(Ingester):
    feed_name = "charging_geojson"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        cp_batch: list[dict] = []
        avail_rows: list[dict] = []

        def flush_batch() -> None:
            nonlocal total
            if not cp_batch:
                return
            cp_ids = [row["id"] for row in cp_batch]
            total += bulk_upsert(session, ChargePoint, cp_batch, ["id"])
            # Availability replacement remains atomic because the outer session
            # commits only after the complete feed succeeds. Flushing here keeps
            # memory bounded to one batch instead of retaining the nationwide
            # availability list (hundreds of thousands of dict values).
            session.execute(
                delete(ChargeAvailability).where(ChargeAvailability.cp_id.in_(cp_ids))
            )
            if avail_rows:
                session.execute(ChargeAvailability.__table__.insert(), avail_rows)
            session.flush()
            cp_batch.clear()
            avail_rows.clear()

        with open_feed(result.path) as f:
            for cp_dict, avail_dicts in parse_charging_geojson(f):
                cp = json_safe(dict(cp_dict))
                cp["geom"] = wkt_geom(cp.get("geom"))
                cp_batch.append(cp)
                avail_rows.extend(json_safe(a) for a in avail_dicts)

                if len(cp_batch) >= BATCH_SIZE:
                    flush_batch()

        flush_batch()

        return total


class TariffIngester(Ingester):
    feed_name = "tariffs_ocpi"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_ocpi_tariffs(f):
                batch.append(json_safe(dict(row)))
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, Tariff, batch, ["id"])
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, Tariff, batch, ["id"])
            session.flush()

        return total
