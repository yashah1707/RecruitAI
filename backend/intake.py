"""Stage 1 (Intake): openings, and applications arriving against them.

Every application belongs to an opening, and takes its school, department,
designation and discipline group from that opening. Those are never read off
the resume (design document, Section 8): they decide which regulation the
candidate is judged under.

Nothing here calls a model. An accepted application is stored in RECEIVED
state and a job is queued for the Reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend import states
from backend.jobs import enqueue_read
from backend.lists import CATEGORIES, STATES  # noqa: F401  (also used through this module)
from backend.models import Application, Candidate, Department, JobOpening, RecruitmentDrive, School
from backend.storage import RejectedUpload, save_resume

DESIGNATIONS: dict[str, str] = {
    "ASSISTANT_PROFESSOR": "Assistant Professor",
    "ASSOCIATE_PROFESSOR": "Associate Professor",
    "PROFESSOR": "Professor",
    "SENIOR_PROFESSOR": "Senior Professor",
}

# "GENERAL" is UGC cl. 4.1. The rest are the AICTE (Degree) Regulation, 2019
# groups a rule exists for (backend/rules_data.py).
DISCIPLINE_GROUPS: dict[str, str] = {
    "GENERAL": "UGC: arts, sciences, commerce, humanities, law and related disciplines",
    "ENGINEERING_TECHNOLOGY": "AICTE: Engineering / Technology",
    "MANAGEMENT": "AICTE: Management",
    "MCA": "AICTE: MCA",
    "DESIGN": "AICTE: Design",
    "PHARMACY": "AICTE: Pharmacy",
    "HMCT": "AICTE: Hotel Management and Catering Technology",
    "ARCHITECTURE": "AICTE: Architecture",
    "TOWN_PLANNING": "AICTE: Town Planning",
    "FINE_ARTS": "AICTE: Fine Arts",
    "SCIENCE_HUMANITIES": "AICTE institution, science and humanities faculty (assessed under UGC)",
    # Disciplines with their own clause in the UGC Regulations. Their rules are not loaded,
    # so these posts are assessed by a person; choosing "UGC" for them would apply the wrong clause.
    "UGC_4_2_PERFORMING_VISUAL_ARTS": "UGC cl. 4.2: music, performing arts, visual arts (assessed by a person)",
    "UGC_4_3_DRAMA": "UGC cl. 4.3: drama (assessed by a person)",
    "UGC_4_4_YOGA": "UGC cl. 4.4: yoga (assessed by a person)",
    "UGC_4_5_OCCUPATIONAL_THERAPY": "UGC cl. 4.5: occupational therapy (assessed by a person)",
    "UGC_4_6_PHYSIOTHERAPY": "UGC cl. 4.6: physiotherapy (assessed by a person)",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_DIGITS_RE = re.compile(r"\d")


class IntakeError(Exception):
    """The submission cannot be accepted. `errors` maps a field to what is wrong with it."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def suggested_discipline_group(school: School) -> str:
    """A starting value for the opening form. HR confirms or changes it."""
    name = school.name.lower()
    if school.overlay_regulator_id != "AICTE":
        if "drama" in name:
            return "UGC_4_3_DRAMA"
        if "fine arts" in name or "sangeet" in name or "music" in name:
            return "UGC_4_2_PERFORMING_VISUAL_ARTS"
        return "GENERAL"
    if "design" in name:
        return "DESIGN"
    if "management" in name or "business" in name:
        return "MANAGEMENT"
    return "ENGINEERING_TECHNOLOGY"


# --- openings ----------------------------------------------------------------


def create_opening(
    session: Session,
    *,
    school_id: str,
    designation: str,
    discipline_group: str,
    department_name: str | None = None,
    title: str | None = None,
    closing_date: date | None = None,
    advertisement_ref: str | None = None,
    advertisement_date: date | None = None,
) -> JobOpening:
    errors: dict[str, str] = {}
    school = session.get(School, school_id)
    if school is None or not school.is_hiring_unit:
        errors["school_id"] = "Choose a school from the list."
    if designation not in DESIGNATIONS:
        errors["designation"] = "Choose a designation from the list."
    if discipline_group not in DISCIPLINE_GROUPS:
        errors["discipline_group"] = "Choose a discipline group from the list."
    if closing_date and advertisement_date and closing_date < advertisement_date:
        errors["closing_date"] = "The closing date is before the advertisement date."
    if errors:
        raise IntakeError(errors)

    department = None
    department_name = " ".join((department_name or "").split())
    if department_name:
        department = session.scalar(
            select(Department).where(
                Department.school_id == school_id, func.lower(Department.name) == department_name.lower()
            )
        )
        if department is None:
            department = Department(school_id=school_id, name=department_name[:200])
            session.add(department)
            school.has_departments = True

    drive = None
    advertisement_ref = (advertisement_ref or "").strip()
    if advertisement_ref:
        drive = session.scalar(
            select(RecruitmentDrive).where(
                RecruitmentDrive.school_id == school_id,
                RecruitmentDrive.advertisement_ref == advertisement_ref,
                RecruitmentDrive.designation == designation,
            )
        )
        if drive is None:
            drive = RecruitmentDrive(
                school_id=school_id, designation=designation, advertisement_ref=advertisement_ref[:80],
                advertisement_date=advertisement_date or date.today(), closing_date=closing_date, status="OPEN",
            )
            session.add(drive)

    opening = JobOpening(
        school_id=school_id, department=department, drive=drive, designation=designation,
        discipline_group=discipline_group, title=(" ".join((title or "").split())[:200] or None),
        closing_date=closing_date, status="OPEN",
    )
    session.add(opening)
    session.flush()
    return opening


