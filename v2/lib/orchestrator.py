"""
v2.lib.orchestrator — Per-turn agent pipeline.

Ties together:
    state machine (S2) + memory (S1) + intent classifier (S2) +
    tool execution + response writer (S3) + audit logging.

Public API:
    Orchestrator(supabase, redis, llm).handle_turn(psid, text, attachments, meta_message_id, platform)
        → TurnResult

Sprint 3 scope:
    - Wire memory + state machine + intent + response writer in one pass
    - Filter tool execution by `allowed_tools(state)` whitelist
    - Persist agent_runs + tool_calls rows for observability
    - Skip outbound send when state is silent
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

from .state_machine import (
    State, StateContext, transition, allowed_tools, is_silent_state, Intent as SMIntent
)
from .intent import classify, Intent
from .memory import MemoryService, TourOption
from .response_writer import write_response, ResponseDecision
from .llm import LLMClient
from . import redactor
from .catalog_safety import filter_customer_visible_tours
# NB: page_post_context is imported lazily inside _build_planning_context to
# keep import-time deps minimal and avoid surprising side effects in tests that
# stub the supabase fake before importing the orchestrator.
# NB: selected_departure_planning is imported lazily inside
# _build_selected_departure_planning so the unit test that asserts
# "no live network during unit run" never triggers an unintended import.

logger = logging.getLogger("v2.orchestrator")

COUNTRY_NAMES = {
    1: "เกาหลี",
    2: "ญี่ปุ่น",
    3: "ฮ่องกง",
    4: "สิงคโปร์",
    5: "จีน",
    6: "มาเลเซีย",
    7: "เวียดนาม",
    19: "ไต้หวัน",
}


@dataclass
class TurnResult:
    psid: str
    conversation_id: str
    turn_number: int
    state_before: str
    state_after: str
    intent_type: str
    reply_text: Optional[str]
    decision: str
    trace_id: str
    duration_ms: int
    tool_calls_made: list[dict] = field(default_factory=list)
    silent: bool = False
    errors: list[str] = field(default_factory=list)


class Orchestrator:
    """
    Single-turn agent. Stateless — instance is cheap to create per request.
    The orchestrator does NOT acquire the per-PSID Redis lock — webhook layer
    handles that (so the lock is held across the entire HTTP turn).
    """

    def __init__(self, supabase, redis, llm: LLMClient, *,
                 enable_llm_intent: bool = False,
                 http_client: Optional[Any] = None,
                 detail_fetch_ttl_s: int = 300,
                 detail_freshness_ttl_s: int = 6 * 60 * 60,
                 now=None):
        self.supabase = supabase
        self.redis = redis
        self.memory = MemoryService(supabase, redis)
        self.llm = llm
        self.enable_llm_intent = enable_llm_intent  # opt-in upgrade path; off in tests
        # Optional injected HTTP client for detail-page enrichment. When
        # ``None`` the orchestrator never triggers a live fetch — DB rows
        # remain the only source. Tests pass a FakeHttp instance.
        self.http_client = http_client
        # Memory-backed guard window for repeat detail fetches of the same
        # web_code. The DB row is the source of truth; this guard prevents
        # the orchestrator from re-fetching the same page within the window
        # when DB rows are already present.
        self.detail_fetch_ttl_s = int(detail_fetch_ttl_s)
        # Sprint 5 Package I: how stale a tour_departures row may be before
        # the orchestrator treats it as a refresh trigger. 6h is conservative
        # for tour pricing on staging; can be overridden in production or
        # in tests via this kwarg.
        self.detail_freshness_ttl_s = int(detail_freshness_ttl_s)
        # Injectable clock for tests — defaults to datetime.utcnow.
        self._now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _memory_value(memory_view: Any, key: str) -> Any:
        if isinstance(memory_view, dict):
            return memory_view.get(key)
        return getattr(memory_view, key, None)

    @staticmethod
    def _country_id_from_name(country: Optional[str]) -> Optional[int]:
        if not country:
            return None
        country_s = str(country).strip()
        for cid, name in COUNTRY_NAMES.items():
            if country_s == str(name).strip():
                return cid
        return None

    def _merge_intent_with_memory_view(self, intent: Intent, memory_view: Any) -> Intent:
        merged = replace(intent)
        latest_country = self._memory_value(memory_view, "latest_country")
        if not merged.country and latest_country:
            merged.country = str(latest_country)
        if not merged.country_id:
            mem_country_id = self._coerce_int(self._memory_value(memory_view, "country_id"))
            if mem_country_id:
                merged.country_id = mem_country_id
        if not merged.country_id:
            cid = self._country_id_from_name(merged.country)
            if cid is not None:
                merged.country_id = cid
        if not merged.budget:
            merged.budget = self._coerce_int(self._memory_value(memory_view, "budget_per_person"))
        if not merged.budget_type and self._memory_value(memory_view, "budget_type"):
            merged.budget_type = str(self._memory_value(memory_view, "budget_type"))
        if not merged.pax_count:
            merged.pax_count = self._coerce_int(self._memory_value(memory_view, "pax_count"))
        if not merged.travel_period:
            mem_period = (
                self._memory_value(memory_view, "travel_month")
                or self._memory_value(memory_view, "travel_period")
            )
            if mem_period:
                merged.travel_period = str(mem_period)
        return merged

    def _intent_with_current_memory(self, psid: str, intent: Intent) -> Intent:
        return self._merge_intent_with_memory_view(
            intent,
            self.memory.get_customer_memory(psid),
        )

    def _should_search_after_preference_update(self, intent: Intent, memory_view: Any) -> bool:
        if intent.type in {"greeting", "ask_human", "send_attachment"}:
            return False
        if not (intent.budget or intent.pax_count or intent.travel_period):
            return False
        merged = self._merge_intent_with_memory_view(intent, memory_view)
        return bool(merged.country_id or merged.country)

    def handle_turn(
        self, *,
        psid: str,
        text: str,
        attachments: Optional[list] = None,
        meta_message_id: Optional[str] = None,
        platform: str = "fb",
        trace_id: Optional[str] = None,
        source_post_id: Optional[str] = None,
        source_type: Optional[str] = None,
        source_platform: str = "facebook",
    ) -> TurnResult:
        """
        Process one customer message end-to-end.

        Returns TurnResult with reply_text=None when bot stays silent
        (state=human_paused/closed, or response writer returned None).
        """
        t_start = time.time()
        trace_id = trace_id or str(uuid.uuid4())
        attachments = attachments or []

        # 1) Ensure customer + conversation rows
        customer = self.supabase.table("customers").select_one({"psid": psid}) \
            or self.supabase.table("customers").insert({"psid": psid})
        conv = self._ensure_conversation(customer["id"], psid)
        state_before = State(conv["state"])

        # 2) Bot pause guard: short-circuit if currently paused
        if conv.get("is_human_paused"):
            logger.info(
                "[%s] PSID=%s is_human_paused — orchestrator skipping",
                trace_id, redactor.mask_psid(psid),
            )
            return TurnResult(
                psid=psid, conversation_id=conv["id"], turn_number=0,
                state_before=state_before.value, state_after=state_before.value,
                intent_type="bot_paused", reply_text=None, decision="silent_paused",
                trace_id=trace_id, duration_ms=int((time.time() - t_start) * 1000),
                silent=True,
            )

        # 3) Classify intent (rule-based; LLM upgrade optional)
        intent = classify(
            text, attachments=attachments, current_state=state_before.value,
            enable_llm=self.enable_llm_intent, llm_client=self.llm if self.enable_llm_intent else None,
        )

        # 4) Build StateContext from current memory
        sm_ctx = self._build_state_context(conv)

        # 5) Compute transition
        sm_intent = self._intent_to_sm(intent)
        result = transition(state_before, sm_intent, sm_ctx)
        state_after = result.next_state
        tool_hints = result.tool_hints

        # 6) Filter tool hints by per-state whitelist
        whitelist = allowed_tools(state_after)
        approved_tools = [t for t in tool_hints if t in whitelist]
        blocked_tools = [t for t in tool_hints if t not in whitelist]
        if blocked_tools:
            logger.warning("[%s] blocked tools by whitelist: %s", trace_id, blocked_tools)

        pre_tool_memory = self.memory.get_customer_memory(psid)
        if (
            state_after in {
                State.NEW_LEAD,
                State.COLLECTING_PREFERENCES,
                State.OPTIONS_PRESENTED,
            }
            and self._should_search_after_preference_update(intent, pre_tool_memory)
        ):
            if state_after != State.OPTIONS_PRESENTED:
                state_after = State.OPTIONS_PRESENTED
            whitelist = allowed_tools(state_after)
            approved_tools = [t for t in approved_tools if t in whitelist]
            forced_tools = [
                t for t in ("update_customer_memory", "search_tours")
                if t in whitelist
            ]
            approved_tools = forced_tools + [
                t for t in approved_tools
                if t not in forced_tools and t in whitelist
            ]
            logger.info(
                "[%s] preference follow-up completed from memory; forcing search_tours",
                trace_id,
            )

        # 7) Execute approved tools, gather tool_results
        tool_results: dict = {"raw_customer_text": text}
        tool_calls_log: list[dict] = []
        for tool_name in approved_tools:
            try:
                out = self._exec_tool(tool_name, psid, conv, intent, tool_results)
                tool_calls_log.append({"tool": tool_name, "status": "success"})
                if out is not None:
                    tool_results[tool_name] = out
                    # Sprint 4 follow-up wire-in: when get_tour_fees signals
                    # `needs_on_demand_extraction`, invoke the on-demand vision
                    # entry point (cached + page-capped) and refresh tool_results.
                    # NB: this only fires when the policy decided handoff_missing
                    # or handoff_low_confidence — high-confidence rows skip the
                    # vision call entirely (cost saved).
                    if tool_name == "get_tour_fees" and isinstance(out, dict) \
                            and out.get("needs_on_demand_extraction"):
                        try:
                            updated = self._run_on_demand_fee_extraction(
                                psid=psid, fee_info=out,
                                raw_customer_text=text,
                            )
                            if updated is not None:
                                tool_results["get_tour_fees"] = updated
                                tool_results["fees"] = updated  # response_writer alias
                                tool_calls_log.append({
                                    "tool": "extract_fees_on_demand",
                                    "status": "success",
                                    "cache_hit": updated.get("on_demand", {}).get("cache_hit"),
                                    "vision_pages_used": updated.get("on_demand", {}).get("vision_pages_used", 0),
                                })
                        except Exception as ee:
                            logger.exception("[%s] on_demand fee extract failed: %s", trace_id, ee)
                            tool_calls_log.append({
                                "tool": "extract_fees_on_demand",
                                "status": "error",
                                "error": str(ee)[:200],
                            })
            except Exception as e:
                logger.exception("[%s] tool %s failed: %s", trace_id, tool_name, e)
                tool_calls_log.append({"tool": tool_name, "status": "error", "error": str(e)[:200]})

        # 8) Load customer memory snapshot
        cmem = self.memory.get_customer_memory(psid).__dict__

        # 9) Persist state transition (atomically with conversation update)
        if state_before != state_after:
            self._commit_state_change(
                conv_id=conv["id"], psid=psid,
                from_state=state_before, to_state=state_after,
                reason=result.reason, meta_message_id=meta_message_id, platform=platform,
            )

        # 9.5) Build planning context — deterministic page-post / sold-out
        # signal evaluation BEFORE the LLM. The planner is no-op-safe for
        # silent states (silent path skips write_response entirely).
        planning = None
        selected_departure = None
        if not is_silent_state(state_after):
            planning = self._build_planning_context(
                psid=psid, conv=conv,
                accumulated=tool_results,
                source_post_id=source_post_id,
                source_type=source_type,
                source_platform=source_platform,
                trace_id=trace_id,
            )
            selected_departure = self._build_selected_departure_planning(
                psid=psid, conv=conv, accumulated=tool_results,
                intent=intent, text=text, state_after=state_after,
                trace_id=trace_id,
            )

        # 10) Response writer
        rd: Optional[ResponseDecision]
        if is_silent_state(state_after):
            rd = ResponseDecision(text=None, decision="silent_state")
        else:
            rd = write_response(
                state=state_after, intent_type=intent.type,
                tool_results=tool_results, customer_memory=cmem,
                llm=self.llm,
                planning=planning,
                selected_departure=selected_departure,
            )

        # 11) Persist conversation_turns (inbound + outbound)
        turn_no = self._next_turn_number(conv["id"])
        self._persist_turn(
            conv_id=conv["id"], psid=psid, turn_no=turn_no,
            direction="inbound", speaker="customer", text=text,
            attachments=attachments, intent_dict=intent.to_dict(),
            state_before=state_before.value, state_after=state_after.value,
            meta_message_id=meta_message_id, platform=platform,
        )
        if rd and rd.text:
            self._persist_turn(
                conv_id=conv["id"], psid=psid, turn_no=turn_no + 1,
                direction="outbound", speaker="bot",
                text=rd.text, attachments=[],
                intent_dict=None, state_before=state_after.value,
                state_after=state_after.value, llm_info=rd,
                meta_message_id=None, platform=platform,
            )

        # 12) Audit log: agent_runs row
        duration_ms = int((time.time() - t_start) * 1000)
        self._log_agent_run(
            conv_id=conv["id"], psid=psid, turn_no=turn_no, trace_id=trace_id,
            state_before=state_before.value, state_after=state_after.value,
            decision=rd.decision if rd else "no_response",
            decision_data={"reason": result.reason, "tools_executed": tool_calls_log,
                           "blocked_tools": blocked_tools},
            llm_response=rd.llm_response if rd else None,
            duration_ms=duration_ms,
            meta_message_id=meta_message_id, platform=platform,
        )

        return TurnResult(
            psid=psid, conversation_id=conv["id"], turn_number=turn_no,
            state_before=state_before.value, state_after=state_after.value,
            intent_type=intent.type,
            reply_text=rd.text if rd else None,
            decision=rd.decision if rd else "no_response",
            trace_id=trace_id, duration_ms=duration_ms,
            tool_calls_made=tool_calls_log,
            silent=(rd is None or rd.text is None),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_conversation(self, customer_id: str, psid: str) -> dict:
        existing = self.supabase.table("conversations").select_one(
            {"psid": psid, "closed_at": None}
        )
        if existing:
            return existing
        return self.supabase.table("conversations").insert({
            "customer_id": customer_id,
            "psid": psid,
            "state": State.NEW_LEAD.value,
        })

    def _build_state_context(self, conv: dict) -> StateContext:
        """
        Pre-compute fee_complete by looking up tour_fees for any active locked tour.
        Other context flags (admin_takeover, admin_resumed) are Sprint 5 (handoff service).
        """
        fee_complete: Optional[bool] = None
        psid = conv.get("psid")
        if psid and conv.get("state") in ("tour_selected", "departure_selected", "fee_check_required"):
            try:
                fee_info = self._get_tour_fees(psid)
                if fee_info:
                    fee_complete = bool(fee_info.get("is_complete"))
            except Exception as e:
                logger.warning("fee precheck failed: %s", e)
        return StateContext(
            fee_complete=fee_complete,
            admin_takeover=False,
            admin_resumed=False,
            timeout_reached=False,
        )

    def _resolve_planning_candidate(self, psid: str, conv: dict,
                                     accumulated: dict) -> tuple:
        """
        Pick the candidate tour fields the response planner should evaluate.

        Priority order:
          1. Just-locked tour from `lock_selected_tour` tool output
          2. Just-fetched detail from `get_tour_detail` tool output
          3. Currently locked tour from memory (warm path)
          4. Top-1 of fresh `search_tours` result

        Returns (web_code, tour_code_real, tour_id). Any field may be None.
        Returns (None, None, None) if no candidate is in scope.
        """
        web_code = None
        tour_code_real = None
        tour_id = None

        if isinstance(accumulated, dict):
            locked = accumulated.get("lock_selected_tour")
            if isinstance(locked, dict) and not locked.get("error"):
                web_code = locked.get("web_code") or web_code
                tour_code_real = locked.get("tour_code_real") or tour_code_real
                tour_id = locked.get("tour_id") or tour_id

            if not (web_code or tour_code_real or tour_id):
                detail = accumulated.get("get_tour_detail")
                if isinstance(detail, dict):
                    web_code = detail.get("web_code") or web_code
                    tour_code_real = detail.get("tour_code_real") or tour_code_real
                    tour_id = detail.get("id") or tour_id

        if not (web_code or tour_code_real or tour_id):
            try:
                existing = self.memory.get_selected_tour(psid)
                if existing:
                    web_code = existing.web_code or web_code
                    tour_code_real = existing.tour_code_real or tour_code_real
                    tour_id = existing.tour_id or tour_id
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("planning: get_selected_tour failed: %s", e)

        if not (web_code or tour_code_real or tour_id) and isinstance(accumulated, dict):
            st = accumulated.get("search_tours")
            if isinstance(st, dict):
                tours = st.get("tours") or []
                if tours:
                    top = tours[0] if isinstance(tours[0], dict) else {}
                    web_code = top.get("web_code") or web_code
                    tour_code_real = top.get("tour_code_real") or tour_code_real

        return web_code, tour_code_real, tour_id

    def _build_planning_context(self, *, psid: str, conv: dict,
                                 accumulated: dict,
                                 source_post_id: Optional[str],
                                 source_type: Optional[str],
                                 source_platform: str,
                                 trace_id: Optional[str]):
        """
        Build the LLM-safe planning bundle the response writer uses to block
        sold-out / full candidates BEFORE the LLM runs. Never raises — returns
        None on any error so the orchestrator can still produce a reply.
        """
        try:
            from .page_post_context import build_response_planning_context
            web_code, tour_code_real, tour_id = self._resolve_planning_candidate(
                psid, conv, accumulated,
            )
            planning = build_response_planning_context(
                self.supabase,
                candidate_web_code=web_code,
                candidate_tour_code_real=tour_code_real,
                candidate_tour_id=tour_id,
                source_post_id=source_post_id,
                source_type=source_type,
                source_platform=source_platform,
            )
            if planning.replacement_needed:
                logger.info(
                    "[%s] planning: candidate blocked scope=%s status=%s",
                    trace_id,
                    getattr(planning.block, "scope", None),
                    getattr(planning.block, "status", None),
                )
            return planning
        except Exception as e:
            logger.warning("[%s] planning: build failed: %s", trace_id, e)
            return None

    # ------------------------------------------------------------------
    # Sprint 5 Package H: selected departure detail planning
    # ------------------------------------------------------------------

    # Intents that NEVER trigger a detail-page enrichment (these are
    # universal/greeting/payment/decline shapes that don't ask anything
    # row-specific).
    _NON_ENRICHING_INTENTS: frozenset[str] = frozenset({
        "greeting", "send_attachment", "ask_human", "payment_keyword",
        "decline_final", "off_topic_strong", "off_topic",
    })

    # Intents that always trigger when a candidate exists — these are
    # explicit follow-ups on the selected tour.
    _ENRICHING_INTENTS: frozenset[str] = frozenset({
        "select_tour", "select_departure", "ask_fee", "ask_tour_detail",
        "confirm_booking", "ask_pax", "ask_period",
    })

    @dataclass
    class _DepartureCandidate:
        web_code: str = ""
        tour_code_real: Optional[str] = None
        tour_id: Optional[str] = None
        name: Optional[str] = None
        airline: Optional[str] = None
        source: str = "none"   # 'just_locked' / 'memory_locked' / 'intent_code' /
                               # 'in_turn_detail' / 'option_index' / 'none'
        is_locked: bool = False

    def _resolve_selected_departure_candidate(
        self, *, psid: str, conv: dict, accumulated: dict, intent: Intent,
    ) -> "Orchestrator._DepartureCandidate":
        """
        Resolve the candidate the response planner should evaluate.

        Priority order (per CURRENT_DEV_TASK.md):
          1. just-selected tour from the current turn
             (``accumulated['lock_selected_tour']``)
          2. existing locked selected tour in memory
             (``self.memory.get_selected_tour(psid)``)
          3. explicit web_code or tour_code_real in customer text
             (``intent.selected_code``)
          4. current detail result if already fetched in the turn
             (``accumulated['get_tour_detail']``)
          5. recent top options — only when the customer selects by
             option number (``intent.selected_index`` against the latest
             offer snapshot).

        Returns ``_DepartureCandidate`` with ``web_code=""`` when no
        candidate is in scope. Missing fields (``tour_id`` / ``airline``)
        are filled from ``tours_canonical`` when the row exists.
        """
        cand = Orchestrator._DepartureCandidate()

        # Priority 1 — just-selected tour from current turn.
        locked = (accumulated or {}).get("lock_selected_tour")
        if isinstance(locked, dict) and not locked.get("error"):
            cand.web_code = locked.get("web_code") or ""
            cand.tour_code_real = locked.get("tour_code_real")
            cand.tour_id = locked.get("tour_id")
            cand.name = locked.get("name")
            cand.source = "just_locked"
            cand.is_locked = True

        # Priority 2 — existing locked selected tour in memory.
        if not cand.web_code:
            try:
                existing = self.memory.get_selected_tour(psid)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("planning: get_selected_tour failed: %s", e)
                existing = None
            if existing:
                cand.web_code = existing.web_code or ""
                cand.tour_code_real = existing.tour_code_real
                cand.tour_id = existing.tour_id
                cand.name = existing.name or cand.name
                cand.source = "memory_locked"
                cand.is_locked = True

        # Priority 3 — explicit web_code or tour_code_real in text.
        if not cand.web_code and intent and intent.selected_code:
            code = intent.selected_code
            # The classifier sets selected_code from either a web_code
            # (e.g. ap242455) or a tour_code_real (e.g. BCCKG27-HU). The
            # lookups below handle both shapes without ever collapsing
            # the two fields together.
            wc_lookup = self.supabase.table("tours_canonical").select_one(
                {"web_code": code.lower()}
            )
            if wc_lookup:
                cand.web_code = wc_lookup.get("web_code") or ""
                cand.tour_code_real = wc_lookup.get("tour_code_real")
                cand.tour_id = wc_lookup.get("id")
                cand.name = wc_lookup.get("name") or cand.name
                cand.airline = wc_lookup.get("airline") or cand.airline
                cand.source = "intent_code"
            else:
                tcr_lookup = self.supabase.table("tours_canonical").select_one(
                    {"tour_code_real": code}
                )
                if tcr_lookup:
                    cand.web_code = tcr_lookup.get("web_code") or ""
                    cand.tour_code_real = tcr_lookup.get("tour_code_real")
                    cand.tour_id = tcr_lookup.get("id")
                    cand.name = tcr_lookup.get("name") or cand.name
                    cand.airline = tcr_lookup.get("airline") or cand.airline
                    cand.source = "intent_code"

        # Priority 4 — current detail fetched in the same turn.
        if not cand.web_code:
            detail = (accumulated or {}).get("get_tour_detail")
            if isinstance(detail, dict):
                cand.web_code = detail.get("web_code") or ""
                cand.tour_code_real = detail.get("tour_code_real")
                cand.tour_id = detail.get("id")
                cand.name = detail.get("name") or cand.name
                cand.airline = detail.get("airline") or cand.airline
                cand.source = "in_turn_detail"

        # Priority 5 — option index against the latest offer snapshot.
        if not cand.web_code and intent and intent.selected_index:
            try:
                snap = self.memory.get_latest_offer_snapshot(psid)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("planning: get_latest_offer_snapshot failed: %s", e)
                snap = None
            if snap and snap.tour_list:
                for opt in snap.tour_list:
                    if opt.rank == intent.selected_index:
                        cand.web_code = opt.web_code
                        cand.tour_code_real = opt.tour_code_real
                        cand.name = opt.name or cand.name
                        cand.airline = opt.airline or cand.airline
                        cand.source = "option_index"
                        break

        # Backfill tour_id / airline from tours_canonical when missing.
        if cand.web_code and (cand.tour_id is None or cand.airline is None):
            row = self.supabase.table("tours_canonical").select_one(
                {"web_code": cand.web_code}
            )
            if row:
                cand.tour_id = cand.tour_id or row.get("id")
                cand.airline = cand.airline or row.get("airline")
                cand.tour_code_real = cand.tour_code_real or row.get("tour_code_real")
                cand.name = cand.name or row.get("name")

        return cand

    def _should_trigger_detail_enrichment(
        self, *, intent: Intent, text: str,
        candidate: "Orchestrator._DepartureCandidate",
    ) -> bool:
        """
        Decide whether the per-turn flow should consult detail enrichment.

        Generic greeting, broad country ask, and other top-of-funnel
        intents must NOT trigger a detail fetch. Selected-tour follow-up
        intents and parseable date phrases always do. With a memory-locked
        tour, any non-greeting follow-up triggers — the row data may be
        needed to answer correctly.
        """
        if not candidate.web_code:
            return False
        if intent.type in self._NON_ENRICHING_INTENTS:
            return False
        if intent.type in self._ENRICHING_INTENTS:
            return True
        # Late import to keep module load light + isolated from network deps.
        from .selected_departure_match import parse_customer_date_phrase
        if parse_customer_date_phrase(text or ""):
            return True
        # Memory-locked tour + a non-trivial follow-up = treat as in-scope.
        if candidate.is_locked and (text or "").strip():
            return True
        return False

    # Redis key prefix for the detail-fetch guard.
    _DETAIL_FETCH_KEY = "detail_fetched:{web_code}"

    def _recently_fetched_detail(self, web_code: str) -> bool:
        """Return True when a recent fetch guard is set for ``web_code``.

        The guard is best-effort. Any Redis failure is treated as "no
        guard" so the orchestrator stays safe rather than silently
        skipping a needed fetch.
        """
        if not web_code or self.redis is None:
            return False
        try:
            val = self.redis.get(self._DETAIL_FETCH_KEY.format(web_code=web_code))
            return val is not None
        except Exception:
            return False

    def _mark_detail_fetched(self, web_code: str) -> None:
        if not web_code or self.redis is None:
            return
        try:
            if hasattr(self.redis, "setex"):
                self.redis.setex(
                    self._DETAIL_FETCH_KEY.format(web_code=web_code),
                    self.detail_fetch_ttl_s, "1",
                )
            else:
                self.redis.set(
                    self._DETAIL_FETCH_KEY.format(web_code=web_code),
                    "1", ex=self.detail_fetch_ttl_s,
                )
        except Exception:
            pass

    def _rows_are_stale(self, db_rows: list) -> bool:
        """Return True when at least one row's ``refreshed_at`` is older
        than ``detail_freshness_ttl_s``. Legacy rows whose refreshed_at
        is NULL are treated as fresh (so V2 keeps working pre-migration
        022 application) — operators tighten this by running the
        scheduled refresher once migration 022 is live on staging.
        """
        if not db_rows:
            return False
        now = self._now()
        ttl = max(0, int(self.detail_freshness_ttl_s))
        if ttl <= 0:
            # TTL=0 means "always treat as stale" — useful in tests.
            return True
        for d in db_rows:
            v = d.get("refreshed_at") if isinstance(d, dict) else None
            if v is None or v == "":
                continue  # legacy row — skip
            try:
                if isinstance(v, str):
                    ts = datetime.fromisoformat(v.replace("Z", "+00:00"))
                else:
                    ts = v
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            age_s = (now - ts).total_seconds()
            if age_s > ttl:
                return True
        return False

    def _get_or_fetch_departure_rows(
        self, *, web_code: str, tour_id: Optional[str], trace_id: Optional[str],
    ):
        """
        Return the parsed DeparturePriceRow list for ``web_code``.

        Strategy (DB-first with freshness gate, then HTTP refresh):
          1. Read ``tour_departures`` rows for the web_code from the DB.
          2. If any rows exist AND every row's ``refreshed_at`` is within
             ``detail_freshness_ttl_s`` (or refreshed_at is missing on
             every row — legacy DB), use them directly without HTTP.
          3. If rows exist but are STALE (oldest refreshed_at older than
             the TTL), check the redis guard. If the guard is hot we
             already tried recently — fall back to the stale rows and
             never quote final price/availability (the planner's
             safe_planning_note already does that). If the guard is
             cold, run ``enrich_tour_detail`` to refresh, set the guard,
             and return the freshly-parsed rows. On refresh failure we
             fall back to the stale rows so the bot still has something
             deterministic to plan around (fail closed: never invent
             availability or final price).
          4. If DB is empty and the redis guard is hot, return [] (don't
             hammer). Otherwise, fetch via ``enrich_tour_detail`` once.
          5. If no http_client is available, return what we have (DB
             rows or []), without ever trying a refresh.

        Never raises — returns ``[]`` (or stale rows) on any failure so
        the planner can still produce a safe note.
        """
        from .selected_departure_planning import row_dict_to_departure_price_row
        if not web_code:
            return []

        try:
            db_rows = self.supabase.table("tour_departures").select_all(
                {"web_code": web_code}
            ) or []
        except Exception as e:
            logger.warning("[%s] departure_rows DB read failed: %s", trace_id, e)
            db_rows = []

        def _convert(rows: list) -> list:
            out = []
            for d in rows:
                try:
                    out.append(
                        row_dict_to_departure_price_row(d, default_web_code=web_code)
                    )
                except Exception as e:  # pragma: no cover - defensive
                    logger.warning(
                        "[%s] departure_rows convert failed wc=%s err=%s",
                        trace_id, web_code, e,
                    )
            return out

        if db_rows:
            is_stale = self._rows_are_stale(db_rows)
            if not is_stale:
                return _convert(db_rows)
            # Stale path — try a deterministic refresh once unless guarded.
            if self.http_client is None:
                # No http_client to refresh with — serve what we have.
                logger.info(
                    "[%s] departure_rows: stale rows wc=%s but no http_client — serving stale",
                    trace_id, web_code,
                )
                return _convert(db_rows)
            if self._recently_fetched_detail(web_code):
                logger.info(
                    "[%s] departure_rows: stale rows wc=%s but refresh guard hot — serving stale",
                    trace_id, web_code,
                )
                return _convert(db_rows)
            # Attempt the refresh.
            try:
                from v2.scraper.detail_enrichment import enrich_tour_detail
                result = enrich_tour_detail(
                    web_code,
                    http=self.http_client,
                    supabase=self.supabase,
                    tour_id=tour_id,
                )
            except Exception as e:
                logger.warning(
                    "[%s] enrich_tour_detail refresh failed wc=%s err=%s — serving stale",
                    trace_id, web_code, e,
                )
                self._mark_detail_fetched(web_code)
                return _convert(db_rows)
            self._mark_detail_fetched(web_code)
            if not result.parsed:
                # Refresh produced nothing usable — fall back to stale
                # rows (fail closed, no fabricated availability).
                logger.info(
                    "[%s] departure_rows: refresh wc=%s did not parse — serving stale",
                    trace_id, web_code,
                )
                return _convert(db_rows)
            return list(result.rows)

        if self._recently_fetched_detail(web_code):
            return []

        if self.http_client is None:
            return []

        try:
            from v2.scraper.detail_enrichment import enrich_tour_detail
            result = enrich_tour_detail(
                web_code,
                http=self.http_client,
                supabase=self.supabase,
                tour_id=tour_id,
            )
        except Exception as e:
            logger.warning("[%s] enrich_tour_detail failed wc=%s err=%s",
                           trace_id, web_code, e)
            self._mark_detail_fetched(web_code)
            return []

        # Mark fetch attempted regardless of outcome — that's the whole
        # point of the guard (don't re-fetch on every message).
        self._mark_detail_fetched(web_code)

        if not result.parsed:
            return []
        return list(result.rows)

    def _build_selected_departure_planning(
        self, *, psid: str, conv: dict, accumulated: dict, intent: Intent,
        text: str, state_after: State, trace_id: Optional[str],
    ):
        """
        Build the compact, LLM-safe planning bundle the response writer
        uses to (a) keep the selected tour visible to the LLM and
        (b) match the customer's date phrase to a deterministic row.

        Never raises — returns ``None`` on failure so the orchestrator
        can still produce a reply via the other paths.
        """
        try:
            candidate = self._resolve_selected_departure_candidate(
                psid=psid, conv=conv, accumulated=accumulated, intent=intent,
            )
        except Exception as e:
            logger.warning("[%s] selected_departure: resolve failed: %s", trace_id, e)
            return None
        if not candidate.web_code:
            return None

        if not self._should_trigger_detail_enrichment(
            intent=intent, text=text, candidate=candidate,
        ):
            return None

        rows = self._get_or_fetch_departure_rows(
            web_code=candidate.web_code, tour_id=candidate.tour_id, trace_id=trace_id,
        )

        try:
            from .selected_departure_planning import build_selected_departure_planning
            planning = build_selected_departure_planning(
                rows=rows,
                customer_text=text or "",
                selected_tour={
                    "web_code": candidate.web_code,
                    "tour_code_real": candidate.tour_code_real,
                    "airline": candidate.airline,
                    "name": candidate.name,
                },
            )
            logger.info(
                "[%s] selected_departure: candidate=%s wc=%s status=%s confidence=%s",
                trace_id, candidate.source, candidate.web_code,
                planning.match_status, planning.confidence,
            )
            return planning
        except Exception as e:
            logger.warning("[%s] selected_departure: build failed: %s", trace_id, e)
            return None

    @staticmethod
    def _intent_to_sm(intent: Intent) -> SMIntent:
        return SMIntent(
            type=intent.type,
            raw_text=intent.raw_text,
            country=intent.country,
            budget=intent.budget,
            selected_index=intent.selected_index,
            selected_code=intent.selected_code,
            has_attachment=intent.has_attachment,
        )

    def _exec_tool(self, tool_name: str, psid: str, conv: dict,
                   intent: Intent, accumulated: dict) -> Any:
        """
        Dispatch tool name → memory service operation. Sprint 3 wires the
        minimal set needed for E2E E-001. Sprint 4 adds get_tour_fees;
        Sprint 5 adds notify_team_line.
        """
        if tool_name == "update_customer_memory":
            patch = {}
            if intent.country: patch["latest_country"] = intent.country
            if intent.budget:
                patch["budget_per_person"] = intent.budget
                patch["budget_type"] = intent.budget_type
            if intent.pax_count: patch["pax_count"] = intent.pax_count
            if intent.travel_period: patch["travel_month"] = intent.travel_period
            patch["conversation_state"] = conv["state"]
            if patch:
                return self.memory.update_customer_memory(psid, patch, reason="orchestrator_turn")
            return None

        if tool_name == "search_tours":
            search_intent = self._intent_with_current_memory(psid, intent)
            result = self._search_tours_simple(search_intent, text=intent.raw_text)
            tours = result.get("tours") or []
            if tours:
                try:
                    snapshot = self.memory.save_offer_snapshot(
                        psid,
                        tours,
                        search_context=result.get("query_echo") or {},
                        conversation_id=conv.get("id"),
                    )
                    result["offer_snapshot_id"] = getattr(snapshot, "id", None)
                except Exception as e:
                    logger.warning(
                        "[%s] save_offer_snapshot failed after search_tours: %s",
                        conv.get("trace_id") or conv.get("id") or psid,
                        e,
                    )
            return result

        if tool_name == "get_latest_offer_snapshot":
            snap = self.memory.get_latest_offer_snapshot(psid)
            return snap.to_dict() if snap else None

        if tool_name == "lock_selected_tour":
            return self._lock_selected_tour(psid, conv["id"], intent, accumulated)

        if tool_name == "get_tour_detail":
            return self._get_tour_detail(intent, accumulated)

        if tool_name == "get_tour_fees":
            return self._get_tour_fees(
                psid,
                raw_customer_text=accumulated.get("raw_customer_text"),
            )

        if tool_name == "append_conversation_event":
            return None  # noop placeholder; orchestrator already logs to agent_runs

        # Sprint 5 tools — Sprint 3 stubs
        if tool_name in ("notify_team_line", "mark_waiting_team"):
            logger.info("Tool %s is stub in Sprint 3", tool_name)
            return {"stub": True, "tool": tool_name}

        raise ValueError(f"unknown tool: {tool_name}")

    # --- Simplified tool implementations (full versions come in Sprint 4-5) ---

    def _country_name_for_intent(self, intent: Intent) -> str:
        if intent.country:
            return str(intent.country)
        try:
            return COUNTRY_NAMES.get(int(intent.country_id or 0), "")
        except (TypeError, ValueError):
            return ""

    def _should_use_live_listing_fallback(self, text: str) -> bool:
        """Use live SSR listing only when the user clearly asks for options."""
        normalized = (text or "").lower()
        return any(
            marker in normalized
            for marker in (
                "มีทัวร์",
                "ขอทัวร์",
                "แนะนำ",
                "หา",
                "โปรแกรม",
                "ทัวร์ไป",
                "ไปเที่ยว",
            )
        )

    def _search_tours_live_listing_fallback(self, intent: Intent) -> list[dict]:
        """Fetch SSR listing when staging DB has no customer-safe rows."""
        if not intent.country_id:
            return []
        country_name = self._country_name_for_intent(intent)
        if not country_name:
            return []
        if self.http_client is None:
            logger.info(
                "Live listing fallback skipped for country_id=%s: no injected HTTP client",
                intent.country_id,
            )
            return []
        try:
            from ..scraper.scrape_tours import fetch_country_listing

            parsed = fetch_country_listing(
                int(intent.country_id),
                country_name,
                http=self.http_client,
            )
        except Exception as exc:  # pragma: no cover - defensive against live-web drift
            logger.warning(
                "Live listing fallback failed for country_id=%s: %s",
                intent.country_id,
                exc,
            )
            return []

        rows = [
            {
                "id": None,
                "web_code": tour.web_code,
                "tour_code_real": None,
                "name": tour.name,
                "price": tour.base_price,
                "base_price": tour.base_price,
                "days": tour.days,
                "airline": tour.airline,
                "url": tour.url,
                "country_id": tour.country_id,
                "country": tour.country,
            }
            for tour in parsed
        ]
        rows = filter_customer_visible_tours(rows)
        return sorted(
            rows,
            key=lambda row: int(row.get("base_price") or row.get("price") or 999999999),
        )[:3]

    def _search_tours_simple(self, intent: Intent, text: str = "") -> dict:
        """Query tours_canonical with simple filters. Save snapshot if results > 0."""
        source = "db"
        # Filter active by country_id if known
        where = {"is_active": True}
        if intent.country_id:
            where["country_id"] = intent.country_id

        table = self.supabase.table("tours_canonical")
        with table._cursor() as cur:
            sql_parts = ['SELECT id, web_code, tour_code_real, name, base_price, days, airline, url '
                          'FROM "tours_canonical" WHERE is_active = TRUE '
                          "AND web_code ~ '^ap[0-9]+$' "
                          "AND url LIKE 'https://www.tourfiremai.com/%' "
                          "AND length(btrim(name)) >= 4 "
                          "AND COALESCE(base_price, 0) >= 3000"]
            args = []
            if intent.country_id:
                sql_parts.append(' AND country_id = %s')
                args.append(intent.country_id)
            sql_parts.append(' ORDER BY base_price ASC LIMIT 3')
            cur.execute(" ".join(sql_parts), args)
            rows = cur.fetchall() if hasattr(cur, "fetchall") else []
        if not rows and hasattr(table, "select_all"):
            rows = table.select_all({"is_active": True})
            if intent.country_id:
                rows = [
                    row for row in rows
                    if str(row.get("country_id")) == str(intent.country_id)
                ]
            rows = filter_customer_visible_tours(rows)
            rows = sorted(
                rows,
                key=lambda row: int(row.get("base_price") or row.get("price") or 999999999),
            )[:3]
            source = "memory_db"
        # Normalize cursor returns (psycopg vs fake)
        if rows and isinstance(rows[0], dict):
            items = rows
        else:
            items = [
                {"id": r[0], "web_code": r[1], "tour_code_real": r[2],
                 "name": r[3], "price": r[4], "days": r[5], "airline": r[6], "url": r[7]}
                for r in rows
            ]
        items = filter_customer_visible_tours(items)
        if self._should_use_live_listing_fallback(text) and len(items) < 3:
            fallback_items = self._search_tours_live_listing_fallback(intent)
            if fallback_items:
                seen_codes = set()
                merged_items = []
                for row in [*items, *fallback_items]:
                    code = str(row.get("web_code") or "").strip().lower()
                    if not code or code in seen_codes:
                        continue
                    seen_codes.add(code)
                    merged_items.append(row)
                merged_items = filter_customer_visible_tours(merged_items)
                merged_items = sorted(
                    merged_items,
                    key=lambda row: int(
                        row.get("base_price") or row.get("price") or 999999999
                    ),
                )[:3]
                if merged_items:
                    if not items:
                        source = "live_listing"
                    elif len(merged_items) > len(items):
                        source = f"{source}+live_listing"
                    items = merged_items
        if not items:
            source = "none"
        top3 = [
            TourOption(
                rank=i + 1, web_code=row.get("web_code", ""),
                tour_code_real=row.get("tour_code_real"),
                name=row.get("name", ""),
                price=int(row.get("price") or row.get("base_price") or 0),
                days=int(row.get("days") or 0), airline=row.get("airline"),
                url=row.get("url"),
            )
            for i, row in enumerate(items[:3])
        ]
        return {
            "tours": [t.to_dict() for t in top3],
            "count": len(top3),
            "query_echo": {
                "country_id": intent.country_id,
                "country": self._country_name_for_intent(intent),
                "budget": intent.budget,
                "budget_type": intent.budget_type,
                "pax_count": intent.pax_count,
                "travel_period": intent.travel_period,
                "source": source,
            },
        }

    def _lock_selected_tour(self, psid: str, conv_id: str, intent: Intent,
                             accumulated: dict) -> Optional[dict]:
        """Use offer snapshot to resolve intent.selected_* and lock the tour."""
        snap = self.memory.get_latest_offer_snapshot(psid)
        if not snap:
            return {"error": "no_offer_snapshot"}
        from .memory import resolve_tour_selection
        # Re-create text from intent fields
        text_for_resolve = intent.raw_text
        rr = resolve_tour_selection(text_for_resolve, snap)
        if not rr.matched or rr.option is None:
            return {"error": "no_resolve", "reason": rr.clarification_reason}
        # Lookup canonical row for full tour
        tour_row = self.supabase.table("tours_canonical").select_one(
            {"web_code": rr.option.web_code}
        )
        if not tour_row:
            return {"error": "tour_not_in_db"}
        lock = self.memory.lock_selected_tour(
            psid, dict(tour_row), conversation_id=conv_id, from_offer_id=snap.id
        )
        return {
            "tour_id": lock.tour_id, "web_code": lock.web_code,
            "tour_code_real": lock.tour_code_real, "name": lock.name,
            "price": lock.price,
        }

    def _get_tour_detail(self, intent: Intent, accumulated: dict) -> Optional[dict]:
        wc = intent.selected_code
        if not wc and "lock_selected_tour" in accumulated:
            wc = accumulated["lock_selected_tour"].get("web_code")
        if not wc:
            return None
        row = self.supabase.table("tours_canonical").select_one({"web_code": wc})
        if not row:
            return None
        # Strip wholesale field (defense in depth — response_writer also strips)
        return {k: v for k, v in row.items() if k != "wholesale"}

    def _get_tour_fees(self, psid: str, *,
                       raw_customer_text: Optional[str] = None) -> Optional[dict]:
        """
        Get fees for currently-locked tour, surfacing per-field confidence and the
        asked-field hint so response writer can apply field-level policy
        (Sprint 4 follow-up).
        """
        # Local import to avoid hard dependency at module load
        from .fee_answer_policy import detect_asked_field, decide_fee_answer

        lock = self.memory.get_selected_tour(psid)
        if not lock:
            return None
        asked_field = detect_asked_field(raw_customer_text or "")
        fees_row = self.supabase.table("tour_fees").select_one({"tour_id": lock.tour_id})
        if not fees_row:
            return {
                "is_complete": False,
                "fees": None,
                "confidence": 0.0,
                "field_confidences": {},
                "needs_handoff": True,
                "handoff_reason": "no_fee_row",
                "asked_field": asked_field,
                "pdf_hash": None,
                "pdf_url": None,
                "tour_id": lock.tour_id,
                "needs_on_demand_extraction": True,
            }
        required_ok = all(
            fees_row.get(f) is not None
            for f in ("tip_amount", "single_supplement", "deposit_amount")
        )
        visa_ok = (fees_row.get("visa_fee") is not None) or (
            fees_row.get("visa_status") in ("exempt", "required", "on_arrival", "evisa")
        )
        confidence = float(fees_row.get("extraction_confidence", 0) or 0)
        is_complete = required_ok and visa_ok and confidence >= 0.7

        from .fee_answer_policy import decide_fee_answer as _decide
        d = _decide(fees_row, asked_field)
        needs_on_demand = d.decision in (
            "handoff_missing", "handoff_low_confidence",
        )

        return {
            "is_complete": is_complete,
            "fees": fees_row,
            "confidence": confidence,
            "field_confidences": {
                "tip_confidence":               fees_row.get("tip_confidence"),
                "deposit_confidence":           fees_row.get("deposit_confidence"),
                "single_supplement_confidence": fees_row.get("single_supplement_confidence"),
                "visa_confidence":              fees_row.get("visa_confidence"),
            },
            "needs_handoff": not is_complete and d.decision != "answer",
            "handoff_reason": d.handoff_reason if not d.can_answer else None,
            "asked_field": asked_field,
            "pdf_hash": fees_row.get("pdf_hash"),
            "pdf_url": fees_row.get("pdf_url"),
            "tour_id": lock.tour_id,
            "needs_on_demand_extraction": needs_on_demand,
        }

    # ------------------------------------------------------------------
    # Sprint 4 follow-up: on-demand vision/OCR wiring
    # ------------------------------------------------------------------

    def _run_on_demand_fee_extraction(self, *,
                                       psid: str,
                                       fee_info: dict,
                                       raw_customer_text: str) -> Optional[dict]:
        """
        Trigger on-demand Vision/OCR for the locked tour when the policy says we
        cannot answer the asked field from the existing DB row.
        """
        from ..scraper.ondemand_vision import (
            extract_fees_on_demand, EXTRACTION_VERSION,
        )
        from ..scraper.extract_fees import ExtractionResult
        from ..scraper.save_fees import upsert_tour_fees
        from .fee_answer_policy import decide_fee_answer

        tour_id = fee_info.get("tour_id")
        pdf_url = fee_info.get("pdf_url")
        pdf_hash = fee_info.get("pdf_hash")
        asked_field = fee_info.get("asked_field") or "any"

        if not pdf_url and tour_id:
            tour_row = self.supabase.table("tours_canonical").select_one({"id": tour_id})
            if tour_row:
                pdf_url = tour_row.get("pdf_url") or pdf_url
        if not pdf_url:
            logger.info("on_demand: no pdf_url available for tour %s — skipping", tour_id)
            return None

        pdf_path: Optional[str] = None
        try:
            from ..scraper.download_pdf import download_pdf
            artifact = download_pdf(pdf_url)
            pdf_path = artifact.local_path
            if not pdf_hash:
                pdf_hash = artifact.sha256
        except Exception as e:
            logger.warning("on_demand: download_pdf failed for %s: %s", pdf_url, e)
            return None

        if not pdf_path or not pdf_hash:
            return None

        prior_row = (fee_info.get("fees") or {})
        prior: Optional[ExtractionResult] = None
        if prior_row:
            prior = ExtractionResult(
                tip_amount=prior_row.get("tip_amount"),
                deposit_amount=prior_row.get("deposit_amount"),
                single_supplement=prior_row.get("single_supplement"),
                visa_fee=prior_row.get("visa_fee"),
                visa_status=prior_row.get("visa_status"),
                infant_fee=prior_row.get("infant_fee"),
                child_fee_no_bed=prior_row.get("child_fee_no_bed"),
                joinland_price=prior_row.get("joinland_price"),
                mandatory_fees_summary=prior_row.get("mandatory_fees_summary"),
                extraction_method=prior_row.get("extraction_method") or "pdfplumber+regex",
                extraction_confidence=float(prior_row.get("extraction_confidence") or 0),
                source_page=prior_row.get("source_page"),
                raw_snippet=prior_row.get("raw_snippet"),
                tip_confidence=prior_row.get("tip_confidence"),
                deposit_confidence=prior_row.get("deposit_confidence"),
                single_supplement_confidence=prior_row.get("single_supplement_confidence"),
                visa_confidence=prior_row.get("visa_confidence"),
            )

        od = extract_fees_on_demand(
            pdf_path, self.llm,
            pdf_hash=pdf_hash,
            prior=prior,
            cache=self.redis,
            max_vision_pages=3,
            asked_field=asked_field,
            extraction_version=EXTRACTION_VERSION,
        )

        if not od.ocr_available:
            logger.info("on_demand: OCR unavailable (%s) — graceful handoff",
                         od.skipped_reason)
            out = dict(fee_info)
            out["on_demand"] = {"attempted": True, "skipped_reason": od.skipped_reason,
                                 "cache_hit": False, "vision_pages_used": 0,
                                 "ocr_available": False}
            return out

        if tour_id:
            try:
                upsert_tour_fees(
                    self.supabase,
                    tour_id=tour_id,
                    tour_code_real=prior_row.get("tour_code_real"),
                    pdf_url=pdf_url, pdf_hash=pdf_hash,
                    result=od.result,
                )
                try:
                    self.supabase.table("tour_fees").update(
                        {"tour_id": tour_id},
                        {"extraction_version": EXTRACTION_VERSION},
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.warning("on_demand: upsert_tour_fees failed: %s", e)

        refreshed_row = self.supabase.table("tour_fees").select_one({"tour_id": tour_id}) if tour_id else None
        d = decide_fee_answer(refreshed_row, asked_field)

        return {
            "is_complete": d.can_answer or bool(refreshed_row and
                                                  refreshed_row.get("tip_amount") is not None and
                                                  refreshed_row.get("deposit_amount") is not None and
                                                  refreshed_row.get("single_supplement") is not None),
            "fees": refreshed_row,
            "confidence": float((refreshed_row or {}).get("extraction_confidence") or 0),
            "field_confidences": {
                "tip_confidence":               (refreshed_row or {}).get("tip_confidence"),
                "deposit_confidence":           (refreshed_row or {}).get("deposit_confidence"),
                "single_supplement_confidence": (refreshed_row or {}).get("single_supplement_confidence"),
                "visa_confidence":              (refreshed_row or {}).get("visa_confidence"),
            },
            "needs_handoff": not d.can_answer,
            "handoff_reason": d.handoff_reason if not d.can_answer else None,
            "asked_field": asked_field,
            "pdf_hash": pdf_hash,
            "pdf_url": pdf_url,
            "tour_id": tour_id,
            "needs_on_demand_extraction": False,
            "on_demand": {
                "attempted": True,
                "cache_hit": od.cache_hit,
                "vision_pages_used": od.vision_pages_used,
                "ocr_available": True,
                "candidate_pages": list(od.candidate_pages),
                "estimated_cost_usd": od.estimated_cost_usd,
                "estimated_tokens_in": od.estimated_tokens_in,
                "estimated_tokens_out": od.estimated_tokens_out,
            },
        }


    # --- Persistence helpers ---

    def _next_turn_number(self, conv_id: str) -> int:
        with self.supabase.table("conversation_turns")._cursor() as cur:
            cur.execute(
                'SELECT id FROM "conversations" WHERE id = %s FOR UPDATE',
                [conv_id],
            )
            cur.execute(
                'SELECT COALESCE(MAX(turn_number), 0) FROM "conversation_turns" WHERE "conversation_id" = %s',
                [conv_id],
            )
            r = cur.fetchone()
            max_n = (r[0] if r else 0) or 0
        return int(max_n) + 1

    def _persist_turn(self, *, conv_id, psid, turn_no, direction, speaker, text,
                       attachments, intent_dict=None, state_before, state_after,
                       llm_info: Optional[ResponseDecision] = None,
                       meta_message_id=None, platform="fb"):
        row = {
            "id": str(uuid.uuid4()),
            "conversation_id": conv_id, "psid": psid, "turn_number": turn_no,
            "direction": direction, "speaker": speaker,
            "message_text": text, "attachments": attachments or None,
            "state_before": state_before, "state_after": state_after,
            "platform": platform,
        }
        if intent_dict:
            row["intent"] = intent_dict
        if meta_message_id:
            row["meta_message_id"] = meta_message_id
        if llm_info and llm_info.llm_response:
            row["llm_model"] = llm_info.llm_response.usage.model_used
            row["llm_tokens_in"] = llm_info.llm_response.usage.tokens_in
            row["llm_tokens_out"] = llm_info.llm_response.usage.tokens_out
            row["latency_ms"] = llm_info.llm_response.usage.latency_ms
        try:
            self.supabase.table("conversation_turns").insert(row)
        except Exception as e:
            if "dedup" in str(e):
                logger.info("Dedup hit on turn — skipping")
            else:
                raise

    def _commit_state_change(self, *, conv_id, psid, from_state, to_state,
                              reason, meta_message_id=None, platform="fb"):
        self.supabase.table("conversations").update(
            {"id": conv_id}, {"state": to_state.value, "last_activity_at": "now()"},
        )
        try:
            self.supabase.table("conversation_events").insert({
                "id": str(uuid.uuid4()),
                "conversation_id": conv_id, "psid": psid,
                "event_type": "state_change",
                "event_data": {"from": from_state.value, "to": to_state.value, "reason": reason},
                "triggered_by": "bot",
                "meta_message_id": meta_message_id,
                "platform": platform,
            })
        except Exception as e:
            if "dedup" in str(e):
                logger.info("Dedup hit on event — skipping")
            else:
                raise

    def _log_agent_run(self, *, conv_id, psid, turn_no, trace_id,
                       state_before, state_after, decision, decision_data,
                       llm_response, duration_ms, meta_message_id=None, platform="fb"):
        run_id = str(uuid.uuid4())
        row = {
            "id": run_id,
            "conversation_id": conv_id, "psid": psid,
            "turn_number": turn_no, "trace_id": trace_id,
            "agent_name": "orchestrator",
            "state_before": state_before, "state_after": state_after,
            "decision": decision, "decision_data": decision_data,
            "duration_ms": duration_ms,
            "platform": platform,
        }
        if llm_response:
            row.update({
                "llm_model": llm_response.usage.model_used,
                "llm_tokens_in": llm_response.usage.tokens_in,
                "llm_tokens_out": llm_response.usage.tokens_out,
                "llm_latency_ms": llm_response.usage.latency_ms,
            })
        if meta_message_id:
            row["meta_message_id"] = meta_message_id
        try:
            self.supabase.table("agent_runs").insert(row)
        except Exception as e:
            logger.warning("agent_runs insert failed: %s", e)
