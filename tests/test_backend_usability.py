"""Fixes from the user-testing pass of 2026-11-14: what a person typed survives a refusal, refusals look like
refusals, view-only accounts are not asked to approve, and the assessment never contradicts itself.

Made-up data only; fake reader; no mail.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend import access, gate2
from backend.engine.decision import FAIL, PASS, UNKNOWN, decide
from backend.engine.facts import Degree
from backend.engine.rules import load_rules
from backend.main import app
from backend.models import JobOpening
from backend.web import current_user
from tests.test_backend_access import PASSWORD, _account, _sign_in
from tests.test_backend_engine import _facts
from tests.test_backend_foundation import _docx, client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_gate2 import _assessed, session  # noqa: F401
from tests.test_backend_intake import DOCX, _data, _opening, _storage  # noqa: F401


# --- 1. adding an account: refused, the form comes back open with what was typed; refusals are red ---------------


def test_a_refused_account_comes_back_open_with_what_was_typed_but_never_the_password(client, session):
    _account(session, "Already There", "INTERVIEWER")
    r = client.post("/admin/users", data={"name": "Mistake Test", "email": "already.there@example.org",
                                          "user_type": "INTERVIEWER", "password": "a-made-up-pass-123"})
    assert r.status_code == 200
    page = r.text
    assert 'id="add-account"' in page and "data-open-now" in page
    assert "Not created. There is already an account with this email address." in page
    assert 'value="Mistake Test"' in page and 'value="already.there@example.org"' in page
    assert '<option value="INTERVIEWER" selected>' in page
    assert "a-made-up-pass-123" not in page and "Type the temporary password again" in page
    again = client.get("/admin/users").text  # read once: a later visit shows a fresh form
    assert "data-open-now" not in again and "Mistake Test" not in again


def test_a_refusal_is_shown_as_a_refusal_and_success_as_success(client):
    refused = client.get("/admin/users", params={"msg": "Not done. Something was wrong."}).text
    assert 'class="notice failed" role="alert"' in refused
    by_flag = client.get("/admin/users", params={"msg": "Give the reason.", "failed": "1"}).text
    assert 'class="notice failed" role="alert"' in by_flag
    fine = client.get("/admin/users", params={"msg": "Account created."}).text
    assert 'class="notice" role="status"' in fine and "notice failed" not in fine


# --- 2. a refused decision or reopening keeps what was typed ----------------------------------------------------


def test_a_refused_override_keeps_the_decision_and_the_reason_typed(client, session, tmp_path):
    a = _assessed(session, tmp_path)
    r = client.post(f"/hr/applications/{a.application_id}/override",
                    data={"outcome": "NOT_ELIGIBLE", "designation": "", "justification": "too short"})
    page = r.text
    assert "notice failed" in page and "at least 15 characters" in page
    assert ">too short</textarea>" in page
    assert '<option value="NOT_ELIGIBLE" selected>' in page
    assert client.get(f"/hr/applications/{a.application_id}").text.count(">too short</textarea>") == 0  # read once


def test_a_refused_reopening_keeps_the_reason_typed(client, session, tmp_path):
    a = _assessed(session, tmp_path)
    gate2.approve(session, a)
    session.commit()
    page = client.post(f"/hr/applications/{a.application_id}/reopen-decision", data={"reason": "brief"}).text
    assert "notice failed" in page and ">brief</textarea>" in page


# --- 3. a view-only account is not asked to approve -----------------------------------------------------------


def test_a_view_only_account_is_told_letters_wait_for_approval_not_asked_to_approve_them(client, session, tmp_path):
    a = _assessed(session, tmp_path)
    gate2.approve(session, a)  # drafts a letter waiting for approval
    session.commit()
    opening = session.get(JobOpening, a.opening_id)
    viewer = _account(session, "Only Looks", "SCHOOL_HR", grants=[(opening.school_id, None, access.VIEW)])
    approver = _account(session, "May Approve", "SCHOOL_HR", grants=[(opening.school_id, None, access.APPROVE)])
    app.dependency_overrides.pop(current_user)
    _sign_in(client, viewer, PASSWORD)
    detail, home = client.get(f"/hr/openings/{opening.opening_id}").text, client.get("/hr/openings").text
    assert "waiting for approval." in detail and ">Read them<" in detail and "Read and approve" not in detail
    assert "Emails waiting for approval" in home and ">Read them<" in home and "Read and approve" not in home
    assert "until you approve" not in detail and "until you approve" not in home
    assert "until you approve" not in client.get(f"/hr/applications/{a.application_id}").text
    _sign_in(client, approver, PASSWORD)
    detail, home = client.get(f"/hr/openings/{opening.opening_id}").text, client.get("/hr/openings").text
    assert "waiting for your approval." in detail and "Read and approve them" in detail
    assert "Emails waiting for your approval" in home and "Read and approve" in home


# --- 4. the assessment never says a degree is held and not on the record ---------------------------------------


def _first_class(d):
    return next(c for r in d.ranks for c in r.checks if c.key == "first_class")


def test_a_degree_held_but_not_listed_is_judged_on_the_masters_figures_the_record_gives(session):
    rules = load_rules(session, "ENGINEERING_TECHNOLOGY")
    held = _facts(discipline_group="ENGINEERING_TECHNOLOGY", highest_degree="PG", degrees=[], masters_cgpa=8.4)
    d = decide(held, rules)
    first = _first_class(d)
    assert first.result == PASS and "CGPA 8.4 against 6.75" in first.detail
    assert "not on the record" not in first.detail

    unstated = _facts(discipline_group="ENGINEERING_TECHNOLOGY", highest_degree="PG", degrees=[], masters_marks_pct=None)
    first = _first_class(decide(unstated, rules))
    assert first.result == UNKNOWN and "neither a class nor marks are stated" in first.detail
    assert "not on the record" not in first.detail
    holds = [c for r in decide(unstated, rules).ranks for c in r.checks if c.key.startswith("holds_")]
    assert holds and all(c.result == PASS for c in holds)

    low = _facts(discipline_group="ENGINEERING_TECHNOLOGY", highest_degree="PG", degrees=[], masters_marks_pct=52)
    assert _first_class(decide(low, rules)).result in (UNKNOWN, FAIL)  # the Bachelor's is still unknown


def test_with_no_qualifications_on_record_the_page_still_says_so(session):
    rules = load_rules(session, "ENGINEERING_TECHNOLOGY")
    nothing = _facts(discipline_group="ENGINEERING_TECHNOLOGY", highest_degree=None, degrees=[], masters_marks_pct=None)
    first = _first_class(decide(nothing, rules))
    assert first.result == UNKNOWN and "no Master's degree is on the record" in first.detail
    one = _facts(discipline_group="ENGINEERING_TECHNOLOGY", highest_degree="PG",
                 degrees=[Degree("UG", name="B.E.", marks_pct=65)])
    assert _first_class(decide(one, rules)).result == PASS


# --- 5. the application form says the resume has to be chosen again ------------------------------------------


def test_after_a_refused_application_the_form_asks_for_the_resume_again(client, session):
    o = _opening(session)
    page = client.post(f"/apply/{o.opening_id}", data=_data(declaration=None),
                       files={"resume": ("cv.docx", _docx(), DOCX)}).text
    assert "Please confirm the declaration." in page and "Please choose your resume again" in page
    wrong = client.post(f"/apply/{o.opening_id}", data=_data(), files={"resume": ("cv.txt", b"x", "text/plain")}).text
    assert "Unsupported file type" in wrong and "Please choose your resume again" not in wrong  # its own reason says it


# --- 6. a new opening shows every mistake at once ------------------------------------------------------------


def test_a_new_opening_with_several_mistakes_shows_them_all_at_once(client):
    page = client.post("/hr/openings", data={"school_id": "", "designation": "", "discipline_group": ""}).text
    for message in ("Choose a school from the list.", "Choose a designation from the list.",
                    "Choose a discipline group from the list."):
        assert message in page, message
