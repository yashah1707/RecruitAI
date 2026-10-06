"""Phase 5: the Assessor and the Decision.

The first block is the design document's Section 12: one test per statutory
edge case, named for the provision it exercises. Expected figures (55, 5,
75, 120, 8, 10 ...) are typed here from the gazette, independently of
backend/rules_data.py, so a slip in either place fails instead of agreeing
with itself. No model is called anywhere in this file.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from backend import states
from backend.assessor_service import assess_application, assess_opening, latest_evaluation
from backend.engine import scores
from backend.engine.decision import FAIL, MANUAL_REVIEW, NOT_ELIGIBLE, PASS, RE_CATEGORISED, SHORTLISTED, UNKNOWN, decide
from backend.engine.experience import adjusted_years, service_years
from backend.engine.facts import Bounds, Degree, Facts, Item, Paper, Period, Post, build_facts, parse_period
from backend.engine.rules import load_rules
from backend.models import Application, EvaluationResult
from backend.reader_service import read_application
from backend.rules_seed import seed_rules
from llm.interface import EducationEntry, ExperienceEntry
from llm.providers.fake_provider import FakeProvider
from tests.test_backend_foundation import _application, _clean_result, _scanned_pdf, client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_intake import _opening, _storage  # noqa: F401

TODAY = date(2026, 10, 6)


@pytest.fixture
def session(_plain_session):
    seed_rules(_plain_session)
    _plain_session.commit()
    return _plain_session


def P(text: str) -> Period:
    return parse_period(text)


def _facts(designation="ASSISTANT_PROFESSOR", **kw) -> Facts:
    """A candidate about whom the form answers are known and nothing else is claimed."""
    base = dict(designation=designation, as_of=TODAY, category="General", differently_abled=False,
                study_leave_taken=False, highest_degree="PG", phd_status="NOT_APPLICABLE", net_set_status="NONE",
                publications_count=0)
    base.update(kw)
    return Facts(**base)


def _ugc(session, facts):
    return decide(facts, load_rules(session, "GENERAL"))


def _check(decision, key, rank=0):
    return next(c for c in decision.ranks[rank].checks if c.key == key)


# --- Section 12: the statutory edge cases ------------------------------------


def test_cl_3_10_as_substituted_masters_56_and_net_is_eligible_without_a_phd(session):
    d = _ugc(session, _facts(masters_marks_pct=56, net_set_status="NET"))
    assert (d.outcome, d.eligible_designation) == (SHORTLISTED, "ASSISTANT_PROFESSOR")
    assert d.rule_version == "UGC-2018-AMD2-2023"
    assert "2nd Amendment" in _check(d, "net_set").clause


def test_cl_3_3_a_phd_under_the_2016_regulations_exempts_from_net(session):
    d = _ugc(session, _facts(masters_marks_pct=60, phd_status="COMPLETED", phd_regulation="2016", highest_degree="PhD"))
    assert d.outcome == SHORTLISTED and "exempt" in _check(d, "net_set").detail


def test_cl_3_3_a_maharashtra_set_is_valid_in_maharashtra(session):
    d = _ugc(session, _facts(masters_marks_pct=56, net_set_status="SET", set_state="Maharashtra"))
    assert d.outcome == SHORTLISTED


def test_cl_3_3_a_karnataka_set_is_not_valid_here(session):
    d = _ugc(session, _facts(masters_marks_pct=56, net_set_status="SET", set_state="Karnataka"))
    assert d.outcome == NOT_ELIGIBLE and d.eligible_designation is None
    assert d.failing.key == "net_set" and "Karnataka" in d.failing.detail and "cl. 3.3" in d.failing.clause


@pytest.mark.parametrize("category", ["SC", "ST", "OBC-NCL", "PwD"])
def test_cl_3_4_reserved_categories_get_five_percent_at_51(session, category):
    d = _ugc(session, _facts(masters_marks_pct=51, net_set_status="NET", category=category))
    marks = _check(d, "marks")
    assert d.outcome == SHORTLISTED and "51% against 50%" in marks.detail and "cl. 3.4" in marks.clause


def test_cl_3_4_a_general_or_ews_candidate_at_51_gets_no_relaxation(session):
    for category in ("General", "EWS"):
        d = _ugc(session, _facts(masters_marks_pct=51, net_set_status="NET", category=category))
        assert d.outcome == NOT_ELIGIBLE and "51% against 55%" in d.failing.detail


def test_cl_3_4_a_differently_abled_candidate_gets_it_whatever_the_category(session):
    d = _ugc(session, _facts(masters_marks_pct=51, net_set_status="NET", differently_abled=True))
    assert d.outcome == SHORTLISTED


def test_cl_3_5_a_phd_holder_with_a_masters_before_19_september_1991_needs_50(session):
    f = _facts(masters_marks_pct=52, net_set_status="NET", phd_status="COMPLETED", highest_degree="PhD", masters_awarded=P("1989"))
    d = _ugc(session, f)
    assert d.outcome == SHORTLISTED and "cl. 3.5" in _check(d, "marks").clause
    f.masters_awarded = P("1991-09-19")  # on the day is not "prior to"
    assert _ugc(session, f).outcome == NOT_ELIGIBLE
    f.masters_awarded = P("1991")  # the year alone cannot settle it
    assert _ugc(session, f).outcome == MANUAL_REVIEW
    f.masters_awarded, f.phd_status = P("1989"), "PURSUING"  # the relaxation is for Ph.D. holders
    assert _ugc(session, f).outcome == NOT_ELIGIBLE


def _professor_applicant(**kw):
    base = dict(masters_marks_pct=60, phd_status="COMPLETED", phd_regulation="2016", highest_degree="PhD",
                publications_count=10, experience_years=Bounds.exactly(10), research_score=Bounds.exactly(92),
                items=[Item(kind="GUIDANCE_PHD", status="Guided 1 Ph.D. scholar, degree awarded", count=1)])
    base.update(kw)
    return _facts("PROFESSOR", **base)


def test_cl_4_1_a_professor_applicant_with_research_score_92_is_recategorised_to_associate(session):
    d = _ugc(session, _professor_applicant())
    assert (d.outcome, d.eligible_designation) == (RE_CATEGORISED, "ASSOCIATE_PROFESSOR")
    assert d.failing.key == "research_score" and "Research Score 92 against threshold 120" in d.failing.detail
    assert [r.designation for r in d.ranks] == ["PROFESSOR", "ASSOCIATE_PROFESSOR"]
    assert _ugc(session, _professor_applicant(research_score=Bounds.exactly(120))).outcome == SHORTLISTED


def _associate_applicant(leave):
    return _facts(
        "ASSOCIATE_PROFESSOR", masters_marks_pct=60, phd_status="COMPLETED", phd_regulation="2016", highest_degree="PhD",
        publications_count=7, research_score=Bounds.exactly(80), study_leave_taken=leave,
        posts=[Post(kind="TEACHING", designation="Assistant Professor", start=P("2015-07-01"), end=P("2024-07-01"))],
        degrees=[Degree(level="PhD", registered=P("2018-07-01"), completed=P("2021-07-01"))],
    )


def test_cl_3_11_part_1_a_phd_taken_on_study_leave_is_deducted_nine_years_become_six(session):
    d = _ugc(session, _associate_applicant(leave=True))
    experience = _check(d, "experience")
    assert experience.result == FAIL and experience.detail.startswith("6 years against 8")
    assert "cl. 3.11" in experience.clause and d.experience_years.exact == 6
    # Not an Associate Professor; the walk down finds the rank they do meet.
    assert (d.outcome, d.eligible_designation) == (RE_CATEGORISED, "ASSISTANT_PROFESSOR")


def test_cl_3_11_part_2_a_phd_done_while_teaching_without_leave_counts_nine_years_stand(session):
    d = _ugc(session, _associate_applicant(leave=False))
    assert d.outcome == SHORTLISTED and _check(d, "experience").detail.startswith("9 years against 8")


def test_cl_3_11_study_leave_not_known_is_worked_out_both_ways_and_sent_to_a_person_when_it_matters(session):
    d = _ugc(session, _associate_applicant(leave=None))
    assert d.outcome == MANUAL_REVIEW and _check(d, "experience").result == UNKNOWN
    assert "between 6 and 9" in d.open_points[0] and "both ways" in d.open_points[0]


@pytest.mark.parametrize("school, regulator", [("SCH-007", "BCI"), ("SCH-019", "DGS"), ("SCH-006", "COA")])
def test_cl_1_1_another_regulators_post_goes_to_manual_review_and_no_ugc_threshold_is_applied(session, tmp_path, school, regulator):
    a = _application(session, tmp_path, category="General", differently_abled=False)
    a.school_id = school
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    d = assess_application(session, a, TODAY)
    assert d.outcome == MANUAL_REVIEW and d.ranks == [] and regulator in d.open_points[0]
    assert a.status == states.MANUAL_REVIEW and a.transitions[-1].note == "cl. 1.1, p. 57"


def test_a_corrupted_upload_is_failed_and_nothing_is_evaluated(session, tmp_path):
    a = _application(session, tmp_path, data=_scanned_pdf(), filename="scan.pdf")
    read_application(session, a, FakeProvider())
    assert a.status == states.FAILED
    with pytest.raises(states.IllegalTransition):
        assess_application(session, a, TODAY)
    assert session.scalars(select(EvaluationResult)).all() == []


# --- what is not known is never rounded either way ---------------------------


@pytest.mark.parametrize(
    "change, names",
    [({"category": None, "masters_marks_pct": 52}, "category"),
     ({"masters_marks_pct": None, "masters_cgpa": 8.4}, "CGPA"),
     ({"masters_marks_pct": None}, "not stated"),
     ({"net_set_status": "SET", "set_state": None}, "State is not known"),
     ({"net_set_status": "NONE", "phd_status": "COMPLETED", "phd_regulation": None}, "certificate")],
)
def test_an_open_point_sends_the_application_to_a_person_and_names_it(session, change, names):
    d = _ugc(session, _facts(**{"masters_marks_pct": 60, "net_set_status": "NET", **change}))
    assert d.outcome == MANUAL_REVIEW and d.eligible_designation is None
    assert any(names in point for point in d.open_points)


def test_an_unknown_does_not_hide_a_requirement_that_plainly_fails(session):
    d = _ugc(session, _facts(masters_marks_pct=40, net_set_status="SET", set_state=None))
    assert d.outcome == NOT_ELIGIBLE and d.failing.key == "marks"


def test_an_unknown_category_does_not_matter_when_the_marks_clear_55_anyway(session):
    assert _ugc(session, _facts(masters_marks_pct=61, net_set_status="NET", category=None)).outcome == SHORTLISTED


def test_a_phd_holder_with_a_set_from_elsewhere_is_still_exempt(session):
    f = _facts(masters_marks_pct=60, net_set_status="SET", set_state="Karnataka", phd_status="COMPLETED", phd_regulation="2009")
    assert _ugc(session, f).outcome == SHORTLISTED


def test_a_senior_professor_is_never_cleared_by_the_engine_alone(session):
    f = _professor_applicant(research_score=Bounds.exactly(200))
    f.designation = "SENIOR_PROFESSOR"
    f.posts = [Post(kind="TEACHING", designation="Professor", start=P("2010-01-01"), end=P("2024-01-01"))]
    f.items = [Item(kind="GUIDANCE_PHD", status="2 Ph.D. degrees awarded", count=2)]
    d = _ugc(session, f)
    assert d.outcome == MANUAL_REVIEW and "three eminent experts" in d.open_points[-1]
    f.posts = [Post(kind="TEACHING", designation="Associate Professor", start=P("2010-01-01"), end=P("2024-01-01"))]
    assert _ugc(session, f).eligible_designation == "PROFESSOR"  # no years as Professor: fails, then walks down


# --- AICTE (Degree) Regulation, 2019 ----------------------------------------


def _engineer(designation="ASSISTANT_PROFESSOR", **kw):
    base = dict(discipline_group="ENGINEERING_TECHNOLOGY", degrees=[
        Degree(level="UG", name="B.E.", cgpa=6.2), Degree(level="PG", name="M.Tech", cgpa=9.13)])
    base.update(kw)
    return _facts(designation, **base)


def _aicte(session, facts):
    return decide(facts, load_rules(session, facts.discipline_group))


def test_aicte_5_1_a_an_engineer_needs_first_class_in_one_degree_and_no_net(session):
    d = _aicte(session, _engineer())
    assert d.outcome == SHORTLISTED and d.rule_version == "AICTE-DEGREE-2019"
    first = _check(d, "first_class")
    assert "AICTE cl. 5.1(a)" in first.clause and "p. 39" in first.page and "9.13 against 6.75" in first.detail
    assert not [c for c in d.ranks[0].checks if c.key in ("net_set", "marks")]
    assert d.research_score is None and d.shortlist_score is None  # AICTE prescribes neither
    assert "relevant branch" in d.notes[0]


def test_aicte_5_1_a_second_class_in_both_degrees_is_not_eligible(session):
    degrees = [Degree(level="UG", name="B.E.", marks_pct=58), Degree(level="PG", name="M.E.", division="Second Class")]
    d = _aicte(session, _engineer(degrees=degrees))
    assert d.outcome == NOT_ELIGIBLE and d.failing.key == "first_class"


@pytest.mark.parametrize("ug, pg, expected", [
    (Degree("UG", marks_pct=60), Degree("PG", marks_pct=40), SHORTLISTED),       # 60% is First Class (cl. 7.3)
    (Degree("UG", marks_pct=59.9), Degree("PG", cgpa=6.74), NOT_ELIGIBLE),
    (Degree("UG", marks_pct=59.9), Degree("PG", cgpa=6.75), SHORTLISTED),
    (Degree("UG", marks_pct=50), Degree("PG"), MANUAL_REVIEW),                    # class not stated
    (Degree("UG", marks_pct=50), Degree("PG", cgpa=3.6), MANUAL_REVIEW),          # not a ten-point scale
    (Degree("UG", division="First Class with Distinction"), Degree("PG", marks_pct=40), SHORTLISTED),
])
def test_aicte_7_3_first_class_is_read_from_the_class_the_marks_or_the_grade_point(session, ug, pg, expected):
    assert _aicte(session, _engineer(degrees=[ug, pg])).outcome == expected


def test_aicte_a_bachelors_alone_does_not_meet_5_1_a(session):
    d = _aicte(session, _engineer(highest_degree="UG", degrees=[Degree("UG", name="B.E.", marks_pct=75)]))
    assert d.outcome == NOT_ELIGIBLE and d.failing.key == "holds_pg"


def _technical_senior(designation, **kw):
    base = dict(phd_status="COMPLETED", highest_degree="PhD", publications_count=6,
                degrees=[Degree("UG", marks_pct=65), Degree("PG", marks_pct=62), Degree("PhD", completed=P("2019-06-30"))],
                posts=[Post(kind="INDUSTRY", designation="Engineer", start=P("2012-01-01"), end=P("2016-01-01")),
                       Post(kind="TEACHING", designation="Assistant Professor", start=P("2016-01-01"), is_current=True)])
    base.update(kw)
    return _engineer(designation, **base)


def test_aicte_5_2_c_associate_professor_counts_industry_years_and_needs_two_after_the_phd(session):
    d = _aicte(session, _technical_senior("ASSOCIATE_PROFESSOR"))
    assert d.outcome == SHORTLISTED
    experience = _check(d, "experience")
    assert experience.detail.startswith("14.76 years against 8") and experience.label == "Experience (teaching, research and industry)"
    # UGC cl. 3.11 is not an AICTE rule: nothing is deducted for the Ph.D., and AICTE's own clause is cited.
    assert "3.11" not in experience.clause and "AICTE cl. 2.25" in experience.clause and "p. 31" in experience.page
    on_leave = _technical_senior("ASSOCIATE_PROFESSOR", study_leave_taken=True)
    on_leave.degrees[2].registered = P("2016-06-30")
    assert _check(_aicte(session, on_leave), "experience").detail.startswith("14.76 years")
    assert _check(d, "post_phd").result == PASS
    late = _technical_senior("ASSOCIATE_PROFESSOR")
    late.degrees[2].completed = P("2025-06-30")
    d = _aicte(session, late)
    assert d.failing.key == "post_phd" and (d.outcome, d.eligible_designation) == (RE_CATEGORISED, "ASSISTANT_PROFESSOR")
    assert _aicte(session, _technical_senior("ASSOCIATE_PROFESSOR", publications_count=5)).failing.key == "publications"
    no_row = _technical_senior("ASSOCIATE_PROFESSOR", phd_awarded=P("2019"))
    no_row.degrees = no_row.degrees[:2]  # no Ph.D. row: the award date read as a field is used
    assert _check(_aicte(session, no_row), "post_phd").result == PASS


def test_aicte_5_2_d_professor_needs_three_years_at_associate_level_and_papers_from_then(session):
    f = _technical_senior("PROFESSOR", posts=[
        Post(kind="TEACHING", designation="Assistant Professor", start=P("2010-01-01"), end=P("2018-01-01")),
        Post(kind="TEACHING", designation="Associate Professor", start=P("2018-01-01"), is_current=True)],
        papers=[Paper(kind="JOURNAL", year=2019 + i % 6) for i in range(10)])
    d = _aicte(session, f)
    assert d.outcome == SHORTLISTED and _check(d, "publications_at_level").result == PASS
    f.papers = f.papers[:7]  # seven papers and no Ph.D. guided meets neither route
    d = _aicte(session, f)
    assert d.failing.key == "publications_at_level" and d.eligible_designation == "ASSOCIATE_PROFESSOR"
    f.items = [Item(kind="GUIDANCE_PHD", status="2 Ph.D. scholars awarded", count=2)]  # six papers and two guided does
    assert _aicte(session, f).outcome == SHORTLISTED


def test_aicte_has_no_senior_professor_rule_and_says_so_instead_of_failing_the_candidate(session):
    d = _aicte(session, _technical_senior("SENIOR_PROFESSOR"))
    assert d.outcome == MANUAL_REVIEW and "no direct-recruitment rule for Senior Professor" in d.open_points[0]
    assert d.ranks == [] and d.failing is None  # nothing was held against the candidate


@pytest.mark.parametrize("group, clause", [("UGC_4_3_DRAMA", "cl. 4.3, p. 63"), ("UGC_4_2_PERFORMING_VISUAL_ARTS", "cl. 4.2, p. 61"),
                                           ("UGC_4_4_YOGA", "cl. 4.4, p. 65")])
def test_ugc_disciplines_with_their_own_clause_are_not_assessed_under_cl_4_1(session, group, clause):
    f = _facts(discipline_group=group, masters_marks_pct=40)  # would fail cl. 4.1; that clause does not govern
    d = decide(f, load_rules(session, group))
    assert d.outcome == MANUAL_REVIEW and d.ranks == [] and clause in d.open_points[0] and "cl. 4.1 was not applied" in d.open_points[0]


def test_the_opening_form_suggests_the_right_ugc_clause_for_drama_and_the_arts(session):
    from backend import intake
    from backend.models import School

    assert intake.suggested_discipline_group(session.get(School, "SCH-004")) == "UGC_4_3_DRAMA"
    assert intake.suggested_discipline_group(session.get(School, "SCH-002")) == "UGC_4_2_PERFORMING_VISUAL_ARTS"
    assert intake.suggested_discipline_group(session.get(School, "SCH-001")) == "UGC_4_2_PERFORMING_VISUAL_ARTS"
    assert intake.suggested_discipline_group(session.get(School, "SCH-003")) == "GENERAL"


def test_an_application_with_no_opening_has_no_rule_set_and_is_not_assessed_under_a_guess(session, tmp_path):
    a = _extracted(session, tmp_path)
    d = assess_application(session, a, TODAY)
    assert d.outcome == MANUAL_REVIEW and "not attached to an opening" in d.open_points[0] and d.ranks == []


def test_aicte_disciplines_with_requirements_a_resume_cannot_show_go_to_a_person(session):
    f = _engineer(discipline_group="MANAGEMENT", degrees=[Degree("UG", marks_pct=70), Degree("PG", name="MBA", marks_pct=66)])
    d = _aicte(session, f)
    assert d.outcome == MANUAL_REVIEW and "two years of professional experience" in d.open_points[0]
    f.degrees[1].marks_pct = 55  # but a plain failure is still a failure
    assert _aicte(session, f).outcome == NOT_ELIGIBLE


def test_aicte_5_1_j_science_and_humanities_faculty_are_assessed_under_the_ugc_rule(session):
    d = _aicte(session, _engineer(discipline_group="SCIENCE_HUMANITIES", masters_marks_pct=56, net_set_status="NET"))
    assert d.outcome == SHORTLISTED and d.rule_version == "UGC-2018-AMD2-2023" and "5.1(j)" in d.notes[0]


# --- experience --------------------------------------------------------------


def test_a_post_dated_by_year_alone_gives_a_range_not_a_guess():
    f = _facts(posts=[Post(kind="TEACHING", start=P("2014"), end=P("2019"))])
    years = service_years(f, ("TEACHING",))
    assert years.low == pytest.approx(4.0, abs=0.01) and years.high == pytest.approx(6.0, abs=0.01)


def test_overlapping_posts_are_counted_once_and_a_current_post_runs_to_the_day_of_assessment():
    f = _facts(posts=[Post(kind="TEACHING", start=P("2020-01-01"), end=P("2022-01-01")),
                      Post(kind="TEACHING", start=P("2021-01-01"), is_current=True),
                      Post(kind="INDUSTRY", start=P("2010-01-01"), end=P("2015-01-01"))])
    assert service_years(f, ("TEACHING", "RESEARCH")).exact == pytest.approx(6.76, abs=0.01)
    assert service_years(f, ("TEACHING", "INDUSTRY")).exact == pytest.approx(11.76, abs=0.01)


def test_an_undated_post_leaves_the_upper_limit_open_unless_the_resume_states_a_total():
    f = _facts(posts=[Post(kind="TEACHING", start=P("2020-01-01"), end=P("2022-01-01")), Post(kind="TEACHING")])
    assert service_years(f, ("TEACHING",)) == Bounds(2.0, None)
    f.stated_teaching_years = 9.0
    assert service_years(f, ("TEACHING",)) == Bounds(2.0, 9.0)
    f.posts = [Post(kind="TEACHING")]
    assert service_years(f, ("TEACHING",)) == Bounds.exactly(9.0)


def test_cl_3_11_years_as_a_research_scholar_are_the_degree_not_experience():
    """ "The time taken by candidates to acquire M.Phil. and/or Ph.D. Degree shall not be considered as
    teaching/research experience". Only teaching alongside it, without leave, is saved by the second limb."""
    f = _facts("ASSOCIATE_PROFESSOR", study_leave_taken=False, phd_status="COMPLETED",
               degrees=[Degree(level="PhD", registered=P("2016-07-01"), completed=P("2020-07-01"))],
               posts=[Post(kind="RESEARCH", designation="Ph.D. Research Scholar", start=P("2016-07-01"), end=P("2020-07-01")),
                      Post(kind="TEACHING", designation="Assistant Professor", start=P("2020-07-01"), end=P("2025-07-01"))])
    years, how = adjusted_years(f, ("TEACHING", "RESEARCH"))
    assert years.exact == pytest.approx(5.0, abs=0.01) and "research posts held during the degree are not counted" in how
    f.posts[0] = Post(kind="RESEARCH", designation="Project Scientist", start=P("2012-07-01"), end=P("2016-07-01"))
    assert adjusted_years(f, ("TEACHING", "RESEARCH"))[0].exact == pytest.approx(9.0, abs=0.01)  # before the degree: counts
    f.degrees[0].registered = None  # dates of the degree unknown: the research post may or may not fall inside it
    years, _ = adjusted_years(f, ("TEACHING", "RESEARCH"))
    assert years.low == pytest.approx(5.0, abs=0.01) and years.high == pytest.approx(9.0, abs=0.01)


