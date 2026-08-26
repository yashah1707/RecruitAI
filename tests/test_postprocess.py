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
        cgpa=_f(None, 0.0, None),
        has_phd=_f(True, 0.9, "PhD in Mathematics"),
        phd_award_date=_f(date(2020, 1, 1), 0.8, "awarded 2020"),
        phd_regulation=_f(None, 0.0, None),
        masters_award_date=_f(None, 0.0, None),
        net_set_status=_f("NET", 0.95, "UGC-NET June 2015"),
        set_state=_f(None, 0.0, None),
        study_leave_taken=_f(None, 0.0, None),
        teaching_years_raw=_f(5, 0.8, "5 years teaching"),
        publications_count=_f(2, 0.9, "2 publications listed"),
        publication_titles=_f(None, 0.0, None),
        publications_in_progress_count=_f(0, 0.9, None),
        publications_in_progress_titles=_f(None, 0.0, None),
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


def test_a_cgpa_in_marks_pct_is_moved_to_cgpa_not_discarded():
    """Discarding it used to cost real candidates their whole marks score:
    four of twelve test resumes stated a CGPA (9.13, 9.20, 8.79, 8.2) and
    were scored as having no marks at all. The number is real data on the
    wrong scale, so it gets filed correctly rather than thrown away."""
    r = _result(marks_pct=_f(8.2, 0.95, "M.Tech 8.2 CGPA"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["cgpa_moved_from_marks_pct"] == 1
    assert result.cgpa.value == 8.2
    assert result.cgpa.evidence == "M.Tech 8.2 CGPA"
    # ...and it must not also linger as a bogus percentage
    assert result.marks_pct.value is None
    assert result.marks_pct.evidence is None


def test_moved_cgpa_does_not_flag_the_row_for_review():
    """Once the value is filed on the right scale nothing is missing, so
    there is nothing for a human to fix."""
    r = _result(marks_pct=_f(8.2, 0.95, "M.Tech 8.2 CGPA"))
    result, _ = sanitize(r, RESUME_TEXT)
    outcome = evaluate(result)
    assert "marks_pct:cgpa_not_percentage" not in outcome.reasons


def test_an_existing_cgpa_is_not_overwritten_by_a_misfiled_one():
    r = _result(
        marks_pct=_f(8.2, 0.95, "M.Tech 8.2 CGPA"),
        cgpa=_f(9.1, 0.95, "B.E. 9.1 CGPA"),
    )
    result, _ = sanitize(r, RESUME_TEXT)
    assert result.cgpa.value == 9.1
    assert result.marks_pct.value is None


def test_a_genuine_percentage_is_left_in_marks_pct():
    r = _result(marks_pct=_f(76.6, 0.95, "2 publications listed"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["cgpa_moved_from_marks_pct"] == 0
    assert result.marks_pct.value == 76.6
    assert result.cgpa.value is None


def test_stats_carry_no_candidate_data():
    """Stats get logged, so they must be counts only."""
    r = _result(marks_pct=_f(8.2, 0.95, "M.Tech 8.2 CGPA"))
    _, stats = sanitize(r, RESUME_TEXT)
    assert all(isinstance(v, int) for v in stats.values())


# --- marks must come from the highest_degree row (P0) -----------------------

@pytest.mark.parametrize(
    "evidence",
    [
        "Higher Secondary (CBSE) Shrimanta Shankar Academy Graduated: May 2015 Percentage: 85.2%",
        "Intermediate Schooling (CBSE) CGPA: 9.2",
        "B.E. [Computer Engg.] R.C. Patel Institute of Technology, Shirpur 2015 73.60",
        "B.Tech in Computer Science GPA 7.51",
        "S.S.C. Maharashtra Board 2005 78%",
    ],
)
def test_marks_from_a_lower_qualification_row_are_rejected(evidence):
    """Real bug: a candidate's 85.2% *Higher Secondary* mark was extracted as
    her marks_pct while her actual M.Tech result sat in cgpa. Indian CVs list
    every qualification back to 10th standard, each with its own figure, so
    "a percentage found in the qualifications section" is reliably the wrong
    row."""
    r = _result(marks_pct=_f(85.2, 0.9, evidence))
    result, stats = sanitize(r, RESUME_TEXT + " " + evidence)
    assert stats["marks_from_wrong_qualification"] == 1
    assert result.marks_pct.value is None


def test_rejected_row_keeps_its_evidence_for_the_reviewer():
    ev = "Higher Secondary (CBSE) Percentage: 85.2%"
    r = _result(marks_pct=_f(85.2, 0.9, ev))
    result, _ = sanitize(r, RESUME_TEXT + " " + ev)
    assert result.marks_pct.evidence == ev  # shows WHAT was rejected
    outcome = evaluate(result)
    assert "marks_pct:wrong_qualification_row" in outcome.reasons


@pytest.mark.parametrize(
    "evidence",
    [
        "M.Tech in Computer Science and Engineering, IIIT Guwahati, GPA: 7.78",
        "M.E.(CSE/SE) Dr.B.A.M.University, Aurangabad. 2017 61.12%",
        "M.Tech. (Computer) First Class 63.56 2015",
        "Ph.D. [Computer Sci. & Engg.] Sage University 2026",
    ],
)
def test_marks_from_the_masters_or_doctoral_row_are_kept(evidence):
    r = _result(marks_pct=_f(63.56, 0.95, evidence))
    result, stats = sanitize(r, RESUME_TEXT + " " + evidence)
    assert stats["marks_from_wrong_qualification"] == 0
    assert result.marks_pct.value == 63.56


def test_a_row_naming_both_levels_is_not_rejected():
    """Conservative by design: an extracted span that mentions a bachelor's
    in passing while quoting the Master's row should not be thrown away."""
    ev = "M.Tech CSE 2018 8.79 (B.E. Computer Engg. 2015)"
    r = _result(marks_pct=_f(72.0, 0.9, ev))
    result, stats = sanitize(r, RESUME_TEXT + " " + ev)
    assert stats["marks_from_wrong_qualification"] == 0


def test_cgpa_from_a_lower_row_is_rejected_too():
    ev = "Intermediate Schooling (CBSE) CGPA: 9.2"
    r = _result(cgpa=_f(9.2, 0.95, ev))
    result, stats = sanitize(r, RESUME_TEXT + " " + ev)
    assert stats["marks_from_wrong_qualification"] == 1
    assert result.cgpa.value is None


def test_a_resume_stating_no_marks_at_all_is_not_flagged():
    """Absence is not a rejection -- plenty of resumes never state marks, and
    those rows must not be flagged as if something was thrown away."""
    result, stats = sanitize(_result(), RESUME_TEXT)
    assert stats["marks_from_wrong_qualification"] == 0
    outcome = evaluate(result)
    assert not any("wrong_qualification_row" in r for r in outcome.reasons)


# --- negative NET/SET findings must earn their confidence (P1) --------------

def test_a_none_on_a_fully_parsed_resume_keeps_high_confidence():
    r = _result(
        net_set_status=_f("NONE", 0.95, None),
        masters_award_date=_f(date(2015, 1, 1), 0.9, "M.Tech 2015"),
        teaching_years_raw=_f(5, 0.9, "5 years teaching"),
    )
    result, _ = sanitize(r, RESUME_TEXT + " M.Tech 2015")
    assert result.net_set_status.confidence >= 0.8


def test_a_none_on_a_barely_parsed_resume_loses_confidence():
    """The failure this guards: a flat, self-reported 0.95 NONE is
    indistinguishable from "the model didn't look properly". Confidence now
    reflects how much qualification content was actually searchable."""
    # highest_degree/has_phd are non-nullable in the schema, so a barely
    # parsed resume shows up as values with no grounded evidence behind them.
    r = _result(
        net_set_status=_f("NONE", 0.95, None),
        highest_degree=_f("PG", 0.3, None),
        has_phd=_f(False, 0.3, None),
        masters_award_date=_f(None, 0.0, None),
        teaching_years_raw=_f(None, 0.0, None),
    )
    result, stats = sanitize(r, "Rahul Synthetic Joshi. Hobbies: cricket.")
    assert result.net_set_status.confidence == 0.0
    assert stats["net_set_none_tempered"] == 1


def test_negative_findings_are_capped_below_certainty():
    """No quote can prove an absence, so a NONE can never be as well-evidenced
    as a positive finding, however sure the model claims to be."""
    r = _result(
        net_set_status=_f("NONE", 1.0, None),
        masters_award_date=_f(date(2015, 1, 1), 0.9, "M.Tech 2015"),
        teaching_years_raw=_f(5, 0.9, "5 years teaching"),
    )
    result, _ = sanitize(r, RESUME_TEXT + " M.Tech 2015")
    assert result.net_set_status.confidence < 1.0


def test_a_positive_net_set_finding_is_left_alone():
    """Only negatives are tempered -- a real NET/SET credential has a real
    quote and must not be penalised by this rule."""
    r = _result(net_set_status=_f("NET", 0.95, "UGC-NET June 2015"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert result.net_set_status.confidence == 0.95
    assert stats["net_set_none_tempered"] == 0


def test_pet_is_not_net_set_and_stays_a_grounded_none():
    """Regression: Nishant's resume states a Ph.D. Entrance Test (PET), which
    is NOT NET/SET/SLET. This is the one row where a grounded NONE already
    worked -- a fix to the negative path must not break it."""
    ev = "Ph.D. Entrance Test (PET), Sant Gadge Baba Amravati University - Qualified"
    r = _result(
        net_set_status=_f("NONE", 0.95, ev),
        masters_award_date=_f(date(2023, 1, 1), 0.9, "M.Tech 2023"),
        teaching_years_raw=_f(0, 0.95, "begin my academic career"),
    )
    text = RESUME_TEXT + " " + ev + " M.Tech 2023 begin my academic career"
    result, _ = sanitize(r, text)
    assert result.net_set_status.value == "NONE"
    assert result.net_set_status.evidence == ev


# --- duplicate publications (P1) -------------------------------------------

def _titles(*names):
    return {"value": list(names), "confidence": 0.95, "evidence": "Publications"}


def test_same_work_relisted_in_two_sections_is_counted_once():
    """The real shape: a "Publications" list and a separate research table
    carrying the same work with venue/year appended. Each mention has its own
    honest quote, so only comparing titles can catch the double count."""
    r = _result(
        publications_count=_f(4, 0.95, "Publications"),
        publication_titles=_titles(
            "Deep Learning Approaches for Image Forgery Detection, IJSRT, 2021",
            "A Survey on Cloud Resource Allocation Techniques, IJCA, 2022",
            "Deep Learning Approaches for Image Forgery Detection",
            "A Survey on Cloud Resource Allocation Techniques",
        ),
    )
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["duplicate_publications_removed"] == 2
    assert result.publications_count.value == 2


def test_distinct_publications_are_not_collapsed():
    """Understating a real record is worse than leaving a near-duplicate."""
    r = _result(
        publications_count=_f(2, 0.95, "Publications"),
        publication_titles=_titles(
            "Neural Networks for Vision Tasks",
            "Neural Networks for Speech Tasks",
        ),
    )
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["duplicate_publications_removed"] == 0
    assert result.publications_count.value == 2


def test_a_short_title_is_not_swallowed_by_a_longer_unrelated_one():
    r = _result(
        publications_count=_f(2, 0.95, "Publications"),
        publication_titles=_titles(
            "Machine Learning",
            "Machine Learning for Healthcare Applications in Rural India",
        ),
    )
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["duplicate_publications_removed"] == 0


def test_correcting_the_count_lowers_confidence_in_it():
    """Once Python overrides the model's count, the model's confidence in
    that count is no longer the relevant number."""
    r = _result(
        publications_count=_f(2, 0.98, "Publications"),
        publication_titles=_titles("Same Title Here Again", "Same Title Here Again"),
    )
    result, _ = sanitize(r, RESUME_TEXT)
    assert result.publications_count.confidence <= 0.75


def test_no_titles_leaves_the_count_untouched():
    r = _result(publications_count=_f(7, 0.95, "Publications"))
    result, stats = sanitize(r, RESUME_TEXT)
    assert stats["duplicate_publications_removed"] == 0
    assert result.publications_count.value == 7
