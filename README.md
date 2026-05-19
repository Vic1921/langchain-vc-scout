# 🔍 LangChain Investment Scout

> An AI-powered research agent for European software-focused VC funds. Scrapes US + EU startup news, synthesizes across sources, and produces structured investment notes — with regulatory tagging, a persistent knowledge base, and tiered Telegram alerts so a deal-velocity signal never sits in your inbox overnight.

---

## Overview

**VC Scout** is a LangChain + LangGraph agent that monitors a curated mix of US and European news sources (TechCrunch, EU-Startups, Maddyness, Tech.eu, Sifted) and turns raw headlines into investment-grade notes aimed at European software-VC partners.

Each run produces:
- A **headline summary** synthesized across all sources (not per-source)
- A **"Visible only when combined"** section — the mosaic signal you can't get from any single feed
- **Why it matters** for an early-stage software investor with European focus
- A **possible investment angle** derived from the synthesis
- A **contrarian view** — Howard-Marks-style pass on the consensus
- **Risks and limitations** to keep the analysis grounded
- A **scored list** of companies with **EU regulatory tags** (AI Act / DORA / MiCA / NIS2 / GDPR), **EU sovereignty thesis** fit (defense, semis, sovereign-cloud, biotech, energy), a **vintage match** to a historical analogue, and a **funding signal** quoted verbatim when present

Outputs:
- Timestamped Markdown report in `outputs/`
- One-page PDF for every company scoring ≥ 7.5/10 (in `outputs/onepagers/`)
- SQLite knowledge base in `data/vc_scout.db` — company timelines and theme velocity accumulate across runs
- Tiered Telegram alerts: daily digest, theme-spike, and an **urgent** ping when a watchlist company appears with a funding signal

---

## How It Works

```
US + EU source URLs ──┐
                      ▼
            scrape_headlines (per URL, tagged with origin)
                      │
                      ▼
       Claude Sonnet 4.6 (single combined pass, ToolStrategy)
                      │  cross-source synthesis + per-company
                      │  regulatory / sovereignty / vintage tagging
                      ▼
          VCScoutOutput (pydantic, fully structured)
                      │
       ┌──────────────┼──────────────┬──────────────┐
       ▼              ▼              ▼              ▼
   Markdown      One-page PDFs    SQLite KB    Telegram tiers
  (outputs/)    (top scorers)   (data/*.db)   ┌──── daily digest
                                              ├──── theme spike
                                              └──── URGENT (watchlist ∩ funding signal)
```

