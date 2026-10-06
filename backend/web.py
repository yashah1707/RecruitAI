"""The web pages: HR's openings and uploads, and the public application form.

Server-rendered, no JavaScript needed. Templates escape everything they print.

There is no login yet (Phase 9). Until there is, the HR pages are open to
anyone who can reach the server, so it must stay bound to 127.0.0.1.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.result_cache import ResultCache
from backend import assessor_service, gate1, intake, jobs, states
from backend.db import get_session, get_session_factory
from backend.deps import get_cache, get_provider
from backend.models import (
    Application,
    CandidateExperience,
    CandidatePersonalDetails,
    CandidateQualification,
    Job,
    JobOpening,
    School,
)
from llm.interface import LLMProvider

logger = logging.getLogger("recruitai.web")

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _date(value) -> str:
    """The house date convention, DD-MM-YYYY (see app/excel_writer.py)."""
    if isinstance(value, datetime):
        # Stored in UTC; shown in the server's local time, which is what the
        # person reading the page means by "received at".
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone().strftime("%d-%m-%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    return ""


def _why_unreadable(note: str | None) -> str:
    """The audit note for a failed read, as a sentence for HR.

    "unreadable: scanned or image-only, review manually (pymupdf: ...)" keeps
    its reason and loses the prefix and the library detail.
    """
    text = (note or "").strip()
    if text.startswith("unreadable:"):
        text = text[len("unreadable:"):].split(" (")[0].strip()
        return text[:1].upper() + text[1:] if text else "The file could not be read"
    if text.startswith("reader_error:"):
        return "The language model is not configured correctly (" + text.split(":", 1)[1].strip() + ")"
    return text or "The file could not be read"


templates.env.filters["ddmmyyyy"] = _date
templates.env.filters["why_unreadable"] = _why_unreadable
templates.env.globals.update(DESIGNATIONS=intake.DESIGNATIONS, DISCIPLINE_GROUPS=intake.DISCIPLINE_GROUPS)

STATE_LABELS = {
    states.RECEIVED: "Received, waiting to be read",
    states.PARSING: "Being read",
    states.EXTRACTED: "Read",
    states.PENDING_REVIEW: "Read, needs a person to check fields",
    states.FAILED: "Could not be read",
    states.NEEDS_JOB_MATCH: "No matching opening",
    states.WITHDRAWN: "Withdrawn",
    states.SHORTLISTED: "Assessed: meets the minimum qualifications for the post applied for",
    states.RE_CATEGORISED: "Assessed: meets the minimum qualifications for a lower post",
    states.NOT_ELIGIBLE: "Assessed: does not meet the minimum qualifications",
    states.MANUAL_REVIEW: "Assessed: needs a person to decide",
}
templates.env.globals["STATE_LABELS"] = STATE_LABELS


def _parse_date(text: str | None, field: str, errors: dict[str, str]) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        errors[field] = "Enter a valid date."
        return None


def _opening_or_404(session: Session, opening_id: int) -> JobOpening:
    opening = session.get(JobOpening, opening_id)
    if opening is None:
        raise HTTPException(status_code=404, detail="opening not found")
    return opening


def _applications(session: Session, opening_id: int) -> list[dict]:
    rows = session.scalars(
        select(Application).where(Application.opening_id == opening_id).order_by(Application.application_id)
    ).all()
    out = []
    for a in rows:
        personal = session.get(CandidatePersonalDetails, a.candidate_id)
        failed_note = a.transitions[-1].note if a.status == states.FAILED and a.transitions else None
        out.append({
            "a": a,
            "name": a.applicant_name or (personal.full_name if personal else None),
            "failed_note": failed_note,
        })
    return out


# --- HR ----------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def hr_home(request: Request, msg: str = "", session: Session = Depends(get_session)):
    openings = session.scalars(select(JobOpening).order_by(JobOpening.opening_id.desc())).all()
    counts: dict[int, dict[str, int]] = {}
    for opening_id, status in session.execute(select(Application.opening_id, Application.status)):
        bucket = counts.setdefault(opening_id, {})
        bucket[status] = bucket.get(status, 0) + 1
        bucket["total"] = bucket.get("total", 0) + 1

    attention = session.scalars(
        select(Application)
        .where((Application.status == states.FAILED) | (Application.possible_duplicate_candidate_id.is_not(None)))
        .order_by(Application.application_id.desc())
    ).all()
    stuck = session.scalars(select(Job).where(Job.status == jobs.FAILED).order_by(Job.job_id.desc())).all()
    return templates.TemplateResponse(request, "hr_home.html", {
        "openings": openings, "counts": counts, "attention": attention, "stuck": stuck, "msg": msg,
        "queue": jobs.queue_summary(session), "due": jobs.due_count(session),
        "accepting": {o.opening_id: intake.is_accepting(o) for o in openings},
        "reading": _run_lock.locked(), "last_run": last_run,
    })


@router.get("/hr/openings/new", response_class=HTMLResponse)
def opening_form(request: Request, session: Session = Depends(get_session)):
    schools = session.scalars(select(School).where(School.is_hiring_unit).order_by(School.name)).all()
    return templates.TemplateResponse(request, "opening_new.html", {
        "schools": schools, "errors": {}, "v": {},
        "suggested": {s.school_id: intake.suggested_discipline_group(s) for s in schools},
    })


@router.post("/hr/openings", response_class=HTMLResponse)
def opening_create(
    request: Request,
    school_id: str = Form(""),
    designation: str = Form(""),
    discipline_group: str = Form(""),
    department_name: str = Form(""),
    title: str = Form(""),
    closing_date: str = Form(""),
    advertisement_ref: str = Form(""),
    advertisement_date: str = Form(""),
    session: Session = Depends(get_session),
):
    errors: dict[str, str] = {}
    closing = _parse_date(closing_date, "closing_date", errors)
    advertised = _parse_date(advertisement_date, "advertisement_date", errors)
    opening = None
    if not errors:
        try:
            opening = intake.create_opening(
                session, school_id=school_id, designation=designation, discipline_group=discipline_group,
                department_name=department_name, title=title, closing_date=closing,
                advertisement_ref=advertisement_ref, advertisement_date=advertised,
            )
        except intake.IntakeError as exc:
            errors = exc.errors
    if errors:
        schools = session.scalars(select(School).where(School.is_hiring_unit).order_by(School.name)).all()
        return templates.TemplateResponse(request, "opening_new.html", {
            "schools": schools, "errors": errors,
            "suggested": {s.school_id: intake.suggested_discipline_group(s) for s in schools},
            "v": {"school_id": school_id, "designation": designation, "discipline_group": discipline_group,
                  "department_name": department_name, "title": title, "closing_date": closing_date,
                  "advertisement_ref": advertisement_ref, "advertisement_date": advertisement_date},
        }, status_code=422)
    return RedirectResponse(f"/hr/openings/{opening.opening_id}?msg=" + quote("Opening created."), status_code=303)


@router.get("/hr/openings/{opening_id}", response_class=HTMLResponse)
def opening_detail(request: Request, opening_id: int, msg: str = "", session: Session = Depends(get_session)):
    opening = _opening_or_404(session, opening_id)
    return templates.TemplateResponse(request, "opening_detail.html", {
        "o": opening, "rows": _applications(session, opening_id), "msg": msg, "outcomes": None,
        "accepting": intake.is_accepting(opening), "due": jobs.due_count(session),
        "reading": _run_lock.locked(),
    })


_OUTCOME_WORDS = {"SHORTLISTED": "meet the post applied for", "RE_CATEGORISED": "meet a lower post",
                  "NOT_ELIGIBLE": "do not meet the minimum qualifications", "MANUAL_REVIEW": "need a person to decide"}


@router.post("/hr/openings/{opening_id}/assess")
def opening_assess(opening_id: int, session: Session = Depends(get_session)):
    """Assess every read application of this opening against the rules. No model is called."""
    _opening_or_404(session, opening_id)
    counts = assessor_service.assess_opening(session, opening_id)
    text = ("Assessed " + str(sum(counts.values())) + " application(s): "
            + "; ".join(f"{n} {_OUTCOME_WORDS[o]}" for o, n in counts.items()) + "."
            if counts else "No read application was waiting to be assessed.")
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/openings/{opening_id}/close")
def opening_close(opening_id: int, session: Session = Depends(get_session)):
    intake.close_opening(session, _opening_or_404(session, opening_id))
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote("Opening closed."), status_code=303)


@router.post("/hr/openings/{opening_id}/upload", response_class=HTMLResponse)
async def opening_upload(
    request: Request, opening_id: int, resumes: list[UploadFile] = File(default=[]),
    session: Session = Depends(get_session),
):
    opening = _opening_or_404(session, opening_id)
    files = [(f.filename or "", await f.read()) for f in resumes if f.filename]
    outcomes = intake.hr_upload(session, opening, files) if files else []
    session.flush()
    added = len([o for o in outcomes if o.application is not None])
    return templates.TemplateResponse(request, "opening_detail.html", {
        "o": opening, "rows": _applications(session, opening_id), "outcomes": outcomes,
        "msg": f"{added} of {len(outcomes)} file(s) added." if outcomes else "No files were chosen.",
        "accepting": intake.is_accepting(opening), "due": jobs.due_count(session),
    })


# One reading run at a time, and what the last one did. Reading can take a
# minute per resume, so it happens after the response is sent: the page
# returns at once and shows progress when refreshed.
_run_lock = threading.Lock()
last_run: dict[str, str] = {}


def _read_in_background(factory, provider: LLMProvider, cache: ResultCache | None, limit: int) -> None:
    try:
        with factory() as session:
            jobs.release_stale_jobs(session)
            session.commit()
            ran = jobs.run_due_jobs(session, provider, cache, limit=limit)
            session.commit()
            last_run["summary"] = jobs.summarise(ran)
    except Exception:  # the page must be able to say that it failed
        logger.exception("reading_run_failed")
        last_run["summary"] = "The reading run stopped because of an error. See the server log."
    finally:
        last_run["finished"] = datetime.now(timezone.utc).astimezone().strftime("%d-%m-%Y %H:%M")
        _run_lock.release()


@router.post("/hr/queue/run")
def queue_run(
    background: BackgroundTasks,
    back: str = Form("/"),
    limit: int = Form(5),
    session: Session = Depends(get_session),
    factory=Depends(get_session_factory),
    provider: LLMProvider = Depends(get_provider),
    cache: ResultCache | None = Depends(get_cache),
):
    """Start reading up to `limit` waiting applications. Each one is a model request."""
    target = back if back.startswith("/") and not back.startswith("//") else "/"
    limit = max(1, min(limit, 50))
    waiting = jobs.due_count(session)
    if waiting == 0:
        text = "Nothing was waiting to be read."
    elif not _run_lock.acquire(blocking=False):
        text = "Reading is already in progress. Refresh this page to see how far it has got."
    else:
        background.add_task(_read_in_background, factory, provider, cache, limit)
        text = (f"Reading {min(waiting, limit)} application(s) now. This can take up to a minute each; "
                "this page refreshes while it runs.")
    return RedirectResponse(f"{target}{'&' if '?' in target else '?'}msg=" + quote(text), status_code=303)


@router.post("/hr/jobs/{job_id}/requeue")
def job_requeue(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    text = ("Put back in the reading queue. Press Process queue to read it." if jobs.requeue(session, job)
            else "That application is no longer waiting to be read.")
    return RedirectResponse("/?msg=" + quote(text), status_code=303)


# --- Gate 1: extraction review ----------------------------------------------

_RESUME_TYPES = {".pdf": "application/pdf",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
_FIELD_LABELS = {name: spec.label for name, spec in gate1.FIELDS.items()} | {"candidate": "Possible duplicate"}
templates.env.globals["ACTION_LABELS"] = {
    "CONFIRMED": "Confirmed as read", "CORRECTED": "Corrected", "ENTERED": "Entered", "LEFT_EMPTY": "Left empty",
    "SAME_PERSON": "Same person: records joined", "DIFFERENT_PERSON": "Different person: kept apart",
}


def _application_or_404(session: Session, application_id: int, lock: bool = False) -> Application:
    application = session.get(Application, application_id, with_for_update=lock)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return application


def _review_page(request: Request, session: Session, a: Application, *, msg: str = "", errors: dict | None = None,
                 answers: dict | None = None, left_empty: set | None = None, status_code: int = 200):
    flags = gate1.flagged_fields(session, a) if a.status == states.PENDING_REVIEW else []
    if answers is not None:  # what the person typed survives a refused save
        for f in flags:
            f.value = answers.get(f.name, "")

    def rows(table, key):
        return session.scalars(select(table).where(table.application_id == a.application_id).order_by(key)).all()

    return templates.TemplateResponse(request, "review.html", {
        "a": a, "o": a.opening, "e": a.extracted, "msg": msg,
        "personal": session.get(CandidatePersonalDetails, a.candidate_id),
        "flags": flags, "errors": errors or {}, "left_empty": left_empty or set(),
        "duplicate": gate1.duplicate_of(session, a), "edits": gate1.history(session, a),
        "qualifications": rows(CandidateQualification, CandidateQualification.qualification_id),
        "experience": rows(CandidateExperience, CandidateExperience.experience_id),
        "failed_note": a.transitions[-1].note if a.status == states.FAILED and a.transitions else None,
        "can_withdraw": states.WITHDRAWN in states.ALLOWED[a.status], "field_labels": _FIELD_LABELS,
        "evaluation": assessor_service.latest_evaluation(session, a.application_id),
    }, status_code=status_code)


@router.get("/hr/applications/{application_id}", response_class=HTMLResponse)
def review_form(request: Request, application_id: int, msg: str = "", session: Session = Depends(get_session)):
    return _review_page(request, session, _application_or_404(session, application_id), msg=msg)


@router.post("/hr/applications/{application_id}/review", response_class=HTMLResponse)
async def review_save(request: Request, application_id: int, session: Session = Depends(get_session)):
    a = _application_or_404(session, application_id, lock=True)
    form = await request.form()
    answers = {k[len("f_"):]: str(v) for k, v in form.items() if k.startswith("f_")}
    left_empty = {str(v) for v in form.getlist("empty")}
    try:
        settled = gate1.save_review(session, a, answers, left_empty)
    except gate1.ReviewError as exc:
        session.rollback()
        a = _application_or_404(session, application_id)
        return _review_page(request, session, a, errors=exc.errors, answers=answers, left_empty=left_empty,
                            msg=exc.errors.get("", ""), status_code=422)
    text = f"Saved {len(settled)} field(s). " + (
        "The application is ready for assessment." if a.status == states.EXTRACTED
        else "The possible duplicate still needs a decision."
    )
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text), status_code=303)


def _review_action(session: Session, application_id: int, action, done: str):
    """Run one Gate 1 action and go back to the review page with what happened."""
    a = _application_or_404(session, application_id, lock=True)
    try:
        action(a)
        text = done
    except gate1.ReviewError as exc:
        session.rollback()
        text = exc.errors.get("", "That could not be saved.")
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/applications/{application_id}/duplicate")
def review_duplicate(application_id: int, same: str = Form(""), session: Session = Depends(get_session)):
    if same not in ("yes", "no"):
        raise HTTPException(status_code=422, detail="answer yes or no")
    return _review_action(
        session, application_id, lambda a: gate1.resolve_duplicate(session, a, same_person=same == "yes"),
        "Recorded as the same person; the records are joined." if same == "yes" else "Recorded as a different person.",
    )


@router.post("/hr/applications/{application_id}/withdraw")
def review_withdraw(application_id: int, session: Session = Depends(get_session)):
    return _review_action(session, application_id, lambda a: gate1.withdraw(session, a), "Application withdrawn.")


@router.get("/hr/applications/{application_id}/resume")
def review_resume(application_id: int, session: Session = Depends(get_session)):
    """The stored resume, so the person checking can read the original beside the form."""
    a = _application_or_404(session, application_id)
    path = Path(a.resume_path)
    if not path.is_file() or path.suffix.lower() not in _RESUME_TYPES:
        raise HTTPException(status_code=404, detail="the resume file is not available")
    # Named by its reference, not by the candidate or the original filename.
    return FileResponse(path, media_type=_RESUME_TYPES[path.suffix.lower()],
                        filename=f"{a.reference}{path.suffix.lower()}", content_disposition_type="inline")


# --- applicants --------------------------------------------------------------


@router.get("/apply", response_class=HTMLResponse)
def apply_list(request: Request, session: Session = Depends(get_session)):
    openings = [o for o in session.scalars(select(JobOpening).order_by(JobOpening.opening_id.desc()))
                if intake.is_accepting(o)]
    return templates.TemplateResponse(request, "apply_list.html", {"openings": openings})


@router.get("/apply/{opening_id}", response_class=HTMLResponse)
def apply_form(request: Request, opening_id: int, session: Session = Depends(get_session)):
    opening = _opening_or_404(session, opening_id)
    return templates.TemplateResponse(request, "apply_form.html", {
        "o": opening, "f": intake.ApplicantForm(), "accepting": intake.is_accepting(opening),
        "states": intake.STATES, "categories": intake.CATEGORIES,
    })


@router.post("/apply/{opening_id}", response_class=HTMLResponse)
async def apply_submit(
    request: Request,
    opening_id: int,
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    state: str = Form(""),
    category: str = Form(""),
    differently_abled: str = Form(""),
    study_leave_taken: str = Form(""),
    declaration: str = Form(""),
    resume: UploadFile | None = File(default=None),
    session: Session = Depends(get_session),
):
    opening = _opening_or_404(session, opening_id)
    form = intake.ApplicantForm(
        full_name=full_name, email=email, phone=phone, state=state, category=category,
        differently_abled=differently_abled, study_leave_taken=study_leave_taken, declaration=bool(declaration),
    )
    filename = (resume.filename if resume else "") or ""
    data = await resume.read() if resume and filename else b""
    try:
        application = intake.submit_application(session, opening, form, filename, data)
    except intake.IntakeError:
        return templates.TemplateResponse(request, "apply_form.html", {
            "o": opening, "f": form, "accepting": intake.is_accepting(opening),
            "states": intake.STATES, "categories": intake.CATEGORIES,
        }, status_code=422)
    return templates.TemplateResponse(request, "apply_done.html", {"o": opening, "a": application}, status_code=201)
