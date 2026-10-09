"""Phase 8: applications by email and through a Google Form.

No mailbox is contacted: messages are built here and handed over by a stand-in
mailbox. Made-up people and files only. SQLite by default; PostgreSQL when
TEST_DATABASE_URL is set.
"""

from __future__ import annotations

from datetime import date
from email.message import EmailMessage

import pytest
from sqlalchemy import select

from backend import inbox, intake, jobs, settings, states
from backend.models import Application, Candidate, InboxMessage, Job, JobOpening
from tests.test_backend_foundation import _docx, client, engine, session  # noqa: F401
from tests.test_backend_intake import _opening, _storage  # noqa: F401

DOCX = ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
MAILBOX = "recruitment@example.org"
TODAY = date(2026, 10, 7)
_counter = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def _mail_settings(monkeypatch):
    for name, value in (("IMAP_HOST", ""), ("IMAP_USER", MAILBOX), ("IMAP_PASSWORD", ""), ("SMTP_FROM", MAILBOX),
                        ("GOOGLE_FORM_SENDERS", "")):
        monkeypatch.setattr(settings, name, value)


def _mail(subject="Application", body="Please find my resume attached.", sender="Sample Exampleton <sample.exampleton@example.org>",
          attach=("cv.docx", None), message_id=None, html=None, **headers) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, MAILBOX, subject
    m["Date"] = "Wed, 07 Oct 2026 10:00:00 +0530"
    m["Message-ID"] = message_id or f"<{next(_counter)}@mail.example.org>"
    for key, value in headers.items():
        m[key.replace("_", "-")] = value
    if html is not None:
        m.set_content(html, subtype="html")
    else:
        m.set_content(body)
    if attach:
        name, data = attach
        # A different made-up file each time unless told otherwise, so "same file twice" is a deliberate case.
        m.add_attachment(data if data is not None else _docx() + str(next(_counter)).encode(), maintype=DOCX[0], subtype=DOCX[1], filename=name)
    return m.as_bytes()


class Box:
    def __init__(self, *messages: bytes) -> None:
        self.messages, self.asked_since = list(messages), None

    def fetch(self, since, known=frozenset()):
        self.asked_since, self.known = since, known
        return iter(self.messages)


def _take(session, raw) -> InboxMessage:
    row = inbox.process_message(session, raw)
    session.commit()
    return row


# --- an application by email -------------------------------------------------


def test_a_resume_that_names_its_opening_is_filed_and_queued_for_reading(session):
    o = _opening(session)
    row = _take(session, _mail(subject=f"Application for {o.reference}"))
    assert (row.kind, row.classified_by, row.status) == ("APPLICATION", "rule:resume_attached", "IMPORTED")
    a = session.get(Application, row.application_id)
    assert (a.opening_id, a.resume_source, a.status) == (o.opening_id, "EMAIL", states.RECEIVED)
    # Who sent it is not taken as whose resume it is: both are read from the resume, as for an HR upload.
    assert (a.applicant_name, a.applicant_email, a.candidate.email) == (None, None, None)
    assert (row.sender_name, row.sender) == ("Sample Exampleton", "sample.exampleton@example.org")
    assert a.category is None and a.school_id == o.school_id and a.applied_designation == o.designation  # from the opening
    assert session.scalars(select(Job)).one().application_id == a.application_id
    assert a.transitions[0].note == f"source=EMAIL; opening={o.reference}"


def test_a_message_is_looked_at_once_however_often_the_inbox_is_checked(session):
    o = _opening(session)
    raw = _mail(subject=o.reference, message_id="<same@mail.example.org>")
    box = Box(raw, raw)
    assert inbox.check_inbox(session, box, today=TODAY) == {"IMPORTED": 1}
    assert inbox.check_inbox(session, box, today=TODAY) == {}
    assert len(session.scalars(select(Application)).all()) == 1 and box.asked_since == date(2026, 9, 7)  # thirty days back
    assert box.known == frozenset({"<same@mail.example.org>"})  # the mailbox is told what need not be downloaded again


def test_the_reference_is_found_in_the_body_and_in_an_html_only_message(session):
    o = _opening(session)
    assert _take(session, _mail(body=f"I wish to apply against {o.reference.lower()}.")).status == "IMPORTED"
    html = f"<p>Applying for <b>{o.reference}</b></p>"
    assert _take(session, _mail(html=html, sender="other.exampleton@example.org")).status == "IMPORTED"


