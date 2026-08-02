"""
Token pricing, in one place.

Split out because two callers need it - the live trace recorder and
measure_cold.py - and two copies of a price table is how they end up
disagreeing.

These numbers are hardcoded and WILL go stale. Everything derived from
them is an estimate, and is labelled as such wherever it is shown. A
wrong number presented confidently is worse than no number, so if you
are quoting a figure anywhere it matters, check the real bill.

Rates are USD per 1M tokens. Source: openai.com/api/pricing
"""
from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4.1": {"in": 2.00, "out": 8.00},
    "gpt-4.1-mini": {"in": 0.40, "out": 1.60},
    "gpt-4.1-nano": {"in": 0.10, "out": 0.40},
}

# Used when a model name isn't in the table. Deliberately the expensive
# one: an unknown model that quietly costs nothing would hide spend, and
# overestimating is the safer direction for anything feeding a cap.
FALLBACK = "gpt-4o"


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING.get(model) or PRICING[FALLBACK]
    return input_tokens / 1_000_000 * rates["in"] + output_tokens / 1_000_000 * rates["out"]