def test_no_research_degree_means_nothing_is_deducted_whatever_the_leave_answer():
    f = _facts(study_leave_taken=None, posts=[Post(kind="TEACHING", start=P("2015-01-01"), end=P("2020-01-01"))])
    years, how = adjusted_years(f, ("TEACHING",))
    assert years.exact == pytest.approx(5.0, abs=0.01) and "nothing is deducted" in how


def test_study_leave_with_undated_research_degree_leaves_the_lower_bound_at_zero():
    f = _associate_applicant(leave=True)
    f.degrees = [Degree(level="PhD")]
    years, how = adjusted_years(f, ("TEACHING", "RESEARCH"))
    assert years.low == 0 and years.high == pytest.approx(9.0, abs=0.01) and "dates of the research degree" in how


@pytest.mark.parametrize("text, earliest, latest", [
    ("2014", date(2014, 1, 1), date(2014, 12, 31)), ("2020-02", date(2020, 2, 1), date(2020, 2, 29)),
    ("2021-05-17", date(2021, 5, 17), date(2021, 5, 17))])
def test_a_partly_stated_date_is_the_span_of_days_it_can_mean(text, earliest, latest):
    assert parse_period(text) == Period(earliest, latest)
    assert parse_period("May 2021") is None and parse_period("2021-13") is None and parse_period(None) is None