def close_opening(session: Session, opening: JobOpening) -> None:
    if opening.status != "CLOSED":
        opening.status = "CLOSED"
        opening.closed_at = datetime.now(timezone.utc)
        session.flush()


def is_accepting(opening: JobOpening, today: date | None = None) -> bool:
    if opening.status != "OPEN":
        return False
    return opening.closing_date is None or (today or date.today()) <= opening.closing_date


# --- applications ------------------------------------------------------------


@dataclass
class ApplicantForm:
    """What the applicant types. All of it is validated before anything is stored."""

    full_name: str = ""
    email: str = ""
    phone: str = ""
    state: str = ""
    category: str = ""
    differently_abled: str = ""  # "yes" / "no"
    study_leave_taken: str = ""  # "yes" / "no" / "na"
    declaration: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    def validate(self) -> dict[str, str]:
        e: dict[str, str] = {}
        self.full_name = " ".join(self.full_name.split())
        self.email = self.email.strip().lower()
        self.phone = " ".join(self.phone.split())
        if len(self.full_name) < 2:
            e["full_name"] = "Enter your full name."
        elif len(self.full_name) > 200:
            e["full_name"] = "The name is too long."
        if not _EMAIL_RE.match(self.email) or len(self.email) > 254:
            e["email"] = "Enter a valid email address."
        digits = len(_PHONE_DIGITS_RE.findall(self.phone))
        if not 10 <= digits <= 15 or len(self.phone) > 40:
            e["phone"] = "Enter a phone number with 10 to 15 digits."
        if self.state not in STATES:
            e["state"] = "Choose your state from the list."
        if self.category not in CATEGORIES:
            e["category"] = "Choose a category from the list."
        if self.differently_abled not in ("yes", "no"):
            e["differently_abled"] = "Answer yes or no."
        if self.study_leave_taken not in ("yes", "no", "na"):
            e["study_leave_taken"] = "Choose one of the three answers."
        if not self.declaration:
            e["declaration"] = "Please confirm the declaration."
        self.errors = e
        return e


def _new_application(session: Session, opening: JobOpening, candidate: Candidate, filename: str, data: bytes,
                     source: str, **fields) -> Application:
    digest, path = save_resume(filename, data)
    suffix = path.suffix
    base = filename.replace("\\", "/").rsplit("/", 1)[-1] or f"resume{suffix}"
    if len(base) > 255:
        base = base[: 255 - len(suffix)] + suffix
    application = Application(
        candidate=candidate, opening=opening, school_id=opening.school_id, department_id=opening.department_id,
        applied_designation=opening.designation, status=states.RECEIVED, resume_source=source,
        resume_filename=base, resume_path=str(path), resume_sha256=digest, **fields,
    )
    session.add(application)
    states.record_initial(session, application, actor="system", note=f"source={source}; opening={opening.reference}")
    session.flush()
    enqueue_read(session, application)
    return application


def submit_application(session: Session, opening: JobOpening, form: ApplicantForm, filename: str, data: bytes) -> Application:
    """An applicant's own submission through the web form."""
    errors = form.validate()
    if not is_accepting(opening):
        errors["opening"] = "This opening is no longer accepting applications."

    candidate = None
    if "email" not in errors:
        candidate = session.scalar(select(Candidate).where(Candidate.email == form.email))
        if candidate is not None:
            already = session.scalar(
                select(Application).where(
                    Application.candidate_id == candidate.candidate_id,
                    Application.opening_id == opening.opening_id,
                    Application.status != states.WITHDRAWN,
                )
            )
            if already is not None:
                errors["email"] = (
                    f"An application from this email address has already been received for this opening "
                    f"(reference {already.reference})."
                )
    if not errors:
        try:
            if not filename:
                raise RejectedUpload("attach your resume as a PDF or DOCX file")
            application = _new_application(
                session, opening, candidate or Candidate(email=form.email), filename, data, "WEB_FORM",
                applicant_name=form.full_name, applicant_email=form.email, applicant_phone=form.phone,
                applicant_state=form.state, category=form.category,
                differently_abled=form.differently_abled == "yes",
                study_leave_taken={"yes": True, "no": False, "na": None}[form.study_leave_taken],
            )
            return application
        except RejectedUpload as exc:
            errors["resume"] = str(exc)[:1].upper() + str(exc)[1:] + "."
    form.errors = errors
    raise IntakeError(errors)