def test_an_application_naming_no_opening_is_held_until_a_person_chooses_one(session):
    o = _opening(session)
    row = _take(session, _mail(subject="Application for faculty post"))
    assert (row.kind, row.status, row.application_id) == ("APPLICATION", "NEEDS_JOB_MATCH", None)
    assert row.attachment_path and session.scalars(select(Application)).all() == []  # the resume is kept; nothing is guessed
    assert inbox.waiting(session) == [row]
    inbox.assign_opening(session, row, o)
    assert row.status == "IMPORTED" and session.get(Application, row.application_id).opening_id == o.opening_id
    with pytest.raises(ValueError):
        inbox.assign_opening(session, row, o)


def test_an_application_for_a_closed_opening_is_held_not_filed(session):
    o = _opening(session)
    intake.close_opening(session, o)
    row = _take(session, _mail(subject=o.reference))
    assert row.status == "NEEDS_JOB_MATCH" and "not accepting" in row.note
    with pytest.raises(ValueError, match="not accepting"):
        inbox.assign_opening(session, row, o)
    inbox.set_aside(session, row)
    assert row.status == "IGNORED" and inbox.waiting(session) == []


def test_one_sender_may_forward_many_resumes_but_the_same_file_is_not_taken_twice(session):
    o = _opening(session)
    same_file = _docx()
    assert _take(session, _mail(subject=o.reference, attach=("cv.docx", same_file))).status == "IMPORTED"
    another = _take(session, _mail(subject=o.reference))  # the same sender, another person's resume
    assert another.status == "IMPORTED"
    twin = _take(session, _mail(subject=o.reference, sender="other.exampleton@example.org", attach=("cv.docx", same_file)))
    assert twin.status == "REJECTED" and "already under this opening" in twin.note
    assert len(session.scalars(select(Application)).all()) == 2 and len(session.scalars(select(Candidate)).all()) == 2


def test_the_name_and_address_come_from_the_resume_and_the_sender_is_kept_for_comparison(session):
    from backend.reader_service import read_application
    from backend.models import CandidatePersonalDetails
    from llm.providers.fake_provider import FakeProvider
    from tests.test_backend_foundation import _clean_result

    o = _opening(session)
    row = _take(session, _mail(subject=o.reference, sender="Forwarding Colleague <colleague@example.org>"))
    a = session.get(Application, row.application_id)
    read_application(session, a, FakeProvider(script=[_clean_result()]))  # the resume says "A. Synthetic Candidate"
    personal = session.get(CandidatePersonalDetails, a.candidate_id)
    assert (personal.full_name, personal.contact_email) == ("A. Synthetic Candidate", "sample.exampleton@example.org")
    assert a.candidate.email == "sample.exampleton@example.org"
    sender = inbox.sender_of(session, a.application_id, personal.full_name)
    assert sender == {"name": "Forwarding Colleague", "address": "colleague@example.org", "same_person": False}
    row.sender_name = "Synthetic Candidate"
    assert inbox.sender_of(session, a.application_id, personal.full_name)["same_person"] is True
    row.sender_name = None
    assert inbox.sender_of(session, a.application_id, personal.full_name)["same_person"] is None


# --- sorting -----------------------------------------------------------------


def test_a_vacancy_announcement_is_shown_to_hr_and_no_opening_is_created(session):
    row = _take(session, _mail(subject="Faculty requirement: Assistant Professor, Computer Engineering", attach=None,
                               body="The department has two vacancies for the coming term."))
    assert (row.kind, row.classified_by, row.status) == ("JOB_OPENING", "rule:opening_words", "FOR_HR")
    assert session.scalars(select(JobOpening)).all() == []  # the rule set is HR's choice, never read from an email


