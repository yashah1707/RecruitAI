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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.result_cache import ResultCache
from backend import assessor_service, emails, gate1, gate2, inbox, intake, jobs, reporting, settings, states
from backend.db import get_session, get_session_factory
from backend.deps import get_cache, get_provider
from backend.engine.facts import counting_date
from backend.reader_service import missing_form_answers
from backend.models import (
    Application,
    CandidateAchievement,
    CandidateEvent,
    CandidateExperience,
    CandidateGuidance,
    CandidateMembership,
    CandidatePublication,
    CandidateResearchProfile,
    CandidateSkill,
    CandidateSubjectTaught,
    EmailDraft,
    InboxMessage,
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
    if isinstance(value, str):  # a date kept as text in stored working, e.g. "2026-10-08"
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
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
    states.ASSESSED: "Being assessed",
    states.HR_APPROVED: "Decided by HR",
    states.CONTACTED: "Decided by HR; candidate informed",
    states.INTERVIEW_SCHEDULED: "Interview scheduled",
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
        evaluation = assessor_service.latest_evaluation(session, a.application_id)
        out.append({
            "a": a,
            "name": a.applicant_name or (personal.full_name if personal else None),
            "failed_note": failed_note,
            "v": evaluation if a.status in gate2.AWAITING_HR or a.status in gate2.DECIDED else None,
            "decided": gate2.final_decision(session, a),
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
        "inbox_waiting": len(inbox.waiting(session)), "inbox_configured": inbox.inbox_is_configured(),
        "accepting": {o.opening_id: intake.is_accepting(o) for o in openings},
        "reading": _run_lock.locked(), "last_run": last_run,
        "letters": {o: emails.waiting_in_words(c) for o, c in emails.waiting_for_approval(session).items()},
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
    eligibility_date: str = Form(""),
    session: Session = Depends(get_session),
):
    errors: dict[str, str] = {}
    closing = _parse_date(closing_date, "closing_date", errors)
    advertised = _parse_date(advertisement_date, "advertisement_date", errors)
    counted_on = _parse_date(eligibility_date, "eligibility_date", errors)
    opening = None
    if not errors:
        try:
            opening = intake.create_opening(
                session, school_id=school_id, designation=designation, discipline_group=discipline_group,
                department_name=department_name, title=title, closing_date=closing,
                advertisement_ref=advertisement_ref, advertisement_date=advertised, eligibility_date=counted_on,
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
                  "advertisement_ref": advertisement_ref, "advertisement_date": advertisement_date,
                  "eligibility_date": eligibility_date},
        }, status_code=422)
    return RedirectResponse(f"/hr/openings/{opening.opening_id}?msg=" + quote("Opening created."), status_code=303)


