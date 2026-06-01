"""Poller: iterate feed registry, run ingesters on cadence."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from ndwinfo.config import settings
from ndwinfo.db import SessionLocal
from ndwinfo.feeds import FEEDS
from ndwinfo.ingest import INGESTERS
from ndwinfo.models import FeedRun, SystemState

TICK_S = 10
UTC = timezone.utc

logger = logging.getLogger(__name__)


def _last_finished_per_feed(session) -> dict[str, datetime]:
    rows = session.execute(
        select(FeedRun.feed, func.max(FeedRun.finished_at)).group_by(FeedRun.feed)
    ).all()
    return {feed: ts for feed, ts in rows if ts}


def _api_idle(session) -> bool:
    state = session.get(SystemState, 1)
    if state is None or state.last_api_request_at is None:
        return False  # no record yet → treat as active (fresh install)
    elapsed = (datetime.now(UTC) - state.last_api_request_at).total_seconds()
    return elapsed > settings.poller_idle_timeout_s


def run_once() -> None:
    with SessionLocal() as session:
        last = _last_finished_per_feed(session)
        idle = _api_idle(session)

    if idle:
        logger.info(
            "API idle for > %ds, pausing poll pass", settings.poller_idle_timeout_s
        )
        return

    now = datetime.now(UTC)

    disabled = {s.strip() for s in settings.disabled_feeds.split(",") if s.strip()}
    for feed in FEEDS:
        name = feed["name"]
        if name in disabled:
            logger.debug("%s: disabled via DISABLED_FEEDS, skipping", name)
            continue
        if name not in INGESTERS:
            logger.debug("%s: no ingester registered, skipping", name)
            continue

        lf = last.get(name)
        elapsed = (now - lf).total_seconds() if lf else float("inf")

        if elapsed >= feed["cadence_s"]:
            logger.info("%s: due (elapsed %.0fs >= cadence %ds), running", name, elapsed, feed["cadence_s"])
            try:
                INGESTERS[name].run()
            except Exception:
                logger.exception("%s: unexpected error in poller", name)
        else:
            logger.debug("%s: not due yet (%.0fs remaining)", name, feed["cadence_s"] - elapsed)


def main() -> None:
    parser = argparse.ArgumentParser(description="NDW feed poller")
    parser.add_argument("--once", action="store_true", help="Run one pass then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if args.once:
        run_once()
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