@pytest.mark.parametrize("message, rule, status", [
    (dict(subject="Application for Assistant Professor", attach=None), "application_without_resume", "FOR_HR"),
    (dict(subject="Photo", body="As asked.", attach=("photo.jpg", b"not a resume")), "no_rule_matched", "IGNORED"),
    (dict(subject="Out of office", attach=None, Auto_Submitted="auto-replied"), "automatic_message", "IGNORED"),
    (dict(subject="Delivery failure", sender="mailer-daemon@mail.example.org", attach=None), "automatic_message", "IGNORED"),
    # A promotional mailing that happens to use the word "application": by its sender's name, or by its headers.
    (dict(subject="Claim your offer", body="Use our application today.", sender="product-noreply@mail.example.org", attach=None),
     "automatic_message", "IGNORED"),
    (dict(subject="Claim your offer", body="Use our application today.", sender="News <news@mail.example.org>", attach=None,
          List_Unsubscribe="<mailto:leave@mail.example.org>"), "automatic_message", "IGNORED"),
    (dict(subject="Weekly digest", body="Apply now.", sender="News <news@mail.example.org>", attach=None, Precedence="bulk"),
     "automatic_message", "IGNORED"),
    # A person whose address merely contains the letters is not taken for a machine.
    (dict(subject="My application", body="I wish to apply.", sender="Norep Lyon <noreplyon@example.org>", attach=None),
     "application_without_resume", "FOR_HR"),
    (dict(subject="Application OPN-00001", sender=MAILBOX), "own_mail", "IGNORED"),
    (dict(subject="Lunch on Friday?", body="See you at one.", attach=None), "no_rule_matched", "IGNORED"),
])
def test_everything_else_is_sorted_by_a_named_rule(session, message, rule, status):
    row = _take(session, _mail(**message))
    assert (row.classified_by, row.status) == (f"rule:{rule}", status)
    assert session.scalars(select(Application)).all() == []


def test_a_file_the_reader_cannot_take_is_reported_not_stored(session, monkeypatch):
    from backend import storage

    monkeypatch.setattr(storage, "MAX_BYTES", 10)
    row = _take(session, _mail(subject="Application OPN-00001"))
    assert row.status == "REJECTED" and "larger than 15 MB" in row.note and row.attachment_path is None


# --- the Google Form ---------------------------------------------------------

FORM_BODY = """Opening: {ref}
Full name: Sample Exampleton
Email: sample.exampleton@example.org
Phone: +91 98765 00000
State: Maharashtra
Category: OBC-NCL
Differently abled: No
Study leave: Not applicable
"""


def _form(o, body=None, **kw):
    fields = dict(subject=f"{inbox.FORM_SUBJECT} {o.reference}", body=(body or FORM_BODY).format(ref=o.reference), sender=MAILBOX)
    fields.update(kw)
    return _mail(**fields)


def test_a_form_response_is_filed_with_its_answers_as_if_typed_into_the_web_form(session):
    o = _opening(session)
    row = _take(session, _form(o))
    assert (row.kind, row.classified_by, row.status) == ("APPLICATION", "rule:google_form", "IMPORTED")
    a = session.get(Application, row.application_id)
    assert (a.resume_source, a.applicant_name, a.applicant_email, a.applicant_phone) == (
        "GOOGLE_FORM", "Sample Exampleton", "sample.exampleton@example.org", "+91 98765 00000")
    assert (a.category, a.applicant_state, a.differently_abled, a.study_leave_taken) == ("OBC-NCL", "Maharashtra", False, None)
    assert row.form_answers["category"] == "OBC-NCL"


def test_a_form_response_is_trusted_only_from_the_form_owners_address(session, monkeypatch):
    o = _opening(session)
    forged = _take(session, _form(o, sender="someone.else@example.org"))
    assert (forged.classified_by, forged.status) == ("rule:form_subject_from_untrusted_sender", "FOR_HR")
    assert session.scalars(select(Application)).all() == []
    monkeypatch.setattr(settings, "GOOGLE_FORM_SENDERS", "forms.owner@example.org, second.owner@example.org")
    assert _take(session, _form(o, sender="Forms Owner <forms.owner@example.org>")).status == "IMPORTED"
    other = _opening(session, school_id="SCH-009")
    unlisted = _take(session, _form(other, sender=MAILBOX))  # once a list is given, the mailbox's own address is not on it
    assert unlisted.classified_by == "rule:form_subject_from_untrusted_sender"


def test_form_answers_that_fail_the_forms_own_checks_go_to_a_person(session):
    o = _opening(session)
    bad = FORM_BODY.replace("Category: OBC-NCL", "Category: Royalty").replace("State: Maharashtra", "State: Atlantis")
    row = _take(session, _form(o, body=bad))
    assert row.status == "FOR_HR" and "category" in row.note and "state" in row.note
    assert session.scalars(select(Application)).all() == []
    no_file = _take(session, _form(o, attach=None))
    assert no_file.status == "FOR_HR" and "no resume attached" in no_file.note


