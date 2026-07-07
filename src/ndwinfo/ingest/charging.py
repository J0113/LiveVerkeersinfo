"""Ingesters: EV charging points and tariffs."""

from __future__ import annotations

from sqlalchemy import delete, text
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
        seen_cp_ids: list[str] = []

        with open_feed(result.path) as f:
            for cp_dict, avail_dicts in parse_charging_geojson(f):
                cp = json_safe(dict(cp_dict))
                cp["geom"] = wkt_geom(cp.get("geom"))
                cp_batch.append(cp)
                seen_cp_ids.append(cp["id"])
                avail_rows.extend(json_safe(a) for a in avail_dicts)

                if len(cp_batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, ChargePoint, cp_batch, ["id"])
                    session.flush()
                    cp_batch.clear()

        if cp_batch:
            total += bulk_upsert(session, ChargePoint, cp_batch, ["id"])
            session.flush()

        # Replace availability for all seen charge points atomically
        for i in range(0, len(seen_cp_ids), BATCH_SIZE):
            batch = seen_cp_ids[i : i + BATCH_SIZE]
            session.execute(
                delete(ChargeAvailability).where(
                    ChargeAvailability.cp_id.in_(batch)
                )
            )
        if seen_cp_ids:
            session.flush()

            for i in range(0, len(avail_rows), BATCH_SIZE):
                batch = avail_rows[i : i + BATCH_SIZE]
                if batch:
                    session.execute(ChargeAvailability.__table__.insert(), batch)
                    session.flush()

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
