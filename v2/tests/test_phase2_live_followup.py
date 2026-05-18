"""
Sprint 4 Phase 2 LIVE-accuracy follow-up tests.

Closes three operational findings after Codex's Phase 2 live recording:
  L1. Pricing estimator was 1000× too high — values were per-1k-token
      stored under a "per token" name. Fixed; new tests lock in semantics.
  L2. Vision-only values must not push money-critical fields above the
      0.84 cap. Without regex corroboration, single_supplement (policy
      0.90) stays below threshold → handoff.
  L3. Duplicate-value detection forces handoff when the LLM hallucinated
      the same number into two money-critical fields.

All tests mock-only; no live OpenAI call.
"""

from __future__ import annotations

import pytest

from v2.lib.llm_pricing import (
    MODEL_PRICING_USD_PER_TOKEN, estimate_cost, format_cost,
    format_cost_with_disclaimer,
)
from v2.scraper.extract_fees import ExtractionResult
from v2.scraper.ondemand_vision import (
    _bump_field_confidence_from_vision,
    _apply_duplicate_value_penalty,
    VISION_PER_FIELD_CAP, DUPLICATE_VALUE_CONF,
)


# ---- L1: pricing units ------------------------------------------------------

class TestPricingPerTokenUnits:
    """The dict name says PER_TOKEN — values must reflect that."""

    def test_gpt_4o_input_price_is_per_token_not_per_1k(self):
        # OpenAI quote (May 2026): $0.0025 per 1k input tokens.
        # Stored as $0.0000025 per single token.
        p = MODEL_PRICING_USD_PER_TOKEN["gpt-4o"]
        assert p["in"] == pytest.approx(0.0000025, rel=0.05)
        assert p["out"] == pytest.approx(0.00001, rel=0.05)

    def test_realistic_vision_call_cost_is_pennies(self):
        # Phase 2 live aggregate: 126,840 in + 9,020 out → must be ~$0.41 not $407
        cost = estimate_cost("gpt-4o", 126840, 9020)
        assert cost is not None
        assert 0.30 < cost < 0.60, f"got ${cost} — pricing regression"

    def test_unknown_model_still_returns_none(self):
        assert estimate_cost("gpt-99-mystery", 100, 100) is None

    def test_format_cost_with_disclaimer_says_estimate(self):
        s = format_cost_with_disclaimer(0.41)
        assert "$0.4100" in s
        assert "estimate" in s.lower() and "billing" in s.lower()

    def test_format_cost_with_disclaimer_unknown_stays_unknown(self):
        assert format_cost_with_disclaimer(None) == "unknown"


# ---- L2: vision-only cap (regex must corroborate to push above 0.84) -------

class TestVisionOnlyCap:
    def test_vision_only_caps_at_VISION_PER_FIELD_CAP(self):
        """No regex baseline → vision-only confidence is capped after the
        merge+bump flow that extract_fees_on_demand actually uses."""
        from v2.scraper.extract_fees import _merge_results
        merged = ExtractionResult()                                # no regex
        page = ExtractionResult(
            tip_amount=2000, deposit_amount=10000, single_supplement=5500,
            extraction_confidence=0.95,
        )
        # Mirror the runtime flow inside extract_fees_on_demand:
        merged = _merge_results(merged, page)
        _bump_field_confidence_from_vision(merged, page)
        # Vision tried 0.95; cap holds at 0.84 because no regex baseline.
        assert merged.tip_confidence == VISION_PER_FIELD_CAP
        assert merged.deposit_confidence == VISION_PER_FIELD_CAP
        assert merged.single_supplement_confidence == VISION_PER_FIELD_CAP

    def test_regex_corroboration_allows_vision_to_exceed_cap(self):
        """When per-field conf > 0 (regex matched), vision can lift fully."""
        from v2.scraper.extract_fees import _merge_results
        merged = ExtractionResult(
            tip_amount=2000, tip_confidence=0.85,                 # regex baseline
            single_supplement=5500, single_supplement_confidence=0.82,
        )
        page = ExtractionResult(
            tip_amount=2000, single_supplement=5500,
            extraction_confidence=0.92,
        )
        merged = _merge_results(merged, page)
        _bump_field_confidence_from_vision(merged, page)
        assert merged.tip_confidence == pytest.approx(0.92, rel=0.01)
        assert merged.single_supplement_confidence == pytest.approx(0.92, rel=0.01)

    def test_lower_confidence_vision_does_not_downgrade(self):
        """Symmetric guarantee from N1 still holds."""
        from v2.scraper.extract_fees import _merge_results
        merged = ExtractionResult(
            tip_amount=2000, tip_confidence=0.85,
        )
        page = ExtractionResult(
            tip_amount=2000, extraction_confidence=0.50,
        )
        merged = _merge_results(merged, page)
        _bump_field_confidence_from_vision(merged, page)
        assert merged.tip_confidence == 0.85   # unchanged

    def test_single_supplement_vision_only_stays_below_policy_threshold(self):
        """The cap is calibrated to keep single_supplement vision-only below
        policy 0.90 → bot will handoff instead of guessing."""
        from v2.scraper.extract_fees import _merge_results
        from v2.lib.fee_answer_policy import decide_fee_answer, SINGLE_SUPPLEMENT_THRESHOLD
        merged = ExtractionResult()  # no regex
        page = ExtractionResult(
            single_supplement=6000, extraction_confidence=0.93,
        )
        merged = _merge_results(merged, page)
        _bump_field_confidence_from_vision(merged, page)
        # Conf capped at 0.84 < policy 0.90 → handoff
        row = {
            "single_supplement": merged.single_supplement,
            "single_supplement_confidence": merged.single_supplement_confidence,
        }
        d = decide_fee_answer(row, "single_supplement")
        assert d.decision == "handoff_low_confidence"
        assert d.threshold == SINGLE_SUPPLEMENT_THRESHOLD