@router.get("/hr/openings/{opening_id}", response_class=HTMLResponse)
def opening_detail(request: Request, opening_id: int, msg: str = "", session: Session = Depends(get_session)):
    opening = _opening_or_404(session, opening_id)
    return templates.TemplateResponse(request, "opening_detail.html", {
        "o": opening, "rows": _applications(session, opening_id), "msg": msg, "outcomes": None,
        "accepting": intake.is_accepting(opening), "due": jobs.due_count(session),
        "reading": _run_lock.locked(), "counted_on": counting_date(opening, date.today()),
        "letters": emails.waiting_in_words(emails.waiting_for_approval(session).get(opening_id)),
        "out_of_date": len(gate2.out_of_date(session, opening)),
        "other_openings": [o for o in session.scalars(select(JobOpening).order_by(JobOpening.opening_id.desc()))
                           if o.status == "OPEN" and o.opening_id != opening_id],
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


@router.post("/hr/openings/{opening_id}/reassess")
def opening_reassess(opening_id: int, session: Session = Depends(get_session)):
    """Assess again the applications whose assessment was counted on a date other than the opening's own."""
    counts = gate2.reassess_out_of_date(session, _opening_or_404(session, opening_id))
    text = ("Assessed again " + str(sum(counts.values())) + " application(s): "
            + "; ".join(f"{n} {_OUTCOME_WORDS[o]}" for o, n in counts.items()) + "."
            if counts else "No assessment was out of date.")
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
        "counted_on": counting_date(opening, date.today()), "out_of_date": 0,
        "letters": emails.waiting_in_words(emails.waiting_for_approval(session).get(opening_id)),
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
_FIELD_LABELS = {name: spec.label for name, spec in gate1.FIELDS.items()} | {"candidate": "Possible duplicate", "opening": "Opening", "resume": "Resume"}
templates.env.globals["ACTION_LABELS"] = {
    "CONFIRMED": "Confirmed as read", "CORRECTED": "Corrected", "ENTERED": "Entered", "LEFT_EMPTY": "Left empty",
    "SAME_PERSON": "Same person: records joined", "DIFFERENT_PERSON": "Different person: kept apart",
    "MOVED": "Moved to another opening", "READ_AGAIN": "Sent to be read again",
}


def _application_or_404(session: Session, application_id: int, lock: bool = False) -> Application:
    application = session.get(Application, application_id, with_for_update=lock)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return application


def _teaching_from_posts(session: Session, a: Application):
    """Years in teaching posts, counted from their dates: shown beside what the resume states as a total."""
    if a.extracted is None:
        return None
    from backend.engine.experience import service_years
    from backend.engine.facts import build_facts

    facts = build_facts(session, a)
    facts.stated_teaching_years = None  # the dated posts alone, not the stated total
    if not any(p.kind == "TEACHING" and p.start for p in facts.posts):
        return None
    return {"years": service_years(facts, ("TEACHING",)), "to": facts.as_of_text if facts.cut_off else "today"}


def _review_page(request: Request, session: Session, a: Application, *, msg: str = "", errors: dict | None = None,
                 answers: dict | None = None, left_empty: set | None = None, status_code: int = 200):
    flags = gate1.flagged_fields(session, a) if a.status == states.PENDING_REVIEW else []
    if answers is not None:  # what the person typed survives a refused save
        for f in flags:
            f.value = answers.get(f.name, "")

    def rows(table, key):
        return session.scalars(select(table).where(table.application_id == a.application_id).order_by(key)).all()

    personal = session.get(CandidatePersonalDetails, a.candidate_id)
    return templates.TemplateResponse(request, "review.html", {
        "a": a, "o": a.opening, "e": a.extracted, "msg": msg,
        "personal": personal,
        "flags": flags, "errors": errors or {}, "left_empty": left_empty or set(),
        "duplicate": gate1.duplicate_of(session, a), "edits": gate1.history(session, a),
        "qualifications": rows(CandidateQualification, CandidateQualification.qualification_id),
        "experience": rows(CandidateExperience, CandidateExperience.experience_id),
        "failed_note": a.transitions[-1].note if a.status == states.FAILED and a.transitions else None,
        "can_withdraw": states.WITHDRAWN in states.ALLOWED[a.status], "field_labels": _FIELD_LABELS,
        "correctable": a.status in gate1.BEFORE_HR_DECISION and a.extracted is not None, "POST_KINDS": gate1.POST_KINDS,
        "evaluation": assessor_service.latest_evaluation(session, a.application_id),
        "sent_by": inbox.sender_of(session, a.application_id, personal.full_name if personal else None),
        "correspondence": inbox.correspondence(session, a.application_id),
        "teaching_from_posts": _teaching_from_posts(session, a),
        "plain_finding": reporting.explain(a, assessor_service.latest_evaluation(session, a.application_id)),
        "acknowledgement": emails.active_draft(session, a.application_id, emails.ACKNOWLEDGEMENT),
        "email": emails.active_draft(session, a.application_id), "email_history": emails.history(session, a.application_id),
        "mail_configured": emails.mail_is_configured(), "PLACEHOLDER": emails.PLACEHOLDER,
        "redirect_to": settings.EMAIL_REDIRECT_TO,
        "missing_answers": missing_form_answers(session, a) if a.status == states.EXTRACTED else [],
        "other_openings": [o for o in session.scalars(select(JobOpening).order_by(JobOpening.opening_id.desc()))
                           if o.status == "OPEN" and o.opening_id != a.opening_id] if a.status in intake.MOVABLE else [],
        "awaiting_hr": a.status in gate2.AWAITING_HR, "decisions": gate2.decisions(session, a.application_id),
        "decided": gate2.final_decision(session, a), "returnable": gate1.FIELDS,
        "would_settle": gate2.fields_that_would_settle(assessor_service.latest_evaluation(session, a.application_id)),
        "publications": rows(CandidatePublication, CandidatePublication.publication_id),
        "events": rows(CandidateEvent, CandidateEvent.id), "achievements": rows(CandidateAchievement, CandidateAchievement.id),
        "guidance": rows(CandidateGuidance, CandidateGuidance.id), "subjects": rows(CandidateSubjectTaught, CandidateSubjectTaught.id),
        "skills": rows(CandidateSkill, CandidateSkill.id), "memberships": rows(CandidateMembership, CandidateMembership.id),
        "research": session.get(CandidateResearchProfile, a.candidate_id),
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


@router.post("/hr/applications/{application_id}/reopen")
async def review_reopen(request: Request, application_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    fields = [str(v) for v in form.getlist("fields")]
    return _review_action(session, application_id, lambda a: gate1.reopen_fields(session, a, fields),
                          "Opened for entry. Fill in the fields below and save.")


@router.post("/hr/applications/{application_id}/read-again")
def review_read_again(application_id: int, session: Session = Depends(get_session)):
    """Queue the resume to be read afresh. No model is called here; it waits for a person to run the queue."""
    return _review_action(session, application_id, lambda a: gate1.read_again(session, a),
                          "Put back in the reading queue. Press the Read button on the opening's page to read it (one request to the language model).")


@router.post("/hr/applications/{application_id}/posts")
async def review_posts(request: Request, application_id: int, session: Session = Depends(get_session)):
    form = {k: str(v) for k, v in (await request.form()).items()}
    a = _application_or_404(session, application_id, lock=True)
    assessed = a.status in gate2.AWAITING_HR
    try:
        n = gate1.save_posts(session, a, form)
        text = f"Saved {n} post(s)." + (" The earlier assessment is set aside: press Assess on the opening's page to assess it again." if assessed else "")
    except gate1.ReviewError as exc:
        session.rollback()
        text = "Not saved. " + exc.errors.get("", "")
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text) + "#posts", status_code=303)


@router.post("/hr/applications/{application_id}/move")
def application_move(application_id: int, opening_id: int = Form(0), session: Session = Depends(get_session)):
    a = _application_or_404(session, application_id, lock=True)
    target = session.get(JobOpening, opening_id)
    try:
        if target is None:
            raise intake.IntakeError({"opening": "Choose an opening to move it to."})
        intake.move_application(session, a, target)
        text = f"Moved to {target.reference}. It will be assessed under that opening's rules."
    except intake.IntakeError as exc:
        session.rollback()
        text = exc.errors.get("opening", "That could not be done.")
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/openings/{opening_id}/move-all")
def opening_move_all(opening_id: int, target_id: int = Form(0), session: Session = Depends(get_session)):
    source, target = _opening_or_404(session, opening_id), session.get(JobOpening, target_id)
    if target is None or target.opening_id == source.opening_id:
        text = "Choose another opening to move the applications to."
    else:
        moved, stayed = intake.move_all(session, source, target)
        text = f"Moved {moved} application(s) to {target.reference}." + (" Not moved: " + "; ".join(stayed) if stayed else "")
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote(text), status_code=303)


# --- Gate 2: the HR decision -------------------------------------------------


def _decision_action(session: Session, application_id: int, action, done: str):
    a = _application_or_404(session, application_id, lock=True)
    try:
        action(a)
        text = done
    except gate2.DecisionError as exc:
        session.rollback()
        text = " ".join(exc.errors.values())
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/applications/{application_id}/approve")
def decision_approve(application_id: int, session: Session = Depends(get_session)):
    return _decision_action(session, application_id, lambda a: gate2.approve(session, a), "Approved as assessed.")


@router.post("/hr/applications/{application_id}/override")
def decision_override(application_id: int, outcome: str = Form(""), designation: str = Form(""),
                      justification: str = Form(""), session: Session = Depends(get_session)):
    return _decision_action(
        session, application_id, lambda a: gate2.override(session, a, outcome, designation or None, justification),
        "Your decision is recorded with its justification.")


@router.post("/hr/applications/{application_id}/return")
async def decision_return(request: Request, application_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    fields = [str(v) for v in form.getlist("fields")]
    if form.get("how") == "reassess":
        return _decision_action(session, application_id, lambda a: gate2.return_for_reassessment(session, a),
                                "Returned. Press Assess on the opening's page to assess it again.")
    return _decision_action(session, application_id, lambda a: gate2.return_for_correction(session, a, fields),
                            "Returned for correction. Check the fields below, then assess it again.")


@router.get("/hr/openings/{opening_id}/export.xlsx")
def opening_export(opening_id: int, session: Session = Depends(get_session)):
    from backend.export import opening_workbook

    opening = _opening_or_404(session, opening_id)
    data = opening_workbook(session, opening, STATE_LABELS)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{opening.reference}.xlsx"'})


# --- the inbox channel -------------------------------------------------------


@router.get("/hr/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, msg: str = "", session: Session = Depends(get_session)):
    recent = session.scalars(select(InboxMessage).order_by(InboxMessage.inbox_id.desc()).limit(50)).all()
    openings = [o for o in session.scalars(select(JobOpening).order_by(JobOpening.opening_id.desc())) if intake.is_accepting(o)]
    rows = inbox.waiting(session)
    # For each held application: the openings its own words point to, and then the rest.
    choices = {}
    for m in rows:
        suggested = inbox.suggest_openings(m, openings) if m.status == inbox.NEEDS_JOB_MATCH else []
        first = [o for o, _ in suggested]
        choices[m.inbox_id] = {"reason": suggested[0][1] if suggested else None, "suggested": suggested[0][0] if suggested else None,
                               "already": inbox.already_filed(session, m),
                               "openings": first + [o for o in openings if o not in first]}
    return templates.TemplateResponse(request, "inbox.html", {
        "rows": rows, "recent": recent, "openings": openings, "msg": msg, "choices": choices,
        "configured": inbox.inbox_is_configured(), "mailbox": settings.IMAP_USER,
    })


@router.post("/hr/inbox/check")
def inbox_check(session: Session = Depends(get_session)):
    """Look at new mail. Started by a person; reads the mailbox without changing it."""
    try:
        text = inbox.summarise(inbox.check_inbox(session))
    except RuntimeError as exc:
        text = str(exc)
    except Exception as exc:  # a wrong password, the server unreachable: say so, without the server's own words
        logger.warning("inbox_check_failed kind=%s", type(exc).__name__)
        session.rollback()
        text = f"The mailbox could not be read ({type(exc).__name__}). Check the IMAP settings and that IMAP is enabled for the account."
    return RedirectResponse("/hr/inbox?msg=" + quote(text), status_code=303)


def _inbox_row(session: Session, inbox_id: int) -> InboxMessage:
    row = session.get(InboxMessage, inbox_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    return row


@router.post("/hr/inbox/{inbox_id}/assign")
def inbox_assign(inbox_id: int, opening_id: str = Form(""), session: Session = Depends(get_session)):
    # Taken as text: "Choose an opening…" posts an empty value, which is an answer to explain, not an error page.
    row = _inbox_row(session, inbox_id)
    opening = session.get(JobOpening, int(opening_id)) if opening_id.isdigit() else None
    try:
        if opening is None:
            raise ValueError("Choose an opening.")
        inbox.assign_opening(session, row, opening)
        text = row.note or "Done."
    except ValueError as exc:
        text = str(exc)
    return RedirectResponse("/hr/inbox?msg=" + quote(text), status_code=303)


@router.get("/hr/inbox/{inbox_id}/attachment")
def inbox_attachment(inbox_id: int, session: Session = Depends(get_session)):
    """The resume attached to a held message, so the person choosing its opening can read it."""
    row = session.get(InboxMessage, inbox_id)
    path = Path(row.attachment_path) if row is not None and row.attachment_path else None
    if path is None or not path.is_file() or path.suffix.lower() not in _RESUME_TYPES:
        raise HTTPException(status_code=404, detail="no attachment is held for this message")
    return FileResponse(path, media_type=_RESUME_TYPES[path.suffix.lower()],
                        filename=f"inbox-{inbox_id}{path.suffix.lower()}", content_disposition_type="inline")


@router.post("/hr/inbox/{inbox_id}/set-aside")
def inbox_set_aside(inbox_id: int, session: Session = Depends(get_session)):
    row = _inbox_row(session, inbox_id)
    try:
        inbox.set_aside(session, row)
        text = "Set aside."
    except ValueError as exc:
        text = str(exc)
    return RedirectResponse("/hr/inbox?msg=" + quote(text), status_code=303)


@router.get("/hr/openings/{opening_id}/edit", response_class=HTMLResponse)
def opening_edit_form(request: Request, opening_id: int, session: Session = Depends(get_session)):
    o = _opening_or_404(session, opening_id)
    return _opening_edit_page(request, session, o, {}, {
        "discipline_group": o.discipline_group, "department_name": o.department.name if o.department else "",
        "title": o.title or "", "closing_date": o.closing_date.isoformat() if o.closing_date else "",
        "eligibility_date": o.eligibility_date.isoformat() if o.eligibility_date else "",
    })


def _opening_edit_page(request: Request, session: Session, o: JobOpening, errors: dict, values: dict, status_code: int = 200):
    statuses = list(session.scalars(select(Application.status).where(Application.opening_id == o.opening_id)))
    return templates.TemplateResponse(request, "opening_edit.html", {
        "o": o, "errors": errors, "v": values,
        "assessed": len([s for s in statuses if s in gate2.AWAITING_HR]), "decided": len([s for s in statuses if s in gate2.DECIDED]),
    }, status_code=status_code)


@router.post("/hr/openings/{opening_id}/edit", response_class=HTMLResponse)
def opening_edit(
    request: Request, opening_id: int, discipline_group: str = Form(""), department_name: str = Form(""),
    title: str = Form(""), closing_date: str = Form(""), eligibility_date: str = Form(""),
    session: Session = Depends(get_session),
):
    o = _opening_or_404(session, opening_id)
    errors: dict[str, str] = {}
    closing = _parse_date(closing_date, "closing_date", errors)
    counted_on = _parse_date(eligibility_date, "eligibility_date", errors)
    rules_before = o.discipline_group
    counts = None
    if not errors:
        try:
            counts = intake.update_opening(session, o, discipline_group=discipline_group, department_name=department_name,
                                           title=title, closing_date=closing, eligibility_date=counted_on)
        except intake.IntakeError as exc:
            session.rollback()
            o, errors = _opening_or_404(session, opening_id), exc.errors
    if errors:
        return _opening_edit_page(request, session, o, errors, {
            "discipline_group": discipline_group, "department_name": department_name, "title": title, "closing_date": closing_date,
            "eligibility_date": eligibility_date,
        }, status_code=422)
    text = "Opening updated."
    if counts["reassess"]:
        what = "rule set" if discipline_group != rules_before else "date eligibility is counted on"
        text += f" The {what} changed, so {counts['reassess']} assessed application(s) were set aside: press Assess to assess them again."
    if counts["decided"]:
        text += f" {counts['decided']} already decided by HR keep their decision; look at them again yourself."
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/openings/{opening_id}/reopen")
def opening_reopen(opening_id: int, session: Session = Depends(get_session)):
    intake.reopen_opening(session, _opening_or_404(session, opening_id))
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote("Opening reopened."), status_code=303)


def _email_rows(session: Session, opening_id: int) -> list[dict]:
    rows = []
    for a in session.scalars(select(Application).where(Application.opening_id == opening_id).order_by(Application.application_id)):
        personal = session.get(CandidatePersonalDetails, a.candidate_id)
        for kind in (emails.ACKNOWLEDGEMENT, emails.DECISION):
            draft = emails.active_draft(session, a.application_id, kind)
            if draft is None:
                continue
            problem = emails.problem_with(draft)
            by_system = draft.approved_by == emails.SYSTEM
            if draft.status == "SENT":
                state = ("Sent automatically " if by_system else "Sent ") + _date(draft.sent_at)
            elif draft.status == "APPROVED":
                state = ("Not yet sent to the candidate" if by_system else "Approved, not yet sent to the candidate") + (
                    "; a test copy went out " + _date(draft.sent_at) if draft.redirected and draft.sent_at else "")
            else:
                state = "Draft" + ("; the address was read from the resume, so check it" if kind == emails.ACKNOWLEDGEMENT else "")
            rows.append({"a": a, "d": draft, "name": a.applicant_name or (personal.full_name if personal else None),
                         "kind": "Acknowledgement" if kind == emails.ACKNOWLEDGEMENT else "Decision",
                         "problem": problem, "state": state, "can_send": draft.status in ("DRAFT", "APPROVED") and not problem})
    return rows


@router.get("/hr/openings/{opening_id}/emails", response_class=HTMLResponse)
def opening_emails(request: Request, opening_id: int, msg: str = "", session: Session = Depends(get_session)):
    o = _opening_or_404(session, opening_id)
    rows = _email_rows(session, opening_id)
    return templates.TemplateResponse(request, "opening_emails.html", {
        "o": o, "rows": rows, "msg": msg, "sendable": len([r for r in rows if r["can_send"]]),
        "mail_configured": emails.mail_is_configured(), "redirect_to": settings.EMAIL_REDIRECT_TO,
    })


@router.post("/hr/openings/{opening_id}/emails")
async def opening_emails_send(request: Request, opening_id: int, session: Session = Depends(get_session)):
    """Approve, and send, the letters a person ticked on the opening's emails page."""
    _opening_or_404(session, opening_id)
    form = await request.form()
    ticked = {int(v) for v in form.getlist("draft_id") if str(v).isdigit()}
    # Only this opening's own live drafts: an id from anywhere else is ignored.
    drafts = [r["d"] for r in _email_rows(session, opening_id) if r["d"].draft_id in ticked]
    text = emails.summarise_sending(emails.approve_and_send(session, drafts))
    return RedirectResponse(f"/hr/openings/{opening_id}/emails?msg=" + quote(text), status_code=303)


# --- Gate 3: candidate emails ------------------------------------------------


def _email_action(session: Session, application_id: int, action, done: str):
    a = _application_or_404(session, application_id, lock=True)
    draft = emails.active_draft(session, a.application_id)
    try:
        if draft is None:
            raise emails.DraftError("There is no email draft for this application.")
        text = action(draft) or done
    except emails.DraftError as exc:
        # What was done before the refusal (a save before an approval that failed) is kept.
        text = str(exc)
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text) + "#email", status_code=303)


@router.post("/hr/applications/{application_id}/email")
def email_update(
    application_id: int, do: str = Form("save"), to_address: str = Form(""), subject: str = Form(""), body: str = Form(""),
    session: Session = Depends(get_session), provider: LLMProvider = Depends(get_provider),
):
    """One form, four buttons: save, reword, approve (and send if a mail server is set), discard."""
    def act(draft):
        if do == "discard":
            emails.discard(session, draft)
            return "Draft discarded. Nothing was sent."
        if draft.status == "DRAFT" or do in ("save", "reword"):
            emails.edit(session, draft, to_address, subject, body)
            session.commit()
        if do == "reword":
            emails.reword(session, draft, provider)
            return "Reworded by the language model. Read it before approving."
        if do == "approve":
            if draft.status == "DRAFT":
                emails.approve(session, draft)
                session.commit()
            emails.send(session, draft)
            return ("Approved. A test copy was sent to the redirect address; the candidate has received nothing."
                    if draft.redirected else "Approved and sent.")
        return "Draft saved."

    return _email_action(session, application_id, act, "Draft saved.")


@router.get("/hr/openings/{opening_id}/digest", response_class=HTMLResponse)
def opening_digest(request: Request, opening_id: int, session: Session = Depends(get_session)):
    opening = _opening_or_404(session, opening_id)
    return templates.TemplateResponse(request, "digest.html", {
        "o": opening, "d": reporting.digest(session, opening), "today": date.today(),
    })


# --- applicants --------------------------------------------------------------


def _acknowledge_in_background(factory, draft_id: int) -> None:
    """Send one acknowledgement after the applicant has their answer; a slow mail server must not hold the form."""
    try:
        with factory() as session:
            emails.send_acknowledgement(session, session.get(EmailDraft, draft_id))
            session.commit()
    except Exception:
        logger.exception("acknowledgement_failed draft=%s", draft_id)


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
    background: BackgroundTasks = None,
    session: Session = Depends(get_session),
    factory=Depends(get_session_factory),
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
        session.commit()  # so the acknowledgement, sent after this response, finds it
        ack = emails.active_draft(session, application.application_id, emails.ACKNOWLEDGEMENT)
        if ack is not None:
            background.add_task(_acknowledge_in_background, factory, ack.draft_id)
    except intake.IntakeError:
        return templates.TemplateResponse(request, "apply_form.html", {
            "o": opening, "f": form, "accepting": intake.is_accepting(opening),
            "states": intake.STATES, "categories": intake.CATEGORIES,
        }, status_code=422)
    return templates.TemplateResponse(request, "apply_done.html", {
        "o": opening, "a": application, "acknowledged": ack is not None and emails.mail_is_configured(),
    }, status_code=201)
