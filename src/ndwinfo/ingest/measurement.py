"""Ingesters: measurement sites, traffic speed/flow, travel time."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.models import MeasurementCharacteristic, MeasurementSite, TrafficMeasurement, TravelTime
from ndwinfo.parsers.datex_v2 import (
    parse_measurement_site_table,
    parse_traveltime,
    parse_trafficspeed,
)


class MeasurementSiteIngester(Ingester):
    feed_name = "measurement_site"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        site_batch: list[dict] = []
        char_batch: list[dict] = []

        def _flush_chars() -> None:
            for i in range(0, len(char_batch), BATCH_SIZE):
                chunk = char_batch[i : i + BATCH_SIZE]
                bulk_upsert(session, MeasurementCharacteristic, chunk, ["site_id", "index"])
            session.flush()
            char_batch.clear()

        with open_feed(result.path) as f:
            for site_dict, char_dicts in parse_measurement_site_table(f):
                row = dict(site_dict)
                row["geom"] = wkt_geom(row.get("geom"))
                row["line_geom"] = wkt_geom(row.get("line_geom"))
                site_batch.append(row)

                for c in char_dicts:
                    char_batch.append(dict(c))

                if len(site_batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, MeasurementSite, site_batch, ["id"])
                    session.flush()
                    site_batch.clear()
                    _flush_chars()

        if site_batch:
            total += bulk_upsert(session, MeasurementSite, site_batch, ["id"])
            session.flush()
            site_batch.clear()
        if char_batch:
            _flush_chars()

        return total


class TrafficspeedIngester(Ingester):
    feed_name = "trafficspeed"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_trafficspeed(f):
                batch.append(dict(row))
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, TrafficMeasurement, batch, ["site_id", "index"])
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, TrafficMeasurement, batch, ["site_id", "index"])
            session.flush()

        return total


class TraveltimeIngester(Ingester):
    feed_name = "traveltime"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_traveltime(f):
                batch.append(dict(row))
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, TravelTime, batch, ["segment_id", "index"])
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, TravelTime, batch, ["segment_id", "index"])
            session.flush()

        return total