# ---- L3: duplicate-value penalty -------------------------------------------

class TestDuplicateValuePenalty:
    def test_two_fields_same_value_drops_both_confidences(self):
        merged = ExtractionResult(
            tip_amount=5500, tip_confidence=0.90,
            single_supplement=5500, single_supplement_confidence=0.92,
        )
        _apply_duplicate_value_penalty(merged)
        assert merged.tip_confidence == DUPLICATE_VALUE_CONF
        assert merged.single_supplement_confidence == DUPLICATE_VALUE_CONF

    def test_three_fields_same_value_drops_all_three(self):
        merged = ExtractionResult(
            tip_amount=2000, tip_confidence=0.95,
            deposit_amount=2000, deposit_confidence=0.92,
            infant_fee=2000,   # no conf column — participates but isn't penalized directly
        )
        _apply_duplicate_value_penalty(merged)
        assert merged.tip_confidence == DUPLICATE_VALUE_CONF
        assert merged.deposit_confidence == DUPLICATE_VALUE_CONF

    def test_zero_values_are_not_duplicates(self):
        """visa_fee=0 (from visa_status='exempt') must NOT trigger the penalty."""
        merged = ExtractionResult(
            tip_amount=0, tip_confidence=0.95,
            deposit_amount=0, deposit_confidence=0.95,
            visa_fee=0, visa_confidence=0.95,
        )
        _apply_duplicate_value_penalty(merged)
        # No penalty: 0 values are skipped
        assert merged.tip_confidence == 0.95
        assert merged.visa_confidence == 0.95

    def test_distinct_values_unaffected(self):
        merged = ExtractionResult(
            tip_amount=2000, tip_confidence=0.85,
            deposit_amount=15000, deposit_confidence=0.85,
            single_supplement=6000, single_supplement_confidence=0.92,
            visa_fee=1650, visa_confidence=0.85,
        )
        _apply_duplicate_value_penalty(merged)
        # No two values are equal → no penalty
        assert merged.tip_confidence == 0.85
        assert merged.deposit_confidence == 0.85
        assert merged.single_supplement_confidence == 0.92
        assert merged.visa_confidence == 0.85

    def test_already_below_penalty_threshold_not_raised(self):
        merged = ExtractionResult(
            tip_amount=5500, tip_confidence=0.30,
            single_supplement=5500, single_supplement_confidence=0.30,
        )
        _apply_duplicate_value_penalty(merged)
        # Already below DUPLICATE_VALUE_CONF (0.50) → unchanged (don't bump UP)
        assert merged.tip_confidence == 0.30
        assert merged.single_supplement_confidence == 0.30

    def test_duplicate_forces_handoff_via_policy(self):
        """End-to-end: a duplicate value can no longer answer."""
        from v2.lib.fee_answer_policy import decide_fee_answer
        merged = ExtractionResult(
            tip_amount=2000, tip_confidence=0.95,
            infant_fee=2000,
        )
        _apply_duplicate_value_penalty(merged)
        # tip dropped to 0.50 < policy 0.80 → handoff
        row = {"tip_amount": merged.tip_amount,
               "tip_confidence": merged.tip_confidence}
        d = decide_fee_answer(row, "tip")
        assert d.decision == "handoff_low_confidence"


# ---- Combined safety check -------------------------------------------------

class TestCombinedSafetyFlow:
    """Vision hallucinates tip=5500 (same as the single-supplement table-col
    value it also saw). Both safeguards should fire: vision-only cap +
    duplicate detection. Result: BOTH fields handoff."""

    def test_hallucinated_duplicate_results_in_handoff(self):
        from v2.scraper.extract_fees import _merge_results
        merged = ExtractionResult()  # no regex baseline
        page = ExtractionResult(
            tip_amount=5500, single_supplement=5500,
            extraction_confidence=0.93,
        )
        merged = _merge_results(merged, page)
        _bump_field_confidence_from_vision(merged, page)
        # Cap → 0.84 each; then duplicate-penalty → 0.50 each
        assert merged.tip_confidence == DUPLICATE_VALUE_CONF
        assert merged.single_supplement_confidence == DUPLICATE_VALUE_CONF
        # Both below their policy threshold → handoff
        from v2.lib.fee_answer_policy import decide_fee_answer
        assert decide_fee_answer(
            {"tip_amount": 5500, "tip_confidence": merged.tip_confidence},
            "tip",
        ).decision == "handoff_low_confidence"
        assert decide_fee_answer(
            {"single_supplement": 5500,
             "single_supplement_confidence": merged.single_supplement_confidence},
            "single_supplement",
        ).decision == "handoff_low_confidence"
