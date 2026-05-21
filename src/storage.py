"""SQLite-backed knowledge base for cross-run memory.

Every run records the synthesis + every ScoredCompany + every theme. This is
what turns "stateless daily report" into "we've been tracking this since
February" — the line a partner actually wants to say in a meeting.

Schema is plain SQLite (no ORM) so the DB file can be shipped, opened in any
SQLite viewer, and committed to a `reports` branch on CI runs.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .matching import normalize_name
from .schema import ScoredCompany, VCScoutOutput


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "vc_scout.db"


def _utcnow() -> str:
    """UTC timestamp in the same `YYYY-MM-DDTHH:MM:SS` format as runs.run_at."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    report_path TEXT,
    headline_summary TEXT,
    why_it_matters TEXT,
    visible_only_when_combined TEXT,
    investment_angle TEXT,
    contrarian_view TEXT,
    risks TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    sources_json TEXT,
    score_market INTEGER,
    score_team INTEGER,
    score_product INTEGER,
    score_defensibility INTEGER,
    score_interest INTEGER,
    score_total REAL,
    regulatory_tag TEXT,
    sovereignty_tag TEXT,
    vintage_match TEXT,
    funding_signal TEXT,
    rationale TEXT
);

CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(LOWER(name));
CREATE INDEX IF NOT EXISTS idx_companies_run ON companies(run_id);

CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    theme TEXT NOT NULL,
    theme_lc TEXT GENERATED ALWAYS AS (LOWER(theme)) VIRTUAL
);

CREATE INDEX IF NOT EXISTS idx_themes_theme_lc ON themes(theme_lc);
CREATE INDEX IF NOT EXISTS idx_themes_run ON themes(run_id);

CREATE TABLE IF NOT EXISTS sent_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_kind TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    sent_at TEXT NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_sent_alerts_key ON sent_alerts(dedup_key);

CREATE TABLE IF NOT EXISTS run_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id),
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_costs_recorded ON run_costs(recorded_at);
"""


@contextmanager
def _connect(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_run(
    output: VCScoutOutput,
    urls: Iterable[str],
    run_at: str,
    report_path: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    """Persist a run and all its companies + themes. Returns the new run_id."""
    urls = list(urls)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO runs (
                run_at, sources_json, report_path,
                headline_summary, why_it_matters, visible_only_when_combined,
                investment_angle, contrarian_view, risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_at,
                json.dumps(urls),
                report_path,
                output.headline_summary,
                output.why_it_matters,
                output.visible_only_when_combined,
                output.possible_investment_angle,
                output.contrarian_view,
                output.risks_or_limitations,
            ),
        )
        run_id = cur.lastrowid

        for company in output.companies:
            conn.execute(
                """
                INSERT INTO companies (
                    run_id, name, sources_json,
                    score_market, score_team, score_product, score_defensibility, score_interest,
                    score_total, regulatory_tag, sovereignty_tag, vintage_match,
                    funding_signal, rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    company.name,
                    json.dumps(company.sources),
                    company.score_market,
                    company.score_team,
                    company.score_product,
                    company.score_defensibility,
                    company.score_interest,
                    company.score_total,
                    company.regulatory_tag,
                    company.sovereignty_tag,
                    company.vintage_match,
                    company.funding_signal,
                    company.rationale,
                ),
            )

        for theme in output.themes:
            conn.execute(
                "INSERT INTO themes (run_id, theme) VALUES (?, ?)",
                (run_id, theme),
            )

        return run_id


def company_timeline(name: str, db_path: Path | str = DEFAULT_DB_PATH) -> list[dict]:
    """Every recorded mention of a company across all runs, oldest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                r.run_at, c.score_total, c.regulatory_tag, c.sovereignty_tag,
                c.vintage_match, c.funding_signal, c.rationale, c.sources_json
            FROM companies c
            JOIN runs r ON r.id = c.run_id
            WHERE LOWER(c.name) = LOWER(?)
            ORDER BY r.run_at ASC
            """,
            (name,),
        ).fetchall()
        return [dict(row) for row in rows]