def test_a_form_response_for_an_opening_it_does_not_name_is_held(session):
    o = _opening(session)
    row = _take(session, _mail(subject=inbox.FORM_SUBJECT, body=FORM_BODY.format(ref=""), sender=MAILBOX))
    assert row.status == "NEEDS_JOB_MATCH"
    inbox.assign_opening(session, row, o)
    assert row.status == "IMPORTED" and session.get(Application, row.application_id).resume_source == "GOOGLE_FORM"


def test_form_lines_are_read_by_their_labels_whatever_the_order_or_case():
    answers = inbox.parse_form("STATE:  Goa \nfull name: A B\nSomething else: ignored\nStudy Leave: yes\nNot a field line\nEmail: a@example.org")
    assert answers == {"state": "Goa", "full_name": "A B", "study_leave_taken": "yes", "email": "a@example.org"}


# --- checking the mailbox ----------------------------------------------------


def test_checking_reports_what_it_found_in_words(session):
    o = _opening(session)
    counts = inbox.check_inbox(session, Box(_mail(subject=o.reference), _mail(subject="Application"), _mail(subject="Hello", body="See you on Friday.", attach=None)),
                               today=TODAY)
    assert counts == {"IMPORTED": 1, "NEEDS_JOB_MATCH": 1, "IGNORED": 1}
    text = inbox.summarise(counts)
    assert text.startswith("3 new message(s): 1 filed as applications; 1 waiting for you to choose the opening; 1 not about recruitment")
    assert inbox.summarise({}) == "No new messages."
    with pytest.raises(RuntimeError, match="No mailbox is configured"):
        inbox.check_inbox(session)


