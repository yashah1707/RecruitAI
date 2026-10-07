"""Phase 7: plain-language findings, the digest, and Gate 3 (candidate emails).

No email is sent and no model is called anywhere in this file: sending goes to
a transport that records what it was given, and rewording to the fake provider.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from backend import emails, gate2, reporting, settings, states
from backend.assessor_service import latest_evaluation
from backend.models import EmailDraft
from backend.rules_seed import seed_rules
from llm.providers.fake_provider import FakeProvider
from tests.test_backend_foundation import client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_gate2 import _assessed, _assessed_on_server, _class_unstated, _no_net
from tests.test_backend_intake import _storage  # noqa: F401

TODAY = date(2026, 10, 7)


@pytest.fixture
def session(_plain_session):
    seed_rules(_plain_session)
    _plain_session.commit()
    return _plain_session


@pytest.fixture(autouse=True)
def _no_mail_server(monkeypatch):
    """Whatever the developer's .env says, these tests start with no mail server and no redirect."""
    for name, value in (("SMTP_HOST", ""), ("SMTP_FROM", ""), ("EMAIL_REDIRECT_TO", ""), ("INSTITUTION_NAME", "Sample University")):
        monkeypatch.setattr(settings, name, value)


class Outbox:
    """Stands in for the mail server."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.sent, self.fail = [], fail

    def send(self, to_address: str, subject: str, body: str) -> None:
        if self.fail:
            raise self.fail
        self.sent.append((to_address, subject, body))


def _decided(session, tmp_path, result=None, **opening):
    a = _assessed(session, tmp_path, result, **opening)
    gate2.approve(session, a)
    session.commit()
    return a, emails.active_draft(session, a.application_id)


# --- the finding in plain words ---------------------------------------------


def test_each_outcome_is_explained_in_a_sentence_with_its_clause_and_page(session, tmp_path):
    met = _assessed(session, tmp_path)
    text = reporting.explain(met, latest_evaluation(session, met.application_id))
    assert text.startswith("Meets every minimum requirement for Assistant Professor under the UGC Regulations, 2018, as amended")
    assert "Master's marks" in text and "NET / SET / SLET" in text


def test_a_failure_names_what_was_not_met_with_the_figures_and_the_citation(session, tmp_path):
    a = _assessed(session, tmp_path, _no_net())
    text = reporting.explain(a, latest_evaluation(session, a.application_id))
    assert "Does not meet the minimum requirements for Assistant Professor" in text and "or for any lower post" in text
    assert "NET / SET / SLET: no NET" in text and "cl. 3.3" in text and "2nd Amd. p. 2" in text


def test_a_lower_post_and_an_unsettled_case_are_explained_too(session, tmp_path):
    lower = _assessed(session, tmp_path, None, designation="PROFESSOR")
    text = reporting.explain(lower, latest_evaluation(session, lower.application_id))
    assert "Does not meet the minimum requirements for Professor" in text and "Ph.D.: a Ph.D. is required" in text
    assert text.endswith("Meets every minimum requirement for Assistant Professor.")
    assert reporting.explain(lower, None) == "Not yet assessed."


def test_an_aicte_case_names_its_own_regulation(session, tmp_path):
    a = _assessed(session, tmp_path, _class_unstated(), school_id="SCH-008", discipline_group="ENGINEERING_TECHNOLOGY")
    text = reporting.explain(a, latest_evaluation(session, a.application_id))
    assert text.startswith("The rules could not settle this application.") and "neither a class nor marks are stated" in text


# --- drafting ----------------------------------------------------------------


def test_a_decision_drafts_its_email_at_once_and_sends_nothing(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    assert a.status == states.HR_APPROVED
    assert (draft.status, draft.drafted_by, draft.to_address) == ("DRAFT", "template", "sample.exampleton@example.org")
    assert draft.subject == f"Application {a.reference}: short-listed for interview, Assistant Professor"
    assert draft.body.startswith("Dear A. Synthetic Candidate,") and a.reference in draft.body
    assert "short-listed for interview for the post of Assistant Professor" in draft.body
    assert "Short-listing is for interview only. Selection is made by the Selection Committee" in draft.body
    assert draft.body.rstrip().endswith("Human Resources\nSample University")
    assert draft.sent_at is None and draft.approved_at is None


def test_an_approved_not_eligible_finding_tells_the_candidate_why_with_the_clause(session, tmp_path):
    a, draft = _decided(session, tmp_path, _no_net())
    assert draft.subject == f"Application {a.reference}: outcome of screening, Assistant Professor"
    assert "the minimum qualifications are not met on the following" in draft.body
    assert "- NET / SET / SLET: no NET" in draft.body and "cl. 3.3" in draft.body
    assert "within seven days" in draft.body and emails.PLACEHOLDER not in draft.body


def test_a_candidate_short_listed_for_a_lower_post_is_told_both_things(session, tmp_path):
    a, draft = _decided(session, tmp_path, None, designation="PROFESSOR")
    assert draft.subject.endswith("short-listed for interview, Assistant Professor")
    assert "prescribed for Professor" in draft.body and "- Ph.D.: a Ph.D. is required" in draft.body
    assert "short-listed for interview for that post" in draft.body


def test_where_hr_decided_otherwise_the_engines_reasons_are_not_put_in_hrs_mouth(session, tmp_path):
    a = _assessed(session, tmp_path)  # the engine found the candidate eligible
    gate2.override(session, a, "NOT_ELIGIBLE", None, "Degree certificate could not be verified with the university.")
    draft = emails.active_draft(session, a.application_id)
    assert emails.PLACEHOLDER in draft.body and "could not be verified" not in draft.body  # the justification is internal
    with pytest.raises(emails.DraftError, match=r"\[HR: \.\.\.\]"):
        emails.approve(session, draft)
    emails.edit(session, draft, draft.to_address, draft.subject,
                draft.body.replace(f"{emails.PLACEHOLDER} state the reason for this decision here]", "- Your Master's degree could not be verified."))
    emails.approve(session, draft)
    assert draft.status == "APPROVED" and draft.drafted_by == "person"


# --- a person approves each send ---------------------------------------------


def test_nothing_is_sent_without_approval_and_approval_alone_sends_nothing(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    outbox = Outbox()
    with pytest.raises(emails.DraftError, match="only after a person has approved"):
        emails.send(session, draft, outbox)
    emails.approve(session, draft)
    assert (draft.status, draft.approved_by) == ("APPROVED", "user:hr") and outbox.sent == [] and a.status == states.HR_APPROVED


def test_an_approved_email_waits_when_no_mail_server_is_configured(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    emails.approve(session, draft)
    with pytest.raises(emails.DraftError, match="no mail server is configured"):
        emails.send(session, draft)
    assert draft.status == "APPROVED" and a.status == states.HR_APPROVED


def test_sending_records_it_and_moves_the_application_to_contacted(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    emails.approve(session, draft)
    outbox = Outbox()
    emails.send(session, draft, outbox)
    assert outbox.sent == [("sample.exampleton@example.org", draft.subject, draft.body)]
    assert draft.status == "SENT" and draft.sent_at is not None and draft.redirected is False
    assert a.status == states.CONTACTED
    last = a.transitions[-1]
    assert (last.from_state, last.to_state, last.note) == ("HR_APPROVED", "CONTACTED", "gate3: email approved and sent")
    assert "@" not in last.note  # the address is in email_drafts, not in the audit trail
    assert gate2.final_decision(session, a).action == "APPROVED"  # the decision still stands once the candidate is told
    for change in (lambda: emails.edit(session, draft, "x@example.org", "s", "b" * 50), lambda: emails.discard(session, draft),
                   lambda: emails.send(session, draft, outbox)):
        with pytest.raises(emails.DraftError):
            change()
    assert len(outbox.sent) == 1


def test_a_refused_send_keeps_the_email_approved_and_records_only_the_kind_of_failure(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    emails.approve(session, draft)
    with pytest.raises(emails.DraftError, match="did not accept"):
        emails.send(session, draft, Outbox(fail=ConnectionRefusedError("550 sample.exampleton@example.org rejected")))
    assert (draft.status, draft.error) == ("APPROVED", "ConnectionRefusedError") and a.status == states.HR_APPROVED
    emails.send(session, draft, Outbox())
    assert draft.status == "SENT" and draft.error is None


def test_in_development_every_email_goes_to_the_redirect_address_and_the_candidate_is_not_marked_contacted(session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_TO", "developer@example.org")
    a, draft = _decided(session, tmp_path)
    emails.approve(session, draft)
    outbox = Outbox()
    emails.send(session, draft, outbox)
    to, subject, _ = outbox.sent[0]
    assert to == "developer@example.org" and subject.startswith("[would go to the candidate] ")
    # A test copy is not a send: the email stays approved and the candidate is not marked as informed.
    assert (draft.status, draft.redirected) == ("APPROVED", True) and draft.sent_at is not None and a.status == states.HR_APPROVED
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_TO", "")
    emails.send(session, draft, outbox)  # the redirect is removed: now it goes for real
    assert outbox.sent[1][0] == "sample.exampleton@example.org" and not outbox.sent[1][1].startswith("[would go")
    assert (draft.status, draft.redirected) == ("SENT", False) and a.status == states.CONTACTED


def test_an_email_needs_an_address_and_a_changed_approval_has_to_be_given_again(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    emails.edit(session, draft, "", draft.subject, draft.body)
    with pytest.raises(emails.DraftError, match="no email address"):
        emails.approve(session, draft)
    for bad in (("not-an-address", draft.subject, draft.body), ("a@example.org", "", draft.body), ("a@example.org", "s", "too short")):
        with pytest.raises(emails.DraftError):
            emails.edit(session, draft, *bad)
    emails.edit(session, draft, " New.Address@Example.org ", draft.subject, draft.body)
    emails.approve(session, draft)
    emails.edit(session, draft, draft.to_address, draft.subject, draft.body)  # saved unchanged: still approved
    assert draft.status == "APPROVED" and draft.to_address == "new.address@example.org"
    emails.edit(session, draft, draft.to_address, draft.subject, draft.body + "P.S. Bring two photographs.")
    assert (draft.status, draft.approved_by) == ("DRAFT", None)


def test_a_discarded_draft_is_kept_as_history_and_is_no_longer_the_live_one(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    emails.discard(session, draft)
    assert emails.active_draft(session, a.application_id) is None
    assert [d.status for d in emails.history(session, a.application_id)] == ["DISCARDED"]


def test_the_smtp_transport_builds_a_plain_message_from_the_settings(monkeypatch):
    seen = {}

    class Server:
        def __init__(self, host, port, timeout):
            seen["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            seen["tls"] = True

        def login(self, user, password):
            seen["login"] = user

        def send_message(self, message):
            seen["message"] = (message["From"], message["To"], message["Subject"], message.get_content())

    for name, value in (("SMTP_HOST", "mail.example.org"), ("SMTP_PORT", 587), ("SMTP_FROM", "hr@example.org"),
                        ("SMTP_USER", "hr"), ("SMTP_PASSWORD", "made-up"), ("SMTP_STARTTLS", True)):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(emails.smtplib, "SMTP", Server)
    assert emails.mail_is_configured()
    emails.SmtpTransport().send("someone@example.org", "A subject", "A body.\n")
    assert seen == {"server": ("mail.example.org", 587), "tls": True, "login": "hr",
                    "message": ("hr@example.org", "someone@example.org", "A subject", "A body.\n")}


# --- rewording by the language model -----------------------------------------


def test_the_model_is_given_the_text_without_the_name_and_its_wording_is_kept_when_no_fact_changed(session, tmp_path):
    a, draft = _decided(session, tmp_path, _no_net())
    reworded = draft.body.replace("A. Synthetic Candidate", emails.NAME_TOKEN).replace("We regret that", "We are sorry to say that")
    provider = FakeProvider(texts=[reworded])
    emails.reword(session, draft, provider)
    instructions, sent = provider.text_calls[0]
    assert "A. Synthetic Candidate" not in sent and emails.NAME_TOKEN in sent and "you must not change what the letter says" in instructions
    assert "We are sorry to say that" in draft.body and draft.body.startswith("Dear A. Synthetic Candidate,")
    assert (draft.drafted_by, draft.status) == ("model", "DRAFT")


@pytest.mark.parametrize("damage, why", [
    (lambda t, ref: t.replace("cl. 3.3", "the rules"), "dropped the citation cl. 3.3"),
    (lambda t, ref: t.replace(ref, "your application"), "dropped the application reference"),
    (lambda t, ref: t.replace("seven days", "14 days"), "introduced figures"),
    (lambda t, ref: t.replace("Assistant Professor", "the post"), "dropped the post name Assistant Professor"),
    (lambda t, ref: t.replace(emails.NAME_TOKEN, "Applicant"), "dropped the greeting"),
    (lambda t, ref: "OK", "not a usable message"),
])
def test_a_rewording_that_changes_a_fact_is_refused_and_the_draft_is_untouched(session, tmp_path, damage, why):
    a, draft = _decided(session, tmp_path, _no_net())
    before = draft.body
    masked = before.replace("A. Synthetic Candidate", emails.NAME_TOKEN)
    with pytest.raises(emails.DraftError, match=why):
        emails.reword(session, draft, FakeProvider(texts=[damage(masked, a.reference)]))
    assert draft.body == before and draft.drafted_by == "template"


def test_rewording_fails_safely_when_the_model_is_down_or_the_draft_is_unfinished(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    before = draft.body
    with pytest.raises(emails.DraftError, match="not available"):
        emails.reword(session, draft, FakeProvider(texts=[RuntimeError("503")]))
    assert draft.body == before
    with pytest.raises(emails.DraftError, match="cannot write text"):
        emails.reword(session, draft, object())
    draft.body = before + f"\n{emails.PLACEHOLDER} more]"
    provider = FakeProvider()
    with pytest.raises(emails.DraftError, match="Complete the part"):
        emails.reword(session, draft, provider)
    assert provider.text_calls == []  # an unfinished letter is never sent to the model


# --- the digest --------------------------------------------------------------


def test_the_digest_groups_an_openings_applications_by_where_they_stand(session, tmp_path):
    a, draft = _decided(session, tmp_path)
    opening = a.opening
    d = reporting.digest(session, opening)
    assert [s["title"] for s in d["sections"]] == ["Decided by HR"]
    row = d["sections"][0]["rows"][0]
    assert row["finding"].startswith("Meets every minimum requirement") and row["decision"].startswith("Approved as assessed: short-listed")
    assert row["email"] == "DRAFT"
    assert d["totals"] == {"applications": 1, "shortlisted": 1, "not_eligible": 0, "emails_drafted": 1, "emails_approved": 0, "emails_sent": 0}
    emails.approve(session, draft)
    emails.send(session, draft, Outbox())
    d = reporting.digest(session, opening)
    assert d["totals"]["emails_sent"] == 1 and d["sections"][0]["rows"][0]["a"].status == states.CONTACTED


# --- the pages ---------------------------------------------------------------


def test_the_email_is_edited_and_approved_from_the_application_page_and_waits_without_a_mail_server(client, engine, tmp_path):
    app_id, opening_id = _assessed_on_server(engine, tmp_path, _no_net())
    assert "Email to the candidate" not in client.get(f"/hr/applications/{app_id}").text  # no decision, no email
    page = client.post(f"/hr/applications/{app_id}/approve").text
    assert "Email to the candidate" in page and "Nothing is sent until you approve it." in page and ">Approve</button>" in page
    assert "Does not meet the minimum requirements for Assistant Professor" in page  # the finding in plain words

    form = {"to_address": "sample.exampleton@example.org", "subject": "Outcome of screening", "do": "save",
            "body": "Dear Applicant,\n\nYour application has been examined. NET or SET was not found (cl. 3.3).\n\nHuman Resources"}
    assert "Draft saved." in client.post(f"/hr/applications/{app_id}/email", data=form).text
    waiting = client.post(f"/hr/applications/{app_id}/email", data={**form, "do": "approve"}).text
    assert "Approved, but not sent: no mail server is configured" in waiting and "Approved, not yet sent to the candidate." in waiting
    assert client.get(f"/applications/{app_id}").json()["status"] == "HR_APPROVED"
    bad = client.post(f"/hr/applications/{app_id}/email", data={**form, "to_address": "nope"}).text
    assert "Enter a valid email address." in bad


def test_rewording_and_discarding_from_the_page(client, engine, tmp_path):
    from sqlalchemy.orm import Session as _Session

    app_id, _ = _assessed_on_server(engine, tmp_path)
    client.post(f"/hr/applications/{app_id}/approve")
    with _Session(engine) as s:
        draft = s.scalars(select(EmailDraft)).one()
        form = {"to_address": draft.to_address, "subject": draft.subject, "body": draft.body}
    reworded = client.post(f"/hr/applications/{app_id}/email", data={**form, "do": "reword"}).text
    assert "Reworded by the language model. Read it before approving." in reworded and "the language model, from the system" in reworded
    gone = client.post(f"/hr/applications/{app_id}/email", data={**form, "do": "discard"}).text
    assert "Draft discarded. Nothing was sent." in gone and "No email is drafted for this decision." in gone
    assert "There is no email draft" in client.post(f"/hr/applications/{app_id}/email", data={**form, "do": "save"}).text


def test_the_digest_page(client, engine, tmp_path):
    app_id, opening_id = _assessed_on_server(engine, tmp_path, _no_net())
    page = client.get(f"/hr/openings/{opening_id}/digest").text
    assert "Assessed, waiting for an HR decision (1)" in page and "NET / SET / SLET: no NET" in page and "not ranked" in page
    client.post(f"/hr/applications/{app_id}/approve")
    page = client.get(f"/hr/openings/{opening_id}/digest").text
    assert "Decided by HR (1)" in page and "Approved as assessed: does not meet the minimum qualifications." in page and "Drafted" in page
    assert "0 short-listed for interview, 1 not meeting" in page
    assert f"/hr/openings/{opening_id}/digest" in client.get(f"/hr/openings/{opening_id}").text
    assert client.get("/hr/openings/999999/digest").status_code == 404


def test_the_page_says_where_the_email_will_really_go(client, engine, tmp_path, monkeypatch):
    app_id, _ = _assessed_on_server(engine, tmp_path)
    client.post(f"/hr/applications/{app_id}/approve")
    assert "Test mode." not in client.get(f"/hr/applications/{app_id}").text  # no mail server: nothing can go anywhere
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.example.org")
    monkeypatch.setattr(settings, "SMTP_FROM", "hr@example.org")
    live = client.get(f"/hr/applications/{app_id}").text
    assert "Live." in live and "delivers this email to the address in the To box" in live and "Approve and send" in live
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_TO", "catch-all@example.org")
    test_mode = client.get(f"/hr/applications/{app_id}").text
    assert "Test mode." in test_mode and "catch-all@example.org" in test_mode and "The candidate receives nothing" in test_mode