def theme_velocity(
    window_days: int = 7,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict]:
    """Per-theme mention counts in the last `window_days` vs the prior window.

    Returns rows sorted by `recent` descending so the heat-map / spike checks
    can read the top off the front. Useful both for an "accelerating themes"
    digest section and for the theme-spike alert.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            WITH recent AS (
                SELECT LOWER(t.theme) AS theme_lc, t.theme AS theme,
                       COUNT(*) AS n
                FROM themes t
                JOIN runs r ON r.id = t.run_id
                WHERE r.run_at >= datetime('now', ?)
                GROUP BY LOWER(t.theme)
            ),
            prior AS (
                SELECT LOWER(t.theme) AS theme_lc,
                       COUNT(*) AS n
                FROM themes t
                JOIN runs r ON r.id = t.run_id
                WHERE r.run_at < datetime('now', ?)
                  AND r.run_at >= datetime('now', ?)
                GROUP BY LOWER(t.theme)
            )
            SELECT recent.theme,
                   recent.n AS recent_n,
                   COALESCE(prior.n, 0) AS prior_n
            FROM recent
            LEFT JOIN prior ON prior.theme_lc = recent.theme_lc
            ORDER BY recent.n DESC
            """,
            (
                f"-{window_days} days",
                f"-{window_days} days",
                f"-{2 * window_days} days",
            ),
        ).fetchall()
        return [dict(row) for row in rows]


def recent_high_scorers(
    days: int = 30,
    min_score: float = 7.5,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict]:
    """Companies scored >= min_score within the last `days`, deduplicated by name."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.name, MAX(c.score_total) AS best_score,
                   MAX(c.regulatory_tag) AS regulatory_tag,
                   MAX(c.sovereignty_tag) AS sovereignty_tag,
                   MAX(c.vintage_match) AS vintage_match,
                   MAX(r.run_at) AS last_seen_at
            FROM companies c
            JOIN runs r ON r.id = c.run_id
            WHERE r.run_at >= datetime('now', ?)
              AND c.score_total >= ?
            GROUP BY LOWER(c.name)
            ORDER BY best_score DESC, last_seen_at DESC
            """,
            (f"-{days} days", min_score),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Conviction delta — how a company's score moved vs the last time we saw it
# ---------------------------------------------------------------------------
def _classify_trend(scores: list[float]) -> str:
    """Label a score trajectory given oldest -> newest scores."""
    if len(scores) < 2:
        return "n/a"
    diffs = [b - a for a, b in zip(scores, scores[1:])]
    ups = sum(1 for d in diffs if d > 0)
    downs = sum(1 for d in diffs if d < 0)
    net = round(scores[-1] - scores[0], 1)
    if net >= 0.5 and ups >= downs:
        return "rising"
    if net <= -0.5 and downs >= ups:
        return "cooling"
    if ups and downs:
        return "volatile"
    return "flat"


@dataclass(frozen=True)
class ConvictionDelta:
    """A company's current score against its KB history."""

    name: str
    current_score: float
    is_new: bool
    prev_score: float | None = None
    prev_date: str | None = None
    trend: str = "n/a"

    @property
    def delta(self) -> float | None:
        if self.is_new or self.prev_score is None:
            return None
        return round(self.current_score - self.prev_score, 1)

    def badge(self) -> str:
        """Compact marker for digest lines / console, e.g. '↑ +0.5' or 'NEW'."""
        if self.is_new:
            return "NEW"
        d = self.delta or 0.0
        if d > 0:
            return f"↑ +{d}"
        if d < 0:
            return f"↓ {d}"
        return "→ 0.0"

    def render(self) -> str:
        """Full sentence for alerts / reports."""
        if self.is_new:
            return "new to the knowledge base"
        trend = f", {self.trend}" if self.trend not in ("n/a", "flat") else ""
        return (
            f"{self.prev_score} → {self.current_score} "
            f"({self.badge()}{trend}, last seen {self.prev_date})"
        )


