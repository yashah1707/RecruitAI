"""The application state machine (Section 9.3).

Every application is in exactly one state. The only way to change it is
`transition()`, which refuses a move the table below does not allow and
writes the audit row in the same unit of work, so the state and its history
cannot disagree.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Application, StateTransition

RECEIVED = "RECEIVED"
NEEDS_JOB_MATCH = "NEEDS_JOB_MATCH"
PARSING = "PARSING"
FAILED = "FAILED"
EXTRACTED = "EXTRACTED"
PENDING_REVIEW = "PENDING_REVIEW"
WITHDRAWN = "WITHDRAWN"
ASSESSED = "ASSESSED"
MANUAL_REVIEW = "MANUAL_REVIEW"
SHORTLISTED = "SHORTLISTED"
RE_CATEGORISED = "RE_CATEGORISED"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
HR_APPROVED = "HR_APPROVED"
CONTACTED = "CONTACTED"
INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"

# Section 9.3, plus three moves the document implies but does not draw:
#   NEEDS_JOB_MATCH   an emailed application with no matching opening (Section 16.1)
#   PARSING -> RECEIVED   the model was unavailable or out of quota; nothing is
#                         wrong with the file, so it goes back to wait, not to FAILED
#   FAILED -> RECEIVED    the candidate re-uploads a readable file (Section 9.6)
# and the two ways HR returns an assessed application at Gate 2 (Section 9.7:
# "return the application for re-assessment"):
#   outcome -> PENDING_REVIEW   to correct named fields first
#   outcome -> EXTRACTED        to be assessed again as it stands
# An applicant may withdraw at any point up to the interview, not only before reading
# (the document draws WITHDRAWN from PENDING_REVIEW alone), so every live state can reach it.
# and EXTRACTED -> PENDING_REVIEW, when HR reopens fields before assessment (to enter
# form answers an uploaded resume came without, or to correct something they noticed).
# and "read this resume again": any state before HR's decision can go back to RECEIVED,
# to wait in the reading queue (backend.jobs.read_again).
ALLOWED: dict[str, frozenset[str]] = {
    RECEIVED: frozenset({PARSING, FAILED, WITHDRAWN}),
    NEEDS_JOB_MATCH: frozenset({RECEIVED, WITHDRAWN}),
    PARSING: frozenset({EXTRACTED, PENDING_REVIEW, FAILED, RECEIVED}),
    FAILED: frozenset({RECEIVED, WITHDRAWN}),
    PENDING_REVIEW: frozenset({EXTRACTED, RECEIVED, WITHDRAWN}),
    EXTRACTED: frozenset({ASSESSED, MANUAL_REVIEW, PENDING_REVIEW, RECEIVED, WITHDRAWN}),
    ASSESSED: frozenset({SHORTLISTED, RE_CATEGORISED, NOT_ELIGIBLE}),
    SHORTLISTED: frozenset({HR_APPROVED, PENDING_REVIEW, EXTRACTED, RECEIVED, WITHDRAWN}),
    RE_CATEGORISED: frozenset({HR_APPROVED, PENDING_REVIEW, EXTRACTED, RECEIVED, WITHDRAWN}),
    NOT_ELIGIBLE: frozenset({HR_APPROVED, PENDING_REVIEW, EXTRACTED, RECEIVED, WITHDRAWN}),
    MANUAL_REVIEW: frozenset({HR_APPROVED, PENDING_REVIEW, EXTRACTED, RECEIVED, WITHDRAWN}),
    # Back to EXTRACTED when a decision not yet sent to the candidate is reopened (backend.gate2.reopen_decision).
    HR_APPROVED: frozenset({CONTACTED, EXTRACTED, WITHDRAWN}),
    CONTACTED: frozenset({INTERVIEW_SCHEDULED, WITHDRAWN}),
    INTERVIEW_SCHEDULED: frozenset(),
    WITHDRAWN: frozenset(),
}

ALL_STATES: frozenset[str] = frozenset(ALLOWED)

# A state as it is said to a person in a sentence ("this application is ..."). The codes above are for the
# database and the audit trail; a message on a page never shows one.
_IN_WORDS: dict[str, str] = {
    RECEIVED: "waiting to be read", NEEDS_JOB_MATCH: "not yet filed under an opening", PARSING: "being read",
    FAILED: "unreadable", EXTRACTED: "read and waiting to be assessed", PENDING_REVIEW: "waiting for its fields to be checked",
    WITHDRAWN: "withdrawn", ASSESSED: "being assessed", MANUAL_REVIEW: "assessed and waiting for a decision",
    SHORTLISTED: "assessed and waiting for a decision", RE_CATEGORISED: "assessed and waiting for a decision",
    NOT_ELIGIBLE: "assessed and waiting for a decision", HR_APPROVED: "already decided",
    CONTACTED: "decided, and the candidate has been informed", INTERVIEW_SCHEDULED: "scheduled for interview",
}


def in_words(state: str) -> str:
    return _IN_WORDS.get(state, state.replace("_", " ").lower())
INITIAL_STATES: frozenset[str] = frozenset({RECEIVED, NEEDS_JOB_MATCH})


class IllegalTransition(Exception):
    """A move the workflow does not permit."""


def record_initial(session: Session, application: Application, actor: str, note: str | None = None) -> None:
    """Write the first audit row for a newly created application."""
    if application.status is None:  # the column default applies only at flush
        application.status = RECEIVED
    if application.status not in INITIAL_STATES:
        raise IllegalTransition(f"an application cannot start in {application.status}")
    session.add(
        StateTransition(application=application, from_state=None, to_state=application.status, actor=actor, note=note)
    )


def transition(
    session: Session, application: Application, to_state: str, actor: str, note: str | None = None
) -> StateTransition:
    """Move `application` to `to_state` and record who did it.

    `note` is for a reason code or a clause reference. It must never carry
    personal data: the audit trail is read by people who should not all see
    a candidate's details.
    """
    from_state = application.status
    if to_state not in ALL_STATES:
        raise IllegalTransition(f"unknown state {to_state!r}")
    if to_state not in ALLOWED[from_state]:
        raise IllegalTransition(f"{from_state} -> {to_state} is not permitted")
    application.status = to_state
    row = StateTransition(application=application, from_state=from_state, to_state=to_state, actor=actor, note=note)
    session.add(row)
    return row
