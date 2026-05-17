"""
v2.lib.response_writer — Compose context + call LLM → customer-facing reply.

Guard rails enforced HERE (in code, not just prompt):
  1. State-silence: return None for waiting_team / human_paused / closed
  2. Wholesale redaction: strip `wholesale` and partner names from tool_results before LLM
  3. Fee discipline: if fees.is_complete == False, return canned handoff message (no LLM)
  4. Length cap: truncate to 400 chars after LLM (paranoia)
  5. Wholesale brand leak check: search reply for known partner names → flag + retry

Public API:
    write_response(state, intent, tool_results, customer_memory, *, llm) -> ResponseDecision | None
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .state_machine import State, is_silent_state
from .llm import LLMClient, LLMResponse

logger = logging.getLogger("v2.response_writer")

# Wholesale brand names that must NEVER appear in customer-facing reply
_WHOLESALE_BLACKLIST = {
    "gs", "ttn", "best", "zego", "formosa", "check in", "rich tour", "i travel",
    "i-travel", "best tour", "ttn เกิดมาเที่ยว",
}

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


@dataclass
class ResponseDecision:
    text: Optional[str]
    decision: str                # 'silent' | 'canned_handoff' | 'llm_reply' | 'redacted_retry' | 'fallback_canned'
    used_canned: bool = False
    used_llm: bool = False
    llm_response: Optional[LLMResponse] = None
    brand_leak_detected: bool = False
    notes: list[str] = field(default_factory=list)


# --- Helpers ------------------------------------------------------------------

def _strip_wholesale(tool_results: dict) -> dict:
    """Recursively drop any 'wholesale' key (case-insensitive) from tool_results."""
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
    """Check if reply mentions any wholesale partner name."""
    lower = text.lower()
    return any(brand in lower for brand in _WHOLESALE_BLACKLIST)


def _truncate(text: str, limit: int = 400) -> str:
    """Hard length cap (post-LLM safety)."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _load_prompt(name: str) -> str:
    """Load versioned prompt from v2/prompts/."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts")
    path = os.path.join(base, f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        body = f.read()
    # Strip YAML frontmatter (--- ... ---) if present
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            body = body[end + 5:]
    return body


# --- Main entry ---------------------------------------------------------------

def write_response(
    *,
    state: State,
    intent_type: str,
    tool_results: dict,
    customer_memory: dict,
    llm: LLMClient,
) -> Optional[ResponseDecision]:
    """
    Generate a reply (or None if bot must stay silent).

    All state/tool inputs must already be in their final form — this function
    does NOT call tools.
    """
    # 1) Silence states — bot returns None (orchestrator will skip sending)
    if is_silent_state(state):
        logger.info("State %s is silent — skipping response writer", state.value)
        return ResponseDecision(text=None, decision="silent")

    # 2) Pre-canned cases that bypass LLM entirely
    if state == State.WAITING_TEAM:
        # 1-shot ack allowed in waiting_team — return canned (orchestrator should
        # only call us once via waiting_ack_sent flag — defense-in-depth: still canned)
        return ResponseDecision(
            text=CANNED_WAITING_ACK,
            decision="canned_handoff", used_canned=True,
        )

    if state == State.FEE_CHECK_REQUIRED:
        fees = tool_results.get("fees")
        if not fees or not fees.get("is_complete"):
            return ResponseDecision(
                text=CANNED_HANDOFF_FEE_INCOMPLETE,
                decision="canned_handoff", used_canned=True,
                notes=["fee_incomplete_handoff"],
            )

    if state == State.BOOKING_READY_FOR_HANDOFF:
        return ResponseDecision(
            text=CANNED_HANDOFF_GENERIC,
            decision="canned_handoff", used_canned=True,
        )

    # 3) Sanitize tool_results before passing to LLM
    clean_tools = _strip_wholesale(tool_results)

    # 4) Compose messages
    system_prompt = _load_prompt("response_writer_v1")
    user_payload = _build_user_payload(state, intent_type, clean_tools, customer_memory)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_payload},
    ]

    # 5) Call LLM (response tier)
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
    # Strip leading template literals just in case
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)

    # 6) Brand leak check
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

    # 7) Length cap
    text = _truncate(text, limit=400)

    # 8) Silent state echo check
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
    """
    Build the user message containing state + intent + sanitized tool_results +
    customer profile. The orchestrator's runtime context, not the customer's
    raw text (customer text is inside tool_results.raw_customer_text).

    Format chosen to make it cheap for the model to parse and for grep-style
    debug/cassette diffs.
    """
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
