"""Ingester for ANWB incidents (jams / roadworks / dynamic radars)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.models import AnwbIncident
from ndwinfo.parsers.anwb import parse_anwb_incidents

logger = logging.getLogger(__name__)
UTC = timezone.utc
CATEGORIES = ("jams", "roadworks", "radars")


class AnwbIncidentIngester(Ingester):
    feed_name = "anwb_incidents"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        run_start = datetime.now(UTC)
        with open_feed(result.path) as f:
            payload = json.load(f)

        # Per-category counts before this run touches anything — lets us tell
        # a payload that dropped a whole category apart from a real "no
        # incidents of this type right now" snapshot.
        prior_counts = dict(
            session.execute(
                select(AnwbIncident.category, func.count()).group_by(AnwbIncident.category)
            ).all()
        )

        total = 0
        batch: dict[str, dict] = {}  # record_id -> row; last-one-wins dedup within a batch
        seen_counts: dict[str, int] = {c: 0 for c in CATEGORIES}

        def flush() -> None:
            nonlocal total
            if not batch:
                return
            total += bulk_upsert(session, AnwbIncident, list(batch.values()), ["record_id"])
            session.flush()
            batch.clear()

        for row in parse_anwb_incidents(payload):
            row["geom"] = wkt_geom(row.get("geom"))
            batch[row["record_id"]] = row
            seen_counts[row["category"]] = seen_counts.get(row["category"], 0) + 1
            if len(batch) >= BATCH_SIZE:
                flush()
        flush()

        for category in CATEGORIES:
            if seen_counts[category] == 0 and prior_counts.get(category, 0) > 0:
                logger.warning(
                    "%s: category %r absent from this payload but had %d rows previously — "
                    "skipping prune for this category (suspect partial payload)",
                    self.feed_name, category, prior_counts[category],
                )
                continue
            session.execute(
                delete(AnwbIncident).where(
                    AnwbIncident.category == category,
                    AnwbIncident.ingested_at < run_start,
                )
            )

        session.flush()
        return total
