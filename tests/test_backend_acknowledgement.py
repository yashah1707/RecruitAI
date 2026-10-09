"""The acknowledgement of receipt: the one letter that may go without a person approving it.

No mail server is contacted: sending goes to a stand-in. Made-up people only.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend import emails, gate1, gate2, inbox, intake, settings, states
from backend.models import Application, EmailDraft
from backend.reader_service import read_application
from backend.rules_seed import seed_rules
from llm.providers.fake_provider import FakeProvider
from tests.test_backend_foundation import _application, _clean_result, _docx, client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_gate2 import _assessed, _other_person
from tests.test_backend_inbox import MAILBOX, _form as _form_mail, _mail, _take
from tests.test_backend_intake import DOCX, _data, _form, _opening, _storage  # noqa: F401
from tests.test_backend_reporting import Outbox

ADDRESS = "sample.exampleton@example.org"
ACK = emails.ACKNOWLEDGEMENT


@pytest.fixture
def session(_plain_session):
    seed_rules(_plain_session)
    _plain_session.commit()
    return _plain_session


def _applied(session) -> Application:
    return intake.submit_application(session, _opening(session), _form(), "cv.docx", _docx())


def _acks(session, a) -> list[EmailDraft]:
    return list(session.scalars(select(EmailDraft).where(EmailDraft.application_id == a.application_id, EmailDraft.kind == ACK)))


# --- an application made on a form ------------------------------------------


def test_an_application_on_the_form_is_acknowledged_without_a_person_and_only_once(session):
    a = _applied(session)
    (ack,) = _acks(session, a)
    assert (ack.status, ack.approved_by, ack.to_address, ack.drafted_by) == ("APPROVED", "system:acknowledgement", ADDRESS, "template")
    assert ack.subject == f"Application {a.reference} received: Assistant Professor, {a.school.name}"
    assert ack.body.startswith("Dear Sample Exampleton,") and a.reference in ack.body
    assert "confirms receipt only and says nothing about eligibility" in ack.body
    assert emails.active_draft(session, a.application_id) is None  # it is not the letter that carries a decision

    box = Outbox()
    assert emails.send_acknowledgement(session, ack, box) is True
    assert box.sent == [(ADDRESS, ack.subject, ack.body)] and ack.status == "SENT"
    assert a.status == states.RECEIVED  # being acknowledged moves an application nowhere
    assert emails.send_acknowledgement(session, ack, box) is False and len(box.sent) == 1
    assert emails.acknowledge(session, a) is None and len(_acks(session, a)) == 1


def test_in_test_mode_the_acknowledgement_goes_to_the_redirect_address_and_not_to_the_applicant(session, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_TO", "catch-all@example.org")
    (ack,) = _acks(session, _applied(session))
    box = Outbox()
    assert emails.send_acknowledgement(session, ack, box) is True
    assert box.sent[0][0] == "catch-all@example.org" and box.sent[0][1].startswith("[would go to the candidate]")
    assert (ack.status, ack.redirected) == ("APPROVED", True)
    assert emails.send_acknowledgement(session, ack, box) is False and len(box.sent) == 1  # one test copy, not one per try


def test_a_mail_server_that_refuses_it_does_not_disturb_the_application_and_it_can_be_sent_later(session):
    a = _applied(session)
    (ack,) = _acks(session, a)
    assert emails.send_acknowledgement(session, ack, Outbox(fail=ConnectionError("made-up refusal"))) is False
    assert (ack.status, ack.error, a.status) == ("APPROVED", "ConnectionError", states.RECEIVED)
    assert emails.send_acknowledgement(session, ack) is False  # no mail server configured: it waits
    box = Outbox()
    assert emails.approve_and_send(session, [ack], box)["sent"] == 1 and ack.status == "SENT"  # from the opening's emails page


def test_it_can_be_switched_off_and_a_resume_hr_uploaded_gets_none(session, tmp_path, monkeypatch):
    uploaded = _application(session, tmp_path, resume_source="MANUAL_UPLOAD")
    read_application(session, uploaded, FakeProvider(script=[_clean_result()]))
    assert emails.acknowledge(session, uploaded) is None and _acks(session, uploaded) == []
    monkeypatch.setattr(settings, "ACKNOWLEDGE_APPLICATIONS", False)
    assert _acks(session, _applied(session)) == []


def test_a_google_form_response_is_acknowledged_as_the_web_form_is(session, monkeypatch):
    sent = []

    class Server:
        def send(self, to_address, subject, body):
            sent.append(to_address)

    monkeypatch.setattr(emails, "SmtpTransport", Server)
    # The form's responses come from the mailbox's own address (see tests/test_backend_inbox.py).
    for name, value in (("SMTP_HOST", "mail.example.org"), ("SMTP_FROM", MAILBOX), ("IMAP_USER", MAILBOX), ("GOOGLE_FORM_SENDERS", "")):
        monkeypatch.setattr(settings, name, value)
    o = _opening(session)
    row = _take(session, _form_mail(o))
    (ack,) = _acks(session, session.get(Application, row.application_id))
    assert sent == [ADDRESS] and (ack.status, ack.approved_by) == ("SENT", "system:acknowledgement")


# --- a resume that came by email ---------------------------------------------


def test_an_emailed_resume_is_acknowledged_only_as_a_draft_to_the_address_on_the_resume(session):
    o = _opening(session)
    row = _take(session, _mail(subject=f"Application for {o.reference}", sender="A Forwarder <forwarder@example.org>"))
    a = session.get(Application, row.application_id)
    assert _acks(session, a) == []  # nothing is known of the applicant until the resume is read
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    (ack,) = _acks(session, a)
    # To the address on the resume, not the sender's; and no one has approved it.
    assert (ack.status, ack.approved_by, ack.to_address) == ("DRAFT", None, ADDRESS)
    assert emails.send_acknowledgement(session, ack, Outbox()) is False and ack.status == "DRAFT"
    box = Outbox()
    assert emails.approve_and_send(session, [ack], box)["sent"] == 1 and ack.approved_by == "user:hr"
    gate1.read_again(session, a)
    read_application(session, a, FakeProvider(script=[_clean_result()]), fresh=True)
    assert len(_acks(session, a)) == 1  # reading again does not acknowledge again


# --- beside the decision letter ----------------------------------------------


def test_an_acknowledgement_never_stands_in_for_or_blocks_the_decision_letter(session, tmp_path):
    a = _assessed(session, tmp_path)
    ack = emails.acknowledge(session, a)  # no address typed on a form here, so a draft
    emails.approve_and_send(session, [ack], Outbox())
    assert (ack.status, a.status) == ("SENT", states.SHORTLISTED)
    gate2.approve(session, a)
    letter = emails.active_draft(session, a.application_id)
    assert (letter.kind, letter.status) == ("DECISION", "DRAFT") and letter.draft_id != ack.draft_id
    emails.approve_and_send(session, [letter], Outbox())
    assert a.status == states.CONTACTED

    # An acknowledgement sent late, after HR has decided, does not mark the candidate as informed of the decision.
    late = _assessed(session, tmp_path, _other_person())
    gate2.approve(session, late)
    emails.approve_and_send(session, [emails.acknowledge(session, late)], Outbox())
    assert late.status == states.HR_APPROVED

    # Withdrawing drops one not yet sent, like any other letter.
    gone = _applied(session)
    gate1.withdraw(session, gone)
    assert [d.status for d in _acks(session, gone)] == ["DISCARDED"]


# --- the pages ---------------------------------------------------------------


def test_applying_on_the_form_sends_it_after_the_page_has_answered_and_hr_can_see_that_it_went(client, engine, monkeypatch):
    from tests.test_backend_intake import _create_opening_via_page

    sent = []

    class Server:
        def send(self, to_address, subject, body):
            sent.append((to_address, subject))

    monkeypatch.setattr(emails, "SmtpTransport", Server)
    opening_id = _create_opening_via_page(client)
    quiet = client.post(f"/apply/{opening_id}", data=_data(email="first@example.org"), files={"resume": ("cv.docx", _docx(), DOCX)})
    assert quiet.status_code == 201 and "An acknowledgement is being sent" not in quiet.text and sent == []  # no mail server
    listing = client.get(f"/hr/openings/{opening_id}/emails").text
    assert "Acknowledgement" in listing and "Not yet sent to the candidate" in listing and listing.count('name="draft_id"') == 1

    for name, value in (("SMTP_HOST", "mail.example.org"), ("SMTP_FROM", "hr@example.org")):
        monkeypatch.setattr(settings, name, value)
    done = client.post(f"/apply/{opening_id}", data=_data(), files={"resume": ("cv.docx", _docx() + b"2", DOCX)})
    assert done.status_code == 201 and "An acknowledgement is being sent to the email address you gave." in done.text
    assert sent == [(ADDRESS, "Application APP-000002 received: Assistant Professor, MIT School of Computing")]
    assert "Sent automatically" in client.get(f"/hr/openings/{opening_id}/emails").text
    page = client.get("/hr/applications/2").text
    assert "<dt>Acknowledgement</dt>" in page and f"to {ADDRESS}" in page
    assert "No email is drafted" not in page and "Email to the candidate" not in page  # no decision yet, so no decision letter


# --- telling HR that a letter waits ------------------------------------------


def test_letters_no_one_has_approved_are_counted_per_opening_and_named_in_words(session, tmp_path):
    assert emails.waiting_for_approval(session) == {} and emails.waiting_in_words(None) == ""
    a = _assessed(session, tmp_path)
    ack = emails.acknowledge(session, a)  # a draft: no address was typed on a form
    assert emails.waiting_for_approval(session) == {a.opening_id: {"acknowledgements": 1, "decisions": 0}}
    gate2.approve(session, a)
    counts = emails.waiting_for_approval(session)[a.opening_id]
    assert counts == {"acknowledgements": 1, "decisions": 1} and emails.waiting_in_words(counts) == "1 acknowledgement and 1 decision letter"
    assert emails.waiting_in_words({"acknowledgements": 0, "decisions": 2}) == "2 decision letters"
    emails.approve_and_send(session, [ack])  # approved; with no mail server it waits on the server, not on a person
    assert emails.waiting_for_approval(session)[a.opening_id] == {"acknowledgements": 0, "decisions": 1}
    form = _applied(session)  # approved by the system: nothing for a person to do
    assert form.opening_id not in emails.waiting_for_approval(session)
    gate1.withdraw(session, a)
    assert emails.waiting_for_approval(session) == {}


def test_the_home_page_and_the_opening_page_say_that_letters_wait_and_link_to_them(client, engine, tmp_path):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        seed_rules(s)
        a = _assessed(s, tmp_path)
        opening_id, app_id = a.opening_id, a.application_id
        s.commit()
    assert "Emails waiting for your approval" not in client.get("/").text
    assert "waiting for your approval" not in client.get(f"/hr/openings/{opening_id}").text
    with _Session(engine) as s:
        emails.acknowledge(s, s.get(Application, app_id))
        s.commit()
    home = client.get("/").text
    assert "Emails waiting for your approval" in home and f'<a href="/hr/openings/{opening_id}/emails">' in home
    assert "1 acknowledgement</li>" in home
    page = client.get(f"/hr/openings/{opening_id}").text
    assert "<strong>1 acknowledgement waiting for your approval.</strong>" in page and "Read and approve them" in page
    client.post(f"/hr/applications/{app_id}/approve")
    assert "1 acknowledgement and 1 decision letter waiting for your approval." in client.get(f"/hr/openings/{opening_id}").text
