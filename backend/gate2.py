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


def decisions(session: Session, application_id: int) -> list[HrDecision]:
    return list(session.scalars(
        select(HrDecision).where(HrDecision.application_id == application_id).order_by(HrDecision.decision_id)
    ))


def final_decision(session: Session, application: Application) -> HrDecision | None:
    """The decision in force for an HR_APPROVED application."""
    if application.status not in (states.HR_APPROVED, states.CONTACTED, states.INTERVIEW_SCHEDULED):
        return None
    return next((d for d in reversed(decisions(session, application.application_id)) if d.action != "RETURNED"), None)
