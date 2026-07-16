"""Ingesters: meetlocaties shapefile and MSI geometry shapefile."""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ndwinfo.download import DownloadResult
from ndwinfo.ingest.base import BATCH_SIZE, Ingester, bulk_upsert, json_safe, wkt_geom
from ndwinfo.ingest.traveltime_geometry import rebuild_traveltime_geometry
from ndwinfo.matching.live_object_job import rebuild_live_object_bindings
from ndwinfo.models import (
    MeetlocatiePunt,
    MeetlocatieVak,
    MsiSign,
    OsmImportRun,
    VildArea,
    VildLine,
    VildPoint,
    VildTmc,
    WeggegLane,
    WeggegRoadAttribute,
)
from ndwinfo.parsers.shapefile_ref import (
    parse_meetlocaties,
    parse_msi_shapefile,
    parse_vild,
    parse_vild_tmc,
)
from ndwinfo.parsers.weggeg import parse_weggeg_lanes, parse_weggeg_road_attributes


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
                "bearing": row.get("bearing"),
                "raw": json_safe(row.get("raw")),
            })
            if len(batch) >= BATCH_SIZE:
                for r in batch:
                    session.execute(
                        update(MsiSign)
                        .where(MsiSign.uuid == r["uuid"])
                        .values(geom=r["geom"], bearing=r["bearing"], raw=r["raw"])
                    )
                total += len(batch)
                session.flush()
                batch.clear()

        if batch:
            for r in batch:
                session.execute(
                    update(MsiSign)
                    .where(MsiSign.uuid == r["uuid"])
                    .values(geom=r["geom"], bearing=r["bearing"], raw=r["raw"])
                )
            total += len(batch)
            session.flush()

        graph_id = session.scalar(
            select(OsmImportRun.id).where(OsmImportRun.is_active.is_(True)).limit(1)
        )
        if graph_id is not None:
            rebuild_live_object_bindings(session, graph_id, kinds=("msi",))

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

        # TMC location table (chain topology for road-following travel-time lines)
        tmc_batch: list[dict] = []
        for row in parse_vild_tmc(result.path):
            tmc_batch.append(row)
            if len(tmc_batch) >= BATCH_SIZE:
                total += bulk_upsert(session, VildTmc, tmc_batch, ["loc_nr"])
                session.flush()
                tmc_batch.clear()
        if tmc_batch:
            total += bulk_upsert(session, VildTmc, tmc_batch, ["loc_nr"])
            session.flush()

        # VILD just refreshed → rebuild road-following travel-time geometry.
        rebuild_traveltime_geometry(session)

        # TMC/VILD topology is also the fail-closed direction fallback for
        # measurement sites without OpenLR. Refresh bindings only as this
        # infrequent static source changes; live speed updates stay cheap.
        graph_id = session.scalar(
            select(OsmImportRun.id).where(OsmImportRun.is_active.is_(True)).limit(1)
        )
        if graph_id is not None:
            from ndwinfo.matching.source_binding import rebuild_measurement_bindings

            rebuild_measurement_bindings(session, graph_id)

        return total


class WeggegLaneIngester(Ingester):
    """Replace the static WEGGEG lane snapshot with the newest monthly release."""

    feed_name = "weggeg_rijstroken"

    def _ingest(self, result: DownloadResult, session: Session) -> int:
        # Each release is a complete national snapshot. This delete is part of
        # the transaction, so a failed parse rolls back to the prior snapshot.
        session.execute(delete(WeggegLane))
        session.execute(delete(WeggegRoadAttribute))
        total = 0
        batch: list[dict] = []
        for row in parse_weggeg_lanes(result.path):
            batch.append({
                **row,
                "geom": wkt_geom(row["geom"]),
                "raw": json_safe(row["raw"]),
            })
            if len(batch) >= BATCH_SIZE:
                total += bulk_upsert(session, WeggegLane, batch, ["id"])
                session.flush()
                batch.clear()
        if batch:
            total += bulk_upsert(session, WeggegLane, batch, ["id"])
            session.flush()
        attribute_batch: list[dict] = []
        for row in parse_weggeg_road_attributes(result.path):
            attribute_batch.append({
                **row,
                "geom": wkt_geom(row["geom"]),
                "raw": json_safe(row["raw"]),
            })
            if len(attribute_batch) >= BATCH_SIZE:
                total += bulk_upsert(
                    session, WeggegRoadAttribute, attribute_batch, ["id"]
                )
                session.flush()
                attribute_batch.clear()
        if attribute_batch:
            total += bulk_upsert(
                session, WeggegRoadAttribute, attribute_batch, ["id"]
            )
            session.flush()
        return total
