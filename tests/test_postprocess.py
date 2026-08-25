"""Tests for the shared post-processing both providers run on model output.

Covers the two deterministic checks a model can't be trusted to do on itself:
evidence that isn't really in the resume, and a CGPA masquerading as a
percentage. No model or network needed.
"""

from __future__ import annotations

from datetime import date

import pytest

from llm.confidence import evaluate
from llm.interface import ExtractionResult
from llm.postprocess import looks_like_cgpa, sanitize

RESUME_TEXT = (
    "Jane Doe. PhD in Mathematics, awarded 2020. UGC-NET June 2015. "
    "5 years teaching. 2 publications listed. M.Tech 8.2 CGPA."
)


def _f(value, confidence=0.9, evidence=None):
    return {"value": value, "confidence": confidence, "evidence": evidence}


def _result(**overrides) -> ExtractionResult:
    base = dict(
        candidate_name=_f("Jane Doe", 0.9, "Jane Doe"),
        highest_degree=_f("PhD", 0.9, "PhD in Mathematics"),
        marks_pct=_f(None, 0.0, None),
        has_phd=_f(True, 0.9, "PhD in Mathematics"),
        phd_award_date=_f(date(2020, 1, 1), 0.8, "awarded 2020"),
        phd_regulation=_f(None, 0.0, None),
        masters_award_date=_f(None, 0.0, None),
        net_set_status=_f("NET", 0.95, "UGC-NET June 2015"),
        set_state=_f(None, 0.0, None),
        study_leave_taken=_f(None, 0.0, None),
        teaching_years_raw=_f(5, 0.8, "5 years teaching"),
        publications_count=_f(2, 0.9, "2 publications listed"),
        raw_llm_output="{}",
    )
    base.update(overrides)
    return ExtractionResult(**base)


# --- grounding -------------------------------------------------------------

def test_grounded_evidence_survives_untouched():
    result, stats = sanitize(_result(), RESUME_TEXT)
    assert stats["ungrounded_evidence"] == 0
    assert result.net_set_status.evidence == "UGC-NET June 2015"


def test_fabricated_evidence_is_nulled_but_value_is_kept():
    r = _result(highest_degree=_f("PhD", 0.9, "this sentence is not in the resume"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["ungrounded_evidence"] == 1
    assert result.highest_degree.value == "PhD"
    assert result.highest_degree.evidence is None


def test_evidence_from_a_different_document_is_stripped():
    r = _result(net_set_status=_f("NET", 0.95, "UGC-NET (Mathematical Sciences), June 2012"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["ungrounded_evidence"] == 1
    assert result.net_set_status.evidence is None


def test_dates_round_trip_through_sanitize():
    result, _ = sanitize(_result(), RESUME_TEXT)
    assert result.phd_award_date.value == date(2020, 1, 1)
    assert isinstance(result.phd_award_date.value, date)


# --- CGPA vs percentage ----------------------------------------------------

@pytest.mark.parametrize(
    "value,evidence",
    [
        (8.2, "8.2% M.E.(Comp.Engg)"),   # real case: CGPA written with a % sign
        (9.13, "9.13 CGPA"),
        (8.79, "8.79"),
        (10.0, "CGPA 10 out of 10"),
        (7.5, "SGPA 7.5"),
    ],
)
def test_grade_points_are_not_accepted_as_percentages(value, evidence):
    assert looks_like_cgpa(value, evidence) is True


@pytest.mark.parametrize(
    "value,evidence",
    [
        (76.6, "76.6%"),
        (55.0, "First class 55%"),
        (91.0, "91% (First Year)"),
        (61.12, "61.12%"),
    ],
)
def test_real_percentages_are_accepted(value, evidence):
    assert looks_like_cgpa(value, evidence) is False


def test_absent_marks_is_not_treated_as_cgpa():
    assert looks_like_cgpa(None, None) is False


def test_cgpa_value_is_dropped_but_evidence_is_kept_for_the_reviewer():
    """The number must not flow downstream as a percentage, but the reviewer
    still needs to see the raw text to enter the right figure."""
    r = _result(marks_pct=_f(8.2, 0.95, "M.Tech 8.2 CGPA"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["cgpa_as_percentage"] == 1
    assert result.marks_pct.value is None
    assert result.marks_pct.confidence == 0.0
    assert result.marks_pct.evidence == "M.Tech 8.2 CGPA"


def test_dropped_cgpa_routes_the_row_to_review_with_a_named_reason():
    r = _result(marks_pct=_f(8.2, 0.95, "M.Tech 8.2 CGPA"))
    result, _ = sanitize(r, RESUME_TEXT)
    outcome = evaluate(result)
    assert outcome.needs_review
    assert "marks_pct:cgpa_not_percentage" in outcome.reasons


def test_a_genuine_percentage_does_not_trigger_the_cgpa_rule():
    r = _result(marks_pct=_f(76.6, 0.95, "2 publications listed"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["cgpa_as_percentage"] == 0
    assert result.marks_pct.value == 76.6


def test_stats_carry_no_candidate_data():
    """Stats get logged, so they must be counts only."""
    r = _result(marks_pct=_f(8.2, 0.95, "M.Tech 8.2 CGPA"))
    _, stats = sanitize(r, RESUME_TEXT)
    assert all(isinstance(v, int) for v in stats.values())
