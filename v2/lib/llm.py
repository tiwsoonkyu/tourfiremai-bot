"""
v2.lib.llm — OpenAI client with 3-tier model routing + Mock/Cassette/Live modes.

Sprint 3 design rules:
  - NO live API call in unit tests (test_mode='mock' by default)
  - NO hardcoded model names — all 3 tiers come from V2_STAGING_OPENAI_*_MODEL env
  - Cost + token logging surfaces via LLMResponse.usage for tool_calls audit
  - Fallback ladder per tier when API rejects a model id (404/permission)

Public API:
    make_llm_client(config) -> LLMClient
    Tier = Literal['response', 'fast', 'vision']
    LLMClient.chat(tier, messages, response_format=None, ...) -> LLMResponse
    LLMClient.vision(messages, image_bytes, ...) -> LLMResponse

Modes:
    mock     — returns canned response based on intent heuristics (no API)
    cassette — replays JSON cassette by request hash; raises if cassette missing
    live     — real OpenAI call (only used in integration tests + production)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

logger = logging.getLogger("v2.llm")

Tier = Literal["response", "fast", "vision"]


# Fallback ladders if primary model id 404s. Override via env vars.
_FALLBACK_LADDERS: dict[str, list[str]] = {
    "response": ["gpt-5.1", "gpt-5-mini", "gpt-5", "gpt-4o", "gpt-4o-mini"],
    "fast":     ["gpt-5-nano", "gpt-4o-mini", "gpt-4o-mini-2024-07-18"],
    "vision":   ["gpt-5-vision", "gpt-4o", "gpt-4o-mini"],
}


# --- Result type --------------------------------------------------------------

@dataclass
class LLMUsage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd_estimate: float = 0.0
    model_used: str = ""
    latency_ms: int = 0


@dataclass
class LLMResponse:
    text: str
    structured: Optional[dict] = None
    finish_reason: str = "stop"
    usage: LLMUsage = field(default_factory=LLMUsage)
    cassette_hit: bool = False
    mock_decision: Optional[str] = None    # for debugging mock-mode classification


# --- Client protocol ----------------------------------------------------------

class LLMClient(Protocol):
    def chat(
        self, *,
        tier: Tier,
        messages: list[dict],
        response_format: Optional[dict] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse: ...

    def vision(
        self, *,
        messages: list[dict],
        image_bytes: bytes,
        response_format: Optional[dict] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse: ...


# --- Mock client (default in tests) ------------------------------------------

class MockLLMClient:
    """
    Deterministic canned responses. NEVER touches network.

    For response writer: picks template based on `state_after` hint in messages
    For fast tier (intent/extract): returns echo of input as structured JSON
    For vision: returns canned fee extraction skeleton
    """

    def __init__(self, config=None):
        self.config = config
        self.call_log: list[dict] = []  # tests can inspect

    def chat(self, *, tier, messages, response_format=None,
             max_tokens=None, temperature=None) -> LLMResponse:
        start = time.time()
        # Inspect the system + user content for cues
        user_msg = next((m for m in messages if m.get("role") == "user"), {})
        sys_msg = next((m for m in messages if m.get("role") == "system"), {})
        user_text = str(user_msg.get("content", ""))[:1000]
        sys_text = str(sys_msg.get("content", ""))[:200]

        self.call_log.append({
            "tier": tier, "user_text": user_text[:120],
            "response_format": response_format is not None,
        })

        if tier == "response":
            return self._mock_response_writer(user_text, sys_text)
        if tier == "fast":
            return self._mock_fast(user_text, response_format)
        # Tier 'vision' fallthrough
        return self._mock_vision(user_text)

    def vision(self, *, messages, image_bytes, response_format=None,
               max_tokens=None) -> LLMResponse:
        return self._mock_vision(str(image_bytes)[:50])

    # --- Mock generators ---

    def _mock_response_writer(self, user_text: str, sys_text: str) -> LLMResponse:
        # Look for a state hint in user_text (orchestrator puts it there)
        lower = user_text.lower()
        if "state=options_presented" in lower or "[present_top" in user_text:
            text = "ตอนนี้น้องเลือกมา 3 โปรแกรมที่น่าจะตรงงบของพี่นะคะ 😊\nลองดูข้อมูลด้านบนได้เลยค่ะ สนใจตัวไหนเป็นพิเศษ?"
            decision = "present_top_n"
        elif "state=tour_selected" in lower or "[show_current" in user_text:
            text = ("ได้เลยค่ะ 😊 สรุปรายละเอียดสำคัญให้นะคะ\n"
                    "✈️ ทัวร์ที่เลือก: (mock)\n"
                    "💰 ราคาเริ่ม: (mock)\n"
                    "📅 วันเดินทาง: (mock)\n\n"
                    "สนใจเดินทางช่วงไหนคะ?")
            decision = "confirm_tour_detail"
        elif "state=fee_check_required" in lower or "[fee_complete" in user_text:
            text = "ค่าใช้จ่ายเพิ่มเติม:\n💵 ค่าทิป (mock)\n📄 วีซ่า (mock)\n🛏 พักเดี่ยว (mock)\n\nสะดวกจองเลยมั้ยคะ?"
            decision = "show_fees"
        elif "state=waiting_team" in lower:
            text = "ขอเวลาสักครู่นะคะ ทีมงานจะติดต่อกลับใน 15 นาทีค่ะ 🙏"
            decision = "handoff_ack"
        elif "state=new_lead" in lower:
            text = "สวัสดีค่ะ 😊 รวมทัวร์ไฟไหม้ยินดีให้บริการ\nสนใจไปประเทศไหนคะ?"
            decision = "greet"
        else:
            text = "รับทราบค่ะ ขอข้อมูลเพิ่มเติมหน่อยนะคะ 😊"
            decision = "ack_generic"

        return LLMResponse(
            text=text,
            usage=LLMUsage(tokens_in=len(user_text)//4, tokens_out=len(text)//4,
                           model_used="mock:response",
                           latency_ms=5),  # mock fixed latency
            mock_decision=decision,
        )

    def _mock_fast(self, user_text: str, response_format) -> LLMResponse:
        # Return synthetic structured JSON when response_format requested
        if response_format and response_format.get("type") == "json_schema":
            structured = {"intent_refined": "unknown", "confidence": 0.6,
                          "notes": "mock-fast classifier"}
        else:
            structured = None
        return LLMResponse(
            text="(mock fast tier)",
            structured=structured,
            usage=LLMUsage(tokens_in=len(user_text)//4, tokens_out=10,
                           model_used="mock:fast", latency_ms=3),
            mock_decision="fast_passthrough",
        )

    def _mock_vision(self, hint: str) -> LLMResponse:
        # Synthetic fee extraction skeleton
        structured = {
            "tip_amount": None, "visa_fee": None, "single_supplement": None,
            "deposit_amount": None, "infant_fee": None, "child_fee_no_bed": None,
            "extraction_confidence": 0.5,
            "extraction_method": "llm_vision",
            "notes": "mock vision tier — no real extraction",
        }
        return LLMResponse(
            text="(mock vision tier)",
            structured=structured,
            usage=LLMUsage(tokens_in=200, tokens_out=80,
                           model_used="mock:vision", latency_ms=4),
            mock_decision="vision_skeleton",
        )


# --- Cassette client (golden-master replay) ----------------------------------

class CassetteLLMClient:
    """
    Replays JSON cassettes by request-hash. Use to lock LLM output stability
    across test runs without needing live API.

    Cassette path: v2/tests/cassettes/<request_hash>.json
    Format: {"request": {...input...}, "response": {...LLMResponse...}}
    """

    def __init__(self, cassette_dir: str, config=None, strict: bool = True):
        self.dir = cassette_dir
        self.strict = strict
        self.config = config
        os.makedirs(cassette_dir, exist_ok=True)

    @staticmethod
    def _hash_request(tier: str, messages: list, response_format: Optional[dict]) -> str:
        canonical = json.dumps(
            {"tier": tier, "messages": messages, "response_format": response_format},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _load(self, h: str) -> Optional[dict]:
        path = os.path.join(self.dir, f"{h}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def chat(self, *, tier, messages, response_format=None,
             max_tokens=None, temperature=None) -> LLMResponse:
        h = self._hash_request(tier, messages, response_format)
        c = self._load(h)
        if c is None:
            if self.strict:
                raise CassetteMissError(
                    f"No cassette {h} for tier={tier!r}. "
                    f"Re-record in 'live' mode and commit the cassette."
                )
            # Fall back to mock
            return MockLLMClient(self.config).chat(
                tier=tier, messages=messages, response_format=response_format
            )
        r = c["response"]
        return LLMResponse(
            text=r["text"],
            structured=r.get("structured"),
            finish_reason=r.get("finish_reason", "stop"),
            usage=LLMUsage(**r.get("usage", {})),
            cassette_hit=True,
        )

    def vision(self, *, messages, image_bytes, response_format=None,
               max_tokens=None) -> LLMResponse:
        # Hash includes image content for stability
        body = {"image_sha256": hashlib.sha256(image_bytes).hexdigest()[:16]}
        h = self._hash_request("vision", messages + [{"role": "image", "content": body}], response_format)
        c = self._load(h)
        if c is None:
            if self.strict:
                raise CassetteMissError(f"No vision cassette {h}")
            return MockLLMClient(self.config).vision(messages=messages, image_bytes=image_bytes)
        r = c["response"]
        return LLMResponse(
            text=r["text"], structured=r.get("structured"),
            usage=LLMUsage(**r.get("usage", {})), cassette_hit=True,
        )


class CassetteMissError(RuntimeError): pass


# --- Live client (used only when test_mode='live' or in production) ----------

class OpenAILLMClient:
    """
    Real OpenAI API client. Lazy import openai package so unit tests don't need it.
    """

    def __init__(self, config):
        self.config = config
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        if not self.config.openai_api_key:
            raise RuntimeError("V2_STAGING_OPENAI_API_KEY not set — cannot use live LLM")
        try:
            import openai
        except ImportError as e:
            raise RuntimeError("openai package not installed — `pip install openai`") from e
        self._client = openai.OpenAI(
            api_key=self.config.openai_api_key,
            timeout=self.config.openai_timeout_sec,
            max_retries=self.config.openai_max_retries,
        )

    def _model_for(self, tier: Tier) -> str:
        if tier == "response":
            return self.config.openai_response_model
        if tier == "fast":
            return self.config.openai_fast_model
        return self.config.openai_vision_model

    def _ladder_for(self, tier: Tier) -> list[str]:
        primary = self._model_for(tier)
        fallbacks = _FALLBACK_LADDERS[tier]
        # primary first, then fallbacks (deduped, preserving order)
        seen = set()
        out = []
        for m in [primary] + fallbacks:
            if m and m not in seen:
                out.append(m)
                seen.add(m)
        return out

    def chat(self, *, tier, messages, response_format=None,
             max_tokens=None, temperature=None) -> LLMResponse:
        self._ensure_client()
        start = time.time()
        last_err = None
        for model in self._ladder_for(tier):
            try:
                kwargs = {"model": model, "messages": messages}
                if max_tokens: kwargs["max_tokens"] = max_tokens
                if temperature is not None: kwargs["temperature"] = temperature
                if response_format: kwargs["response_format"] = response_format
                rsp = self._client.chat.completions.create(**kwargs)
                text = rsp.choices[0].message.content or ""
                structured = None
                if response_format and response_format.get("type") in ("json_object", "json_schema"):
                    try:
                        structured = json.loads(text)
                    except Exception:
                        pass
                latency_ms = int((time.time() - start) * 1000)
                return LLMResponse(
                    text=text, structured=structured,
                    finish_reason=rsp.choices[0].finish_reason or "stop",
                    usage=LLMUsage(
                        tokens_in=rsp.usage.prompt_tokens if rsp.usage else 0,
                        tokens_out=rsp.usage.completion_tokens if rsp.usage else 0,
                        model_used=model,
                        latency_ms=latency_ms,
                    ),
                )
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "model" in msg and ("not found" in msg or "does not exist" in msg or "404" in msg):
                    logger.warning("Model %s unavailable, trying next in ladder", model)
                    continue
                raise
        raise RuntimeError(f"All models in tier={tier!r} failed: {last_err}")

    def vision(self, *, messages, image_bytes, response_format=None,
               max_tokens=None) -> LLMResponse:
        import base64
        self._ensure_client()
        b64 = base64.b64encode(image_bytes).decode()
        # Mutate last user message to include image content (OpenAI vision format)
        msgs = [m for m in messages]
        last_user = next((i for i, m in enumerate(msgs) if m["role"] == "user"), None)
        if last_user is None:
            msgs.append({"role": "user", "content": []})
            last_user = len(msgs) - 1
        original = msgs[last_user]
        content_list = original["content"] if isinstance(original["content"], list) else [
            {"type": "text", "text": str(original["content"])}
        ]
        content_list.append({"type": "image_url",
                              "image_url": {"url": f"data:image/png;base64,{b64}"}})
        msgs[last_user] = {"role": "user", "content": content_list}
        return self.chat(tier="vision", messages=msgs, response_format=response_format,
                          max_tokens=max_tokens)


# --- Recording client (wraps live → persists cassettes) ----------------------

class RecordingLLMClient:
    """
    Wraps OpenAILLMClient: makes a live call, then writes a redacted cassette.

    Use during Sprint 4 'record' phase. After cassettes are committed, switch
    test_mode back to 'cassette' (or 'mock' for CI default).

    The redactor function is injected so tests can swap it; default is
    `lib.cassette_redactor.redact_cassette` (lazy import).
    """

    def __init__(self, live_client, cassette_dir: str, *, redactor=None,
                 meta: Optional[dict] = None):
        self.live = live_client
        self.dir = cassette_dir
        self.meta = meta or {}
        os.makedirs(cassette_dir, exist_ok=True)
        if redactor is None:
            try:
                from .cassette_redactor import redact_cassette
                redactor = redact_cassette
            except Exception:
                # Last-resort no-op (still passes raw text — tests should use injection)
                redactor = lambda d: d
        self._redact = redactor

    def chat(self, *, tier, messages, response_format=None,
             max_tokens=None, temperature=None) -> LLMResponse:
        rsp = self.live.chat(
            tier=tier, messages=messages, response_format=response_format,
            max_tokens=max_tokens, temperature=temperature,
        )
        self._persist(tier=tier, messages=messages,
                       response_format=response_format, rsp=rsp,
                       extra_meta={"max_tokens": max_tokens, "temperature": temperature})
        return rsp

    def vision(self, *, messages, image_bytes, response_format=None,
               max_tokens=None) -> LLMResponse:
        import hashlib as _hl
        rsp = self.live.vision(
            messages=messages, image_bytes=image_bytes,
            response_format=response_format, max_tokens=max_tokens,
        )
        ihash = _hl.sha256(image_bytes).hexdigest()[:16]
        wrapped_messages = list(messages) + [
            {"role": "image", "content": {"image_sha256": ihash}}
        ]
        self._persist(tier="vision", messages=wrapped_messages,
                       response_format=response_format, rsp=rsp,
                       extra_meta={"max_tokens": max_tokens, "image_sha256": ihash})
        return rsp

    def _persist(self, *, tier, messages, response_format, rsp, extra_meta):
        import datetime as _dt
        h = CassetteLLMClient._hash_request(tier, messages, response_format)
        cassette = {
            "request": {
                "tier": tier,
                "messages": messages,
                "response_format": response_format,
                **extra_meta,
            },
            "response": {
                "text": rsp.text,
                "structured": rsp.structured,
                "finish_reason": rsp.finish_reason,
                "usage": {
                    "tokens_in": rsp.usage.tokens_in,
                    "tokens_out": rsp.usage.tokens_out,
                    "model_used": rsp.usage.model_used,
                    "latency_ms": rsp.usage.latency_ms,
                    "cost_usd_estimate": rsp.usage.cost_usd_estimate,
                },
            },
            "meta": {
                "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                **self.meta,
            },
        }
        # Redact + write
        try:
            cassette = self._redact(cassette)
        except Exception:
            pass  # fall through; raw cassette better than crash
        path = os.path.join(self.dir, f"{h}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cassette, f, ensure_ascii=False, indent=2, default=str)


# --- Factory ------------------------------------------------------------------

def make_llm_client(config, *, cassette_dir: Optional[str] = None) -> LLMClient:
    """
    Build LLMClient based on config.openai_test_mode.
        - mock      → MockLLMClient (default, used in unit tests)
        - cassette  → CassetteLLMClient(cassette_dir)
        - live      → OpenAILLMClient (requires API key)
    """
    mode = (getattr(config, "openai_test_mode", "mock") or "mock").lower()
    if mode == "mock":
        return MockLLMClient(config)
    if mode == "cassette":
        path = cassette_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "tests", "cassettes",
        )
        return CassetteLLMClient(path, config)
    if mode == "live":
        return OpenAILLMClient(config)
    if mode == "record":
        live_client = OpenAILLMClient(config)
        path = cassette_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "tests", "cassettes",
        )
        return RecordingLLMClient(live_client, path)
    raise ValueError(f"unknown openai_test_mode={mode!r}; expected mock|cassette|live|record")
