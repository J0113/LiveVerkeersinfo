"""Download a configured OSM PBF and activate the corresponding local graph."""

from __future__ import annotations

import json
import logging
from email.utils import parsedate_to_datetime
from pathlib import Path

from sqlalchemy import func, select

from ndwinfo.config import settings
from ndwinfo.db import SessionLocal
from ndwinfo.download import fetch
from ndwinfo.feeds import FEEDS_BY_NAME
from ndwinfo.ingest.osm import OsmPbfImporter, import_and_bind
from ndwinfo.matching.live_object_job import rebuild_live_object_bindings
from ndwinfo.matching.live_objects import (
    ALGORITHM_VERSION as LIVE_OBJECT_ALGORITHM_VERSION,
)
from ndwinfo.matching.live_objects import PERSISTED_SOURCE_TYPES
from ndwinfo.matching.source_binding import (
    ALGORITHM_VERSION,
    SOURCE_TYPE,
    rebuild_measurement_bindings,
)
from ndwinfo.models import OsmImportRun, SourceLocationBinding

logger = logging.getLogger(__name__)


def bootstrap() -> OsmImportRun:
    """Conditionally download, import and bind the configured graph snapshot."""
    feed = FEEDS_BY_NAME["osm_pbf"]
    metadata_path = Path(settings.data_dir) / ".meta" / "osm_pbf.json"
    metadata = _read_metadata(metadata_path)
    result = fetch(
        feed,
        etag=metadata.get("etag"),
        last_modified=metadata.get("last_modified"),
    )
    if result.status == "error" or result.path is None:
        raise RuntimeError(f"OSM PBF download failed: {result.error}")
    if result.status == "ok":
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {"etag": result.etag, "last_modified": result.last_modified},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    importer = OsmPbfImporter(path=result.path)
    with SessionLocal() as session:
        existing = session.scalar(
            select(OsmImportRun).where(
                OsmImportRun.graph_version == importer.graph_version
            )
        )
        if existing is not None and existing.is_active and existing.status == "active":
            binding_count = session.scalar(
                select(func.count(SourceLocationBinding.id)).where(
                    SourceLocationBinding.source_type == SOURCE_TYPE,
                    SourceLocationBinding.graph_version == existing.graph_version,
                    SourceLocationBinding.algorithm_version == ALGORITHM_VERSION,
                )
            ) or 0
            if binding_count == 0:
                # A new matcher version has no evaluations yet. Site metadata
                # refreshes are handled incrementally by MeasurementSiteIngester,
                # so an unchanged graph with existing evaluations is a no-op.
                with SessionLocal.begin() as binding_session:
                    counts = rebuild_measurement_bindings(binding_session, existing.id)
                logger.info(
                    "OSM graph %s already active; initialized bindings=%s",
                    existing.graph_version,
                    counts,
                )
            else:
                logger.info(
                    "OSM graph %s already active with %d %s bindings; no rebuild needed",
                    existing.graph_version,
                    binding_count,
                    ALGORITHM_VERSION,
                )
            live_kinds = []
            for kind, source_type in PERSISTED_SOURCE_TYPES.items():
                live_count = session.scalar(
                    select(func.count(SourceLocationBinding.id)).where(
                        SourceLocationBinding.source_type == source_type,
                        SourceLocationBinding.graph_version == existing.graph_version,
                        SourceLocationBinding.algorithm_version
                        == LIVE_OBJECT_ALGORITHM_VERSION,
                    )
                ) or 0
                if live_count == 0:
                    live_kinds.append(kind)
            if live_kinds:
                with SessionLocal.begin() as live_session:
                    live_counts = rebuild_live_object_bindings(
                        live_session, existing.id, kinds=live_kinds
                    )
                logger.info("Initialized current live-object bindings=%s", live_counts)
            return existing
        if existing is not None:
            raise RuntimeError(
                f"OSM graph version {existing.graph_version} already exists with "
                f"status={existing.status}; supply an explicit graph version to retry"
            )

    timestamp = (
        parsedate_to_datetime(result.last_modified)
        if result.last_modified
        else None
    )
    run, counts = import_and_bind(result.path, source_timestamp=timestamp)
    with SessionLocal.begin() as live_session:
        live_counts = rebuild_live_object_bindings(live_session, run.id)
    logger.info("Activated %s; bindings=%s", run.graph_version, counts)
    logger.info("Activated %s; MSI/DRIP bindings=%s", run.graph_version, live_counts)
    return run


def _read_metadata(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    run = bootstrap()
    logger.info(
        "OSM bootstrap ready: %s (%d directed segments)",
        run.graph_version,
        run.segment_count,
    )


if __name__ == "__main__":
    main()
