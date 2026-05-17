"""
v2.lib.memory — Memory layer for TourFireMai V2

Implements `V2_MEMORY_SPEC.md`:
    - 3-layer memory (Redis hot / Supabase warm+cold)
    - Write-through: Supabase first, Redis mirror best-effort
    - Offer snapshots: deterministic resolver for "ตัวที่ N" / web_code / price / city

Public API (matches Sprint 1 brief):
    get_customer_memory(psid)
    update_customer_memory(psid, patch, reason)
    get_latest_offer_snapshot(psid)
    save_offer_snapshot(psid, options, search_context)
    lock_selected_tour(psid, tour)
    get_selected_tour(psid)
    clear_selected_tour(psid, reason)
    resolve_tour_selection(text, offer_snapshot)
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

# --- Protocols ----------------------------------------------------------------

class SupabaseLike(Protocol):
    def table(self, name: str): ...  # returns query builder

class RedisLike(Protocol):
    def set(self, key: str, value: str, *, nx: bool = False, ex: Optional[int] = None) -> bool: ...
    def get(self, key: str) -> Optional[str]: ...
    def delete(self, key: str) -> int: ...
    def setex(self, key: str, ttl: int, value: str) -> bool: ...


# --- Models -------------------------------------------------------------------

@dataclass
class CustomerMemoryView:
    customer_id: Optional[str] = None
    psid: str = ""
    customer_name: Optional[str] = None
    latest_country: Optional[str] = None
    latest_city: Optional[str] = None
    budget_per_person: Optional[int] = None
    budget_type: Optional[str] = None
    travel_month: Optional[str] = None
    pax_count: Optional[int] = None
    airline_preference: Optional[str] = None
    selected_tour_web_code: Optional[str] = None
    selected_tour_code_real: Optional[str] = None
    latest_offer_set_id: Optional[str] = None
    conversation_state: Optional[str] = None
    updated_at: Optional[str] = None
    is_returning_customer: bool = False

    @classmethod
    def empty(cls, psid: str) -> "CustomerMemoryView":
        return cls(psid=psid)


@dataclass
class TourOption:
    rank: int
    web_code: str
    tour_code_real: Optional[str]
    name: str
    price: int
    days: int
    airline: Optional[str]
    departure_dates: list[str] = field(default_factory=list)
    url: Optional[str] = None
    tier: Optional[str] = None
    city_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "web_code": self.web_code,
            "tour_code_real": self.tour_code_real,
            "name": self.name,
            "price": self.price,
            "days": self.days,
            "airline": self.airline,
            "departure_dates": self.departure_dates,
            "url": self.url,
            "tier": self.tier,
            "city_tags": self.city_tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TourOption":
        return cls(
            rank=int(d.get("rank", 0)),
            web_code=str(d.get("web_code") or ""),
            tour_code_real=d.get("tour_code_real"),
            name=str(d.get("name") or ""),
            price=int(d.get("price") or 0),
            days=int(d.get("days") or 0),
            airline=d.get("airline"),
            departure_dates=list(d.get("departure_dates") or []),
            url=d.get("url"),
            tier=d.get("tier"),
            city_tags=list(d.get("city_tags") or []),
        )


@dataclass
class OfferSnapshot:
    id: str
    conversation_id: Optional[str]
    psid: str
    presented_at: str
    context: dict
    tour_list: list[TourOption]
    was_selected: bool = False
    selected_rank: Optional[int] = None
    selected_tour_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "psid": self.psid,
            "presented_at": self.presented_at,
            "context": self.context,
            "tour_list": [t.to_dict() for t in self.tour_list],
            "was_selected": self.was_selected,
            "selected_rank": self.selected_rank,
            "selected_tour_id": self.selected_tour_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OfferSnapshot":
        return cls(
            id=str(d["id"]),
            conversation_id=d.get("conversation_id"),
            psid=str(d["psid"]),
            presented_at=str(d.get("presented_at", "")),
            context=dict(d.get("context") or {}),
            tour_list=[TourOption.from_dict(t) for t in (d.get("tour_list") or [])],
            was_selected=bool(d.get("was_selected", False)),
            selected_rank=d.get("selected_rank"),
            selected_tour_id=d.get("selected_tour_id"),
        )


@dataclass
class SelectedTourLock:
    id: str
    psid: str
    conversation_id: Optional[str]
    tour_id: str
    web_code: str
    tour_code_real: Optional[str]
    name: str
    price: int
    selected_at: str
    is_locked: bool = True


# --- Helpers ------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis_set_json(redis: Optional[RedisLike], key: str, value: dict, ttl: int) -> None:
    if not redis:
        return
    try:
        if hasattr(redis, "setex"):
            redis.setex(key, ttl, json.dumps(value, default=str))
        else:
            redis.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        # Best-effort mirror. Never block on Redis.
        pass


def _redis_get_json(redis: Optional[RedisLike], key: str) -> Optional[dict]:
    if not redis:
        return None
    try:
        raw = redis.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    except Exception:
        return None


# --- MemoryService ------------------------------------------------------------

class MemoryService:
    """
    Combined orchestrator over Supabase + Redis. Stateless wrapper.

    Tests inject InMemorySupabase + InMemoryRedis. Production uses real clients.
    """

    HOT_TTL_S = 60
    OFFER_TTL_S = 86400
    SELECTED_TTL_S = 86400

    def __init__(self, supabase: SupabaseLike, redis: Optional[RedisLike] = None):
        self.sb = supabase
        self.redis = redis

    # -- Customer Memory ------------------------------------------------------

    def get_customer_memory(self, psid: str) -> CustomerMemoryView:
        # 1) Redis fast-path
        cached = _redis_get_json(self.redis, f"mem:customer:{psid}")
        if cached:
            try:
                return CustomerMemoryView(**{k: v for k, v in cached.items() if k in CustomerMemoryView.__annotations__})
            except Exception:
                pass

        # 2) Supabase: join customers + customer_memory
        customer_row = self._select_one("customers", {"psid": psid})
        if not customer_row:
            return CustomerMemoryView.empty(psid)
        cmem_row = self._select_one("customer_memory", {"psid": psid}) or {}

        view = CustomerMemoryView(
            customer_id=customer_row.get("id"),
            psid=psid,
            customer_name=cmem_row.get("customer_name") or customer_row.get("fb_name"),
            latest_country=cmem_row.get("latest_country") or customer_row.get("preferred_country"),
            latest_city=cmem_row.get("latest_city"),
            budget_per_person=cmem_row.get("budget_per_person") or customer_row.get("preferred_budget"),
            budget_type=cmem_row.get("budget_type"),
            travel_month=cmem_row.get("travel_month") or customer_row.get("preferred_period"),
            pax_count=cmem_row.get("pax_count") or customer_row.get("preferred_pax"),
            airline_preference=cmem_row.get("airline_preference") or customer_row.get("preferred_airline"),
            selected_tour_web_code=cmem_row.get("selected_tour_web_code"),
            selected_tour_code_real=cmem_row.get("selected_tour_code_real"),
            latest_offer_set_id=cmem_row.get("latest_offer_set_id"),
            conversation_state=cmem_row.get("conversation_state"),
            updated_at=cmem_row.get("updated_at"),
            is_returning_customer=(int(customer_row.get("total_bookings", 0)) > 0)
            or (str(customer_row.get("customer_tier", "new")) != "new"),
        )

        # Mirror cache
        _redis_set_json(self.redis, f"mem:customer:{psid}", view.__dict__, self.HOT_TTL_S)
        return view

    def update_customer_memory(self, psid: str, patch: dict, reason: str) -> dict:
        """
        Patch mode: routes fields to `customers` (profile/PII) and `customer_memory` (snapshot).

        Returns dict with updated_fields_customers, updated_fields_memory, skipped_fields.
        """
        CUSTOMER_FIELDS = {
            "fb_name", "preferred_country", "preferred_budget", "preferred_pax",
            "preferred_period", "preferred_airline", "tags",
        }
        # `notes` is admin-only — bot writes are blocked
        PROTECTED = {"notes"}
        MEM_FIELDS = {
            "customer_name", "latest_country", "latest_city",
            "budget_per_person", "budget_type", "travel_month",
            "pax_count", "airline_preference",
            "selected_tour_web_code", "selected_tour_code_real",
            "latest_offer_set_id", "conversation_state",
        }

        customer_updates: dict[str, Any] = {}
        mem_updates: dict[str, Any] = {}
        skipped: list[tuple[str, str]] = []

        for k, v in patch.items():
            if k in PROTECTED:
                skipped.append((k, "protected_field_admin_only"))
                continue
            if k in CUSTOMER_FIELDS:
                customer_updates[k] = v
            elif k in MEM_FIELDS:
                mem_updates[k] = v
            else:
                skipped.append((k, "unknown_field"))

        # 1) Ensure customer row exists
        customer_row = self._upsert(
            "customers",
            match={"psid": psid},
            insert={"psid": psid, "fb_name": patch.get("fb_name"), "last_seen_at": _utcnow_iso()},
            update={**customer_updates, "last_seen_at": _utcnow_iso()},
        )
        customer_id = customer_row.get("id")

        # 2) Upsert customer_memory
        if mem_updates or customer_id:
            self._upsert(
                "customer_memory",
                match={"psid": psid},
                insert={
                    "psid": psid,
                    "customer_id": customer_id,
                    **mem_updates,
                    "updated_at": _utcnow_iso(),
                    "updated_by": "bot",
                    "last_update_reason": reason,
                },
                update={
                    **mem_updates,
                    "updated_at": _utcnow_iso(),
                    "updated_by": "bot",
                    "last_update_reason": reason,
                },
            )

        # 3) Invalidate Redis cache for customer
        if self.redis:
            try:
                self.redis.delete(f"mem:customer:{psid}")
            except Exception:
                pass

        return {
            "customer_id": customer_id,
            "updated_fields_customers": list(customer_updates.keys()),
            "updated_fields_memory": list(mem_updates.keys()),
            "skipped_fields": skipped,
        }

    # -- Offer Snapshots ------------------------------------------------------

    def save_offer_snapshot(
        self,
        psid: str,
        options: list[TourOption | dict],
        search_context: dict,
        conversation_id: Optional[str] = None,
    ) -> OfferSnapshot:
        """
        Write-through: Supabase first, then mirror to Redis.

        Snapshot is **immutable** after save. If criteria change, create a new snapshot.
        """
        if not options:
            raise ValueError("save_offer_snapshot: options must not be empty")

        tour_objs = [
            o if isinstance(o, TourOption) else TourOption.from_dict(o) for o in options
        ]

        snap_id = str(uuid.uuid4())
        presented_at = _utcnow_iso()
        row = {
            "id": snap_id,
            "conversation_id": conversation_id,
            "psid": psid,
            "presented_at": presented_at,
            "context": search_context,
            "tour_list": [t.to_dict() for t in tour_objs],
            "was_selected": False,
        }
        self._insert("offer_snapshots", row)

        snap = OfferSnapshot.from_dict(row)
        # Mirror to Redis (24h TTL)
        _redis_set_json(self.redis, f"offer:latest:{psid}", snap.to_dict(), self.OFFER_TTL_S)
        return snap

    def get_latest_offer_snapshot(self, psid: str) -> Optional[OfferSnapshot]:
        cached = _redis_get_json(self.redis, f"offer:latest:{psid}")
        if cached:
            return OfferSnapshot.from_dict(cached)

        row = self._select_latest("offer_snapshots", {"psid": psid}, order_by="presented_at")
        if not row:
            return None
        return OfferSnapshot.from_dict(row)

    # -- Selected Tour Lock ---------------------------------------------------

    def lock_selected_tour(
        self,
        psid: str,
        tour: dict,
        conversation_id: Optional[str] = None,
        from_offer_id: Optional[str] = None,
    ) -> SelectedTourLock:
        """
        Lock customer's tour selection. Unique partial index in DB enforces
        one active lock per PSID. Caller must clear_selected_tour() first
        if a previous lock exists.
        """
        existing = self.get_selected_tour(psid)
        if existing:
            raise ValueError(
                f"PSID {psid!r} already has an active locked tour {existing.tour_id}; "
                f"call clear_selected_tour first"
            )

        # Need customer_id
        cust = self._select_one("customers", {"psid": psid})
        if not cust:
            raise ValueError(f"customers row missing for psid={psid!r}; call update_customer_memory first")

        lock_id = str(uuid.uuid4())
        selected_at = _utcnow_iso()
        row = {
            "id": lock_id,
            "conversation_id": conversation_id,
            "customer_id": cust["id"],
            "psid": psid,
            "tour_id": tour["id"],
            "tour_code_real": tour.get("tour_code_real"),
            "selected_at": selected_at,
            "booking_status": "considering",
            "is_fee_acknowledged": False,
        }
        self._insert("selected_tours", row)

        # Update offer_snapshots was_selected if from_offer_id provided
        if from_offer_id:
            self._update(
                "offer_snapshots",
                {"id": from_offer_id},
                {"was_selected": True, "selected_tour_id": tour["id"]},
            )

        lock = SelectedTourLock(
            id=lock_id,
            psid=psid,
            conversation_id=conversation_id,
            tour_id=str(tour["id"]),
            web_code=str(tour.get("web_code", "")),
            tour_code_real=tour.get("tour_code_real"),
            name=str(tour.get("name", "")),
            price=int(tour.get("price", 0)),
            selected_at=selected_at,
            is_locked=True,
        )

        _redis_set_json(self.redis, f"selected:{psid}", lock.__dict__, self.SELECTED_TTL_S)
        # Mirror to customer_memory snapshot
        try:
            self.update_customer_memory(
                psid,
                {
                    "selected_tour_web_code": lock.web_code,
                    "selected_tour_code_real": lock.tour_code_real,
                },
                reason="tour_locked",
            )
        except Exception:
            pass

        return lock

    def get_selected_tour(self, psid: str) -> Optional[SelectedTourLock]:
        cached = _redis_get_json(self.redis, f"selected:{psid}")
        if cached:
            try:
                return SelectedTourLock(**{k: v for k, v in cached.items() if k in SelectedTourLock.__annotations__})
            except Exception:
                pass

        row = self._select_one("selected_tours", {"psid": psid, "unlocked_at": None})
        if not row:
            return None
        # Join tour_canonical for web_code/name/price if needed
        tour = self._select_one("tours_canonical", {"id": row["tour_id"]}) or {}
        lock = SelectedTourLock(
            id=str(row["id"]),
            psid=psid,
            conversation_id=row.get("conversation_id"),
            tour_id=str(row["tour_id"]),
            web_code=str(tour.get("web_code") or ""),
            tour_code_real=row.get("tour_code_real") or tour.get("tour_code_real"),
            name=str(tour.get("name", "")),
            price=int(tour.get("base_price", 0)),
            selected_at=str(row.get("selected_at", "")),
            is_locked=True,
        )
        return lock

    def clear_selected_tour(self, psid: str, reason: str = "changed_mind") -> None:
        self._update(
            "selected_tours",
            {"psid": psid, "unlocked_at": None},
            {"unlocked_at": _utcnow_iso(), "unlock_reason": reason},
        )
        if self.redis:
            try:
                self.redis.delete(f"selected:{psid}")
            except Exception:
                pass

    # -- Supabase pluggable helpers -------------------------------------------

    def _select_one(self, table: str, where: dict) -> Optional[dict]:
        # Defer to backend
        return self.sb.table(table).select_one(where)

    def _select_latest(self, table: str, where: dict, order_by: str) -> Optional[dict]:
        return self.sb.table(table).select_latest(where, order_by=order_by)

    def _insert(self, table: str, row: dict) -> dict:
        return self.sb.table(table).insert(row)

    def _update(self, table: str, where: dict, patch: dict) -> int:
        return self.sb.table(table).update(where, patch)

    def _upsert(self, table: str, match: dict, insert: dict, update: dict) -> dict:
        return self.sb.table(table).upsert(match=match, insert=insert, update=update)


# --- Offer resolution (deterministic, no LLM) ---------------------------------

# Number words (Thai) → index. "ตัวแรก" = 1, "ตัวที่ 2" = 2, ฯลฯ
_TH_NUM_WORDS: dict[str, int] = {
    "หนึ่ง": 1, "แรก": 1, "ที่หนึ่ง": 1, "ที่1": 1,
    "สอง": 2, "ที่สอง": 2, "ที่2": 2,
    "สาม": 3, "ที่สาม": 3, "ที่3": 3,
    "สี่": 4, "ที่สี่": 4, "ที่4": 4,
    "ห้า": 5, "ที่ห้า": 5, "ที่5": 5,
}

INDEX_RE = re.compile(r"ตัวที่\s*(\d+)|ตัวแรก|อันที่\s*(\d+)|ลำดับที่?\s*(\d+)")
DIGIT_INDEX_RE = re.compile(r"\b(\d+)\b")
PRICE_RE = re.compile(r"([1-9]\d{0,2}(?:[,\s]?\d{3})+|[1-9]\d{3,5})")
WEB_CODE_RE = re.compile(r"\b([a-z]{2,3}\d{5,7})\b")
TOUR_CODE_REAL_RE = re.compile(r"\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+)\b")


def _parse_price(text: str) -> Optional[int]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1)
    clean = raw.replace(",", "").replace(" ", "")
    try:
        n = int(clean)
        if 1000 <= n <= 10_000_000:
            return n
    except ValueError:
        pass
    return None


def _parse_index(text: str) -> Optional[int]:
    if not text:
        return None
    low = text.lower().strip()
    # Thai patterns
    m = INDEX_RE.search(text)
    if m:
        for g in m.groups():
            if g and g.isdigit():
                return int(g)
        if "ตัวแรก" in text:
            return 1
    # Thai number words
    for word, n in _TH_NUM_WORDS.items():
        if word in text:
            return n
    # Bare digit only if short utterance (avoid matching prices)
    if len(low.split()) <= 3:
        m2 = DIGIT_INDEX_RE.search(low)
        if m2:
            try:
                n = int(m2.group(1))
                if 1 <= n <= 9:
                    return n
            except ValueError:
                pass
    return None


@dataclass
class ResolveResult:
    matched: bool
    option: Optional[TourOption] = None
    match_kind: Optional[str] = None  # 'index' | 'web_code' | 'tour_code_real' | 'price' | 'city'
    candidates: list[TourOption] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_reason: Optional[str] = None


def resolve_tour_selection(text: str, snapshot: OfferSnapshot) -> ResolveResult:
    """
    Deterministic resolver. Priority:
        1. web_code (ap242455) — unique
        2. tour_code_real (BCCKG27-HU) — unique
        3. index (ตัวที่ 2, ตัวแรก, สอง) — unique
        4. price (25,900) — disambiguate if multiple options match
        5. city keyword (คิวชู, โตเกียว) — disambiguate if multiple

    Returns ResolveResult.needs_clarification=True when price/city matches >1.
    """
    if not snapshot or not snapshot.tour_list:
        return ResolveResult(matched=False, clarification_reason="no_snapshot")

    options = snapshot.tour_list

    # 1) web_code
    if text:
        m = WEB_CODE_RE.search(text.lower())
        if m:
            wc = m.group(1)
            hits = [o for o in options if o.web_code.lower() == wc]
            if hits:
                return ResolveResult(matched=True, option=hits[0], match_kind="web_code")

    # 2) tour_code_real
    if text:
        m = TOUR_CODE_REAL_RE.search(text.upper())
        if m:
            tcr = m.group(1)
            hits = [o for o in options if o.tour_code_real and o.tour_code_real.upper() == tcr]
            if hits:
                return ResolveResult(matched=True, option=hits[0], match_kind="tour_code_real")

    # 3) index
    idx = _parse_index(text)
    if idx is not None:
        for o in options:
            if o.rank == idx:
                return ResolveResult(matched=True, option=o, match_kind="index")
        # Index out of range
        return ResolveResult(
            matched=False,
            needs_clarification=True,
            clarification_reason=f"index_{idx}_out_of_range",
        )

    # 4) price
    price = _parse_price(text)
    if price is not None:
        # Match within ±100 baht tolerance
        hits = [o for o in options if abs(o.price - price) <= 100]
        if len(hits) == 1:
            return ResolveResult(matched=True, option=hits[0], match_kind="price")
        if len(hits) > 1:
            return ResolveResult(
                matched=False,
                needs_clarification=True,
                clarification_reason="duplicate_price",
                candidates=hits,
            )

    # 5) city keyword (from city_tags + name)
    if text:
        # Import lazily to avoid cycle
        from .country import CITY_TO_COUNTRY, _normalize
        norm = _normalize(text)
        hits = []
        for o in options:
            haystack = " ".join([_normalize(o.name)] + [_normalize(c) for c in o.city_tags])
            for city in CITY_TO_COUNTRY:
                cnorm = _normalize(city)
                if cnorm and cnorm in norm and cnorm in haystack:
                    hits.append(o)
                    break
        # Dedup while preserving order
        seen = set()
        deduped = []
        for o in hits:
            if o.web_code not in seen:
                seen.add(o.web_code)
                deduped.append(o)
        if len(deduped) == 1:
            return ResolveResult(matched=True, option=deduped[0], match_kind="city")
        if len(deduped) > 1:
            return ResolveResult(
                matched=False,
                needs_clarification=True,
                clarification_reason="duplicate_city",
                candidates=deduped,
            )

    return ResolveResult(matched=False, clarification_reason="no_match")
