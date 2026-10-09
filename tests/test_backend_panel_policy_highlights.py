"""Phase 9, part 2: pages that offer only what an account may do, a forgotten password, the interview
panel's view, highlights, the university's own criteria, and the dashboard.

Made-up people and passwords. No mail server is contacted. SQLite by default; PostgreSQL when TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import access, emails, gate2, highlights, policy, reporting, settings, states, views
from backend.assessor_service import latest_evaluation
from backend.models import (
    Application,
    CandidateQualification,
    CandidateResearchProfile,
    InstitutionMaster,
    PasswordReset,
    UniversityPolicyRule,
    ViewFieldVisibility,
)
from backend.rules_seed import seed_rules
from llm.interface import EducationEntry, PublicationEntry, ResearchProfile
from tests.test_backend_access import COMPUTING, OTHER_PASSWORD, PASSWORD, SCIENCE, _account, _sign_in, _two_schools, web  # noqa: F401
from tests.test_backend_foundation import _clean_result, client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_gate2 import _assessed, _no_net, _other_person
from tests.test_backend_intake import _storage  # noqa: F401


@pytest.fixture
def session(_plain_session):
    seed_rules(_plain_session)
    _plain_session.commit()
    return _plain_session


def _no_net_other():
    r = _other_person()
    r.net_set_status.value = "NONE"
    return r


def _researcher(h_index=None, citations=None, papers=()):
    r = _other_person()
    r.research_profile = ResearchProfile(h_index=h_index, total_citations=citations)
    r.publications = list(papers) or r.publications
    return r


# --- a page offers only what the account may do ---------------------------------


def test_each_level_is_offered_only_its_own_actions(web, engine, tmp_path):
    opening_id, app_id = _two_schools(engine, tmp_path)["science"]
    with Session(engine) as s:
        viewer = _account(s, "Vani Viewer", "SCHOOL_HR", [(SCIENCE, None, "VIEW")])
        editor = _account(s, "Esha Editor", "SCHOOL_HR", [(SCIENCE, None, "VIEW_EDIT")])
        approver = _account(s, "Anil Approver", "SCHOOL_HR", [(SCIENCE, None, "APPROVE")])
    edit_only = ("Withdraw this application", "Read this resume again", "Correct the dates or type of a post", "Add a highlight")
    upload = f'action="/hr/openings/{opening_id}/upload"'

    _sign_in(web, viewer)
    page, listing = web.get(f"/hr/applications/{app_id}").text, web.get(f"/hr/openings/{opening_id}").text
    assert "Approve as assessed" not in page and not any(x in page for x in edit_only)
    assert "Your account may not decide it." in page and "What the reader found" in page  # everything can still be read
    assert upload not in listing and "Edit this opening" not in listing and "may view this opening but not change it" in listing
    assert "Waiting for a decision" in listing and ">Decide<" not in listing

    _sign_in(web, editor)
    page, listing = web.get(f"/hr/applications/{app_id}").text, web.get(f"/hr/openings/{opening_id}").text
    assert all(x in page for x in edit_only) and "Approve as assessed" not in page and "Your account may not decide it." in page
    assert upload in listing and "Edit this opening" in listing

    _sign_in(web, approver)
    page = web.get(f"/hr/applications/{app_id}").text
    assert "Approve as assessed" in page and all(x in page for x in edit_only)
    web.post(f"/hr/applications/{app_id}/approve")
    assert 'name="draft_id"' in web.get(f"/hr/openings/{opening_id}/emails").text
    _sign_in(web, editor)
    emails_page, decided = web.get(f"/hr/openings/{opening_id}/emails").text, web.get(f"/hr/applications/{app_id}").text
    assert 'name="draft_id"' not in emails_page and "may read these letters but not approve them" in emails_page
    assert "may read this letter but not change or approve it" in decided and "Reopen this decision" not in decided


# --- a forgotten password ----------------------------------------------------


def test_a_reset_link_works_once_for_half_an_hour_and_ends_the_accounts_sessions(session):
    user = _account(session, "Hari HR", "HR_ADMIN")
    signed_in = access.sign_in(session, user.email, PASSWORD)
    assert access.start_password_reset(session, "nobody@example.org") is None
    _, token = access.start_password_reset(session, " HARI.HR@example.org ")
    row = session.scalars(select(PasswordReset)).one()
    assert row.token_hash != token and len(row.token_hash) == 64  # the link's token is not kept
    assert access.start_password_reset(session, user.email) is None  # not a second link straight away
    assert access.reset_is_live(session, token) and not access.reset_is_live(session, "made-up")
    with pytest.raises(access.AccessError, match="at least 10 characters"):
        access.finish_password_reset(session, token, "short")
    assert access.reset_is_live(session, token)  # a refused password does not use the link up
    access.finish_password_reset(session, token, OTHER_PASSWORD)
    assert access.user_for_token(session, signed_in) is None and access.sign_in(session, user.email, OTHER_PASSWORD)
    with pytest.raises(access.AccessError, match="no longer valid"):
        access.finish_password_reset(session, token, PASSWORD + "-again")

    later = datetime.now(timezone.utc) + timedelta(minutes=6)
    _, second = access.start_password_reset(session, user.email, now=later)
    assert not access.reset_is_live(session, second, now=later + access.RESET_VALID_FOR + timedelta(minutes=1))
    access.set_active(session, user, False)  # a closed account's link is dead, and it is given no new one
    assert not access.reset_is_live(session, second, now=later)
    assert access.start_password_reset(session, user.email, now=later + timedelta(minutes=10)) is None


def test_the_forgotten_password_pages_answer_alike_for_any_address_and_mail_only_the_owner(web, engine, monkeypatch):
    sent = []

    class Server:
        def send(self, to_address, subject, body):
            sent.append((to_address, subject, body))

    monkeypatch.setattr(emails, "SmtpTransport", Server)
    with Session(engine) as s:
        hr = _account(s, "Hari HR", "HR_ADMIN")
    assert "not set up on this system" in web.get("/forgot").text and 'href="/forgot"' not in web.get("/login").text
    assert "not set up on this system" in web.post("/forgot", data={"email": hr.email}).text and sent == []

    for name, value in (("SMTP_HOST", "mail.example.org"), ("SMTP_FROM", "hr@example.org"), ("EMAIL_REDIRECT_TO", "catch-all@example.org")):
        monkeypatch.setattr(settings, name, value)
    assert 'href="/forgot"' in web.get("/login").text
    unknown, known = web.post("/forgot", data={"email": "nobody@example.org"}).text, web.post("/forgot", data={"email": hr.email}).text
    said = "If there is an account with that address"
    assert said in unknown and said in known and len(sent) == 1
    to_address, subject, body = sent[0]
    # To the account's own address even in test mode: a reset link must not land in a shared test mailbox.
    assert to_address == hr.email and "choose a new password" in subject and PASSWORD not in body
    link = re.search(r"http://testserver(/reset/\S+)", body).group(1)
    assert "Choose a new password" in web.get(link).text
    assert "The two passwords are not the same." in web.post(link, data={"new": OTHER_PASSWORD, "again": "something-else-9"}).text
    done = web.post(link, data={"new": OTHER_PASSWORD, "again": OTHER_PASSWORD})
    assert "Your password is changed. Sign in with the new one." in done.text
    assert web.post("/login", data={"email": hr.email, "password": PASSWORD}).history[0].status_code == 303
    _sign_in(web, hr, OTHER_PASSWORD)
    web.cookies.clear()
    used = web.get(link)
    assert used.status_code == 410 and "no longer valid" in used.text
    assert web.get("/reset/made-up-token").status_code == 410


# --- the interview panel's view ------------------------------------------------


def test_until_the_university_decides_the_panel_is_not_shown_contact_details_or_category(session):
    show = views.interviewer_visibility(session)
    withheld = {k for k, v in show.items() if not v}
    assert withheld == {"personal.contact", "personal.category", "personal.state", "application.resume", "assessment.working"}
    assert len(session.scalars(select(ViewFieldVisibility)).all()) == len(views.INTERVIEWER_PARTS)  # kept as data
    views.set_interviewer_visibility(session, {"personal.contact", "qualifications.*"})
    assert {k for k, v in views.interviewer_visibility(session).items() if v} == {"personal.contact", "qualifications.*"}


def test_an_interviewer_sees_only_short_listed_candidates_of_its_school_and_only_the_permitted_parts(web, engine, tmp_path):
    ids = _two_schools(engine, tmp_path)
    (_, science_app), (_, computing_app) = ids["science"], ids["computing"]
    with Session(engine) as s:
        science = s.get(Application, science_app)
        undecided = _assessed(s, tmp_path, _other_person(), school_id=SCIENCE)  # in the school, but HR has not decided
        refused = _assessed(s, tmp_path, _no_net_other(), school_id=SCIENCE)
        gate2.approve(s, science)
        gate2.approve(s, refused)  # decided, but not short-listed
        gate2.approve(s, s.get(Application, computing_app))
        s.commit()
        undecided_id, refused_id = undecided.application_id, refused.application_id
        panel = _account(s, "Indu Interviewer", "INTERVIEWER", [(SCIENCE, None, "VIEW")])
        admin = _account(s, "Asha Admin", "UNIVERSITY_ADMIN")
        hr_admin = _account(s, "Hari HR", "HR_ADMIN")

    _sign_in(web, panel)
    home = web.get("/")
    assert home.url.path == "/interview" and f"APP-{science_app:06d}" in home.text
    for hidden in (computing_app, undecided_id, refused_id):
        assert f"APP-{hidden:06d}" not in home.text
        assert web.get(f"/interview/applications/{hidden}").status_code == 404
    record = web.get(f"/interview/applications/{science_app}").text
    assert "A. Synthetic Candidate" in record or "Sample Exampleton" in record
    assert "<h2>Qualifications</h2>" in record and "M.Sc." in record and "<h2>Posts held</h2>" in record
    for withheld in ("example.org", "<dt>Category</dt>", "<dt>Email</dt>", "Open the resume", "<th>Requirement</th>", "<dt>State</dt>"):
        assert withheld not in record, withheld
    assert web.get(f"/interview/applications/{science_app}/resume").status_code == 404
    for hr_page in (f"/hr/applications/{science_app}", f"/hr/applications/{science_app}/resume", "/hr/dashboard", "/admin/views"):
        assert web.get(hr_page).status_code == 403, hr_page

    _sign_in(web, hr_admin)
    assert web.get("/admin/views").status_code == 403  # what the panel sees is the university administrator's to change
    _sign_in(web, admin)
    form = web.get("/admin/views").text
    assert "Category and disability status" in form and form.count(" checked>") == len(views.INTERVIEWER_PARTS) - 5
    shown = [f"{e}.{f}" for e, f, _, default in views.INTERVIEWER_PARTS if default] + ["personal.contact", "application.resume", "assessment.working"]
    assert "Saved." in web.post("/admin/views", data={"shown": shown}).text

    _sign_in(web, panel)
    record = web.get(f"/interview/applications/{science_app}").text
    assert "<dt>Email</dt>" in record and "<th>Requirement</th>" in record and "<dt>Category</dt>" not in record
    assert web.get(f"/interview/applications/{science_app}/resume").status_code == 200


# --- highlights ----------------------------------------------------------------


def test_highlights_follow_the_universitys_norms_and_nothing_is_pointed_out_without_one(session, tmp_path):
    papers = [PublicationEntry(title="A made-up paper", kind="JOURNAL", year="2022", is_first_author=True, impact_factor=6.2),
              PublicationEntry(title="Another made-up paper", kind="JOURNAL", year="2021", is_first_author=False, impact_factor=9.0)]
    a = _assessed(session, tmp_path, _researcher(h_index=12, citations=300, papers=papers))
    assert highlights.for_application(session, a) == []  # no norm has been set, so no figure is called notable

    highlights.set_norm(session, None, "12", "250", "5")
    found = [h.description for h in highlights.for_application(session, a)]
    assert found == ["300 citations, above the norm of 250",
                     "First-author publication, impact factor 6.2 (above the norm of 5): A made-up paper"]  # 12 is not above 12
    highlights.set_norm(session, a.school_id, "10", "", "")  # the school's own figures replace the university-wide ones
    assert [h.description for h in highlights.for_application(session, a)] == ["h-index of 12, above the norm of 10"]
    assert highlights.set_norm(session, a.school_id, "", "", "") is None  # all empty removes the school's row
    assert len(highlights.for_application(session, a)) == 2
    for args, message in ((("SCH-999", "1", "", ""), "Choose a school"), ((None, "many", "", ""), "h-index: enter a whole number"),
                          ((None, "", "", "-1"), "Impact factor: enter a number from 0")):
        with pytest.raises(highlights.HighlightError, match=message):
            highlights.set_norm(session, *args)


def test_a_phd_from_a_premier_institute_is_pointed_out_and_hr_may_add_its_own(session, tmp_path):
    r = _other_person()
    r.education.append(EducationEntry(level="PhD", degree="Ph.D.", university="Made-up Institute of Technology", completion="2020"))
    a = _assessed(session, tmp_path, r)
    institute = InstitutionMaster(institution_name="Made-up Institute of Technology", tier="PREMIER", category="Other")
    session.add(institute)
    session.flush()
    phd = session.scalar(select(CandidateQualification).where(CandidateQualification.application_id == a.application_id,
                                                              CandidateQualification.degree_level == "PhD"))
    phd.institution_id = institute.institution_id
    assert [h.description for h in highlights.for_application(session, a)] == ["Ph.D. from a premier institute: Made-up Institute of Technology"]
    institute.tier = "STATE"
    assert highlights.for_application(session, a) == []

    with pytest.raises(highlights.HighlightError, match="5 to 400"):
        highlights.add(session, a, " no ", "user:7")
    row = highlights.add(session, a, "  Developed the department's   MOOC ", "user:7")
    (added,) = highlights.for_application(session, a)
    assert (added.description, added.automatic, row.source_reference) == ("Developed the department's MOOC", False, "user:7")
    highlights.remove(session, a, added.highlight_id)
    assert highlights.for_application(session, a) == []


# --- the university's own criteria -----------------------------------------------


def test_a_criterion_is_checked_and_what_cannot_be_checked_is_refused(session):
    ok = dict(school_id=None, designation="ASSISTANT_PROFESSOR", criterion="MIN_MASTERS_MARKS_PCT", value="60", actor="user:1")
    for change, message in (({"school_id": "SCH-999"}, "Choose a school"), ({"designation": "DEAN"}, "Choose the post"),
                            ({"criterion": "MIN_HEIGHT"}, "Choose a criterion"), ({"value": "sixty"}, "as a number"),
                            ({"value": "0"}, "above 0"), ({"value": "101"}, "up to 100")):
        with pytest.raises(policy.PolicyError, match=message):
            policy.add_rule(session, **{**ok, **change})
    row = policy.add_rule(session, **ok)
    assert (row.criterion_value, row.created_by) == ("60", "user:1")
    with pytest.raises(policy.PolicyError, match="already set"):
        policy.add_rule(session, **{**ok, "value": "65"})
    assert policy.add_rule(session, **{**ok, "criterion": "PHD_REQUIRED", "value": ""}).criterion_value == "yes"
    policy.remove_rule(session, row.policy_id)
    assert [r.criterion_name for r in session.scalars(select(UniversityPolicyRule))] == ["PHD_REQUIRED"]


def test_a_criterion_not_met_is_shown_beside_the_statutory_finding_and_does_not_change_it(session, tmp_path):
    def rule(criterion, value, school=None, post="ASSISTANT_PROFESSOR"):
        policy.add_rule(session, school_id=school, designation=post, criterion=criterion, value=value, actor="user:1")

    rule("MIN_MASTERS_MARKS_PCT", "70")
    rule("MIN_H_INDEX", "5")
    rule("PHD_REQUIRED", "")
    rule("MIN_PUBLICATIONS", "1")
    rule("MIN_CITATIONS", "10", school=COMPUTING)  # another school's criterion
    rule("MIN_EXPERIENCE_YEARS", "20", post="PROFESSOR")  # another post's criterion
    a = _assessed(session, tmp_path)  # M.Sc. 68.4% with NET: meets the Regulations for Assistant Professor
    results = latest_evaluation(session, a.application_id).details["policy"]
    assert a.status == states.SHORTLISTED  # the statutory finding stands as it was
    assert {r["criterion"]: r["result"] for r in results} == {
        "MIN_MASTERS_MARKS_PCT": "FAIL", "MIN_H_INDEX": "UNKNOWN", "PHD_REQUIRED": "FAIL", "MIN_PUBLICATIONS": "PASS"}
    by = {r["criterion"]: r for r in results}
    assert by["MIN_MASTERS_MARKS_PCT"]["detail"] == "68.4 against 70" and by["MIN_H_INDEX"]["detail"] == "the h-index is not stated on the resume"
    assert by["MIN_MASTERS_MARKS_PCT"]["label"] == "Master's marks of at least (per cent) 70" and by["PHD_REQUIRED"]["scope"] == "every school"
    assert policy.summary(results) == "University criteria: 2 not met, 1 open" and policy.summary([]) == ""
    assert policy.summary([by["MIN_PUBLICATIONS"]]) == "University criteria: all met"
    # HR still decides, with both in view: approving the statutory finding remains possible.
    assert gate2.approve(session, a).final_outcome == "SHORTLISTED"


def test_no_criterion_can_help_a_candidate_the_regulations_find_not_eligible(session, tmp_path):
    policy.add_rule(session, school_id=None, designation="ASSISTANT_PROFESSOR", criterion="MIN_PUBLICATIONS", value="1", actor="user:1")
    a = _assessed(session, tmp_path, _no_net())  # no NET, no Ph.D.: not eligible under the Regulations
    evaluation = latest_evaluation(session, a.application_id)
    assert a.status == states.NOT_ELIGIBLE and evaluation.outcome == "NOT_ELIGIBLE"
    assert evaluation.details["policy"] == []  # the criterion they would have met is not even reported


def test_the_criteria_pages(client, web, engine, tmp_path):
    with Session(engine) as s:
        seed_rules(s)
        admin = _account(s, "Hari HR", "HR_ADMIN")
        school_hr = _account(s, "Sara School", "SCHOOL_HR", [(SCIENCE, None, "APPROVE")])
    _sign_in(web, school_hr)
    assert web.get("/hr/policy").status_code == 403 and web.get("/hr/dashboard").status_code == 403
    assert web.post("/hr/policy/rules", data={"designation": "ASSISTANT_PROFESSOR", "criterion": "PHD_REQUIRED"}).status_code == 403
    _sign_in(web, admin)
    page = web.get("/hr/policy").text
    assert "can only add to the statutory minimum" in page and "None. Candidates are assessed against the statutory minimum only." in page
    assert "Not added. Enter the figure as a number." in web.post(
        "/hr/policy/rules", data={"designation": "ASSISTANT_PROFESSOR", "criterion": "MIN_MASTERS_MARKS_PCT", "value": "high"}).text
    added = web.post("/hr/policy/rules", data={"designation": "ASSISTANT_PROFESSOR", "criterion": "MIN_MASTERS_MARKS_PCT", "value": "70"}).text
    assert "Criterion added." in added and "Master&#39;s marks of at least (per cent) 70" in added and "Every school" in added
    assert "Norms saved." in web.post("/hr/policy/norms", data={"h_index": "10"}).text

    with Session(engine) as s:
        a = _assessed(s, tmp_path)
        opening_id, app_id = a.opening_id, a.application_id
    record = web.get(f"/hr/applications/{app_id}").text
    assert "The university's own criteria, for Assistant Professor" in record and "68.4 against 70" in record
    assert "cannot make eligible" in record and "Approve as assessed" in record
    assert "University criteria: 1 not met" in web.get(f"/hr/openings/{opening_id}").text
    with Session(engine) as s:
        policy_id = s.scalars(select(UniversityPolicyRule.policy_id)).one()
    assert "Criterion removed." in web.post(f"/hr/policy/rules/{policy_id}/remove").text

    # A highlight added on the application page.
    assert "Highlight added." in web.post(f"/hr/applications/{app_id}/highlights", data={"description": "Guided a national hackathon team"}).text
    assert "Guided a national hackathon team" in web.get(f"/hr/applications/{app_id}").text


# --- the dashboard -------------------------------------------------------------


def test_the_dashboard_counts_by_school_opening_and_channel_and_names_no_one(web, engine, tmp_path):
    ids = _two_schools(engine, tmp_path)
    with Session(engine) as s:
        gate2.approve(s, s.get(Application, ids["science"][1]))
        refused = _assessed(s, tmp_path, _no_net_other(), school_id=SCIENCE)
        gate2.approve(s, refused)
        s.commit()
        d = reporting.dashboard(s)
        by_school = {row["school"].school_id: row for row in d["schools"]}
        assert (by_school[SCIENCE]["openings"], by_school[SCIENCE]["applications"]) == (2, 2)
        assert (by_school[SCIENCE]["shortlisted"], by_school[SCIENCE]["not_eligible"], by_school[SCIENCE]["to_decide"]) == (1, 1, 0)
        assert (by_school[COMPUTING]["applications"], by_school[COMPUTING]["to_decide"]) == (1, 1)
        assert d["totals"]["applications"] == 3 and d["channels"] == {"WEB_FORM": 3} and len(d["openings"]) == 3
        admin = _account(s, "Hari HR", "HR_ADMIN")
    _sign_in(web, admin)
    page = web.get("/hr/dashboard").text
    assert "University dashboard" in page and "All schools" in page and "Application form" in page
    assert "Exampleton" not in page and "Synthetic" not in page and "example.org" not in page
    # The dashboard is an administrator's first page; the openings list is one link away.
    first = web.get("/?msg=hello")
    assert first.url.path == "/hr/dashboard" and "University dashboard" in first.text and "hello" in first.text
    assert 'href="/hr/openings"' in first.text and "Reading queue" in web.get("/hr/openings").text
    with Session(engine) as s:
        school_hr = _account(s, "Sara School", "SCHOOL_HR", [(SCIENCE, None, "VIEW")])
    _sign_in(web, school_hr)
    own = web.get("/")  # an account tied to a school has no university-wide dashboard: it starts on its openings
    assert own.url.path == "/" and "Reading queue" in own.text and web.get("/hr/dashboard").status_code == 403
