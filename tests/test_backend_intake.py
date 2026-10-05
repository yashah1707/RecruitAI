"""Phase 3: openings, the application form, HR upload, duplicates and the reading queue.

Fake provider, synthetic files, no network. Runs on SQLite by default and on
PostgreSQL when TEST_DATABASE_URL is set.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import intake, jobs, states
from backend.models import Application, Candidate, CandidatePersonalDetails, Department, Job, JobOpening, RecruitmentDrive
from llm.interface import ExtractionFailure
from llm.providers.fake_provider import FakeProvider
from tests.test_backend_foundation import _clean_result, _docx, _scanned_pdf, client, engine, session  # noqa: F401

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _storage(tmp_path, monkeypatch):
    from backend import settings

    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "resumes")


def _opening(session, **kw) -> JobOpening:
    fields = dict(school_id="SCH-008", designation="ASSISTANT_PROFESSOR", discipline_group="ENGINEERING_TECHNOLOGY")
    fields.update(kw)
    opening = intake.create_opening(session, **fields)
    session.commit()
    return opening


def _form(**kw) -> intake.ApplicantForm:
    fields = dict(full_name="Sample Exampleton", email="sample.exampleton@example.org", phone="+91 98765 00000",
                  state="Maharashtra", category="General", differently_abled="no", study_leave_taken="na",
                  declaration=True)
    fields.update(kw)
    return intake.ApplicantForm(**fields)


def _data(**kw) -> dict:
    d = {"full_name": "Sample Exampleton", "email": "sample.exampleton@example.org", "phone": "+91 98765 00000",
         "state": "Maharashtra", "category": "OBC-NCL", "differently_abled": "no", "study_leave_taken": "no",
         "declaration": "yes"}
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


# --- openings ----------------------------------------------------------------


def test_an_opening_records_the_rule_set_hr_chose(session):
    o = _opening(session, department_name="  Department of   Computer Engineering ", title="Data Science",
                 closing_date=date(2026, 12, 31), advertisement_ref="MIT-ADT/HR/2026/014", advertisement_date=date(2026, 10, 1))
    assert o.reference == f"OPN-{o.opening_id:05d}" and o.status == "OPEN"
    assert o.discipline_group == "ENGINEERING_TECHNOLOGY"
    assert o.department.name == "Department of Computer Engineering" and o.school.has_departments is True
    assert o.drive.advertisement_ref == "MIT-ADT/HR/2026/014" and o.drive.advertisement_date == date(2026, 10, 1)


def test_a_department_and_a_drive_are_reused_not_duplicated(session):
    a = _opening(session, department_name="Computer Engineering", advertisement_ref="AD-1")
    b = _opening(session, department_name="computer engineering", advertisement_ref="AD-1")
    assert a.department_id == b.department_id and a.drive_id == b.drive_id
    assert len(session.scalars(select(Department)).all()) == 1
    assert len(session.scalars(select(RecruitmentDrive)).all()) == 1


@pytest.mark.parametrize(
    "bad, field",
    [({"school_id": "SCH-999"}, "school_id"), ({"designation": "DEAN"}, "designation"),
     ({"discipline_group": "ASTROLOGY"}, "discipline_group"),
     ({"closing_date": date(2026, 1, 1), "advertisement_date": date(2026, 2, 1)}, "closing_date")],
)
def test_an_opening_with_a_bad_field_is_refused_and_names_the_field(session, bad, field):
    with pytest.raises(intake.IntakeError) as exc:
        _opening(session, **bad)
    assert field in exc.value.errors
    assert session.scalars(select(JobOpening)).all() == []


def test_the_suggested_rule_set_follows_the_schools_regulator(session):
    from backend.models import School

    assert intake.suggested_discipline_group(session.get(School, "SCH-008")) == "ENGINEERING_TECHNOLOGY"
    assert intake.suggested_discipline_group(session.get(School, "SCH-005")) == "DESIGN"
    assert intake.suggested_discipline_group(session.get(School, "SCH-016")) == "MANAGEMENT"
    assert intake.suggested_discipline_group(session.get(School, "SCH-013")) == "GENERAL"


def test_an_opening_stops_accepting_when_closed_or_past_its_date(session):
    o = _opening(session, closing_date=date(2026, 10, 10))
    assert intake.is_accepting(o, today=date(2026, 10, 10)) is True  # the closing day itself still counts
    assert intake.is_accepting(o, today=date(2026, 10, 11)) is False
    intake.close_opening(session, o)
    assert intake.is_accepting(o, today=date(2026, 10, 1)) is False and o.closed_at is not None


# --- the application form ----------------------------------------------------


def test_a_valid_application_is_stored_with_the_form_answers_and_queued(session):
    o = _opening(session)
    a = intake.submit_application(session, o, _form(category="SC", differently_abled="yes", study_leave_taken="no"),
                                  "My Resume.docx", _docx())
    session.commit()
    assert a.status == states.RECEIVED and a.resume_source == "WEB_FORM" and a.reference == f"APP-{a.application_id:06d}"
    # school, department and designation come from the opening, never from the applicant or the resume
    assert (a.school_id, a.applied_designation, a.opening_id) == ("SCH-008", "ASSISTANT_PROFESSOR", o.opening_id)
    assert (a.category, a.differently_abled, a.study_leave_taken) == ("SC", True, False)
    assert (a.applicant_name, a.applicant_email, a.applicant_state) == ("Sample Exampleton", "sample.exampleton@example.org", "Maharashtra")
    assert a.candidate.email == "sample.exampleton@example.org"
    job = session.scalars(select(Job)).one()
    assert (job.kind, job.status, job.application_id) == (jobs.READ_APPLICATION, jobs.PENDING, a.application_id)
    assert a.transitions[0].note == f"source=WEB_FORM; opening={o.reference}"


def test_study_leave_not_applicable_is_stored_as_unknown_not_as_no(session):
    a = intake.submit_application(session, _opening(session), _form(study_leave_taken="na"), "cv.docx", _docx())
    assert a.study_leave_taken is None


@pytest.mark.parametrize(
    "bad, field",
    [({"full_name": " "}, "full_name"), ({"email": "not-an-email"}, "email"), ({"phone": "12345"}, "phone"),
     ({"state": "Atlantis"}, "state"), ({"category": "Royalty"}, "category"),
     ({"differently_abled": ""}, "differently_abled"), ({"study_leave_taken": "maybe"}, "study_leave_taken"),
     ({"declaration": False}, "declaration")],
)
def test_each_invalid_answer_is_reported_against_its_own_field_and_nothing_is_stored(session, bad, field):
    o = _opening(session)
    with pytest.raises(intake.IntakeError) as exc:
        intake.submit_application(session, o, _form(**bad), "cv.docx", _docx())
    assert list(exc.value.errors) == [field]
    session.rollback()
    assert session.scalars(select(Application)).all() == [] and session.scalars(select(Job)).all() == []


@pytest.mark.parametrize("name, data", [("", b""), ("cv.txt", b"text"), ("cv.pdf", b"")])
def test_a_missing_or_unsupported_resume_is_a_form_error(session, name, data):
    with pytest.raises(intake.IntakeError) as exc:
        intake.submit_application(session, _opening(session), _form(), name, data)
    assert list(exc.value.errors) == ["resume"]


def test_a_closed_opening_refuses_applications(session):
    o = _opening(session)
    intake.close_opening(session, o)
    with pytest.raises(intake.IntakeError) as exc:
        intake.submit_application(session, o, _form(), "cv.docx", _docx())
    assert "opening" in exc.value.errors


# --- duplicates --------------------------------------------------------------


def test_the_same_email_cannot_apply_twice_to_one_opening(session):
    o = _opening(session)
    first = intake.submit_application(session, o, _form(), "cv.docx", _docx())
    session.commit()
    with pytest.raises(intake.IntakeError) as exc:
        intake.submit_application(session, o, _form(email="Sample.Exampleton@Example.org"), "cv2.docx", _docx())
    assert first.reference in exc.value.errors["email"]


def test_the_same_person_applying_to_two_openings_is_one_candidate(session):
    a = intake.submit_application(session, _opening(session), _form(), "cv.docx", _docx())
    b = intake.submit_application(session, _opening(session, designation="ASSOCIATE_PROFESSOR"), _form(), "cv.docx", _docx())
    session.commit()
    assert a.candidate_id == b.candidate_id and a.application_id != b.application_id
    assert len(session.scalars(select(Candidate)).all()) == 1


def test_an_uploaded_resume_whose_email_matches_an_existing_candidate_is_flagged_not_merged(session):
    o = _opening(session)
    applied = intake.submit_application(session, o, _form(), "cv.docx", _docx())
    uploaded = intake.hr_upload(session, o, [("from_hr.docx", _docx() + b" ")])[0].application
    session.commit()
    jobs.run_due_jobs(session, FakeProvider(script=[_clean_result(), _clean_result()]), None)

    assert uploaded.possible_duplicate_candidate_id == applied.candidate_id
    assert uploaded.candidate_id != applied.candidate_id  # a person decides; nothing is merged
    assert applied.possible_duplicate_candidate_id is None


# --- HR upload ---------------------------------------------------------------


def test_hr_upload_takes_many_files_and_reports_the_ones_it_cannot_accept(session):
    o = _opening(session)
    one, two = _docx(), _docx() + b" "
    outcomes = intake.hr_upload(session, o, [("a.docx", one), ("notes.txt", b"x"), ("b.docx", two), ("a_again.docx", one)])
    session.commit()
    assert [bool(u.application) for u in outcomes] == [True, False, True, False]
    assert "unsupported file type" in outcomes[1].problem
    assert "already uploaded" in outcomes[3].problem and outcomes[0].application.reference in outcomes[3].problem
    apps = session.scalars(select(Application)).all()
    assert len(apps) == 2 and {a.resume_source for a in apps} == {"MANUAL_UPLOAD"}
    assert all(a.category is None and a.applicant_email is None for a in apps)  # no form, so no form answers
    assert len(session.scalars(select(Job)).all()) == 2


# --- the reading queue -------------------------------------------------------


def _queued(session):
    a = intake.submit_application(session, _opening(session), _form(), "cv.docx", _docx())
    session.commit()
    return a, session.scalars(select(Job)).one()


def test_running_the_queue_reads_the_application(session):
    a, job = _queued(session)
    ran = jobs.run_due_jobs(session, FakeProvider(script=[_clean_result()]), None, now=NOW + timedelta(days=1))
    assert [j.job_id for j in ran] == [job.job_id]
    assert (job.status, job.attempts, a.status) == (jobs.DONE, 1, states.EXTRACTED)
    assert job.note == "application is EXTRACTED" and job.finished_at is not None


def test_form_answers_outrank_what_was_read_from_the_resume(session):
    a, _ = _queued(session)
    result = _clean_result()
    result.candidate_name.value, result.email, result.phone = "Someone Else", "other@example.org", "+91 11111 11111"
    jobs.run_due_jobs(session, FakeProvider(script=[result]), None, now=NOW + timedelta(days=1))
    personal = session.get(CandidatePersonalDetails, a.candidate_id)
    assert personal.full_name == "Sample Exampleton" and personal.contact_email == "sample.exampleton@example.org"
    assert personal.contact_phone == "+91 98765 00000" and personal.state == "Maharashtra"


def test_nothing_runs_before_its_time(session):
    _, job = _queued(session)
    job.run_after = NOW + timedelta(hours=1)
    session.commit()
    provider = FakeProvider()
    assert jobs.run_due_jobs(session, provider, None, now=NOW) == [] and provider.calls == 0
    assert jobs.due_count(session, now=NOW) == 0 and jobs.due_count(session, now=NOW + timedelta(hours=2)) == 1


@pytest.mark.parametrize("kind, wait", [("api_unavailable", timedelta(minutes=2)), ("quota", timedelta(hours=1))])
def test_an_unavailable_model_reschedules_the_job_and_keeps_the_application_waiting(session, kind, wait):
    a, job = _queued(session)
    jobs.run_due_jobs(session, FakeProvider(script=[ExtractionFailure("down", kind=kind)]), None, now=NOW)
    assert (job.status, job.attempts, a.status) == (jobs.PENDING, 1, states.RECEIVED)
    assert jobs._aware(job.run_after) == NOW + wait and kind in job.note

    # and it is picked up again once the wait has passed
    jobs.run_due_jobs(session, FakeProvider(script=[_clean_result()]), None, now=NOW + wait + timedelta(seconds=1))
    assert (job.status, job.attempts, a.status) == (jobs.DONE, 2, states.EXTRACTED)


def test_retries_back_off_and_stop_after_the_limit(session):
    assert [jobs.retry_delay(n, "reader_unavailable: api_unavailable") for n in (1, 2, 3, 6, 9)] == [
        timedelta(minutes=2), timedelta(minutes=4), timedelta(minutes=8), timedelta(minutes=60), timedelta(minutes=60)]
    a, job = _queued(session)
    when = NOW
    for _ in range(jobs.MAX_ATTEMPTS):
        jobs.run_due_jobs(session, FakeProvider(script=[ExtractionFailure("down", kind="api_unavailable")]), None, now=when)
        when += timedelta(hours=2)
    assert (job.status, job.attempts) == (jobs.FAILED, jobs.MAX_ATTEMPTS) and "gave up" in job.note
    assert a.status == states.RECEIVED  # the file is fine; a person can queue it again
    assert jobs.run_due_jobs(session, FakeProvider(), None, now=when) == []


def test_a_scanned_resume_finishes_its_job_and_fails_the_application(session):
    o = _opening(session)
    a = intake.hr_upload(session, o, [("scan.pdf", _scanned_pdf())])[0].application
    session.commit()
    provider = FakeProvider()
    jobs.run_due_jobs(session, provider, None, now=NOW + timedelta(days=1))
    job = session.scalars(select(Job)).one()
    assert (job.status, a.status, provider.calls) == (jobs.DONE, states.FAILED, 0)


def test_an_application_is_never_queued_twice(session):
    a, job = _queued(session)
    assert jobs.enqueue_read(session, a).job_id == job.job_id
    assert len(session.scalars(select(Job)).all()) == 1


def test_a_job_for_an_application_already_read_does_nothing(session):
    from backend.reader_service import read_application

    a, job = _queued(session)
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    session.commit()
    provider = FakeProvider()
    jobs.run_due_jobs(session, provider, None, now=NOW + timedelta(days=1))
    assert job.status == jobs.DONE and "skipped" in job.note and provider.calls == 0


# --- the pages ---------------------------------------------------------------


def _create_opening_via_page(client, **kw) -> int:
    data = {"school_id": "SCH-008", "designation": "ASSISTANT_PROFESSOR", "discipline_group": "ENGINEERING_TECHNOLOGY",
            "department_name": "Computer Engineering", "title": "Data Science", "closing_date": "2030-12-31"}
    data.update(kw)
    r = client.post("/hr/openings", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    return int(r.headers["location"].split("/hr/openings/")[1].split("?")[0])


def test_hr_creates_an_opening_and_an_applicant_applies_through_the_pages(client, engine):
    assert "No openings yet" in client.get("/").text
    opening_id = _create_opening_via_page(client)

    listing = client.get("/apply").text
    assert "Assistant Professor" in listing and "Data Science" in listing and "31-12-2030" in listing

    form = client.get(f"/apply/{opening_id}").text
    for needle in ("Full name", "Category", "study leave", "Resume (PDF or DOCX", "MIT School of Computing"):
        assert needle in form

    done = client.post(f"/apply/{opening_id}", data=_data(), files={"resume": ("cv.docx", _docx(), DOCX)})
    assert done.status_code == 201 and "Application received" in done.text and "APP-000001" in done.text

    home = client.get("/").text
    assert "1</strong> application(s) waiting to be read" in home and f"OPN-{opening_id:05d}" in home

    detail = client.get(f"/hr/openings/{opening_id}").text
    assert "Sample Exampleton" in detail and "Received, waiting to be read" in detail and "OBC-NCL" in detail
    assert "AICTE: Engineering / Technology" in detail


def test_form_errors_come_back_on_the_form_with_the_answers_kept(client, engine):
    opening_id = _create_opening_via_page(client)
    r = client.post(f"/apply/{opening_id}", data=_data(email="nope", state=""), files={"resume": ("cv.docx", _docx(), DOCX)})
    assert r.status_code == 422
    assert "Enter a valid email address." in r.text and "Choose your state from the list." in r.text
    assert 'value="Sample Exampleton"' in r.text  # what was typed is not lost
    assert "Nothing has been submitted yet" in r.text
    with Session(engine) as s:
        assert s.scalars(select(Application)).all() == []


def test_a_submission_without_a_file_is_a_form_error_not_a_crash(client):
    opening_id = _create_opening_via_page(client)
    r = client.post(f"/apply/{opening_id}", data=_data())
    assert r.status_code == 422 and "Attach your resume as a PDF or DOCX file." in r.text


def test_what_an_applicant_types_is_escaped_when_shown_back(client):
    opening_id = _create_opening_via_page(client)
    r = client.post(f"/apply/{opening_id}", data=_data(full_name='<script>alert(1)</script>', email="x"),
                    files={"resume": ("cv.docx", _docx(), DOCX)})
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_hr_uploads_several_files_and_processes_the_queue(client, engine):
    opening_id = _create_opening_via_page(client)
    r = client.post(f"/hr/openings/{opening_id}/upload", files=[
        ("resumes", ("a.docx", _docx(), DOCX)), ("resumes", ("scan.pdf", _scanned_pdf(), "application/pdf")),
        ("resumes", ("notes.txt", b"x", "text/plain")),
    ])
    assert r.status_code == 200 and "2 of 3 file(s) added." in r.text and "not added, unsupported file type" in r.text

    run = client.post("/hr/queue/run", data={"back": f"/hr/openings/{opening_id}", "limit": "12"}, follow_redirects=False)
    assert run.status_code == 303 and run.headers["location"].startswith(f"/hr/openings/{opening_id}?msg=")
    # the button answers at once; the reading itself happens after the response
    from urllib.parse import unquote

    from backend import web

    assert unquote(run.headers["location"].split("msg=")[1]).startswith("Reading 2 application(s) now.")
    # and what it did to the applications is reported: one read, one unreadable
    assert web.last_run["summary"] == "Read 1 application(s). 1 could not be read; see Needs attention."
    assert not web._run_lock.locked()

    detail = client.get(f"/hr/openings/{opening_id}").text
    assert "Could not be read" in detail and "Scanned or image-only, review manually" in detail
    assert "pymupdf" not in detail  # the library detail is for the audit trail, not for HR's screen
    home = client.get("/").text
    assert "Needs attention" in home and "the resume could not be read" in home


def test_the_queue_button_only_redirects_within_the_site(client):
    r = client.post("/hr/queue/run", data={"back": "https://example.org/x"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?msg=")
    r = client.post("/hr/queue/run", data={"back": "//example.org/x"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?msg=")


def test_a_closed_opening_disappears_from_the_applicant_list_and_refuses_the_form(client):
    opening_id = _create_opening_via_page(client)
    assert client.post(f"/hr/openings/{opening_id}/close", follow_redirects=False).status_code == 303
    assert "There are no openings accepting applications" in client.get("/apply").text
    assert "no longer accepting applications" in client.get(f"/apply/{opening_id}").text
    r = client.post(f"/apply/{opening_id}", data=_data(), files={"resume": ("cv.docx", _docx(), DOCX)})
    assert r.status_code == 422


def test_an_invalid_opening_form_is_shown_again_with_its_errors(client):
    r = client.post("/hr/openings", data={"school_id": "SCH-008", "designation": "", "discipline_group": "", "closing_date": "soon"})
    assert r.status_code == 422 and "Enter a valid date." in r.text


def test_unknown_openings_are_404(client):
    assert client.get("/apply/4242").status_code == 404 and client.get("/hr/openings/4242").status_code == 404


# --- reading runs in the background -----------------------------------------


def test_a_job_interrupted_mid_read_is_put_back_in_the_queue(session):
    """If the server stops while a resume is being read, the job is RUNNING and
    the application PARSING forever unless something releases them."""
    a, job = _queued(session)
    states.transition(session, a, states.PARSING, "agent:reader")
    job.status, job.run_after = jobs.RUNNING, NOW
    session.commit()

    assert jobs.release_stale_jobs(session, now=NOW + timedelta(minutes=5)) == 0  # still plausibly running
    assert jobs.release_stale_jobs(session, now=NOW + timedelta(minutes=30)) == 1
    assert (job.status, a.status) == (jobs.PENDING, states.RECEIVED)
    assert a.transitions[-1].note == "reader_interrupted"

    jobs.run_due_jobs(session, FakeProvider(script=[_clean_result()]), None, now=NOW + timedelta(minutes=31))
    assert a.status == states.EXTRACTED


def test_a_second_press_while_reading_does_not_start_a_second_run(client):
    from urllib.parse import unquote

    from backend import web

    opening_id = _create_opening_via_page(client)
    client.post(f"/hr/openings/{opening_id}/upload", files=[("resumes", ("a.docx", _docx(), DOCX))])
    assert web._run_lock.acquire(blocking=False)  # as if a run were under way
    try:
        r = client.post("/hr/queue/run", data={"back": "/"}, follow_redirects=False)
        assert "already in progress" in unquote(r.headers["location"])
        home = client.get("/").text
        assert "Reading in progress" in home and 'http-equiv="refresh"' in home
        assert "Process queue" not in home  # the button is not offered while a run is under way
    finally:
        web._run_lock.release()
    assert 'http-equiv="refresh"' not in client.get("/").text


def test_the_queue_button_says_so_when_nothing_is_waiting(client):
    from urllib.parse import unquote

    r = client.post("/hr/queue/run", data={"back": "/"}, follow_redirects=False)
    assert unquote(r.headers["location"]) == "/?msg=Nothing was waiting to be read."
