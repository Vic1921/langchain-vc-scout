"""Inbound-deal triage — score a pasted company against the scout's rubric.

This flips the scout from an outbound radar into an analyst's desk: drop in a
deck summary or company blurb and get it scored on the same five axes the
daily scan uses, plus the closest comparables drawn from the knowledge base
("you've seen 3 plays like this — here's where it ranks").
"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model

from .costs import CostRecord, extract_cost
from .schema import TriageVerdict
from .storage import recent_high_scorers

logger = logging.getLogger(__name__)

TRIAGE_MODEL = "claude-sonnet-4-6"

TRIAGE_PROMPT = """You are a European software-VC analyst triaging an inbound company.

Score it on the scout's rubric: market potential, team strength, product
innovation, defensibility, investor interest (each 1-10). Tag EU regulatory
exposure and strategic-autonomy thesis fit. Give a recommendation:
'pursue' (worth a partner's time now), 'track' (watchlist it), or 'pass'.

Pick the closest comparables ONLY from the knowledge-base list below, and say
in comparables_note how the inbound company compares to them. If the list is
empty or nothing is close, return an empty comparables list and say so.

Be concrete and skeptical — penalize hype, reward specifics.
"""


def _kb_context(limit: int = 40) -> str:
    """A compact digest of recently-seen companies for the model to compare against."""
    rows = recent_high_scorers(days=180, min_score=0.0)[:limit]
    if not rows:
        return "(knowledge base is empty — no comparables available)"
    return "\n".join(
        f"- {r['name']} (score {r['best_score']}; {r['sovereignty_tag']}; {r['regulatory_tag']})"
        for r in rows
    )


def triage_company(text: str) -> tuple[TriageVerdict, CostRecord]:
    """Triage one inbound company. Returns the verdict and the LLM call's cost."""
    context = _kb_context()
    model = init_chat_model(TRIAGE_MODEL, temperature=0.2, max_tokens=1500, timeout=60)
    triager = model.with_structured_output(TriageVerdict, include_raw=True)
    result = triager.invoke(
        f"{TRIAGE_PROMPT}\n\n--- KNOWLEDGE BASE (recent companies) ---\n{context}"
        f"\n\n--- INBOUND COMPANY ---\n{text.strip()}"
    )
    verdict = result["parsed"]
    if verdict is None:
        raise ValueError(f"triage produced no parseable verdict: {result.get('parsing_error')}")
    cost = extract_cost([result["raw"]], fallback_model=TRIAGE_MODEL)
    logger.info(
        "Triaged %s: %s/10, recommendation=%s",
        verdict.company, verdict.score_total, verdict.recommendation,
    )
    return verdict, cost


def render_triage(verdict: TriageVerdict) -> str:
    """Render a triage verdict as a Markdown note."""
    comparables = ", ".join(verdict.comparables) if verdict.comparables else "none in the KB"
    return f"""# Triage — {verdict.company}

**Recommendation:** {verdict.recommendation.upper()}  ·  **Score:** {verdict.score_total}/10

- **Scores:** market {verdict.score_market} · team {verdict.score_team} · product {verdict.score_product} · defensibility {verdict.score_defensibility} · interest {verdict.score_interest}
- **Regulatory:** {verdict.regulatory_tag}
- **Sovereignty thesis:** {verdict.sovereignty_tag}
- **KB comparables:** {comparables}

## Rationale
{verdict.rationale}

## How it compares
{verdict.comparables_note}
"""
