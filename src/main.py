"""VC Scout entry point.

Modes:
- (default)  : full run — scrape sources, synthesize, save MD + PDF, persist
               to SQLite, emit daily digest, fire urgent alerts and theme spikes.
- --urgent   : run cheaply for intra-day cron — same scrape + synthesis, but
               only emits the URGENT tier (watchlist hits + funding signals).
               Skips daily digest and PDF generation.
- --timeline NAME      : print every recorded mention of a company across runs.
- --theme-velocity     : print themes ranked by recent-vs-prior mention count.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from dotenv import load_dotenv

from .agent import build_agent
from .alerts import (
    find_theme_spikes,
    find_urgent,
    load_watchlist,
    send_daily_digest,
    send_theme_spike,
    send_urgent_alert,
)
from .reports import generate_onepagers, save_markdown_report
from .sources import DEFAULT_SOURCES
from .storage import company_timeline, record_run, theme_velocity


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scout")


def _user_prompt(urls: list[str]) -> str:
    sources_block = "\n".join(f"    - {u}" for u in urls)
    return f"""
Analyze the following sources for a European software-focused VC. Call
scrape_headlines once per URL, then produce ONE combined analysis synthesized
across all sources (not a per-source list).

Sources:
{sources_block}

Populate every field of the VCScoutOutput schema. In particular:
- `visible_only_when_combined`: the mosaic-mode finding — what only emerges
  from synthesis.
- `contrarian_view`: a Howard-Marks pass on the consensus.
- For each company: regulatory_tag, sovereignty_tag, vintage_match, and
  funding_signal MUST be populated. Cite sources inline in the rationale
  using `[source: <url>]`.
"""


def run(urls: list[str], urgent_only: bool = False) -> int:
    agent = build_agent()
    thread_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    config = {"configurable": {"thread_id": thread_id}}

    response = agent.invoke(
        {"messages": [{"role": "user", "content": _user_prompt(urls)}]},
        config=config,
    )
    output = response["structured_response"]

    if urgent_only:
        # Cheap path: only fire urgent alerts. Skip MD, PDF, daily digest.
        # We still record the run so the KB stays warm for theme velocity.
        run_id = record_run(output, urls, run_at=datetime.utcnow().isoformat(timespec="seconds"))
        urgent = find_urgent(output, load_watchlist())
        log.info("Urgent matches: %d", len(urgent))
        for company, watch in urgent:
            send_urgent_alert(company, watch)
        return run_id

    report_path = save_markdown_report(urls, output)
    log.info("Markdown report saved: %s", report_path)

    pdfs = generate_onepagers(output.companies)
    if pdfs:
        log.info("PDF one-pagers generated: %d", len(pdfs))

    run_id = record_run(
        output,
        urls,
        run_at=datetime.utcnow().isoformat(timespec="seconds"),
        report_path=report_path,
    )

    # Tiered alerts — all three tiers fire in a full run.
    watchlist = load_watchlist()
    urgent = find_urgent(output, watchlist)
    for company, watch in urgent:
        send_urgent_alert(company, watch)
    for spike in find_theme_spikes(output):
        send_theme_spike(spike)
    send_daily_digest(output, report_path)

    _print_console_summary(output, report_path, urgent_count=len(urgent))
    return run_id


def _print_console_summary(output, report_path: str, urgent_count: int) -> None:
    print("\n=== VC SCOUT NOTE ===\n")
    print(f"Report: {report_path}")
    print(f"Urgent watchlist hits: {urgent_count}")
    print(f"\nHeadline summary:\n{output.headline_summary}")
    print(f"\nVisible only when combined:\n{output.visible_only_when_combined}")
    print(f"\nContrarian view:\n{output.contrarian_view}")
    print(f"\nThemes: {', '.join(output.themes) if output.themes else '—'}")
    if output.companies:
        top = sorted(output.companies, key=lambda c: c.score_total, reverse=True)[:5]
        print("\nTop scored:")
        for c in top:
            print(f"  - {c.name}: {c.score_total}/10  [{c.regulatory_tag}; {c.sovereignty_tag}]")


def _cli_timeline(name: str) -> None:
    rows = company_timeline(name)
    if not rows:
        print(f"No recorded mentions for {name!r}.")
        return
    print(f"\nTimeline for {name} ({len(rows)} mentions):\n")
    for row in rows:
        print(
            f"  {row['run_at']}  ·  score {row['score_total']}  ·  "
            f"reg={row['regulatory_tag']}  ·  sov={row['sovereignty_tag']}  ·  "
            f"funding={row['funding_signal']}"
        )


def _cli_theme_velocity() -> None:
    rows = theme_velocity()
    if not rows:
        print("No themes recorded yet — run the scout first.")
        return
    print("\nTheme velocity (recent vs prior 7-day window):\n")
    for row in rows:
        arrow = "↑" if row["recent_n"] > row["prior_n"] else ("=" if row["recent_n"] == row["prior_n"] else "↓")
        print(f"  {arrow}  {row['theme']:<40}  recent={row['recent_n']:>3}   prior={row['prior_n']:>3}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VC Scout — European-VC research agent")
    p.add_argument("--urgent", action="store_true", help="Run the cheap intra-day pass: urgent alerts only.")
    p.add_argument("--timeline", metavar="COMPANY", help="Print recorded timeline for a company across runs.")
    p.add_argument("--theme-velocity", action="store_true", help="Print recent-vs-prior theme mention counts.")
    p.add_argument("--sources", nargs="*", help="Override the default source URL list.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.timeline:
        _cli_timeline(args.timeline)
        return
    if args.theme_velocity:
        _cli_theme_velocity()
        return
    urls = args.sources or DEFAULT_SOURCES
    run(urls, urgent_only=args.urgent)


if __name__ == "__main__":
    main()
