"""Self-grading pass: a cheap Haiku auditor that grades each report.

Why: an LLM left alone drifts toward confident, fluent hype. A second model
whose only job is to penalize hype and vagueness is a cheap quality gate. If
a note scores below the bar, the main agent regenerates it once with the
auditor's specific complaints fed back in.

The grader uses Haiku (≈5x cheaper than Sonnet) and is deliberately skipped
on the intra-day --urgent path, where prose quality doesn't matter.
"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model

from .costs import CostRecord, extract_cost
from .schema import QualityGrade, VCScoutOutput


logger = logging.getLogger(__name__)

QUALITY_BAR = 6  # score >= bar passes; below triggers one regeneration
GRADER_MODEL = "claude-haiku-4-5-20251001"

GRADER_PROMPT = """You are a ruthless quality auditor for VC research notes.
Grade the note below 0-10 on CONCRETENESS and SIGNAL.

Penalize hard:
- Hype / fluff words: "revolutionary", "game-changing", "next-gen", "poised to",
  "cutting-edge", "transformative", "disrupt", "leverage synergies".
- Vague claims with no specifics ("strong team", "huge market", "well-positioned")
  that aren't backed by a concrete detail from a source.
- Generic observations a partner could have written without reading anything.
- Company rationales missing inline [source: <url>] citations.

Reward:
- Specific numbers, named investors, concrete regulatory points.
- Cross-source synthesis that names exactly what was combined.
- A contrarian view with a falsifiable claim.

Return `score` (0-10) and `issues` (a list of specific, actionable weaknesses).
"""


def grade_report(output: VCScoutOutput) -> tuple[QualityGrade, CostRecord]:
    """Grade a report with the Haiku auditor.

    Returns the grade and the CostRecord for the grading call. A grader
    failure is non-fatal: it returns a passing grade so a flaky auditor
    never blocks a report.
    """
    try:
        model = init_chat_model(GRADER_MODEL, temperature=0, max_tokens=700, timeout=45)
        grader = model.with_structured_output(QualityGrade, include_raw=True)
        result = grader.invoke(
            GRADER_PROMPT + "\n\n--- NOTE UNDER REVIEW ---\n" + output.model_dump_json(indent=2)
        )
        grade = result["parsed"]
        if grade is None:
            raise ValueError(f"grader returned no parseable grade: {result.get('parsing_error')}")
        cost = extract_cost([result["raw"]], fallback_model=GRADER_MODEL)
        logger.info("Self-grade: %d/10 (%d issue(s))", grade.score, len(grade.issues))
        return grade, cost
    except Exception as exc:  # noqa: BLE001 — the grader must never abort a run
        logger.warning("Grading pass failed (%s) — treating as pass", exc)
        return QualityGrade(score=QUALITY_BAR, issues=[]), CostRecord(GRADER_MODEL, 0, 0, 0.0)


def regeneration_prompt(grade: QualityGrade) -> str:
    """Build the feedback message asking the agent to revise a rejected note."""
    issues = "\n".join(f"- {i}" for i in grade.issues) or "- Too generic; add concrete specifics."
    return f"""Your previous VCScoutOutput was rejected by quality review with a
score of {grade.score}/10. Issues:
{issues}

Produce a revised VCScoutOutput that fixes every issue. Do NOT call
scrape_headlines again — reuse the headlines already gathered. Be concrete:
cite sources inline, name investors and numbers, and cut every hype word.
"""
