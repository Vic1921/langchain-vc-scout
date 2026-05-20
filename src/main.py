"""VC Scout entry point.

Modes:
- (default)        : full run — scrape, synthesize, self-grade (regenerate once
                     if below the quality bar), save MD + PDF, persist to SQLite,
                     record cost, emit daily digest + theme spikes, fire deduped
                     urgent alerts.
- --urgent         : cheap intra-day pass — scrape + synthesize, record the run +
                     cost, fire only deduped URGENT alerts. No grading / MD / PDF.
- --timeline NAME  : print a company's mention timeline across runs.
- --theme-velocity : print themes ranked by recent-vs-prior mention count.
- --cost           : print the month-to-date cost ledger.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

# Windows consoles default to cp1252; force UTF-8 so ↑/↓ badges and the €
# sign in console output don't raise UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .agent import build_agent
from .alerts import (
    find_theme_spikes,
    find_urgent,
    load_watchlist,
    send_daily_digest,
    send_theme_spike,
    send_urgent_alert,
    theme_spike_dedup_key,
    urgent_dedup_key,
)
from .costs import CostRecord, extract_cost
from .grading import QUALITY_BAR, grade_report, regeneration_prompt
from .reports import generate_onepagers, save_markdown_report
from .sources import DEFAULT_SOURCES
from .storage import (
    company_timeline,
    compute_conviction_deltas,
    mark_alert_sent,
    monthly_cost,
    record_cost,
    record_run,
    theme_velocity,
    was_alert_sent,
)


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scout")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


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


def _record_costs(run_id: int, costs: list[CostRecord]) -> None:
    for cr in costs:
        record_cost(run_id, cr.model, cr.input_tokens, cr.output_tokens, cr.cost_usd)


def _fire_urgent(output, deltas, label: str) -> int:
    """Send deduped urgent alerts. Returns the count of newly-fired alerts."""
    fired = 0
    for company, watch in find_urgent(output, load_watchlist()):
        key = urgent_dedup_key(company)
        if was_alert_sent(key):
            log.info("[%s] urgent alert for %s already sent — skipping", label, company.name)
            continue
        send_urgent_alert(company, watch, deltas.get(company.name.lower()))
        mark_alert_sent("urgent", key, company.name)
        fired += 1
    return fired


def run(urls: list[str], urgent_only: bool = False) -> int:
    agent = build_agent()
    config = {"configurable": {"thread_id": datetime.now().strftime("%Y%m%d_%H%M%S")}}
    costs: list[CostRecord] = []

    response = agent.invoke(
        {"messages": [{"role": "user", "content": _user_prompt(urls)}]},
        config=config,
    )
    costs.append(extract_cost(response["messages"]))
    output = response["structured_response"]

    if urgent_only:
        # Cheap path: record the run + cost, fire only deduped urgent alerts.
        deltas = compute_conviction_deltas(output.companies)
        run_id = record_run(output, urls, run_at=_utc_iso())
        _record_costs(run_id, costs)
        fired = _fire_urgent(output, deltas, label="urgent")
        log.info("Urgent pass complete — %d new alert(s) fired", fired)
        return run_id

    # Self-grading pass: a cheap Haiku auditor; regenerate once if below the bar.
    grade, grade_cost = grade_report(output)
    costs.append(grade_cost)
    if grade.score < QUALITY_BAR:
        log.info(
            "Report scored %d/%d — regenerating once with auditor feedback",
            grade.score, QUALITY_BAR,
        )
        response = agent.invoke(
            {"messages": [{"role": "user", "content": regeneration_prompt(grade)}]},
            config=config,
        )
        costs.append(extract_cost(response["messages"]))
        output = response["structured_response"]

    # Conviction deltas MUST be computed before record_run inserts this run.
    deltas = compute_conviction_deltas(output.companies)

    report_path = save_markdown_report(urls, output, deltas=deltas, grade=grade)
    log.info("Markdown report saved: %s", report_path)

    pdfs = generate_onepagers(output.companies)
    if pdfs:
        log.info("PDF one-pagers generated: %d", len(pdfs))

    run_id = record_run(output, urls, run_at=_utc_iso(), report_path=report_path)
    _record_costs(run_id, costs)

    fired = _fire_urgent(output, deltas, label="daily")
    for spike in find_theme_spikes(output):
        key = theme_spike_dedup_key(spike["theme"])
        if was_alert_sent(key, within_days=7):
            continue
        if send_theme_spike(spike):
            mark_alert_sent("theme_spike", key, spike["theme"])

    monthly = monthly_cost()
    send_daily_digest(output, report_path, deltas=deltas, monthly=monthly)

    _print_console_summary(output, report_path, deltas, grade, monthly, fired)
    return run_id


def _print_console_summary(output, report_path, deltas, grade, monthly, urgent_fired) -> None:
    print("\n=== VC SCOUT NOTE ===\n")
    print(f"Report: {report_path}")
    issues = f"  ({len(grade.issues)} issue(s) flagged)" if grade.issues else ""
    print(f"Quality self-check: {grade.score}/10{issues}")
    print(f"Urgent alerts fired: {urgent_fired}")
    print(f"Spend ({monthly['year_month']}): ${monthly['cost']:.2f} across {monthly['runs']} run(s)")
    print(f"\nHeadline summary:\n{output.headline_summary}")
    print(f"\nVisible only when combined:\n{output.visible_only_when_combined}")
    print(f"\nContrarian view:\n{output.contrarian_view}")
    print(f"\nThemes: {', '.join(output.themes) if output.themes else '—'}")
    if output.companies:
        top = sorted(output.companies, key=lambda c: c.score_total, reverse=True)[:5]
        print("\nTop scored:")
        for c in top:
            d = deltas.get(c.name.lower())
            badge = f"  ({d.badge()})" if d else ""
            print(f"  - {c.name}: {c.score_total}/10{badge}  [{c.regulatory_tag}; {c.sovereignty_tag}]")


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


def _cli_cost() -> None:
    m = monthly_cost()
    print(f"\nCost ledger — {m['year_month']}:\n")
    print(f"  Runs:          {m['runs']}")
    print(f"  Input tokens:  {m['input_tokens']:,}")
    print(f"  Output tokens: {m['output_tokens']:,}")
    print(f"  Spend:         ${m['cost']:.2f}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VC Scout — European-VC research agent")
    p.add_argument("--urgent", action="store_true", help="Cheap intra-day pass: deduped urgent alerts only.")
    p.add_argument("--timeline", metavar="COMPANY", help="Print a company's timeline across runs.")
    p.add_argument("--theme-velocity", action="store_true", help="Print recent-vs-prior theme mention counts.")
    p.add_argument("--cost", action="store_true", help="Print the month-to-date cost ledger.")
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
    if args.cost:
        _cli_cost()
        return
    urls = args.sources or DEFAULT_SOURCES
    run(urls, urgent_only=args.urgent)


if __name__ == "__main__":
    main()
