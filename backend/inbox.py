"""Stage 1, second channel: applications that arrive by email, and through a Google Form.

A mailbox is read over IMAP when a person asks ("Check inbox" on the HR page,
or `python -m backend.inbox`). Nothing here runs on its own. The mailbox is
opened read-only: no message is deleted, moved or marked as read. Each
message is looked at once, remembered by its Message-ID, and sorted by fixed
rules into one of three kinds (design document, Section 16.1):

    APPLICATION   it carries a resume (PDF or DOCX)
    JOB_OPENING   it reads like a vacancy announcement
    OTHER         anything else

No language model is used to sort mail. A message the rules cannot place is
shown to a person, not guessed at; which rule decided is recorded on the row.

An application names its opening by reference ("OPN-00003") in the subject or
body. Whose resume it is comes from the resume, never from who sent the
message: resumes are forwarded. The sender is kept on the inbox row. One that names none, or one that is closed, is held as NEEDS_JOB_MATCH
until a person picks the opening. An opening is never created from an email:
the opening fixes which regulation a candidate is judged under, and that is
HR's choice.

The Google Form channel rides on the same mailbox. A short script on the form
(docs/GOOGLE_FORM_SETUP.md) emails each response, with the uploaded resume
attached and the answers as "Field: value" lines. Such a message is trusted
only from the form owner's own address, and its answers go through the same
checks as the web form.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import intake, settings
from backend.models import Application, InboxMessage, JobOpening
from backend.storage import ALLOWED_SUFFIXES, RejectedUpload, save_resume

logger = logging.getLogger("recruitai.inbox")

APPLICATION, JOB_OPENING, OTHER = "APPLICATION", "JOB_OPENING", "OTHER"
# What became of a message.
IMPORTED = "IMPORTED"                # an application was created from it
NEEDS_JOB_MATCH = "NEEDS_JOB_MATCH"  # an application, waiting for a person to pick the opening
FOR_HR = "FOR_HR"                    # a person should look: an opening announcement, or something incomplete
REJECTED = "REJECTED"                # an application that could not be accepted; `note` says why
IGNORED = "IGNORED"                  # not about recruitment, or set aside by a person

FORM_SUBJECT = "RecruitAI form response"
_REFERENCE_RE = re.compile(r"\bOPN-(\d{5})\b", re.IGNORECASE)
_APPLICATION_REFERENCE_RE = re.compile(r"\bAPP-(\d{6})\b", re.IGNORECASE)
ABOUT_APPLICATION = "about_an_existing_application"
_OPENING_WORDS_RE = re.compile(r"\b(vacanc(y|ies)|faculty (position|requirement)|recruitment|advertisement|openings? for|we are hiring)\b", re.IGNORECASE)
_APPLYING_WORDS_RE = re.compile(r"\b(appl(y|ying|ication)|resume|curriculum vitae|\bcv\b|candidature)\b", re.IGNORECASE)
# Anywhere in the part before the @: "noreply", and also "google-gemini-noreply" or "news.no-reply".
_AUTOMATIC_SENDERS_RE = re.compile(r"(^|[-_.+])(mailer-daemon|postmaster|no-?reply|do-?not-?reply)([-_.+]|$)", re.IGNORECASE)
_FORM_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z '/-]{1,40}?)\s*:\s*(.*?)\s*$")
# The labels the form script writes, and the form field each one fills.
_FORM_FIELDS = {
    "full name": "full_name", "email": "email", "phone": "phone", "state": "state", "category": "category",
    "differently abled": "differently_abled", "study leave": "study_leave_taken", "opening": "opening",
}
_YES_NO = {"yes": "yes", "no": "no", "y": "yes", "n": "no"}
_STUDY_LEAVE = {**_YES_NO, "not applicable": "na", "na": "na", "n/a": "na"}


class Mailbox(Protocol):
    def fetch(self, since: date, known: frozenset[str] = frozenset()) -> Iterable[bytes]:
        """The raw messages received on or after `since`, leaving out those whose Message-ID is in `known`."""


class ImapMailbox:
    """Reads the configured mailbox over IMAP, without changing anything in it."""

    def fetch(self, since: date, known: frozenset[str] = frozenset()) -> Iterable[bytes]:
        with imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT) as server:
            server.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
            server.select(settings.IMAP_FOLDER, readonly=True)
            _, found = server.search(None, "SINCE", since.strftime("%d-%b-%Y"))
            for number in (found[0] or b"").split():
                # First the one header that identifies it: a message seen before is not downloaded
                # again, attachments and all, on every check.
                _, head = server.fetch(number, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                header = next((p[1] for p in head if isinstance(p, tuple)), b"")
                message_id = _cut(email.message_from_bytes(header, policy=email.policy.default).get("Message-ID"), 250)
                if message_id and message_id in known:
                    continue
                _, parts = server.fetch(number, "(BODY.PEEK[])")  # PEEK: the message stays unread
                for part in parts:
                    if isinstance(part, tuple):
                        yield part[1]


def inbox_is_configured() -> bool:
    return bool(settings.IMAP_HOST and settings.IMAP_USER and settings.IMAP_PASSWORD)


# --- reading one message -----------------------------------------------------


def _body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        return ""
    if part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return text


def _resume_attachment(message: EmailMessage) -> tuple[str, bytes] | None:
    """The first attached PDF or DOCX. Pictures, signatures and other files are passed over."""
    for part in message.iter_attachments():
        name = part.get_filename() or ""
        if Path(name).suffix.lower() in ALLOWED_SUFFIXES:
            data = part.get_payload(decode=True)
            if data:
                return name, data
    return None


def _trusted_form_senders() -> set[str]:
    listed = {a.strip().lower() for a in settings.GOOGLE_FORM_SENDERS.split(",") if a.strip()}
    return listed or {settings.IMAP_USER.lower()} - {""}


def classify(sender: str, subject: str, body: str, has_resume: bool, automatic: bool) -> tuple[str, str]:
    """(kind, the rule that decided). Fixed rules, tried in order; the first that fits wins."""
    if automatic or _AUTOMATIC_SENDERS_RE.search(sender.split("@")[0]) and not subject.startswith(FORM_SUBJECT):
        return OTHER, "automatic_message"
    if subject.startswith(FORM_SUBJECT):
        return (APPLICATION, "google_form") if sender in _trusted_form_senders() else (OTHER, "form_subject_from_untrusted_sender")
    if sender == (settings.SMTP_FROM or settings.IMAP_USER).lower():
        return OTHER, "own_mail"
    if has_resume:
        return APPLICATION, "resume_attached"
    if _OPENING_WORDS_RE.search(subject) or (_OPENING_WORDS_RE.search(body) and not _APPLYING_WORDS_RE.search(subject)):
        return JOB_OPENING, "opening_words"
    if _APPLYING_WORDS_RE.search(subject) or _APPLYING_WORDS_RE.search(body[:2000]):
        return OTHER, "application_without_resume"
    return OTHER, "no_rule_matched"


def parse_form(body: str) -> dict[str, str]:
    """The answers in a form-response message, keyed as the application form's fields."""
    answers: dict[str, str] = {}
    for line in body.splitlines():
        m = _FORM_LINE_RE.match(line)
        if m and m.group(1).strip().lower() in _FORM_FIELDS:
            answers.setdefault(_FORM_FIELDS[m.group(1).strip().lower()], m.group(2).strip())
    return answers


