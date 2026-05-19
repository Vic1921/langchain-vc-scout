import os
import requests
# import uuid
from bs4 import BeautifulSoup
from dataclasses import dataclass
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langchain.agents.structured_output import ToolStrategy
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()

SOURCES = [
    "https://techcrunch.com/category/startups/",
    "https://techcrunch.com/category/artificial-intelligence/",
]

# ---------------------------
# 1) SYSTEM PROMPT
# ---------------------------
SYSTEM_PROMPT = """
You are a sharp junior VC research assistant.

You will be given MULTIPLE sources to analyze in a single pass. You MUST:
1. Call scrape_headlines exactly once per source URL provided.
2. Synthesize ACROSS the sources rather than producing per-source summaries — the value is in cross-source signal.
3. Explicitly flag any signal that is only visible when sources are read together (e.g. a company appears in two feeds; a theme spikes across sources; one source contradicts another).
4. Identify recurring themes and rank investable areas.
5. Point out noise vs real signal, and call out source quality issues if any source looks thin or PR-driven.
6. Score every distinct company/startup observed on (market potential, team strength, product innovation, defensibility, investor interest). Tag each scored item with the source(s) it came from so claims are auditable.

Be practical, specific, and avoid hype and corporate fluff-like phrases — concrete, actionable advice only.
If the combined sources look noisy or low quality, say so plainly.
"""

# ---------------------------
# 2) STRUCTURED OUTPUT SCHEMA
# ---------------------------
@dataclass
class VCScoutOutput:
    headline_summary: str
    why_it_matters: str
    possible_investment_angle: str
    risks_or_limitations: str
    list_with_scoring_decisions: str

# ---------------------------
# 3) TOOL: SCRAPE HEADLINES
# ---------------------------
@tool
def scrape_headlines(url: str) -> str:
    """
    Scrape visible headlines and article titles from a webpage.
    Input should be a full URL.
    """
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    texts = []

    # Try common heading tags
    for tag_name in ["h1", "h2", "h3", "title"]:
        for tag in soup.find_all(tag_name):
            text = tag.get_text(" ", strip=True)
            if text and len(text) > 20:
                texts.append(text)

    # Deduplicate while preserving order
    seen = set()
    unique_texts = []
    for t in texts:
        if t not in seen:
            seen.add(t)
            unique_texts.append(t)

    # Keep first 20 useful lines, prefixed with the source URL so the
    # agent can attribute every headline back to its origin during synthesis.
    body = "\n".join(unique_texts[:20]) if unique_texts else "(no headlines extracted)"
    return f"=== SOURCE: {url} ===\n{body}"

# ---------------------------
# 4) MODEL SETUP
# ---------------------------
model = init_chat_model(
    "claude-sonnet-4-6",
    temperature=0.3,
    max_tokens=2500,
    timeout=60,
)

# ---------------------------
# 5) MEMORY / CHECKPOINTER
# ---------------------------
checkpointer = InMemorySaver()

# ---------------------------
# 6) CREATE AGENT
# ---------------------------
agent = create_agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[scrape_headlines],
    response_format=ToolStrategy(VCScoutOutput),
    checkpointer=checkpointer,
)

# ---------------------------
# 7) HELPER FUNCTION TO SAVE MARKDOWN REPORT
# ---------------------------
def save_markdown_report(urls: list[str], result) -> str:
    outputs_dir = Path(__file__).resolve().parent.parent / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = outputs_dir / f"vc_scout_report_{timestamp}.md"

    sources_block = "\n".join(f"- {u}" for u in urls)

    markdown_content = f"""# VC Scout Report

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Sources Analyzed
{sources_block}

## Headline Summary
{result.headline_summary}

## Why It Matters
{result.why_it_matters}

## Possible Investment Angle
{result.possible_investment_angle}

## Risks or Limitations
{result.risks_or_limitations}

## Scored Companies / Startups
{result.list_with_scoring_decisions}
"""

    filename.write_text(markdown_content, encoding="utf-8")
    return str(filename)

# ---------------------------
# 8) RUN
# ---------------------------
def run_agent(urls: list[str]):
    config = {"configurable": {"thread_id": datetime.now().strftime("%Y%m%d_%H%M%S")}}

    sources_block = "\n".join(f"- {u}" for u in urls)
    user_prompt = f"""
    Analyze the following sources for a software-focused VC. Call scrape_headlines
    once per URL, then produce ONE combined analysis synthesized across all sources
    (not a per-source list).

    Sources:
    {sources_block}

    Return:
    - headline_summary: cross-source synthesis of what's happening
    - why_it_matters: what only becomes visible when these sources are read together
    - possible_investment_angle: prioritized theses informed by all sources
    - risks_or_limitations: include data gaps and source bias
    - list_with_scoring_decisions: every distinct company/startup observed, one per line,
      each tagged with its source(s) and scored 1-10 across
      (market potential, team strength, product innovation, defensibility, investor interest).
    """

    response = agent.invoke(
        {"messages": [{"role": "user", "content": user_prompt}]},
        config=config,
    )

    result = response["structured_response"]

    print("\n=== VC SCOUT NOTE ===\n")
    print("Sources analyzed:")
    for u in urls:
        print(f"  - {u}")
    print("\nHeadline summary:")
    print(result.headline_summary)
    print("\nWhy it matters:")
    print(result.why_it_matters)
    print("\nPossible investment angle:")
    print(result.possible_investment_angle)
    print("\nRisks or limitations:")
    print(result.risks_or_limitations)
    print("\nList with scoring decisions:")
    print(result.list_with_scoring_decisions)

    saved_file = save_markdown_report(urls, result)
    print(f"\nMarkdown report saved to: {saved_file}")
    return result


if __name__ == "__main__":
    run_agent(SOURCES)