# --- Appendix II scores ------------------------------------------------------


def test_table_2_research_score_spans_the_two_readings_and_what_the_resume_leaves_unsaid(session):
    f = _facts("ASSOCIATE_PROFESSOR",
               papers=[Paper(kind="JOURNAL", author_count=1), Paper(kind="JOURNAL", author_count=1),
                       Paper(kind="JOURNAL", author_count=1, cleared_review=False)],
               items=[Item(kind="GUIDANCE_PHD", status="1 Ph.D. awarded", count=1),
                      Item(kind="PATENT", level="NATIONAL", status="Granted"), Item(kind="TALK", level="NATIONAL")])
    s = scores.research_score(f, load_rules(session, "GENERAL"))
    # Papers: 5 each with no impact factor if that replaces the base; 10 + 5 each if it adds to it.
    assert (s.lines[0].low, s.lines[0].high) == (10, 30)
    # A Ph.D. awarded is 10; a co-supervisor gets 70% of it. National patent 7. National talk 3.
    assert (s.lines[3].low, s.lines[3].high) == (7, 10) and (s.lines[4].low, s.lines[4].high) == (7, 7)
    assert s.total == Bounds(27, 50) and s.categories.low == 4


def test_table_2_impact_factor_and_authorship_shares(session):
    rules = load_rules(session, "GENERAL")
    one = lambda **kw: scores.research_score(_facts(papers=[Paper(kind="JOURNAL", **kw)]), rules).lines[0]  # noqa: E731
    assert (one(author_count=1, impact_factor=3.2).low, one(author_count=1, impact_factor=3.2).high) == (20, 30)
    assert (one(author_count=1, impact_factor=12).low, one(author_count=1, impact_factor=0.4).low) == (30, 10)
    assert one(author_count=2).low == pytest.approx(3.5) and one(author_count=2).high == pytest.approx(10.5)   # 70% each
    assert one(author_count=4, is_first_author=True).low == pytest.approx(3.5)                                  # first: 70%
    assert one(author_count=4, is_first_author=False).low == pytest.approx(1.5)                                 # joint: 30%
    assert (one().low, one().high) == (pytest.approx(1.5), 15)                                                  # authors not listed


