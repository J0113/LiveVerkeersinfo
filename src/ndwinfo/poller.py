"""Poller: iterate feed registry, run ingesters on cadence."""

from __future__ import annotations

import argparse
import logging
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from ndwinfo.config import settings
from ndwinfo.db import SessionLocal
from ndwinfo.feeds import FEEDS, FEEDS_BY_NAME, FeedDef
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


def _seconds_since_api_activity(session) -> float:
    """Return API idle age; a never-observed install is fully idle.

    Treating an untouched install as idle lets reference data bootstrap without
    making it part of the first user request. Work is still bounded by the bulk
    concurrency limit.
    """
    state = session.get(SystemState, 1)
    if state is None or state.last_api_request_at is None:
        return math.inf
    last_request = state.last_api_request_at
    if last_request.tzinfo is None:
        last_request = last_request.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - last_request).total_seconds())


def _api_idle(session) -> bool:
    """Backward-compatible helper used by diagnostics and older integrations."""
    return _seconds_since_api_activity(session) >= settings.poller_idle_timeout_s


_executor = ThreadPoolExecutor(max_workers=settings.poller_max_workers)
_inflight: dict[str, Future] = {}
_last_maintenance_at = 0.0
_last_schedule_mode: str | None = None


def _schedule_class(feed: FeedDef) -> str:
    # Unknown/new feeds default to background so adding a large feed cannot
    # accidentally put it on the latency-sensitive path.
    return feed.get("schedule_class", "background")


def _select_due_feeds(
    due_feeds: list[FeedDef],
    *,
    active_names: set[str],
    idle_for_s: float,
    max_workers: int,
    bulk_max_inflight: int,
    idle_timeout_s: int,
    maintenance_idle_s: int,
) -> list[FeedDef]:
    """Select a bounded, priority-ordered batch without queuing behind workers.

    Realtime work may run in every mode. Background and maintenance work only
    starts while idle, with one shared bulk quota by default. During idle a bulk
    slot is reserved so reference refreshes do not starve, while the remaining
    workers stay available to realtime feeds.
    """
    available = max(0, max_workers - len(active_names))
    if available == 0:
        return []

    feed_lookup = {feed["name"]: feed for feed in due_feeds}
    feed_lookup.update(FEEDS_BY_NAME)
    active_bulk = sum(
        name not in feed_lookup or _schedule_class(feed_lookup[name]) != "realtime"
        for name in active_names
    )
    bulk_capacity = max(0, bulk_max_inflight - active_bulk)

    candidates = [feed for feed in due_feeds if feed["name"] not in active_names]
    candidates.sort(key=lambda feed: feed.get("priority", 100))
    realtime = [feed for feed in candidates if _schedule_class(feed) == "realtime"]

    bulk: list[FeedDef] = []
    if idle_for_s >= idle_timeout_s:
        for feed in candidates:
            schedule_class = _schedule_class(feed)
            if schedule_class == "background":
                bulk.append(feed)
            elif (
                schedule_class == "maintenance"
                and idle_for_s >= max(idle_timeout_s, maintenance_idle_s)
            ):
                bulk.append(feed)

    # Reserve only as many slots as may actually be used. This protects live
    # capacity from bulk work without starving periodic reference refreshes.
    # Even with a one-worker configuration, a due live feed must win over bulk.
    # With more capacity, at least one free slot stays available to realtime.
    bulk_reservable_slots = available if not realtime else max(0, available - 1)
    reserved_bulk = min(bulk_reservable_slots, bulk_capacity, len(bulk))
    selected_realtime = realtime[: available - reserved_bulk]
    remaining = available - len(selected_realtime)
    selected_bulk = bulk[: min(reserved_bulk, remaining)]
    return selected_realtime + selected_bulk


def _prune_completed_inflight() -> None:
    for name, future in list(_inflight.items()):
        if future.done():
            del _inflight[name]


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