The agent uses **LangGraph's `InMemorySaver`** as a checkpointer (per-run isolation by timestamp). Long-term memory lives in the SQLite knowledge base — `company_timeline()` and `theme_velocity()` queries power the timeline view and the spike alert.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent framework | [LangChain](https://python.langchain.com/) |
| Per-run memory | [LangGraph](https://langchain-ai.github.io/langgraph/) `InMemorySaver` |
| Cross-run memory (KB) | SQLite (`data/vc_scout.db`) — companies, themes, full run history |
| LLM | Claude Sonnet 4.6 via Anthropic API |
| Web scraping | `requests` + `BeautifulSoup` (`lxml`) |
| Structured output | `pydantic` v2 + `ToolStrategy` (nested `ScoredCompany` list) |
| PDF one-pagers | `reportlab` (A4, score table, sources, rationale) |
| Alerts | Telegram Bot API (graceful no-op if creds absent) |
| Scheduling | GitHub Actions cron (daily digest + hourly urgent) |
| Config | `python-dotenv` + optional `watchlist.csv` |

---

## Project Structure

```
langchain-vc-scout/
├── src/
│   ├── main.py          # CLI entry point (full run, --urgent, --timeline, --theme-velocity)
│   ├── agent.py         # Scraper tool + system prompt + model + agent wiring
│   ├── schema.py        # Pydantic models (VCScoutOutput, ScoredCompany)
│   ├── sources.py       # Default source URL list (US + EU mix)
│   ├── storage.py       # SQLite KB — record_run, company_timeline, theme_velocity
│   ├── alerts.py        # Watchlist + Telegram tiered alerts
│   └── reports.py       # Markdown + reportlab PDF one-pager
├── .github/workflows/
│   ├── scout-daily.yml  # Mon–Fri 06:00 UTC — full run + digest, KB cached
│   └── scout-urgent.yml # Mon–Fri hourly — urgent-only intra-day pass
├── outputs/             # Auto-generated reports + PDF one-pagers (git-ignored)
├── data/                # SQLite KB (git-ignored locally; cached on CI)
├── watchlist.csv        # Your private watchlist (git-ignored)
├── watchlist.example.csv # Public template
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
# Full run: scrape + synthesize + save MD + PDF + record in KB + alerts
python -m src.main

# Cheap intra-day pass: urgent alerts only (no MD, no PDF, no digest)
python -m src.main --urgent

# Inspect a company's full timeline across every recorded run
python -m src.main --timeline "Mistral AI"

# Print theme velocity: recent-vs-prior 7-day mention counts
python -m src.main --theme-velocity

# Override the source list ad hoc
python -m src.main --sources https://sifted.eu https://techcrunch.com/category/artificial-intelligence/
```

Reports land in `outputs/vc_scout_report_<timestamp>.md`. PDF one-pagers for top-scoring companies land in `outputs/onepagers/`. The SQLite KB at `data/vc_scout.db` accumulates across runs.

### 6. (Optional) Enable Telegram alerts

1. Talk to [@BotFather](https://t.me/BotFather), create a bot, copy the token.
2. Send any message to your bot, then call `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat.id`.
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

Without these env vars the alert layer is a logged no-op — fine for local development.

### 7. (Optional) Build a watchlist

Copy `watchlist.example.csv` to `watchlist.csv` and replace the entries with companies your fund is actively tracking. When any of them surfaces in the scout's output with a non-empty `funding_signal`, you get an immediate Telegram ping (no waiting for the daily digest).

### 8. (Optional) Run it 24/7 on GitHub Actions

Push the repo to GitHub and add three secrets: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. The included workflows take over:

- `scout-daily.yml` — Mon–Fri 06:00 UTC, full pipeline, publishes reports + the SQLite KB to a `reports` branch.
- `scout-urgent.yml` — Mon–Fri hourly during European business hours, urgent-only.

---

## Configuration

- **Sources** — edit `DEFAULT_SOURCES` in [src/sources.py](src/sources.py), or pass `--sources URL [URL …]` on the CLI.
- **Watchlist** — `watchlist.csv` at the repo root. Columns: `name,thesis_tag,note`. Template at [watchlist.example.csv](watchlist.example.csv).
- **Model behavior** — `build_agent()` in [src/agent.py](src/agent.py): `temperature=0.3` (analytical), `max_tokens=4000` (room for nested scoring), `timeout=90`.
- **System prompt** — `SYSTEM_PROMPT` in [src/agent.py](src/agent.py). Tuned for European-VC framing; edit if you want a different persona.
- **Alert thresholds** — `find_theme_spikes(spike_ratio=2.0, min_recent=3)` and `generate_onepagers(min_score=7.5)` are the two knobs worth tuning by fund stage.

---

## Sample Output

```
=== VC SCOUT NOTE ===

Report: outputs/vc_scout_report_2026-05-19_07-03-22.md
Urgent watchlist hits: 1

Headline summary:
Across TechCrunch, EU-Startups, Sifted and Tech.eu, three themes dominate:
(1) defense / dual-use AI accelerating in Munich and London,
(2) sovereign cloud signalling around Mistral and Black Forest Labs,
(3) the AI-Act compliance race for enterprise foundation models.

Visible only when combined:
Helsing appears in both Sifted and Tech.eu within 48 hours; Sifted reports
NATO-aligned contract framing while Tech.eu emphasises the German federal
procurement angle — together they suggest a coordinated public-private push,
not a one-off raise.

Contrarian view:
Coverage is unanimously bullish on "EU sovereignty" but quiet on whether
European LPs will actually fund the late stages. If the C/D rounds rely on
US growth capital, sovereignty framing is a marketing layer, not a moat.

Themes: defense-dual-use, sovereign cloud, AI Act compliance, vertical AI for FS

Top scored:
  - Helsing: 8.6/10  [AI Act high-risk; defense-dual-use]
  - Black Forest Labs: 7.8/10  [AI Act limited-risk; sovereign-cloud]
  - Pigment: 7.4/10  [low; none]
```

---

## Limitations & Known Issues

- Scraping is limited to visible heading tags (`h1`–`h3`, `title`) — paywalled or JS-rendered content is not captured. Sifted in particular shows only the homepage tease.
- Source quality varies by feed: TechCrunch skews toward US, PR-driven announcements; EU-Startups is broad-net but light on rounds; Sifted is the highest signal but the most paywalled.
- Regulatory tagging (AI Act / DORA / MiCA / NIS2) is LLM-inferred from headline text, not legal opinion. Use it to flag *what's worth a lawyer's hour*, not as a compliance verdict.
- The SQLite KB lives locally by default. On CI it's cached between runs and mirrored to a `reports` branch — see the workflow files for the exact mechanism.
- Telegram is the only alert sink wired in. Slack / email would slot in next to it in [src/alerts.py](src/alerts.py).

---

## Why I Built This

I wanted to understand how LangChain agent workflows behave in practice — and how feasible it is to turn noisy web data into something resembling a real investment signal, without manual curation. The original version was a single-source TechCrunch summarizer with no memory. The current version is opinionated toward European VC reality: cross-source mosaic synthesis, AI Act / DORA / MiCA / NIS2 tagging, a persistent knowledge base, a contrarian pass, vintage-match pattern recognition, and a tiered alert system that won't let a watchlist company close a round overnight while you're not looking.

Next up the roadmap:
- A "Sifted vs TechCrunch divergence index" — surfacing companies where EU and US coverage diverges (mispriced narrative = alpha).
- A founder–portfolio adjacency check (drop in a CSV of your portfolio, get warm-intro paths surfaced automatically).
- A cross-border arbitrage view comparing each US round to its closest European analogue.

---

## License

MIT
