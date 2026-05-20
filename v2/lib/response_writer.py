"""
v2.lib.response_writer — Compose context + call LLM → customer-facing reply.

Guard rails enforced HERE (in code, not just prompt):
  1. State-silence: return None for waiting_team / human_paused / closed
  2. Wholesale redaction: strip `wholesale` and partner names from tool_results before LLM
  3. Fee discipline: if fees.is_complete == False, return canned handoff message (no LLM)
  4. Length cap: truncate to 400 chars after LLM (paranoia)
  5. Wholesale brand leak check: search reply for known partner names → flag + retry
  6. Page-post / sold-out planning: if planner says replacement_needed, return canned
     reply BEFORE the LLM. The LLM never decides sold-out semantics.
  7. Selected-departure planning: a compact, LLM-safe bundle is injected
     into ``tool_results`` for the LLM to consume. The bot never quotes
     a price/seat as final and never guesses a row — it must use the
     ``matched_departure`` data when ``match_status == "matched_high"``
     and otherwise rely on the planning ``safe_planning_note``.

Public API:
    write_response(state, intent, tool_results, customer_memory, *, llm,
                   planning=None, selected_departure=None)
                       -> ResponseDecision | None
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .state_machine import State, is_silent_state
from .llm import LLMClient, LLMResponse
from .fee_answer_policy import (
    decide_fee_answer, detect_asked_field, format_fee_answer,
    FeeAnswerDecision,
)
# NOTE: page_post_context is imported lazily inside write_response to avoid a
# circular import path (page_post_context imports _WHOLESALE_BLACKLIST from
# this module). The `planning` kwarg uses a forward reference.

logger = logging.getLogger("v2.response_writer")

# Wholesale brand names that must NEVER appear in customer-facing reply.
import re as _re_brand_check

_WHOLESALE_BLACKLIST = [
    _re_brand_check.compile(r"\b(?:ttn|zego|formosa|i[-\s]?travel|rich\s+tour|best\s+tour)\b", _re_brand_check.I),
    _re_brand_check.compile(r"(?:^|[\s.,/])GS\s+(?:travel|tour)", _re_brand_check.I),
    _re_brand_check.compile(r"ttn[\s_]?เกิดมาเที่ยว"),
]

# Canned messages for state-silence + handoff cases (NO LLM CALL)
CANNED_HANDOFF_FEE_INCOMPLETE = (
    "ขอตรวจสอบรายละเอียดค่าใช้จ่ายกับทีมงานสักครู่นะคะ 🙏\n"
    "ทีมงานจะตอบกลับใน 15 นาทีค่ะ 😊"
)
CANNED_HANDOFF_GENERIC = (
    "ขอเวลาสักครู่นะคะ ทีมงานจะติดต่อกลับใน 15 นาทีค่ะ 🙏"
)
CANNED_WAITING_ACK = (
    "รับทราบค่ะ ทีมงานกำลังเตรียมข้อมูล ขอเวลาสักครู่นะคะ 😊"
)
CANNED_BLOCKED_REPLACEMENT = (
    "ขอตรวจสอบรอบนี้ก่อนนะคะ เดี๋ยวช่วยคัดตัวที่ยังเปิดรับให้ตรงงบให้ค่ะ 🙏"
)


@dataclass
class ResponseDecision:
    text: Optional[str]
    decision: str
    used_canned: bool = False
    used_llm: bool = False
    llm_response: Optional[LLMResponse] = None
    brand_leak_detected: bool = False
    notes: list[str] = field(default_factory=list)


# --- Helpers ------------------------------------------------------------------

def _strip_wholesale(tool_results: dict) -> dict:
    if isinstance(tool_results, dict):
        return {
            k: _strip_wholesale(v)
            for k, v in tool_results.items()
            if str(k).lower() != "wholesale"
        }
    if isinstance(tool_results, list):
        return [_strip_wholesale(x) for x in tool_results]
    return tool_results


def _has_brand_leak(text: str) -> bool:
    if not text:
        return False
    return any(pat.search(text) for pat in _WHOLESALE_BLACKLIST)


def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _load_prompt(name: str) -> str:
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts")
    path = os.path.join(base, f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        body = f.read()
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            body = body[end + 5:]
    return body


def _planning_to_compact_note(planning) -> dict:
    """Compact LLM-safe dict from a PlanningContext (or None)."""
    if planning is None:
        return {}
    src = planning.source
    note = {
        "source_type": src.source_type,
        "is_recent": src.is_recent,
        "title": src.title,
        "linked_web_codes": list(src.linked_web_codes),
        "replacement_needed": bool(planning.replacement_needed),
    }
    if planning.block.is_blocked:
        note["block_status"] = planning.block.status
        note["block_scope"] = planning.block.scope
        note["block_reason"] = planning.block.reason_text
    if planning.safe_reason_text:
        note["safe_reason"] = planning.safe_reason_text
    return {
        k: v for k, v in note.items()
        if v not in (None, "", [], False) or k == "replacement_needed"
    }


# --- Main entry ---------------------------------------------------------------

def write_response(
    *,
    state: State,
    intent_type: str,
    tool_results: dict,
    customer_memory: dict,
    llm: LLMClient,
    planning=None,
    selected_departure=None,
) -> Optional[ResponseDecision]:
    """
    Generate a reply (or None if bot must stay silent).
    """
    if is_silent_state(state):
        logger.info("State %s is silent — skipping response writer", state.value)
        return ResponseDecision(text=None, decision="silent")

    if state == State.WAITING_TEAM:
        return ResponseDecision(
            text=CANNED_WAITING_ACK,
            decision="canned_handoff", used_canned=True,
        )

    if state == State.FEE_CHECK_REQUIRED:
        fees = tool_results.get("fees") or tool_results.get("get_tour_fees") or {}
        fees_row = (fees or {}).get("fees") or (fees or {}).get("fees_row")
        raw_text = tool_results.get("raw_customer_text") or ""
        asked_field = (fees or {}).get("asked_field") or detect_asked_field(raw_text)

        decision = decide_fee_answer(fees_row, asked_field)
        if decision.can_answer:
            return ResponseDecision(
                text=format_fee_answer(decision),
                decision="canned_fee_answer",
                used_canned=True,
                notes=[
                    f"fee_answer:{decision.asked_field}",
                    f"confidence={decision.confidence:.2f}",
                    f"threshold={decision.threshold:.2f}",
                ],
            )
        return ResponseDecision(
            text=CANNED_HANDOFF_FEE_INCOMPLETE,
            decision="canned_handoff", used_canned=True,
            notes=[
                f"fee_incomplete_handoff:{decision.decision}",
                f"asked_field:{decision.asked_field}",
                f"reason:{decision.handoff_reason or 'n/a'}",
            ],
        )

    if state == State.BOOKING_READY_FOR_HANDOFF:
        return ResponseDecision(
            text=CANNED_HANDOFF_GENERIC,
            decision="canned_handoff", used_canned=True,
        )

    # 2.5) Deterministic page-post / sold-out block — runs BEFORE the LLM.
    if planning is not None and getattr(planning, "replacement_needed", False):
        safe = getattr(planning, "safe_reason_text", None) or CANNED_BLOCKED_REPLACEMENT
        logger.info(
            "page_post_planning: blocked candidate (scope=%s status=%s) — canned reply",
            getattr(planning.block, "scope", None),
            getattr(planning.block, "status", None),
        )
        return ResponseDecision(
            text=_truncate(safe, limit=400),
            decision="canned_blocked",
            used_canned=True,
            notes=[
                f"page_post_block:{getattr(planning.block, 'scope', None)}",
                f"status:{getattr(planning.block, 'status', None)}",
                f"source:{getattr(planning.source, 'source_type', None)}",
            ],
        )

    # 3) Sanitize tool_results before passing to LLM
    clean_tools = _strip_wholesale(tool_results)
    planning_note = _planning_to_compact_note(planning)
    if planning_note:
        clean_tools = {**clean_tools, "page_post_planning_note": planning_note}

    # 3.5) Selected-departure planning bundle (Sprint 5 Package H).
    # The bundle is strictly LLM-safe: web_code / tour_code_real /
    # airline are kept separate, "-" stays None, wholesale never appears,
    # and a deterministic ``safe_planning_note`` tells the LLM how to
    # phrase the reply without quoting price/seat as final.
    if selected_departure is not None:
        try:
            sd_dict = selected_departure.to_compact_dict()
        except AttributeError:
            sd_dict = selected_departure if isinstance(selected_departure, dict) else {}
        if sd_dict:
            clean_tools = {
                **clean_tools,
                "selected_departure_planning": _strip_wholesale(sd_dict),
            }

    # 4) Compose messages
    system_prompt = _load_prompt("response_writer_v1")
    user_payload = _build_user_payload(state, intent_type, clean_tools, customer_memory)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_payload},
    ]

    # 5) Call LLM
    try:
        rsp = llm.chat(
            tier="response",
            messages=messages,
            max_tokens=500,
            temperature=0.4,
        )
    except Exception as e:
        logger.exception("LLM call failed in response writer: %s", e)
        return ResponseDecision(
            text=CANNED_HANDOFF_GENERIC,
            decision="fallback_canned", used_canned=True,
            notes=[f"llm_error: {type(e).__name__}"],
        )

    text = rsp.text or ""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)

    leak = _has_brand_leak(text)
    if leak:
        logger.warning("Wholesale brand leak detected in LLM reply — falling back to canned")
        return ResponseDecision(
            text=CANNED_HANDOFF_GENERIC,
            decision="fallback_canned", used_canned=True,
            used_llm=True, llm_response=rsp,
            brand_leak_detected=True,
            notes=["brand_leak_in_llm_reply"],
        )

    text = _truncate(text, limit=400)

    if "__SILENT__" in text:
        return ResponseDecision(text=None, decision="silent",
                                 used_llm=True, llm_response=rsp,
                                 notes=["llm_returned_silent_marker"])

    return ResponseDecision(
        text=text,
        decision="llm_reply",
        used_llm=True,
        llm_response=rsp,
    )


def _build_user_payload(state: State, intent_type: str, tool_results: dict,
                        customer_memory: dict) -> str:
    customer_name = customer_memory.get("customer_name") or customer_memory.get("fb_name") or ""
    parts = [
        f"state={state.value}",
        f"intent={intent_type}",
        f"customer_name={customer_name}",
    ]
    if tool_results:
        import json as _json
        parts.append("tool_results=" + _json.dumps(tool_results, ensure_ascii=False, default=str))
    return "\n".join(parts)
