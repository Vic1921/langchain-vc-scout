"""Weekly LP-grade rollup — a strategic synthesis from the knowledge base.

The daily digest is tactical (what happened today). This is the Monday-morning
view a partner opens a week with or forwards to LPs: theme heat-map, the week's
biggest conviction movers, new entrants, the hit-rate scorecard, and companies
the KB suggests adding to the watchlist.

Pure-data — no LLM call — so it is free, instant, and never fails on a flaky
model. The full doc is saved to outputs/; a Telegram/Slack-safe summary is
pushed to the alert sinks.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .alerts import load_watchlist, send_message
from .matching import normalize_name
from .storage import (
    count_runs,
    hit_rate,
    new_entrants,
    suggest_watchlist,
    theme_velocity,
    top_movers,
)

logger = logging.getLogger(__name__)


def _known_normalized() -> set[str]:
    """Normalized names + aliases of everything already on the watchlist."""
    known: set[str] = set()
    for entry in load_watchlist():
        for label in (entry.name, *entry.aliases):
            known.add(normalize_name(label))
    return known


def gather() -> dict:
    """Collect every figure the rollup needs in one pass."""
    return {
        "runs": count_runs(days=7),
        "themes": theme_velocity(window_days=7)[:8],
        "movers": top_movers(days=7),
        "entrants": new_entrants(days=7),
        "hit": hit_rate(),
        "suggestions": suggest_watchlist(_known_normalized()),
    }


def render_markdown(data: dict) -> str:
    """Full GitHub-flavoured Markdown rollup (the saved artifact)."""
    def arrow(recent: int, prior: int) -> str:
        return "UP" if recent > prior else ("DOWN" if recent < prior else "FLAT")

    themes = "\n".join(
        f"- [{arrow(t['recent_n'], t['prior_n'])}] **{t['theme']}** "
        f"— {t['recent_n']} this week vs {t['prior_n']} prior"
        for t in data["themes"]
    ) or "- (no themes recorded this week)"

    movers = "\n".join(
        f"- **{m['name']}** {m['first_score']} -> {m['last_score']} ({m['delta']:+})"
        for m in data["movers"]
    ) or "- (no movers this week)"

    entrants = "\n".join(
        f"- **{e['name']}** — best score {e['best_score']}"
        for e in data["entrants"][:10]
    ) or "- (no new entrants this week)"

    suggestions = "\n".join(
        f"- **{s['name']}** — avg {s['avg_score']} over {s['appearances']} runs "
        f"[{s['sovereignty_tag']}]"
        for s in data["suggestions"][:8]
    ) or "- (nothing new worth adding — watchlist looks current)"

    hit = data["hit"]
    return f"""# VC Scout — Weekly Rollup

**Week ending {datetime.now().strftime('%Y-%m-%d')}**  ·  {data['runs']} scout run(s)

## Theme heat-map (this week vs prior)
{themes}

## Biggest conviction movers
{movers}

## New entrants
{entrants}

## Hit-rate scorecard
Of companies first scored >= {hit['min_score']} at least {hit['settle_days']} days ago,
**{hit['hits']} of {hit['evaluated']}** later showed a funding signal — a hit rate of
**{hit['rate'] * 100:.0f}%**.

## Suggested watchlist additions
Companies the knowledge base keeps surfacing that you do not yet track:
{suggestions}
"""


def render_chat(data: dict) -> str:
    """A compact, sink-safe summary (single-asterisk bold works in Telegram + Slack)."""
    top_theme = data["themes"][0]["theme"] if data["themes"] else "—"
    mover = data["movers"][0] if data["movers"] else None
    mover_line = f"{mover['name']} ({mover['delta']:+})" if mover else "—"
    hit = data["hit"]
    suggestions = ", ".join(s["name"] for s in data["suggestions"][:3]) or "none"
    return (
        f"📊 *VC Scout — weekly rollup*  ({datetime.now().strftime('%Y-%m-%d')})\n\n"
        f"*Runs this week:* {data['runs']}\n"
        f"*Hottest theme:* {top_theme}\n"
        f"*Biggest mover:* {mover_line}\n"
        f"*Hit rate:* {hit['rate'] * 100:.0f}% ({hit['hits']}/{hit['evaluated']})\n"
        f"*Suggested watchlist adds:* {suggestions}\n\n"
        f"Full rollup saved to outputs/."
    )


def run_weekly_rollup(outputs_dir: Path | None = None) -> str:
    """Build the rollup, save it to outputs/, and push a summary to the alert sinks."""
    outputs_dir = outputs_dir or (Path(__file__).resolve().parent.parent / "outputs")
    outputs_dir.mkdir(exist_ok=True)
    data = gather()
    path = outputs_dir / f"weekly_rollup_{datetime.now().strftime('%Y-%m-%d')}.md"
    path.write_text(render_markdown(data), encoding="utf-8")
    logger.info("Weekly rollup saved: %s", path)
    send_message(render_chat(data))
    return str(path)
