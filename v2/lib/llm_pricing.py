"""
v2.lib.llm_pricing — Per-model USD-per-token estimates.

Phase 2 follow-up: live recording reported tokens_in=96540, tokens_out=12244,
but cost_usd_estimate=$0.0000 because the cost field was never populated.
This module provides a stable price table + estimator that the OpenAI client
plumbs through `LLMUsage.cost_usd_estimate`.

Pricing semantics:
  - Prices are approximate USD per token (already divided by 1000 in the table
    for readability). Sourced from OpenAI's public pricing page snapshot taken
    in May 2026 — DO NOT cite as authoritative; bump as needed.
  - If a model is not in the table, `estimate_cost` returns `None`. Downstream
    code must treat None as "unknown — do not display $0.00".
  - Vision pages count toward tokens_in via the OpenAI image-tokens estimate
    (already returned in usage.prompt_tokens).

Public API:
    MODEL_PRICING_USD_PER_TOKEN  — dict[model_name -> {"in": float, "out": float}]
    estimate_cost(model_name, tokens_in, tokens_out) -> Optional[float]
    format_cost(cost: Optional[float]) -> str   # "$0.0123" or "unknown"
"""

from __future__ import annotations

from typing import Optional


# All values are USD **per single token** (NOT per 1,000 tokens).
# Source: OpenAI public pricing (May 2026 snapshot), divided by 1000.
# Update as quotes change. These are ESTIMATES for budget guardrails; they
# are NOT exact OpenAI billing — actual invoices may differ due to caching,
# enterprise discounts, prompt cache hits, etc.
#
# Phase 2 follow-up bug fix: the previous values were per-1k-token figures
# stored under a "per token" name; estimate_cost() multiplied by raw token
# counts → 1000× overstated cost ($407 for ~$0.41 of real usage). Now values
# are stored at the unit advertised by the variable name.
MODEL_PRICING_USD_PER_TOKEN: dict[str, dict[str, float]] = {
    # GPT-5 family (best-guess placeholders for gpt-5.x model ids)
    "gpt-5":            {"in": 0.0000015,  "out": 0.0000060},
    "gpt-5-mini":       {"in": 0.00000030, "out": 0.0000012},
    "gpt-5-nano":       {"in": 0.00000005, "out": 0.0000002},
    "gpt-5.1":          {"in": 0.0000020,  "out": 0.0000080},
    "gpt-5-vision":     {"in": 0.0000025,  "out": 0.0000100},

    # GPT-4o family
    "gpt-4o":           {"in": 0.0000025,  "out": 0.0000100},
    "gpt-4o-mini":      {"in": 0.00000015, "out": 0.0000006},
    "gpt-4o-mini-2024-07-18": {"in": 0.00000015, "out": 0.0000006},
}


def estimate_cost(model_name: Optional[str],
                  tokens_in: int,
                  tokens_out: int) -> Optional[float]:
    """
    Estimate USD cost for a single LLM call.

    Returns:
        float — USD cost (>= 0), if `model_name` is in the pricing table.
        None  — if model is unknown. Caller must format as "unknown", not $0.
    """
    if not model_name:
        return None
    p = MODEL_PRICING_USD_PER_TOKEN.get(model_name)
    if p is None:
        return None
    cost = (tokens_in or 0) * p["in"] + (tokens_out or 0) * p["out"]
    return round(cost, 6)


def format_cost(cost: Optional[float]) -> str:
    """Render a per-call cost (USD estimate) for human-readable reports/logs.

    Always reflects an *estimate* — never claim it matches OpenAI billing.
    Callers that want the disclaimer included in the rendered string should
    use `format_cost_with_disclaimer()` instead.
    """
    if cost is None:
        return "unknown"
    return f"${cost:.4f}"


def format_cost_with_disclaimer(cost: Optional[float]) -> str:
    """Same as format_cost but appends an 'estimate only' note."""
    base = format_cost(cost)
    if base == "unknown":
        return base
    return f"{base} (estimate, not exact OpenAI billing)"


def sum_costs(costs: list[Optional[float]]) -> tuple[Optional[float], int]:
    """
    Sum a list of per-call costs.

    Returns (total_cost, unknown_count). If unknown_count > 0, callers should
    surface that the total is a lower bound — at least one call had no price.
    """
    total: float = 0.0
    unknown = 0
    for c in costs:
        if c is None:
            unknown += 1
            continue
        total += c
    return (round(total, 6) if unknown == 0 else (round(total, 6) if total > 0 else None),
            unknown)
