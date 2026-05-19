"""
v2.lib.page_post_context — Page Post Intelligence + Sold-Out Signal foundation.

DEV-2026-05-19-006 / docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md.

Deterministic helpers (no LLM, no live network, no secrets) that:

    1. Upsert recent Facebook / IG / LINE OA page posts idempotently by
       (platform, post_id). text_hash captures caption changes so re-ingest
       safely updates the row.
    2. Extract tour references from page-post text (web_code, tour_code_real,
       URL forms such as /intertour/.../{slug} and /tour/{web_code}).
    3. Link page posts to one or more tours (web_code OR tour_code_real OR
       tour_id), idempotent per (post, code).
    4. Let admin mark a tour, tour departure, or specific page post as
       sold_out / full and clear that override.
    5. Decide deterministically whether a candidate tour is "blocked" because
       an active sold_out / full override covers it (tour-wide, the requested
       departure date, or the source post the customer came from).
    6. Build a compact, LLM-safe source-context summary — title-like text,
       masked, wholesale-name-scrubbed, length-capped — so the response
       planner never receives raw post captions.

All functions take the Supabase-like + optional `now` clock as arguments so
unit tests run against `v2/tests/conftest.py` in-memory fakes. No env reads,
no Meta Graph API calls, no LINE API calls, no wholesale partner brand names
ever appear in returned strings.

Hard rules (enforced by `v2/tests/test_page_post_context.py`):

    - Wholesale partner names: filtered through the same
      response_writer._WHOLESALE_BLACKLIST and replaced with a redaction
      token before any string is returned to a caller.
    - Compact context: caption text is title-only — first
      ``CONTEXT_TITLE_MAX_CHARS`` chars, single-line, scrubbed.
    - Idempotency: re-upsert of the same post must NOT create a duplicate row.
    - Recent window: list_recent_page_posts defaults to 3 days from
      ``posted_at``; ``active_until`` (if present) supersedes the default.
    - Block decision: an active override (cleared_at IS NULL AND
      (expires_at IS NULL OR expires_at > now)) with status in
      {sold_out, full} blocks the candidate.

This module deliberately does not include any UI, dashboard, or webhook
adapter. A future LINE admin command (DEV ?), Meta Graph ingester, and
admin dashboard read/write API will use these functions as-is.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Protocol

from . import redactor
from .response_writer import _WHOLESALE_BLACKLIST
from .tour_codes import normalize_tour_code_real, normalize_web_code

logger = logging.getLogger("v2.page_post_context")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RECENT_WINDOW_DAYS = 3
MAX_RECENT_WINDOW_DAYS = 30
CONTEXT_TITLE_MAX_CHARS = 80
CONTEXT_REASON_MAX_CHARS = 160
_WHOLESALE_REDACTION_TOKEN = "***WHOLESALE-REDACTED***"

# Status values that count as "blocked" for sales.
_BLOCKING_STATUSES = frozenset({"sold_out", "full"})

# Valid status / scope sets — kept in sync with migration 020 CHECKs.
_OVERRIDE_STATUSES = frozenset({"available", "sold_out", "full", "unknown"})
_OVERRIDE_SCOPES = frozenset({"tour", "departure", "post"})
_SOURCE_TYPES = frozenset({"page_post", "ad", "organic", "unknown"})
_PLATFORMS = frozenset({"facebook", "instagram", "line_oa", "website", "other"})

# Thai admin/bot-facing reason templates (deterministic — never call LLM).
REASON_TOUR_FULL = (
    "ทัวร์นี้แอดมินแจ้งว่าเต็มแล้ว เดี๋ยวช่วยคัดตัวใกล้เคียงที่ยังเปิดรับให้นะคะ"
)
REASON_DEPARTURE_FULL = (
    "รอบเดินทางวันที่ลูกค้าถามเต็มแล้ว แต่ยังมีรอบใกล้เคียงให้เลือกค่ะ"
)
REASON_POST_FULL = (
    "โปรแกรมในโพสต์นี้เต็มแล้วค่ะ เดี๋ยวช่วยคัดตัวใกล้เคียงที่ยังเปิดรับให้นะคะ"
)
REASON_AVAILABLE_FROM_POST = (
    "โปรแกรมในโพสต์นี้ยังมีตัวเลือกให้เช็กค่ะ ขอคัดรอบ/ราคาให้ตรงงบก่อนนะคะ"
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class SupabaseLike(Protocol):
    def table(self, name: str): ...  # returns a query-builder object


@dataclass
class ExtractedRefs:
    web_codes: list[str] = field(default_factory=list)
    tour_codes_real: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    @property
    def has_any(self) -> bool:
        return bool(self.web_codes or self.tour_codes_real)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PagePostRecord:
    id: str
    platform: str
    page_id: str
    post_id: str
    permalink_url: Optional[str]
    posted_at: str
    text_hash: str
    caption_text: Optional[str]
    status: str
    active_until: Optional[str]
    source_type: str
    ingested_at: str
    updated_at: str
    inserted: bool = False  # True if a new row was inserted; False if updated

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PagePostSummary:
    """Compact view of a recent page post — safe to surface to admin."""
    id: str
    platform: str
    page_id: str
    post_id: str
    permalink_url: Optional[str]
    posted_at: str
    source_type: str
    title: str  # length-capped, scrubbed
    linked_web_codes: list[str] = field(default_factory=list)
    linked_tour_codes_real: list[str] = field(default_factory=list)
    is_post_blocked: bool = False
    block_status: Optional[str] = None  # None | sold_out | full

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PagePostTourLink:
    id: str
    page_post_id: str
    web_code: Optional[str]
    tour_code_real: Optional[str]
    tour_id: Optional[str]
    confidence: float
    status: str
    detected_at: str
    inserted: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AvailabilityOverride:
    id: str
    scope: str
    status: str
    web_code: Optional[str]
    tour_code_real: Optional[str]
    tour_id: Optional[str]
    page_post_id: Optional[str]
    departure_date: Optional[str]
    reason: Optional[str]
    marked_by: str
    marked_at: str
    expires_at: Optional[str]
    cleared_at: Optional[str] = None
    cleared_by: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BlockDecision:
    is_blocked: bool
    status: Optional[str]  # None | sold_out | full
    scope: Optional[str]   # None | tour | departure | post
    reason_text: Optional[str]  # safe Thai bot/admin text
    matched_override_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SourceContext:
    """LLM-safe summary of where the customer came from."""
    source_type: str             # one of _SOURCE_TYPES
    page_post_id: Optional[str]
    permalink_url: Optional[str]
    posted_at: Optional[str]
    title: Optional[str]
    linked_web_codes: list[str] = field(default_factory=list)
    linked_tour_codes_real: list[str] = field(default_factory=list)
    is_recent: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanningContext:
    """Single bundle the response planner uses."""
    source: SourceContext
    block: BlockDecision
    replacement_needed: bool
    safe_reason_text: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00") if isinstance(value, str) else value
        ts = datetime.fromisoformat(s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


def _scrub_wholesale(value: Optional[str]) -> Optional[str]:
    """Redact wholesale brand tokens from a free-text field. None-safe."""
    if not value:
        return value
    for pat in _WHOLESALE_BLACKLIST:
        if pat.search(value):
            return _WHOLESALE_REDACTION_TOKEN
    return value


def _safe_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    return redactor.redact(_scrub_wholesale(value) or "")


def _shorten_title(text: Optional[str]) -> str:
    """Single-line, length-capped, wholesale-scrubbed title for context."""
    if not text:
        return ""
    cleaned = _safe_text(text) or ""
    one_line = " ".join(cleaned.split())
    if len(one_line) > CONTEXT_TITLE_MAX_CHARS:
        return one_line[: CONTEXT_TITLE_MAX_CHARS - 1].rstrip() + "…"
    return one_line


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _override_active(row: dict, now_dt: datetime) -> bool:
    if row.get("cleared_at"):
        return False
    expires = _parse_iso(row.get("expires_at"))
    if expires is not None and expires <= now_dt:
        return False
    return True


def _select_all(supabase: SupabaseLike, table: str, where: dict) -> list[dict]:
    tbl = supabase.table(table)
    if hasattr(tbl, "select_all"):
        return tbl.select_all(where)
    return []


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_TOURFIREMAI_HOST = "tourfiremai.com"

# /intertour/<country_id>/<slug>-ap123456    (legacy slug form)
# /intertourdetail/ap123456                  (canonical detail URL)
# /tour/ap123456                             (short link)
_URL_RE = re.compile(
    r"https?://(?:www\.)?[^\s)]*"  # anything tourfiremai or short link
    , re.I,
)

# web codes embedded in URL or text
_WEB_CODE_TOKEN_RE = re.compile(r"\b([a-z]{2,3}\d{5,7})\b", re.I)


def extract_tour_references(text: Optional[str]) -> ExtractedRefs:
    """
    Extract web codes / real tour codes / URLs from free text.

    web codes: ap123456, in1234567, etc. — lowercase prefix + digits.
    real tour codes: BCCKG27-HU, JX001, KIX-FUK-5D4N — uppercase, has dash
      OR letters+digits, never matches the web-code shape, never an airline
      code on its own (see v2.lib.tour_codes).
    URLs: any http(s) URL is captured for later inspection.
    """
    refs = ExtractedRefs()
    if not text:
        return refs

    # URLs
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,);:")
        if url not in refs.urls:
            refs.urls.append(url)

    # web codes — from full text (lowercased) to catch URL-embedded ones too.
    for m in _WEB_CODE_TOKEN_RE.finditer(text):
        candidate = normalize_web_code(m.group(1))
        if candidate and candidate not in refs.web_codes:
            refs.web_codes.append(candidate)

    # real tour codes — scan whole text, but treat each match independently.
    seen_real: set[str] = set()
    # Reuse extractor patterns from tour_codes via normalize_tour_code_real
    # in a sliding manner on tokens that look uppercase-ish.
    for token in re.finditer(r"\b([A-Z][A-Z0-9\-]{2,})\b", text):
        candidate = normalize_tour_code_real(token.group(1))
        if candidate and candidate not in seen_real:
            seen_real.add(candidate)
            refs.tour_codes_real.append(candidate)

    return refs


# ---------------------------------------------------------------------------
# Upsert page post
# ---------------------------------------------------------------------------


def _validate_platform(platform: str) -> str:
    if platform not in _PLATFORMS:
        raise ValueError(
            f"upsert_page_post: platform must be one of {sorted(_PLATFORMS)}; "
            f"got {platform!r}"
        )
    return platform


def _validate_source_type(source_type: str) -> str:
    if source_type not in _SOURCE_TYPES:
        raise ValueError(
            f"source_type must be one of {sorted(_SOURCE_TYPES)}; "
            f"got {source_type!r}"
        )
    return source_type


def upsert_page_post(
    supabase: SupabaseLike,
    *,
    platform: str = "facebook",
    page_id: str,
    post_id: str,
    posted_at: Optional[datetime] = None,
    permalink_url: Optional[str] = None,
    caption_text: Optional[str] = None,
    source_type: str = "page_post",
    active_until: Optional[datetime] = None,
    status: str = "active",
    meta: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> PagePostRecord:
    """
    Idempotent upsert keyed by (platform, post_id).

    Re-ingesting the same post UPDATES caption_text / text_hash / updated_at
    in place — it does NOT create a new row.
    """
    platform = _validate_platform(platform)
    source_type = _validate_source_type(source_type)
    if not page_id or not post_id:
        raise ValueError("upsert_page_post: page_id and post_id are required")
    if status not in {"active", "archived", "removed"}:
        raise ValueError(f"upsert_page_post: invalid status {status!r}")

    now_dt = _now(now)
    posted_at_dt = _now(posted_at) if posted_at is not None else now_dt

    text = caption_text or ""
    text_hash = _sha256_hex(text)

    existing = supabase.table("page_posts").select_one(
        {"platform": platform, "post_id": post_id}
    )

    common = {
        "platform": platform,
        "page_id": page_id,
        "post_id": post_id,
        "permalink_url": permalink_url,
        "posted_at": _iso(posted_at_dt),
        "text_hash": text_hash,
        "caption_text": caption_text,
        "status": status,
        "active_until": _iso(active_until) if active_until else None,
        "source_type": source_type,
        "updated_at": _iso(now_dt),
        "meta": meta or {},
    }

    if existing:
        supabase.table("page_posts").update(
            {"id": existing["id"]},
            common,
        )
        row = supabase.table("page_posts").select_one({"id": existing["id"]}) or {}
        return PagePostRecord(
            id=str(row.get("id") or existing["id"]),
            platform=row.get("platform", platform),
            page_id=row.get("page_id", page_id),
            post_id=row.get("post_id", post_id),
            permalink_url=row.get("permalink_url"),
            posted_at=row.get("posted_at") or common["posted_at"],
            text_hash=row.get("text_hash") or text_hash,
            caption_text=row.get("caption_text"),
            status=row.get("status", status),
            active_until=row.get("active_until"),
            source_type=row.get("source_type", source_type),
            ingested_at=row.get("ingested_at") or existing.get("ingested_at") or _iso(now_dt),
            updated_at=row.get("updated_at") or _iso(now_dt),
            inserted=False,
        )

    new_id = str(uuid.uuid4())
    inserted = supabase.table("page_posts").insert({
        "id": new_id,
        **common,
        "ingested_at": _iso(now_dt),
    })
    return PagePostRecord(
        id=str(inserted.get("id") or new_id),
        platform=platform,
        page_id=page_id,
        post_id=post_id,
        permalink_url=permalink_url,
        posted_at=common["posted_at"],
        text_hash=text_hash,
        caption_text=caption_text,
        status=status,
        active_until=common["active_until"],
        source_type=source_type,
        ingested_at=_iso(now_dt),
        updated_at=common["updated_at"],
        inserted=True,
    )


# ---------------------------------------------------------------------------
# Listing recent page posts
# ---------------------------------------------------------------------------


def _is_recent(row: dict, now_dt: datetime, window_days: int) -> bool:
    if row.get("status") != "active":
        return False
    active_until = _parse_iso(row.get("active_until"))
    if active_until is not None:
        return active_until >= now_dt
    posted = _parse_iso(row.get("posted_at"))
    if not posted:
        return False
    return posted >= (now_dt - timedelta(days=window_days))


def list_recent_page_posts(
    supabase: SupabaseLike,
    *,
    days: int = DEFAULT_RECENT_WINDOW_DAYS,
    platform: Optional[str] = None,
    limit: int = 25,
    now: Optional[datetime] = None,
) -> list[PagePostSummary]:
    """
    Return recent active page posts (default last 3 days), newest first.

    `active_until` (when set) takes precedence over `posted_at + days`.
    """
    if days <= 0 or days > MAX_RECENT_WINDOW_DAYS:
        raise ValueError(
            f"list_recent_page_posts: days must be in (0, {MAX_RECENT_WINDOW_DAYS}]; "
            f"got {days}"
        )
    now_dt = _now(now)
    where: dict = {}
    if platform:
        _validate_platform(platform)
        where["platform"] = platform
    rows = _select_all(supabase, "page_posts", where)
    rows = [r for r in rows if _is_recent(r, now_dt, days)]
    rows.sort(key=lambda r: r.get("posted_at") or "", reverse=True)
    out: list[PagePostSummary] = []
    for r in rows[: max(0, limit)]:
        links = _select_all(supabase, "page_post_tour_links", {
            "page_post_id": r.get("id"), "status": "active",
        })
        web_codes = sorted({l.get("web_code") for l in links if l.get("web_code")})
        real_codes = sorted({l.get("tour_code_real") for l in links if l.get("tour_code_real")})
        post_block = _post_scope_block(supabase, r.get("id"), now_dt)
        out.append(PagePostSummary(
            id=str(r.get("id") or ""),
            platform=str(r.get("platform") or ""),
            page_id=str(r.get("page_id") or ""),
            post_id=str(r.get("post_id") or ""),
            permalink_url=r.get("permalink_url"),
            posted_at=str(r.get("posted_at") or ""),
            source_type=str(r.get("source_type") or "unknown"),
            title=_shorten_title(r.get("caption_text")),
            linked_web_codes=[c for c in web_codes if c],
            linked_tour_codes_real=[c for c in real_codes if c],
            is_post_blocked=post_block is not None,
            block_status=(post_block or {}).get("status") if post_block else None,
        ))
    return out


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


def link_page_post_to_tour(
    supabase: SupabaseLike,
    *,
    page_post_id: str,
    web_code: Optional[str] = None,
    tour_code_real: Optional[str] = None,
    tour_id: Optional[str] = None,
    confidence: float = 0.7,
    status: str = "active",
    meta: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> PagePostTourLink:
    """
    Idempotently link a page post to a tour. At least one of (web_code,
    tour_code_real, tour_id) is required. Calling again with the same
    (post_id, web_code) tuple updates confidence/status in place.
    """
    if not page_post_id:
        raise ValueError("link_page_post_to_tour: page_post_id required")
    if not (web_code or tour_code_real or tour_id):
        raise ValueError(
            "link_page_post_to_tour: provide at least one of web_code / "
            "tour_code_real / tour_id"
        )
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("link_page_post_to_tour: confidence must be in [0, 1]")
    if status not in {"active", "dismissed", "superseded"}:
        raise ValueError(f"link_page_post_to_tour: invalid status {status!r}")

    normalized_web = normalize_web_code(web_code) if web_code else None
    normalized_real = normalize_tour_code_real(tour_code_real) if tour_code_real else None
    if web_code and not normalized_web:
        raise ValueError(f"link_page_post_to_tour: invalid web_code {web_code!r}")
    if tour_code_real and not normalized_real:
        raise ValueError(
            f"link_page_post_to_tour: invalid tour_code_real {tour_code_real!r}"
        )
    if normalized_web and normalized_real and normalized_web == normalized_real:
        raise ValueError(
            "link_page_post_to_tour: web_code and tour_code_real must differ"
        )

    now_iso = _iso(_now(now))

    # Build a deterministic existing-match using the same uniqueness as the
    # migration's partial unique indexes.
    existing: Optional[dict] = None
    candidates = _select_all(supabase, "page_post_tour_links", {
        "page_post_id": page_post_id,
    })
    for cand in candidates:
        if normalized_web and cand.get("web_code") == normalized_web:
            existing = cand
            break
        if normalized_real and cand.get("tour_code_real") == normalized_real:
            existing = cand
            break
        if tour_id and cand.get("tour_id") == tour_id:
            existing = cand
            break

    patch_common = {
        "web_code": normalized_web,
        "tour_code_real": normalized_real,
        "tour_id": tour_id,
        "confidence": float(confidence),
        "status": status,
        "meta": meta or {},
    }
    if existing:
        supabase.table("page_post_tour_links").update(
            {"id": existing["id"]}, patch_common,
        )
        row = supabase.table("page_post_tour_links").select_one(
            {"id": existing["id"]}
        ) or {}
        return PagePostTourLink(
            id=str(row.get("id") or existing["id"]),
            page_post_id=str(row.get("page_post_id") or page_post_id),
            web_code=row.get("web_code"),
            tour_code_real=row.get("tour_code_real"),
            tour_id=row.get("tour_id"),
            confidence=float(row.get("confidence", confidence)),
            status=str(row.get("status") or status),
            detected_at=str(row.get("detected_at") or now_iso),
            inserted=False,
        )

    new_id = str(uuid.uuid4())
    supabase.table("page_post_tour_links").insert({
        "id": new_id,
        "page_post_id": page_post_id,
        "detected_at": now_iso,
        **patch_common,
    })
    return PagePostTourLink(
        id=new_id,
        page_post_id=page_post_id,
        web_code=normalized_web,
        tour_code_real=normalized_real,
        tour_id=tour_id,
        confidence=float(confidence),
        status=status,
        detected_at=now_iso,
        inserted=True,
    )


def link_page_post_from_text(
    supabase: SupabaseLike,
    *,
    page_post_id: str,
    text: Optional[str],
    confidence: float = 0.6,
    now: Optional[datetime] = None,
) -> list[PagePostTourLink]:
    """Extract refs from `text` and idempotently create one link per code."""
    refs = extract_tour_references(text)
    links: list[PagePostTourLink] = []
    for wc in refs.web_codes:
        links.append(link_page_post_to_tour(
            supabase,
            page_post_id=page_post_id,
            web_code=wc,
            confidence=confidence,
            now=now,
        ))
    for rc in refs.tour_codes_real:
        # Skip if equal to any web code (shouldn't happen — different shape).
        if rc in refs.web_codes:
            continue
        links.append(link_page_post_to_tour(
            supabase,
            page_post_id=page_post_id,
            tour_code_real=rc,
            confidence=confidence,
            now=now,
        ))
    return links


# ---------------------------------------------------------------------------
# Sold-out / full override (mark / clear)
# ---------------------------------------------------------------------------


def _validate_marker(
    *,
    scope: str,
    web_code: Optional[str],
    tour_code_real: Optional[str],
    tour_id: Optional[str],
    page_post_id: Optional[str],
    departure_date: Optional[date],
) -> tuple[Optional[str], Optional[str]]:
    if scope not in _OVERRIDE_SCOPES:
        raise ValueError(f"scope must be one of {sorted(_OVERRIDE_SCOPES)}")
    normalized_web = normalize_web_code(web_code) if web_code else None
    normalized_real = normalize_tour_code_real(tour_code_real) if tour_code_real else None
    if web_code and not normalized_web:
        raise ValueError(f"invalid web_code {web_code!r}")
    if tour_code_real and not normalized_real:
        raise ValueError(f"invalid tour_code_real {tour_code_real!r}")

    if scope == "post":
        if not page_post_id:
            raise ValueError("scope='post' requires page_post_id")
    else:
        if not (normalized_web or normalized_real or tour_id):
            raise ValueError(
                f"scope='{scope}' requires at least one of "
                "web_code / tour_code_real / tour_id"
            )
    if scope == "departure" and departure_date is None:
        raise ValueError("scope='departure' requires departure_date")
    if normalized_web and normalized_real and normalized_web == normalized_real:
        raise ValueError("web_code and tour_code_real must differ")
    return normalized_web, normalized_real


def mark_availability_override(
    supabase: SupabaseLike,
    *,
    scope: str,
    status: str,
    marked_by: str,
    web_code: Optional[str] = None,
    tour_code_real: Optional[str] = None,
    tour_id: Optional[str] = None,
    page_post_id: Optional[str] = None,
    departure_date: Optional[date] = None,
    reason: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    meta: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> AvailabilityOverride:
    """
    Mark a tour / departure / post as sold_out / full / unknown / available.

    "Active" override semantics: a row with cleared_at IS NULL AND (expires_at
    IS NULL OR expires_at > now). If an active row already exists for the same
    (scope, target [, departure_date]), it is REPLACED (cleared then a new row
    inserted) — preserving audit history.
    """
    if not marked_by:
        raise ValueError("mark_availability_override: marked_by is required")
    if status not in _OVERRIDE_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_OVERRIDE_STATUSES)}; got {status!r}"
        )
    normalized_web, normalized_real = _validate_marker(
        scope=scope, web_code=web_code, tour_code_real=tour_code_real,
        tour_id=tour_id, page_post_id=page_post_id,
        departure_date=departure_date,
    )
    now_dt = _now(now)
    now_iso = _iso(now_dt)

    # Clear any existing active override on the same target+scope tuple.
    existing_active = _find_active_override_for_target(
        supabase,
        scope=scope,
        web_code=normalized_web,
        tour_code_real=normalized_real,
        tour_id=tour_id,
        page_post_id=page_post_id,
        departure_date=departure_date,
        now_dt=now_dt,
    )
    if existing_active:
        supabase.table("tour_availability_overrides").update(
            {"id": existing_active["id"]},
            {"cleared_at": now_iso, "cleared_by": marked_by},
        )

    new_id = str(uuid.uuid4())
    row = {
        "id": new_id,
        "scope": scope,
        "status": status,
        "web_code": normalized_web,
        "tour_code_real": normalized_real,
        "tour_id": tour_id,
        "page_post_id": page_post_id,
        "departure_date": departure_date.isoformat() if departure_date else None,
        "reason": reason,
        "marked_by": marked_by,
        "marked_at": now_iso,
        "expires_at": _iso(expires_at) if expires_at else None,
        "cleared_at": None,
        "cleared_by": None,
        "meta": meta or {},
    }
    supabase.table("tour_availability_overrides").insert(row)

    logger.info(
        "page_post_context.mark_override scope=%s status=%s by=%s "
        "web=%s real=%s tour_id=%s post_id=%s date=%s",
        scope, status, redactor.redact(marked_by),
        normalized_web, normalized_real, tour_id, page_post_id,
        row["departure_date"],
    )
    return AvailabilityOverride(
        id=new_id, scope=scope, status=status,
        web_code=normalized_web, tour_code_real=normalized_real,
        tour_id=tour_id, page_post_id=page_post_id,
        departure_date=row["departure_date"],
        reason=reason, marked_by=marked_by, marked_at=now_iso,
        expires_at=row["expires_at"],
    )


def clear_availability_override(
    supabase: SupabaseLike,
    *,
    cleared_by: str,
    scope: Optional[str] = None,
    web_code: Optional[str] = None,
    tour_code_real: Optional[str] = None,
    tour_id: Optional[str] = None,
    page_post_id: Optional[str] = None,
    departure_date: Optional[date] = None,
    override_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> int:
    """
    Clear an active sold_out / full override. Returns the number of rows
    cleared.

    Caller may pass `override_id` to clear exactly one row, or pass scope +
    target identifiers to clear whatever active row matches.
    """
    if not cleared_by:
        raise ValueError("clear_availability_override: cleared_by is required")
    now_dt = _now(now)
    now_iso = _iso(now_dt)

    if override_id:
        row = supabase.table("tour_availability_overrides").select_one(
            {"id": override_id}
        )
        if not row or not _override_active(row, now_dt):
            return 0
        supabase.table("tour_availability_overrides").update(
            {"id": row["id"]},
            {"cleared_at": now_iso, "cleared_by": cleared_by},
        )
        return 1

    if not scope:
        raise ValueError(
            "clear_availability_override: pass override_id OR scope + target"
        )
    normalized_web = normalize_web_code(web_code) if web_code else None
    normalized_real = normalize_tour_code_real(tour_code_real) if tour_code_real else None

    target = _find_active_override_for_target(
        supabase,
        scope=scope,
        web_code=normalized_web,
        tour_code_real=normalized_real,
        tour_id=tour_id,
        page_post_id=page_post_id,
        departure_date=departure_date,
        now_dt=now_dt,
    )
    if not target:
        return 0
    supabase.table("tour_availability_overrides").update(
        {"id": target["id"]},
        {"cleared_at": now_iso, "cleared_by": cleared_by},
    )
    return 1


def _find_active_override_for_target(
    supabase: SupabaseLike,
    *,
    scope: str,
    web_code: Optional[str],
    tour_code_real: Optional[str],
    tour_id: Optional[str],
    page_post_id: Optional[str],
    departure_date: Optional[date],
    now_dt: datetime,
) -> Optional[dict]:
    rows = _select_all(supabase, "tour_availability_overrides", {"scope": scope})
    for r in rows:
        if not _override_active(r, now_dt):
            continue
        if scope == "post":
            if r.get("page_post_id") == page_post_id:
                return r
            continue
        # tour or departure scope
        if scope == "departure":
            dep = departure_date.isoformat() if departure_date else None
            if r.get("departure_date") != dep:
                continue
        if web_code and r.get("web_code") == web_code:
            return r
        if tour_code_real and r.get("tour_code_real") == tour_code_real:
            return r
        if tour_id and r.get("tour_id") == tour_id:
            return r
    return None


# ---------------------------------------------------------------------------
# Block decision
# ---------------------------------------------------------------------------


def _post_scope_block(
    supabase: SupabaseLike, page_post_id: Optional[str], now_dt: datetime,
) -> Optional[dict]:
    if not page_post_id:
        return None
    rows = _select_all(supabase, "tour_availability_overrides", {
        "scope": "post", "page_post_id": page_post_id,
    })
    for r in rows:
        if not _override_active(r, now_dt):
            continue
        if r.get("status") in _BLOCKING_STATUSES:
            return r
    return None


def _reason_for(scope: str, status: str) -> Optional[str]:
    if status not in _BLOCKING_STATUSES:
        return None
    if scope == "departure":
        return REASON_DEPARTURE_FULL
    if scope == "post":
        return REASON_POST_FULL
    return REASON_TOUR_FULL


def is_candidate_blocked(
    supabase: SupabaseLike,
    *,
    web_code: Optional[str] = None,
    tour_code_real: Optional[str] = None,
    tour_id: Optional[str] = None,
    page_post_id: Optional[str] = None,
    departure_date: Optional[date] = None,
    now: Optional[datetime] = None,
) -> BlockDecision:
    """
    Decide whether a candidate tour is blocked by an active override.

    Precedence:
        1. Departure-scope override matching (target, departure_date)
        2. Post-scope override matching page_post_id
        3. Tour-scope override matching (web_code / tour_code_real / tour_id)
    """
    if not any((web_code, tour_code_real, tour_id, page_post_id)):
        return BlockDecision(False, None, None, None)
    now_dt = _now(now)
    normalized_web = normalize_web_code(web_code) if web_code else None
    normalized_real = normalize_tour_code_real(tour_code_real) if tour_code_real else None

    # 1. departure
    if departure_date is not None:
        dep_row = _find_active_override_for_target(
            supabase, scope="departure",
            web_code=normalized_web, tour_code_real=normalized_real,
            tour_id=tour_id, page_post_id=None,
            departure_date=departure_date, now_dt=now_dt,
        )
        if dep_row and dep_row.get("status") in _BLOCKING_STATUSES:
            return BlockDecision(
                is_blocked=True,
                status=dep_row.get("status"),
                scope="departure",
                reason_text=_reason_for("departure", dep_row.get("status") or ""),
                matched_override_id=str(dep_row.get("id") or ""),
            )

    # 2. post scope
    post_row = _post_scope_block(supabase, page_post_id, now_dt)
    if post_row:
        return BlockDecision(
            is_blocked=True,
            status=post_row.get("status"),
            scope="post",
            reason_text=_reason_for("post", post_row.get("status") or ""),
            matched_override_id=str(post_row.get("id") or ""),
        )

    # 3. tour scope
    tour_row = _find_active_override_for_target(
        supabase, scope="tour",
        web_code=normalized_web, tour_code_real=normalized_real,
        tour_id=tour_id, page_post_id=None,
        departure_date=None, now_dt=now_dt,
    )
    if tour_row and tour_row.get("status") in _BLOCKING_STATUSES:
        return BlockDecision(
            is_blocked=True,
            status=tour_row.get("status"),
            scope="tour",
            reason_text=_reason_for("tour", tour_row.get("status") or ""),
            matched_override_id=str(tour_row.get("id") or ""),
        )

    return BlockDecision(False, None, None, None)


# ---------------------------------------------------------------------------
# Source context + planning bundle
# ---------------------------------------------------------------------------


def _page_post_row(
    supabase: SupabaseLike,
    *,
    post_id: Optional[str] = None,
    platform: str = "facebook",
    row_id: Optional[str] = None,
) -> Optional[dict]:
    if row_id:
        return supabase.table("page_posts").select_one({"id": row_id})
    if post_id:
        return supabase.table("page_posts").select_one(
            {"platform": platform, "post_id": post_id}
        )
    return None


def get_source_context(
    supabase: SupabaseLike,
    *,
    source_type: Optional[str] = None,
    post_id: Optional[str] = None,
    page_post_id: Optional[str] = None,
    platform: str = "facebook",
    days: int = DEFAULT_RECENT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> SourceContext:
    """
    Build a compact source-context view. Caller passes whatever info Meta /
    webhook attribution gave them (post_id, source_type) and gets back a
    deterministic, LLM-safe bundle.

    - When source_type is omitted, it is inferred:
        * post_id matches an active page_post row → 'page_post'
        * otherwise 'unknown'
    """
    now_dt = _now(now)
    row = _page_post_row(supabase, post_id=post_id, platform=platform, row_id=page_post_id)

    inferred = source_type
    if inferred is None:
        inferred = "page_post" if row else "unknown"
    _validate_source_type(inferred)

    if not row:
        return SourceContext(
            source_type=inferred,
            page_post_id=None,
            permalink_url=None,
            posted_at=None,
            title=None,
            linked_web_codes=[],
            linked_tour_codes_real=[],
            is_recent=False,
        )

    links = _select_all(supabase, "page_post_tour_links", {
        "page_post_id": row.get("id"), "status": "active",
    })
    web_codes = sorted({l.get("web_code") for l in links if l.get("web_code")})
    real_codes = sorted({l.get("tour_code_real") for l in links if l.get("tour_code_real")})

    # If a source_type was passed explicitly, prefer it; otherwise prefer the
    # row's stored source_type, falling back to inferred.
    final_source = source_type or row.get("source_type") or inferred
    _validate_source_type(final_source)

    return SourceContext(
        source_type=final_source,
        page_post_id=str(row.get("id") or ""),
        permalink_url=row.get("permalink_url"),
        posted_at=str(row.get("posted_at") or ""),
        title=_shorten_title(row.get("caption_text")),
        linked_web_codes=[c for c in web_codes if c],
        linked_tour_codes_real=[c for c in real_codes if c],
        is_recent=_is_recent(row, now_dt, days),
    )


def build_response_planning_context(
    supabase: SupabaseLike,
    *,
    candidate_web_code: Optional[str] = None,
    candidate_tour_code_real: Optional[str] = None,
    candidate_tour_id: Optional[str] = None,
    candidate_departure_date: Optional[date] = None,
    source_post_id: Optional[str] = None,
    source_page_post_id: Optional[str] = None,
    source_type: Optional[str] = None,
    source_platform: str = "facebook",
    days: int = DEFAULT_RECENT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> PlanningContext:
    """
    Single entrypoint for the response planner.

    Returns the compact source summary, a deterministic block decision, a
    replacement_needed signal (True iff the candidate is blocked), and a safe
    Thai admin/bot reason text.

    The caller (response writer / orchestrator) is expected to pass through
    `safe_reason_text` to a canned reply rather than concatenating into the
    LLM prompt; this preserves the invariant that the LLM never decides
    sold-out semantics.
    """
    source = get_source_context(
        supabase,
        source_type=source_type,
        post_id=source_post_id,
        page_post_id=source_page_post_id,
        platform=source_platform,
        days=days,
        now=now,
    )

    block = is_candidate_blocked(
        supabase,
        web_code=candidate_web_code,
        tour_code_real=candidate_tour_code_real,
        tour_id=candidate_tour_id,
        page_post_id=source.page_post_id,
        departure_date=candidate_departure_date,
        now=now,
    )

    replacement_needed = bool(block.is_blocked)

    if block.is_blocked and block.reason_text:
        safe = block.reason_text
    elif source.source_type == "page_post" and source.title:
        safe = REASON_AVAILABLE_FROM_POST
    else:
        safe = None
    if safe and len(safe) > CONTEXT_REASON_MAX_CHARS:
        safe = safe[: CONTEXT_REASON_MAX_CHARS - 1].rstrip() + "…"
    safe_reason_text = _safe_text(safe) if safe else None

    return PlanningContext(
        source=source,
        block=block,
        replacement_needed=replacement_needed,
        safe_reason_text=safe_reason_text,
    )


__all__ = [
    # constants
    "DEFAULT_RECENT_WINDOW_DAYS",
    "MAX_RECENT_WINDOW_DAYS",
    "CONTEXT_TITLE_MAX_CHARS",
    "CONTEXT_REASON_MAX_CHARS",
    "REASON_TOUR_FULL",
    "REASON_DEPARTURE_FULL",
    "REASON_POST_FULL",
    "REASON_AVAILABLE_FROM_POST",
    # dataclasses
    "ExtractedRefs",
    "PagePostRecord",
    "PagePostSummary",
    "PagePostTourLink",
    "AvailabilityOverride",
    "BlockDecision",
    "SourceContext",
    "PlanningContext",
    # functions
    "extract_tour_references",
    "upsert_page_post",
    "list_recent_page_posts",
    "link_page_post_to_tour",
    "link_page_post_from_text",
    "mark_availability_override",
    "clear_availability_override",
    "is_candidate_blocked",
    "get_source_context",
    "build_response_planning_context",
]
