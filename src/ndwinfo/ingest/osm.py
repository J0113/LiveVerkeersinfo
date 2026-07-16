"""Streaming, offline OSM PBF importer for the canonical directed road graph.

The public Overpass API is deliberately absent from this module.  A local PBF
is scanned twice: once to find shared road nodes using a disk-backed SQLite
counter, then once to resolve coordinates and write directed segments in
batches.  The previous graph remains active throughout the build.  Activation
is a small final transaction on ``osm_import_run``.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from geoalchemy2 import WKTElement
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ndwinfo.config import settings
from ndwinfo.db import SessionLocal
from ndwinfo.models import OsmImportRun, OsmRoadNode, OsmRoadSegment
from ndwinfo.osm.graph import build_directed_segments
from ndwinfo.osm.tags import DEFAULT_HIGHWAY_CLASSES, is_drivable

logger = logging.getLogger(__name__)
UTC = timezone.utc


class _SharedNodeIndex:
    """Disk-backed capped reference counts; keeps national imports RAM-bounded."""

    def __init__(self, directory: str | None = None):
        handle = tempfile.NamedTemporaryFile(
            prefix="ndwinfo-osm-nodes-", suffix=".sqlite", dir=directory, delete=False
        )
        handle.close()
        self.path = Path(handle.name)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute(
            "CREATE TABLE node_ref (node_id INTEGER PRIMARY KEY, ref_count INTEGER NOT NULL)"
        )
        self._pending: list[tuple[int]] = []

    def add_way(self, node_ids: Iterable[int]) -> None:
        # Count a node once per way; a malformed way repeating a node must not
        # manufacture a graph intersection.
        self._pending.extend((int(node_id),) for node_id in set(node_ids))
        if len(self._pending) >= 50_000:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self.connection.executemany(
            """
            INSERT INTO node_ref(node_id, ref_count) VALUES (?, 1)
            ON CONFLICT(node_id) DO UPDATE
            SET ref_count = MIN(2, node_ref.ref_count + 1)
            """,
            self._pending,
        )
        self.connection.commit()
        self._pending.clear()

    def shared_for(self, node_ids: Iterable[int]) -> set[int]:
        values = tuple(dict.fromkeys(int(value) for value in node_ids))
        if not values:
            return set()
        shared: set[int] = set()
        # Stay below SQLite's traditional 999-variable limit.
        for offset in range(0, len(values), 900):
            chunk = values[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"SELECT node_id FROM node_ref WHERE ref_count >= 2 "
                f"AND node_id IN ({placeholders})",
                chunk,
            )
            shared.update(row[0] for row in rows)
        return shared

    def close(self) -> None:
        self.connection.close()
        self.path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class OsmPbfImporter:
    def __init__(
        self,
        *,
        path: Path,
        graph_version: str | None = None,
        source_timestamp: datetime | None = None,
        batch_size: int | None = None,
        highway_classes: frozenset[str] | None = None,
    ):
        self.path = path.resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"OSM PBF does not exist: {self.path}")
        self.source_sha256 = _sha256(self.path)
        self.graph_version = graph_version or f"osm-{self.source_sha256[:16]}"
        self.source_timestamp = source_timestamp
        self.batch_size = batch_size or settings.osm_import_batch_size
        configured_classes = frozenset(
            value.strip()
            for value in settings.osm_highway_classes.split(",")
            if value.strip()
        )
        self.highway_classes = highway_classes or configured_classes or DEFAULT_HIGHWAY_CLASSES

    def run(self, *, activate: bool = True) -> OsmImportRun:
        """Build an isolated graph and optionally atomically activate it.

        ``activate=False`` is the production-safe shadow path: the complete
        graph is retained with status ``ready`` while the current graph remains
        active.  This allows national coverage and binding benchmarks without
        changing any user-facing road request.
        """
        with SessionLocal() as session:
            existing = session.execute(
                select(OsmImportRun).where(OsmImportRun.graph_version == self.graph_version)
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(f"Graph version already exists: {self.graph_version}")
            run = OsmImportRun(
                graph_version=self.graph_version,
                source_path=str(self.path),
                source_sha256=self.source_sha256,
                source_timestamp=self.source_timestamp,
                status="importing",
                is_active=False,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        try:
            counts = self._build(run_id)
            return self._activate(run_id, counts) if activate else self._mark_ready(
                run_id, counts
            )
        except Exception as exc:
            self._mark_failed(run_id, str(exc))
            raise

    def _build(self, run_id: int) -> dict[str, int]:
        osmium = _require_osmium()
        importer = self

        with _SharedNodeIndex(settings.osm_import_temp_dir or None) as shared_index:
            class ReferenceCounter(osmium.SimpleHandler):
                def way(self, way):
                    tags = {tag.k: tag.v for tag in way.tags}
                    if is_drivable(tags, importer.highway_classes):
                        shared_index.add_way(node.ref for node in way.nodes)

            logger.info("OSM %s: pass 1/2, indexing shared nodes", self.graph_version)
            ReferenceCounter().apply_file(str(self.path), locations=False)
            shared_index.flush()

            counts = {"nodes": 0, "segments": 0, "ways": 0, "skipped": 0}
            node_rows: dict[str, dict] = {}
            segment_rows: list[dict] = []

            def flush() -> None:
                if not segment_rows:
                    return
                with SessionLocal.begin() as session:
                    if node_rows:
                        stmt = pg_insert(OsmRoadNode.__table__).values(list(node_rows.values()))
                        stmt = stmt.on_conflict_do_nothing(
                            index_elements=["import_run_id", "internal_node_id"]
                        )
                        session.execute(stmt)
                    session.execute(insert(OsmRoadSegment), segment_rows)
                counts["nodes"] += len(node_rows)
                counts["segments"] += len(segment_rows)
                node_rows.clear()
                segment_rows.clear()

            class GraphWriter(osmium.SimpleHandler):
                def way(self, way):
                    tags = {tag.k: tag.v for tag in way.tags}
                    if not is_drivable(tags, importer.highway_classes):
                        return
                    node_ids = [int(node.ref) for node in way.nodes]
                    try:
                        coordinates = [
                            (float(node.location.lon), float(node.location.lat))
                            for node in way.nodes
                        ]
                    except osmium.InvalidLocationError:
                        counts["skipped"] += 1
                        return
                    segments = build_directed_segments(
                        way_id=int(way.id),
                        way_version=int(way.version) if way.version is not None else None,
                        node_ids=node_ids,
                        coordinates=coordinates,
                        tags=tags,
                        split_node_ids=shared_index.shared_for(node_ids),
                    )
                    if not segments:
                        counts["skipped"] += 1
                        return
                    counts["ways"] += 1
                    for segment in segments:
                        for node_id, coordinate in (
                            (segment.from_node_id, segment.coordinates[0]),
                            (segment.to_node_id, segment.coordinates[-1]),
                        ):
                            node_rows[node_id] = {
                                "import_run_id": run_id,
                                "internal_node_id": node_id,
                                "graph_version": importer.graph_version,
                                "osm_node_id": int(node_id.removeprefix("osmn_")),
                                "geom": _point_geom(coordinate),
                            }
                        normalized = segment.normalized_tags
                        segment_rows.append(
                            {
                                "import_run_id": run_id,
                                "internal_segment_id": segment.internal_segment_id,
                                "graph_version": importer.graph_version,
                                "osm_way_id": segment.osm_way_id,
                                "osm_version": segment.osm_version,
                                "sequence": segment.sequence,
                                "source_from_node_id": segment.source_from_node_id,
                                "source_to_node_id": segment.source_to_node_id,
                                "from_node_id": segment.from_node_id,
                                "to_node_id": segment.to_node_id,
                                "travel_direction": segment.travel_direction,
                                **normalized,
                                "length_m": segment.length_m,
                                "tags": segment.tags,
                                "geom": _line_geom(segment.coordinates),
                            }
                        )
                    if len(segment_rows) >= importer.batch_size:
                        flush()

            logger.info("OSM %s: pass 2/2, writing directed graph", self.graph_version)
            # File-backed pyosmium indexes require an explicit cache filename.
            # Generate a disposable one unless the operator configured a
            # persistent filename (useful later for replication diffs).
            with tempfile.TemporaryDirectory(
                prefix="ndwinfo-osm-locations-",
                dir=settings.osm_import_temp_dir or None,
            ) as location_directory:
                location_index = _location_index_config(
                    settings.osm_location_index, Path(location_directory)
                )
                GraphWriter().apply_file(
                    str(self.path), locations=True, idx=location_index
                )
            flush()
            # Batch-local node dictionaries can contain the same junction in
            # different batches. Use the persisted cardinality for exact run
            # metadata instead of adding those local dictionary sizes.
            with SessionLocal() as session:
                counts["nodes"] = int(
                    session.scalar(
                        select(func.count())
                        .select_from(OsmRoadNode)
                        .where(OsmRoadNode.import_run_id == run_id)
                    )
                    or 0
                )
            return counts

    def _activate(self, run_id: int, counts: dict[str, int]) -> OsmImportRun:
        now = datetime.now(UTC)
        with SessionLocal.begin() as session:
            run = session.execute(
                select(OsmImportRun).where(OsmImportRun.id == run_id).with_for_update()
            ).scalar_one()
            # Lock the current active row so concurrent importers serialize the
            # final switch. The partial unique index is the last safety net.
            active = session.execute(
                select(OsmImportRun).where(OsmImportRun.is_active.is_(True)).with_for_update()
            ).scalars()
            for old in active:
                old.is_active = False
                old.status = "superseded"
            run.node_count = counts["nodes"]
            run.segment_count = counts["segments"]
            run.way_count = counts["ways"]
            run.completed_at = now
            run.activated_at = now
            run.status = "active"
            run.is_active = True
        with SessionLocal() as session:
            return session.get(OsmImportRun, run_id)

    @staticmethod
    def _mark_ready(run_id: int, counts: dict[str, int]) -> OsmImportRun:
        """Finalize a shadow graph without touching the active graph row."""
        now = datetime.now(UTC)
        with SessionLocal.begin() as session:
            run = session.execute(
                select(OsmImportRun).where(OsmImportRun.id == run_id).with_for_update()
            ).scalar_one()
            run.node_count = counts["nodes"]
            run.segment_count = counts["segments"]
            run.way_count = counts["ways"]
            run.completed_at = now
            run.status = "ready"
            run.is_active = False
        with SessionLocal() as session:
            return session.get(OsmImportRun, run_id)

    @staticmethod
    def _mark_failed(run_id: int, error: str) -> None:
        with SessionLocal.begin() as session:
            session.execute(delete(OsmRoadSegment).where(OsmRoadSegment.import_run_id == run_id))
            session.execute(delete(OsmRoadNode).where(OsmRoadNode.import_run_id == run_id))
            session.execute(
                update(OsmImportRun)
                .where(OsmImportRun.id == run_id)
                .values(
                    status="failed",
                    is_active=False,
                    completed_at=datetime.now(UTC),
                    error=error[:20_000],
                )
            )


def _require_osmium():
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - depends on deployment image
        raise RuntimeError(
            "The 'osmium' package is required for PBF import; install project dependencies"
        ) from exc
    return osmium


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _location_index_config(config: str, temp_directory: Path) -> str:
    value = config.strip() or "flex_mem"
    index_type = value.split(",", 1)[0]
    if index_type.endswith("_file_array") and "," not in value:
        return f"{index_type},{temp_directory / 'node-locations.idx'}"
    return value


def _point_geom(coordinate: tuple[float, float]) -> WKTElement:
    return WKTElement(f"POINT({coordinate[0]:.8f} {coordinate[1]:.8f})", srid=4326)


def _line_geom(coordinates: tuple[tuple[float, float], ...]) -> WKTElement:
    points = ",".join(f"{lon:.8f} {lat:.8f}" for lon, lat in coordinates)
    return WKTElement(f"LINESTRING({points})", srid=4326)


def import_and_bind(
    path: Path,
    *,
    graph_version: str | None = None,
    source_timestamp: datetime | None = None,
) -> tuple[OsmImportRun, dict[str, int | str]]:
    """Import, atomically activate and then persist NDW measurement bindings."""
    run = OsmPbfImporter(
        path=path,
        graph_version=graph_version,
        source_timestamp=source_timestamp,
    ).run()
    # Binding is deliberately outside the graph activation transaction. Until
    # it completes the new graph safely returns roads without live speeds; an
    # ambiguous or missing binding can therefore never leak through activation.
    from ndwinfo.matching.source_binding import rebuild_measurement_bindings

    with SessionLocal.begin() as session:
        binding_counts = rebuild_measurement_bindings(session, run.id)
    return run, binding_counts


def import_shadow_and_bind(
    path: Path,
    *,
    graph_version: str | None = None,
    source_timestamp: datetime | None = None,
) -> tuple[OsmImportRun, dict[str, int | str]]:
    """Build and bind a comparison graph without activating it."""
    run = OsmPbfImporter(
        path=path,
        graph_version=graph_version,
        source_timestamp=source_timestamp,
    ).run(activate=False)
    from ndwinfo.matching.source_binding import rebuild_measurement_bindings

    with SessionLocal.begin() as session:
        binding_counts = rebuild_measurement_bindings(
            session,
            run.id,
            allow_inactive=True,
        )
    return run, binding_counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a local OSM PBF as directed road graph")
    parser.add_argument("path", nargs="?", default=settings.osm_pbf_path)
    parser.add_argument("--graph-version")
    parser.add_argument(
        "--shadow",
        action="store_true",
        help="build and bind a ready graph without replacing the active graph",
    )
    parser.add_argument(
        "--source-timestamp",
        help="Upstream snapshot timestamp (ISO 8601); omitted when unknown",
    )
    args = parser.parse_args()
    source_timestamp = (
        datetime.fromisoformat(args.source_timestamp.replace("Z", "+00:00"))
        if args.source_timestamp
        else None
    )
    logging.basicConfig(level=logging.INFO)
    import_fn = import_shadow_and_bind if args.shadow else import_and_bind
    run, binding_counts = import_fn(
        path=Path(args.path),
        graph_version=args.graph_version,
        source_timestamp=source_timestamp,
    )
    logger.info(
        "%s %s: %d nodes, %d directed segments; bindings=%s",
        "Prepared shadow" if args.shadow else "Activated",
        run.graph_version,
        run.node_count,
        run.segment_count,
        binding_counts,
    )


if __name__ == "__main__":
    main()