def test_table_2_talks_are_capped_at_thirty_percent_of_the_total(session):
    rules = load_rules(session, "GENERAL")
    talks = [Item(kind="TALK", level="NATIONAL") for _ in range(20)]  # 60 points uncapped
    assert scores.research_score(_facts(items=talks), rules).total == Bounds(0, 0)
    s = scores.research_score(_facts(items=talks, papers=[Paper(kind="BOOK_CHAPTER", author_count=1) for _ in range(7)]), rules)
    assert s.total.low == pytest.approx(35 + 15) and s.total.low * 0.3 == pytest.approx(15)


def test_the_research_score_check_passes_fails_or_is_left_open_by_its_bounds(session):
    papers = [Paper(kind="JOURNAL", author_count=1) for _ in range(16)]  # between 80 and 240
    f = _associate_applicant(leave=False)
    f.research_score, f.papers = None, papers
    # A Ph.D. guided (7 to 10) and a national patent (7): with the papers, three of the six categories.
    f.items = [Item(kind="GUIDANCE_PHD", status="1 Ph.D. awarded", count=1), Item(kind="PATENT", level="NATIONAL", status="Granted")]
    d = _ugc(session, f)
    assert d.outcome == SHORTLISTED and _check(d, "research_score").result == PASS   # 94 clears 75 on either reading
    assert _check(d, "research_score_categories").result == PASS
    f.papers = papers[:10]                                                  # between 64 and 167: depends on the reading
    d = _ugc(session, f)
    assert d.outcome == MANUAL_REVIEW and "between 64 and 167" in d.open_points[0]
    f.papers = papers[:3]                                                   # at most 62
    assert _check(_ugc(session, f), "research_score").result == FAIL


