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
from dataclasses import dataclass, field
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

logger = logging.getLogger("v2.orchestrator")


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
                 enable_llm_intent: bool = False):
        self.supabase = supabase
        self.redis = redis
        self.memory = MemoryService(supabase, redis)
        self.llm = llm
        self.enable_llm_intent = enable_llm_intent  # opt-in upgrade path; off in tests

    def handle_turn(
        self, *,
        psid: str,
        text: str,
        attachments: Optional[list] = None,
        meta_message_id: Optional[str] = None,
        platform: str = "fb",
        trace_id: Optional[str] = None,
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

        # 7) Execute approved tools, gather tool_results
        tool_results: dict = {"raw_customer_text": text}
        tool_calls_log: list[dict] = []
        for tool_name in approved_tools:
            try:
                out = self._exec_tool(tool_name, psid, conv, intent, tool_results)
                tool_calls_log.append({"tool": tool_name, "status": "success"})
                if out is not None:
                    tool_results[tool_name] = out
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

        # 10) Response writer
        rd: Optional[ResponseDecision]
        if is_silent_state(state_after):
            rd = ResponseDecision(text=None, decision="silent_state")
        else:
            rd = write_response(
                state=state_after, intent_type=intent.type,
                tool_results=tool_results, customer_memory=cmem,
                llm=self.llm,
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
            return self._search_tours_simple(intent)

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

    def _search_tours_simple(self, intent: Intent) -> dict:
        """Query tours_canonical with simple filters. Save snapshot if results > 0."""
        # Filter active by country_id if known
        where = {"is_active": True}
        if intent.country_id:
            where["country_id"] = intent.country_id

        with self.supabase.table("tours_canonical")._cursor() as cur:
            sql_parts = ['SELECT id, web_code, tour_code_real, name, base_price, days, airline, url '
                          'FROM "tours_canonical" WHERE is_active = TRUE']
            args = []
            if intent.country_id:
                sql_parts.append(' AND country_id = %s')
                args.append(intent.country_id)
            sql_parts.append(' ORDER BY base_price ASC LIMIT 3')
            cur.execute(" ".join(sql_parts), args)
            rows = cur.fetchall() if hasattr(cur, "fetchall") else []
        # Normalize cursor returns (psycopg vs fake)
        if rows and isinstance(rows[0], dict):
            items = rows
        else:
            items = [
                {"id": r[0], "web_code": r[1], "tour_code_real": r[2],
                 "name": r[3], "price": r[4], "days": r[5], "airline": r[6], "url": r[7]}
                for r in rows
            ]
        top3 = [
            TourOption(
                rank=i + 1, web_code=row.get("web_code", ""),
                tour_code_real=row.get("tour_code_real"),
                name=row.get("name", ""), price=int(row.get("price") or 0),
                days=int(row.get("days") or 0), airline=row.get("airline"),
                url=row.get("url"),
            )
            for i, row in enumerate(items[:3])
        ]
        return {
            "tours": [t.to_dict() for t in top3],
            "count": len(top3),
            "query_echo": {"country_id": intent.country_id, "budget": intent.budget},
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

        Returns a dict with shape:
          {
            "is_complete": bool,
            "fees": <fee_row dict or None>,
            "confidence": float,              # row-level extraction_confidence
            "field_confidences": {
                "tip_confidence": ..., "deposit_confidence": ...,
                "single_supplement_confidence": ..., "visa_confidence": ...,
            },
            "needs_handoff": bool,
            "handoff_reason": str | None,
            "asked_field": str,               # detected from customer text
            "pdf_hash": str | None,           # for on-demand vision cache key
            "pdf_url": str | None,            # for on-demand vision fetch
            "tour_id": str | None,
            "needs_on_demand_extraction": bool,
          }
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

        # Decide whether on-demand extraction would help. Trigger when:
        #   - asked_field is specific (not "any") AND
        #   - field value is missing OR per-field confidence is below threshold.
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