def _opening_named(session: Session, text: str) -> JobOpening | None:
    m = _REFERENCE_RE.search(text)
    return session.get(JobOpening, int(m.group(1))) if m else None


def _cut(text: str | None, limit: int) -> str | None:
    text = " ".join((text or "").split())
    return text[:limit] or None


def process_message(session: Session, raw: bytes) -> InboxMessage | None:
    """Sort one message and act on it. Returns its row, or None if it was seen before."""
    message: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = _cut(message.get("Message-ID"), 250)
    if not message_id:
        # Nothing to remember it by; a made-up key from what it does have keeps it from being taken twice.
        message_id = "no-id:" + _cut(f"{message.get('Date')}|{message.get('From')}|{message.get('Subject')}", 240)
    if session.scalar(select(InboxMessage).where(InboxMessage.message_id == message_id)) is not None:
        return None

    sender_name, sender = parseaddr(str(message.get("From", "")))
    sender = sender.strip().lower()
    subject = _cut(str(message.get("Subject", "")), 300) or ""
    body = _body_text(message)
    attachment = _resume_attachment(message)
    automatic = str(message.get("Auto-Submitted", "no")).lower() not in ("no", "") or message.get("X-Autoreply") is not None
    # A mailing: it carries an unsubscribe header or is marked bulk. Such a message with no resume is not an
    # applicant writing, whatever words it uses. With a resume attached it is still looked at (a job portal forwards so).
    mailing = message.get("List-Unsubscribe") is not None or str(message.get("Precedence", "")).strip().lower() in ("bulk", "list", "junk")
    automatic = automatic or (mailing and attachment is None)
    try:
        received = parsedate_to_datetime(str(message.get("Date")))
        received = (received if received.tzinfo else received.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)

    kind, rule = classify(sender, subject, body, attachment is not None, automatic)
    about = None
    if rule in ("resume_attached", "application_without_resume", "opening_words", "no_rule_matched"):
        # Our own letters quote the application's reference and invite a reply with documents. Such a
        # reply is about that application; taking its attachment as a new resume would create a
        # second, false application for the same person.
        quoted = _APPLICATION_REFERENCE_RE.search(f"{subject}\n{body[:4000]}")
        about = session.get(Application, int(quoted.group(1))) if quoted else None
        if about is not None:
            kind, rule = OTHER, ABOUT_APPLICATION
    row = InboxMessage(message_id=message_id, received_at=received, sender=_cut(sender, 254), sender_name=_cut(sender_name, 200),
                       subject=subject, kind=kind, classified_by=f"rule:{rule}", status=IGNORED,
                       body_excerpt=_cut(body, 1200))
    session.add(row)

    if about is not None:
        row.application_id, row.status = about.application_id, FOR_HR
        row.note = f"A message about {about.reference}. Read it; if it changes the record, send that application back for correction."
        if attachment is not None:
            try:
                _, path = save_resume(*attachment)
                row.attachment_name, row.attachment_path = _cut(attachment[0], 255), str(path)
                row.note = f"A message about {about.reference}, with a document attached. " + row.note.split(". ", 1)[1]
            except RejectedUpload:
                pass
    elif kind == JOB_OPENING:
        row.status, row.note = FOR_HR, "Reads like a vacancy announcement. Create the opening yourself if it is one."
    elif rule == "application_without_resume":
        row.status, row.note = FOR_HR, "Mentions an application but has no PDF or DOCX attached. Ask the sender for the resume."
    elif rule == "form_subject_from_untrusted_sender":
        row.status, row.note = FOR_HR, "Has the form-response subject but did not come from the form owner's address. Not imported."
    elif kind == APPLICATION:
        _take_application(session, row, rule, subject, body, attachment)
    session.flush()
    logger.info("inbox_message kind=%s by=%s status=%s", row.kind, row.classified_by, row.status)  # no sender, no subject
    return row