def test_table_2_a_score_from_papers_alone_does_not_meet_the_three_categories_rule(session):
    """ "The research score shall be from the minimum of three categories out of six" (p. 107)."""
    f = _associate_applicant(leave=False)
    f.research_score, f.papers = None, [Paper(kind="JOURNAL", author_count=1) for _ in range(30)]
    d = _ugc(session, f)
    spread = _check(d, "research_score_categories")
    assert _check(d, "research_score").result == PASS and spread.result == FAIL and "p. 107" in spread.page
    assert (d.outcome, d.eligible_designation) == (RE_CATEGORISED, "ASSISTANT_PROFESSOR")
    assert d.shortlist_score is not None  # they will be short-listed as an Assistant Professor candidate
    f.items = [Item(kind="PATENT", level="NATIONAL", status="Granted")]  # two sure categories; the unread third may exist
    assert _check(_ugc(session, f), "research_score_categories").result == UNKNOWN


def test_table_3a_short_listing_score(session):
    f = _facts(masters_marks_pct=65, net_set_status="NET", publications_count=3, experience_years=Bounds.exactly(2),
               degrees=[Degree("UG", name="B.Sc.", marks_pct=72), Degree("PG", name="M.Sc.", marks_pct=65)])
    d = _ugc(session, f)
    lines = {l["label"][:2]: (l["low"], l["high"]) for l in d.shortlist_score["lines"]}
    # Graduation 60-80%: 13. Post-Graduation 60-80%: 23. NET 5 (7 with JRF, which is not read).
    # Publications 2 each. Teaching 2 a year.
    assert lines == {"1.": (13, 13), "2.": (23, 23), "3-": (0, 0), "5.": (5, 7), "6.": (6, 6), "7.": (4, 4), "8.": (0, 0)}
    assert (d.shortlist_score["low"], d.shortlist_score["high"]) == (51, 53)
    assert "interview" in d.shortlist_score["notes"][0]


