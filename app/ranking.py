"""Deterministic candidate ranking — a shortlisting aid, not a hiring decision.

This computes a composite score in plain Python arithmetic over already-
extracted fields. The LLM is never involved in scoring, matching the same
"LLM extracts, Python decides" split the rest of this codebase follows for
grounding and CGPA detection.

Why this module exists and what it is NOT:

The source design docs for this project (cl. 4.1 Note, gazette p. 60) are
explicit that in a real statutory faculty-hiring process, a score is valid
for narrowing a shortlist but the actual selection is legally an interview
decision — presenting a ranked list as if it settles hiring "misrepresents a
legally interview-decided process." This module was added on an explicit,
informed instruction to build a ranking feature anyway, for the user's own
internal use. It is built to be as safe as that instruction allows:

  - The formula is fully visible in DEFAULT_COMPONENTS below, not hidden
    inside a model.
  - This is a heuristic the author invented for triage convenience. It has
    no statutory basis, unlike the full design's "Research Score" (which is
    grounded in an actual UGC point-system rubric and scoped narrowly to
    rank re-categorization, never candidate selection). Do not treat this
    number as authoritative.
  - Every row keeps its needs_review flag and reasons next to its rank, so a
    high rank built on unverified data stays visibly suspect.
  - Missing inputs are excluded from the score, not defaulted to zero -- a
    resume that never states publications_count is not assumed to have none;
    that would fabricate a value from absence.
  - score_completeness says how much of the score is actually backed by
    data, so "62 points, 40% complete" isn't mistaken for "62 points, fully
    assessed".

This produces a shortlisting order, never a hiring decision. Selection still
has to be made against the actual statutory eligibility rules (not
implemented anywhere in this MVP slice) and a human interview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from llm.confidence import evaluate
from llm.interface import ExtractionResult, ResumeRecord

RANKING_DISCLAIMER = (
    "Ranking is a shortlisting heuristic computed from extracted resume "
    "fields, not a hiring decision. It carries no statutory weight. Rows "
    "flagged needs_review may rank on unverified data -- check the "
    "review_reasons column before trusting a score. Final selection must "
    "still follow the applicable eligibility rules and interview process."
)

NET_SET_QUALIFIED = frozenset({"NET", "SET", "SLET"})


@dataclass(frozen=True)
class ScoreComponent:
    """One input to the composite score: which fields feed it, and its cap."""

    name: str
    fields: tuple[str, ...]
    max_points: float
    # Field values (in `fields` order) -> points. Must return None when no
    # field had data, so an absent input is excluded rather than counted as
    # an assessed zero.
    to_points: Callable[..., float | None]


def _linear(value, floor: float, ceiling: float, max_points: float) -> float | None:
    if value is None:
        return None
    clamped = max(floor, min(ceiling, float(value)))
    return (clamped - floor) / (ceiling - floor) * max_points


def _bonus_if_true(value, max_points: float) -> float | None:
    if value is None:
        return None
    return max_points if value else 0.0


def _bonus_if_qualified(value, max_points: float) -> float | None:
    if value is None:
        return None
    return max_points if value in NET_SET_QUALIFIED else 0.0


def _academic_marks(marks_pct, cgpa, max_points: float = 20.0) -> float | None:
    """Score the candidate's Master's marks, on whichever scale was reported.

    Both fields describe the same one degree, so they share a single budget
    rather than stacking. The percentage is preferred when present because
    statutory thresholds are written in percentage terms; the CGPA is the
    fallback for resumes that report only a grade point. Neither is ever
    converted into the other.

    Deliberately not `max()` of the two: that quietly rewards whoever reports
    the more flattering figure, which on real resumes meant a candidate's
    85.2% school marks outscoring their own 7.78 Master's GPA.
    """
    if marks_pct is not None:
        return _linear(marks_pct, 0, 100, max_points)
    return _linear(cgpa, 0, 10, max_points)


# Chosen from the fields the source docs themselves treat as
# eligibility-relevant (marks, PhD, NET/SET, teaching experience,
# publications) -- but the weighting is this project's own heuristic, not a
# standard. Edit freely; this is the one place it lives.
DEFAULT_COMPONENTS: tuple[ScoreComponent, ...] = (
    ScoreComponent("teaching_years", ("teaching_years_raw",), 30.0, lambda v: _linear(v, 0, 20, 30.0)),
    ScoreComponent("publications", ("publications_count",), 25.0, lambda v: _linear(v, 0, 20, 25.0)),
    ScoreComponent("academic_marks", ("marks_pct", "cgpa"), 20.0, _academic_marks),
    ScoreComponent("phd", ("has_phd",), 15.0, lambda v: _bonus_if_true(v, 15.0)),
    ScoreComponent("net_set", ("net_set_status",), 10.0, lambda v: _bonus_if_qualified(v, 10.0)),
)


@dataclass
class CandidateScore:
    source_filename: str
    score: float | None  # None if literally no component had data
    max_possible: float
    completeness: float  # 0.0-1.0: fraction of components that had data
    needs_review: bool
    review_reasons: str | None
    rank: int | None = field(default=None)  # filled in by rank_candidates


def score_result(result: ExtractionResult, components: tuple[ScoreComponent, ...] = DEFAULT_COMPONENTS) -> tuple[float | None, float, float]:
    """Return (score, max_possible, completeness) for one extraction."""
    earned = 0.0
    possible = 0.0
    available = 0
    for comp in components:
        values = [getattr(result, f).value for f in comp.fields]
        points = comp.to_points(*values)
        possible += comp.max_points
        if points is not None:
            earned += points
            available += 1
    completeness = available / len(components) if components else 0.0
    score = round(earned, 1) if available > 0 else None
    return score, possible, completeness


def rank_candidates(
    records: list[ResumeRecord],
    components: tuple[ScoreComponent, ...] = DEFAULT_COMPONENTS,
) -> list[CandidateScore]:
    """Score and rank every record. Failed extractions score None and sort last.

    Ties keep a stable, deterministic order (by filename) rather than an
    arbitrary one, so the same input always produces the same rank.
    """
    scored: list[CandidateScore] = []
    for record in records:
        if record.result is None:
            scored.append(
                CandidateScore(
                    source_filename=record.source_filename,
                    score=None,
                    max_possible=sum(c.max_points for c in components),
                    completeness=0.0,
                    needs_review=True,
                    review_reasons="parse_error" if record.parse_error else None,
                )
            )
            continue
        outcome = evaluate(record.result, record.parse_error)
        score, max_possible, completeness = score_result(record.result, components)
        scored.append(
            CandidateScore(
                source_filename=record.source_filename,
                score=score,
                max_possible=max_possible,
                completeness=completeness,
                needs_review=outcome.needs_review,
                review_reasons="; ".join(outcome.reasons) or None,
            )
        )

    # Highest score first; None (no data at all) sorts last. Filename as a
    # deterministic tiebreaker, not upload order, so re-running the same
    # batch always produces the same ranking.
    scored.sort(key=lambda c: (c.score is None, -(c.score or 0), c.source_filename))
    for i, c in enumerate(scored, start=1):
        c.rank = i
    return scored
