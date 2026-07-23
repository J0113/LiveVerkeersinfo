"""Ingesters: measurement sites, traffic speed/flow, travel time."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.ingest.traveltime_geometry import rebuild_traveltime_geometry
from ndwinfo.ingest.vild_direction import rebuild_speed_site_directions, resolve_effective_road
from ndwinfo.models import (
    MeasurementCharacteristic,
    MeasurementSite,
    TrafficMeasurement,
    TravelTime,
)
from ndwinfo.parsers.datex_v2 import (
    parse_measurement_site_table,
    parse_trafficspeed,
    parse_traveltime,
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

        # Sites refreshed → recompute road-following travel-time geometry from
        # the VILD TMC chain (no-op until the VILD table is present).
        rebuild_traveltime_geometry(session)
        rebuild_speed_site_directions(session)
        resolve_effective_road(session)

        return total


_TRAFFIC_STAGE_SQL = """
    CREATE TEMP TABLE traffic_measurement_stage (
        site_id text,
        "index" integer,
        measured_at timestamptz,
        value_type text,
        flow_veh_h numeric,
        speed_kmh numeric,
        n_inputs integer,
        std_dev numeric
    ) ON COMMIT DROP
"""

_TRAFFIC_COPY_SQL = (
    'COPY traffic_measurement_stage'
    ' (site_id, "index", measured_at, value_type, flow_veh_h, speed_kmh, n_inputs, std_dev)'
    " FROM STDIN"
)

# raw is dropped (set NULL): it would just duplicate these same typed columns
# into JSONB, adding wire/JSONB/WAL overhead with no reader depending on it.
# DISTINCT ON + `ctid DESC` guards against the same (site_id, index) appearing
# twice within one snapshot (parse_trafficspeed doesn't produce this today,
# verified against the real feed, but NDW's publish behavior isn't a contract
# we control); IS DISTINCT FROM skips writing/WAL-logging unchanged rows.
_TRAFFIC_MERGE_SQL = """
    INSERT INTO traffic_measurement
        (site_id, "index", measured_at, value_type, flow_veh_h, speed_kmh, n_inputs, std_dev, raw, ingested_at)
    SELECT DISTINCT ON (site_id, "index")
        site_id, "index", measured_at, value_type, flow_veh_h, speed_kmh, n_inputs, std_dev, NULL, now()
    FROM traffic_measurement_stage
    ORDER BY site_id, "index", ctid DESC
    ON CONFLICT (site_id, "index") DO UPDATE SET
        measured_at = EXCLUDED.measured_at,
        value_type = EXCLUDED.value_type,
        flow_veh_h = EXCLUDED.flow_veh_h,
        speed_kmh = EXCLUDED.speed_kmh,
        n_inputs = EXCLUDED.n_inputs,
        std_dev = EXCLUDED.std_dev,
        raw = NULL,
        ingested_at = EXCLUDED.ingested_at
    WHERE (traffic_measurement.measured_at, traffic_measurement.value_type,
           traffic_measurement.flow_veh_h, traffic_measurement.speed_kmh,
           traffic_measurement.n_inputs, traffic_measurement.std_dev)
      IS DISTINCT FROM
          (EXCLUDED.measured_at, EXCLUDED.value_type, EXCLUDED.flow_veh_h,
           EXCLUDED.speed_kmh, EXCLUDED.n_inputs, EXCLUDED.std_dev)
      OR traffic_measurement.raw IS NOT NULL
"""


class TrafficspeedIngester(Ingester):
    feed_name = "trafficspeed"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        cursor = self._copy_cursor(session)
        if cursor is None:
            return self._ingest_via_orm(result, session)

        total = 0
        cursor.execute(_TRAFFIC_STAGE_SQL)
        with cursor.copy(_TRAFFIC_COPY_SQL) as copy:
            with open_feed(result.path) as f:
                for row in parse_trafficspeed(f):
                    copy.write_row((
                        row["site_id"],
                        row["index"],
                        row["measured_at"],
                        row["value_type"],
                        row["flow_veh_h"],
                        row["speed_kmh"],
                        row["n_inputs"],
                        row["std_dev"],
                    ))
                    total += 1
        cursor.execute(_TRAFFIC_MERGE_SQL)
        cursor.close()
        return total

    @staticmethod
    def _copy_cursor(session: Session):
        """Return a psycopg cursor for the COPY path, or None to fall back to ORM upsert."""
        if session.get_bind().dialect.name != "postgresql":
            return None
        cursor = session.connection().connection.cursor()
        return cursor if hasattr(cursor, "copy") else None

    def _ingest_via_orm(self, result: DownloadResult, session: Session) -> int:
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