def test_the_mailbox_is_opened_read_only_and_messages_are_peeked_not_marked_read(monkeypatch):
    calls = []

    class Server:
        def __init__(self, host, port):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, user, password):
            calls.append(("login", user))

        def select(self, folder, readonly=False):
            calls.append(("select", folder, readonly))

        def search(self, charset, *criteria):
            calls.append(("search", criteria))
            return "OK", [b"1 2"]

        def fetch(self, number, what):
            calls.append(("fetch", number, what))
            if "HEADER.FIELDS" in what:
                return "OK", [(b"1 (BODY[HEADER.FIELDS (MESSAGE-ID)] {30}", b"Message-ID: <m" + number + b"@x>\r\n\r\n"), b")"]
            return "OK", [(b"1 (BODY[] {3}", b"raw" + number), b")"]

    for name, value in (("IMAP_HOST", "imap.example.org"), ("IMAP_PORT", 993), ("IMAP_PASSWORD", "made-up"), ("IMAP_FOLDER", "INBOX")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", Server)
    assert inbox.inbox_is_configured()
    assert list(inbox.ImapMailbox().fetch(date(2026, 10, 1))) == [b"raw1", b"raw2"]
    assert ("select", "INBOX", True) in calls and ("search", ("SINCE", "01-Oct-2026")) in calls
    assert all("PEEK" in c[2] for c in calls if c[0] == "fetch")
    # A message seen before is recognised from one header and its body is never downloaded again.
    calls.clear()
    assert list(inbox.ImapMailbox().fetch(date(2026, 10, 1), frozenset({"<m1@x>"}))) == [b"raw2"]
    assert [c[1:] for c in calls if c[0] == "fetch" and c[2] == "(BODY.PEEK[])"] == [(b"2", "(BODY.PEEK[])")]


# --- the pages ---------------------------------------------------------------


def test_the_inbox_page_lists_what_waits_and_files_a_held_application(client, engine):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        o = _opening(s)
        held = _take(s, _mail(subject="Application for faculty post"))
        _take(s, _mail(subject="Faculty requirement in Design", attach=None, sender="dean@example.org"))
        opening_id, held_id = o.opening_id, held.inbox_id
    assert "Inbox (2)" in client.get("/").text
    page = client.get("/hr/inbox").text
    assert "Waiting for you (2)" in page and "File under this opening" in page and "No mailbox is configured" in page
    assert "Opening announcement" in page and "rule: resume attached" in page
    assert "No mailbox is configured" in client.post("/hr/inbox/check").text
    filed = client.post(f"/hr/inbox/{held_id}/assign", data={"opening_id": opening_id}).text
    assert f"Filed under OPN-{opening_id:05d}." in filed and "Waiting for you (1)" in filed
    assert "not waiting for an opening" in client.post(f"/hr/inbox/{held_id}/assign", data={"opening_id": opening_id}).text
    assert "Choose an opening." in client.post(f"/hr/inbox/{held_id + 1}/assign", data={"opening_id": 999999}).text
    aside = client.post(f"/hr/inbox/{held_id + 1}/set-aside").text
    assert "Set aside." in aside and "Nothing is waiting." in aside
    assert client.post("/hr/inbox/999999/set-aside").status_code == 404
    listing = client.get(f"/hr/openings/{opening_id}").text
    assert "Email" in listing and "Not yet known" in listing  # the name will come from the resume, not the sender
    page = client.get(f"/hr/applications/{client.get(f'/applications/1').json()['application_id']}").text
    assert "Email sent by" in page and "sample.exampleton@example.org" in page


def test_a_mailbox_that_cannot_be_read_says_so_without_the_servers_words(client, engine, monkeypatch):
    def refuse(*a, **kw):
        raise ConnectionRefusedError("535 bad credentials for recruitment@example.org")

    for name, value in (("IMAP_HOST", "imap.example.org"), ("IMAP_PASSWORD", "made-up")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", refuse)
    page = client.post("/hr/inbox/check").text
    assert "The mailbox could not be read (ConnectionRefusedError)" in page and "535" not in page


def test_a_resume_with_dated_teaching_posts_and_no_stated_total_is_not_sent_to_a_person_for_it(session, tmp_path):
    """Seen on a live resume: an experience table with periods, no total. The engine counts the posts."""
    from backend.reader_service import read_application
    from llm.interface import ExperienceEntry
    from llm.providers.fake_provider import FakeProvider
    from tests.test_backend_foundation import _application, _clean_result

    def read(experience):
        r = _clean_result()
        r.teaching_years_raw.value = r.teaching_years_raw.evidence = None
        r.teaching_years_raw.confidence = 0.0
        r.experience = experience
        r.email = f"person{next(_counter)}@example.org"
        a = _application(session, tmp_path, category="General", differently_abled=False)
        read_application(session, a, FakeProvider(script=[r]))
        return a

    dated = read([ExperienceEntry(designation="Assistant Professor", institution="Placeholder College", kind="TEACHING",
                                  start="2020-01-01", end="2024-10-22", duration="4 yrs 10 Months")])
    assert dated.status == states.EXTRACTED and dated.extracted.review_reasons == []
    undated = read([ExperienceEntry(designation="Assistant Professor", institution="Placeholder College", kind="TEACHING")])
    assert undated.extracted.review_reasons == ["teaching_years_raw:missing_required"]  # nothing to count from: still asked
    industry_only = read([ExperienceEntry(designation="Engineer", institution="Placeholder Ltd", kind="INDUSTRY", start="2020")])
    assert industry_only.extracted.review_reasons == ["teaching_years_raw:missing_required"]


def test_the_application_page_shows_teaching_counted_from_the_dated_posts(client, engine, tmp_path):
    from sqlalchemy.orm import Session as _Session

    from backend.reader_service import read_application
    from llm.providers.fake_provider import FakeProvider
    from tests.test_backend_foundation import _application, _clean_result

    with _Session(engine) as s:
        a = _application(s, tmp_path, category="General", differently_abled=False)
        read_application(s, a, FakeProvider(script=[_clean_result()]))  # Assistant Professor, 2018 to present
        s.commit()
        app_id = a.application_id
    page = client.get(f"/hr/applications/{app_id}").text
    assert "Teaching, from the dated posts" in page and "this is what the assessment uses" in page and "Email sent by" not in page


# --- helping a person choose the opening for a held application ---------------


def test_a_held_application_is_suggested_the_opening_its_own_words_point_to(session):
    computing_ap = _opening(session, department_name="Computer Engineering")                      # School of Computing
    computing_prof = _opening(session, designation="PROFESSOR")
    design_ap = _opening(session, school_id="SCH-005", discipline_group="DESIGN")                 # Institute of Design
    openings = [design_ap, computing_prof, computing_ap]

    row = _take(session, _mail(subject="Application", body="I wish to apply for Assistant Professor in Computer Engineering."))
    assert row.status == "NEEDS_JOB_MATCH" and row.body_excerpt.startswith("I wish to apply for Assistant Professor")
    best, why = inbox.suggest_openings(row, openings)[0]
    assert best is computing_ap
    assert why == "the message mentions the post (Assistant Professor) and the department (Computer Engineering)"

    prof = _take(session, _mail(subject="Applying for the post of Professor, School of Computing", body="CV attached."))
    assert inbox.suggest_openings(prof, openings)[0][0] is computing_prof  # "Professor" is not read out of "Assistant Professor"...
    assistant = _take(session, _mail(subject="Assistant Professor", body="CV attached."))
    assert computing_prof not in [o for o, _ in inbox.suggest_openings(assistant, openings)]  # ...nor the other way round

    design = _take(session, _mail(subject="Faculty application", body="Applying to the Institute of Design."))
    assert [o for o, _ in inbox.suggest_openings(design, openings)] == [design_ap]

    silent = _take(session, _mail(subject="Application for faculty post", body="Please find my resume attached."))
    assert inbox.suggest_openings(silent, openings) == []  # nothing to go on: no suggestion, not a guess


def test_the_inbox_page_gives_what_is_needed_to_choose(client, engine):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        o = _opening(s, department_name="Computer Engineering")
        pointed = _take(s, _mail(subject="Application", body="Applying for Assistant Professor, Computer Engineering."))
        silent = _take(s, _mail(subject="Application for faculty post", body="Please find my resume attached.",
                                sender="Other Person <other.person@example.org>"))
        ids = (pointed.inbox_id, silent.inbox_id, o.opening_id)
    page = client.get("/hr/inbox").text
    assert "Applying for Assistant Professor, Computer Engineering." in page                     # what the sender wrote
    assert f"Suggested: OPN-{ids[2]:05d}" in page and "the department (Computer Engineering)" in page and "Check it before filing." in page
    assert "Nothing in the message points to an opening." in page and "Choose an opening…" in page
    assert "mailto:other.person@example.org?subject=Re%3A%20Application%20for%20faculty%20post" in page
    resume = client.get(f"/hr/inbox/{ids[0]}/attachment")
    assert resume.status_code == 200 and f"inbox-{ids[0]}.docx" in resume.headers["content-disposition"]
    assert client.get("/hr/inbox/999999/attachment").status_code == 404
    unchosen = client.post(f"/hr/inbox/{ids[1]}/assign", data={"opening_id": ""})
    assert unchosen.status_code == 200 and "Choose an opening." in unchosen.text  # an explanation, not an error page
    filed = client.post(f"/hr/inbox/{ids[0]}/assign", data={"opening_id": str(ids[2])}).text
    assert f"Filed under OPN-{ids[2]:05d}." in filed


def test_the_read_button_says_how_many_it_will_read_not_only_its_limit(client, engine):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        o = _opening(s)
        _take(s, _mail(subject=o.reference))
        opening_id = o.opening_id
    one = client.get(f"/hr/openings/{opening_id}").text
    assert "Read the 1 waiting application</button>" in one and "1 request(s) to the language model" in one
    with _Session(engine) as s:
        for _ in range(13):
            _take(s, _mail(subject=f"OPN-{opening_id:05d}"))
    many = client.get(f"/hr/openings/{opening_id}").text
    assert "Read 12 of the 14 waiting applications</button>" in many and "Twelve at a time; 12 request(s)" in many


def test_a_held_resume_that_is_already_in_the_system_is_pointed_out_before_it_is_filed_again(client, engine):
    """Seen live: a resume already filed and decided under one opening was emailed again with no reference,
    and the page asked for an opening as if it were new."""
    from sqlalchemy.orm import Session as _Session

    same_file = _docx() + b"the same made-up resume"
    with _Session(engine) as s:
        first, second = _opening(s), _opening(s, school_id="SCH-009")
        filed = _take(s, _mail(subject=first.reference, attach=("cv.docx", same_file)))
        held = _take(s, _mail(subject="", body="", attach=("renamed.docx", same_file), sender="other.person@example.org"))
        assert held.status == "NEEDS_JOB_MATCH"
        assert [a.application_id for a in inbox.already_filed(s, held)] == [filed.application_id]
        fresh = _take(s, _mail(subject="Application"))
        assert inbox.already_filed(s, fresh) == []
        ids = (held.inbox_id, filed.application_id, first.opening_id, second.opening_id)
    page = client.get("/hr/inbox").text
    assert "This same resume is already in the system" in page and f"APP-{ids[1]:06d}</a> under OPN-{ids[2]:05d}" in page
    assert page.count("This same resume is already in the system") == 1  # only on the row it is true of
    # It is still the person's call: refused under the same opening, allowed under another.
    refused = client.post(f"/hr/inbox/{ids[0]}/assign", data={"opening_id": str(ids[2])}).text
    assert "This resume is already under this opening" in refused and "resume:" not in refused


# --- a reply about an application is correspondence, not a new application ----


def test_a_reply_quoting_an_application_reference_is_attached_to_that_application_not_filed_as_a_new_one(session):
    """Our own letters quote the reference and invite a reply with supporting documents."""
    o = _opening(session)
    original = _take(session, _mail(subject=o.reference))
    a = session.get(Application, original.application_id)
    reply = _take(session, _mail(subject=f"Re: Application {a.reference}: outcome of screening, Assistant Professor",
                                 body="Please find my NET certificate attached.", attach=("net_certificate.pdf", b"%PDF-1.4 made-up certificate")))
    assert (reply.kind, reply.classified_by, reply.status) == ("OTHER", "rule:about_an_existing_application", "FOR_HR")
    assert reply.application_id == a.application_id and reply.attachment_path
    assert f"A message about {a.reference}, with a document attached." in reply.note
    assert len(session.scalars(select(Application)).all()) == 1  # no second application for the same person
    assert inbox.correspondence(session, a.application_id) == [reply]
    assert inbox.already_filed(session, reply) == []  # a certificate is not a resume to be matched
    assert inbox.sender_of(session, a.application_id)["address"] == "sample.exampleton@example.org"  # the original sender, not the reply's

    thanks = _take(session, _mail(subject=f"RE: {a.reference}", body="Thank you, I will attend the interview.", attach=None))
    assert thanks.classified_by == "rule:about_an_existing_application" and "with a document" not in thanks.note
    unknown = _take(session, _mail(subject="Re: Application APP-999999", sender="other.person@example.org"))
    assert unknown.classified_by == "rule:resume_attached"  # a reference to nothing is not a reference


def test_correspondence_shows_on_the_application_page_and_the_inbox_links_to_it(client, engine):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        o = _opening(s)
        original = _take(s, _mail(subject=o.reference))
        app_id = original.application_id
        _take(s, _mail(subject=f"Re: APP-{app_id:06d}", body="My NET certificate is attached.", attach=("net.pdf", b"%PDF-1.4 made-up")))
    page = client.get(f"/hr/applications/{app_id}").text
    assert "Messages received about this application (1)" in page and "My NET certificate is attached." in page and "Open the attached document" in page
    inbox_page = client.get("/hr/inbox").text
    assert f'Open APP-{app_id:06d}</a>' in inbox_page and "File under this opening" not in inbox_page


def test_a_scanned_file_is_refused_at_the_application_form_so_the_applicant_can_fix_it(session):
    from tests.test_backend_foundation import _scanned_pdf
    from tests.test_backend_intake import _form

    o = _opening(session)
    with pytest.raises(intake.IntakeError) as exc:
        intake.submit_application(session, o, _form(), "scan.pdf", _scanned_pdf())
    assert "no text that can be read" in exc.value.errors["resume"] and "scan" in exc.value.errors["resume"]
    session.rollback()
    assert session.scalars(select(Application)).all() == []
    assert intake.submit_application(session, o, _form(), "cv.docx", _docx()).status == states.RECEIVED


def test_the_time_a_message_was_received_is_kept_in_utc_whatever_zone_its_date_header_used(session):
    from datetime import datetime, timezone

    row = _take(session, _mail(subject="Hello", body="See you.", attach=None))  # Date: 10:00 +0530
    stored = row.received_at if row.received_at.tzinfo else row.received_at.replace(tzinfo=timezone.utc)
    assert stored == datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)


def test_only_a_row_waiting_for_a_post_offers_to_ask_which_post(client, engine):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        _opening(s)
        _take(s, _mail(subject="Faculty requirement in Design", body="Two vacancies.", attach=None, sender="dean@example.org"))
    page = client.get("/hr/inbox").text
    assert "Reply to ask which post" not in page and ">Reply</a>" in page
