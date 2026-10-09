"""The date eligibility is counted on, reading a resume again, and correcting the posts held.

Fake provider, made-up data, no network. SQLite by default; PostgreSQL when TEST_DATABASE_URL is set.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.result_cache import ResultCache
from backend import gate1, gate2, intake, jobs, states
from backend.assessor_service import _details, assess_application, latest_evaluation
from backend.engine.decision import FAIL, MANUAL_REVIEW, PASS, SHORTLISTED, UNKNOWN, decide
from backend.engine.experience import service_years
from backend.engine.facts import Facts, Post, counting_date, parse_period as P
from backend.engine.rules import load_rules
from backend.models import CandidateExperience, Job, JobOpening
from backend.reader_service import read_application
from backend.rules_seed import seed_rules
from llm.interface import ExperienceEntry
from llm.providers.fake_provider import FakeProvider
from tests.test_backend_foundation import _application, _clean_result, client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_gate2 import _assessed, _assessed_on_server, _no_net, _other_person
from tests.test_backend_intake import _opening, _storage  # noqa: F401

TODAY = date(2026, 10, 6)  # the day tests/test_backend_gate2.py assesses on
CUT = date(2026, 9, 30)
LATE = date(2026, 10, 5)  # an application that arrived after the date


@pytest.fixture
def session(_plain_session):
    seed_rules(_plain_session)
    _plain_session.commit()
    return _plain_session


def _facts(designation="ASSISTANT_PROFESSOR", **kw) -> Facts:
    base = dict(designation=designation, as_of=CUT, cut_off=True, received_on=LATE, category="General",
                differently_abled=False, study_leave_taken=False, highest_degree="PG", phd_status="NOT_APPLICABLE",
                net_set_status="NET", masters_marks_pct=60, publications_count=0)
    base.update(kw)
    return Facts(**base)


def _check(decision, key, rank=0):
    return next(c for c in decision.ranks[rank].checks if c.key == key)


def _ugc(session, facts):
    return decide(facts, load_rules(session, "GENERAL"))


# --- which date --------------------------------------------------------------


def test_the_closing_date_is_the_date_once_it_has_passed_and_hr_may_name_another():
    def opening(closing=None, eligibility=None):
        return SimpleNamespace(closing_date=closing, eligibility_date=eligibility)

    assert counting_date(None, TODAY)[::2] == (TODAY, False)
    assert counting_date(opening(), TODAY) == (TODAY, "the day of assessment, as the opening has no closing date or eligibility date", False)
    assert counting_date(opening(CUT), TODAY) == (CUT, "the closing date of this opening", True)
    # Not yet passed: service that has not happened cannot be counted, so the count runs to the day itself.
    assert counting_date(opening(date(2026, 12, 31)), TODAY) == (
        TODAY, "the day of assessment, as the closing date of this opening (31-12-2026) has not passed", False)
    assert counting_date(opening(CUT, date(2026, 7, 1)), TODAY) == (date(2026, 7, 1), "the eligibility date set for this opening", True)


# --- what the date changes ---------------------------------------------------


def test_service_after_the_date_is_not_counted_whenever_the_post_ended():
    current = _facts(posts=[Post("TEACHING", start=P("2020-10-01"), is_current=True)])
    ended_later = _facts(posts=[Post("TEACHING", start=P("2020-10-01"), end=P("2026-10-04"))])
    not_begun = _facts(posts=[Post("TEACHING", start=P("2026-10-02"), is_current=True)])
    assert service_years(current, ("TEACHING",)).exact == service_years(ended_later, ("TEACHING",)).exact == 6.0
    assert service_years(not_begun, ("TEACHING",)).exact == 0


def test_a_phd_awarded_after_the_date_is_not_held_on_it(session):
    phd = dict(phd_status="COMPLETED", phd_regulation="2016", highest_degree="PhD", phd_awarded=P("2026-10-03"))
    d = _ugc(session, _facts("ASSOCIATE_PROFESSOR", **phd))
    c = _check(d, "phd")
    assert c.result == FAIL and "awarded after 30-09-2026" in c.detail
    # Counted to the day of assessment, the same record holds the degree.
    assert _check(_ugc(session, _facts("ASSOCIATE_PROFESSOR", cut_off=False, as_of=TODAY, **phd)), "phd").result == PASS
    # And it does not exempt from NET/SET on that date either.
    no_net = _ugc(session, _facts(net_set_status="NONE", **phd))
    assert _check(no_net, "net_set").result == FAIL


def test_a_phd_dated_only_to_the_year_is_settled_by_when_the_resume_arrived(session):
    phd = dict(phd_status="COMPLETED", phd_regulation="2016", highest_degree="PhD", phd_awarded=P("2026"))
    # The resume arrived by the date and already called it completed.
    assert _check(_ugc(session, _facts("ASSOCIATE_PROFESSOR", received_on=date(2026, 9, 1), **phd)), "phd").result == PASS
    # It arrived after the date: 2026 may be before or after it, so a person is asked for the award date.
    d = _ugc(session, _facts("ASSOCIATE_PROFESSOR", **phd))
    c = _check(d, "phd")
    assert c.result == UNKNOWN and "30-09-2026" in c.detail
    assert "phd_award_date" in gate2.fields_that_would_settle(SimpleNamespace(details=_details(d)))
    assert _check(_ugc(session, _facts(net_set_status="NONE", **phd)), "net_set").result == UNKNOWN
    # With no award date at all nothing puts it after the date; it is held, as it would be without a date.
    undated = {**phd, "phd_awarded": None}
    assert _check(_ugc(session, _facts("ASSOCIATE_PROFESSOR", **undated)), "phd").result == PASS


def test_a_masters_degree_dated_after_the_date_goes_to_a_person_and_is_never_failed_here(session):
    d = _ugc(session, _facts(masters_awarded=P("2026-10-02")))
    c = _check(d, "masters_by_date")
    assert d.outcome == MANUAL_REVIEW and c.result == UNKNOWN and "after 30-09-2026" in c.detail
    assert (c.clause, c.page) == ("Eligibility date of the opening", "set by HR")  # not presented as a clause of the Regulations
    assert gate2.fields_that_would_settle(SimpleNamespace(details=_details(d))) == ["masters_award_date"]
    assert _ugc(session, _facts(masters_awarded=P("2026-10-02"), cut_off=False, as_of=TODAY)).outcome == SHORTLISTED
    assert _ugc(session, _facts(masters_awarded=P("2015-06"))).outcome == SHORTLISTED


def test_an_assessment_states_the_date_it_counted_on_and_counts_experience_to_it(session, tmp_path):
    a = _assessed(session, tmp_path, None, closing_date=CUT)
    details = latest_evaluation(session, a.application_id).details
    assert (details["as_of"], details["as_of_basis"]) == ("2026-09-30", "the closing date of this opening")
    # The one post on the record: Assistant Professor, 2018 to the present.
    assert details["experience_years"] == {"low": round((CUT - date(2018, 12, 31)).days / 365.25, 2),
                                           "high": round((CUT - date(2018, 1, 1)).days / 365.25, 2)}
    open_ended = _assessed(session, tmp_path, _other_person())
    assert latest_evaluation(session, open_ended.application_id).details["as_of"] == TODAY.isoformat()


def test_publications_dated_after_the_year_of_the_date_are_left_out_and_said_so(session, tmp_path):
    from llm.interface import PublicationEntry

    r = _clean_result()
    r.publications = [PublicationEntry(title="Paper A", kind="JOURNAL", year="2021"), PublicationEntry(title="Paper B", kind="JOURNAL", year="2026")]
    a = _assessed(session, tmp_path, r, closing_date=date(2025, 12, 31))
    assert "1 publication(s) dated after 2025 are not counted." in latest_evaluation(session, a.application_id).details["notes"]


# --- when the date changes ---------------------------------------------------


def test_changing_the_date_in_force_sets_aside_assessments_not_yet_decided(session, tmp_path):
    a = _assessed(session, tmp_path)
    o = a.opening
    same = dict(discipline_group=o.discipline_group, today=TODAY)
    # A closing date still ahead leaves the count on the day of assessment: nothing is disturbed.
    assert intake.update_opening(session, o, closing_date=date(2026, 12, 31), **same) == {"reassess": 0, "decided": 0}
    assert a.status == states.SHORTLISTED
    counts = intake.update_opening(session, o, closing_date=date(2026, 12, 31), eligibility_date=date(2026, 7, 1), **same)
    assert counts == {"reassess": 1, "decided": 0} and a.status == states.EXTRACTED
    assert a.transitions[-1].note == f"date eligibility is counted on changed for {o.reference}; to be assessed again"
    assert assess_application(session, a, TODAY).as_of == date(2026, 7, 1)


def test_assessments_made_before_the_closing_date_can_be_brought_to_it_once_it_has_passed(session, tmp_path):
    a = _assessed(session, tmp_path)  # no closing date: counted to the day of assessment
    o = a.opening
    o.closing_date = date(2026, 12, 31)
    assert gate2.out_of_date(session, o, TODAY) == []  # not yet passed
    o.closing_date = CUT
    assert gate2.out_of_date(session, o, TODAY) == [a]
    assert gate2.reassess_out_of_date(session, o, TODAY) == {"SHORTLISTED": 1}
    assert latest_evaluation(session, a.application_id).details["as_of"] == "2026-09-30"
    assert gate2.out_of_date(session, o, TODAY) == [] and [d.action for d in gate2.decisions(session, a.application_id)] == ["RETURNED"]
    gate2.approve(session, a)
    o.closing_date = date(2026, 9, 1)
    assert gate2.out_of_date(session, o, TODAY) == []  # HR's decision is not reopened by a setting


# --- reading a resume again --------------------------------------------------


def test_a_resume_read_again_is_put_to_the_model_afresh_and_the_new_reading_replaces_the_old(session, tmp_path):
    cache = ResultCache(tmp_path / "cache")
    o = _opening(session, school_id="SCH-013", discipline_group="GENERAL")
    a = _application(session, tmp_path, category="General", differently_abled=False, study_leave_taken=False)
    a.opening_id, a.school_id = o.opening_id, o.school_id
    first = FakeProvider(script=[_clean_result()], cache_model_id="fake")
    read_application(session, a, first, cache)
    assess_application(session, a, TODAY)
    assert (a.status, a.extracted.net_set_status, cache.count()) == (states.SHORTLISTED, "NET", 1)

    gate1.read_again(session, a)
    job = session.scalar(select(Job).where(Job.application_id == a.application_id, Job.status == jobs.PENDING))
    assert (a.status, job.kind) == (states.RECEIVED, jobs.READ_AGAIN)
    assert a.transitions[-1].note == "gate1: to be read again" and a.extracted.net_set_status == "NET"  # nothing is read yet
    assert [d.action for d in gate2.decisions(session, a.application_id)] == ["RETURNED"]
    assert gate1.history(session, a)[-1].action == "READ_AGAIN"
    session.commit()

    second = FakeProvider(script=[_no_net()], cache_model_id="fake")
    ran = jobs.run_due_jobs(session, second, cache)
    assert len(ran) == 1 and second.calls == 1  # the stored result of the first reading was not served
    session.refresh(a)
    assert (a.status, a.extracted.net_set_status, a.category) == (states.EXTRACTED, "NONE", "General")
    assert assess_application(session, a, TODAY).outcome == "NOT_ELIGIBLE"


def test_an_ordinary_reading_still_uses_the_stored_result(session, tmp_path):
    cache = ResultCache(tmp_path / "cache")
    for expected_calls in (1, 0):
        a = _application(session, tmp_path)
        provider = FakeProvider(script=[_clean_result()] if expected_calls else [], cache_model_id="fake")
        read_application(session, a, provider, cache)
        assert provider.calls == expected_calls
        gate1.withdraw(session, a)  # so the second is not taken for a second application by one person


def test_reading_again_is_refused_before_a_first_reading_and_after_hrs_decision(session, tmp_path):
    unread = _application(session, tmp_path)
    with pytest.raises(gate1.ReviewError, match="only after a first reading"):
        gate1.read_again(session, unread)
    decided = _assessed(session, tmp_path)
    gate2.approve(session, decided)
    with pytest.raises(gate1.ReviewError, match="before HR has decided"):
        gate1.read_again(session, decided)
    assert decided.status == states.HR_APPROVED


# --- correcting the posts held -----------------------------------------------


def _only_post(session, a) -> CandidateExperience:
    return session.scalars(select(CandidateExperience).where(CandidateExperience.application_id == a.application_id)).one()


def test_post_dates_can_be_narrowed_and_a_post_added_and_the_assessment_is_done_again(session, tmp_path):
    a = _assessed(session, tmp_path)
    before = latest_evaluation(session, a.application_id).details["experience_years"]
    post = _only_post(session, a)
    k = post.experience_id
    n = gate1.save_posts(session, a, {
        f"kind_{k}": "TEACHING", f"start_{k}": "07-2018", f"current_{k}": "yes",
        "new_designation": "Lecturer", "new_employer": "Placeholder Polytechnic", "kind_new": "TEACHING",
        "start_new": "2015", "end_new": "30/06/2018",
    })
    assert n == 2 and (post.start_stated, post.end_stated, post.is_current) == ("2018-07", None, True)
    added = session.scalars(select(CandidateExperience).where(
        CandidateExperience.application_id == a.application_id, CandidateExperience.experience_id != k)).one()
    assert (added.designation_held, added.start_stated, added.end_stated, added.found_in_resume) == ("Lecturer", "2015", "2018-06-30", False)
    assert a.status == states.EXTRACTED and a.transitions[-1].note == "gate1: posts corrected; to be assessed again"
    assert [d.action for d in gate2.decisions(session, a.application_id)] == ["RETURNED"]
    edits = [(e.field, e.action, e.old_value, e.new_value) for e in gate1.history(session, a)]
    assert edits == [("Post 1: type and dates", "CORRECTED", "Teaching, 2018 to present", "Teaching, 07-2018 to present"),
                     ("Post 2: added", "ENTERED", None, "Lecturer: Teaching, 2015 to 30-06-2018")]
    after = _details(assess_application(session, a, TODAY))["experience_years"]
    assert after["low"] > before["low"] and after["high"] > before["high"]


def test_a_bad_post_entry_is_refused_in_words_and_nothing_is_stored(session, tmp_path):
    a = _assessed(session, tmp_path)
    post = _only_post(session, a)
    k = post.experience_id
    same = {f"kind_{k}": "TEACHING", f"start_{k}": "2018", f"current_{k}": "yes"}
    for form, message in (
        ({**same, f"start_{k}": "July 2018"}, "Post 1: From: write the date as 2019, 07-2019 or 15-07-2019."),
        ({**same, f"end_{k}": "2020"}, "Post 1: give an end date or tick \"still in this post\", not both."),
        ({f"kind_{k}": "TEACHING", f"start_{k}": "2018", f"end_{k}": "2016"}, "Post 1: the end date is before the start date."),
        ({f"kind_{k}": "TEACHING", f"end_{k}": "2016"}, "Post 1: an end date needs a start date."),
        ({**same, f"kind_{k}": "HOBBY"}, "Post 1: choose the type."),
        ({**same, f"start_{k}": "2099"}, "Post 1: From: the date must be between 1950 and today."),
        ({**same, "new_employer": "Placeholder Polytechnic", "kind_new": "TEACHING"}, "New post: give at least the post and its start date."),
        # One good change beside one bad one: neither is stored.
        ({**same, f"start_{k}": "2018-07", "new_designation": "Lecturer", "kind_new": "TEACHING", "start_new": "soon"},
         "New post: From: write the date as 2019, 07-2019 or 15-07-2019."),
        (same, "Nothing was changed."),
    ):
        with pytest.raises(gate1.ReviewError) as exc:
            gate1.save_posts(session, a, form)
        assert exc.value.errors[""] == message
    session.expire_all()
    assert (post.start_stated, a.status, gate1.history(session, a)) == ("2018", states.SHORTLISTED, [])
    gate2.approve(session, a)
    with pytest.raises(gate1.ReviewError, match="before HR has decided"):
        gate1.save_posts(session, a, {**same, f"start_{k}": "2018-07"})


def test_a_post_marked_other_is_no_longer_counted(session, tmp_path):
    r = _clean_result()
    r.teaching_years_raw.value = r.teaching_years_raw.evidence = None  # no stated total to fall back on
    a = _assessed(session, tmp_path, r)
    k = _only_post(session, a).experience_id
    gate1.save_posts(session, a, {f"kind_{k}": "OTHER", f"start_{k}": "2018", f"current_{k}": "yes"})
    assert _details(assess_application(session, a, TODAY))["experience_years"] == {"low": 0.0, "high": 0.0}


def test_dating_a_teaching_post_answers_the_question_about_the_total_years(session, tmp_path):
    r = _clean_result()
    r.experience = [ExperienceEntry(designation="Assistant Professor", institution="Placeholder College", kind="TEACHING")]
    r.teaching_years_raw.value = r.teaching_years_raw.evidence = None
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[r]))
    assert a.status == states.PENDING_REVIEW and a.extracted.review_reasons == ["teaching_years_raw:missing_required"]
    k = _only_post(session, a).experience_id
    gate1.save_posts(session, a, {f"kind_{k}": "TEACHING", f"start_{k}": "2018", f"current_{k}": "yes"})
    assert a.status == states.EXTRACTED and a.extracted.review_reasons == []


# --- the pages ---------------------------------------------------------------


def test_the_application_page_offers_both_and_reports_what_was_done(client, engine, tmp_path):
    from sqlalchemy.orm import Session as _Session

    app_id, opening_id = _assessed_on_server(engine, tmp_path)
    page = client.get(f"/hr/applications/{app_id}").text
    assert "Read this resume again" in page and "Correct the dates or type of a post, or add a post" in page
    assert "Counted as on" in page and "the day of assessment, as the opening has no closing date or eligibility date" in page
    assert "Saving sets the assessment above aside" in page
    with _Session(engine) as s:
        k = s.scalars(select(CandidateExperience.experience_id).where(CandidateExperience.application_id == app_id)).one()

    bad = client.post(f"/hr/applications/{app_id}/posts", data={f"kind_{k}": "TEACHING", f"start_{k}": "sometime"}).text
    assert "Not saved. Post 1: From: write the date as" in bad and "Your decision" in bad
    done = client.post(f"/hr/applications/{app_id}/posts", data={f"kind_{k}": "TEACHING", f"start_{k}": "07-2018", f"current_{k}": "yes"}).text
    assert "Saved 1 post(s). The earlier assessment is set aside" in done and "07-2018" in done and "2018-07" not in done and "Your decision" not in done
    assert "Post 1: type and dates" in done

    again = client.post(f"/hr/applications/{app_id}/read-again").text
    assert "Put back in the reading queue." in again and "Received, waiting to be read" in again and "Sent to be read again" in again
    assert "Read the 1 waiting application" in client.get(f"/hr/openings/{opening_id}").text
    refused = client.post(f"/hr/applications/{app_id}/read-again").text
    assert "A resume can be read again only after a first reading and before HR has decided" in refused
    assert client.post("/hr/applications/999999/read-again").status_code == 404


def test_the_eligibility_date_is_set_shown_and_acted_on_from_the_opening_pages(client, engine, tmp_path):
    from sqlalchemy.orm import Session as _Session

    assert "Eligibility counted as on" in client.get("/hr/openings/new").text
    made = client.post("/hr/openings", data={"school_id": "SCH-013", "designation": "ASSISTANT_PROFESSOR", "discipline_group": "GENERAL",
                                             "eligibility_date": "2026-07-01"}).text
    assert "01-07-2026" in made and "the eligibility date set for this opening" in made
    assert client.post("/hr/openings", data={"school_id": "SCH-013", "designation": "ASSISTANT_PROFESSOR", "discipline_group": "GENERAL",
                                             "eligibility_date": "July"}).history[0].status_code == 303

    app_id, opening_id = _assessed_on_server(engine, tmp_path)
    assert "Assess 1 application(s) again" not in client.get(f"/hr/openings/{opening_id}").text
    with _Session(engine) as s:  # as if the closing date had come and gone since the assessment
        s.get(JobOpening, opening_id).closing_date = date(2020, 1, 1)
        s.commit()
    listing = client.get(f"/hr/openings/{opening_id}").text
    assert "Assess 1 application(s) again as on 01-01-2020" in listing and "the closing date of this opening" in listing
    done = client.post(f"/hr/openings/{opening_id}/reassess").text
    assert "Assessed again 1 application(s): 1 meet the post applied for." in done and "Assess 1 application(s) again" not in done
    page = client.get(f"/hr/applications/{app_id}").text
    assert "01-01-2020" in page and "the closing date of this opening" in page
    assert "No assessment was out of date." in client.post(f"/hr/openings/{opening_id}/reassess").text

    form = client.get(f"/hr/openings/{opening_id}/edit").text
    assert 'name="eligibility_date"' in form and 'value="2020-01-01"' in form
    moved = client.post(f"/hr/openings/{opening_id}/edit", data={"discipline_group": "GENERAL", "closing_date": "2020-01-01",
                                                                 "eligibility_date": "2019-06-30"}).text
    assert "The date eligibility is counted on changed, so 1 assessed application(s) were set aside" in moved
    assert "30-06-2019" in moved and "the eligibility date set for this opening" in moved
