"""Gate 1: a person completes or corrects what the Reader could not settle.

An application in PENDING_REVIEW carries a list of reasons, each naming a
field. This module turns those reasons into the short list of fields a person
has to look at, checks what they enter, stores it, records every change in
`review_edits`, and moves the application to EXTRACTED once nothing is left.

Only the flagged fields are shown and only they can be changed (design
document, Section 9.6: "the specific fields are surfaced for manual
completion, not the whole record"). Nothing here calls a model, and nothing
here judges eligibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import get_args

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend import states
from backend.lists import CATEGORIES, STATES
from backend.models import (
    Application,
    Candidate,
    CandidateAchievement,
    CandidateEvent,
    CandidateExperience,
    CandidateGuidance,
    CandidateMembership,
    CandidatePersonalDetails,
    CandidateProfile,
    CandidatePublication,
    CandidateQualification,
    CandidateResearchProfile,
    CandidateSkill,
    CandidateSubjectTaught,
    ExtractedData,
    ReviewEdit,
)
from backend.reader_service import POSSIBLE_DUPLICATE
from llm.interface import HighestDegree

# No logins until Phase 9, so every edit is recorded against the HR role.
ACTOR = "user:hr"

_ITEM_TABLES = (
    CandidateQualification, CandidateExperience, CandidatePublication, CandidateSubjectTaught,
    CandidateSkill, CandidateEvent, CandidateAchievement, CandidateGuidance, CandidateMembership,
)

YES_NO = (("yes", "Yes"), ("no", "No"))


@dataclass(frozen=True)
class FieldSpec:
    label: str
    kind: str  # text / number / integer / date / choice
    where: str  # extracted / application / personal
    column: str
    choices: tuple[tuple[str, str], ...] = ()
    low: float | None = None
    high: float | None = None
    # May a person say "the resume does not give this" and leave it empty?
    may_be_empty: bool = True
    empty_label: str = "Not stated in the resume; leave empty"
    hint: str = ""


def _same(values) -> tuple[tuple[str, str], ...]:
    return tuple((v, v) for v in values)


FIELDS: dict[str, FieldSpec] = {
    "candidate_name": FieldSpec("Name", "text", "personal", "full_name", may_be_empty=False),
    "highest_degree": FieldSpec(
        "Highest completed qualification", "choice", "extracted", "highest_degree",
        choices=_same(get_args(HighestDegree)), may_be_empty=False,
        hint="A degree still in progress does not count.",
    ),
    "marks_pct": FieldSpec("Master's marks (percentage)", "number", "extracted", "marks_pct", low=0, high=100,
                           hint="Only if the resume gives a percentage. Do not convert a CGPA."),
    "cgpa": FieldSpec("Master's CGPA (out of 10)", "number", "extracted", "cgpa", low=0, high=10),
    "ug_marks_pct": FieldSpec("Bachelor's marks (percentage)", "number", "degree:UG", "marks_pct", low=0, high=100,
                              hint="From the marksheet. Do not convert a CGPA."),
    "ug_cgpa": FieldSpec("Bachelor's CGPA (out of 10)", "number", "degree:UG", "cgpa", low=0, high=10),
    "phd_status": FieldSpec(
        "Ph.D. status", "choice", "extracted", "phd_status", may_be_empty=False,
        choices=(("NOT_APPLICABLE", "No doctoral study"), ("REGISTERED", "Registered"), ("PURSUING", "Pursuing"),
                 ("THESIS_SUBMITTED", "Thesis submitted"), ("COMPLETED", "Completed (awarded)")),
    ),
    "phd_award_date": FieldSpec("Ph.D. award date", "date", "extracted", "phd_award_date"),
    "phd_regulation": FieldSpec(
        "Ph.D. Regulations it was awarded under", "choice", "extracted", "phd_regulation",
        choices=(("2009", "UGC Ph.D. Regulations, 2009"), ("2016", "UGC Ph.D. Regulations, 2016")),
        empty_label="Not known yet; to be taken from the certificate",
        hint="Resumes do not state this. It decides the NET/SET exemption (cl. 3.3).",
    ),
    "masters_award_date": FieldSpec("Master's award date", "date", "extracted", "masters_award_date"),
    "net_set_status": FieldSpec(
        "NET / SET / SLET", "choice", "extracted", "net_set_status", may_be_empty=False,
        choices=(("NET", "NET"), ("SET", "SET"), ("SLET", "SLET"), ("NONE", "None of these")),
        hint="A Ph.D. entrance test (PET) or GATE is not NET/SET.",
    ),
    "set_state": FieldSpec("State of the SET / SLET", "choice", "extracted", "set_state", choices=_same(STATES),
                           hint="A SET or SLET is valid only in its own State (cl. 3.3)."),
    "teaching_years_raw": FieldSpec(
        "Teaching experience stated (years)", "number", "extracted", "teaching_years", low=0, high=60,
        hint="The total the resume states, before any adjustment. Leave empty if it gives no total.",
    ),
    "publications_count": FieldSpec("Publications that have cleared review", "integer", "extracted",
                                    "publications_count", low=0, high=2000, may_be_empty=False),
    "publications_in_progress_count": FieldSpec("Publications still under review", "integer", "extracted",
                                                "publications_in_progress_count", low=0, high=2000, may_be_empty=False),
    # Answers the application form asks for. For a resume HR uploaded there
    # was no form, so a person enters them here; they are never read off the resume.
    "category": FieldSpec("Category", "choice", "application", "category", choices=_same(CATEGORIES),
                          empty_label="Not known; ask the applicant"),
    "state": FieldSpec("State of residence", "choice", "application", "applicant_state", choices=_same(STATES),
                       empty_label="Not known; ask the applicant"),
    "differently_abled": FieldSpec("Differently abled", "choice", "application", "differently_abled", choices=YES_NO,
                                   empty_label="Not known; ask the applicant"),
    "study_leave_taken": FieldSpec(
        "Study leave taken for a research degree", "choice", "application", "study_leave_taken", choices=YES_NO,
        empty_label="Not applicable, or not known",
        hint="Asked of the applicant (cl. 3.11). The resume is not relied on for this.",
    ),
}

# A reason against one of these is answered through another field.
_ALIASES = {
    "has_phd": "phd_status",
    "publication_titles": "publications_count",
    "publications_in_progress_titles": "publications_in_progress_count",
}
# Changing the first can make the second necessary, so they are shown together.
_COMPANIONS = {"net_set_status": "set_state", "phd_status": "phd_regulation"}

_EXTRACTED_FIELDS = tuple(n for n, s in FIELDS.items() if s.where in ("extracted", "personal"))

REASON_TEXT: dict[str, str] = {
    "missing_required": "The resume does not state this.",
    "low_confidence": "The reader was not sure of this value.",
    "no_evidence": "The reader gave a value but could not point to the line it came from.",
    "wrong_qualification_row": "The marks the reader found belong to a school or Bachelor's row, not the Master's.",
    "cgpa_not_percentage": "The figure looks like a grade point, not a percentage.",
    "manual_entry_required": "A resume does not say which Ph.D. Regulations applied.",
    "required_for_set": "A SET was found without its State.",
    "required_for_slet": "A SLET was found without its State.",
    "mentioned_in_resume": "The resume mentions NET or SET, but the reader recorded none.",
    "form_answer_missing": "An answer the application form asks for; this resume came without one.",
    "lighter_model_fallback": "Read by the back-up model, which is less accurate. Check every field.",
    "shown_with": "Shown because it depends on the field above.",
    "returned_by_hr": "Returned by HR for correction after the assessment.",
}


class ReviewError(Exception):
    """The review cannot be saved as entered. `errors` maps a field to what is wrong."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