def _take_application(session: Session, row: InboxMessage, rule: str, subject: str, body: str,
                      attachment: tuple[str, bytes] | None) -> None:
    if attachment is None:
        row.status, row.note = FOR_HR, "A form response with no resume attached. Ask the applicant for it."
        return
    filename, data = attachment
    try:
        _, path = save_resume(filename, data)
    except RejectedUpload as exc:
        row.status, row.note = REJECTED, f"The attached file was not accepted: {exc}."[:300]
        return
    row.attachment_name, row.attachment_path = _cut(filename, 255), str(path)

    if rule == "google_form":
        row.form_answers = parse_form(body)
        opening = _opening_named(session, row.form_answers.get("opening", "")) or _opening_named(session, subject)
    else:
        opening = _opening_named(session, f"{subject}\n{body}")
    if opening is None:
        row.status, row.note = NEEDS_JOB_MATCH, "Names no opening. Choose the opening it is for."
    elif not intake.is_accepting(opening):
        row.status, row.note = NEEDS_JOB_MATCH, f"Names {opening.reference}, which is not accepting applications. Choose an opening, or set it aside."
    else:
        import_into(session, row, opening)


def import_into(session: Session, row: InboxMessage, opening: JobOpening) -> None:
    """Create the application for a message whose opening is known. Sets the row's status either way."""
    data = Path(row.attachment_path).read_bytes()
    try:
        if row.form_answers is not None:
            a = row.form_answers
            form = intake.ApplicantForm(
                full_name=a.get("full_name", ""), email=a.get("email", ""), phone=a.get("phone", ""), state=a.get("state", ""),
                category=a.get("category", ""), differently_abled=_YES_NO.get(a.get("differently_abled", "").lower(), ""),
                study_leave_taken=_STUDY_LEAVE.get(a.get("study_leave", a.get("study_leave_taken", "")).lower(), ""),
                declaration=True,  # the form's own declaration question is required before it can be submitted
            )
            application = intake.submit_application(session, opening, form, row.attachment_name, data, source="GOOGLE_FORM")
        else:
            application = intake.email_application(session, opening, row.attachment_name, data)
    except intake.IntakeError as exc:
        problems = "; ".join(f"{k}: {v}" for k, v in exc.errors.items())
        if row.form_answers is not None and set(exc.errors) - {"email", "opening", "resume"}:
            row.status, row.note = FOR_HR, f"The form answers could not be accepted ({problems})."[:300]
        else:
            row.status, row.note = REJECTED, " ".join(exc.errors.values())[:300]  # the reason in words, without the field's name
        return
    row.application_id, row.status, row.note = application.application_id, IMPORTED, f"Filed under {opening.reference}."
    from backend import emails

    emails.send_acknowledgement(session, emails.active_draft(session, application.application_id, emails.ACKNOWLEDGEMENT))


