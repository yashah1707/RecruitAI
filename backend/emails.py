"""Gate 3: candidate emails are drafted and queued, and a person approves each send.

A draft is written by code from HR's recorded decision the moment that
decision is made. Nothing is ever sent on its own: a person reads the draft,
may edit it, and approves it; only then does it go, and only if a mail
server is configured. Every draft, approval and send is a row in
`email_drafts`, and a successful send moves the application to CONTACTED.

A language model may reword the body at a person's request. It is given the
text with the candidate's name taken out, and its answer is refused unless it
keeps the application reference, every post named, every clause cited, and
introduces no figure that was not in the original. The model cannot change
what was decided, only how it is said.
"""

from __future__ import annotations

import logging
import re
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import gate2, settings, states
from backend.assessor_service import latest_evaluation
from backend.models import Application, CandidatePersonalDetails, EmailDraft, EvaluationResult, HrDecision
from backend.reporting import RANKS, _not_met, regulation_name

logger = logging.getLogger("recruitai.emails")

ACTOR = gate2.ACTOR
# Left in a draft where only a person can supply the words. A draft holding one cannot be approved.
PLACEHOLDER = "[HR:"
NAME_TOKEN = "{{candidate_name}}"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_REWORD_PROMPT = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "reword_email.md"


class DraftError(Exception):
    """The draft cannot be saved, approved or sent as it stands. The message is for the person."""


class Transport(Protocol):
    def send(self, to_address: str, subject: str, body: str) -> None: ...