def test_table_3a_caps_bands_and_open_points(session):
    rules = load_rules(session, "GENERAL")
    f = _facts(net_set_status="SET", set_state="Karnataka", publications_count=40, experience_years=Bounds.exactly(30),
               phd_status="COMPLETED", category="SC",
               degrees=[Degree("UG", cgpa=8.1), Degree("PG", name="M.Tech", marks_pct=52)],
               items=[Item(kind="AWARD", level="NATIONAL"), Item(kind="AWARD", level="STATE")])
    lines = {l.label[:2]: (l.low, l.high) for l in scores.shortlist_score(f, rules).lines}
    assert lines["1."] == (0, 15)      # a CGPA cannot be banded
    assert lines["2."] == (0, 20)      # 52% scores 20 for a reserved category under S.No. 2, nothing under S.No. 3
    assert lines["3-"] == (30, 30)     # Ph.D. 30, and the M.Phil. + Ph.D. cap is 30
    assert lines["5."] == (0, 0)       # a SET counts only in its own State (Note D)
    assert lines["6."] == (10, 10) and lines["7."] == (10, 10) and lines["8."] == (3, 3)
    # A research post is post-doctoral experience only if it came after the Ph.D., which is not on the record.
    g = _facts(posts=[Post(kind="TEACHING", start=P("2020-01-01"), end=P("2022-01-01")),
                      Post(kind="RESEARCH", start=P("2022-01-01"), end=P("2024-01-01"))])
    line = scores.shortlist_score(g, rules).lines[5]
    assert (line.low, line.high) == (pytest.approx(4.0, abs=0.01), pytest.approx(8.0, abs=0.01))


