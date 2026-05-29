"""Conditional GET download helper."""

import gzip
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import httpx

from ndwinfo.config import settings


@dataclass
class DownloadResult:
    status: str           # 'ok' | 'not_modified' | 'error'
    path: Path | None
    etag: str | None
    last_modified: str | None
    http_status: int | None
    error: str | None


def fetch(
    feed: dict,
    etag: str | None = None,
    last_modified: str | None = None,
) -> DownloadResult:
    """Download feed file with conditional GET.

    Pass etag/last_modified from the previous feed_run to avoid re-downloading
    unchanged files (returns status='not_modified' on HTTP 304).
    """
    url = f"{settings.ndw_base_url.rstrip('/')}/{feed['filename']}"
    path = Path(settings.data_dir) / feed["filename"]
    path.parent.mkdir(parents=True, exist_ok=True)

    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    elif last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        with httpx.stream(
            "GET", url, headers=headers, follow_redirects=True, timeout=60.0
        ) as resp:
            if resp.status_code == 304:
                return DownloadResult(
                    status="not_modified",
                    path=path if path.exists() else None,
                    etag=etag,
                    last_modified=last_modified,
                    http_status=304,
                    error=None,
                )
            resp.raise_for_status()

            resp_etag = resp.headers.get("ETag")
            resp_lm = resp.headers.get("Last-Modified")

            with open(path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

            return DownloadResult(
                status="ok",
                path=path,
                etag=resp_etag,
                last_modified=resp_lm,
                http_status=resp.status_code,
                error=None,
            )
    except Exception as exc:
        return DownloadResult(
            status="error",
            path=None,
            etag=None,
            last_modified=None,
            http_status=None,
            error=str(exc),
        )


@contextmanager
def open_feed(path: Path) -> IO[bytes]:
    """Open a downloaded feed file, decompressing on-the-fly if .gz."""
    if path.suffix == ".gz":
        f = gzip.open(path, "rb")
    else:
        f = open(path, "rb")
    try:
        yield f
    finally:
        f.close()


if __name__ == "__main__":
    import json
    import sys

    from ndwinfo.feeds import FEEDS

    if len(sys.argv) < 2:
        print(f"Usage: python -m ndwinfo.download <feed_name>")
        sys.exit(1)

    feed_name = sys.argv[1]
    feed = next((f for f in FEEDS if f["name"] == feed_name), None)
    if feed is None:
        names = ", ".join(f["name"] for f in FEEDS)
        print(f"Unknown feed '{feed_name}'. Available: {names}")
        sys.exit(1)

    # Persist ETag/Last-Modified between CLI runs for 304 testing
    meta_dir = Path(settings.data_dir) / ".meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{feed_name}.json"

    cached_etag = cached_lm = None
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        cached_etag = meta.get("etag")
        cached_lm = meta.get("last_modified")

    result = fetch(feed, etag=cached_etag, last_modified=cached_lm)

    if result.status == "ok":
        size = result.path.stat().st_size if result.path else 0
        print(f"status=ok  path={result.path}  size={size:,} bytes")
        meta_path.write_text(
            json.dumps({"etag": result.etag, "last_modified": result.last_modified})
        )
    elif result.status == "not_modified":
        print("status=not_modified  (304 — server confirms file unchanged)")
    else:
        print(f"status=error  {result.error}")
        sys.exit(1)
