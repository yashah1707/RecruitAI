"""Tests for the deterministic candidate scorer.

No model involved -- scoring is plain Python over already-extracted fields.
The behaviours that matter most here are the ones that stop the number being
misleading: absent data must not score as zero, and completeness must fall
when inputs are missing.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.ranking import (
    DEFAULT_COMPONENTS,
    RANKING_DISCLAIMER,
    rank_candidates,
    score_result,
)
from llm.interface import ExtractionResult, ResumeRecord

MAX_POSSIBLE = sum(c.max_points for c in DEFAULT_COMPONENTS)


def _f(value, confidence=0.95, evidence="quoted from the resume"):
    return {"value": value, "confidence": confidence, "evidence": evidence}


ABSENT = {"value": None, "confidence": 0.0, "evidence": None}


def build(**overrides) -> ExtractionResult:
    base = dict(
        candidate_name=_f("A. Candidate"),
        highest_degree=_f("PG"),
        marks_pct=_f(70.0),
        cgpa=ABSENT,
        has_phd=_f(False),
        phd_status=_f("NOT_APPLICABLE"),
        phd_award_date=ABSENT,
        phd_regulation=ABSENT,
        masters_award_date=_f(date(2012, 5, 1)),
        net_set_status=_f("NONE"),
        set_state=ABSENT,
        study_leave_taken=ABSENT,
        teaching_years_raw=_f(5.0),
        publications_count=_f(2),
        publication_titles=_f(["Paper One", "Paper Two"]),
        publications_in_progress_count=_f(0),
        publications_in_progress_titles=_f([]),
        raw_llm_output="{}",
    )
    base.update(overrides)
    return ExtractionResult(**base)


def _record(result, name="cv.pdf") -> ResumeRecord:
    return ResumeRecord(source_filename=name, processed_at=datetime(2026, 8, 25), result=result)


# --- the anti-misleading guarantees -----------------------------------------

def test_absent_field_is_excluded_from_scoring_not_counted_as_zero():
    """Scoring a missing marks_pct as 0 would invent a fact from an absence --
    the same failure the extraction layer refuses to make. Both cases earn 0
    points, but only one of them was actually assessed."""
    with_data = build(marks_pct=_f(0.0))
    without = build(marks_pct=ABSENT)

    score_a, _, completeness_a = score_result(with_data)
    score_b, _, completeness_b = score_result(without)

    assert score_a == score_b
    assert completeness_b < completeness_a


def test_completeness_drops_as_inputs_go_missing():
    """Only the nullable score inputs can go missing: the schema forbids a
    null has_phd / net_set_status / publications_count, so a valid extraction
    always has at least those three."""
    full = score_result(build())[2]
    partial = score_result(build(marks_pct=ABSENT, teaching_years_raw=ABSENT))[2]
    assert full == 1.0
    assert partial == pytest.approx(3 / 5)


def test_a_valid_extraction_always_has_some_scoreable_data():
    """has_phd, net_set_status and publications_count are non-nullable, so
    completeness can never fall to zero for a row that extracted at all --
    only a failed extraction scores None (covered separately below)."""
    sparse = build(marks_pct=ABSENT, teaching_years_raw=ABSENT)
    score, _, completeness = score_result(sparse)
    assert score is not None
    assert completeness >= 3 / 5


# --- ordering ---------------------------------------------------------------

def test_higher_scoring_candidate_ranks_first():
    strong = _record(build(teaching_years_raw=_f(20.0), publications_count=_f(20), has_phd=_f(True)), "strong.pdf")
    weak = _record(build(teaching_years_raw=_f(1.0), publications_count=_f(0), has_phd=_f(False)), "weak.pdf")
    ranked = rank_candidates([weak, strong])
    assert ranked[0].source_filename == "strong.pdf"
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2


def test_failed_extraction_ranks_last_and_scores_none():
    ok = _record(build(), "ok.pdf")
    failed = ResumeRecord.failed("broken.pdf", "no extractable text")
    ranked = rank_candidates([failed, ok])
    assert ranked[0].source_filename == "ok.pdf"
    assert ranked[-1].source_filename == "broken.pdf"
    assert ranked[-1].score is None
    assert ranked[-1].needs_review is True


def test_ranks_are_contiguous_starting_at_one():
    records = [_record(build(publications_count=_f(n)), f"cv{n}.pdf") for n in (1, 5, 3)]
    ranked = rank_candidates(records)
    assert [c.rank for c in ranked] == [1, 2, 3]


def test_ties_break_deterministically_so_reruns_match():
    a = _record(build(), "b_second.pdf")
    b = _record(build(), "a_first.pdf")
    first = [c.source_filename for c in rank_candidates([a, b])]
    second = [c.source_filename for c in rank_candidates([b, a])]
    assert first == second  # upload order must not change the ranking


# --- review status survives ranking -----------------------------------------

def test_needs_review_is_carried_onto_the_scored_row():
    """A top-ranked candidate built on unverified data must stay flagged --
    that pairing is the whole safeguard against trusting the number."""
    unverified = build(publications_count=_f(20, 0.95, None))  # claimed, unquoted
    ranked = rank_candidates([_record(unverified)])
    assert ranked[0].needs_review is True
    assert ranked[0].review_reasons and "publications_count" in ranked[0].review_reasons


def test_a_high_score_does_not_clear_needs_review():
    strong_but_unverified = build(
        teaching_years_raw=_f(20.0, 0.95, None),
        publications_count=_f(20, 0.95, None),
        has_phd=_f(True),
    )
    ranked = rank_candidates([_record(strong_but_unverified)])
    assert ranked[0].rank == 1
    assert ranked[0].needs_review is True


# --- scoring bounds ---------------------------------------------------------

def test_score_never_exceeds_the_declared_maximum():
    maxed = build(
        teaching_years_raw=_f(99.0),
        publications_count=_f(999),
        marks_pct=_f(100.0),
        has_phd=_f(True),
        net_set_status=_f("NET"),
    )
    score, max_possible, _ = score_result(maxed)
    assert max_possible == MAX_POSSIBLE
    assert score is not None and score <= MAX_POSSIBLE


def test_score_is_never_negative():
    floor = build(teaching_years_raw=_f(-5.0), publications_count=_f(0), marks_pct=_f(0.0))
    score, _, _ = score_result(floor)
    assert score is not None and score >= 0


@pytest.mark.parametrize("status,qualifies", [("NET", True), ("SET", True), ("SLET", True), ("NONE", False)])
def test_net_set_points_only_for_a_real_qualification(status, qualifies):
    base = score_result(build(net_set_status=_f("NONE")))[0]
    got = score_result(build(net_set_status=_f(status)))[0]
    assert (got > base) is qualifies


def test_disclaimer_states_it_is_not_a_hiring_decision():
    """The wording is load-bearing, not decoration: it is what travels into
    the workbook and the UI."""
    assert "not a hiring decision" in RANKING_DISCLAIMER
    assert "needs_review" in RANKING_DISCLAIMER


# --- CGPA scores alongside percentage ---------------------------------------

def test_a_cgpa_only_candidate_is_scored_not_zeroed():
    """The bug this fixes: four real candidates stating 8.2-9.2 CGPA scored 0
    on marks and sank to the bottom, purely because the figure was on the
    grade-point scale rather than a percentage."""
    cgpa_only = build(marks_pct=ABSENT, cgpa=_f(9.13, 0.95, "9.13 CGPA"))
    no_marks = build(marks_pct=ABSENT, cgpa=ABSENT)
    assert score_result(cgpa_only)[0] > score_result(no_marks)[0]


def test_equivalent_marks_on_either_scale_score_about_the_same():
    """9.0/10 and 90% are the same achievement; neither scale should be
    systematically advantaged."""
    pct = score_result(build(marks_pct=_f(90.0), cgpa=ABSENT))[0]
    cgpa = score_result(build(marks_pct=ABSENT, cgpa=_f(9.0)))[0]
    assert pct == pytest.approx(cgpa)


def test_stating_both_scales_does_not_double_count():
    """marks_pct and cgpa describe one degree, so they share one budget."""
    both = score_result(build(marks_pct=_f(76.6), cgpa=_f(8.0)))[0]
    pct_only = score_result(build(marks_pct=_f(76.6), cgpa=ABSENT))[0]
    assert both == pytest.approx(pct_only)


def test_percentage_wins_over_cgpa_rather_than_the_higher_of_the_two():
    """Taking the better of the two rewarded whoever reported the more
    flattering figure -- on real resumes that let a candidate's 85.2% school
    marks outscore their own 7.78 Master's GPA."""
    flattering_cgpa = score_result(build(marks_pct=_f(60.0), cgpa=_f(9.9)))[0]
    pct_only = score_result(build(marks_pct=_f(60.0), cgpa=ABSENT))[0]
    assert flattering_cgpa == pytest.approx(pct_only)


def test_cgpa_is_used_when_no_percentage_was_reported():
    assert score_result(build(marks_pct=ABSENT, cgpa=_f(8.0)))[0] > score_result(
        build(marks_pct=ABSENT, cgpa=ABSENT)
    )[0]


def test_cgpa_only_candidate_counts_as_complete_on_the_marks_component():
    """A resume that states a CGPA has told us its marks; completeness must
    reflect that rather than treating the candidate as unassessed."""
    assert score_result(build(marks_pct=ABSENT, cgpa=_f(8.5)))[2] == 1.0
    assert score_result(build(marks_pct=ABSENT, cgpa=ABSENT))[2] == pytest.approx(4 / 5)