# An application may be moved until HR has decided it.
MOVABLE: frozenset[str] = frozenset({
    states.RECEIVED, states.PENDING_REVIEW, states.EXTRACTED, states.FAILED,
    states.SHORTLISTED, states.RE_CATEGORISED, states.NOT_ELIGIBLE, states.MANUAL_REVIEW,
})
_ASSESSED: frozenset[str] = frozenset({states.SHORTLISTED, states.RE_CATEGORISED, states.NOT_ELIGIBLE, states.MANUAL_REVIEW})


def move_application(session: Session, application: Application, target: JobOpening, actor: str = "user:hr") -> None:
    """Put an application under another opening (it was filed against the wrong post).

    The post, school, department and rule set all come from the opening, so
    they change with it and nothing is read again. An assessment made under
    the old opening no longer applies: the application goes back to be
    assessed under the new one, and the old finding is kept as history.
    """
    from backend.models import HrDecision, ReviewEdit

    if application.status not in MOVABLE:
        raise IntakeError({"opening": f"An application that is {application.status} cannot be moved."})
    if target.opening_id == application.opening_id:
        raise IntakeError({"opening": "The application is already under that opening."})
    if target.status != "OPEN":
        raise IntakeError({"opening": f"{target.reference} is closed."})
    clash = session.scalar(
        select(Application).where(
            Application.opening_id == target.opening_id, Application.status != states.WITHDRAWN,
            (Application.candidate_id == application.candidate_id) | (Application.resume_sha256 == application.resume_sha256),
        )
    )
    if clash is not None:
        raise IntakeError({"opening": f"{target.reference} already has this candidate or this resume ({clash.reference})."})

    old = application.opening
    session.add(ReviewEdit(application_id=application.application_id, field="opening", action="MOVED",
                           old_value=old.reference if old else None, new_value=target.reference, actor=actor))
    application.opening = target
    application.school_id, application.department_id = target.school_id, target.department_id
    application.applied_designation = target.designation
    if application.status in _ASSESSED:
        session.add(HrDecision(application_id=application.application_id, action="RETURNED", actor=actor))
        states.transition(session, application, states.EXTRACTED, actor, note=f"moved to {target.reference}; to be assessed again")
    session.flush()


def move_all(session: Session, source: JobOpening, target: JobOpening, actor: str = "user:hr") -> tuple[int, list[str]]:
    """Move every application of `source` that can be moved. Returns (how many moved, why the rest stayed)."""
    moved, stayed = 0, []
    rows = session.scalars(
        select(Application).where(Application.opening_id == source.opening_id).order_by(Application.application_id)
    ).all()
    for application in rows:
        try:
            move_application(session, application, target, actor)
            moved += 1
        except IntakeError as exc:
            stayed.append(f"{application.reference}: {exc.errors['opening']}")
    return moved, stayed


@dataclass
class UploadOutcome:
    filename: str
    application: Application | None = None
    problem: str | None = None


def hr_upload(session: Session, opening: JobOpening, files: list[tuple[str, bytes]]) -> list[UploadOutcome]:
    """Resumes HR already holds, added to an opening one or many at a time.

    There is no form, so category, state and study leave stay empty until a
    person supplies them. A file that cannot be accepted is reported and the
    rest of the batch still goes in. The same file twice for one opening is
    reported as a duplicate, not stored twice.
    """
    outcomes: list[UploadOutcome] = []
    for filename, data in files:
        try:
            digest, _ = save_resume(filename, data)
        except RejectedUpload as exc:
            outcomes.append(UploadOutcome(filename, problem=str(exc)))
            continue
        existing = session.scalar(
            select(Application).where(
                Application.opening_id == opening.opening_id,
                Application.resume_sha256 == digest,
                Application.status != states.WITHDRAWN,
            )
        )
        if existing is not None:
            outcomes.append(UploadOutcome(filename, problem=f"already uploaded for this opening ({existing.reference})"))
            continue
        application = _new_application(session, opening, Candidate(), filename, data, "MANUAL_UPLOAD")
        outcomes.append(UploadOutcome(filename, application=application))
    return outcomes
