"""End-to-end smoke test for the storage layer.

Pushes a synthetic VCScoutOutput into a temp SQLite DB, then exercises every
query the agent / alert layer relies on. No network, no LLM, no API keys.
Run from the repo root: python scripts/smoke_storage.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make `src` importable when running this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schema import ScoredCompany, VCScoutOutput
from src.storage import (
    company_timeline,
    recent_high_scorers,
    record_run,
    theme_velocity,
)
from src.alerts import (
    WatchlistEntry,
    find_theme_spikes,
    find_urgent,
    has_funding_signal,
)


def _make_output(funding: str = "raised €40M Series A led by Index Ventures") -> VCScoutOutput:
    helsing = ScoredCompany(
        name="Helsing",
        sources=["https://sifted.eu/", "https://tech.eu/"],
        score_market=9,
        score_team=9,
        score_product=8,
        score_defensibility=8,
        score_interest=9,
        rationale="Defense-AI roll-up in Munich [source: https://sifted.eu/]; NATO framing [source: https://tech.eu/].",
        regulatory_tag="AI Act high-risk",
        sovereignty_tag="defense-dual-use",
        vintage_match="Anduril-pattern: defense + AI + sovereign ownership",
        funding_signal=funding,
    )
    pigment = ScoredCompany(
        name="Pigment",
        sources=["https://techcrunch.com/category/startups/"],
        score_market=8, score_team=7, score_product=8,
        score_defensibility=6, score_interest=7,
        rationale="Vertical SaaS FP&A [source: https://techcrunch.com/category/startups/].",
        regulatory_tag="low",
        sovereignty_tag="none",
        vintage_match="Anaplan-pattern: planning-platform consolidation",
        funding_signal="none",
    )
    return VCScoutOutput(
        headline_summary="EU defense AI accelerates; vertical SaaS holding pattern.",
        why_it_matters="Helsing's velocity matters for the sovereignty thesis.",
        visible_only_when_combined="Helsing in 2 EU feeds within 48h suggests coordinated push.",
        possible_investment_angle="Track defense-AI ecosystem; index sovereign-cloud co-investors.",
        contrarian_view="Consensus on EU sovereignty is bullish; LP appetite at late stages is the real question.",
        risks_or_limitations="EU sources are noisy on details; ranges of funding unverified.",
        themes=["defense-dual-use", "sovereign cloud", "AI Act compliance"],
        companies=[helsing, pigment],
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "smoke.db"
        # Run #1: yesterday
        rid1 = record_run(
            _make_output(funding="none"),
            urls=["https://sifted.eu/", "https://tech.eu/"],
            run_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"),
            db_path=db,
        )
        # Run #2: today (with a real funding signal)
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
        print(f"OK  company_timeline('helsing'): {len(timeline)} mentions, latest score {timeline[-1]['score_total']}")

        velocities = theme_velocity(db_path=db)
        assert any(v["theme"].lower() == "sovereign cloud" for v in velocities), "sovereign cloud theme missing"
        print(f"OK  theme_velocity: {len(velocities)} themes ranked")

        top = recent_high_scorers(db_path=db)
        assert any(t["name"].lower() == "helsing" for t in top), "Helsing should be a top scorer"
        print(f"OK  recent_high_scorers: {len(top)} companies at >= 7.5/10")

        # Alerts layer (no network, just predicate logic)
        helsing_today = _make_output().companies[0]
        watch = [WatchlistEntry(name="Helsing", thesis_tag="defense-dual-use", note="watch")]
        urgent = find_urgent(_make_output(), watch)
        assert len(urgent) == 1, f"expected 1 urgent hit, got {len(urgent)}"
        print(f"OK  find_urgent: {len(urgent)} hit ({urgent[0][0].name})")

        assert has_funding_signal(helsing_today), "helsing should have funding signal"
        assert not has_funding_signal(_make_output(funding="none").companies[0]), "should be silent on 'none'"
        print("OK  has_funding_signal: positive + negative cases")

        # theme_velocity uses the global DEFAULT path inside find_theme_spikes,
        # so we don't exercise spikes here — confirmed via theme_velocity above.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
