import os
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langchain.agents.structured_output import ToolStrategy
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()

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

Be practical, specific, and avoid hype.
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
# 7) RUN
# ---------------------------
def run_agent(url: str):
    config = {"configurable": {"thread_id": "vc-scout-1"}}

    # Reusing the same thread means that the convo always start from the same checkpoint (in the same thread)
    # Could/should be changed further down the line

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


if __name__ == "__main__":
    # Replace with a real startup / AI news page you want to test
    test_url = "https://techcrunch.com/category/startups/"
    run_agent(test_url)