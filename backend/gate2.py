"""Gate 2: HR approves, overrides or returns every outcome the engine produces.

Nothing the engine finds is final. A person either accepts it, replaces it
with their own decision and a recorded justification, or sends the
application back: to correct the record first (Gate 1 again, for the fields
they name) or to be assessed again as it stands.

Every decision is a row in `hr_decisions`; the engine's own finding stays in
`evaluation_results` beside it, so an override never erases what it overrode.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import gate1, states
from backend.assessor_service import latest_evaluation
from backend.models import Application, HrDecision

ACTOR = gate1.ACTOR  # no logins until Phase 9

AWAITING_HR: frozenset[str] = frozenset(
    {states.SHORTLISTED, states.RE_CATEGORISED, states.NOT_ELIGIBLE, states.MANUAL_REVIEW}
)
DECIDED: frozenset[str] = frozenset({states.HR_APPROVED, states.CONTACTED, states.INTERVIEW_SCHEDULED})
# What HR may decide. A lower post is still a short-listing, for that post.
FINAL_OUTCOMES: tuple[str, ...] = ("SHORTLISTED", "NOT_ELIGIBLE")
DESIGNATIONS: tuple[str, ...] = ("ASSISTANT_PROFESSOR", "ASSOCIATE_PROFESSOR", "PROFESSOR", "SENIOR_PROFESSOR")
MIN_JUSTIFICATION = 15
RETURNED = "returned_by_hr"


class DecisionError(Exception):
    """The decision cannot be recorded as entered. `errors` maps a form field to what is wrong."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _awaiting(application: Application) -> None:
    if application.status not in AWAITING_HR:
        raise DecisionError({"": f"This application is not waiting for an HR decision (it is {application.status})."})


def _record(session: Session, application: Application, action: str, outcome: str | None, designation: str | None,
            justification: str | None, actor: str) -> HrDecision:
    evaluation = latest_evaluation(session, application.application_id)
    row = HrDecision(
        application_id=application.application_id,
        evaluation_id=evaluation.evaluation_id if evaluation else None,
        action=action, final_outcome=outcome, final_designation=designation,
        justification=justification, actor=actor,
    )
    session.add(row)
    return row


def _draft_email(session: Session, application: Application, decision: HrDecision) -> None:
    from backend import emails  # emails builds on this module

    emails.draft_for_decision(session, application, decision)


def approve(session: Session, application: Application, actor: str = ACTOR) -> HrDecision:
    """Accept the engine's finding as it stands."""
    _awaiting(application)
    if application.status == states.MANUAL_REVIEW:
        raise DecisionError({"": "The rules could not settle this application, so there is no finding to approve. Record your own decision."})
    evaluation = latest_evaluation(session, application.application_id)
    outcome = "NOT_ELIGIBLE" if evaluation.outcome == "NOT_ELIGIBLE" else "SHORTLISTED"
    row = _record(session, application, "APPROVED", outcome, evaluation.eligible_designation, None, actor)
    states.transition(session, application, states.HR_APPROVED, actor, note="gate2: approved as assessed")
    session.flush()
    _draft_email(session, application, row)
    return row


def override(session: Session, application: Application, outcome: str, designation: str | None, justification: str,
             actor: str = ACTOR) -> HrDecision:
    """Record HR's own decision in place of the engine's finding, with the reason for it."""
    _awaiting(application)
    errors: dict[str, str] = {}
    justification = " ".join((justification or "").split())
    if outcome not in FINAL_OUTCOMES:
        errors["outcome"] = "Choose the decision."
    if outcome == "SHORTLISTED" and designation not in DESIGNATIONS:
        errors["designation"] = "Choose the post the candidate is short-listed for."
    if len(justification) < MIN_JUSTIFICATION:
        errors["justification"] = f"Give the reason in at least {MIN_JUSTIFICATION} characters; it is kept with the decision."
    elif len(justification) > 1000:
        errors["justification"] = "Keep the reason under 1000 characters."
    if errors:
        raise DecisionError(errors)
    decided_post = designation if outcome == "SHORTLISTED" else None
    action = "DECIDED" if application.status == states.MANUAL_REVIEW else "OVERRIDDEN"
    row = _record(session, application, action, outcome, decided_post, justification, actor)
    # The justification is in hr_decisions; the audit trail says only that there is one.
    states.transition(session, application, states.HR_APPROVED, actor,
                      note="gate2: decided by HR" if action == "DECIDED" else "gate2: overridden with a recorded justification")
    session.flush()
    _draft_email(session, application, row)
    return row


def return_for_correction(session: Session, application: Application, fields: list[str], actor: str = ACTOR) -> None:
    """Send the application back to Gate 1 so the named fields can be corrected, then assessed again."""
    _awaiting(application)
    wanted = [f for f in fields if f in gate1.FIELDS]
    if not wanted:
        raise DecisionError({"fields": "Tick at least one field to correct."})
    if application.extracted is None:
        raise DecisionError({"": "There is no extracted record to correct."})
    _record(session, application, "RETURNED", None, None, None, actor)
    application.extracted.review_reasons = [f"{name}:{RETURNED}" for name in wanted]
    application.extracted.needs_review = True
    states.transition(session, application, states.PENDING_REVIEW, actor, note="gate2: returned for " + ", ".join(wanted))
    session.flush()