# --- the workflow step -------------------------------------------------------


def _extracted(session, tmp_path, opening=None, result=None, **fields) -> Application:
    fields = {"category": "General", "differently_abled": False, "study_leave_taken": False, **fields}
    a = _application(session, tmp_path, **fields)
    if opening is not None:
        a.opening_id, a.school_id, a.applied_designation = opening.opening_id, opening.school_id, opening.designation
    read_application(session, a, FakeProvider(script=[result or _clean_result()]))
    session.commit()
    assert a.status == states.EXTRACTED
    return a


def test_an_assessment_is_stored_with_its_working_and_the_state_follows_the_outcome(session, tmp_path):
    o = _opening(session, school_id="SCH-013", discipline_group="GENERAL")
    a = _extracted(session, tmp_path, o)  # M.Sc. 68.4%, NET
    d = assess_application(session, a, TODAY)
    session.commit()
    assert d.outcome == SHORTLISTED and a.status == states.SHORTLISTED
    assert [t.to_state for t in a.transitions][-2:] == ["ASSESSED", "SHORTLISTED"]
    assert a.transitions[-2].actor == "agent:assessor" and a.transitions[-2].note == "rules: UGC-2018-AMD2-2023"
    e = latest_evaluation(session, a.application_id)
    assert (e.outcome, e.eligible_designation, e.was_recategorised, e.failing_clause) == ("SHORTLISTED", "ASSISTANT_PROFESSOR", False, None)
    checks = {c["key"]: c for c in e.details["ranks"][0]["checks"]}
    assert checks["marks"]["result"] == "PASS" and checks["marks"]["page"] and "68.4% against 55%" in checks["marks"]["detail"]
    assert e.details["shortlist_score"]["table"] == "TABLE_3A" and e.details["as_of"] == "2026-10-06"


