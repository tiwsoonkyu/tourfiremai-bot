"""
v2.scraper.download_pdf — Download PDF files from tour URLs to local/Supabase storage.

Sprint 3 scope: local filesystem cache (sandbox-friendly). Sprint 4 wires
Supabase Storage bucket `tour-pdfs/`.

Public API:
    download_pdf(url, *, cache_dir=...) -> PDFArtifact
        with hash, local_path, fetched_at
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("v2.scraper.pdf_download")


@dataclass
class PDFArtifact:
    url: str
    local_path: str
    sha256: str
    size_bytes: int
    fetched_at: float
    was_cached: bool = False


def _safe_basename(url: str) -> str:
    """Hash-based filename so URL queries / special chars don't break FS."""
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    suffix = ".pdf"
    return f"{h}{suffix}"


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def download_pdf(
    url: str,
    *,
    cache_dir: str = "/tmp/v2-pdf-cache",
    http_client=None,
    force: bool = False,
    timeout_sec: int = 30,
) -> PDFArtifact:
    """
    Download `url` to `cache_dir` (skip if already cached).

    Args:
        http_client: optional injected HTTP client (must have .get(url, timeout))
                     If None, uses requests.
        force: re-download even if cached
    """
    os.makedirs(cache_dir, exist_ok=True)
    fname = _safe_basename(url)
    path = os.path.join(cache_dir, fname)

    if os.path.exists(path) and not force:
        return PDFArtifact(
            url=url, local_path=path,
            sha256=_hash_file(path),
            size_bytes=os.path.getsize(path),
            fetched_at=os.path.getmtime(path),
            was_cached=True,
        )

    if http_client is None:
        import requests
        resp = requests.get(url, timeout=timeout_sec, headers={
            "User-Agent": "Mozilla/5.0 (TourFireMai V2 PDF cache)",
            "Accept": "application/pdf,*/*;q=0.5",
        })
        status = resp.status_code
        ok = status == 200
        content = resp.content
    else:
        r = http_client.get(url, timeout=timeout_sec)
        status = r.status_code
        ok = status == 200
        content = r.content if hasattr(r, "content") else (r.text or "").encode()

    if not ok:
        raise RuntimeError(f"PDF download failed: {url} → status {status}")

    with open(path, "wb") as f:
        f.write(content)

    return PDFArtifact(
        url=url, local_path=path,
        sha256=_hash_file(path),
        size_bytes=len(content),
        fetched_at=time.time(),
    )
