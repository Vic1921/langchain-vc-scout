"""Pydantic models for the agent's structured output.

The model is intentionally rich: every per-company field is something a
European VC partner would actually scan for in a one-pager (regulatory
exposure, sovereignty thesis fit, vintage match, funding signal). Keeping
the LLM on this schema means the downstream layers (SQLite, alerts, PDF)
don't have to parse free text.
"""

from pydantic import BaseModel, Field


class ScoredCompany(BaseModel):
    name: str = Field(description="Company / startup name exactly as it appeared in the source")
    sources: list[str] = Field(
        default_factory=list,
        description="Source URLs the company was mentioned in (use the URLs passed to scrape_headlines)",
    )
    score_market: int = Field(ge=1, le=10, description="Market potential, 1-10")
    score_team: int = Field(ge=1, le=10, description="Team strength inferable from the source, 1-10")
    score_product: int = Field(ge=1, le=10, description="Product innovation, 1-10")
    score_defensibility: int = Field(ge=1, le=10, description="Defensibility / moat, 1-10")
    score_interest: int = Field(ge=1, le=10, description="Overall investor interest signal, 1-10")
    rationale: str = Field(description="2-3 sentences on why these scores, with [source: <url>] citations inline")
    regulatory_tag: str = Field(
        description=(
            "EU regulatory exposure. Pick from: 'AI Act high-risk', 'AI Act limited-risk', "
            "'DORA in-scope', 'MiCA-favored', 'MiCA-burdened', 'NIS2 in-scope', 'GDPR-sensitive', "
            "'low' — or combine (comma-separated). 'low' if no significant exposure."
        ),
    )
    sovereignty_tag: str = Field(
        description=(
            "EU strategic-autonomy thesis fit. Pick from: 'defense-dual-use', 'semiconductors', "
            "'sovereign-cloud', 'biotech', 'energy', 'critical-materials', 'none'."
        ),
    )
    vintage_match: str = Field(
        description=(
            "Closest historical analogue with the pattern name and 5-10 word descriptor, e.g. "
            "'Mistral-pattern: open-source AI + EU sovereignty + DeepMind-alumni moat'. "
            "Write 'n/a' if no clear match."
        ),
    )
    funding_signal: str = Field(
        default="none",
        description=(
            "Funding-round signal pulled verbatim from the source if present, e.g. "
            "'raised €40M Series A led by Index Ventures'. Write 'none' if no funding signal."
        ),
    )

    @property
    def score_total(self) -> float:
        """Average of the five sub-scores, rounded to one decimal."""
        return round(
            (
                self.score_market
                + self.score_team
                + self.score_product
                + self.score_defensibility
                + self.score_interest
            )
            / 5.0,
            1,
        )


class VCScoutOutput(BaseModel):
    headline_summary: str = Field(description="Cross-source synthesis of what's happening today")
    why_it_matters: str = Field(description="Why this matters for an early-stage software investor with European focus")
    visible_only_when_combined: str = Field(
        description=(
            "Signals that only become visible when the sources are read together: "
            "same company in two feeds, contradictions, theme convergence. The mosaic-mode "
            "section — this is where the agent earns its keep."
        ),
    )
    possible_investment_angle: str = Field(description="Prioritized investment theses informed by all sources")
    contrarian_view: str = Field(
        description=(
            "Howard-Marks-style contrarian pass: where does the coverage feel unanimous, "
            "and what would have to be true for the consensus to be wrong?"
        ),
    )
    risks_or_limitations: str = Field(description="Risks, data gaps, source bias")
    themes: list[str] = Field(default_factory=list, description="3-6 recurring themes across the sources, short noun phrases")
    companies: list[ScoredCompany] = Field(default_factory=list)