def test_the_audit_trail_cites_the_clause_and_never_the_candidates_figures(session, tmp_path):
    r = _clean_result()
    r.net_set_status.value = "NONE"
    o = _opening(session, school_id="SCH-013", discipline_group="GENERAL")
    a = _extracted(session, tmp_path, o, r)
    assess_application(session, a, TODAY)
    assert a.status == states.NOT_ELIGIBLE
    note = a.transitions[-1].note
    assert "cl. 3.3" in note and "68.4" not in note and "Maharashtra" not in note
    e = latest_evaluation(session, a.application_id)
    assert "no NET" in e.failing_clause and e.failing_clause_page


def test_the_facts_come_from_the_record_as_checked_at_gate_1(session, tmp_path):
    r = _clean_result()
    r.education = [EducationEntry(level="UG", degree="B.E.", completion="2013", cgpa=6.1),
                   EducationEntry(level="PG", degree="M.Tech", completion="2015-06", cgpa=8.2)]
    r.experience = [ExperienceEntry(designation="Assistant Professor", institution="Placeholder College",
                                    kind="TEACHING", start="2018", end="2024")]
    o = _opening(session)  # School of Computing, AICTE engineering
    a = _extracted(session, tmp_path, o, r)
    f = build_facts(session, a, TODAY)
    assert (f.discipline_group, f.regulator_implemented, f.category) == ("ENGINEERING_TECHNOLOGY", True, "General")
    assert [(d.level, d.cgpa) for d in f.degrees] == [("UG", 6.1), ("PG", 8.2)]
    assert f.posts[0].start == P("2018") and f.posts[0].end == P("2024")
    assert assess_application(session, a, TODAY).outcome == SHORTLISTED


def test_assessing_an_opening_takes_only_its_read_applications_and_each_only_once(session, tmp_path):
    o = _opening(session)
    a = _extracted(session, tmp_path, o)
    waiting = _application(session, tmp_path)
    waiting.opening_id = o.opening_id
    session.commit()
    assert assess_opening(session, o.opening_id, TODAY) == {SHORTLISTED: 1}  # B.Sc. 71% and M.Sc. 68.4%: First Class
    assert assess_opening(session, o.opening_id, TODAY) == {}
    assert waiting.status == states.RECEIVED and len(session.scalars(select(EvaluationResult)).all()) == 1
    with pytest.raises(states.IllegalTransition):
        assess_application(session, a, TODAY)


# --- the pages ---------------------------------------------------------------


def test_the_opening_page_assesses_and_the_application_page_shows_the_working(client, engine, tmp_path):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        seed_rules(s)
        o = _opening(s, school_id="SCH-013", discipline_group="GENERAL")
        a = _extracted(s, tmp_path, o)
        opening_id, app_id = o.opening_id, a.application_id
    assert "Assess 1 read application(s)" in client.get(f"/hr/openings/{opening_id}").text
    done = client.post(f"/hr/openings/{opening_id}/assess")
    assert "Assessed 1 application(s): 1 meet the post applied for." in done.text
    assert "Assess 1 read" not in done.text
    page = client.get(f"/hr/applications/{app_id}").text
    assert "Meets the minimum qualifications for Assistant Professor." in page
    assert "68.4% against 55%" in page and "2nd Amd. p. 2" in page and "Short-listing score (Table 3A)" in page
    assert "HR approves or overrides every outcome" in page
    again = client.post(f"/hr/openings/{opening_id}/assess")
    assert "No read application was waiting to be assessed." in again.text
    assert client.post("/hr/openings/999999/assess").status_code == 404
