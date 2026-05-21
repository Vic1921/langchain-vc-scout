"""Alerts: watchlist matching + Telegram tiered delivery.

Three tiers, ranked by how much they ought to interrupt a partner's day:

1. URGENT       — a watchlist company appears WITH a funding signal. Day-stop.
2. THEME-SPIKE  — a tracked theme's mention count crosses 2x its 7-day baseline.
3. DAILY DIGEST — the regular morning brief.

If no sink (Telegram or Slack) is configured the module is a no-op so local
runs stay quiet. Logging the would-have-sent message is on purpose — useful
for debugging the workflow without spamming the chat.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import requests

from .matching import close_match, normalize_name
from .schema import ScoredCompany, VCScoutOutput
from .storage import ConvictionDelta, theme_velocity


logger = logging.getLogger(__name__)


DEFAULT_WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "watchlist.csv"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Verbs that strongly imply a fundraising / deal-action moment. The model
# already extracts funding_signal as a structured field, but we double-check
# free-text fields too in case it slipped through.
FUNDING_VERBS = (
    "raises", "raised", "closes", "closed", "secures", "secured",
    "lands", "landed", "leads", "led", "in talks", "valuation",
    "series a", "series b", "series c", "seed round", "pre-seed",
    "term sheet", "tender offer", "acquires", "acquired",
)


@dataclass(frozen=True)
class WatchlistEntry:
    name: str
    thesis_tag: str
    note: str
    aliases: tuple[str, ...] = ()


def load_watchlist(path: Path | str = DEFAULT_WATCHLIST_PATH) -> list[WatchlistEntry]:
    """Load the watchlist, in priority order:

    1. the WATCHLIST_CSV env var holding raw CSV text — for CI / containers /
       workers where shipping a file alongside the code is awkward,
    2. the CSV file at `path` — for local or persistent-disk deployments.

    Missing both → empty list (urgent alerts simply never fire). Never raises,
    so an absent or malformed watchlist degrades gracefully.
    """
    raw = os.environ.get("WATCHLIST_CSV", "").strip()
    if raw:
        return _parse_watchlist(io.StringIO(raw))
    path = Path(path)
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            return _parse_watchlist(f)
    return []


def _parse_watchlist(handle) -> list[WatchlistEntry]:
    """Parse watchlist rows from any text handle.

    Columns: name, aliases, thesis_tag, note. `aliases` is an optional
    semicolon-separated list of alternate names; column order is irrelevant
    and a missing `aliases` column is fine (older watchlists still parse).
    """
    entries: list[WatchlistEntry] = []
    for row in csv.DictReader(handle):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        aliases = tuple(
            a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()
        )
        entries.append(
            WatchlistEntry(
                name=name,
                thesis_tag=(row.get("thesis_tag") or "").strip(),
                note=(row.get("note") or "").strip(),
                aliases=aliases,
            )
        )
    return entries


def has_funding_signal(company: ScoredCompany) -> bool:
    """Funding signal present if the structured field is non-trivial OR a known verb shows up."""
    signal = (company.funding_signal or "").strip().lower()
    if signal and signal != "none":
        return True
    blob = f"{company.rationale} {company.funding_signal}".lower()
    return any(verb in blob for verb in FUNDING_VERBS)


def _watchlist_index(watchlist: list[WatchlistEntry]) -> dict[str, WatchlistEntry]:
    """Map every normalized name + alias to its WatchlistEntry."""
    index: dict[str, WatchlistEntry] = {}
    for entry in watchlist:
        for label in (entry.name, *entry.aliases):
            key = normalize_name(label)
            if key:
                index.setdefault(key, entry)
    return index


def match_watchlist(
    company_name: str,
    watchlist: list[WatchlistEntry],
    index: dict[str, WatchlistEntry] | None = None,
) -> WatchlistEntry | None:
    """Resolve a company name to a watchlist entry: normalized exact, then close match."""
    if not watchlist:
        return None
    index = index if index is not None else _watchlist_index(watchlist)
    norm = normalize_name(company_name)
    hit = index.get(norm)
    if hit:
        return hit
    near = close_match(norm, list(index.keys()))
    return index.get(near) if near else None


def find_urgent(
    output: VCScoutOutput,
    watchlist: list[WatchlistEntry],
) -> list[tuple[ScoredCompany, WatchlistEntry]]:
    """Companies on the watchlist that surfaced today WITH a funding signal.

    Matching is normalized + alias-aware + close-match, so the model writing
    "Mistral" still resolves to a "Mistral AI" watchlist entry.
    """
    if not watchlist:
        return []
    index = _watchlist_index(watchlist)
    matches: list[tuple[ScoredCompany, WatchlistEntry]] = []
    for company in output.companies:
        hit = match_watchlist(company.name, watchlist, index)
        if hit and has_funding_signal(company):
            matches.append((company, hit))
    return matches


def find_theme_spikes(
    output: VCScoutOutput,
    window_days: int = 7,
    spike_ratio: float = 2.0,
    min_recent: int = 3,
) -> list[dict]:
    """Themes whose recent count is >= spike_ratio * prior count.

    `min_recent` prevents firing on a single mention of an obscure theme.
    """
    velocities = theme_velocity(window_days=window_days)
    today_themes = {t.lower() for t in output.themes}
    spikes: list[dict] = []
    for row in velocities:
        if row["theme"].lower() not in today_themes:
            continue
        if row["recent_n"] < min_recent:
            continue
        if row["prior_n"] == 0:
            spikes.append(row)
            continue
        if row["recent_n"] / row["prior_n"] >= spike_ratio:
            spikes.append(row)
    return spikes


def urgent_dedup_key(company: ScoredCompany) -> str:
    """Stable key for an urgent alert — same company + same funding signal = same alert.

    Hashing the funding-signal text means a genuinely *new* round re-fires,
    but the identical headline sitting on the page for three days does not.
    """
    signal = (company.funding_signal or "").strip().lower()
    digest = hashlib.sha1(signal.encode("utf-8")).hexdigest()[:12]
    return f"urgent:{company.name.lower()}:{digest}"


def theme_spike_dedup_key(theme: str) -> str:
    """Stable key for a theme-spike alert."""
    return f"theme_spike:{theme.strip().lower()}"


def _telegram_creds() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    return token, chat


def _post_telegram(text: str, parse_mode: str) -> bool:
    """POST to Telegram. Returns False (not an error) if creds are absent."""
    creds = _telegram_creds()
    if not creds:
        return False
    token, chat = creds
    # Telegram caps messages at 4096 chars. Truncate gracefully.
    body = text if len(text) <= 4000 else text[:3990] + "\n…(truncated)"
    resp = requests.post(
        TELEGRAM_API.format(token=token),
        json={"chat_id": chat, "text": body, "parse_mode": parse_mode, "disable_web_page_preview": True},
        timeout=15,
    )
    if not resp.ok:
        logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
        return False
    return True


def _post_slack(text: str) -> bool:
    """POST to a Slack incoming webhook. Returns False if SLACK_WEBHOOK_URL is unset.

    Slack mrkdwn shares *bold* / _italic_ syntax with Telegram Markdown, so the
    same message body renders acceptably in both.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return False
    resp = requests.post(webhook, json={"text": text}, timeout=15)
    if not resp.ok:
        logger.warning("Slack send failed: %s %s", resp.status_code, resp.text[:200])
        return False
    return True


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Deliver an alert to every configured sink (Telegram, Slack).

    Returns True if at least one sink accepted it. With no sink configured it
    logs the message and returns False — local runs stay quiet, CI stays
    debuggable. New sinks (email, etc.) slot in here without touching callers.
    """
    delivered = any([_post_telegram(text, parse_mode), _post_slack(text)])
    if not delivered:
        logger.info("No alert sink configured (Telegram/Slack) — message: %s", text[:140])
    return delivered


def send_urgent_alert(
    company: ScoredCompany,
    watch: WatchlistEntry,
    delta: ConvictionDelta | None = None,
) -> bool:
    conviction = f"\n*Conviction:* {delta.render()}" if delta else ""
    text = (
        f"🚨 *URGENT — watchlist hit with funding signal*\n\n"
        f"*{company.name}*  (score {company.score_total}/10){conviction}\n"
        f"Thesis tag: `{watch.thesis_tag or '—'}`\n"
        f"Note: {watch.note or '—'}\n\n"
        f"*Funding signal:* {company.funding_signal}\n"
        f"*Regulatory:* {company.regulatory_tag}    *Sovereignty:* {company.sovereignty_tag}\n"
        f"*Vintage:* {company.vintage_match}\n\n"
        f"_{company.rationale}_"
    )
    return send_message(text)


def send_theme_spike(spike: dict) -> bool:
    text = (
        f"📈 *Theme spike*\n\n"
        f"*{spike['theme']}* — {spike['recent_n']} mentions in the recent window "
        f"vs {spike['prior_n']} in the prior. Worth a closer look."
    )
    return send_message(text)


def send_daily_digest(
    output: VCScoutOutput,
    report_path: str | None,
    deltas: dict[str, ConvictionDelta] | None = None,
    monthly: dict | None = None,
    suggestions: list[dict] | None = None,
) -> bool:
    deltas = deltas or {}
    top = sorted(output.companies, key=lambda c: c.score_total, reverse=True)[:5]

    def _line(i: int, c: ScoredCompany) -> str:
        d = deltas.get(c.name.lower())
        badge = f"  ({d.badge()})" if d else ""
        return (
            f"  {i + 1}. *{c.name}* — {c.score_total}/10{badge}  "
            f"[{c.regulatory_tag}; {c.sovereignty_tag}]"
        )

    top_block = "\n".join(_line(i, c) for i, c in enumerate(top)) or "  (no scored companies today)"
    text = (
        f"☕ *VC Scout — daily digest*\n\n"
        f"*Headline:* {output.headline_summary[:280]}\n\n"
        f"*Visible only when combined:* {output.visible_only_when_combined[:280]}\n\n"
        f"*Contrarian view:* {output.contrarian_view[:240]}\n\n"
        f"*Top scored:*\n{top_block}\n\n"
        f"Themes: {', '.join(output.themes[:6]) if output.themes else '—'}\n"
    )
    if monthly:
        text += (
            f"\n💸 Scout spend ({monthly['year_month']}): "
            f"${monthly['cost']:.2f} · {monthly['runs']} run(s) · "
            f"{monthly['input_tokens']:,} in / {monthly['output_tokens']:,} out tokens\n"
        )
    if suggestions:
        names = ", ".join(s["name"] for s in suggestions[:3])
        more = f" +{len(suggestions) - 3} more" if len(suggestions) > 3 else ""
        text += f"\n💡 Recurring & off-watchlist: {names}{more} — run --suggest-watchlist\n"
    if report_path:
        text += f"\nFull report: `{report_path}`"
    return send_message(text)