# --- checking the mailbox ----------------------------------------------------


def check_inbox(session: Session, mailbox: Mailbox | None = None, days: int = 30, today: date | None = None) -> dict[str, int]:
    """Look at every message of the last `days` days not seen before. Returns how many ended in each status."""
    if mailbox is None:
        if not inbox_is_configured():
            raise RuntimeError("No mailbox is configured (IMAP_HOST, IMAP_USER, IMAP_PASSWORD).")
        mailbox = ImapMailbox()
    counts: dict[str, int] = {}
    known = frozenset(session.scalars(select(InboxMessage.message_id)))
    for raw in mailbox.fetch((today or date.today()) - timedelta(days=days), known):
        with session.begin_nested():  # one bad message does not undo the others
            row = process_message(session, raw)
        if row is not None:
            counts[row.status] = counts.get(row.status, 0) + 1
    session.flush()
    return counts


def summarise(counts: dict[str, int]) -> str:
    if not counts:
        return "No new messages."
    words = {IMPORTED: "filed as applications", NEEDS_JOB_MATCH: "waiting for you to choose the opening",
             FOR_HR: "for you to look at", REJECTED: "not accepted", IGNORED: "not about recruitment"}
    return f"{sum(counts.values())} new message(s): " + "; ".join(f"{n} {words[s]}" for s, n in counts.items()) + "."


def assign_opening(session: Session, row: InboxMessage, opening: JobOpening) -> None:
    """A person says which opening a held application is for."""
    if row.status != NEEDS_JOB_MATCH or not row.attachment_path:
        raise ValueError("This message is not waiting for an opening.")
    if not intake.is_accepting(opening):
        raise ValueError(f"{opening.reference} is not accepting applications.")
    import_into(session, row, opening)
    session.flush()


def set_aside(session: Session, row: InboxMessage) -> None:
    if row.status not in (NEEDS_JOB_MATCH, FOR_HR, REJECTED):
        raise ValueError("This message needs no action.")
    row.status, row.note = IGNORED, "Set aside by HR."
    session.flush()


_POSTS = (  # checked longest first, so "Assistant Professor" is not also read as "Professor"
    ("SENIOR_PROFESSOR", "senior professor"), ("ASSOCIATE_PROFESSOR", "associate professor"),
    ("ASSISTANT_PROFESSOR", "assistant professor"), ("PROFESSOR", "professor"),
)
_SCHOOL_FILLER = frozenset({"mit", "school", "college", "institute", "academy", "centre", "center", "department", "the", "and", "for"})


