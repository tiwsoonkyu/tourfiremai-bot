"""
v2.lib.intent — Intent classifier with rule-based stub + optional LLM upgrade.

Sprint 2 deliverable: NO live LLM call. Pure rule-based classifier that:
  - Uses keyword + regex patterns to detect intent
  - Returns a Pydantic-style Intent (without requiring pydantic — keeps deps light)
  - Has a clean injection point for LLM (`classify_with_llm`) used in Sprint 3+

The LLM path is opt-in via `enable_llm=True` AND `config.has_llm`. In tests,
we never enable LLM — keeps test runs free of network/secrets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

# Lazy import: country/code normalization
from .country import normalize_country_typo
from .tour_codes import classify_code


IntentType = Literal[
    "greeting", "ask_country", "ask_budget", "ask_pax", "ask_period",
    "ask_tour_detail", "ask_fee", "select_tour", "select_departure",
    "confirm_booking", "ask_human", "send_attachment", "payment_keyword",
    "decline_final", "off_topic_strong", "off_topic", "unknown",
]


@dataclass
class Intent:
    type: str = "unknown"
    raw_text: str = ""
    country: Optional[str] = None
    country_id: Optional[int] = None
    budget: Optional[int] = None
    budget_type: Optional[str] = None       # 'strict' | 'flexible'
    pax_count: Optional[int] = None
    travel_period: Optional[str] = None
    selected_index: Optional[int] = None
    selected_code: Optional[str] = None
    has_attachment: bool = False
    confidence: float = 0.5
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": self.type, "country": self.country, "country_id": self.country_id,
            "budget": self.budget, "budget_type": self.budget_type,
            "pax_count": self.pax_count, "travel_period": self.travel_period,
            "selected_index": self.selected_index, "selected_code": self.selected_code,
            "has_attachment": self.has_attachment, "confidence": self.confidence,
        }


# --- Rule patterns ------------------------------------------------------------

_GREETING_RE = re.compile(r"(สวัสดี|สวัส|หวัดดี|hi\b|hello\b|hey\b|ดีค่า|ดีครับ)", re.I)
_ASK_HUMAN_RE = re.compile(
    r"(แอดมิน|พนักงาน|คุยกับ\s*คน|คุยคน|human|live\s*agent|operator|เจ้าหน้าที่)",
    re.I,
)
_PAYMENT_RE = re.compile(
    r"(โอน|ชำระ|จ่ายเงิน|บัญชี|พร้อมเพย์|promptpay|qr\s*code|สลิป|slip|transfer)",
    re.I,
)
_FEE_RE = re.compile(
    r"(ค่าทิป|ทิป|tip|วีซ่า|visa|พักเดี่ยว|single\s*supp|มัดจำ|deposit|ค่าใช้จ่ายเพิ่ม|ค่าเพิ่ม)",
    re.I,
)
_DECLINE_RE = re.compile(r"(ไม่เอา|ไม่สนใจ|ไม่อยากไป|cancel|ยกเลิก)", re.I)
_CONFIRM_BOOK_RE = re.compile(
    r"(จอง|book|ตกลง|เอา\s*เลย|ok\s*ครับ|ok\s*ค่ะ|พร้อมจ่าย)",
    re.I,
)

# Budget extraction: "งบ 30,000" or "30k" or "ไม่เกิน 25000"
_BUDGET_AMOUNT_RE = re.compile(
    r"(?:งบ|budget|ไม่เกิน|ประมาณ|around)?\s*"
    r"([1-9]\d{0,2}(?:,?\d{3})+|\d{4,6}|\d+\s*k)",
    re.I,
)
_BUDGET_STRICT_RE = re.compile(r"(ไม่เกิน|ห้ามเกิน|under|max|ขั้นต่ำ|low)", re.I)
_BUDGET_FLEX_RE = re.compile(r"(ประมาณ|ราว|around|about|แถว|sukha)", re.I)

# Pax: "4 คน" / "2 ผู้ใหญ่"
_PAX_RE = re.compile(r"(\d{1,2})\s*(?:คน|ผู้ใหญ่|adult|pax|person)", re.I)

# Period: "เดือนหน้า" / "ก.ค." / "July" / "2026-07"
_PERIOD_RE = re.compile(
    r"(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"เดือนหน้า|เดือนนี้|next\s*month)",
    re.I,
)

# Selection-related signals: "ตัวที่ N", "เอาตัวแรก", "ตัวสอง"
_INDEX_RE = re.compile(
    r"(?:ตัวที่|อันที่|ลำดับที่?)\s*(\d+)|"
    r"ตัวแรก|ตัวที่สอง|ตัวที่สาม",
    re.I,
)
_TH_INDEX_WORDS = {
    "ตัวแรก": 1, "ตัวที่สอง": 2, "ตัวที่สาม": 2, "ที่หนึ่ง": 1, "ที่สอง": 2, "ที่สาม": 3,
}


def _parse_int_amount(raw: str) -> Optional[int]:
    if not raw:
        return None
    s = raw.lower().replace(",", "").replace(" ", "")
    if s.endswith("k"):
        try:
            return int(float(s[:-1]) * 1000)
        except ValueError:
            return None
    try:
        n = int(s)
        if 1_000 <= n <= 1_000_000:
            return n
    except ValueError:
        return None
    return None


def _parse_budget(text: str) -> tuple[Optional[int], Optional[str]]:
    if not text:
        return None, None
    m = _BUDGET_AMOUNT_RE.search(text)
    if not m:
        return None, None
    amount = _parse_int_amount(m.group(1))
    if not amount:
        return None, None
    if _BUDGET_STRICT_RE.search(text):
        return amount, "strict"
    if _BUDGET_FLEX_RE.search(text):
        return amount, "flexible"
    return amount, "flexible"


def _parse_index(text: str) -> Optional[int]:
    if not text:
        return None
    m = _INDEX_RE.search(text)
    if m:
        if m.group(1):
            return int(m.group(1))
        for word, n in _TH_INDEX_WORDS.items():
            if word in text:
                return n
    return None


# --- Main classifier ---------------------------------------------------------

def classify_rule_based(
    text: str,
    *,
    attachments: Optional[list] = None,
    current_state: Optional[str] = None,
) -> Intent:
    """
    Rule-based intent classifier. Always returns an Intent (never None).
    Confidence is set heuristically (1.0 for unambiguous matches, lower for fallbacks).
    """
    text = text or ""
    attachments = attachments or []

    intent = Intent(raw_text=text)

    # 1) Attachment intent dominates
    if attachments:
        intent.type = "send_attachment"
        intent.has_attachment = True
        intent.confidence = 1.0
        return intent

    # 2) Universal triggers
    if _ASK_HUMAN_RE.search(text):
        intent.type = "ask_human"
        intent.confidence = 1.0
        return intent
    if _PAYMENT_RE.search(text):
        intent.type = "payment_keyword"
        intent.confidence = 1.0
        return intent
    if _DECLINE_RE.search(text):
        intent.type = "decline_final"
        intent.confidence = 0.9
        return intent

    # 3) Code selection (overrides index)
    code = classify_code(text)
    if code["kind"] in ("web", "tour_code_real"):
        intent.type = "select_tour"
        intent.selected_code = code["value"]
        intent.confidence = 1.0
        return intent

    # 4) Index selection
    idx = _parse_index(text)
    if idx is not None:
        intent.type = "select_tour"
        intent.selected_index = idx
        intent.confidence = 0.95
        return intent

    # 5) Confirm booking
    if _CONFIRM_BOOK_RE.search(text):
        intent.type = "confirm_booking"
        intent.confidence = 0.85
        return intent

    # 6) Fee question
    if _FEE_RE.search(text):
        intent.type = "ask_fee"
        intent.confidence = 0.95
        return intent

    # 7) Parse criteria components (additive)
    country, country_id = normalize_country_typo(text)
    budget, budget_type = _parse_budget(text)
    period_m = _PERIOD_RE.search(text)
    pax_m = _PAX_RE.search(text)

    intent.country = country
    intent.country_id = country_id
    intent.budget = budget
    intent.budget_type = budget_type
    intent.travel_period = period_m.group(0) if period_m else None
    intent.pax_count = int(pax_m.group(1)) if pax_m else None

    # 8) Type from strongest signal
    if country and not budget:
        intent.type = "ask_country"
        intent.confidence = 0.85
    elif budget:
        intent.type = "ask_budget"
        intent.confidence = 0.9
    elif intent.pax_count is not None:
        intent.type = "ask_pax"
        intent.confidence = 0.85
    elif intent.travel_period:
        intent.type = "ask_period"
        intent.confidence = 0.8
    elif _GREETING_RE.search(text):
        intent.type = "greeting"
        intent.confidence = 0.95
    elif text.strip():
        intent.type = "ask_tour_detail" if country else "unknown"
        intent.confidence = 0.4
    else:
        intent.type = "unknown"
        intent.confidence = 0.1

    return intent


def classify(
    text: str,
    *,
    attachments: Optional[list] = None,
    current_state: Optional[str] = None,
    enable_llm: bool = False,
    llm_client=None,
) -> Intent:
    """
    Public entry. By default uses rule-based only.

    Sprint 3+: set enable_llm=True and pass an OpenAI client to upgrade
    accuracy on ambiguous inputs. The rule-based result is always the
    fallback if LLM call fails.
    """
    rule_intent = classify_rule_based(text, attachments=attachments, current_state=current_state)

    if not enable_llm or llm_client is None or rule_intent.confidence >= 0.9:
        return rule_intent

    try:
        return classify_with_llm(text, rule_intent, llm_client, current_state)
    except Exception as e:
        rule_intent.notes.append(f"llm_failed: {type(e).__name__}")
        return rule_intent


def classify_with_llm(text: str, rule_intent: Intent, llm_client, current_state: Optional[str]) -> Intent:
    """
    LLM-assisted classification. NOT called in Sprint 2 tests.
    Sprint 3 will wire OpenAI/Anthropic here with structured output.
    """
    raise NotImplementedError(
        "LLM intent classification is a Sprint 3+ deliverable. "
        "Sprint 2 uses rule-based only."
    )
