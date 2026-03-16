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

Your job is to:
1. Read startup / AI headlines from the provided source.
2. Identify the most relevant items for an early-stage software investor.
3. Summarize the key developments clearly.
4. Produce concise, structured investment-style output.
5. Identify recurring themes
6. Rank investable areas
7. Point out noise vs real signal
8. Implement a scoring mechanism for each company/startup based on factors like market potential, team strength, product innovation, defensibility, and overall investor interest. 
Provide a final score for each company/startup to help prioritize investment opportunities.



Be practical, specific, and avoid hype adn corporate fluff-like phrases, I need concrete, actionable advice.
If the source looks noisy or low quality, say so.
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

    # Keep first 20 useful lines
    return "\n".join(unique_texts[:20])

# ---------------------------
# 4) MODEL SETUP
# ---------------------------
model = init_chat_model(
    "claude-sonnet-4-6",
    temperature=0.3,
    max_tokens=900,
    timeout=30,
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
def save_markdown_report(url: str, result) -> str:
    outputs_dir = Path(__file__).resolve().parent.parent / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = outputs_dir / f"vc_scout_report_{timestamp}.md"

    markdown_content = f"""# VC Scout Report

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Source URL:** {url}

## Headline Summary
{result.headline_summary}

## Why It Matters
{result.why_it_matters}

## Possible Investment Angle
{result.possible_investment_angle}

## Risks or Limitations
{result.risks_or_limitations}
"""

    filename.write_text(markdown_content, encoding="utf-8")
    return str(filename)

# ---------------------------
# 8) RUN
# ---------------------------
def run_agent(url: str):
    config = {"configurable": {"thread_id": datetime.now().strftime("%Y%m%d_%H%M%S")}}

    user_prompt = f"""
    Please analyze this source for a software-focused VC:
    {url}

    Use the scrape_headlines tool first.
    Then return:
    - a concise headline summary
    - why it matters
    - a possible investment angle
    - risks or limitations
    """

    response = agent.invoke(
        {"messages": [{"role": "user", "content": user_prompt}]},
        config=config,
    )

    result = response["structured_response"]

    print("\n=== VC SCOUT NOTE ===\n")
    print("Headline summary:")
    print(result.headline_summary)
    print("\nWhy it matters:")
    print(result.why_it_matters)
    print("\nPossible investment angle:")
    print(result.possible_investment_angle)
    print("\nRisks or limitations:")
    print(result.risks_or_limitations)
    print("\nList with scoring decisions:")
    print(result.list_with_scoring_decisions)

    saved_file = save_markdown_report(url, result)
    print(f"\nMarkdown report saved to: {saved_file}")



if __name__ == "__main__":
    for url in SOURCES:
        run_agent(url)
