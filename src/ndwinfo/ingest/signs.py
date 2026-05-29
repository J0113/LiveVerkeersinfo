"""Ingesters: matrix signs (MSI) and DRIPs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.models import Drip, MsiSign, MsiState
from ndwinfo.parsers.datex_v3 import parse_drip
from ndwinfo.parsers.ndw_vms import parse_matrix_signs

UTC = timezone.utc


class MatrixSignIngester(Ingester):
    feed_name = "matrix_signs"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        run_start = datetime.now(UTC)
        total = 0
        sign_batch: list[dict] = []
        state_batch: list[dict] = []
        saw_any = False

        with open_feed(result.path) as f:
            for sign_dict, state_dict in parse_matrix_signs(f):
                s = dict(sign_dict)
                s["geom"] = wkt_geom(s.get("geom"))
                sign_batch.append(s)
                saw_any = True

                if state_dict is not None:
                    state_batch.append(dict(state_dict))

                if len(sign_batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, MsiSign, sign_batch, ["uuid"])
                    if state_batch:
                        bulk_upsert(session, MsiState, state_batch, ["uuid"])
                    session.flush()
                    sign_batch.clear()
                    state_batch.clear()

        if sign_batch:
            total += bulk_upsert(session, MsiSign, sign_batch, ["uuid"])
            if state_batch:
                bulk_upsert(session, MsiState, state_batch, ["uuid"])
            session.flush()

        # Prune states not refreshed this run (stale signs)
        if saw_any:
            session.execute(
                delete(MsiState).where(MsiState.ingested_at < run_start)
            )
        else:
            session.execute(delete(MsiState))
        session.flush()
        return total


class DripIngester(Ingester):
    feed_name = "drips"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        with open_feed(result.path) as f:
            for row in parse_drip(f):
                r = dict(row)
                r["geom"] = wkt_geom(r.get("geom"))
                batch.append(r)
                if len(batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, Drip, batch, ["controller_id", "vms_index"])
                    session.flush()
                    batch.clear()

        if batch:
            total += bulk_upsert(session, Drip, batch, ["controller_id", "vms_index"])
            session.flush()

        return total
