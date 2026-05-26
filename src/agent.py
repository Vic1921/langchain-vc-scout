"""Agent: scraper tool + model + prompt + LangChain wiring.

The system prompt is opinionated on purpose. The reader is a European
software-investor partner, so we drill the model into producing the things
they actually use: cross-source synthesis, regulatory tags, vintage matches,
a contrarian pass, and source-cited rationales.
"""

import os

import requests
from bs4 import BeautifulSoup

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from .schema import VCScoutOutput


DEFAULT_MODEL_ID = "claude-sonnet-4-6"


SYSTEM_PROMPT = """
You are a sharp research assistant for a European software-focused VC fund.

You will be given MULTIPLE sources to analyze in a single pass. You MUST:
1. Call scrape_headlines exactly once per source URL provided.
2. Synthesize ACROSS the sources rather than producing per-source summaries — the value is in cross-source signal.
3. In the `visible_only_when_combined` field, explicitly call out signals that only emerge from synthesis: a company appearing in two feeds, a theme spiking simultaneously, one source contradicting another.
4. In the `contrarian_view` field, do a Howard-Marks pass: where does coverage feel unanimous, and what would have to be true for the consensus to be wrong?
5. Identify 3-6 recurring `themes` as short noun phrases.
6. For every distinct company/startup observed, populate a `ScoredCompany` entry with:
   - `sources`: the URL(s) the company was mentioned in (use the URLs you scraped, not article URLs).
   - `score_market`, `score_team`, `score_product`, `score_defensibility`, `score_interest`: integers 1-10.
   - `rationale`: 2-3 sentences with INLINE source citations in the form `[source: <url>]` so every claim is auditable.
   - `regulatory_tag`: EU regulatory exposure — AI Act / DORA / MiCA / NIS2 / GDPR — or 'low'.
   - `sovereignty_tag`: EU strategic-autonomy thesis — defense-dual-use, semiconductors, sovereign-cloud, biotech, energy, critical-materials — or 'none'.
   - `vintage_match`: closest historical analogue as a named pattern (e.g. 'Mistral-pattern: open-source AI + EU sovereignty').
   - `funding_signal`: if a funding round is mentioned, quote it verbatim; otherwise 'none'.

If a source returns "(scrape failed: ...)", do not abort — note the missing source
in `risks_or_limitations` and synthesize from the sources that succeeded.

Be practical, specific, and avoid hype and corporate fluff. Concrete, actionable analysis only.
If the combined sources look noisy or PR-driven, say so plainly in `risks_or_limitations`.
Bias your output toward European angles: when comparing a US round to a European analogue, name the analogue.
"""


@tool
def scrape_headlines(url: str) -> str:
    """Scrape visible headlines and article titles from a webpage.

    Input should be a full URL. Returns headlines prefixed with a source
    marker so the model can attribute claims back to the originating URL.
    On any network/HTTP failure it returns a `(scrape failed: ...)` marker
    instead of raising, so one dead source can't abort the whole run.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VCScoutBot/1.0)"}
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"=== SOURCE: {url} ===\n(scrape failed: {type(exc).__name__}: {exc})"

    soup = BeautifulSoup(response.text, "lxml")
    texts: list[str] = []
    for tag_name in ["h1", "h2", "h3", "title"]:
        for tag in soup.find_all(tag_name):
            text = tag.get_text(" ", strip=True)
            if text and len(text) > 20:
                texts.append(text)

    seen: set[str] = set()
    unique_texts: list[str] = []
    for t in texts:
        if t not in seen:
            seen.add(t)
            unique_texts.append(t)

    body = "\n".join(unique_texts[:25]) if unique_texts else "(no headlines extracted)"
    return f"=== SOURCE: {url} ===\n{body}"


def build_agent(model_id: str | None = None, max_tokens: int = 4000):
    """Compose the chat model, tools, structured output, and in-memory checkpointer.

    `model_id` priority: explicit argument > SCOUT_MAIN_MODEL env var > the
    Sonnet default. The urgent path passes Haiku for ~3x cheaper runs.
    """
    model_id = model_id or os.environ.get("SCOUT_MAIN_MODEL") or DEFAULT_MODEL_ID
    model = init_chat_model(
        model_id,
        temperature=0.3,
        max_tokens=max_tokens,
        timeout=90,
    )
    checkpointer = InMemorySaver()
    return create_agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[scrape_headlines],
        response_format=ToolStrategy(VCScoutOutput),
        checkpointer=checkpointer,
    )
