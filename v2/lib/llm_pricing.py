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


# All values are USD per token (i.e. divided by 1000 from per-1k-token quotes).
# Source: OpenAI public pricing (May 2026 snapshot). Update as quotes change.
MODEL_PRICING_USD_PER_TOKEN: dict[str, dict[str, float]] = {
    # GPT-5 family (price guesses for placeholder gpt-5.x ids)
    "gpt-5":            {"in": 0.0015,  "out": 0.0060},
    "gpt-5-mini":       {"in": 0.00030, "out": 0.0012},
    "gpt-5-nano":       {"in": 0.00005, "out": 0.0002},
    "gpt-5.1":          {"in": 0.0020,  "out": 0.0080},
    "gpt-5-vision":     {"in": 0.0025,  "out": 0.0100},

    # GPT-4o family
    "gpt-4o":           {"in": 0.0025,  "out": 0.0100},
    "gpt-4o-mini":      {"in": 0.00015, "out": 0.0006},
    "gpt-4o-mini-2024-07-18": {"in": 0.00015, "out": 0.0006},
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
    """Render a per-call cost for human-readable reports / logs."""
    if cost is None:
        return "unknown"
    return f"${cost:.4f}"


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
