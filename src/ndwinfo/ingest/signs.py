"""Ingesters: matrix signs (MSI) and DRIPs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult, open_feed
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, wkt_geom
from ndwinfo.matching.live_object_job import rebuild_live_object_bindings
from ndwinfo.models import Drip, MsiSign, MsiState, OsmImportRun
from ndwinfo.parsers.datex_v3 import parse_drip
from ndwinfo.parsers.ndw_vms import parse_matrix_signs

UTC = timezone.utc


def _upsert_signs_preserve_geom(session, rows: list[dict]) -> int:
    """Upsert msi_sign rows without overwriting non-NULL geom from shapefile."""
    if not rows:
        return 0
    now = datetime.now(UTC)
    for r in rows:
        r["ingested_at"] = now
    table = MsiSign.__table__
    stmt = pg_insert(table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["uuid"],
        set_={
            "road": stmt.excluded.road,
            "carriageway": stmt.excluded.carriageway,
            "lane": stmt.excluded.lane,
            "km": stmt.excluded.km,
            # Keep existing geom (from shapefile) if already set; NULL from parser won't clobber it
            "geom": func.coalesce(table.c.geom, stmt.excluded.geom),
            "raw": stmt.excluded.raw,
            "ingested_at": stmt.excluded.ingested_at,
        },
    )
    session.execute(stmt)
    return len(rows)


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
                    total += _upsert_signs_preserve_geom(session, sign_batch)
                    if state_batch:
                        bulk_upsert(session, MsiState, state_batch, ["uuid"])
                    session.flush()
                    sign_batch.clear()
                    state_batch.clear()

        if sign_batch:
            total += _upsert_signs_preserve_geom(session, sign_batch)
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
        run_start = datetime.now(UTC)
        total = 0
        batch: list[dict] = []
        saw_any = False

        with open_feed(result.path) as f:
            for row in parse_drip(f):
                saw_any = True
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

        # This feed is a latest-state publication. A panel omitted from the
        # current snapshot must not remain eligible for path/HUD matching.
        if saw_any:
            session.execute(delete(Drip).where(Drip.ingested_at < run_start))
        else:
            session.execute(delete(Drip))
        session.flush()

        graph_id = session.scalar(
            select(OsmImportRun.id).where(OsmImportRun.is_active.is_(True)).limit(1)
        )
        if graph_id is not None:
            rebuild_live_object_bindings(session, graph_id, kinds=("drip",))

        return total