def compute_conviction_deltas(
    companies: Iterable[ScoredCompany],
    history_window: int = 4,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, ConvictionDelta]:
    """Map each company (lowercased name) to its score change + trend vs the KB.

    MUST be called BEFORE record_run for the current run — otherwise the
    current run's freshly-inserted rows become the 'previous' record. The
    trend reads the last `history_window` scores plus the current one, so a
    multi-run slide ("cooling") is visible, not just a single-step delta.
    """
    deltas: dict[str, ConvictionDelta] = {}
    with _connect(db_path) as conn:
        for company in companies:
            rows = conn.execute(
                """
                SELECT c.score_total, r.run_at
                FROM companies c
                JOIN runs r ON r.id = c.run_id
                WHERE LOWER(c.name) = LOWER(?)
                ORDER BY r.run_at DESC
                LIMIT ?
                """,
                (company.name, history_window),
            ).fetchall()
            if not rows:
                deltas[company.name.lower()] = ConvictionDelta(
                    name=company.name, current_score=company.score_total, is_new=True,
                )
                continue
            prior_scores = [r["score_total"] for r in reversed(rows)]  # oldest -> newest
            deltas[company.name.lower()] = ConvictionDelta(
                name=company.name,
                current_score=company.score_total,
                is_new=False,
                prev_score=rows[0]["score_total"],
                prev_date=rows[0]["run_at"][:10],
                trend=_classify_trend(prior_scores + [company.score_total]),
            )
    return deltas


# ---------------------------------------------------------------------------
# Alert dedup ledger — stops the hourly urgent cron re-sending the same alert
# ---------------------------------------------------------------------------
def was_alert_sent(
    dedup_key: str,
    within_days: int = 14,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> bool:
    """True if an alert with this key was sent within the cooldown window."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_alerts WHERE dedup_key = ? AND sent_at >= datetime('now', ?) LIMIT 1",
            (dedup_key, f"-{within_days} days"),
        ).fetchone()
        return row is not None


def mark_alert_sent(
    alert_kind: str,
    dedup_key: str,
    detail: str = "",
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Record that an alert fired. INSERT OR REPLACE refreshes the cooldown."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO sent_alerts (alert_kind, dedup_key, sent_at, detail)
            VALUES (?, ?, ?, ?)
            """,
            (alert_kind, dedup_key, _utcnow(), detail),
        )


# ---------------------------------------------------------------------------
# Cost ledger — token spend per run, summarized per month
# ---------------------------------------------------------------------------
def record_cost(
    run_id: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """Persist the cost of one LLM call against a run."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO run_costs (run_id, model, input_tokens, output_tokens, cost_usd, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, model, input_tokens, output_tokens, cost_usd, _utcnow()),
        )


def monthly_cost(
    year_month: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict:
    """Month-to-date spend. `year_month` defaults to the current UTC month."""
    year_month = year_month or datetime.now(timezone.utc).strftime("%Y-%m")
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0)       AS cost,
                   COALESCE(SUM(input_tokens), 0)   AS input_tokens,
                   COALESCE(SUM(output_tokens), 0)  AS output_tokens,
                   COUNT(DISTINCT run_id)           AS runs
            FROM run_costs
            WHERE recorded_at LIKE ?
            """,
            (f"{year_month}%",),
        ).fetchone()
        return {**dict(row), "year_month": year_month}


# ---------------------------------------------------------------------------
# Watchlist auto-suggest — companies the KB keeps surfacing that you don't track
# ---------------------------------------------------------------------------
def suggest_watchlist(
    known_normalized: set[str],
    min_appearances: int = 3,
    min_avg_score: float = 7.5,
    days: int = 30,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict]:
    """Recurring, high-scoring companies that are NOT already on the watchlist.

    `known_normalized` is the set of normalize_name() forms of every watchlist
    name + alias — the caller builds it, so storage stays watchlist-agnostic.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.name AS name,
                   COUNT(*) AS appearances,
                   ROUND(AVG(c.score_total), 1) AS avg_score,
                   MAX(c.score_total) AS best_score,
                   MAX(c.sovereignty_tag) AS sovereignty_tag,
                   MAX(c.regulatory_tag) AS regulatory_tag
            FROM companies c
            JOIN runs r ON r.id = c.run_id
            WHERE r.run_at >= datetime('now', ?)
            GROUP BY LOWER(c.name)
            HAVING appearances >= ? AND avg_score >= ?
            ORDER BY avg_score DESC, appearances DESC
            """,
            (f"-{days} days", min_appearances, min_avg_score),
        ).fetchall()
    return [dict(r) for r in rows if normalize_name(r["name"]) not in known_normalized]


