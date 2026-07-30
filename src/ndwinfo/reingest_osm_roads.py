"""Force a transactional OSM-road ingest from an existing local PBF file."""

from __future__ import annotations

import argparse
from pathlib import Path

from ndwinfo.db import SessionLocal
from ndwinfo.download import DownloadResult
from ndwinfo.ingest.osm_roads import OsmRoadIngester


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    path = args.path.resolve()
    if not path.is_file():
        parser.error(f"PBF file does not exist: {path}")

    result = DownloadResult(
        status="ok",
        path=path,
        etag=None,
        last_modified=None,
        http_status=None,
        error=None,
    )
    with SessionLocal() as session:
        rows = OsmRoadIngester(
            feed_name="osm_netherlands",
            extract_key="netherlands",
            build_independent_lanes=False,
        )._ingest(result, session)
        session.commit()
    print(f"upserted {rows} OSM driving roads from {path}")


if __name__ == "__main__":
    main()
