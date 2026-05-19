"""
v2.lib.source_attribution — DEV-2026-05-19-008.

Deterministic source-attribution adapter. Inspects a Meta-like (Messenger /
Instagram / LINE) webhook event and decides whether the conversation came
from:

    * 'page_post' — customer replied/commented on a page post or clicked
      a m.me/ig.me link tied to a known page_post row.
    * 'ad'       — customer engaged with an Ads-attached event
      (`referral.source == 'ADS'` / `ref` containing an ad / click-to-msg id).
    * 'organic'  — customer engaged through a direct, non-ad page entry
      (Page profile, search, manual DM) with no post/ad reference.
    * 'unknown'  — no signal at all OR the signal cannot be validated.

The adapter NEVER calls Meta Graph API. Validation of `post_id` is purely
DB-side via `v2.lib.page_post_context._page_post_row` — an arbitrary user-
typed string can never become a `page_post_id` unless that post is already
in the V2 `page_posts` table.

Public API:

    extract_source(event, supabase) -> SourceAttribution

`SourceAttribution.to_orchestrator_kwargs()` returns the kwargs accepted by
`Orchestrator.handle_turn(..., source_post_id=..., source_type=...,
source_platform=...)`. Unknown / absent source preserves current behaviour
(all three kwargs default).

Hard rules:

    - No live Meta / FB / Instagram / LINE / OpenAI / paid-provider calls.
    - No env reads. Caller passes the event payload.
    - User-typed text is never trusted as a post id. Only Meta-supplied
      `message.reply_to.story.id`, `message.reply_to.story_id`, `postback`,
      `referral`, `entry.changes.value.post_id`, `comment_id`, and similar
      Meta-attribution fields are inspected.
    - Caption text is never extracted here; only ids/types/refs.
    - Wholesale partner names are never inspected — this module only reads
      ids and source-type tokens.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional

from .page_post_context import _page_post_row

logger = logging.getLogger("v2.source_attribution")

# All valid source types in the V2 contract (matches page_post_context).
_SOURCE_TYPES = ("page_post", "ad", "organic", "unknown")

# Valid source platforms — additive: future LINE OA support uses 'line'.
_SOURCE_PLATFORMS = ("facebook", "instagram", "line")

# Conservative ceiling — Meta post ids are e.g. "<page_id>_<post_id>" or
# "fb_<...>". An attacker-controlled, user-typed string longer than this is
# either junk or an injection probe; refuse it.
_MAX_REF_ID_LEN = 200


@dataclass(frozen=True)
class SourceAttribution:
    """
    Deterministic source signal extracted from a webhook event.

    `page_post_validated` is True iff the candidate `post_id` matched an
    actual row in `page_posts`. When False, the adapter falls back to
    'unknown' so the planner cannot be tricked into trusting an arbitrary
    id.
    """
    source_type: str               # one of _SOURCE_TYPES
    source_post_id: Optional[str]
    source_platform: str           # one of _SOURCE_PLATFORMS
    page_post_id: Optional[str] = None  # internal DB uuid when validated
    raw_ref: Optional[str] = None       # Meta-supplied `ref` token, for log
    page_post_validated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def to_orchestrator_kwargs(self) -> dict:
        """
        Return only the kwargs `Orchestrator.handle_turn(...)` accepts.

        Notably we DO NOT pass `source_post_id` upstream when the post id
        could not be validated against `page_posts`. The orchestrator's
        planner uses `source_post_id` to look up post-scoped overrides —
        passing an unverified id risks leaking that we don't own that post
        or, worse, matching another platform's id collision. So unverified
        ids are dropped at the boundary.
        """
        kwargs = {
            "source_type": self.source_type,
            "source_platform": self.source_platform,
        }
        if self.page_post_validated and self.source_post_id:
            kwargs["source_post_id"] = self.source_post_id
        else:
            kwargs["source_post_id"] = None
        return kwargs


def _safe_str(val: Any) -> Optional[str]:
    """Return a sanitised, length-capped string or None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if len(s) > _MAX_REF_ID_LEN:
        return None
    # Block obvious junk / whitespace-only / control chars.
    if any(c in s for c in ("\n", "\r", "\t")):
        return None
    return s


