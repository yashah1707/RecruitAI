"""Phase 9: accounts, signing in, and what each account may see and do.

Made-up people and made-up passwords. SQLite by default; PostgreSQL when TEST_DATABASE_URL is set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import access, emails, gate2, states
from backend.assessor_service import assess_application
from backend.main import app
from backend.models import Application, HrDecision, JobOpening, User, UserSession
from backend.rules_seed import seed_rules
from backend.web import current_user
from tests.test_backend_foundation import client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_gate2 import TODAY, _assessed, _other_person
from tests.test_backend_intake import _opening, _storage  # noqa: F401

PASSWORD = "made-up-pass-phrase-1"
OTHER_PASSWORD = "another-made-up-phrase-2"
COMPUTING, SCIENCE = "SCH-008", "SCH-013"


@pytest.fixture
def session(_plain_session):
    seed_rules(_plain_session)
    _plain_session.commit()
    return _plain_session


@pytest.fixture
def web(client):
    """The pages with nobody signed in: the stand-in administrator the other tests use is taken away."""
    app.dependency_overrides.pop(current_user)
    return client


def _account(session, name, user_type, grants=(), password=PASSWORD, must_change=False) -> User:
    session.expire_on_commit = False  # so names and addresses can still be read once the session is closed
    user = access.create_user(session, name=name, email=f"{name.lower().replace(' ', '.')}@example.org", password=password,
                              user_type=user_type, must_change_password=must_change)
    for school_id, department_id, level in grants:
        access.grant(session, user, school_id, department_id, level)
    session.commit()
    return user


def _sign_in(web, user, password=PASSWORD):
    web.cookies.clear()
    r = web.post("/login", data={"email": user.email, "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    return r


# --- passwords and sessions --------------------------------------------------


def test_a_password_is_stored_only_as_a_hash_that_checks_it():
    stored = access.hash_password(PASSWORD)
    assert PASSWORD not in stored and stored.startswith("scrypt$") and stored != access.hash_password(PASSWORD)  # salted
    assert access.verify_password(PASSWORD, stored) and not access.verify_password(PASSWORD + "x", stored)
    assert not access.verify_password(PASSWORD, "not-a-hash") and not access.verify_password(PASSWORD, "md5$1$2$3$4$5")


def test_signing_in_gives_a_session_and_the_database_holds_only_its_hash(session):
    user = _account(session, "Asha Admin", "UNIVERSITY_ADMIN")
    token = access.sign_in(session, "  ASHA.ADMIN@example.org ", PASSWORD)
    row = session.scalars(select(UserSession)).one()
    assert row.token_hash != token and len(row.token_hash) == 64
    assert access.user_for_token(session, token).user_id == user.user_id
    assert access.user_for_token(session, "made-up-token") is None and access.user_for_token(session, None) is None
    # Idle for longer than the limit: the session is over, and is removed.
    later = datetime.now(timezone.utc) + access.SESSION_IDLE + timedelta(minutes=1)
    assert access.user_for_token(session, token, now=later) is None and session.scalars(select(UserSession)).all() == []


def test_a_wrong_address_and_a_wrong_password_are_answered_alike_and_five_wrong_ones_lock_the_account(session):
    _account(session, "Asha Admin", "UNIVERSITY_ADMIN")
    messages = set()
    for email, password in (("nobody@example.org", PASSWORD), ("asha.admin@example.org", "wrong-password-0")):
        with pytest.raises(access.AccessError) as exc:
            access.sign_in(session, email, password)
        messages.add(str(exc.value))
    assert messages == {"The email address or the password is not right."}
    for _ in range(access.MAX_FAILED_LOGINS - 1):
        with pytest.raises(access.AccessError):
            access.sign_in(session, "asha.admin@example.org", "wrong-password-0")
    with pytest.raises(access.AccessError, match="Too many wrong passwords"):
        access.sign_in(session, "asha.admin@example.org", PASSWORD)  # even the right one, while it is locked
    after = datetime.now(timezone.utc) + access.LOCKED_FOR + timedelta(minutes=1)
    assert access.sign_in(session, "asha.admin@example.org", PASSWORD, now=after)


def test_a_new_password_has_to_be_long_enough_and_different(session):
    user = _account(session, "Asha Admin", "UNIVERSITY_ADMIN")
    for current, new, message in ((OTHER_PASSWORD, OTHER_PASSWORD + "x", "current password is not right"),
                                  (PASSWORD, "short", "at least 10 characters"), (PASSWORD, PASSWORD, "different from the current"),
                                  (PASSWORD, user.email, "must not be the email address")):
        with pytest.raises(access.AccessError, match=message):
            access.change_own_password(session, user, current, new)
    kept, dropped = access.sign_in(session, user.email, PASSWORD), access.sign_in(session, user.email, PASSWORD)
    access.change_own_password(session, user, PASSWORD, OTHER_PASSWORD, keep_token=kept)
    assert access.user_for_token(session, kept) is not None and access.user_for_token(session, dropped) is None
    assert access.sign_in(session, user.email, OTHER_PASSWORD)


# --- what a grant covers -----------------------------------------------------


def test_a_grant_covers_its_school_or_only_its_department_at_its_level():
    school_hr = access.Principal(1, "S", "SCHOOL_HR", ((COMPUTING, None, "VIEW_EDIT"),))
    department_hr = access.Principal(2, "D", "DEPARTMENT_HR", ((COMPUTING, 7, "APPROVE"),))
    assert school_hr.may("VIEW_EDIT", COMPUTING, None) and school_hr.may("VIEW", COMPUTING, 7) and not school_hr.may("APPROVE", COMPUTING, 7)
    assert not school_hr.may("VIEW", SCIENCE, None)
    assert department_hr.may("APPROVE", COMPUTING, 7) and not department_hr.may("VIEW", COMPUTING, 8) and not department_hr.may("VIEW", COMPUTING, None)
    assert school_hr.schools_for_openings() == {COMPUTING} and department_hr.schools_for_openings() == set()
    admin = access.Principal(3, "A", "HR_ADMIN")
    assert admin.may("APPROVE", SCIENCE, 99) and admin.schools_for_openings() is None and not admin.manages_accounts
    interviewer = access.Principal(4, "I", "INTERVIEWER", ((COMPUTING, None, "APPROVE"),))
    assert not interviewer.may("VIEW", COMPUTING, None) and not interviewer.may_anywhere("VIEW")


def test_the_query_filter_returns_only_the_openings_an_account_is_granted(session):
    computing = _opening(session, school_id=COMPUTING, department_name="Computer Engineering")
    other_department = _opening(session, school_id=COMPUTING, department_name="Information Technology")
    science = _opening(session, school_id=SCIENCE, discipline_group="GENERAL")

    def seen(principal):
        return set(session.scalars(select(JobOpening.opening_id).where(access.opening_filter(principal))))

    everything = {computing.opening_id, other_department.opening_id, science.opening_id}
    assert seen(access.Principal(1, "A", "UNIVERSITY_ADMIN")) == everything
    assert seen(access.Principal(2, "S", "SCHOOL_HR", ((COMPUTING, None, "VIEW"),))) == {computing.opening_id, other_department.opening_id}
    assert seen(access.Principal(3, "D", "DEPARTMENT_HR", ((COMPUTING, computing.department_id, "VIEW"),))) == {computing.opening_id}
    assert seen(access.Principal(4, "N", "SCHOOL_HR")) == set() and seen(access.Principal(5, "I", "INTERVIEWER", ((COMPUTING, None, "VIEW"),))) == set()


def test_grants_are_checked_and_the_last_university_administrator_cannot_be_closed_or_demoted(session):
    only_admin = _account(session, "Asha Admin", "UNIVERSITY_ADMIN")
    with pytest.raises(access.AccessError, match="only university administrator"):
        access.set_active(session, only_admin, False)
    with pytest.raises(access.AccessError, match="only university administrator"):
        access.set_user_type(session, only_admin, "HR_ADMIN")
    with pytest.raises(access.AccessError, match="already an account"):
        _account(session, "Asha Admin", "HR_ADMIN")
    dept = _opening(session, school_id=COMPUTING, department_name="Computer Engineering").department_id
    hr = _account(session, "Dev Department", "DEPARTMENT_HR")
    for args, message in (((COMPUTING, None, "VIEW"), "granted a department"), ((SCIENCE, dept, "VIEW"), "not in that school"),
                          (("SCH-999", None, "VIEW"), "Choose a school"), ((COMPUTING, dept, "OWNER"), "Choose a level")):
        with pytest.raises(access.AccessError, match=message):
            access.grant(session, hr, *args)
    access.grant(session, hr, COMPUTING, dept, "VIEW")
    access.grant(session, hr, COMPUTING, dept, "APPROVE")  # the same scope again replaces the level
    assert [(g.school_id, g.department_id, g.access_level) for g in hr.grants] == [(COMPUTING, dept, "APPROVE")]
    access.revoke(session, hr, hr.grants[0].id)
    assert hr.grants == []
    second = _account(session, "Bela Admin", "UNIVERSITY_ADMIN")
    token = access.sign_in(session, second.email, PASSWORD)
    access.set_active(session, second, False)  # allowed now that there are two; and it ends that account's sessions
    assert access.user_for_token(session, token) is None
    with pytest.raises(access.AccessError, match="not right"):
        access.sign_in(session, second.email, PASSWORD)


# --- the pages: setting up, signing in and out --------------------------------


def test_with_no_account_every_hr_page_leads_to_setup_and_setup_works_once(web, engine):
    assert web.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert web.get("/login", follow_redirects=False).headers["location"] == "/setup"
    assert "Set up RecruitAI" in web.get("/").text
    form = {"name": "Asha Admin", "email": "asha.admin@example.org", "password": PASSWORD, "again": PASSWORD}
    assert "The two passwords are not the same." in web.post("/setup", data={**form, "again": "something-else-9"}).text
    assert "at least 10 characters" in web.post("/setup", data={**form, "password": "short", "again": "short"}).text
    done = web.post("/setup", data=form, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"].startswith("/hr/openings?msg=")
    cookie = done.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and PASSWORD not in cookie
    home = web.get("/").text
    assert "Openings" in home and "Asha Admin" in home and 'href="/admin/users"' in home and "Sign out" in home
    with Session(engine) as s:
        (user,) = s.scalars(select(User)).all()
        assert (user.user_type, user.must_change_password) == ("UNIVERSITY_ADMIN", False) and PASSWORD not in user.password_hash
    # Once there is an account, setup is closed to everyone.
    web.cookies.clear()
    assert web.get("/setup", follow_redirects=False).headers["location"].startswith("/login")
    again = web.post("/setup", data={**form, "email": "intruder@example.org"}, follow_redirects=False)
    assert again.headers["location"].startswith("/login")
    with Session(engine) as s:
        assert len(s.scalars(select(User)).all()) == 1


def test_signing_in_and_out_through_the_pages(web, engine):
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
    asked = web.get("/hr/inbox?msg=hello", follow_redirects=False)
    assert asked.status_code == 303 and asked.headers["location"] == "/login?next=%2Fhr%2Finbox%3Fmsg%3Dhello"
    assert web.post("/hr/queue/run", follow_redirects=False).headers["location"].startswith("/login")  # nothing is done for no one
    wrong = web.post("/login", data={"email": admin.email, "password": "wrong-password-0"})
    assert wrong.history[0].status_code == 303 and "The email address or the password is not right." in wrong.text and "Sign out" not in wrong.text
    r = web.post("/login", data={"email": admin.email, "password": PASSWORD, "next": "/hr/inbox"}, follow_redirects=False)
    assert r.headers["location"] == "/hr/inbox"
    for elsewhere in ("https://example.org/", "//example.org", "inbox"):  # never sent off to another site after signing in
        web.cookies.clear()
        r = web.post("/login", data={"email": admin.email, "password": PASSWORD, "next": elsewhere}, follow_redirects=False)
        assert r.headers["location"] == "/"
    assert web.get("/").status_code == 200
    with Session(engine) as s:
        before = len(s.scalars(select(UserSession)).all())
    out = web.post("/logout")
    assert "You are signed out." in out.text
    assert web.get("/", follow_redirects=False).headers["location"].startswith("/login")
    with Session(engine) as s:
        assert len(s.scalars(select(UserSession)).all()) == before - 1  # this browser's session is gone from the server too


def test_the_application_form_and_the_health_check_need_no_account(web, engine):
    with Session(engine) as s:
        _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
        opening_id = _opening(s).opening_id
    assert web.get("/apply").status_code == 200 and web.get(f"/apply/{opening_id}").status_code == 200
    assert web.get("/health").json()["status"] == "ok" and web.get("/static/app.css").status_code == 200
    # The direct API to candidates' records is not public.
    assert web.get("/applications/1", follow_redirects=False).status_code == 303
    assert web.post("/applications", data={}, follow_redirects=False).headers["location"].startswith("/login")


def test_a_form_posted_from_another_site_is_refused(web, engine):
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
    _sign_in(web, admin)
    refused = web.post("/hr/inbox/check", headers={"origin": "https://elsewhere.example.org"})
    assert refused.status_code == 403 and "came from another site" in refused.text
    assert web.post("/hr/inbox/check", headers={"origin": "http://testserver"}).status_code == 200


def test_a_temporary_password_has_to_be_changed_before_anything_else(web, engine):
    with Session(engine) as s:
        hr = _account(s, "Hari HR", "HR_ADMIN", must_change=True)
    _sign_in(web, hr)
    assert web.get("/", follow_redirects=False).headers["location"] == "/account/password"
    page = web.get("/").text
    assert "Your password was set by an administrator" in page and 'href="/"' not in page.split("<main")[1]
    bad = web.post("/account/password", data={"current": PASSWORD, "new": OTHER_PASSWORD, "again": "not-the-same-thing"})
    assert bad.history[0].status_code == 303 and "The two new passwords are not the same." in bad.text
    done = web.post("/account/password", data={"current": PASSWORD, "new": OTHER_PASSWORD, "again": OTHER_PASSWORD})
    assert "Your password is changed." in done.text and "Openings" in done.text
    _sign_in(web, hr, OTHER_PASSWORD)
    assert web.get("/").status_code == 200


# --- the pages: what each account sees and may do -----------------------------


def _two_schools(engine, tmp_path):
    """One assessed application in a Science opening and one in a Computing opening. Returns their ids."""
    with Session(engine) as s:
        seed_rules(s)
        science = _assessed(s, tmp_path)
        computing = _assessed(s, tmp_path, _other_person(), school_id=COMPUTING, discipline_group="GENERAL")
        return {"science": (science.opening_id, science.application_id), "computing": (computing.opening_id, computing.application_id)}


def test_a_school_account_sees_only_its_school_and_the_rest_is_not_found(web, engine, tmp_path):
    ids = _two_schools(engine, tmp_path)
    (mine, my_app), (theirs, their_app) = ids["science"], ids["computing"]
    with Session(engine) as s:
        viewer = _account(s, "Vani Viewer", "SCHOOL_HR", [(SCIENCE, None, "VIEW")])
    _sign_in(web, viewer)
    home = web.get("/").text
    assert f"OPN-{mine:05d}" in home and f"OPN-{theirs:05d}" not in home
    assert 'href="/hr/inbox"' not in home and 'href="/admin/users"' not in home and 'href="/hr/openings/new"' not in home
    assert web.get(f"/hr/openings/{mine}").status_code == 200 and web.get(f"/hr/applications/{my_app}").status_code == 200
    for address in (f"/hr/openings/{theirs}", f"/hr/openings/{theirs}/emails", f"/hr/openings/{theirs}/digest",
                    f"/hr/openings/{theirs}/export.xlsx", f"/hr/applications/{their_app}", f"/hr/applications/{their_app}/resume",
                    f"/applications/{their_app}", f"/applications/{their_app}/transitions"):
        assert web.get(address).status_code == 404, address
    assert web.post(f"/hr/applications/{their_app}/approve").status_code == 404
    # View only: its own school's pages open, but nothing can be changed, and nothing was.
    for address in (f"/hr/openings/{mine}/assess", f"/hr/applications/{my_app}/withdraw", f"/hr/applications/{my_app}/approve",
                    f"/hr/openings/{mine}/close", "/hr/queue/run"):
        refused = web.post(address)
        assert refused.status_code == 403 and "Your account does not allow this" in refused.text, address
    for address in ("/hr/inbox", "/admin/users", "/hr/openings/new"):
        assert web.get(address).status_code == 403, address
    with Session(engine) as s:
        assert s.get(Application, my_app).status == states.SHORTLISTED and s.get(JobOpening, mine).status == "OPEN"


def test_editing_and_approving_are_separate_levels_and_each_action_is_recorded_against_the_person(web, engine, tmp_path):
    ids = _two_schools(engine, tmp_path)
    opening_id, app_id = ids["science"]
    with Session(engine) as s:
        editor = _account(s, "Esha Editor", "SCHOOL_HR", [(SCIENCE, None, "VIEW_EDIT")])
        approver = _account(s, "Anil Approver", "SCHOOL_HR", [(SCIENCE, None, "APPROVE")])
        editor_id, approver_id = editor.user_id, approver.user_id
    _sign_in(web, editor)
    for address in (f"/hr/applications/{app_id}/approve", f"/hr/applications/{app_id}/override", f"/hr/applications/{app_id}/return",
                    f"/hr/applications/{app_id}/email", f"/hr/openings/{opening_id}/emails", f"/hr/applications/{app_id}/reopen-decision"):
        assert web.post(address).status_code == 403, address
    assert web.post(f"/hr/openings/{opening_id}/assess").status_code == 200  # editing is allowed
    # An application cannot be moved into a school the account is not granted.
    moved = web.post(f"/hr/applications/{app_id}/move", data={"opening_id": str(ids["computing"][0])})
    assert "Choose an opening to move it to." in moved.text
    with Session(engine) as s:
        assert s.get(Application, app_id).opening_id == opening_id

    _sign_in(web, approver)
    done = web.post(f"/hr/applications/{app_id}/approve").text
    assert "Approved as assessed." in done and "Anil Approver" in done.split("Decisions at this gate")[1]
    with Session(engine) as s:
        a = s.get(Application, app_id)
        assert a.transitions[-1].actor == f"user:{approver_id}" != f"user:{editor_id}"
        assert s.scalars(select(HrDecision.actor).where(HrDecision.application_id == app_id)).all() == [f"user:{approver_id}"]
    sent = web.post(f"/hr/openings/{opening_id}/emails", data={"draft_id": "1"})
    assert sent.status_code == 200
    with Session(engine) as s:
        assert emails.active_draft(s, app_id).approved_by == f"user:{approver_id}"
    assert "Approved by" not in web.get(f"/hr/applications/{app_id}").text or "Anil Approver" in web.get(f"/hr/applications/{app_id}").text


def test_a_department_account_sees_its_department_and_a_school_account_may_open_posts_only_in_its_school(web, engine):
    with Session(engine) as s:
        mine = _opening(s, school_id=COMPUTING, department_name="Computer Engineering")
        other = _opening(s, school_id=COMPUTING, department_name="Information Technology")
        mine_id, other_id, dept = mine.opening_id, other.opening_id, mine.department_id
        department_hr = _account(s, "Dev Department", "DEPARTMENT_HR", [(COMPUTING, dept, "VIEW_EDIT")])
        school_hr = _account(s, "Sara School", "SCHOOL_HR", [(COMPUTING, None, "VIEW_EDIT")])
    _sign_in(web, department_hr)
    home = web.get("/").text
    assert f"OPN-{mine_id:05d}" in home and f"OPN-{other_id:05d}" not in home
    assert web.get(f"/hr/openings/{mine_id}").status_code == 200 and web.get(f"/hr/openings/{other_id}").status_code == 404
    assert web.get("/hr/openings/new").status_code == 403  # a department account does not open posts

    _sign_in(web, school_hr)
    form = web.get("/hr/openings/new").text
    assert "MIT School of Computing" in form and form.count("<option value=\"SCH-") == 1
    post = {"designation": "ASSISTANT_PROFESSOR", "discipline_group": "GENERAL"}
    refused = web.post("/hr/openings", data={**post, "school_id": SCIENCE})
    assert refused.history[0].status_code == 303 and "Choose a school from the list." in refused.text
    assert "Opening created." in web.post("/hr/openings", data={**post, "school_id": COMPUTING}).text


def test_only_a_university_administrator_manages_accounts_and_an_interviewer_has_no_hr_pages(web, engine):
    with Session(engine) as s:
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
        hr_admin = _account(s, "Hari HR", "HR_ADMIN")
        interviewer = _account(s, "Indu Interviewer", "INTERVIEWER", [(COMPUTING, None, "VIEW")])
        admin_id = admin.user_id
    _sign_in(web, hr_admin)
    assert web.get("/hr/inbox").status_code == 200 and web.get("/admin/users").status_code == 403
    assert web.post("/admin/users", data={"name": "X Y", "email": "x@example.org", "user_type": "HR_ADMIN", "password": PASSWORD}).status_code == 403
    _sign_in(web, interviewer)
    own = web.get("/")  # an interviewer's home is the panel's view (tests/test_backend_panel_policy_highlights.py)
    assert own.status_code == 200 and "Short-listed candidates" in own.text and "Sign out" in own.text
    refused = web.get("/hr/inbox")
    assert refused.status_code == 403 and "interviewer account" in refused.text
    assert web.get("/account/password").status_code == 200

    _sign_in(web, admin)
    page = web.get("/admin/users").text
    assert "Indu Interviewer" in page and "Every school." in page and "Add an account" in page
    made = web.post("/admin/users", data={"name": "Nita New", "email": "Nita.New@Example.org", "user_type": "SCHOOL_HR", "password": PASSWORD}).text
    assert "Account created." in made and "nita.new@example.org" in made and "Nothing yet: this account sees no applications." in made
    with Session(engine) as s:
        new = s.scalar(select(User).where(User.email == "nita.new@example.org"))
        new_id = new.user_id
        assert new.must_change_password and PASSWORD not in new.password_hash
    assert "Not done. There is already an account" in web.post(
        "/admin/users", data={"name": "Nita Again", "email": "nita.new@example.org", "user_type": "SCHOOL_HR", "password": PASSWORD}).text
    granted = web.post(f"/admin/users/{new_id}/grants", data={"school_id": COMPUTING, "department_id": "", "level": "APPROVE"}).text
    assert "Access granted." in granted and "MIT School of Computing: View, edit and approve" in granted
    with Session(engine) as s:
        grant_id = s.get(User, new_id).grants[0].id
    assert "Access removed." in web.post(f"/admin/users/{new_id}/grants/{grant_id}/remove").text
    assert "Temporary password set." in web.post(f"/admin/users/{new_id}/update", data={"do": "password", "password": OTHER_PASSWORD}).text
    assert "Not done. Choose a password of at least 10" in web.post(f"/admin/users/{new_id}/update", data={"do": "password", "password": "short"}).text
    assert "Account closed." in web.post(f"/admin/users/{new_id}/update", data={"do": "close"}).text
    assert "Account opened again." in web.post(f"/admin/users/{new_id}/update", data={"do": "open"}).text
    assert "Kind of account changed." in web.post(f"/admin/users/{new_id}/update", data={"do": "type", "user_type": "HR_ADMIN"}).text
    # One's own account is not closed or changed by oneself.
    assert "Ask another university administrator" in web.post(f"/admin/users/{admin_id}/update", data={"do": "close"}).text
    assert web.post("/admin/users/999999/update", data={"do": "close"}).status_code == 404


def test_closing_an_account_signs_it_out_at_once(web, engine):
    with Session(engine) as s:
        hr = _account(s, "Hari HR", "HR_ADMIN")
        hr_id = hr.user_id
    _sign_in(web, hr)
    assert web.get("/").status_code == 200
    with Session(engine) as s:
        _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
        access.set_active(s, s.get(User, hr_id), False)
        s.commit()
    assert web.get("/", follow_redirects=False).headers["location"].startswith("/login")
    assert web.post("/login", data={"email": hr.email, "password": PASSWORD}).history[0].status_code == 303


# --- reopening a decision ----------------------------------------------------


def test_a_decision_not_yet_sent_can_be_reopened_with_a_reason_and_is_then_decided_again(session, tmp_path):
    a = _assessed(session, tmp_path)
    gate2.approve(session, a)
    letter = emails.active_draft(session, a.application_id)
    for reason, field in (("", "reason"), ("too short", "reason"), ("x" * 1001, "reason")):
        with pytest.raises(gate2.DecisionError) as exc:
            gate2.reopen_decision(session, a, reason)
        assert field in exc.value.errors
    gate2.reopen_decision(session, a, "The NET certificate turned out to be for another person.", actor="user:7")
    assert a.status == states.EXTRACTED and a.transitions[-1].note == "gate2: decision reopened with a recorded reason"
    assert letter.status == "DISCARDED" and emails.active_draft(session, a.application_id) is None
    kept = gate2.decisions(session, a.application_id)
    assert [(d.action, d.actor) for d in kept] == [("APPROVED", "user:hr"), ("REOPENED", "user:7")]
    assert kept[-1].justification.startswith("The NET certificate") and gate2.final_decision(session, a) is None
    assess_application(session, a, TODAY)
    again = gate2.override(session, a, "NOT_ELIGIBLE", None, "NET certificate not the applicant's own.")
    assert gate2.final_decision(session, a) is again and emails.active_draft(session, a.application_id).status == "DRAFT"


def test_a_decision_the_candidate_has_been_told_of_cannot_be_reopened(session, tmp_path):
    from tests.test_backend_reporting import Outbox

    a = _assessed(session, tmp_path)
    with pytest.raises(gate2.DecisionError, match="not yet been informed"):
        gate2.reopen_decision(session, a, "There is no decision yet to reopen.")
    gate2.approve(session, a)
    emails.approve_and_send(session, [emails.active_draft(session, a.application_id)], Outbox())
    assert a.status == states.CONTACTED
    with pytest.raises(gate2.DecisionError, match="not yet been informed"):
        gate2.reopen_decision(session, a, "Too late: the letter has gone to the candidate.")


def test_reopening_from_the_application_page(client, engine, tmp_path):
    with Session(engine) as s:
        seed_rules(s)
        a = _assessed(s, tmp_path)
        app_id = a.application_id
    assert "Reopen this decision" not in client.get(f"/hr/applications/{app_id}").text
    assert "Reopen this decision" in client.post(f"/hr/applications/{app_id}/approve").text
    assert "Say why the decision is reopened" in client.post(f"/hr/applications/{app_id}/reopen-decision", data={"reason": "oops"}).text
    done = client.post(f"/hr/applications/{app_id}/reopen-decision", data={"reason": "Approved by mistake before the documents were seen."}).text
    assert "The decision is reopened and its letter dropped." in done and "Decision reopened" in done
    assert "Approved by mistake before the documents were seen." in done and "An earlier assessment was sent back" in done