# ---------------------------------------------------------------------------
# Hit-rate scorecard — did the scout's high-conviction calls play out?
# ---------------------------------------------------------------------------
def hit_rate(
    min_score: float = 8.0,
    settle_days: int = 90,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict:
    """Of companies first scored >= min_score long enough ago to judge, the
    fraction that later showed a funding signal in a subsequent run."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settle_days)).strftime("%Y-%m-%dT%H:%M:%S")
    with _connect(db_path) as conn:
        highs = conn.execute(
            """
            SELECT LOWER(c.name) AS name_lc, c.name AS name, MIN(r.run_at) AS first_high
            FROM companies c JOIN runs r ON r.id = c.run_id
            WHERE c.score_total >= ?
            GROUP BY LOWER(c.name)
            """,
            (min_score,),
        ).fetchall()
        fundings = conn.execute(
            """
            SELECT LOWER(c.name) AS name_lc, r.run_at AS run_at
            FROM companies c JOIN runs r ON r.id = c.run_id
            WHERE c.funding_signal IS NOT NULL
              AND LOWER(TRIM(c.funding_signal)) NOT IN ('none', '')
            """
        ).fetchall()
    funding_dates: dict[str, list[str]] = {}
    for f in fundings:
        funding_dates.setdefault(f["name_lc"], []).append(f["run_at"])
    evaluated = 0
    hits: list[str] = []
    for h in highs:
        if h["first_high"] > cutoff:
            continue  # too recent to fairly judge
        evaluated += 1
        if any(d >= h["first_high"] for d in funding_dates.get(h["name_lc"], [])):
            hits.append(h["name"])
    return {
        "evaluated": evaluated,
        "hits": len(hits),
        "rate": round(len(hits) / evaluated, 3) if evaluated else 0.0,
        "hit_names": hits,
        "min_score": min_score,
        "settle_days": settle_days,
    }


# ---------------------------------------------------------------------------
# Weekly-rollup helpers
# ---------------------------------------------------------------------------
def top_movers(days: int = 7, limit: int = 8, db_path: Path | str = DEFAULT_DB_PATH) -> list[dict]:
    """Companies with the biggest score change across the window (first vs last seen)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT LOWER(c.name) AS name_lc, c.name AS name, r.run_at, c.score_total
            FROM companies c JOIN runs r ON r.id = c.run_id
            WHERE r.run_at >= datetime('now', ?)
            ORDER BY r.run_at ASC
            """,
            (f"-{days} days",),
        ).fetchall()
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["name_lc"], []).append(row)
    movers: list[dict] = []
    for recs in grouped.values():
        if len(recs) < 2:
            continue
        delta = round(recs[-1]["score_total"] - recs[0]["score_total"], 1)
        if delta == 0:
            continue
        movers.append({
            "name": recs[-1]["name"],
            "delta": delta,
            "first_score": recs[0]["score_total"],
            "last_score": recs[-1]["score_total"],
        })
    movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
    return movers[:limit]


def new_entrants(days: int = 7, db_path: Path | str = DEFAULT_DB_PATH) -> list[dict]:
    """Companies whose first-ever KB appearance falls within the window."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.name AS name, MIN(r.run_at) AS first_seen,
                   MAX(c.score_total) AS best_score
            FROM companies c JOIN runs r ON r.id = c.run_id
            GROUP BY LOWER(c.name)
            HAVING first_seen >= datetime('now', ?)
            ORDER BY best_score DESC
            """,
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def count_runs(days: int = 7, db_path: Path | str = DEFAULT_DB_PATH) -> int:
    """Number of recorded runs in the window."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE run_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchone()
        return row["n"]