class SmtpTransport:
    """Sends through the mail server named in the settings."""

    def send(self, to_address: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = settings.SMTP_FROM, to_address, subject
        message.set_content(body)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
            if settings.SMTP_STARTTLS:
                server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(message)


def mail_is_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


# --- drafting ----------------------------------------------------------------


def _name_and_address(session: Session, application: Application) -> tuple[str | None, str | None]:
    personal = session.get(CandidatePersonalDetails, application.candidate_id)
    name = application.applicant_name or (personal.full_name if personal else None)
    address = application.applicant_email or (personal.contact_email if personal else None)
    return name, address


def compose(application: Application, decision: HrDecision, evaluation: EvaluationResult | None, name: str | None) -> tuple[str, str]:
    """(subject, body) for the decision as recorded. Plain text; nothing here is decided, only stated."""
    applied = RANKS.get(application.applied_designation, application.applied_designation)
    school = application.school.name
    ref = application.reference
    details = (evaluation.details or {}) if evaluation else {}
    ranks = details.get("ranks") or []
    # The engine's reasons are quoted only where HR approved the engine's finding. Where HR
    # decided otherwise, the engine's reasons are not HR's, and a person writes the reason.
    engine_reasons = _not_met(ranks[0]) if decision.action == "APPROVED" and ranks else []
    reason_lines = "\n".join(f"  - {r}" for r in engine_reasons) or f"  {PLACEHOLDER} state the reason for this decision here]"
    greeting = f"Dear {name}," if name else "Dear Applicant,"
    closing = f"Yours sincerely,\nHuman Resources\n{settings.INSTITUTION_NAME}"

    if decision.final_outcome == "SHORTLISTED":
        post = RANKS.get(decision.final_designation, applied)
        subject = f"Application {ref}: short-listed for interview, {post}"
        opening = (f"Thank you for applying for the post of {applied} at {school}. "
                   f"Your application ({ref}) has been short-listed for interview for the post of {post}.")
        if post != applied:
            opening = (f"Thank you for applying for the post of {applied} at {school}. On the information in your application ({ref}), "
                       f"the minimum qualifications prescribed for {applied} under {regulation_name(details.get('rule_version'))} "
                       f"are not met on the following:\n\n{reason_lines}\n\n"
                       f"Your application meets the minimum qualifications for the post of {post}, and has been short-listed "
                       f"for interview for that post. If you do not wish to be considered for it, please let us know.")
        body = (f"{greeting}\n\n{opening}\n\n"
                "The date, time and place of the interview will be sent to you separately. Please keep the originals of your "
                "degree certificates, marksheets, experience certificates and publications ready for verification.\n\n"
                "Short-listing is for interview only. Selection is made by the Selection Committee on the interview.\n\n"
                f"{closing}\n")
        return subject, body

    subject = f"Application {ref}: outcome of screening, {applied}"
    body = (f"{greeting}\n\nThank you for applying for the post of {applied} at {school}. We have examined your application ({ref}) "
            f"against the minimum qualifications prescribed under {regulation_name(details.get('rule_version'))}.\n\n"
            f"We regret that, on the information in your application, the minimum qualifications are not met on the following:\n\n"
            f"{reason_lines}\n\n"
            "If any of this does not reflect your record, please write to us with the supporting document within seven days, "
            "quoting the reference above, and your application will be looked at again.\n\n"
            f"We thank you for your interest in {settings.INSTITUTION_NAME}.\n\n{closing}\n")
    return subject, body


def active_draft(session: Session, application_id: int) -> EmailDraft | None:
    return session.scalars(
        select(EmailDraft).where(EmailDraft.application_id == application_id, EmailDraft.status != "DISCARDED")
        .order_by(EmailDraft.draft_id.desc()).limit(1)
    ).first()


def history(session: Session, application_id: int) -> list[EmailDraft]:
    return list(session.scalars(select(EmailDraft).where(EmailDraft.application_id == application_id).order_by(EmailDraft.draft_id)))


def draft_for_decision(session: Session, application: Application, decision: HrDecision) -> EmailDraft:
    """Write the draft for a decision just recorded. One live draft per application."""
    existing = active_draft(session, application.application_id)
    if existing is not None and existing.status == "SENT":
        return existing
    if existing is not None:
        existing.status = "DISCARDED"
    name, address = _name_and_address(session, application)
    subject, body = compose(application, decision, latest_evaluation(session, application.application_id), name)
    draft = EmailDraft(application_id=application.application_id, decision_id=decision.decision_id, to_address=address,
                       subject=subject[:250], body=body, status="DRAFT", drafted_by="template")
    session.add(draft)
    session.flush()
    return draft


# --- a person edits, approves, discards --------------------------------------


def _editable(draft: EmailDraft) -> None:
    if draft.status not in ("DRAFT", "APPROVED"):
        raise DraftError(f"This email has been {draft.status.lower()} and can no longer be changed.")


def edit(session: Session, draft: EmailDraft, to_address: str, subject: str, body: str) -> None:
    """Save a person's changes. An approved draft that is changed has to be approved again."""
    _editable(draft)
    to_address, subject, body = to_address.strip().lower(), " ".join(subject.split()), body.replace("\r\n", "\n").strip() + "\n"
    if to_address and (not _EMAIL_RE.match(to_address) or len(to_address) > 254):
        raise DraftError("Enter a valid email address.")
    if not subject or len(subject) > 250:
        raise DraftError("Give the email a subject of up to 250 characters.")
    if len(body.strip()) < 40 or len(body) > 10000:
        raise DraftError("The message must be between 40 and 10,000 characters.")
    if (draft.to_address or "", draft.subject, draft.body) != (to_address, subject, body):
        draft.to_address, draft.subject, draft.body = to_address or None, subject, body
        draft.status, draft.approved_by, draft.approved_at, draft.drafted_by = "DRAFT", None, None, "person"
        draft.sent_at, draft.redirected = None, False  # a test copy of the earlier wording says nothing about this one
    session.flush()


def approve(session: Session, draft: EmailDraft, actor: str = ACTOR) -> None:
    """A person approves this email for sending. It is not sent by this call."""
    if draft.status != "DRAFT":
        raise DraftError(f"Only a draft can be approved; this email is {draft.status.lower()}.")
    if not draft.to_address:
        raise DraftError("There is no email address for this candidate. Enter one and save before approving.")
    if PLACEHOLDER in draft.body or PLACEHOLDER in draft.subject:
        raise DraftError("The draft still has a part marked [HR: ...] to be written. Complete it and save before approving.")
    draft.status, draft.approved_by, draft.approved_at = "APPROVED", actor, datetime.now(timezone.utc)
    session.flush()


def discard(session: Session, draft: EmailDraft) -> None:
    _editable(draft)
    draft.status = "DISCARDED"
    session.flush()


# --- sending -----------------------------------------------------------------


def send(session: Session, draft: EmailDraft, transport: Transport | None = None, actor: str = ACTOR) -> None:
    """Send an approved email. On success the application moves to CONTACTED."""
    if draft.status != "APPROVED":
        raise DraftError("An email is sent only after a person has approved it.")
    if transport is None:
        if not mail_is_configured():
            raise DraftError("Approved, but not sent: no mail server is configured (SMTP_HOST and SMTP_FROM).")
        transport = SmtpTransport()
    to_address, subject = draft.to_address, draft.subject
    if settings.EMAIL_REDIRECT_TO:
        # A development safeguard: every email goes to one address, never to a candidate.
        to_address, subject = settings.EMAIL_REDIRECT_TO, f"[would go to the candidate] {subject}"
    try:
        transport.send(to_address, subject, draft.body)
    except Exception as exc:  # the server's own words can hold an address, so only the kind is kept
        draft.error = type(exc).__name__[:120]
        session.flush()
        logger.warning("email_send_failed draft=%s kind=%s", draft.draft_id, draft.error)
        raise DraftError("The mail server did not accept the email. It is still approved; try sending again.") from exc
    draft.sent_at, draft.error = datetime.now(timezone.utc), None
    draft.redirected = bool(settings.EMAIL_REDIRECT_TO)
    if draft.redirected:
        # A test copy went to the redirect address. The candidate has received nothing, so the
        # email stays approved and can still be sent for real once the redirect is removed.
        session.flush()
        logger.info("email_test_copy_sent draft=%s application=%s", draft.draft_id, draft.application_id)
        return
    draft.status = "SENT"
    application = session.get(Application, draft.application_id)
    if application.status == states.HR_APPROVED:
        states.transition(session, application, states.CONTACTED, actor, note="gate3: email approved and sent")
    session.flush()
    logger.info("email_sent draft=%s application=%s redirected=%s", draft.draft_id, draft.application_id, draft.redirected)


# --- rewording by the language model -----------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_CLAUSE_RE = re.compile(r"(?:AICTE )?cl\. [\w.()]+")


def reword(session: Session, draft: EmailDraft, provider) -> None:
    """Ask the language model to reword the body, and keep its answer only if it changed no fact."""
    _editable(draft)
    if PLACEHOLDER in draft.body:
        raise DraftError("Complete the part marked [HR: ...] first, so the model is rewording a finished message.")
    write = getattr(provider, "draft_text", None)
    if write is None:
        raise DraftError("The configured language model cannot write text.")
    application = session.get(Application, draft.application_id)
    name, _ = _name_and_address(session, application)
    original = draft.body
    masked = original.replace(name, NAME_TOKEN) if name else original  # the candidate's name does not leave this machine
    try:
        answer = (write(_REWORD_PROMPT.read_text(encoding="utf-8"), masked) or "").strip()
    except Exception as exc:
        raise DraftError("The language model was not available. The draft is unchanged.") from exc

    problems = []
    if name and NAME_TOKEN not in answer:
        problems.append("it dropped the greeting")
    if application.reference not in answer:
        problems.append("it dropped the application reference")
    for post in {p for p in RANKS.values() if p in masked}:
        if post not in answer:
            problems.append(f"it dropped the post name {post}")
    for clause in set(_CLAUSE_RE.findall(masked)):
        if clause not in answer:
            problems.append(f"it dropped the citation {clause}")
    invented = set(_NUMBER_RE.findall(answer)) - set(_NUMBER_RE.findall(masked))
    if invented:
        problems.append("it introduced figures that were not in the draft")
    if PLACEHOLDER in answer or len(answer) < 40 or len(answer) > 10000:
        problems.append("its answer was not a usable message")
    if problems:
        raise DraftError("The reworded text was not kept, because " + "; ".join(sorted(problems)) + ". The draft is unchanged.")
    draft.body = (answer.replace(NAME_TOKEN, name) if name else answer) + "\n"
    draft.status, draft.approved_by, draft.approved_at, draft.drafted_by = "DRAFT", None, None, "model"
    session.flush()
