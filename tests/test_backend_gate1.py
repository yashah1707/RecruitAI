"""Phase 4: the Reader's new fields, the NET/SET flag, and Gate 1 (extraction review).

Fake provider, synthetic data, no network. Runs on SQLite by default and on
PostgreSQL when TEST_DATABASE_URL is set.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from backend import gate1, intake, jobs, states
from backend.institutions import load_csv, match_institution, match_key
from backend.lists import DISCIPLINES, canonical_state, listed_discipline
from backend.models import (
    Application,
    Candidate,
    CandidateAchievement,
    CandidateEvent,
    CandidateExperience,
    CandidatePersonalDetails,
    CandidateProfile,
    CandidatePublication,
    CandidateQualification,
    CandidateResearchProfile,
    CandidateSubjectTaught,
    ExtractedData,
    InstitutionMaster,
    Job,
    ReviewEdit,
)
from backend.reader_service import read_application
from llm.confidence import evaluate
from llm.interface import (
    AchievementEntry,
    EducationEntry,
    EventEntry,
    ExperienceEntry,
    ExtractionFailure,
    PublicationEntry,
    ResearchProfile,
    SubjectEntry,
)
from llm.postprocess import mentions_net_set, parse_amount_inr, sanitize
from llm.providers.fake_provider import CANNED_RESULTS, FakeProvider
from tests.test_backend_foundation import _application, _clean_result, _docx, client, engine, session  # noqa: F401
from tests.test_backend_intake import _opening, _storage  # noqa: F401

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

RESUME = (
    "Sample Exampleton, 12 Placeholder Road, Pune, Maharashtra. "
    "EDUCATIONAL QUALIFICATION M.Tech (Computer Engineering), Synthetic University, 2015, 68.4%. "
    "Assistant Professor, Placeholder College, 2018-2024. Pursued Ph.D. part-time while working. "
    "Publications: S. Exampleton, A. Otherperson, Paper A on Grounded Reading, Journal of Samples, IF: 3.2. "
    "Funded project: Sample Sensing Study, Rs. 12.5 Lakhs. "
    "Presented at International Conference on Samples. "
    "Subjects taught (M.Tech): Advanced Sampling. ORCID 0000-0002-1825-0097. h-index: 8. Citations: 214."
)


def _none_result(**kw):
    """A candidate with no NET/SET, everything else read cleanly."""
    r = _clean_result()
    r.net_set_status.value, r.net_set_status.confidence, r.net_set_status.evidence = "NONE", 0.95, None
    for name, value in kw.items():
        setattr(r, name, value)
    return r


# --- NET/SET: "none" on a resume that never mentions it ----------------------


def test_a_resume_with_no_net_set_wording_is_no_longer_flagged_for_it():
    """Before: confidence was scaled by how much else was read, so a candidate
    who simply had no NET/SET was sent to review nearly every time."""
    r = _none_result()
    r.teaching_years_raw.value = r.teaching_years_raw.evidence = None  # coverage drops; this used to sink it
    result, _ = sanitize(r, RESUME)
    assert result.net_set_mentioned_in_text is False
    assert result.net_set_status.confidence == 0.85
    assert not [x for x in evaluate(result).reasons if x.startswith("net_set_status")]


def test_none_is_flagged_when_the_resume_does_use_net_set_wording():
    result, _ = sanitize(_none_result(), RESUME + " Appeared for UGC-NET in 2019.")
    assert result.net_set_mentioned_in_text is True
    assert "net_set_status:mentioned_in_resume" in evaluate(result).reasons


@pytest.mark.parametrize(
    "text, expected",
    [("Qualified NET (June 2019)", True), ("MH-SET 2018", True), ("SLET, Assam", True),
     ("State Eligibility Test cleared", True), ("CSIR JRF", True),
     ("Skills: ASP.NET, VB.NET, Dot Net, .NET Core", False), ("Used the internet and a data set", False),
     ("Ph.D. Entrance Test (PET) qualified", False), ("Set up a networking laboratory", False)],
)
def test_net_set_wording_is_found_and_dot_net_is_not_mistaken_for_it(text, expected):
    assert mentions_net_set(text) is expected


def test_a_found_net_is_not_touched_by_the_wording_check():
    result, _ = sanitize(_clean_result(), RESUME + " UGC-NET (Physical Sciences), December 2016")
    assert result.net_set_status.value == "NET" and not evaluate(result).reasons == ["net_set_status:mentioned_in_resume"]


# --- the new fields are kept only when the resume states them ----------------


def _rich_result():
    r = _none_result()
    r.publications = [PublicationEntry(
        title="Paper A on Grounded Reading", kind="JOURNAL", authors=["S. Exampleton", "A. Otherperson"],
        is_first_author=True, impact_factor=3.2,
    )]
    r.achievements = [AchievementEntry(kind="FUNDED_PROJECT", title="Sample Sensing Study", amount="Rs. 12.5 Lakhs",
                                       level="NATIONAL")]
    r.events = [EventEntry(kind="CONFERENCE", title="International Conference on Samples", role="PRESENTED",
                           level="INTERNATIONAL")]
    r.experience = [ExperienceEntry(designation="Assistant Professor", institution="Placeholder College",
                                    kind="TEACHING", start="2018", end="2024", concurrent_with_study=True)]
    r.subjects = [SubjectEntry(name="Advanced Sampling", level="PG"), SubjectEntry(name="Invented Subject", level="UG")]
    r.research_profile = ResearchProfile(orcid_id="0000-0002-1825-0097", scopus_author_id="57200000000",
                                         h_index=8, total_citations=214, i10_index=99)
    r.state = "Maharashtra"
    return r


def test_stated_details_survive_and_python_does_the_counting_and_the_unit_change():
    result, _ = sanitize(_rich_result(), RESUME)
    p = result.publications[0]
    assert (p.author_count, p.is_first_author, p.impact_factor) == (2, True, 3.2)
    a = result.achievements[0]
    assert a.amount == "Rs. 12.5 Lakhs" and a.amount_inr == 1_250_000
    assert a.level is None  # the resume never says "national"
    assert result.events[0].level == "INTERNATIONAL"
    assert result.experience[0].concurrent_with_study is True
    assert [(s.name, s.level) for s in result.subjects] == [("Advanced Sampling", "PG")]
    assert result.subjects_taught == ["Advanced Sampling"]
    profile = result.research_profile
    assert (profile.orcid_id, profile.h_index, profile.total_citations) == ("0000-0002-1825-0097", 8, 214)
    assert profile.scopus_author_id is None and profile.i10_index is None  # neither is in the resume
    assert result.state == "Maharashtra"


def test_details_the_resume_does_not_state_are_dropped_not_trusted():
    r = _rich_result()
    r.publications[0].authors = ["S. Exampleton", "Someone Invented"]
    r.publications[0].impact_factor = 7.9
    r.experience[0].concurrent_with_study = False
    r.state = "Karnataka"
    result, _ = sanitize(r, RESUME.replace("part-time while working", "full time"))
    p = result.publications[0]
    assert (p.authors, p.author_count, p.is_first_author, p.impact_factor) == ([], None, None, None)
    assert result.experience[0].concurrent_with_study is None  # "no" cannot be read off a resume
    assert result.state is None


def test_concurrency_needs_the_resume_to_say_so_in_words():
    result, _ = sanitize(_rich_result(), RESUME.replace("part-time while working", "from 2019"))
    assert result.experience[0].concurrent_with_study is None


@pytest.mark.parametrize(
    "text, rupees",
    [("Rs. 12.5 Lakhs", 1_250_000), ("INR 5,00,000", 500_000), ("₹ 2 crore", 20_000_000), ("Rs 75 thousand", 75_000),
     ("8.5 lacs", 850_000), ("Rs. 3,50,000/-", 350_000),
     ("USD 20,000", None), ("12.5", None), ("2019, Rs. 5 lakh", None), ("", None), (None, None)],
)
def test_a_funding_amount_is_turned_into_rupees_only_when_it_is_plainly_one_rupee_amount(text, rupees):
    assert parse_amount_inr(text) == rupees


# --- reference lists ---------------------------------------------------------


@pytest.mark.parametrize(
    "course, expected",
    [("Computer Engineering", "Computer Science & Engineering"), ("Computer Science (Artificial Intelligence)", "Artificial Intelligence"),
     ("IT", "Information Technology"), ("VLSI Design", "Electronics & Communication"), ("E&TC", "Electronics & Communication"),
     ("Civil Engg", "Civil Engineering"), ("Business Administration", "Management"), ("Physics", None), ("", None), (None, None)],
)
def test_a_course_is_placed_on_the_discipline_list_only_when_its_wording_names_one(course, expected):
    assert listed_discipline(course) == expected
    assert expected is None or expected in DISCIPLINES


def test_a_state_is_accepted_only_in_the_lists_own_spelling():
    assert canonical_state(" maharashtra ") == "Maharashtra"
    assert canonical_state("Jammu & Kashmir") == "Jammu and Kashmir"
    assert canonical_state("Poona") is None and canonical_state(None) is None


def test_the_workbooks_institutions_are_seeded_and_matched_exactly(session):
    assert len(session.scalars(select(InstitutionMaster)).all()) == 4
    iitb = match_institution(session, None, "The Indian Institute of Technology, Bombay")
    assert session.get(InstitutionMaster, iitb).tier == "PREMIER"
    assert match_institution(session, "Indian Institute of Technology Madras") is None  # near is not the same
    assert match_institution(session, "IIT Bombay") is None  # no alias has been given yet
    assert match_key("Savitribai Phule Pune University, Pune") != match_key("Savitribai Phule Pune University")


def test_hr_can_load_institutions_and_aliases_from_a_csv(session, tmp_path):
    f = tmp_path / "institutions.csv"
    f.write_text(
        "institution_name,tier,category,aliases\n"
        "Indian Institute of Technology Bombay,PREMIER,IIT,IIT Bombay; I.I.T. Mumbai\n"
        "Synthetic State University,STATE,State University,\n",
        encoding="utf-8",
    )
    assert load_csv(session, f) == {"added": 1, "updated": 1}
    assert match_institution(session, "iit bombay") == match_institution(session, "Indian Institute of Technology Bombay")
    f.write_text("institution_name,tier,category,aliases\nBad College,EXCELLENT,Other,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_csv(session, f)


# --- the Reader stores the new fields, and asks for what only a form can give -


def test_the_new_fields_reach_the_tables(session, tmp_path):
    r, _ = sanitize(_rich_result(), RESUME)
    r.education = [EducationEntry(level="PG", degree="M.Tech", course="Computer Engineering",
                                  university="Savitribai Phule Pune University", completion="2015", marks_pct=68.4)]
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[r]))
    session.flush()
    p = session.scalars(select(CandidatePublication)).one()
    assert (p.author_count, p.is_first_author, p.impact_factor) == (2, True, 3.2)
    ach = session.scalars(select(CandidateAchievement)).one()
    assert (ach.amount_stated, ach.amount_inr, ach.level) == ("Rs. 12.5 Lakhs", 1_250_000, None)
    assert session.scalars(select(CandidateEvent)).one().level == "INTERNATIONAL"
    assert session.scalars(select(CandidateExperience)).one().concurrent_with_study is True
    assert session.scalars(select(CandidateSubjectTaught)).one().course_level == "PG"
    q = session.scalars(select(CandidateQualification)).one()
    assert q.discipline_listed == "Computer Science & Engineering"
    assert session.get(InstitutionMaster, q.institution_id).institution_name == "Savitribai Phule Pune University"
    research = session.get(CandidateResearchProfile, a.candidate_id)
    assert (research.orcid_id, research.h_index, research.total_citations) == ("0000-0002-1825-0097", 8, 214)
    assert session.get(CandidatePersonalDetails, a.candidate_id).state == "Maharashtra"


def test_the_form_answer_outranks_the_state_on_the_resume(session, tmp_path):
    r = _clean_result()
    r.state = "Maharashtra"
    a = _application(session, tmp_path, applicant_state="Goa")
    read_application(session, a, FakeProvider(script=[r]))
    assert session.get(CandidatePersonalDetails, a.candidate_id).state == "Goa"


def test_an_hr_upload_is_not_held_back_for_the_form_answers_it_came_without(session, tmp_path):
    """They matter only to the cl. 3.4 relaxation and to cl. 3.11; where one would change an
    outcome the engine names it. Holding every upload for them made twelve of twelve stop."""
    from backend.reader_service import missing_form_answers

    a = _application(session, tmp_path, resume_source="MANUAL_UPLOAD")
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    assert a.status == states.EXTRACTED and a.extracted.review_reasons == []
    assert missing_form_answers(session, a) == ["category", "state", "differently_abled", "study_leave_taken"]
    answered = _application(session, tmp_path, resume_source="WEB_FORM", category="SC", differently_abled=False, applicant_state="Goa")
    read_application(session, answered, FakeProvider(script=[_none_result(email="other.exampleton@example.org")]))
    assert missing_form_answers(session, answered) == []  # "not applicable" for study leave is an answer on the form


def test_an_application_from_the_form_with_a_clean_reading_needs_no_review(session, tmp_path):
    a = _application(session, tmp_path, resume_source="WEB_FORM", category="General", differently_abled=False)
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    assert a.status == states.EXTRACTED and a.extracted.review_reasons == []


def test_a_poorly_read_name_is_not_a_reason_when_the_applicant_typed_it(session, tmp_path):
    r = _clean_result()
    r.candidate_name.confidence = 0.2
    a = _application(session, tmp_path, applicant_name="Sample Exampleton")
    read_application(session, a, FakeProvider(script=[r]))
    assert a.status == states.EXTRACTED


# --- Gate 1 ------------------------------------------------------------------


def _set_without_state():
    """The canned SET-without-State result, with nothing else to query."""
    r = CANNED_RESULTS[1].model_copy(deep=True)
    r.publications_in_progress_count.evidence = r.publications_in_progress_titles.evidence = "1 paper under review"
    return r


def _pending(session, tmp_path, result=None, **fields) -> Application:
    a = _application(session, tmp_path, **fields)
    read_application(session, a, FakeProvider(script=[result or _set_without_state()]))
    session.commit()
    assert a.status == states.PENDING_REVIEW
    return a


def test_only_the_flagged_fields_are_shown_with_the_reason_and_the_quote(session, tmp_path):
    """The SET-without-State result: the State is missing, and a Ph.D. holder's
    Regulations year has to come from a person."""
    a = _pending(session, tmp_path)
    flags = {f.name: f for f in gate1.flagged_fields(session, a)}
    assert set(flags) == {"phd_regulation", "set_state"}
    assert flags["set_state"].reasons == ["A SET was found without its State."]
    assert flags["phd_regulation"].value == ""


def test_a_field_that_decides_another_brings_it_along(session, tmp_path):
    r = _clean_result()
    r.net_set_status.confidence = 0.3
    a = _pending(session, tmp_path, r)
    assert [f.name for f in gate1.flagged_fields(session, a)] == ["net_set_status", "set_state"]


def test_a_back_up_model_reading_puts_every_read_field_up_for_checking(session, tmp_path):
    r = _clean_result()
    r.lighter_model_fallback = True
    a = _pending(session, tmp_path, r)
    names = [f.name for f in gate1.flagged_fields(session, a)]
    assert "highest_degree" in names and "publications_count" in names and "category" not in names


def test_an_incomplete_check_is_refused_whole_and_names_each_field(session, tmp_path):
    a = _pending(session, tmp_path)
    with pytest.raises(gate1.ReviewError) as exc:
        gate1.save_review(session, a, {"set_state": "Atlantis"}, set())
    assert set(exc.value.errors) == {"set_state", "phd_regulation"}
    session.rollback()
    assert a.status == states.PENDING_REVIEW and a.extracted.set_state is None
    assert session.scalars(select(ReviewEdit)).all() == []


def test_completing_the_check_stores_the_answers_audits_them_and_moves_on(session, tmp_path):
    a = _pending(session, tmp_path)
    settled = gate1.save_review(session, a, {"set_state": "Maharashtra"}, {"phd_regulation"})
    session.commit()
    assert settled == ["phd_regulation", "set_state"]
    e = a.extracted
    assert (e.set_state, e.phd_regulation, e.needs_review, e.review_reasons) == ("Maharashtra", None, False, [])
    assert a.status == states.EXTRACTED
    last = a.transitions[-1]
    assert (last.from_state, last.to_state, last.actor) == ("PENDING_REVIEW", "EXTRACTED", "user:hr")
    assert last.note == "gate1: phd_regulation, set_state"  # field names only, never the values
    edits = {d.field: (d.action, d.old_value, d.new_value) for d in session.scalars(select(ReviewEdit))}
    assert edits == {"set_state": ("ENTERED", None, "Maharashtra"), "phd_regulation": ("LEFT_EMPTY", None, None)}


def test_a_value_left_as_read_is_confirmed_and_a_changed_one_is_corrected(session, tmp_path):
    r = CANNED_RESULTS[2].model_copy(deep=True)  # low confidence on teaching years and the name
    a = _pending(session, tmp_path, r, category="General", differently_abled=False)
    flags = {f.name: f.value for f in gate1.flagged_fields(session, a)}
    assert flags["teaching_years_raw"] == "3"
    answers = dict(flags, teaching_years_raw="4.5", candidate_name="C. Synthetic Candidate")
    gate1.save_review(session, a, answers, set())
    actions = {d.field: d.action for d in session.scalars(select(ReviewEdit))}
    assert actions["teaching_years_raw"] == "CORRECTED" and actions["candidate_name"] == "CONFIRMED"
    assert a.extracted.teaching_years == 4.5 and a.status == states.EXTRACTED


def test_changing_to_set_demands_its_state_and_changing_away_clears_it(session, tmp_path):
    r = _clean_result()
    r.net_set_status.confidence = 0.3
    a = _pending(session, tmp_path, r)
    with pytest.raises(gate1.ReviewError) as exc:
        gate1.save_review(session, a, {"net_set_status": "SET"}, set())
    assert list(exc.value.errors) == ["set_state"]
    session.rollback()
    gate1.save_review(session, a, {"net_set_status": "NONE", "set_state": "Goa"}, set())
    assert (a.extracted.net_set_status, a.extracted.set_state) == ("NONE", None)


def test_a_ph_d_status_change_keeps_has_phd_in_step(session, tmp_path):
    r = _clean_result()
    r.phd_status.confidence = 0.3
    a = _pending(session, tmp_path, r)
    gate1.save_review(session, a, {"phd_status": "COMPLETED", "phd_regulation": "2016"}, set())
    assert (a.extracted.has_phd, a.extracted.phd_regulation) == (True, "2016")


@pytest.mark.parametrize(
    "field, value",
    [("marks_pct", "140"), ("marks_pct", "abc"), ("masters_award_date", "2999-01-01"), ("masters_award_date", "soon"),
     ("publications_count", "2.5"), ("teaching_years_raw", "-1"), ("teaching_years_raw", "nan")],
)
def test_values_that_cannot_be_right_are_refused(session, tmp_path, field, value):
    r = _clean_result()
    getattr(r, field).confidence = 0.2
    if getattr(r, field).value is None:
        getattr(r, field).value = date(2015, 6, 30) if "date" in field else 5
    a = _pending(session, tmp_path, r)
    with pytest.raises(gate1.ReviewError) as exc:
        gate1.save_review(session, a, {field: value}, set())
    assert field in exc.value.errors


def test_hr_enters_the_form_answers_for_an_uploaded_resume(session, tmp_path):
    a = _application(session, tmp_path, resume_source="MANUAL_UPLOAD")
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    with pytest.raises(gate1.ReviewError):
        gate1.reopen_fields(session, a, ["not_a_field"])
    opened = gate1.reopen_fields(session, a, ["category", "state", "differently_abled", "study_leave_taken"])
    assert len(opened) == 4 and a.status == states.PENDING_REVIEW
    assert a.transitions[-1].note == "gate1: reopened for category, state, differently_abled, study_leave_taken"
    assert gate1.flagged_fields(session, a)[0].reasons == ["An answer the application form asks for; this resume came without one."]
    gate1.save_review(session, a, {"category": "SC", "state": "Maharashtra", "differently_abled": "no"}, {"study_leave_taken"})
    assert (a.category, a.applicant_state, a.differently_abled, a.study_leave_taken) == ("SC", "Maharashtra", False, None)
    personal = session.get(CandidatePersonalDetails, a.candidate_id)
    assert (personal.category, personal.state, personal.differently_abled_flag) == ("SC", "Maharashtra", False)
    assert a.status == states.EXTRACTED


def test_an_application_not_waiting_for_review_cannot_be_edited(session, tmp_path):
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    with pytest.raises(gate1.ReviewError):
        gate1.save_review(session, a, {"marks_pct": "99"}, set())
    assert a.extracted.marks_pct == 68.4


# --- possible duplicates -----------------------------------------------------


def _two_with_one_email(session, tmp_path, **second_fields):
    first = _application(session, tmp_path, category="General", differently_abled=False)
    read_application(session, first, FakeProvider(script=[_clean_result()]))
    second = _application(session, tmp_path, category="General", differently_abled=False, **second_fields)
    read_application(session, second, FakeProvider(script=[_clean_result()]))
    session.commit()
    assert second.status == states.PENDING_REVIEW and second.possible_duplicate_candidate_id == first.candidate_id
    return first, second


def test_the_same_person_joins_the_records_and_removes_the_stand_in(session, tmp_path):
    first, second = _two_with_one_email(session, tmp_path)
    stand_in = second.candidate_id
    shown = gate1.duplicate_of(session, second)
    assert shown["email"] == "sample.exampleton@example.org" and shown["applications"] == [first]

    gate1.resolve_duplicate(session, second, same_person=True)
    session.commit()
    assert second.candidate_id == first.candidate_id and second.possible_duplicate_candidate_id is None
    assert second.status == states.EXTRACTED
    assert session.get(Candidate, stand_in) is None
    assert session.get(CandidatePersonalDetails, stand_in) is None and session.get(CandidateProfile, stand_in) is None
    owners = {q.candidate_id for q in session.scalars(select(CandidateQualification))}
    assert owners == {first.candidate_id}
    assert session.scalars(select(ReviewEdit)).one().action == "SAME_PERSON"


def test_a_different_person_is_kept_apart(session, tmp_path):
    first, second = _two_with_one_email(session, tmp_path)
    gate1.resolve_duplicate(session, second, same_person=False)
    assert second.candidate_id != first.candidate_id and second.possible_duplicate_candidate_id is None
    assert second.status == states.EXTRACTED and second.extracted.review_reasons == []


def test_one_person_cannot_end_up_with_two_live_applications_for_one_opening(session, tmp_path):
    o = _opening(session)
    first, second = _two_with_one_email(session, tmp_path)
    first.opening_id = second.opening_id = o.opening_id
    session.commit()
    with pytest.raises(gate1.ReviewError, match="already applied"):
        gate1.resolve_duplicate(session, second, same_person=True)
    session.rollback()
    gate1.withdraw(session, second)
    assert second.status == states.WITHDRAWN and second.possible_duplicate_candidate_id is None


def test_the_duplicate_question_stays_open_until_it_is_answered(session, tmp_path):
    r = _set_without_state()
    r.email = "sample.exampleton@example.org"
    first = _application(session, tmp_path)
    read_application(session, first, FakeProvider(script=[_clean_result()]))
    second = _application(session, tmp_path)
    read_application(session, second, FakeProvider(script=[r]))
    gate1.save_review(session, second, {"set_state": "Goa"}, {"phd_regulation"})
    assert second.status == states.PENDING_REVIEW and second.extracted.review_reasons == ["candidate:possible_duplicate"]
    gate1.resolve_duplicate(session, second, same_person=False)
    assert second.status == states.EXTRACTED


def test_an_assessed_application_cannot_be_withdrawn_from_this_screen(session, tmp_path):
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    with pytest.raises(gate1.ReviewError):
        gate1.withdraw(session, a)


# --- a job that stopped retrying ---------------------------------------------


def test_hr_can_give_a_stopped_job_a_fresh_start(session):
    o = _opening(session)
    a = intake.hr_upload(session, o, [("cv.docx", _docx())])[0].application
    job = session.scalars(select(Job)).one()
    job.status, job.attempts, job.note = jobs.FAILED, jobs.MAX_ATTEMPTS, "gave up after 8 attempts: reader_unavailable: quota"
    session.commit()
    assert jobs.requeue(session, job) is True
    assert (job.status, job.attempts, job.finished_at) == (jobs.PENDING, 0, None)
    assert jobs.requeue(session, job) is False  # only a stopped job

    ran = jobs.run_due_jobs(session, FakeProvider(script=[_clean_result()]))
    assert len(ran) == 1 and a.status == states.EXTRACTED


# --- the pages ---------------------------------------------------------------


def _uploaded(client, engine, result=None) -> int:
    """An HR upload, read with the fake provider; returns its application id."""
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        o = _opening(s)
        a = intake.hr_upload(s, o, [("cv.docx", _docx())])[0].application
        jobs.run_due_jobs(s, FakeProvider(script=[result or _clean_result()]))
        s.commit()
        return a.application_id


def test_the_review_page_shows_the_flagged_fields_and_nothing_to_edit_elsewhere(client, engine):
    app_id = _uploaded(client, engine)
    before = client.get(f"/hr/applications/{app_id}").text
    assert "Form answers not yet entered" in before and "can be assessed without them" in before and "Fields to check" not in before
    page = client.post(f"/hr/applications/{app_id}/reopen", data={"fields": ["category", "state", "differently_abled", "study_leave_taken"]})
    assert page.status_code == 200
    assert "Fields to check (4)" in page.text and 'name="f_category"' in page.text
    assert 'name="f_marks_pct"' not in page.text and 'name="f_net_set_status"' not in page.text
    assert "What the reader found" in page.text and "Placeholder College" in page.text
    assert client.get("/hr/applications/999999").status_code == 404


def test_saving_from_the_page_finishes_the_check_and_shows_the_changes(client, engine):
    app_id = _uploaded(client, engine)
    client.post(f"/hr/applications/{app_id}/reopen", data={"fields": ["category", "state", "differently_abled", "study_leave_taken"]})
    bad = client.post(f"/hr/applications/{app_id}/review", data={"f_category": "SC", "f_state": "Goa"})
    assert bad.status_code == 422 and "Enter a value, or tick the box" in bad.text
    assert '<option value="SC" selected>' in bad.text  # what was typed is kept

    ok = client.post(
        f"/hr/applications/{app_id}/review", follow_redirects=False,
        data={"f_category": "SC", "f_state": "Goa", "f_differently_abled": "no", "empty": "study_leave_taken"},
    )
    assert ok.status_code == 303
    page = client.get(ok.headers["location"])
    assert "ready for assessment" in page.text and "Changes made at this check" in page.text
    assert "Fields to check" not in page.text
    assert client.get(f"/applications/{app_id}").json()["status"] == "EXTRACTED"
    again = client.post(f"/hr/applications/{app_id}/review", data={"f_category": "ST"})
    assert again.status_code == 422  # a finished check cannot be reopened by posting to it


def test_the_resume_is_served_under_its_reference_not_its_filename(client, engine):
    app_id = _uploaded(client, engine)
    r = client.get(f"/hr/applications/{app_id}/resume")
    assert r.status_code == 200 and r.headers["content-type"] == DOCX
    assert f"APP-{app_id:06d}.docx" in r.headers["content-disposition"] and "cv.docx" not in r.headers["content-disposition"]


def test_the_opening_page_links_to_the_check_and_home_offers_a_retry(client, engine):
    from sqlalchemy.orm import Session as _Session

    app_id = _uploaded(client, engine)
    with _Session(engine) as s:
        opening_id = s.get(Application, app_id).opening_id
        job = s.scalars(select(Job)).one()
        s.get(Application, app_id).status = states.RECEIVED
        job.status, job.attempts = jobs.FAILED, jobs.MAX_ATTEMPTS
        s.commit()
        job_id = job.job_id
    assert f'/hr/applications/{app_id}"' in client.get(f"/hr/openings/{opening_id}").text
    assert f"/hr/jobs/{job_id}/requeue" in client.get("/").text
    r = client.post(f"/hr/jobs/{job_id}/requeue", follow_redirects=False)
    assert r.status_code == 303 and "Put%20back" in r.headers["location"]
    assert client.post("/hr/jobs/999999/requeue").status_code == 404


def test_the_duplicate_and_withdraw_buttons_work_from_the_page(client, engine):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        o = _opening(s)
        for name in ("a.docx", "b.docx"):
            d = _docx() if name == "a.docx" else _docx() + b" "
            intake.hr_upload(s, o, [(name, d)])
        s.commit()
    # two different files, read as the same email address
    with _Session(engine) as s:
        jobs.run_due_jobs(s, FakeProvider(script=[_clean_result(), _clean_result()]))
        s.commit()
        second = s.scalars(select(Application).order_by(Application.application_id)).all()[-1].application_id
    page = client.get(f"/hr/applications/{second}")
    assert "Is this the same person?" in page.text
    assert client.post(f"/hr/applications/{second}/duplicate", data={"same": "maybe"}).status_code == 422
    clash = client.post(f"/hr/applications/{second}/duplicate", data={"same": "yes"})
    assert "already applied for this opening" in clash.text
    gone = client.post(f"/hr/applications/{second}/withdraw")
    assert "Application withdrawn." in gone.text and "Withdraw this application" not in gone.text