def _detect_platform(event: dict) -> str:
    """
    Infer source platform from event shape. Defaults to 'facebook' because
    historical V2 webhooks are Messenger; explicit `platform` / `object`
    hints in the event override.
    """
    if not isinstance(event, dict):
        return "facebook"
    plat = event.get("platform")
    if isinstance(plat, str):
        s = plat.lower().strip()
        if s in _SOURCE_PLATFORMS:
            return s
    obj = event.get("object")
    if isinstance(obj, str):
        s = obj.lower().strip()
        if s in ("instagram", "ig"):
            return "instagram"
        if s == "line":
            return "line"
    src = event.get("source")
    if isinstance(src, str):
        s = src.lower().strip()
        if s in _SOURCE_PLATFORMS:
            return s
    return "facebook"


def _extract_candidate_post_id(event: dict) -> Optional[str]:
    """
    Pull a candidate page-post id out of Meta-supplied fields.

    Order is conservative — strongest signal first:

        1. `message.reply_to.story.id` or `.story_id` (IG reply-to-story)
        2. `postback.payload` when it looks like a post-id ref
        3. `referral.ref` (m.me/ig.me click-to-message)
        4. `entry.changes.value.post_id` (Page comment webhook shape)
        5. Top-level `source_post_id` (caller-provided)
    """
    if not isinstance(event, dict):
        return None

    # 1. message.reply_to
    msg = event.get("message") or {}
    if isinstance(msg, dict):
        reply_to = msg.get("reply_to") or {}
        if isinstance(reply_to, dict):
            story = reply_to.get("story") or {}
            if isinstance(story, dict):
                cand = _safe_str(story.get("id"))
                if cand:
                    return cand
            cand = _safe_str(reply_to.get("story_id"))
            if cand:
                return cand

    # 2. postback.payload (only when shaped like a post ref).
    postback = event.get("postback") or {}
    if isinstance(postback, dict):
        payload = postback.get("payload")
        s = _safe_str(payload)
        if s and (s.startswith("POST:") or s.startswith("post:")):
            return s.split(":", 1)[1]

    # 3. referral.ref (m.me/?ref=...).
    referral = event.get("referral") or {}
    if not isinstance(referral, dict):
        referral = {}
    # Also check inside message.referral / postback.referral
    if not referral and isinstance(msg, dict):
        referral = msg.get("referral") or {}
    if not isinstance(referral, dict):
        referral = {}
    ref_value = _safe_str(referral.get("ref"))
    if ref_value and (ref_value.startswith("POST:") or ref_value.startswith("post:")):
        return ref_value.split(":", 1)[1]

    # 4. comments webhook shape
    changes_value = (event.get("value") or {}) if isinstance(event, dict) else {}
    if isinstance(changes_value, dict):
        cand = _safe_str(changes_value.get("post_id"))
        if cand:
            return cand

    # 5. Caller-provided top-level (Meta sometimes routes via app-specific
    # serialisation that already pulled the id).
    cand = _safe_str(event.get("source_post_id"))
    if cand:
        return cand

    return None