@dataclass
class Flag:
    name: str
    spec: FieldSpec
    reasons: list[str] = field(default_factory=list)  # in words
    value: str = ""  # what the form field starts with
    evidence: str | None = None


def _target(session: Session, application: Application, spec: FieldSpec, create: bool = False):
    if spec.where == "extracted":
        return application.extracted
    if spec.where == "personal":
        return session.get(CandidatePersonalDetails, application.candidate_id)
    if spec.where.startswith("degree:"):
        # The candidate's degree at that level, as read from this application's resume.
        # If the Reader found none, one is added so the person has somewhere to put the marks.
        level = spec.where.split(":")[1]
        row = session.scalars(
            select(CandidateQualification)
            .where(CandidateQualification.application_id == application.application_id,
                   CandidateQualification.degree_level == level)
            .order_by(CandidateQualification.qualification_id)
        ).first()
        if row is None and create:
            row = CandidateQualification(candidate_id=application.candidate_id, application_id=application.application_id,
                                         degree_level=level, found_in_resume=False)
            session.add(row)
        return row
    return application


def _as_text(value) -> str:
    """A stored value as the text a form field holds."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def flagged_fields(session: Session, application: Application) -> list[Flag]:
    """The fields a person has to settle, in the order the form shows them."""
    extracted = application.extracted
    if extracted is None:
        return []
    by_field: dict[str, list[str]] = {}
    for reason in extracted.review_reasons or []:
        name, _, code = reason.partition(":")
        if reason == POSSIBLE_DUPLICATE:
            continue  # settled with its own buttons, not a field
        if code == "lighter_model_fallback":
            for each in _EXTRACTED_FIELDS:
                by_field.setdefault(each, []).append(REASON_TEXT[code])
            continue
        name = _ALIASES.get(name, name)
        if name in FIELDS:
            text = REASON_TEXT.get(code, code.replace("_", " ").capitalize() + ".")
            if text not in by_field.setdefault(name, []):
                by_field[name].append(text)
    for first, second in _COMPANIONS.items():
        if first in by_field and second not in by_field:
            by_field[second] = [REASON_TEXT["shown_with"]]

    flags = []
    for name, spec in FIELDS.items():  # FIELDS is in display order
        if name not in by_field:
            continue
        target = _target(session, application, spec)
        value = _as_text(getattr(target, spec.column, None)) if target is not None else ""
        evidence = (extracted.evidence or {}).get(name) if spec.where in ("extracted", "personal") else None
        flags.append(Flag(name, spec, by_field[name], value, evidence))
    return flags


def _parse(spec: FieldSpec, raw: str):
    """Text from the form as the value to store. Raises ValueError with a message for the person."""
    if spec.kind == "choice":
        if raw not in {c for c, _ in spec.choices}:
            raise ValueError("Choose one from the list.")
        return {"yes": True, "no": False}[raw] if spec.choices is YES_NO else raw
    if spec.kind == "text":
        text = " ".join(raw.split())
        if len(text) < 2 or len(text) > 200:
            raise ValueError("Enter between 2 and 200 characters.")
        return text
    if spec.kind == "date":
        try:
            value = date.fromisoformat(raw)
        except ValueError:
            raise ValueError("Enter a valid date.") from None
        if not date(1950, 1, 1) <= value <= date.today():
            raise ValueError("The date must be between 1950 and today.")
        return value
    try:
        number = int(raw) if spec.kind == "integer" else float(raw)
    except ValueError:
        raise ValueError("Enter a whole number." if spec.kind == "integer" else "Enter a number.") from None
    if number != number or not spec.low <= number <= spec.high:  # NaN, or out of range
        raise ValueError(f"Enter a number from {spec.low:g} to {spec.high:g}.")
    return number


def save_review(
    session: Session, application: Application, answers: dict[str, str], left_empty: set[str], actor: str = ACTOR
) -> list[str]:
    """Store a person's answers for every flagged field; returns the fields settled.

    All or nothing: if any flagged field has neither a valid value nor the
    "leave empty" tick, nothing is stored and ReviewError names each one.
    The application moves to EXTRACTED when no reason for review is left.
    """
    if application.status != states.PENDING_REVIEW:
        raise ReviewError({"": f"This application is not waiting for review (it is {states.in_words(application.status)})."})
    flags = flagged_fields(session, application)
    errors: dict[str, str] = {}
    new: dict[str, object] = {}
    for f in flags:
        raw = (answers.get(f.name) or "").strip()
        if raw and f.name in left_empty and f.spec.may_be_empty:
            # A value and "leave empty" together say two different things. Neither is assumed.
            errors[f.name] = "Either give a value or tick the box, not both."
        elif f.name in left_empty and f.spec.may_be_empty:
            new[f.name] = None
        elif not raw:
            errors[f.name] = ("Enter a value, or tick the box to leave it empty." if f.spec.may_be_empty
                              else "Enter a value.")
        else:
            try:
                new[f.name] = _parse(f.spec, raw)
            except ValueError as exc:
                errors[f.name] = str(exc)

    # Fields that only mean something alongside another.
    if "net_set_status" in new and "net_set_status" not in errors:
        if new["net_set_status"] in ("SET", "SLET"):
            if new.get("set_state") is None and "set_state" not in left_empty and "set_state" not in errors:
                errors["set_state"] = "Choose the State, or tick the box if it is not known."
        else:
            new["set_state"] = None
            errors.pop("set_state", None)
    if "phd_status" in new and "phd_status" not in errors and new["phd_status"] != "COMPLETED":
        new["phd_regulation"] = None
        errors.pop("phd_regulation", None)
    if errors:
        raise ReviewError(errors)

    for f in flags:
        value = new[f.name]
        target = _target(session, application, f.spec, create=True)
        old = getattr(target, f.spec.column)
        setattr(target, f.spec.column, value)
        if value is None:
            action = "LEFT_EMPTY"
        elif old is None:
            action = "ENTERED"
        else:
            action = "CONFIRMED" if _as_text(old) == _as_text(value) else "CORRECTED"
        session.add(ReviewEdit(
            application_id=application.application_id, field=f.name, action=action,
            old_value=_as_text(old)[:300] or None, new_value=_as_text(value)[:300] or None, actor=actor,
        ))

    extracted = application.extracted
    if "phd_status" in new:
        extracted.has_phd = new["phd_status"] == "COMPLETED"
    # The form answers are also kept on the candidate's own record.
    personal = session.get(CandidatePersonalDetails, application.candidate_id)
    if personal is not None:
        personal.category = application.category
        personal.differently_abled_flag = application.differently_abled
        personal.state = application.applicant_state or personal.state

    settled = [f.name for f in flags]
    _finish(session, application, [r for r in extracted.review_reasons or [] if r == POSSIBLE_DUPLICATE], actor,
            note="gate1: " + ", ".join(settled))
    return settled


def _finish(session: Session, application: Application, remaining: list[str], actor: str, note: str) -> None:
    """Record what is still open; with nothing open, the review is over."""
    extracted = application.extracted
    extracted.review_reasons, extracted.needs_review = remaining, bool(remaining)
    if not remaining and application.status == states.PENDING_REVIEW:
        # Field names only: the values are in review_edits, not in the audit trail.
        states.transition(session, application, states.EXTRACTED, actor, note=note[:500])
    session.flush()


def reopen_fields(session: Session, application: Application, fields: list[str], actor: str = ACTOR) -> list[str]:
    """Before assessment, open named fields for a person to enter or correct. Returns the fields opened."""
    if application.status != states.EXTRACTED or application.extracted is None:
        raise ReviewError({"": f"Fields can be reopened here only before assessment (this application is {states.in_words(application.status)})."})
    wanted = [f for f in fields if f in FIELDS]
    if not wanted:
        raise ReviewError({"": "Choose at least one field."})
    code = lambda name: "form_answer_missing" if FIELDS[name].where == "application" else "returned_by_hr"  # noqa: E731
    application.extracted.review_reasons = [f"{name}:{code(name)}" for name in wanted]
    application.extracted.needs_review = True
    states.transition(session, application, states.PENDING_REVIEW, actor, note="gate1: reopened for " + ", ".join(wanted))
    session.flush()
    return wanted


# --- possible duplicates -----------------------------------------------------


def duplicate_of(session: Session, application: Application) -> dict | None:
    """What a person needs to see to decide whether two records are one applicant."""
    if application.possible_duplicate_candidate_id is None:
        return None
    other_id = application.possible_duplicate_candidate_id
    personal = session.get(CandidatePersonalDetails, other_id)
    theirs = session.scalars(
        select(Application).where(Application.candidate_id == other_id).order_by(Application.application_id)
    ).all()
    return {"name": personal.full_name if personal else None, "email": session.get(Candidate, other_id).email,
            "applications": theirs}


def resolve_duplicate(session: Session, application: Application, same_person: bool, actor: str = ACTOR) -> None:
    """Record a person's decision on a possible duplicate.

    The same person: this application and everything read from its resume
    move to the candidate already held, and the stand-in record created for
    the upload is removed. Not the same: the flag is cleared and both stay.
    """
    other_id = application.possible_duplicate_candidate_id
    if other_id is None:
        raise ReviewError({"": "This application is not flagged as a possible duplicate."})
    app_id, old_id = application.application_id, application.candidate_id

    if same_person:
        clash = session.scalar(
            select(Application).where(
                Application.candidate_id == other_id, Application.opening_id == application.opening_id,
                Application.opening_id.is_not(None), Application.status != states.WITHDRAWN,
            )
        )
        if clash is not None:
            raise ReviewError({"": f"This person has already applied for this opening ({clash.reference}). "
                                   "Withdraw one of the two applications instead."})
        # The stand-in's profile points at a qualification row that is about to move.
        stub_profile = session.get(CandidateProfile, old_id)
        if stub_profile is not None:
            session.delete(stub_profile)
            session.flush()
        for table in _ITEM_TABLES:
            session.execute(update(table).where(table.application_id == app_id).values(candidate_id=other_id))
        for table in (CandidatePersonalDetails, CandidateResearchProfile):
            mine, theirs = session.get(table, old_id), session.get(table, other_id)
            if mine is None:
                continue
            if theirs is None:  # the candidate already held has none yet: keep what this resume gave
                columns = [c.key for c in table.__table__.columns if c.key != "candidate_id"]
                session.add(table(candidate_id=other_id, **{c: getattr(mine, c) for c in columns}))
            session.delete(mine)
        application.candidate_id = other_id
        session.flush()
        session.expire(application, ["candidate"])
        if not session.scalars(select(Application.application_id).where(Application.candidate_id == old_id)).first():
            session.delete(session.get(Candidate, old_id))

    application.possible_duplicate_candidate_id = None
    session.add(ReviewEdit(
        application_id=app_id, field="candidate", action="SAME_PERSON" if same_person else "DIFFERENT_PERSON",
        old_value=f"candidate {old_id}", new_value=f"candidate {other_id if same_person else old_id}", actor=actor,
    ))
    if application.extracted is not None:
        remaining = [r for r in application.extracted.review_reasons or [] if r != POSSIBLE_DUPLICATE]
        _finish(session, application, remaining, actor, note="gate1: possible duplicate settled")
    session.flush()


def withdraw(session: Session, application: Application, actor: str = ACTOR) -> None:
    """Take an application out of consideration (a second copy, or at the applicant's request), at any stage."""
    if states.WITHDRAWN not in states.ALLOWED[application.status]:
        raise ReviewError({"": f"An application that is {states.in_words(application.status)} cannot be withdrawn here."})
    application.possible_duplicate_candidate_id = None
    states.transition(session, application, states.WITHDRAWN, actor, note="withdrawn by HR")
    # A letter not yet sent must not go out to someone who has withdrawn.
    from backend.models import EmailDraft

    for draft in session.scalars(select(EmailDraft).where(
            EmailDraft.application_id == application.application_id, EmailDraft.status.in_(("DRAFT", "APPROVED")))):
        draft.status = "DISCARDED"
    session.flush()


# --- reading a resume again, and the dates of posts ---------------------------

# Until HR has decided, the record can still be put right.
BEFORE_HR_DECISION: frozenset[str] = frozenset({
    states.PENDING_REVIEW, states.EXTRACTED, states.SHORTLISTED, states.RE_CATEGORISED, states.NOT_ELIGIBLE,
    states.MANUAL_REVIEW,
})
_ASSESSED: frozenset[str] = BEFORE_HR_DECISION - {states.PENDING_REVIEW, states.EXTRACTED}


def _set_aside_assessment(session: Session, application: Application, actor: str) -> None:
    """An assessment made on the record as it was no longer stands. It is kept as history, marked as sent back."""
    if application.status in _ASSESSED:
        from backend.models import HrDecision

        session.add(HrDecision(application_id=application.application_id, action="RETURNED", actor=actor))


def read_again(session: Session, application: Application, actor: str = ACTOR) -> None:
    """Put a resume already read back in the reading queue, to be read afresh by the model.

    What the new reading finds replaces what the first one found, including
    corrections a person made to it; the list of those corrections is kept.
    The answers from the application form are not touched. Nothing is read
    here: the job waits until a person runs the queue.
    """
    if application.status not in BEFORE_HR_DECISION or application.extracted is None:
        raise ReviewError({"": f"A resume can be read again only after a first reading and before HR has decided (this application is {states.in_words(application.status)})."})
    from backend import jobs

    _set_aside_assessment(session, application, actor)
    session.add(ReviewEdit(application_id=application.application_id, field="resume", action="READ_AGAIN", actor=actor))
    states.transition(session, application, states.RECEIVED, actor, note="gate1: to be read again")
    jobs.enqueue_read(session, application, fresh=True)
    session.flush()


POST_KINDS: tuple[tuple[str, str], ...] = (
    ("TEACHING", "Teaching"), ("RESEARCH", "Research"), ("INDUSTRY", "Industry"), ("OTHER", "Other (not counted)"),
)
_DATE_SHAPES = (
    (re.compile(r"^(?P<y>\d{4})$"), "{y}"),
    (re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})$"), "{y}-{m:0>2}"),
    (re.compile(r"^(?P<m>\d{1,2})[-/](?P<y>\d{4})$"), "{y}-{m:0>2}"),
    (re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$"), "{y}-{m:0>2}-{d:0>2}"),
    (re.compile(r"^(?P<d>\d{1,2})[-/](?P<m>\d{1,2})[-/](?P<y>\d{4})$"), "{y}-{m:0>2}-{d:0>2}"),
)


def _post_date(raw: str | None) -> str | None:
    """A date typed for a post, to a year, a month or a day, as it is stored. Raises ValueError in words."""
    from backend.engine.facts import parse_period

    raw = (raw or "").strip()
    if not raw:
        return None
    text = next((shape.format(**m.groupdict()) for pattern, shape in _DATE_SHAPES if (m := pattern.match(raw))), None)
    period = parse_period(text)
    if period is None:
        raise ValueError("write the date as 2019, 07-2019 or 15-07-2019")
    if period.earliest.year < 1950 or period.earliest > date.today():
        raise ValueError("the date must be between 1950 and today")
    return text


def _house(text: str | None) -> str | None:
    """ "2019-07-15" as 15-07-2019, the order every other date on the pages is in."""
    return "-".join(reversed(text.split("-"))) if text else text


def _post_text(kind: str, start: str | None, end: str | None, current: bool) -> str:
    start, end = _house(start), _house(end)
    dates = f"{start or 'not dated'} to {'present' if current else (end or 'not stated')}" if (start or end or current) else "no dates"
    return f"{kind.capitalize()}, {dates}"


def _read_post(form: dict[str, str], key: str, label: str, errors: list[str]) -> tuple[str, str | None, str | None, bool] | None:
    """One post's type and dates from the form, checked. Adds to `errors` and returns None if it cannot stand."""
    from backend.engine.facts import parse_period

    kind = form.get(f"kind_{key}", "")
    current = bool(form.get(f"current_{key}"))
    problems = []
    if kind not in {k for k, _ in POST_KINDS}:
        problems.append("choose the type")
    dates: list[str | None] = []
    for name in ("start", "end"):
        try:
            dates.append(_post_date(form.get(f"{name}_{key}")))
        except ValueError as exc:
            problems.append(f"{'From' if name == 'start' else 'To'}: {exc}")
            dates.append(None)
    start, end = dates
    if not problems:
        if current and end:
            problems.append("give an end date or tick \"still in this post\", not both")
        elif end and not start:
            problems.append("an end date needs a start date")
        elif start and end and parse_period(end).latest < parse_period(start).earliest:
            problems.append("the end date is before the start date")
    if problems:
        errors.append(f"{label}: " + "; ".join(problems) + ".")
        return None
    return kind, start, end, current


def save_posts(session: Session, application: Application, form: dict[str, str], actor: str = ACTOR) -> int:
    """Store the type and dates a person entered for the posts held; returns how many posts changed or were added.

    Experience is counted from these dates (backend/engine/experience.py), so
    an assessment already made is set aside and the application waits to be
    assessed again. All or nothing: one bad date and nothing is stored.
    """
    if application.status not in BEFORE_HR_DECISION or application.extracted is None:
        raise ReviewError({"": f"Posts can be corrected only after the resume is read and before HR has decided (this application is {states.in_words(application.status)})."})
    app_id = application.application_id
    posts = session.scalars(
        select(CandidateExperience).where(CandidateExperience.application_id == app_id).order_by(CandidateExperience.experience_id)
    ).all()
    errors: list[str] = []
    changes: list[tuple[CandidateExperience, tuple]] = []
    for n, post in enumerate(posts, start=1):
        if f"kind_{post.experience_id}" not in form:
            continue  # not on the form that was sent; left alone
        new = _read_post(form, str(post.experience_id), f"Post {n}", errors)
        old = (post.experience_type, post.start_stated, post.end_stated, bool(post.is_current))
        if new is not None and new != old:
            changes.append((post, new))

    added = None
    designation = " ".join((form.get("new_designation") or "").split())[:200]
    employer = " ".join((form.get("new_employer") or "").split())[:250]
    if designation or employer or (form.get("start_new") or "").strip() or (form.get("end_new") or "").strip():
        added = _read_post(form, "new", "New post", errors)
        if added is not None and not (designation and added[1]):
            errors.append("New post: give at least the post and its start date.")
    if errors:
        raise ReviewError({"": " ".join(errors)})
    if not changes and added is None:
        raise ReviewError({"": "Nothing was changed."})

    for post, new in changes:
        n = posts.index(post) + 1
        old_text = _post_text(post.experience_type, post.start_stated, post.end_stated, bool(post.is_current))
        post.experience_type, post.start_stated, post.end_stated, post.is_current = new
        session.add(ReviewEdit(application_id=app_id, field=f"Post {n}: type and dates", action="CORRECTED",
                               old_value=old_text, new_value=_post_text(*new), actor=actor))
    if added is not None:
        kind, start, end, current = added
        session.add(CandidateExperience(
            candidate_id=application.candidate_id, application_id=app_id, found_in_resume=False,
            employer_name=employer or None, designation_held=designation, experience_type=kind,
            start_stated=start, end_stated=end, is_current=current,
        ))
        session.add(ReviewEdit(application_id=app_id, field=f"Post {len(posts) + 1}: added", action="ENTERED",
                               new_value=f"{designation[:150]}: {_post_text(*added)}", actor=actor))
    session.flush()

    if application.status in _ASSESSED:
        _set_aside_assessment(session, application, actor)
        states.transition(session, application, states.EXTRACTED, actor, note="gate1: posts corrected; to be assessed again")
    elif application.status == states.PENDING_REVIEW:
        # A total was asked for only because no teaching post could be dated. If one now can, it is not needed.
        dated = session.scalars(select(CandidateExperience.experience_id).where(
            CandidateExperience.application_id == app_id, CandidateExperience.experience_type == "TEACHING",
            CandidateExperience.start_stated.is_not(None))).first()
        reasons = list(application.extracted.review_reasons or [])
        if dated and "teaching_years_raw:missing_required" in reasons:
            reasons.remove("teaching_years_raw:missing_required")
            _finish(session, application, reasons, actor, note="gate1: posts dated")
    session.flush()
    return len(changes) + (1 if added is not None else 0)


def history(session: Session, application: Application) -> list[ReviewEdit]:
    return list(session.scalars(
        select(ReviewEdit).where(ReviewEdit.application_id == application.application_id).order_by(ReviewEdit.edit_id)
    ))
