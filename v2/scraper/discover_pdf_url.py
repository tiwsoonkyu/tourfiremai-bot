"""
v2.scraper.discover_pdf_url — Step 1 of fee pipeline.

Find PDF URL for a given tour, preferring (in order):
  1. tours_canonical.pdf_url column (if already populated)
  2. HTTP GET /intertourdetail/{web_code} — parse <a href="...pdf"> or
     well-known patterns
  3. None — caller must handoff to manual queue

The function NEVER hits the FB Page or Make.com. Only tourfiremai.com.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("v2.scraper.pdf_discovery")

BASE_URL = "https://www.tourfiremai.com"
DETAIL_PATH = "/intertourdetail/{web_code}"


# Anchor href ending with .pdf (case-insensitive)
_PDF_HREF_RE = re.compile(
    r'href=["\']([^"\']+\.pdf[^"\']*)["\']',
    re.I,
)
# Some pages use download links with ?file=...pdf
_PDF_QUERY_RE = re.compile(
    r'href=["\']([^"\']*(?:[?&](?:file|pdf)=[^"&\']+\.pdf[^"\']*))["\']',
    re.I,
)


@dataclass
class PdfDiscoveryResult:
    web_code: str
    pdf_url: Optional[str]
    found_in: str          # 'db_column' | 'detail_html' | 'not_found'
    detail_url: str
    notes: str = ""


def _absolutize(url: str) -> str:
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL + url
    return BASE_URL + "/" + url


def parse_pdf_url_from_html(html: str) -> Optional[str]:
    """Find first PDF link in a tour detail page HTML. Returns absolute URL."""
    if not html:
        return None
    m = _PDF_HREF_RE.search(html)
    if m:
        return _absolutize(m.group(1))
    m = _PDF_QUERY_RE.search(html)
    if m:
        return _absolutize(m.group(1))
    return None


def discover_pdf_url(
    web_code: str,
    *,
    supabase=None,
    http_client=None,
    prefer_db: bool = True,
    timeout_sec: int = 30,
) -> PdfDiscoveryResult:
    """
    Step 1 of fee pipeline. Discover the PDF URL for a tour.

    Args:
        web_code: tour identifier (e.g. "ap242455")
        supabase: optional adapter; if provided + prefer_db, checks
                  tours_canonical.pdf_url first
        http_client: optional injected HTTP client (object with .get(url, timeout))
        prefer_db: if False, always fetch detail page (forces refresh)
    """
    detail_url = BASE_URL + DETAIL_PATH.format(web_code=web_code)

    # 1) Prefer pdf_url in DB if available
    if prefer_db and supabase is not None:
        row = supabase.table("tours_canonical").select_one({"web_code": web_code})
        if row and row.get("pdf_url"):
            return PdfDiscoveryResult(
                web_code=web_code, pdf_url=row["pdf_url"],
                found_in="db_column", detail_url=detail_url,
            )

    # 2) Scrape detail HTML
    if http_client is None:
        try:
            import requests as _requests  # noqa
        except ImportError:
            return PdfDiscoveryResult(
                web_code=web_code, pdf_url=None,
                found_in="not_found", detail_url=detail_url,
                notes="requests_not_installed",
            )
        import requests
        try:
            resp = requests.get(detail_url, timeout=timeout_sec, headers={
                "User-Agent": "Mozilla/5.0 (TourFireMai V2 PDF discover)",
                "Accept": "text/html",
            })
        except Exception as e:
            return PdfDiscoveryResult(
                web_code=web_code, pdf_url=None,
                found_in="not_found", detail_url=detail_url,
                notes=f"http_error: {type(e).__name__}",
            )
        if resp.status_code != 200:
            return PdfDiscoveryResult(
                web_code=web_code, pdf_url=None,
                found_in="not_found", detail_url=detail_url,
                notes=f"http_status: {resp.status_code}",
            )
        html = resp.text
    else:
        r = http_client.get(detail_url, timeout=timeout_sec)
        if r.status_code != 200:
            return PdfDiscoveryResult(
                web_code=web_code, pdf_url=None,
                found_in="not_found", detail_url=detail_url,
                notes=f"http_status: {r.status_code}",
            )
        html = r.text

    pdf_url = parse_pdf_url_from_html(html)
    if pdf_url:
        # Cache into DB if possible
        if supabase is not None:
            try:
                supabase.table("tours_canonical").update(
                    {"web_code": web_code}, {"pdf_url": pdf_url}
                )
            except Exception:
                pass  # non-fatal
        return PdfDiscoveryResult(
            web_code=web_code, pdf_url=pdf_url,
            found_in="detail_html", detail_url=detail_url,
        )

    return PdfDiscoveryResult(
        web_code=web_code, pdf_url=None,
        found_in="not_found", detail_url=detail_url,
        notes="no_pdf_anchor_in_html",
    )