def _detect_ad_signal(event: dict) -> tuple:
    """
    Return (is_ad, ad_ref). Looks for explicit Ads attribution fields.

    Meta puts ad attribution in:
        - referral.source == 'ADS' or 'CTM_ADS' or 'IG_CTM_ADS'
        - referral.ad_id present
        - postback.payload starts with 'AD:' (custom convention used by
          test fixtures / internal ad routers)
    """
    if not isinstance(event, dict):
        return False, None

    referral = event.get("referral") or {}
    if not isinstance(referral, dict):
        referral = {}
    msg = event.get("message") or {}
    if isinstance(msg, dict) and not referral:
        referral = msg.get("referral") or {}
        if not isinstance(referral, dict):
            referral = {}

    src = (referral.get("source") or "")
    if isinstance(src, str):
        src_upper = src.upper()
    else:
        src_upper = ""
    if src_upper in ("ADS", "CTM_ADS", "IG_CTM_ADS", "FACEBOOK_ADS"):
        return True, _safe_str(referral.get("ad_id") or referral.get("ref"))
    if _safe_str(referral.get("ad_id")):
        return True, _safe_str(referral.get("ad_id"))

    postback = event.get("postback") or {}
    if isinstance(postback, dict):
        payload = _safe_str(postback.get("payload"))
        if payload and (payload.startswith("AD:") or payload.startswith("ad:")):
            return True, (payload.split(":", 1)[1] or None)

    return False, None


def _detect_explicit_source_type(event: dict) -> Optional[str]:
    """
    Honour a caller-provided `source_type` only if it is a valid token.
    """
    if not isinstance(event, dict):
        return None
    explicit = event.get("source_type")
    if isinstance(explicit, str):
        s = explicit.lower().strip()
        if s in _SOURCE_TYPES:
            return s
    return None


def _detect_organic_signal(event: dict) -> bool:
    """
    Decide if this is an 'organic' page entry — i.e. there is *some* page
    contact event but no post/ad attribution. We treat the presence of
    standard messaging shape as organic if no post/ad signal was found.
    """
    if not isinstance(event, dict):
        return False
    if event.get("message") or event.get("postback") or event.get("referral"):
        return True
    if event.get("sender"):
        return True
    return False


def extract_source(event: Any, supabase: Any) -> SourceAttribution:
    """
    Inspect a webhook event and return a SourceAttribution.

    `event` is the raw messaging event (the dict inside `entry.messaging[]`
    for Messenger, or an equivalent IG / LINE shape). `supabase` is the
    Supabase-like used for page_posts validation. Never raises — on any
    parse error, returns ('unknown', None, default_platform).
    """
    try:
        if not isinstance(event, dict):
            return SourceAttribution(
                source_type="unknown",
                source_post_id=None,
                source_platform="facebook",
            )

        platform = _detect_platform(event)
        explicit = _detect_explicit_source_type(event)
        candidate_post_id = _extract_candidate_post_id(event)
        is_ad, ad_ref = _detect_ad_signal(event)

        page_post_id: Optional[str] = None
        validated = False
        if candidate_post_id and supabase is not None:
            try:
                lookup_platform = "facebook" if platform == "facebook" else platform
                row = _page_post_row(
                    supabase,
                    post_id=candidate_post_id,
                    platform=lookup_platform,
                )
                if row:
                    page_post_id = str(row.get("id") or "") or None
                    validated = True
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "source_attribution: page_post lookup failed: %s", e,
                )

        # Resolution order:
        #   1. Explicit caller-provided source_type wins iff it is valid.
        #   2. Validated post_id → page_post.
        #   3. Ad signal → ad.
        #   4. Organic page-entry signal → organic.
        #   5. Otherwise unknown.
        if explicit:
            final_type = explicit
            # If caller explicitly said 'page_post' but the post couldn't be
            # validated, downgrade to 'unknown' so we don't claim provenance.
            if final_type == "page_post" and not validated:
                final_type = "unknown"
        elif validated:
            final_type = "page_post"
        elif is_ad:
            final_type = "ad"
        elif _detect_organic_signal(event):
            final_type = "organic"
        else:
            final_type = "unknown"

        return SourceAttribution(
            source_type=final_type,
            source_post_id=candidate_post_id if validated else None,
            source_platform=platform,
            page_post_id=page_post_id,
            raw_ref=ad_ref if is_ad else None,
            page_post_validated=validated,
        )
    except Exception as e:
        logger.warning("source_attribution.extract_source crashed: %s", e)
        return SourceAttribution(
            source_type="unknown",
            source_post_id=None,
            source_platform="facebook",
        )


__all__ = [
    "SourceAttribution",
    "extract_source",
]
