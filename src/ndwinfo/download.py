"""Conditional GET download helper."""

import gzip
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO
from urllib.parse import urljoin

import httpx

from ndwinfo.config import settings

logger = logging.getLogger(__name__)
MAX_RESUME_ATTEMPTS = 5


class _RangeNotHonored(Exception):
    """Raised when a Range-resume request gets a non-206 response.

    A server that ignores Range and returns 200 with the full body would
    otherwise get silently appended onto the partial file already on disk,
    corrupting it with no error. Caught alongside transport errors so the
    retry loop restarts the download from byte zero instead.
    """

_VERSIONED_ZIP_RE = re.compile(r'href=["\'](\d{2}-\d{2}-\d{4}\.zip)["\']', re.I)


def _source_url(feed: dict) -> str:
    """Return a feed URL, resolving a versioned source index when necessary."""
    url = feed.get("url")
    if url:
        return url

    index_url = feed.get("index_url")
    if not index_url:
        return f"{settings.ndw_base_url.rstrip('/')}/{feed['filename']}"

    response = httpx.get(index_url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    candidates = _VERSIONED_ZIP_RE.findall(response.text)
    if not candidates:
        raise RuntimeError(f"No dated ZIP packages found at {index_url}")

    latest = max(candidates, key=lambda name: datetime.strptime(name[:-4], "%d-%m-%Y"))
    return urljoin(index_url, latest)


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

    Large downloads can be dropped mid-stream by an intermediate proxy well
    before completion (observed on multi-hundred-MB files). When that happens
    the partial write is resumed with a Range request instead of restarting
    from byte zero, up to MAX_RESUME_ATTEMPTS.
    """
    path = Path(settings.data_dir) / feed["filename"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")

    base_headers: dict[str, str] = {}
    if etag:
        base_headers["If-None-Match"] = etag
    elif last_modified:
        base_headers["If-Modified-Since"] = last_modified

    resp_etag = resp_lm = None
    resume_from = 0
    tmp_path.unlink(missing_ok=True)

    try:
        url = _source_url(feed)
        for attempt in range(1, MAX_RESUME_ATTEMPTS + 1):
            req_headers = dict(base_headers)
            mode = "wb"
            is_resume = bool(resume_from)
            if is_resume:
                req_headers.pop("If-None-Match", None)
                req_headers.pop("If-Modified-Since", None)
                req_headers["Range"] = f"bytes={resume_from}-"
                mode = "ab"

            try:
                with httpx.stream(
                    "GET", url, headers=req_headers, follow_redirects=True, timeout=60.0
                ) as resp:
                    if resp.status_code == 304 and resume_from == 0:
                        # Server says unchanged, but if our local copy is gone (e.g. a
                        # prior download failed mid-stream and was unlinked) we have
                        # nothing to parse. Re-fetch unconditionally instead of getting
                        # stuck on 304 until the upstream ETag next changes.
                        if not path.exists():
                            return fetch(feed, etag=None, last_modified=None)
                        return DownloadResult(
                            status="not_modified",
                            path=path,
                            etag=etag,
                            last_modified=last_modified,
                            http_status=304,
                            error=None,
                        )
                    resp.raise_for_status()

                    if is_resume and resp.status_code != 206:
                        raise _RangeNotHonored(
                            f"expected 206 Partial Content on resume, got {resp.status_code}"
                        )

                    if resume_from == 0:
                        resp_etag = resp.headers.get("ETag")
                        resp_lm = resp.headers.get("Last-Modified")

                    with open(tmp_path, mode) as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            resume_from += len(chunk)
                break  # streamed to completion without the connection dropping
            except (httpx.TransportError, httpx.StreamError, _RangeNotHonored) as exc:
                if isinstance(exc, _RangeNotHonored):
                    # Server can't resume this download — the partial file is
                    # unusable as a base for further Range requests. Discard it
                    # and restart from byte zero on the next attempt.
                    tmp_path.unlink(missing_ok=True)
                    resume_from = 0
                if attempt >= MAX_RESUME_ATTEMPTS:
                    raise
                logger.warning(
                    "%s: download interrupted at %d bytes (attempt %d/%d), resuming: %s",
                    feed["name"], resume_from, attempt, MAX_RESUME_ATTEMPTS, exc,
                )

        tmp_path.replace(path)
        return DownloadResult(
            status="ok",
            path=path,
            etag=resp_etag,
            last_modified=resp_lm,
            http_status=200,
            error=None,
        )
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
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
        print("Usage: python -m ndwinfo.download <feed_name>")
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