def return_for_reassessment(session: Session, application: Application, actor: str = ACTOR) -> None:
    """Send the application back to be assessed again as it stands (after a rule or a record has changed)."""
    _awaiting(application)
    _record(session, application, "RETURNED", None, None, None, actor)
    states.transition(session, application, states.EXTRACTED, actor, note="gate2: returned for re-assessment")
    session.flush()


def out_of_date(session: Session, opening, today=None) -> list[Application]:
    """Applications awaiting HR whose assessment was counted on another date than the one now fixed for the opening.

    This happens by itself: an assessment made before the closing date counts
    to the day it was made, and once the closing date has passed the count
    belongs on that date. Nothing is changed here; a person asks for it.
    """
    from datetime import date

    from backend.engine.facts import counting_date

    counted_on, _, fixed = counting_date(opening, today or date.today())
    if not fixed:
        return []
    found = []
    for application in session.scalars(select(Application).where(
            Application.opening_id == opening.opening_id, Application.status.in_(AWAITING_HR)).order_by(Application.application_id)):
        evaluation = latest_evaluation(session, application.application_id)
        if evaluation is not None and (evaluation.details or {}).get("as_of") != counted_on.isoformat():
            found.append(application)
    return found


def reassess_out_of_date(session: Session, opening, today=None, actor: str = ACTOR) -> dict[str, int]:
    """Assess again every application `out_of_date` names; returns how many ended in each outcome."""
    from backend.assessor_service import assess_application

    counts: dict[str, int] = {}
    for application in out_of_date(session, opening, today):
        _record(session, application, "RETURNED", None, None, None, actor)
        states.transition(session, application, states.EXTRACTED, actor, note="gate2: to be counted on the opening's eligibility date")
        outcome = assess_application(session, application, today).outcome
        counts[outcome] = counts.get(outcome, 0) + 1
    session.flush()
    return counts


def reopen_decision(session: Session, application: Application, reason: str, actor: str = ACTOR) -> None:
    """Take back a decision the candidate has not yet been told of, with the reason for doing so.

    The decision and its reason stay on record; the letter drafted from it is
    dropped; and the application goes back to be assessed and decided again.
    Once the candidate has been informed it is too late for this: what was
    sent cannot be unsent, and a change then is a new letter, written by a person.
    """
    if application.status != states.HR_APPROVED:
        raise DecisionError({"": "Only a decision the candidate has not yet been informed of can be reopened "
                                 f"(this application is {application.status})."})
    reason = " ".join((reason or "").split())
    if len(reason) < MIN_JUSTIFICATION:
        raise DecisionError({"reason": f"Say why the decision is reopened, in at least {MIN_JUSTIFICATION} characters; it is kept on record."})
    if len(reason) > 1000:
        raise DecisionError({"reason": "Keep the reason under 1000 characters."})
    _record(session, application, "REOPENED", None, None, reason, actor)
    from backend.models import EmailDraft

    for draft in session.scalars(select(EmailDraft).where(
            EmailDraft.application_id == application.application_id, EmailDraft.kind == "DECISION",
            EmailDraft.status.in_(("DRAFT", "APPROVED")))):
        draft.status = "DISCARDED"
    states.transition(session, application, states.EXTRACTED, actor, note="gate2: decision reopened with a recorded reason")
    session.flush()


def fields_that_would_settle(evaluation) -> list[str]:
    """The Gate 1 fields behind the points the engine left open, so "send back" can offer them ready-ticked.

    Read from the stored working: each open requirement names what it is
    missing. A help to the person, who may tick more or fewer.
    """
    wanted: list[str] = []

    def want(*names: str) -> None:
        wanted.extend(n for n in names if n in gate1.FIELDS and n not in wanted)

    for rank in ((evaluation.details or {}).get("ranks") or []) if evaluation is not None else []:
        for check in rank.get("checks", []):
            if check["result"] != "UNKNOWN":
                continue
            key, detail = check["key"], check["detail"].lower()
            if key == "first_class":
                want("ug_marks_pct", "marks_pct")
            elif key == "marks":
                if "category" in detail or "disability" in detail:
                    want("category", "differently_abled")
                if "award date" in detail:
                    want("masters_award_date")
                if "cgpa" in detail or "not stated" in detail:
                    want("marks_pct")
            elif key == "net_set":
                if "state is not known" in detail:
                    want("set_state")
                elif "certificate" in detail:
                    want("phd_regulation")
                else:
                    want("net_set_status")
            elif key == "phd":
                want("phd_award_date" if "awarded on or before" in detail else "phd_status")
            elif key == "masters_by_date":
                want("masters_award_date")
            elif key == "experience" and "study leave" in detail:
                want("study_leave_taken")
            elif key == "post_phd":
                want("phd_award_date")
            elif key == "publications":
                want("publications_count")
    return wanted


def decisions(session: Session, application_id: int) -> list[HrDecision]:
    return list(session.scalars(
        select(HrDecision).where(HrDecision.application_id == application_id).order_by(HrDecision.decision_id)
    ))


def final_decision(session: Session, application: Application) -> HrDecision | None:
    """The decision in force for an HR_APPROVED application."""
    if application.status not in DECIDED:
        return None
    return next((d for d in reversed(decisions(session, application.application_id)) if d.action not in ("RETURNED", "REOPENED")), None)
