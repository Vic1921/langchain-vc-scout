"""End-to-end smoke test for the storage, alert, and cost layers.

Pushes synthetic data through every query the agent / alert / digest layers
rely on. No network, no LLM, no API keys — safe to run anywhere.
Run from the repo root: python scripts/smoke_storage.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make `src` importable when running this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252; force UTF-8 so ↑/↓ badges print cleanly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.alerts import (
    WatchlistEntry,
    find_urgent,
    has_funding_signal,
    load_watchlist,
    theme_spike_dedup_key,
    urgent_dedup_key,
)
from src.costs import compute_cost, extract_cost
from src.schema import ScoredCompany, VCScoutOutput
from src.storage import (
    company_timeline,
    compute_conviction_deltas,
    mark_alert_sent,
    monthly_cost,
    recent_high_scorers,
    record_cost,
    record_run,
    theme_velocity,
    was_alert_sent,
)


def _company(name: str, scores: tuple[int, int, int, int, int], funding: str = "none") -> ScoredCompany:
    m, t, p, d, i = scores
    return ScoredCompany(
        name=name,
        sources=["https://sifted.eu/"],
        score_market=m, score_team=t, score_product=p,
        score_defensibility=d, score_interest=i,
        rationale=f"{name} synthetic rationale [source: https://sifted.eu/].",
        regulatory_tag="AI Act high-risk",
        sovereignty_tag="defense-dual-use",
        vintage_match="Anduril-pattern: defense + AI",
        funding_signal=funding,
    )


def _make_output(funding: str = "raised €40M Series A led by Index Ventures") -> VCScoutOutput:
    return VCScoutOutput(
        headline_summary="EU defense AI accelerates; vertical SaaS holding pattern.",
        why_it_matters="Helsing's velocity matters for the sovereignty thesis.",
        visible_only_when_combined="Helsing in 2 EU feeds within 48h suggests a coordinated push.",
        possible_investment_angle="Track defense-AI ecosystem; index sovereign-cloud co-investors.",
        contrarian_view="Consensus on EU sovereignty is bullish; LP appetite at late stages is the real question.",
        risks_or_limitations="EU sources are noisy on details; funding ranges unverified.",
        themes=["defense-dual-use", "sovereign cloud", "AI Act compliance"],
        companies=[
            _company("Helsing", (9, 9, 8, 8, 9), funding=funding),  # score_total 8.6
            _company("Pigment", (8, 7, 8, 6, 7), funding="none"),   # score_total 7.2
        ],
    )


class _FakeMsg:
    """Stands in for a LangChain AIMessage so extract_cost can be tested LLM-free."""

    def __init__(self, inp: int, out: int, model: str):
        self.usage_metadata = {"input_tokens": inp, "output_tokens": out}
        self.response_metadata = {"model_name": model}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "smoke.db"

        rid1 = record_run(
            _make_output(funding="none"),
            urls=["https://sifted.eu/", "https://tech.eu/"],
            run_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"),
            db_path=db,
        )
        rid2 = record_run(
            _make_output(),
            urls=["https://sifted.eu/", "https://tech.eu/", "https://techcrunch.com/"],
            run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            report_path="/fake/report.md",
            db_path=db,
        )
        assert rid1 != rid2, "run ids should differ"
        print(f"OK  recorded two runs (ids={rid1}, {rid2})")

        timeline = company_timeline("helsing", db_path=db)
        assert len(timeline) == 2, f"expected 2 timeline rows, got {len(timeline)}"
        print(f"OK  company_timeline('helsing'): {len(timeline)} mentions")

        velocities = theme_velocity(db_path=db)
        assert any(v["theme"].lower() == "sovereign cloud" for v in velocities), "sovereign cloud theme missing"
        print(f"OK  theme_velocity: {len(velocities)} themes ranked")

        top = recent_high_scorers(db_path=db)
        assert any(t["name"].lower() == "helsing" for t in top), "Helsing should be a top scorer"
        print(f"OK  recent_high_scorers: {len(top)} companies at >= 7.5/10")

        # --- Conviction delta -------------------------------------------------
        hotter = _company("Helsing", (10, 10, 9, 9, 10))  # score_total 9.6 vs prior 8.6
        deltas = compute_conviction_deltas([hotter], db_path=db)
        cd = deltas["helsing"]
        assert not cd.is_new, "Helsing is already in the KB"
        assert cd.prev_score == 8.6, f"expected prev 8.6, got {cd.prev_score}"
        assert cd.delta == 1.0, f"expected delta +1.0, got {cd.delta}"
        print(f"OK  conviction delta (existing): {cd.render()}")

        fresh = compute_conviction_deltas([_company("NewCo", (5, 5, 5, 5, 5))], db_path=db)["newco"]
        assert fresh.is_new and fresh.badge() == "NEW", "NewCo should be flagged new"
        print(f"OK  conviction delta (new): {fresh.render()}")

        # --- Alert dedup ledger ----------------------------------------------
        key = urgent_dedup_key(hotter)
        assert not was_alert_sent(key, db_path=db), "key should be unseen initially"
        mark_alert_sent("urgent", key, "Helsing", db_path=db)
        assert was_alert_sent(key, db_path=db), "key should be seen after marking"
        assert not was_alert_sent("urgent:unrelated:000", db_path=db), "unrelated key should be unseen"
        print("OK  alert dedup: mark + was_sent + isolation")

        k_a = urgent_dedup_key(_make_output(funding="raised €40M Series A").companies[0])
        k_b = urgent_dedup_key(_make_output(funding="raised €80M Series B").companies[0])
        assert k_a != k_b, "different funding signal must yield a different dedup key"
        assert theme_spike_dedup_key("Sovereign Cloud") == "theme_spike:sovereign cloud"
        print("OK  dedup keys: signal-sensitive + theme key normalized")

        # --- Cost ledger ------------------------------------------------------
        assert compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0, "sonnet 1M+1M should be $18"
        assert compute_cost("claude-haiku-4-5-20251001", 1_000_000, 0) == 1.0, "haiku 1M in should be $1"
        cr = extract_cost([_FakeMsg(1000, 200, "claude-sonnet-4-6"), _FakeMsg(500, 100, "claude-sonnet-4-6")])
        assert (cr.input_tokens, cr.output_tokens) == (1500, 300), f"unexpected token sum: {cr}"
        print(f"OK  compute_cost + extract_cost: summed call = ${cr.cost_usd}")

        record_cost(rid2, "claude-sonnet-4-6", 50_000, 8_000, compute_cost("claude-sonnet-4-6", 50_000, 8_000), db_path=db)
        record_cost(rid2, "claude-haiku-4-5", 12_000, 600, compute_cost("claude-haiku-4-5", 12_000, 600), db_path=db)
        m = monthly_cost(db_path=db)
        assert m["runs"] == 1, f"expected 1 run with cost, got {m['runs']}"
        assert m["input_tokens"] == 62_000, f"expected 62000 input tokens, got {m['input_tokens']}"
        assert m["cost"] > 0, "monthly cost should be positive"
        print(f"OK  cost ledger: {m['year_month']} = ${m['cost']:.4f}, {m['input_tokens']:,} input tokens")

        # --- Alert predicate logic -------------------------------------------
        watch = [WatchlistEntry(name="Helsing", thesis_tag="defense-dual-use", note="watch")]
        urgent = find_urgent(_make_output(), watch)
        assert len(urgent) == 1, f"expected 1 urgent hit, got {len(urgent)}"
        assert has_funding_signal(_make_output().companies[0]), "Helsing should have a funding signal"
        assert not has_funding_signal(_make_output(funding="none").companies[0]), "should be silent on 'none'"
        print(f"OK  find_urgent + has_funding_signal: {len(urgent)} hit, predicates correct")

        # --- Watchlist from the WATCHLIST_CSV env var (CI / container path) ---
        os.environ["WATCHLIST_CSV"] = "name,thesis_tag,note\nHelsing,defense-dual-use,from env\n"
        try:
            wl_env = load_watchlist()
            assert len(wl_env) == 1 and wl_env[0].name == "Helsing", f"env watchlist parse failed: {wl_env}"
        finally:
            del os.environ["WATCHLIST_CSV"]
        assert load_watchlist(path=db.parent / "nonexistent.csv") == [], "absent watchlist should be empty"
        print("OK  load_watchlist: WATCHLIST_CSV env path + graceful absence")

        print("\nAll smoke checks passed.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
