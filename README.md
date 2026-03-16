# 🔍 LangChain Investment Scout

> An AI-powered research agent that scrapes startup and AI news, then produces structured, investment-grade analysis notes automatically.

---

## Overview

**VC Scout** is a LangChain + LangGraph agent that monitors curated news sources (currently TechCrunch Startups & AI) and distills raw headlines into actionable VC-style research notes.

Each run produces:
- A **headline summary** of what's happening in the market
- **Why it matters** for early-stage software investors
- A **possible investment angle** derived from the signal
- **Risks and limitations** to keep the analysis grounded
- A **scored list** of companies/startups ranked by investment potential

Reports are automatically saved as timestamped Markdown files in the `/outputs` directory.

---

## How It Works

```
TechCrunch URLs
      │
      ▼
scrape_headlines (LangChain tool)
      │  extracts h1/h2/h3/title tags
      ▼
Claude claude-sonnet-4-6 (via Anthropic)
      │  structured prompt → VC analyst persona
      ▼
VCScoutOutput (dataclass)
      │  headline_summary, why_it_matters,
      │  investment_angle, risks, scored_list
      ▼
Markdown report saved to /outputs/vc_scout_report_<timestamp>.md
```

The agent uses **LangGraph's `InMemorySaver`** as a checkpointer, giving each run its own isolated thread by timestamp.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent framework | [LangChain](https://python.langchain.com/) |
| Memory / state | [LangGraph](https://langchain-ai.github.io/langgraph/) `InMemorySaver` |
| LLM | Claude claude-sonnet-4-6 via Anthropic API |
| Web scraping | `requests` + `BeautifulSoup` (`lxml`) |
| Structured output | Python `dataclass` + `ToolStrategy` |
| Config | `python-dotenv` |

---

## Project Structure

```
langchain-vc-scout/
├── src/
│   └── main.py          # Agent definition, tools, and run logic
├── outputs/             # Auto-generated Markdown reports (git-ignored)
├── .env                 # Your API keys — never committed
├── .env.example         # Safe template to share
├── requirements.txt     # Python dependencies
└── README.md
```

---

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/Vic1921/langchain-vc-scout.git
cd langchain-vc-scout
```

### 2. Create and activate a virtual environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
```bash
cp .env.example .env
```
Then open `.env` and fill in your API key:
```
ANTHROPIC_API_KEY=your_key_here
```

### 5. Run
```bash
python src/main.py
```

Reports will be saved to `/outputs/vc_scout_report_<timestamp>.md`.

---

## Configuration

To change which sources are scraped, edit the `SOURCES` list at the top of `main.py`:

```python
SOURCES = [
    "https://techcrunch.com/category/startups/",
    "https://techcrunch.com/category/artificial-intelligence/",
]
```

Model behavior can be adjusted via:
- `temperature` — currently `0.3` (analytical, low creativity)
- `max_tokens` — currently `900`
- `SYSTEM_PROMPT` — the VC analyst persona and output instructions

---

## Sample Output

```
=== VC SCOUT NOTE ===

Headline summary:
TechCrunch's feed is dominated by three themes: (1) AI infrastructure & agents...

Why it matters:
Benchmark backing Gumloop at $50M signals strong conviction in no-code AI agent builders...

Possible investment angle:
1. AI Agent Infrastructure — gap in making agents reliable in enterprise settings...
2. Vertical AI for Financial Services — legacy fintech ripe for AI-native replacement...

Risks or limitations:
1. Valuation Inflation — entry price discipline is critical...
2. AI Wrapper Fatigue — undifferentiated LLM apps face rapid commoditization...
```

---

## Limitations & Known Issues

- Scraping is limited to visible heading tags (`h1`–`h3`, `title`) —> paywalled or JS-rendered content is not captured
- `max_tokens: 900` may truncate detailed scoring sections on busy news days
- Source quality is dependent on TechCrunch's feed, which skews toward funded, PR-driven announcements
- No persistent storage between runs — each execution is stateless

---

## Why I Built This

I wanted to understand how LangChain agent workflows behave in practice and how feasible it is to turn noisy web data into something resembling a real investment signal, without manual curation. Possible future developments would be to implement cron jobs so that the agent workflow is executed after every US business day, and the timestamped results in the output folder are automatically forwarded to a privately hosted instance of an OpenClaw agent/multi-agent setup.    

---

## License

MIT