def run_once(wait: bool = False) -> None:
    global _last_maintenance_at, _last_schedule_mode
    with SessionLocal() as session:
        monotonic_now = time.monotonic()
        if monotonic_now - _last_maintenance_at >= MAINTENANCE_INTERVAL_S:
            pruned = _prune_feed_runs(session)
            _last_maintenance_at = monotonic_now
            if pruned:
                logger.info("feed_run maintenance: pruned %d old rows", pruned)
        last = _last_finished_per_feed(session)
        idle_for_s = _seconds_since_api_activity(session)

    if math.isinf(idle_for_s):
        schedule_mode = "unobserved/bootstrap"
    elif idle_for_s >= max(
        settings.poller_idle_timeout_s, settings.poller_maintenance_idle_s
    ):
        schedule_mode = "maintenance-idle"
    elif idle_for_s >= settings.poller_idle_timeout_s:
        schedule_mode = "background-idle"
    else:
        schedule_mode = "active/realtime"
    if schedule_mode != _last_schedule_mode:
        logger.info("poller scheduling mode: %s", schedule_mode)
        _last_schedule_mode = schedule_mode

    now = datetime.now(UTC)
    _prune_completed_inflight()

    disabled = {s.strip() for s in settings.disabled_feeds.split(",") if s.strip()}
    due_feeds: list[FeedDef] = []
    for feed in FEEDS:
        name = feed["name"]
        if name in disabled:
            logger.debug("%s: disabled via DISABLED_FEEDS, skipping", name)
            continue
        if name not in INGESTERS:
            logger.debug("%s: no ingester registered, skipping", name)
            continue

        if name in _inflight:
            logger.debug("%s: still running from a previous pass, skipping", name)
            continue

        lf = last.get(name)
        elapsed = (now - lf).total_seconds() if lf else float("inf")

        if elapsed >= feed["cadence_s"]:
            due_feeds.append(feed)
        else:
            logger.debug(
                "%s: not due yet (%.0fs remaining)",
                name,
                feed["cadence_s"] - elapsed,
            )

    selected = _select_due_feeds(
        due_feeds,
        active_names=set(_inflight),
        idle_for_s=idle_for_s,
        max_workers=settings.poller_max_workers,
        bulk_max_inflight=settings.poller_bulk_max_inflight,
        idle_timeout_s=settings.poller_idle_timeout_s,
        maintenance_idle_s=settings.poller_maintenance_idle_s,
    )
    scheduled_names: set[str] = set()

    def submit_batch(batch: list[FeedDef]) -> None:
        for feed in batch:
            name = feed["name"]
            logger.info(
                "%s: starting due %s feed (priority=%d)",
                name,
                _schedule_class(feed),
                feed.get("priority", 100),
            )
            _inflight[name] = _executor.submit(_run_feed, name)
            scheduled_names.add(name)

    submit_batch(selected)

    if wait:
        # --once historically drains every due feed. Drain every feed eligible
        # for the current activity mode in deterministic waves, without queuing
        # all heavy ingesters behind the executor at once.
        while _inflight:
            for future in list(_inflight.values()):
                future.result()
            _prune_completed_inflight()
            remaining = [
                feed for feed in due_feeds if feed["name"] not in scheduled_names
            ]
            selected = _select_due_feeds(
                remaining,
                active_names=set(_inflight),
                idle_for_s=idle_for_s,
                max_workers=settings.poller_max_workers,
                bulk_max_inflight=settings.poller_bulk_max_inflight,
                idle_timeout_s=settings.poller_idle_timeout_s,
                maintenance_idle_s=settings.poller_maintenance_idle_s,
            )
            if not selected:
                break
            submit_batch(selected)

    deferred = len(due_feeds) - len(scheduled_names)
    if deferred:
        logger.debug(
            "%d due feeds deferred by activity, priority or concurrency limits",
            deferred,
        )


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
