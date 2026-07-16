"""Ingesters: measurement sites, traffic speed/flow, travel time."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.ingest.traveltime_geometry import rebuild_traveltime_geometry
from ndwinfo.models import (
    MeasurementCharacteristic,
    MeasurementSite,
    OsmImportRun,
    TrafficMeasurement,
    TravelTime,
)
from ndwinfo.parsers.datex_v2 import (
    parse_measurement_site_table,
    parse_trafficspeed,
    parse_traveltime,
)

UTC = timezone.utc

_TRAFFIC_STAGE_SQL = """
CREATE TEMP TABLE traffic_measurement_stage (
    ordinal bigint GENERATED ALWAYS AS IDENTITY,
    site_id varchar NOT NULL,
    "index" integer NOT NULL,
    measured_at text,
    value_type varchar,
    flow_veh_h numeric,
    speed_kmh numeric,
    n_inputs integer,
    std_dev numeric
) ON COMMIT DROP
"""

_TRAFFIC_COPY_SQL = """
COPY traffic_measurement_stage (
    site_id, "index", measured_at, value_type, flow_veh_h,
    speed_kmh, n_inputs, std_dev
) FROM STDIN
"""

_TRAFFIC_MERGE_SQL = """
INSERT INTO traffic_measurement (
    site_id, "index", measured_at, value_type, flow_veh_h,
    speed_kmh, n_inputs, std_dev, raw, ingested_at
)
SELECT DISTINCT ON (site_id, "index")
    site_id,
    "index",
    measured_at::timestamptz,
    value_type,
    flow_veh_h,
    speed_kmh,
    n_inputs,
    std_dev,
    jsonb_build_object(
        'site_id', site_id,
        'index', "index",
        'measured_at', measured_at,
        'value_type', value_type,
        'flow_veh_h', flow_veh_h,
        'speed_kmh', speed_kmh,
        'n_inputs', n_inputs,
        'std_dev', std_dev
    ),
    :ingested_at
FROM traffic_measurement_stage
ORDER BY site_id, "index", ordinal DESC
ON CONFLICT (site_id, "index") DO UPDATE SET
    measured_at = excluded.measured_at,
    value_type = excluded.value_type,
    flow_veh_h = excluded.flow_veh_h,
    speed_kmh = excluded.speed_kmh,
    n_inputs = excluded.n_inputs,
    std_dev = excluded.std_dev,
    raw = excluded.raw,
    ingested_at = excluded.ingested_at
WHERE (
    traffic_measurement.measured_at,
    traffic_measurement.value_type,
    traffic_measurement.flow_veh_h,
    traffic_measurement.speed_kmh,
    traffic_measurement.n_inputs,
    traffic_measurement.std_dev,
    traffic_measurement.raw
) IS DISTINCT FROM (
    excluded.measured_at,
    excluded.value_type,
    excluded.flow_veh_h,
    excluded.speed_kmh,
    excluded.n_inputs,
    excluded.std_dev,
    excluded.raw
)
"""


def _supports_postgres_copy(session: Session) -> bool:
    """Return whether this session exposes psycopg's streaming COPY API."""
    try:
        if session.get_bind().dialect.name != "postgresql":
            return False
        driver_connection = session.connection().connection.driver_connection
        return hasattr(driver_connection, "cursor")
    except (AttributeError, NotImplementedError):
        return False


def _copy_traffic_measurements(session: Session, rows: Iterable[dict]) -> int:
    """Stream one complete publication into PostgreSQL and merge it atomically.

    The staging table lives in the caller's transaction. A parse, COPY or merge
    failure therefore leaves the previous live snapshot untouched. ``raw`` is
    reconstructed by PostgreSQL from the same normalized fields as the parser;
    copying that derived JSON a second time would only double wire traffic.
    """
    connection = session.connection()
    connection.exec_driver_sql(_TRAFFIC_STAGE_SQL)
    driver_connection = connection.connection.driver_connection

    total = 0
    with driver_connection.cursor() as cursor:
        with cursor.copy(_TRAFFIC_COPY_SQL) as copy:
            for row in rows:
                copy.write_row(
                    (
                        row.get("site_id"),
                        row.get("index"),
                        row.get("measured_at"),
                        row.get("value_type"),
                        row.get("flow_veh_h"),
                        row.get("speed_kmh"),
                        row.get("n_inputs"),
                        row.get("std_dev"),
                    )
                )
                total += 1

    if total:
        session.execute(text(_TRAFFIC_MERGE_SQL), {"ingested_at": datetime.now(UTC)})
    return total


class MeasurementSiteIngester(Ingester):
    feed_name = "measurement_site"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        site_batch: list[dict] = []
        char_batch: list[dict] = []
        refreshed_site_ids: list[str] = []

        def _flush_chars() -> None:
            for i in range(0, len(char_batch), BATCH_SIZE):
                chunk = char_batch[i : i + BATCH_SIZE]
                bulk_upsert(session, MeasurementCharacteristic, chunk, ["site_id", "index"])
            session.flush()
            char_batch.clear()

        with open_feed(result.path) as f:
            for site_dict, char_dicts in parse_measurement_site_table(f):
                row = dict(site_dict)
                refreshed_site_ids.append(row["id"])
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

        # Geometry/road metadata changed, so recompute only these persisted
        # source bindings. This remains an hourly background operation rather
        # than work performed by a user-facing road request.
        active_graph_id = session.scalar(
            select(OsmImportRun.id).where(OsmImportRun.is_active.is_(True)).limit(1)
        )
        if active_graph_id is not None and refreshed_site_ids:
            from ndwinfo.matching.source_binding import rebuild_measurement_bindings

            rebuild_measurement_bindings(
                session,
                active_graph_id,
                source_ids=refreshed_site_ids,
            )

        return total


class TrafficspeedIngester(Ingester):
    feed_name = "trafficspeed"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        if _supports_postgres_copy(session):
            with open_feed(result.path) as f:
                return _copy_traffic_measurements(session, parse_trafficspeed(f))

        # Portable fallback for non-PostgreSQL development/test sessions and
        # unusual DBAPI wrappers that do not expose psycopg's COPY protocol.
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
