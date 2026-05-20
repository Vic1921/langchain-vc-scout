"""Token-usage accounting for every LLM call the scout makes.

The cost ledger answers a question partners always ask: what does this thing
cost to run? Each agent / grader call's usage is extracted, priced at
Anthropic list rates, and persisted (see storage.record_cost). The daily
digest then reports the month-to-date spend.
"""

from __future__ import annotations

from dataclasses import dataclass


# USD per 1M tokens — Anthropic list prices (input, output), May 2026.
# Keys are substring-matched against the model name so version suffixes
# (e.g. claude-haiku-4-5-20251001) resolve without an exact-match table.
_RATES: dict[str, tuple[float, float]] = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}
_FALLBACK_RATE = _RATES["sonnet"]


@dataclass(frozen=True)
class CostRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Price a call at Anthropic list rates. Unknown models fall back to Sonnet."""
    name = (model or "").lower()
    rate = next((r for key, r in _RATES.items() if key in name), _FALLBACK_RATE)
    return round(input_tokens / 1_000_000 * rate[0] + output_tokens / 1_000_000 * rate[1], 4)


def _sum_usage(messages) -> tuple[str, int, int]:
    """Sum input/output tokens across messages; capture the reported model name."""
    model_name = ""
    input_tokens = output_tokens = 0
    for msg in messages:
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            input_tokens += usage.get("input_tokens", 0) or 0
            output_tokens += usage.get("output_tokens", 0) or 0
        meta = getattr(msg, "response_metadata", None) or {}
        model_name = meta.get("model_name") or meta.get("model") or model_name
    return model_name, input_tokens, output_tokens


def extract_cost(messages, fallback_model: str = "claude-sonnet-4-6") -> CostRecord:
    """Build a CostRecord from a list of LangChain messages.

    `messages` is the `messages` list from an agent response, or a single-item
    list wrapping the `raw` AIMessage from a with_structured_output call.
    """
    model_name, input_tokens, output_tokens = _sum_usage(messages)
    model_name = model_name or fallback_model
    return CostRecord(
        model=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=compute_cost(model_name, input_tokens, output_tokens),
    )
