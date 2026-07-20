"""Poller: iterate feed registry, run ingesters on cadence."""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from ndwinfo.config import settings
from ndwinfo.db import SessionLocal
from ndwinfo.feeds import FEEDS, FEEDS_BY_NAME
from ndwinfo.ingest import INGESTERS
from ndwinfo.models import FeedRun, SystemState

TICK_S = 10
MAINTENANCE_INTERVAL_S = 3600
FEED_RUNS_PER_FEED = 500
UTC = timezone.utc

logger = logging.getLogger(__name__)


def _last_finished_per_feed(session) -> dict[str, datetime]:
    rows = session.execute(
        select(FeedRun.feed, func.max(FeedRun.finished_at)).group_by(FeedRun.feed)
    ).all()
    return {feed: ts for feed, ts in rows if ts}


def _idle_for(session) -> float:
    """Seconds since the last API request, or +inf if there's no record yet."""
    state = session.get(SystemState, 1)
    if state is None or state.last_api_request_at is None:
        return float("inf")  # no record yet → fresh install, treat as idle
    return (datetime.now(UTC) - state.last_api_request_at).total_seconds()


_executor = ThreadPoolExecutor(max_workers=settings.poller_max_workers)
_inflight: dict[str, Future] = {}
_last_maintenance_at = 0.0


def _prune_feed_runs(session) -> int:
    """Bound operational history while retaining ample per-feed diagnostics."""
    result = session.execute(
        text(
            """
            DELETE FROM feed_run
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           row_number() OVER (PARTITION BY feed ORDER BY id DESC) AS rn
                    FROM feed_run
                ) ranked
                WHERE rn > :keep
            )
            """
        ),
        {"keep": FEED_RUNS_PER_FEED},
    )
    session.commit()
    return result.rowcount or 0


def _run_feed(name: str) -> None:
    try:
        INGESTERS[name].run()
    except Exception:
        logger.exception("%s: unexpected error in poller", name)


def _bulk_inflight_count() -> int:
    return sum(
        1
        for name, fut in _inflight.items()
        if not fut.done()
        and FEEDS_BY_NAME.get(name, {}).get("schedule_class", "background") != "realtime"
    )


def run_once(wait: bool = False) -> None:
    global _last_maintenance_at
    with SessionLocal() as session:
        monotonic_now = time.monotonic()
        if monotonic_now - _last_maintenance_at >= MAINTENANCE_INTERVAL_S:
            pruned = _prune_feed_runs(session)
            _last_maintenance_at = monotonic_now
            if pruned:
                logger.info("feed_run maintenance: pruned %d old rows", pruned)
        last = _last_finished_per_feed(session)
        idle_s = _idle_for(session)

    now = datetime.now(UTC)
    disabled = {s.strip() for s in settings.disabled_feeds.split(",") if s.strip()}

    # realtime feeds always run when due; background/maintenance feeds only
    # start once the API has been idle long enough, and share a capped
    # concurrency budget so a burst of due bulk feeds can never fill every
    # worker slot and starve a realtime feed that becomes due moments later.
    bulk_budget = max(0, settings.poller_bulk_max_inflight - _bulk_inflight_count())

    for feed in FEEDS:
        name = feed["name"]
        if name in disabled:
            logger.debug("%s: disabled via DISABLED_FEEDS, skipping", name)
            continue
        if name not in INGESTERS:
            logger.debug("%s: no ingester registered, skipping", name)
            continue

        # A slow feed (e.g. a 30-minute shapefile/CSV ingest) may still be
        # running from a previous tick — don't resubmit it, and don't let
        # it block other feeds from being checked/started this tick.
        prev = _inflight.get(name)
        if prev is not None and not prev.done():
            logger.debug("%s: still running from a previous pass, skipping", name)
            continue

        lf = last.get(name)
        elapsed = (now - lf).total_seconds() if lf else float("inf")
        if elapsed < feed["cadence_s"]:
            logger.debug("%s: not due yet (%.0fs remaining)", name, feed["cadence_s"] - elapsed)
            continue

        schedule_class = feed.get("schedule_class", "background")
        if schedule_class != "realtime":
            idle_threshold = (
                settings.poller_maintenance_idle_s
                if schedule_class == "maintenance"
                else settings.poller_idle_timeout_s
            )
            if idle_s < idle_threshold:
                logger.debug(
                    "%s: %s feed waiting for idle (%.0fs < %ds)",
                    name, schedule_class, idle_s, idle_threshold,
                )
                continue
            if bulk_budget <= 0:
                logger.debug("%s: bulk concurrency budget exhausted, deferring", name)
                continue
            bulk_budget -= 1

        logger.info(
            "%s: due (elapsed %.0fs >= cadence %ds, class=%s), running",
            name, elapsed, feed["cadence_s"], schedule_class,
        )
        _inflight[name] = _executor.submit(_run_feed, name)

    if wait:
        for future in list(_inflight.values()):
            future.result()


def main() -> None:
    parser = argparse.ArgumentParser(description="NDW feed poller")
    parser.add_argument("--once", action="store_true", help="Run one pass then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if args.once:
        run_once(wait=True)
        return

    logger.info("Poller started (tick=%ds)", TICK_S)
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Unexpected error in poll loop")
        time.sleep(TICK_S)


if __name__ == "__main__":
    main()
