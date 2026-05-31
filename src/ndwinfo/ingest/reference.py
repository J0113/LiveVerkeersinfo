"""Ingesters: meetlocaties shapefile and MSI geometry shapefile."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, json_safe, wkt_geom
from ndwinfo.models import MeetlocatiePunt, MeetlocatieVak, MsiSign, VildArea, VildLine, VildPoint
from ndwinfo.parsers.shapefile_ref import parse_meetlocaties, parse_msi_shapefile, parse_vild


class MeetlocatiesIngester(Ingester):
    feed_name = "meetlocaties_shapefile"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        punt_batch: list[dict] = []
        vak_batch: list[dict] = []

        for kind, row in parse_meetlocaties(result.path):
            r = dict(row)
            r["geom"] = wkt_geom(r.get("geom"))
            r["raw"] = json_safe(r.get("raw"))
            if kind == "punt":
                punt_batch.append(r)
                if len(punt_batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, MeetlocatiePunt, punt_batch, ["id"])
                    session.flush()
                    punt_batch.clear()
            else:
                vak_batch.append(r)
                if len(vak_batch) >= BATCH_SIZE:
                    total += bulk_upsert(session, MeetlocatieVak, vak_batch, ["id"])
                    session.flush()
                    vak_batch.clear()

        if punt_batch:
            total += bulk_upsert(session, MeetlocatiePunt, punt_batch, ["id"])
            session.flush()
        if vak_batch:
            total += bulk_upsert(session, MeetlocatieVak, vak_batch, ["id"])
            session.flush()

        return total


class MsiShapefileIngester(Ingester):
    feed_name = "msi_shapefiles"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batch: list[dict] = []

        for row in parse_msi_shapefile(result.path):
            if not row.get("uuid"):
                continue
            batch.append({
                "uuid": row["uuid"],
                "geom": wkt_geom(row.get("geom")),
                "raw": json_safe(row.get("raw")),
            })
            if len(batch) >= BATCH_SIZE:
                for r in batch:
                    session.execute(
                        update(MsiSign)
                        .where(MsiSign.uuid == r["uuid"])
                        .values(geom=r["geom"], raw=r["raw"])
                    )
                total += len(batch)
                session.flush()
                batch.clear()

        if batch:
            for r in batch:
                session.execute(
                    update(MsiSign)
                    .where(MsiSign.uuid == r["uuid"])
                    .values(geom=r["geom"], raw=r["raw"])
                )
            total += len(batch)
            session.flush()

        return total


class VildIngester(Ingester):
    feed_name = "vild_shapefile"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        total = 0
        batches: dict[str, list[dict]] = {"point": [], "line": [], "area": []}
        models = {"point": VildPoint, "line": VildLine, "area": VildArea}

        for kind, row in parse_vild(result.path):
            r = dict(row)
            r["geom"] = wkt_geom(r.get("geom"))
            r["raw"] = json_safe(r.get("raw"))
            batches[kind].append(r)
            if len(batches[kind]) >= BATCH_SIZE:
                total += bulk_upsert(session, models[kind], batches[kind], ["id"])
                session.flush()
                batches[kind].clear()

        for kind, batch in batches.items():
            if batch:
                total += bulk_upsert(session, models[kind], batch, ["id"])
                session.flush()

        return total
