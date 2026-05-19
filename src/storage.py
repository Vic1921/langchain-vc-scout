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
from pathlib import Path
from typing import Iterable, Iterator

from .schema import ScoredCompany, VCScoutOutput


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "vc_scout.db"


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
