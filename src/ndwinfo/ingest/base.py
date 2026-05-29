"""Ingester base class and shared helpers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from geoalchemy2 import WKTElement
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ndwinfo.db import SessionLocal
from ndwinfo.download import DownloadResult, fetch
from ndwinfo.feeds import FEEDS_BY_NAME
from ndwinfo.models import FeedRun

logger = logging.getLogger(__name__)
UTC = timezone.utc
BATCH_SIZE = 1000


def wkt_geom(wkt: str | None) -> WKTElement | None:
    return WKTElement(wkt, srid=4326) if wkt else None


def json_safe(obj):
    """Recursively clean values so JSONB columns serialize cleanly.

    - Decimal (from ijson) → float
    - float NaN / Inf (from pandas/shapefile) → None
    - date / datetime → ISO string
    - numpy scalar types → Python native
    """
    import decimal
    import math
    from datetime import date, datetime
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    # Handle numpy scalar types (from geopandas/pandas)
    try:
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            v = float(obj)
            return None if (math.isnan(v) or math.isinf(v)) else v
        if isinstance(obj, np.bool_):
            return bool(obj)
    except ImportError:
        pass
    return obj


def bulk_upsert(
    session: Session,
    orm_class,
    rows: list[dict],
    conflict_cols: list[str],
) -> int:
    if not rows:
        return 0
    # Stamp each row with a consistent Python-side timestamp so callers can
    # prune stale rows by comparing ingested_at to their run_start.
    now = datetime.now(UTC)
    for row in rows:
        row["ingested_at"] = now
    table = orm_class.__table__
    pk_col_names = {col.name for col in table.primary_key.columns}
    update_cols = [col.name for col in table.columns if col.name not in pk_col_names]
    stmt = pg_insert(table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=conflict_cols,
        set_={col: stmt.excluded[col] for col in update_cols},
    )
    session.execute(stmt)
    return len(rows)


class Ingester(ABC):
    feed_name: str

    def run(self) -> None:
        with SessionLocal() as session:
            last = session.execute(
                select(FeedRun)
                .where(FeedRun.feed == self.feed_name)
                .where(FeedRun.status.in_(["ok", "not_modified"]))
                .order_by(desc(FeedRun.id))
                .limit(1)
            ).scalar_one_or_none()

            etag = last.etag if last else None
            lm = last.last_modified if last else None

            feed = FEEDS_BY_NAME[self.feed_name]
            started_at = datetime.now(UTC)
            result = fetch(feed, etag=etag, last_modified=lm)

            if result.status == "not_modified":
                session.add(FeedRun(
                    feed=self.feed_name,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    status="not_modified",
                    http_status=304,
                    etag=etag,
                    last_modified=lm,
                    rows_upserted=0,
                ))
                session.commit()
                logger.info("%s: not modified (304)", self.feed_name)
                return

            if result.status == "error":
                session.add(FeedRun(
                    feed=self.feed_name,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    status="error",
                    http_status=result.http_status,
                    rows_upserted=0,
                    error=result.error,
                ))
                session.commit()
                logger.error("%s: download error: %s", self.feed_name, result.error)
                return

            try:
                n = self._ingest(result, session)
                session.add(FeedRun(
                    feed=self.feed_name,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    status="ok",
                    http_status=result.http_status,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    rows_upserted=n,
                ))
                session.commit()
                logger.info("%s: upserted %d rows", self.feed_name, n)
            except Exception as exc:
                session.rollback()
                session.add(FeedRun(
                    feed=self.feed_name,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    status="error",
                    http_status=result.http_status,
                    rows_upserted=0,
                    error=str(exc),
                ))
                session.commit()
                logger.exception("%s: ingest failed", self.feed_name)

    @abstractmethod
    def _ingest(self, result: DownloadResult, session: Session) -> int: ...
