"""Ingester for Flitspalen.nl static (fixed) speed cameras."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.ingest.flitspalen_route import build_pair_routes
from ndwinfo.models import FlitspalenCamera, FlitspalenCameraRoute
from ndwinfo.parsers.flitspalen import parse_flitspalen

logger = logging.getLogger(__name__)
UTC = timezone.utc

# A fetch returning under this fraction of the previous run's row count reads
# as a truncated/partial response, not a real mass camera removal in a week.
_MIN_ROW_FRACTION = 0.5


class FlitspalenCameraIngester(Ingester):
    feed_name = "flitspalen_cameras"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        run_start = datetime.now(UTC)
        with open_feed(result.path) as f:
            payload = json.load(f)

        prior_count = session.execute(
            select(func.count()).select_from(FlitspalenCamera)
        ).scalar_one()

        total = 0
        batch: dict[int, dict] = {}  # id -> row; last-one-wins dedup within a batch
        # Kept alongside `batch` (pre-WKTElement) so build_pair_routes can shapely.wkt.loads
        # the geometry after the DB-bound rows have their geom swapped to a WKTElement.
        cameras_by_id: dict[int, dict] = {}

        def flush() -> None:
            nonlocal total
            if not batch:
                return
            total += bulk_upsert(session, FlitspalenCamera, list(batch.values()), ["id"])
            session.flush()
            batch.clear()

        for row in parse_flitspalen(payload):
            cameras_by_id[row["id"]] = {
                "id": row["id"],
                "camera_type": row.get("camera_type"),
                "street": row.get("street"),
                "geom": row.get("geom"),
            }
            row["geom"] = wkt_geom(row.get("geom"))
            batch[row["id"]] = row
            if len(batch) >= BATCH_SIZE:
                flush()
        flush()

        if prior_count > 0 and total < prior_count * _MIN_ROW_FRACTION:
            raise ValueError(
                f"suspiciously small active-NL-camera count this run ({total} vs "
                f"{prior_count} previously) — treating as a truncated fetch, keeping old rows"
            )

        session.execute(delete(FlitspalenCamera).where(FlitspalenCamera.ingested_at < run_start))
        session.flush()

        routes = build_pair_routes(session, list(cameras_by_id.values()))
        for route in routes:
            route["geom"] = wkt_geom(route["geom"])
        route_run_start = datetime.now(UTC)
        if routes:
            bulk_upsert(session, FlitspalenCameraRoute, routes, ["sc_id"])
        session.execute(
            delete(FlitspalenCameraRoute).where(FlitspalenCameraRoute.ingested_at < route_run_start)
        )
        session.flush()

        return total
