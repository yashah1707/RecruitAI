"""Editing an opening, and approving many candidate emails from one page.

No email is sent and no model is called: sending goes to a stand-in transport.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from backend import emails, gate2, intake, settings, states
from backend.assessor_service import assess_application, latest_evaluation
from backend.models import Department, EvaluationResult
from backend.rules_seed import seed_rules
from tests.test_backend_foundation import client, engine, session as _plain_session  # noqa: F401
from tests.test_backend_gate2 import _assessed, _assessed_on_server, _no_net, _other_person
from tests.test_backend_intake import _opening, _storage  # noqa: F401
from tests.test_backend_reporting import Outbox

TODAY = date(2026, 10, 8)


@pytest.fixture
def session(_plain_session):
    seed_rules(_plain_session)
    _plain_session.commit()
    return _plain_session


@pytest.fixture(autouse=True)
def _no_mail_server(monkeypatch):
    for name, value in (("SMTP_HOST", ""), ("SMTP_FROM", ""), ("EMAIL_REDIRECT_TO", "")):
        monkeypatch.setattr(settings, name, value)


# --- editing an opening ------------------------------------------------------


def test_the_title_department_and_closing_date_of_an_opening_can_be_corrected(session, tmp_path):
    a = _assessed(session, tmp_path)
    o = a.opening
    counts = intake.update_opening(session, o, discipline_group=o.discipline_group, department_name="  English   Literature ",
                                   title="Assistant Professor, English", closing_date=date(2026, 12, 31), today=TODAY)
    assert counts == {"reassess": 0, "decided": 0}  # the rules did not change, so no assessment is disturbed
    assert (o.title, o.department.name, o.closing_date) == ("Assistant Professor, English", "English Literature", date(2026, 12, 31))
    assert a.department_id == o.department_id and a.status == states.SHORTLISTED
    intake.update_opening(session, o, discipline_group=o.discipline_group, today=TODAY)  # emptied fields are cleared, not kept
    assert (o.title, o.department, o.closing_date, a.department_id) == (None, None, None, None)
    assert len(session.scalars(select(Department)).all()) == 1


def test_changing_the_rule_set_sets_aside_assessments_not_yet_decided_and_keeps_hrs_decisions(session, tmp_path):
    undecided = _assessed(session, tmp_path, None, school_id="SCH-008")  # School of Computing, wrongly opened under UGC
    o = undecided.opening
    decided = _assessed_into(session, tmp_path, o)
    gate2.approve(session, decided)
    assert undecided.status == states.SHORTLISTED and latest_evaluation(session, undecided.application_id).details["rule_version"].startswith("UGC")

    counts = intake.update_opening(session, o, discipline_group="ENGINEERING_TECHNOLOGY")
    assert counts == {"reassess": 1, "decided": 1}
    assert undecided.status == states.EXTRACTED and decided.status == states.HR_APPROVED
    assert undecided.transitions[-1].note == f"rule set of {o.reference} changed from GENERAL to ENGINEERING_TECHNOLOGY; to be assessed again"
    assert [d.action for d in gate2.decisions(session, undecided.application_id)] == ["RETURNED"]

    again = assess_application(session, undecided, TODAY)
    assert again.rule_version == "AICTE-DEGREE-2019"
    kept = [e.details["rule_version"] for e in session.scalars(select(EvaluationResult).where(
        EvaluationResult.application_id == undecided.application_id).order_by(EvaluationResult.evaluation_id))]
    assert kept == ["UGC-2018-AMD2-2023", "AICTE-DEGREE-2019"]  # the earlier finding is history, not erased


def _assessed_into(session, tmp_path, opening):
    from backend.reader_service import read_application
    from llm.providers.fake_provider import FakeProvider
    from tests.test_backend_foundation import _application

    a = _application(session, tmp_path, category="General", differently_abled=False, study_leave_taken=False)
    a.opening_id, a.school_id, a.applied_designation = opening.opening_id, opening.school_id, opening.designation
    read_application(session, a, FakeProvider(script=[_other_person()]))
    assess_application(session, a, TODAY)
    session.commit()
    return a


def test_a_bad_edit_is_refused_and_changes_nothing(session):
    o = _opening(session, advertisement_ref="AD-1", advertisement_date=date(2026, 10, 1))
    for bad, field in (({"discipline_group": "ASTROLOGY"}, "discipline_group"),
                       ({"discipline_group": o.discipline_group, "closing_date": date(2026, 9, 1)}, "closing_date")):
        with pytest.raises(intake.IntakeError) as exc:
            intake.update_opening(session, o, **bad)
        assert field in exc.value.errors
    assert o.discipline_group == "ENGINEERING_TECHNOLOGY" and o.closing_date is None


def test_a_closed_opening_can_be_reopened(session):
    o = _opening(session)
    intake.close_opening(session, o)
    assert not intake.is_accepting(o)
    intake.reopen_opening(session, o)
    assert intake.is_accepting(o) and o.closed_at is None


def test_editing_from_the_pages(client, engine, tmp_path):
    app_id, opening_id = _assessed_on_server(engine, tmp_path)
    form = client.get(f"/hr/openings/{opening_id}/edit").text
    assert "1 assessed application(s) will be set aside" in form and "The school and the post cannot be changed here" in form
    assert f"/hr/openings/{opening_id}/edit" in client.get(f"/hr/openings/{opening_id}").text
    bad = client.post(f"/hr/openings/{opening_id}/edit", data={"discipline_group": "GENERAL", "closing_date": "not-a-date", "title": "Kept"})
    assert bad.history[0].status_code == 303 and "Enter a valid date." in bad.text and 'value="Kept"' in bad.text
    done = client.post(f"/hr/openings/{opening_id}/edit", data={"discipline_group": "SCIENCE_HUMANITIES", "title": "Physics", "closing_date": "2026-12-31"}).text
    assert "Opening updated. The rule set changed, so 1 assessed application(s) were set aside" in done
    assert "31-12-2026" in done and "Assess 1 read application(s)" in done
    closed = client.post(f"/hr/openings/{opening_id}/close").text
    assert "Reopen this opening" in closed
    assert "Close this opening" in client.post(f"/hr/openings/{opening_id}/reopen").text
    assert client.get("/hr/openings/999999/edit").status_code == 404


# --- approving many emails ---------------------------------------------------


def _three_decided(session, tmp_path):
    """Three decided applications of one opening: one ready, one overridden (reason to be written), one with no address."""
    ready = _assessed(session, tmp_path, _no_net())
    o = ready.opening
    overridden, no_address = _assessed_into(session, tmp_path, o), _assessed_into(session, tmp_path, o)
    gate2.approve(session, ready)
    gate2.override(session, overridden, "NOT_ELIGIBLE", None, "Degree certificate could not be verified.")
    gate2.approve(session, no_address)
    emails.active_draft(session, no_address.application_id).to_address = None
    session.commit()
    return o, [emails.active_draft(session, a.application_id) for a in (ready, overridden, no_address)]


def test_only_letters_that_are_complete_can_be_approved_together_and_the_rest_are_left_alone(session, tmp_path):
    o, (ready, unwritten, no_address) = _three_decided(session, tmp_path)
    assert emails.problem_with(ready) is None
    assert "still to be written" in emails.problem_with(unwritten) and "No address" in emails.problem_with(no_address)
    outbox = Outbox()
    counts = emails.approve_and_send(session, [ready, unwritten, no_address], outbox)
    assert counts == {"sent": 1, "test_copies": 0, "waiting": 0, "failed": 0, "skipped": 2}
    assert [d.status for d in (ready, unwritten, no_address)] == ["SENT", "DRAFT", "DRAFT"] and len(outbox.sent) == 1
    assert emails.summarise_sending(counts) == "1 sent to candidates; 2 left as they were, not ready to approve."
    assert emails.summarise_sending(emails.approve_and_send(session, [], outbox)) == "Nothing was ticked."


def test_without_a_mail_server_the_ticked_letters_are_approved_and_wait(session, tmp_path):
    o, (ready, _, _) = _three_decided(session, tmp_path)
    counts = emails.approve_and_send(session, [ready])
    assert counts["waiting"] == 1 and ready.status == "APPROVED" and ready.approved_by == "user:hr"
    assert "no mail server is configured" in emails.summarise_sending(counts)


def test_one_refused_letter_does_not_stop_the_others_and_test_mode_sends_only_copies(session, tmp_path, monkeypatch):
    first = _assessed(session, tmp_path, _no_net())
    second = _assessed_into(session, tmp_path, first.opening)
    for a in (first, second):
        gate2.approve(session, a)
    drafts = [emails.active_draft(session, a.application_id) for a in (first, second)]

    class Flaky(Outbox):
        def send(self, to_address, subject, body):
            if not self.sent and not getattr(self, "failed_once", False):
                self.failed_once = True
                raise ConnectionError("made-up refusal")
            super().send(to_address, subject, body)

    counts = emails.approve_and_send(session, drafts, Flaky())
    assert (counts["failed"], counts["sent"]) == (1, 1) and [d.status for d in drafts] == ["APPROVED", "SENT"]
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_TO", "catch-all@example.org")
    box = Outbox()
    counts = emails.approve_and_send(session, [drafts[0]], box)
    assert counts["test_copies"] == 1 and box.sent[0][0] == "catch-all@example.org" and drafts[0].status == "APPROVED"
    assert first.status == states.HR_APPROVED  # a test copy informs no candidate


def test_the_emails_page_lists_every_letter_to_be_read_and_approves_only_what_is_ticked(client, engine, tmp_path):
    from sqlalchemy.orm import Session as _Session

    with _Session(engine) as s:
        seed_rules(s)
        o, drafts = _three_decided(s, tmp_path)
        opening_id, ids = o.opening_id, [d.draft_id for d in drafts]
        other = _assessed(s, tmp_path, _other_person())  # another opening's letter must not be reachable from here
        gate2.approve(s, other)
        s.commit()
        foreign = emails.active_draft(s, other.application_id).draft_id
    page = client.get(f"/hr/openings/{opening_id}/emails").text
    assert "No mail server is configured" in page and page.count('name="draft_id"') == 1  # only the complete letter can be ticked
    assert "The reason is still to be written" in page and "No address." in page
    assert "outcome of screening" in page and '<pre class="letter">' in page  # each letter can be opened and read here
    assert f"/hr/openings/{opening_id}/emails" in client.get(f"/hr/openings/{opening_id}").text

    nothing = client.post(f"/hr/openings/{opening_id}/emails", data={}).text
    assert "Nothing was ticked." in nothing
    done = client.post(f"/hr/openings/{opening_id}/emails", data={"draft_id": [str(ids[0]), str(ids[1]), str(foreign), "junk"]}).text
    assert "1 approved and waiting, because no mail server is configured; 1 left as they were" in done
    with _Session(engine) as s:
        from backend.models import EmailDraft

        assert s.get(EmailDraft, ids[0]).status == "APPROVED" and s.get(EmailDraft, ids[1]).status == "DRAFT"
        assert s.get(EmailDraft, foreign).status == "DRAFT"  # not this opening's: ignored
    assert client.get("/hr/openings/999999/emails").status_code == 404


def test_the_emails_page_says_which_mode_sending_is_in(client, engine, tmp_path, monkeypatch):
    app_id, opening_id = _assessed_on_server(engine, tmp_path)
    client.post(f"/hr/applications/{app_id}/approve")
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.example.org")
    monkeypatch.setattr(settings, "SMTP_FROM", "hr@example.org")
    assert "Live." in client.get(f"/hr/openings/{opening_id}/emails").text
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_TO", "catch-all@example.org")
    page = client.get(f"/hr/openings/{opening_id}/emails").text
    assert "Test mode." in page and "catch-all@example.org" in page and "Approve and send the ticked emails" in page
