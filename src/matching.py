"""Company-name matching.

Mapping the scout's free-text company names onto a fixed watchlist is
surprisingly error-prone: the model writes "Mistral", the watchlist says
"Mistral AI". Matching is deterministic-first — exact, then normalized form,
then a strict close-match — because a fuzzy false positive fires a bogus
urgent alert, which is worse than a miss.
"""

from __future__ import annotations

import difflib
import re

# Corporate suffixes / filler dropped during normalization so "Mistral AI"
# and "Mistral" collapse to the same key.
_NOISE = {
    "ai", "labs", "lab", "inc", "co", "corp", "ltd", "limited", "llc",
    "gmbh", "sas", "sa", "se", "bv", "oy", "ab", "plc", "the",
    "technologies", "technology", "io", "app", "group", "global",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation and corporate-suffix noise, collapse spaces.

    Falls back to the bare lowercased string if normalization would empty it
    (e.g. a name that is entirely a suffix word).
    """
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    tokens = [t for t in cleaned.split() if t and t not in _NOISE]
    return " ".join(tokens) or (name or "").strip().lower()


def close_match(query: str, candidates: list[str], cutoff: float = 0.88) -> str | None:
    """Highest-scoring candidate within `cutoff`, or None.

    Inputs should already be normalized. The cutoff is deliberately strict —
    this is the last resort before declaring no match.
    """
    if not query or not candidates:
        return None
    hits = difflib.get_close_matches(query, candidates, n=1, cutoff=cutoff)
    return hits[0] if hits else None