def _posts_named(text: str) -> set[str]:
    found = set()
    for code, phrase in _POSTS:
        if phrase in text:
            found.add(code)
            text = text.replace(phrase, " ")
    return found


def suggest_openings(row: InboxMessage, openings: list[JobOpening]) -> list[tuple[JobOpening, str]]:
    """Openings the message's own words point to, best first, each with what it matched on.

    A suggestion for the person choosing, worked out from the subject and the
    sender's text by fixed rules: the post named, and words of the department,
    school or opening title. It files nothing. A message that names nothing
    gets no suggestion, not a guess.
    """
    text = " ".join(f"{row.subject or ''} {row.body_excerpt or ''}".lower().split())
    posts = _posts_named(text)
    ranked = []
    for o in openings:
        why = []
        if o.designation in posts:
            why.append("the post (" + intake.DESIGNATIONS[o.designation] + ")")
        for label, name in (("the department", o.department.name if o.department else None), ("the opening's title", o.title)):
            if name and name.lower() in text:
                why.append(f"{label} ({name})")
        words = {w for w in re.findall(r"[a-z]{4,}", o.school.name.lower()) if w not in _SCHOOL_FILLER}
        hit = sorted(w for w in words if re.search(rf"\b{w}\b", text))
        if hit:
            why.append("the school (" + ", ".join(hit) + ")")
        if why:
            ranked.append((len(why), o, "the message mentions " + " and ".join(why)))
    ranked.sort(key=lambda item: (-item[0], -item[1].opening_id))
    return [(o, reason) for _, o, reason in ranked]


_NAME_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def sender_of(session: Session, application_id: int, resume_name: str | None = None) -> dict | None:
    """Who sent the message an application came in, and whether that looks like the applicant.

    The two names are compared word by word. Sharing a word (a first name or
    a surname) is taken as the same person; sharing none suggests the resume
    was forwarded. It is a note for the person reading, and decides nothing.
    """
    row = session.scalars(select(InboxMessage).where(
        InboxMessage.application_id == application_id, InboxMessage.status == IMPORTED)).first()
    if row is None or row.form_answers is not None:
        return None
    words = lambda text: {w.lower() for w in _NAME_WORD_RE.findall(text or "")}  # noqa: E731
    sent_by, on_resume = words(row.sender_name), words(resume_name)
    same = None if not sent_by or not on_resume else bool(sent_by & on_resume)
    return {"name": row.sender_name, "address": row.sender, "same_person": same}


def correspondence(session: Session, application_id: int) -> list[InboxMessage]:
    """Messages received about an application after it was made (replies to our letters, documents sent in)."""
    return list(session.scalars(
        select(InboxMessage).where(InboxMessage.application_id == application_id,
                                   InboxMessage.classified_by == f"rule:{ABOUT_APPLICATION}").order_by(InboxMessage.received_at)
    ))


def already_filed(session: Session, row: InboxMessage) -> list:
    """Applications already made from the very same file as this message's attachment, in any opening.

    The store names a file by the hash of its content, so the same resume sent
    again is recognised whatever it is called and whoever sends it. That is
    not a reason to refuse it: one person may apply for two posts. It is
    something the person deciding has to know before filing it a second time.
    """
    from backend import states

    if not row.attachment_path or row.classified_by == f"rule:{ABOUT_APPLICATION}":
        return []
    digest = Path(row.attachment_path).stem
    return list(session.scalars(
        select(Application).where(Application.resume_sha256 == digest, Application.status != states.WITHDRAWN)
        .order_by(Application.application_id)
    ))


def waiting(session: Session) -> list[InboxMessage]:
    """Messages a person has to act on, oldest first."""
    return list(session.scalars(
        select(InboxMessage).where(InboxMessage.status.in_((NEEDS_JOB_MATCH, FOR_HR, REJECTED))).order_by(InboxMessage.received_at)
    ))


if __name__ == "__main__":  # python -m backend.inbox
    from backend.db import get_engine

    with Session(get_engine()) as s:
        print(summarise(check_inbox(s)))
        s.commit()
