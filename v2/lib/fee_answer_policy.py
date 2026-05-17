"""
v2.lib.fee_answer_policy — Pure policy for "answer fee question from DB or handoff".

Sprint 4 follow-up. Bot must NEVER guess fee data.

Given a tour_fees row + which fee the customer asked about, decide:
  - 'answer'                  → use the DB value (response writer formats it)
  - 'handoff_no_fees_row'     → no row at all
  - 'handoff_missing'         → row exists but the asked field is NULL
  - 'handoff_low_confidence'  → field present but confidence below threshold

Per-field thresholds (per spec):
  - tip                          ≥ 0.80
  - deposit                      ≥ 0.80
  - visa (status or fee)         ≥ 0.80
  - single_supplement            ≥ 0.90  ← stricter until accuracy improves
  - infant / child_no_bed        ≥ 0.80 (fall back to row-level confidence)

If a per-field confidence column is NULL (rows extracted before migration 019),
falls back to row-level `extraction_confidence` with the SAME thresholds — so
old rows behave conservatively.

NO LLM call inside this module. No DB call. Pure function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# --- Constants ---------------------------------------------------------------

DEFAULT_THRESHOLD = 0.80
SINGLE_SUPPLEMENT_THRESHOLD = 0.90  # stricter — known accuracy gap

# AskedFee values used across the codebase. "any" = customer asked generic
# "ค่าใช้จ่ายมีอะไรบ้าง" without naming a specific field.
ASKED_FEE_VALUES = frozenset({
    "tip", "deposit", "single_supplement", "visa", "infant", "child_no_bed", "any",
})

# Map AskedFee → (value column in tour_fees, per-field confidence column or None, threshold)
_FIELD_MAP: dict[str, tuple[str, Optional[str], float]] = {
    "tip":               ("tip_amount",         "tip_confidence",               DEFAULT_THRESHOLD),
    "deposit":           ("deposit_amount",     "deposit_confidence",           DEFAULT_THRESHOLD),
    "single_supplement": ("single_supplement",  "single_supplement_confidence", SINGLE_SUPPLEMENT_THRESHOLD),
    "visa":              ("visa_fee",           "visa_confidence",              DEFAULT_THRESHOLD),
    "infant":            ("infant_fee",          None,                          DEFAULT_THRESHOLD),
    "child_no_bed":      ("child_fee_no_bed",    None,                          DEFAULT_THRESHOLD),
}


# --- Result type -------------------------------------------------------------

@dataclass
class FeeAnswerDecision:
    decision: str            # 'answer' | 'handoff_no_fees_row' | 'handoff_missing' | 'handoff_low_confidence' | 'handoff_unknown_field'
    asked_field: str
    value: Optional[int] = None
    confidence: Optional[float] = None
    threshold: Optional[float] = None
    visa_status: Optional[str] = None
    handoff_reason: Optional[str] = None

    @property
    def can_answer(self) -> bool:
        return self.decision == "answer"


# --- Asked-field detection ---------------------------------------------------

def detect_asked_field(text: Optional[str]) -> str:
    """
    Inspect raw customer text → return one of ASKED_FEE_VALUES.

    Order matters: more specific keywords first (e.g. "no bed" before "child").
    """
    if not text:
        return "any"
    t = text.lower()
    if any(kw in t for kw in ("ไม่เสริมเตียง", "ไม่มีเตียง", "no bed", "no-bed", "child no bed")):
        return "child_no_bed"
    if any(kw in t for kw in ("ทารก", "infant")):
        return "infant"
    if any(kw in t for kw in ("พักเดี่ยว", "single supp", "single supplement", "single-supp")):
        return "single_supplement"
    if any(kw in t for kw in ("วีซ่า", "visa")):
        return "visa"
    if any(kw in t for kw in ("มัดจำ", "deposit")):
        return "deposit"
    if any(kw in t for kw in ("ทิป", "tip")):
        return "tip"
    if any(kw in t for kw in ("ค่าใช้จ่ายเพิ่มเติม", "ค่าใช้จ่ายเพิ่ม", "อัตราค่าบริการ", "ราคารวม")):
        return "any"
    return "any"


# --- Main decision -----------------------------------------------------------

def _field_confidence(fees_row: dict, conf_col: Optional[str]) -> float:
    """Per-field confidence (if column populated) else fall back to row-level."""
    if conf_col is not None:
        v = fees_row.get(conf_col)
        if v is not None:
            return float(v)
    row_v = fees_row.get("extraction_confidence")
    return float(row_v or 0)


def decide_fee_answer(fees_row: Optional[dict], asked_field: str) -> FeeAnswerDecision:
    """
    Decide whether the bot can answer the asked fee question from the DB row.

    Args:
        fees_row: row from `tour_fees` (or None if no row exists).
        asked_field: one of ASKED_FEE_VALUES. Unknown values map to 'any'.

    Returns FeeAnswerDecision.
    """
    if asked_field not in ASKED_FEE_VALUES:
        asked_field = "any"

    if fees_row is None:
        return FeeAnswerDecision(
            decision="handoff_no_fees_row",
            asked_field=asked_field,
            handoff_reason="no_fee_row",
        )

    # Special case: generic "any" fees question — needs row to be reasonably complete
    if asked_field == "any":
        row_conf = float(fees_row.get("extraction_confidence") or 0)
        all_required_present = all(
            fees_row.get(f) is not None
            for f in ("tip_amount", "deposit_amount", "single_supplement")
        )
        visa_decided = (
            fees_row.get("visa_fee") is not None
            or fees_row.get("visa_status") in ("exempt", "required", "on_arrival", "evisa")
        )
        if row_conf >= DEFAULT_THRESHOLD and all_required_present and visa_decided:
            return FeeAnswerDecision(
                decision="answer", asked_field="any",
                confidence=row_conf, threshold=DEFAULT_THRESHOLD,
                visa_status=fees_row.get("visa_status"),
            )
        return FeeAnswerDecision(
            decision="handoff_low_confidence",
            asked_field="any",
            confidence=row_conf,
            threshold=DEFAULT_THRESHOLD,
            handoff_reason="row_below_threshold_or_incomplete",
        )

    value_col, conf_col, threshold = _FIELD_MAP[asked_field]
    value = fees_row.get(value_col)

    # Visa special handling: 'exempt' status counts as a valid answer with value 0.
    if asked_field == "visa":
        visa_status = fees_row.get("visa_status")
        if value is None and visa_status == "exempt":
            confidence = _field_confidence(fees_row, conf_col)
            if confidence < threshold:
                return FeeAnswerDecision(
                    decision="handoff_low_confidence",
                    asked_field="visa",
                    value=0, confidence=confidence, threshold=threshold,
                    visa_status="exempt", handoff_reason="visa_status_low_confidence",
                )
            return FeeAnswerDecision(
                decision="answer", asked_field="visa",
                value=0, confidence=confidence, threshold=threshold,
                visa_status="exempt",
            )

    if value is None:
        return FeeAnswerDecision(
            decision="handoff_missing", asked_field=asked_field,
            handoff_reason="field_value_missing",
        )

    confidence = _field_confidence(fees_row, conf_col)
    if confidence < threshold:
        return FeeAnswerDecision(
            decision="handoff_low_confidence",
            asked_field=asked_field,
            value=int(value), confidence=confidence, threshold=threshold,
            handoff_reason="below_threshold",
        )

    return FeeAnswerDecision(
        decision="answer", asked_field=asked_field,
        value=int(value), confidence=confidence, threshold=threshold,
        visa_status=fees_row.get("visa_status") if asked_field == "visa" else None,
    )


# --- Answer formatting (Thai brand voice) -----------------------------------

_LABELS: dict[str, str] = {
    "tip":               "ค่าทิป",
    "deposit":           "ค่ามัดจำ",
    "single_supplement": "พักเดี่ยวเพิ่ม",
    "visa":              "ค่าวีซ่า",
    "infant":            "ค่าทารก",
    "child_no_bed":      "เด็กไม่เสริมเตียง",
}

ANSWER_SUFFIX = " ตามเอกสารโปรแกรมค่ะ"  # required phrase per spec


def format_fee_answer(decision: FeeAnswerDecision) -> str:
    """
    Compose the customer-facing reply when decision.can_answer is True.

    Length: ≤ 100 chars, ≤ 2 lines, brand-voice compliant (warm + concise).
    """
    if not decision.can_answer:
        raise ValueError("format_fee_answer called on a non-answer decision")
    label = _LABELS.get(decision.asked_field, "ค่าใช้จ่าย")
    if decision.asked_field == "visa" and decision.visa_status == "exempt":
        return f"{label}: ฟรี (ไม่ต้องใช้วีซ่า){ANSWER_SUFFIX} ✨"
    return f"{label}: {decision.value:,} บาท{ANSWER_SUFFIX} 😊"
