"""Feed status endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from ndwinfo.api.deps import DbDep
from ndwinfo.models import FeedRun

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("")
def get_feed_status(db: DbDep):
    # Latest run per feed (by MAX id — runs are sequential)
    subq = (
        select(func.max(FeedRun.id).label("max_id"))
        .group_by(FeedRun.feed)
        .subquery()
    )
    rows = db.execute(
        select(FeedRun).join(subq, FeedRun.id == subq.c.max_id).order_by(FeedRun.feed)
    ).scalars().all()

    return {
        "feeds": [
            {
                "feed": r.feed,
                "status": r.status,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "rows_upserted": r.rows_upserted,
                "http_status": r.http_status,
                "error": r.error,
            }
            for r in rows
        ]
    }
