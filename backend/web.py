"""The web pages: HR's openings and uploads, and the public application form.

Server-rendered, no JavaScript needed. Templates escape everything they print.

Every HR page needs a signed-in account, and `current_user` checks each
request against what that account is granted (backend/access.py). The
application form under /apply is public.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.result_cache import ResultCache
from backend import (
    access, assessor_service, emails, gate1, gate2, highlights, inbox, intake, jobs, policy, reporting, settings, states, views,
)
from backend.access import Principal
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
    HighlightNorm,
    HrDecision,
    InboxMessage,
    CandidatePersonalDetails,
    CandidateQualification,
    Job,
    JobOpening,
    School,
    UniversityPolicyRule,
    User,
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


# --- a refused form is answered with a redirect ---------------------------------
#
# A page drawn as the direct answer to a submitted form cannot be refreshed or gone back to: the browser
# asks to send the form again ("Confirm Form Resubmission"). So a refused form sends the browser back to
# the form's own address, and what was typed and what was wrong wait here, on the server, to be read once
# by that page. Kept in memory for five minutes: nothing is lost if it is, the form is simply empty again.

_CARRY = "recruitai_form"
_CARRY_SECONDS = 300
_carried_over: dict[str, tuple[float, dict]] = {}


def _back_to(url: str, kind: str, **data) -> RedirectResponse:
    now = time.monotonic()
    for old in [k for k, (at, _) in _carried_over.items() if now - at > _CARRY_SECONDS]:
        _carried_over.pop(old, None)
    token = secrets.token_urlsafe(16)
    _carried_over[token] = (now, {"kind": kind, **data})
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(_CARRY, token, max_age=_CARRY_SECONDS, httponly=True, samesite="lax", path="/")
    return response


def _carried(request: Request, kind: str) -> dict:
    """What a refused form left for this page, if anything. Read once; another page's leftovers are not taken."""
    token = request.cookies.get(_CARRY)
    at, data = _carried_over.get(token, (0.0, {})) if token else (0.0, {})
    if data.get("kind") != kind or time.monotonic() - at > _CARRY_SECONDS:
        return {}
    _carried_over.pop(token, None)
    return data


def _same_origin(request: Request) -> None:
    """A form posted from another site is refused. (The cookie is also SameSite=Lax, which stops most of these.)"""
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin and urlparse(origin).netloc != request.headers.get("host"):
        raise access.Forbidden("This request came from another site.")


def current_user(request: Request, session: Session = Depends(get_session)) -> Principal:
    """The signed-in person, already checked against what this address needs (backend.access.authorise)."""
    _same_origin(request)
    account = access.user_for_token(session, request.cookies.get(access.COOKIE))
    if account is None:
        raise access.NotSignedIn() if access.any_account(session) else access.NotSetUp()
    principal = access.principal_of(account)
    request.state.user = principal
    route = getattr(request.scope.get("route"), "path", request.url.path)
    if account.must_change_password and not route.startswith("/account/"):
        raise access.MustChangePassword()
    access.authorise(session, principal, request.method, route, request.path_params)
    return principal


def _signed_in(request: Request) -> Principal | None:
    return getattr(request.state, "user", None)


def _rights(request: Request, target: JobOpening | Application) -> dict[str, bool]:
    """What the signed-in account may do to an opening or an application, so a page offers only that.

    The pages hide what is not allowed; `access.authorise` is what refuses it.
    """
    user = _signed_in(request)
    scope = access.scope_of(target)
    return {"can_edit": user is not None and user.may(access.VIEW_EDIT, *scope),
            "can_approve": user is not None and user.may(access.APPROVE, *scope)}


def _move_targets(session: Session, user: Principal | None, exclude_id: int | None) -> list[JobOpening]:
    """Open openings an application may be moved to: only those this person may change."""
    return [o for o in session.scalars(select(JobOpening).order_by(JobOpening.opening_id.desc()))
            if o.status == "OPEN" and o.opening_id != exclude_id
            and (user is None or user.may(access.VIEW_EDIT, o.school_id, o.department_id))]


def _target_opening(session: Session, user: Principal, opening_id: int) -> JobOpening | None:
    """An opening named as the destination of a move; None if it does not exist or is not this person's to change."""
    target = session.get(JobOpening, opening_id)
    return target if target is not None and user.may(access.VIEW_EDIT, target.school_id, target.department_id) else None


def _schools_for(session: Session, user: Principal) -> list[School]:
    allowed = user.schools_for_openings()
    return [x for x in session.scalars(select(School).where(School.is_hiring_unit).order_by(School.name))
            if allowed is None or x.school_id in allowed]


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
            "policy": policy.summary((evaluation.details or {}).get("policy")) if evaluation is not None else "",
        })
    return out


# --- HR ----------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
@router.get("/hr/openings", response_class=HTMLResponse)
def hr_home(request: Request, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    """The list of openings. At the bare address an administrator is taken to the dashboard, which is their first page;
    an account tied to schools has no university-wide dashboard, so this list is its first page."""
    if request.url.path == "/" and user.is_admin:
        return RedirectResponse("/hr/dashboard" + ("?" + request.url.query if request.url.query else ""), status_code=303)
    openings = session.scalars(select(JobOpening).where(access.opening_filter(user)).order_by(JobOpening.opening_id.desc())).all()
    mine = {o.opening_id for o in openings}
    counts: dict[int, dict[str, int]] = {}
    for opening_id, status in session.execute(select(Application.opening_id, Application.status)):
        if opening_id not in mine:
            continue
        bucket = counts.setdefault(opening_id, {})
        bucket[status] = bucket.get(status, 0) + 1
        bucket["total"] = bucket.get("total", 0) + 1

    attention = [a for a in session.scalars(
        select(Application)
        .where((Application.status == states.FAILED) | (Application.possible_duplicate_candidate_id.is_not(None)))
        .order_by(Application.application_id.desc())
    ) if a.opening_id in mine or (a.opening_id is None and user.is_admin)]
    # A job that stopped retrying is not tied to one school's page; administrators deal with those.
    stuck = session.scalars(select(Job).where(Job.status == jobs.FAILED).order_by(Job.job_id.desc())).all() if user.is_admin else []
    return templates.TemplateResponse(request, "hr_home.html", {
        "openings": openings, "counts": counts, "attention": attention, "stuck": stuck, "msg": msg,
        "queue": jobs.queue_summary(session), "due": jobs.due_count(session),
        "inbox_waiting": len(inbox.waiting(session)) if user.is_admin else 0, "inbox_configured": inbox.inbox_is_configured(),
        "can_open": bool(_schools_for(session, user)), "can_read": user.may_anywhere(access.VIEW_EDIT),
        "accepting": {o.opening_id: intake.is_accepting(o) for o in openings},
        "reading": _run_lock.locked(), "last_run": last_run,
        "letters": {o: emails.waiting_in_words(c) for o, c in emails.waiting_for_approval(session).items() if o in mine},
    })


@router.get("/hr/openings/new", response_class=HTMLResponse)
def opening_form(request: Request, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    schools = _schools_for(session, user)
    if not schools:
        raise access.Forbidden("Your account is not granted a whole school to edit, so it cannot create an opening.")
    refused = _carried(request, "opening_new")
    return templates.TemplateResponse(request, "opening_new.html", {
        "schools": schools, "errors": refused.get("errors", {}), "v": refused.get("v", {}),
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
    user: Principal = Depends(current_user), session: Session = Depends(get_session),
):
    errors: dict[str, str] = {}
    closing = _parse_date(closing_date, "closing_date", errors)
    advertised = _parse_date(advertisement_date, "advertisement_date", errors)
    counted_on = _parse_date(eligibility_date, "eligibility_date", errors)
    opening = None
    schools = _schools_for(session, user)
    if school_id not in {x.school_id for x in schools}:
        errors["school_id"] = "Choose a school from the list."  # one this account may open posts in
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
        session.rollback()
        return _back_to("/hr/openings/new", "opening_new", errors=errors, v={
            "school_id": school_id, "designation": designation, "discipline_group": discipline_group,
            "department_name": department_name, "title": title, "closing_date": closing_date,
            "advertisement_ref": advertisement_ref, "advertisement_date": advertisement_date,
            "eligibility_date": eligibility_date})
    return RedirectResponse(f"/hr/openings/{opening.opening_id}?msg=" + quote("Opening created."), status_code=303)


@router.get("/hr/openings/{opening_id}", response_class=HTMLResponse)
def opening_detail(request: Request, opening_id: int, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    opening = _opening_or_404(session, opening_id)
    uploaded = _carried(request, f"upload:{opening_id}")
    return templates.TemplateResponse(request, "opening_detail.html", {
        "o": opening, "rows": _applications(session, opening_id), "msg": msg or uploaded.get("msg", ""),
        "outcomes": uploaded.get("outcomes"),
        "accepting": intake.is_accepting(opening), "due": jobs.due_count(session),
        "reading": _run_lock.locked(), "counted_on": counting_date(opening, date.today()),
        "letters": emails.waiting_in_words(emails.waiting_for_approval(session).get(opening_id)),
        "out_of_date": len(gate2.out_of_date(session, opening)),
        "other_openings": _move_targets(session, user, opening_id),
        **_rights(request, opening),
    })


_OUTCOME_WORDS = {"SHORTLISTED": "meet the post applied for", "RE_CATEGORISED": "meet a lower post",
                  "NOT_ELIGIBLE": "do not meet the minimum qualifications", "MANUAL_REVIEW": "need a person to decide"}


@router.post("/hr/openings/{opening_id}/assess")
def opening_assess(opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    """Assess every read application of this opening against the rules. No model is called."""
    _opening_or_404(session, opening_id)
    counts = assessor_service.assess_opening(session, opening_id)
    text = ("Assessed " + str(sum(counts.values())) + " application(s): "
            + "; ".join(f"{n} {_OUTCOME_WORDS[o]}" for o, n in counts.items()) + "."
            if counts else "No read application was waiting to be assessed.")
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/openings/{opening_id}/reassess")
def opening_reassess(opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    """Assess again the applications whose assessment was counted on a date other than the opening's own."""
    counts = gate2.reassess_out_of_date(session, _opening_or_404(session, opening_id), actor=user.actor)
    text = ("Assessed again " + str(sum(counts.values())) + " application(s): "
            + "; ".join(f"{n} {_OUTCOME_WORDS[o]}" for o, n in counts.items()) + "."
            if counts else "No assessment was out of date.")
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/openings/{opening_id}/close")
def opening_close(opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    intake.close_opening(session, _opening_or_404(session, opening_id))
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote("Opening closed."), status_code=303)


@router.post("/hr/openings/{opening_id}/upload", response_class=HTMLResponse)
async def opening_upload(
    request: Request, opening_id: int, resumes: list[UploadFile] = File(default=[]),
    user: Principal = Depends(current_user), session: Session = Depends(get_session),
):
    opening = _opening_or_404(session, opening_id)
    files = [(f.filename or "", await f.read()) for f in resumes if f.filename]
    outcomes = intake.hr_upload(session, opening, files) if files else []
    session.commit()
    added = len([o for o in outcomes if o.application is not None])
    # Back to the opening's own address, so refreshing the page does not upload the files again.
    return _back_to(f"/hr/openings/{opening_id}", f"upload:{opening_id}",
                    msg=f"{added} of {len(outcomes)} file(s) added." if outcomes else "No files were chosen.",
                    outcomes=[{"filename": o.filename, "reference": o.application.reference if o.application else None,
                               "problem": o.problem} for o in outcomes])


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
    user: Principal = Depends(current_user), session: Session = Depends(get_session),
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
def job_requeue(job_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    text = ("Put back in the reading queue. Press Process queue to read it." if jobs.requeue(session, job)
            else "That application is no longer waiting to be read.")
    return RedirectResponse("/hr/openings?msg=" + quote(text), status_code=303)


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
    rights = _rights(request, a)
    return templates.TemplateResponse(request, "review.html", {
        **rights,
        "a": a, "o": a.opening, "e": a.extracted, "msg": msg,
        "personal": personal,
        "flags": flags, "errors": errors or {}, "left_empty": left_empty or set(),
        "duplicate": gate1.duplicate_of(session, a), "edits": gate1.history(session, a),
        "qualifications": rows(CandidateQualification, CandidateQualification.qualification_id),
        "experience": rows(CandidateExperience, CandidateExperience.experience_id),
        "failed_note": a.transitions[-1].note if a.status == states.FAILED and a.transitions else None,
        "can_withdraw": states.WITHDRAWN in states.ALLOWED[a.status] and rights["can_edit"], "field_labels": _FIELD_LABELS,
        "correctable": a.status in gate1.BEFORE_HR_DECISION and a.extracted is not None and rights["can_edit"],
        "POST_KINDS": gate1.POST_KINDS,
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
        "other_openings": _move_targets(session, _signed_in(request), a.opening_id) if a.status in intake.MOVABLE and rights["can_edit"] else [],
        "people": access.names(session),
        "highlights": highlights.for_application(session, a) if a.extracted is not None else [],
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
def review_form(request: Request, application_id: int, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    refused = _carried(request, f"review:{application_id}")
    return _review_page(request, session, _application_or_404(session, application_id), msg=msg or refused.get("msg", ""),
                        errors=refused.get("errors"), answers=refused.get("answers"), left_empty=refused.get("left_empty"))


@router.post("/hr/applications/{application_id}/review", response_class=HTMLResponse)
async def review_save(request: Request, application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    a = _application_or_404(session, application_id, lock=True)
    form = await request.form()
    answers = {k[len("f_"):]: str(v) for k, v in form.items() if k.startswith("f_")}
    left_empty = {str(v) for v in form.getlist("empty")}
    try:
        settled = gate1.save_review(session, a, answers, left_empty, actor=user.actor)
    except gate1.ReviewError as exc:
        session.rollback()
        return _back_to(f"/hr/applications/{application_id}", f"review:{application_id}", errors=exc.errors, answers=answers,
                        left_empty=left_empty, msg=exc.errors.get("", "") or "Not saved. Correct the fields marked below.")
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
def review_duplicate(application_id: int, same: str = Form(""), user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    if same not in ("yes", "no"):
        raise HTTPException(status_code=422, detail="answer yes or no")
    return _review_action(
        session, application_id, lambda a: gate1.resolve_duplicate(session, a, same_person=same == "yes", actor=user.actor),
        "Recorded as the same person; the records are joined." if same == "yes" else "Recorded as a different person.",
    )


@router.post("/hr/applications/{application_id}/withdraw")
def review_withdraw(application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    return _review_action(session, application_id, lambda a: gate1.withdraw(session, a, user.actor), "Application withdrawn.")


@router.get("/hr/applications/{application_id}/resume")
def review_resume(application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    """The stored resume, so the person checking can read the original beside the form."""
    a = _application_or_404(session, application_id)
    path = Path(a.resume_path)
    if not path.is_file() or path.suffix.lower() not in _RESUME_TYPES:
        raise HTTPException(status_code=404, detail="the resume file is not available")
    # Named by its reference, not by the candidate or the original filename.
    return FileResponse(path, media_type=_RESUME_TYPES[path.suffix.lower()],
                        filename=f"{a.reference}{path.suffix.lower()}", content_disposition_type="inline")


@router.post("/hr/applications/{application_id}/reopen")
async def review_reopen(request: Request, application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    fields = [str(v) for v in form.getlist("fields")]
    return _review_action(session, application_id, lambda a: gate1.reopen_fields(session, a, fields, user.actor),
                          "Opened for entry. Fill in the fields below and save.")


@router.post("/hr/applications/{application_id}/read-again")
def review_read_again(application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    """Queue the resume to be read afresh. No model is called here; it waits for a person to run the queue."""
    return _review_action(session, application_id, lambda a: gate1.read_again(session, a, user.actor),
                          "Put back in the reading queue. Press the Read button on the opening's page to read it (one request to the language model).")


@router.post("/hr/applications/{application_id}/posts")
async def review_posts(request: Request, application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    form = {k: str(v) for k, v in (await request.form()).items()}
    a = _application_or_404(session, application_id, lock=True)
    assessed = a.status in gate2.AWAITING_HR
    try:
        n = gate1.save_posts(session, a, form, user.actor)
        text = f"Saved {n} post(s)." + (" The earlier assessment is set aside: press Assess on the opening's page to assess it again." if assessed else "")
    except gate1.ReviewError as exc:
        session.rollback()
        text = "Not saved. " + exc.errors.get("", "")
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text) + "#posts", status_code=303)


@router.post("/hr/applications/{application_id}/move")
def application_move(application_id: int, opening_id: int = Form(0), user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    a = _application_or_404(session, application_id, lock=True)
    target = _target_opening(session, user, opening_id)
    try:
        if target is None:
            raise intake.IntakeError({"opening": "Choose an opening to move it to."})
        intake.move_application(session, a, target, user.actor)
        text = f"Moved to {target.reference}. It will be assessed under that opening's rules."
    except intake.IntakeError as exc:
        session.rollback()
        text = exc.errors.get("opening", "That could not be done.")
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/openings/{opening_id}/move-all")
def opening_move_all(opening_id: int, target_id: int = Form(0), user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    source, target = _opening_or_404(session, opening_id), _target_opening(session, user, target_id)
    if target is None or target.opening_id == source.opening_id:
        text = "Choose another opening to move the applications to."
    else:
        moved, stayed = intake.move_all(session, source, target, user.actor)
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
def decision_approve(application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    return _decision_action(session, application_id, lambda a: gate2.approve(session, a, user.actor), "Approved as assessed.")


@router.post("/hr/applications/{application_id}/override")
def decision_override(application_id: int, outcome: str = Form(""), designation: str = Form(""),
                      justification: str = Form(""), user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    return _decision_action(
        session, application_id, lambda a: gate2.override(session, a, outcome, designation or None, justification, user.actor),
        "Your decision is recorded with its justification.")


@router.post("/hr/applications/{application_id}/return")
async def decision_return(request: Request, application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    fields = [str(v) for v in form.getlist("fields")]
    if form.get("how") == "reassess":
        return _decision_action(session, application_id, lambda a: gate2.return_for_reassessment(session, a, user.actor),
                                "Returned. Press Assess on the opening's page to assess it again.")
    return _decision_action(session, application_id, lambda a: gate2.return_for_correction(session, a, fields, user.actor),
                            "Returned for correction. Check the fields below, then assess it again.")


@router.post("/hr/applications/{application_id}/reopen-decision")
def decision_reopen(application_id: int, reason: str = Form(""), user: Principal = Depends(current_user),
                    session: Session = Depends(get_session)):
    return _decision_action(
        session, application_id, lambda a: gate2.reopen_decision(session, a, reason, user.actor),
        "The decision is reopened and its letter dropped. Press Assess on the opening's page, then decide again.")


@router.get("/hr/openings/{opening_id}/export.xlsx")
def opening_export(opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    from backend.export import opening_workbook

    opening = _opening_or_404(session, opening_id)
    data = opening_workbook(session, opening, STATE_LABELS)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{opening.reference}.xlsx"'})


# --- the inbox channel -------------------------------------------------------


@router.get("/hr/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
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
def inbox_check(user: Principal = Depends(current_user), session: Session = Depends(get_session)):
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
def inbox_assign(inbox_id: int, opening_id: str = Form(""), user: Principal = Depends(current_user), session: Session = Depends(get_session)):
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
def inbox_attachment(inbox_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    """The resume attached to a held message, so the person choosing its opening can read it."""
    row = session.get(InboxMessage, inbox_id)
    path = Path(row.attachment_path) if row is not None and row.attachment_path else None
    if path is None or not path.is_file() or path.suffix.lower() not in _RESUME_TYPES:
        raise HTTPException(status_code=404, detail="no attachment is held for this message")
    return FileResponse(path, media_type=_RESUME_TYPES[path.suffix.lower()],
                        filename=f"inbox-{inbox_id}{path.suffix.lower()}", content_disposition_type="inline")


@router.post("/hr/inbox/{inbox_id}/set-aside")
def inbox_set_aside(inbox_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    row = _inbox_row(session, inbox_id)
    try:
        inbox.set_aside(session, row)
        text = "Set aside."
    except ValueError as exc:
        text = str(exc)
    return RedirectResponse("/hr/inbox?msg=" + quote(text), status_code=303)


@router.get("/hr/openings/{opening_id}/edit", response_class=HTMLResponse)
def opening_edit_form(request: Request, opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    o = _opening_or_404(session, opening_id)
    refused = _carried(request, f"opening_edit:{opening_id}")
    return _opening_edit_page(request, session, o, refused.get("errors", {}), refused.get("v") or {
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
    user: Principal = Depends(current_user), session: Session = Depends(get_session),
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
                                           title=title, closing_date=closing, eligibility_date=counted_on, actor=user.actor)
        except intake.IntakeError as exc:
            session.rollback()
            o, errors = _opening_or_404(session, opening_id), exc.errors
    if errors:
        return _back_to(f"/hr/openings/{opening_id}/edit", f"opening_edit:{opening_id}", errors=errors, v={
            "discipline_group": discipline_group, "department_name": department_name, "title": title, "closing_date": closing_date,
            "eligibility_date": eligibility_date})
    text = "Opening updated."
    if counts["reassess"]:
        what = "rule set" if discipline_group != rules_before else "date eligibility is counted on"
        text += f" The {what} changed, so {counts['reassess']} assessed application(s) were set aside: press Assess to assess them again."
    if counts["decided"]:
        text += f" {counts['decided']} already decided by HR keep their decision; look at them again yourself."
    return RedirectResponse(f"/hr/openings/{opening_id}?msg=" + quote(text), status_code=303)


@router.post("/hr/openings/{opening_id}/reopen")
def opening_reopen(opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
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
def opening_emails(request: Request, opening_id: int, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    o = _opening_or_404(session, opening_id)
    rows = _email_rows(session, opening_id)
    can_approve = _rights(request, o)["can_approve"]
    for r in rows:
        r["can_send"] = r["can_send"] and can_approve
    return templates.TemplateResponse(request, "opening_emails.html", {
        "can_approve": can_approve,
        "o": o, "rows": rows, "msg": msg, "sendable": len([r for r in rows if r["can_send"]]),
        "mail_configured": emails.mail_is_configured(), "redirect_to": settings.EMAIL_REDIRECT_TO,
    })


@router.post("/hr/openings/{opening_id}/emails")
async def opening_emails_send(request: Request, opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    """Approve, and send, the letters a person ticked on the opening's emails page."""
    _opening_or_404(session, opening_id)
    form = await request.form()
    ticked = {int(v) for v in form.getlist("draft_id") if str(v).isdigit()}
    # Only this opening's own live drafts: an id from anywhere else is ignored.
    drafts = [r["d"] for r in _email_rows(session, opening_id) if r["d"].draft_id in ticked]
    text = emails.summarise_sending(emails.approve_and_send(session, drafts, actor=user.actor))
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
    user: Principal = Depends(current_user), session: Session = Depends(get_session), provider: LLMProvider = Depends(get_provider),
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
                emails.approve(session, draft, user.actor)
                session.commit()
            emails.send(session, draft, actor=user.actor)
            return ("Approved. A test copy was sent to the redirect address; the candidate has received nothing."
                    if draft.redirected else "Approved and sent.")
        return "Draft saved."

    return _email_action(session, application_id, act, "Draft saved.")


@router.get("/hr/openings/{opening_id}/digest", response_class=HTMLResponse)
def opening_digest(request: Request, opening_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    opening = _opening_or_404(session, opening_id)
    return templates.TemplateResponse(request, "digest.html", {
        "o": opening, "d": reporting.digest(session, opening), "today": date.today(),
    })


# --- signing in ---------------------------------------------------------------


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


def _with_cookie(response, request: Request, token: str):
    # No lifetime is given, so the browser drops it when it closes; the server ends it after eight idle hours.
    response.set_cookie(access.COOKIE, token, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
    return response


# What the sign-in page may say on arrival. A code in the address chooses one; the address cannot supply words of its own,
# since this is the page where a made-up message ("your password has expired, call ...") would do most harm.
_SIGN_IN_MESSAGES = {"out": "You are signed out.", "changed": "Your password is changed. Sign in with the new one."}


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", said: str = "", expired: str = "", session: Session = Depends(get_session)):
    if not access.any_account(session):
        return RedirectResponse("/setup", status_code=303)
    if access.user_for_token(session, request.cookies.get(access.COOKIE)) is not None:
        return RedirectResponse(_safe_next(next), status_code=303)  # already signed in: there is nothing to do here
    refused = _carried(request, "login")
    return templates.TemplateResponse(request, "login.html", {
        "next": _safe_next(next), "msg": _SIGN_IN_MESSAGES.get(said, ""), "email": refused.get("email", ""), "error": refused.get("error", ""),
        "expired": bool(expired), "can_reset": emails.mail_is_configured()})


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/"),
          session: Session = Depends(get_session)):
    _same_origin(request)
    try:
        token = access.sign_in(session, email, password)
    except access.AccessError as exc:
        session.commit()  # the count of wrong passwords is kept
        target = _safe_next(next)
        return _back_to("/login" + ("?next=" + quote(target, safe="") if target != "/" else ""), "login", error=str(exc), email=email[:254])
    session.commit()
    return _with_cookie(RedirectResponse(_safe_next(next), status_code=303), request, token)


@router.post("/logout")
def logout(request: Request, session: Session = Depends(get_session)):
    _same_origin(request)
    access.sign_out(session, request.cookies.get(access.COOKIE))
    session.commit()
    response = RedirectResponse("/login?said=out", status_code=303)
    response.delete_cookie(access.COOKIE, path="/")
    # Tells the browser to drop every page of this site it is holding, including those it keeps ready for Back.
    response.headers["Clear-Site-Data"] = '"cache"'
    return response


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, session: Session = Depends(get_session)):
    if access.any_account(session):
        return RedirectResponse("/login", status_code=303)
    refused = _carried(request, "setup")
    return templates.TemplateResponse(request, "setup.html", {
        "name": refused.get("name", ""), "email": refused.get("email", ""), "error": refused.get("error", ""), "min": access.MIN_PASSWORD})


@router.post("/setup", response_class=HTMLResponse)
def setup(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""), again: str = Form(""),
          session: Session = Depends(get_session)):
    """Create the first administrator. Works only while there is no account at all."""
    _same_origin(request)
    try:
        if password != again:
            raise access.AccessError("The two passwords are not the same.")
        access.create_first_administrator(session, name=name, email=email, password=password)
        token = access.sign_in(session, email, password)
    except access.AccessError as exc:
        session.rollback()
        if access.any_account(session):
            return RedirectResponse("/login", status_code=303)
        return _back_to("/setup", "setup", error=str(exc), name=name[:200], email=email[:254])
    session.commit()
    return _with_cookie(RedirectResponse("/hr/openings?msg=" + quote("Your administrator account is created. Add the other accounts under Accounts."),
                                         status_code=303), request, token)


@router.get("/account/password", response_class=HTMLResponse)
def password_form(request: Request, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    account = session.get(User, user.user_id) if user.user_id else None
    return templates.TemplateResponse(request, "password.html", {
        "error": _carried(request, "password").get("error", ""), "min": access.MIN_PASSWORD,
        "forced": bool(account and account.must_change_password)})


@router.post("/account/password", response_class=HTMLResponse)
def password_change(request: Request, current: str = Form(""), new: str = Form(""), again: str = Form(""),
                    user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    account = session.get(User, user.user_id) if user.user_id else None
    try:
        if account is None:
            raise access.AccessError("This account has no password to change.")
        if new != again:
            raise access.AccessError("The two new passwords are not the same.")
        access.change_own_password(session, account, current, new, keep_token=request.cookies.get(access.COOKIE))
    except access.AccessError as exc:
        return _back_to("/account/password", "password", error=str(exc))
    session.commit()
    home = "/" if user.user_type != "INTERVIEWER" else "/interview"
    return RedirectResponse(home + "?msg=" + quote("Your password is changed."), status_code=303)


def _accounts_page(request: Request, session: Session, msg: str = "", status_code: int = 200):
    return templates.TemplateResponse(request, "users.html", {
        "users": session.scalars(select(User).order_by(User.name)).all(), "msg": msg,
        "schools": session.scalars(select(School).where(School.is_hiring_unit).order_by(School.name)).all(),
        "USER_TYPES": access.USER_TYPES, "LEVELS": access.LEVELS, "min": access.MIN_PASSWORD,
    }, status_code=status_code)


@router.get("/admin/users", response_class=HTMLResponse)
def accounts(request: Request, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    return _accounts_page(request, session, msg)


def _account_action(session: Session, action, done: str):
    try:
        action()
        session.commit()
        text = done
    except access.AccessError as exc:
        session.rollback()
        text = "Not done. " + str(exc)
    return RedirectResponse("/admin/users?msg=" + quote(text), status_code=303)


def _account_or_404(session: Session, user_id: int) -> User:
    account = session.get(User, user_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


@router.post("/admin/users")
def account_create(name: str = Form(""), email: str = Form(""), user_type: str = Form(""), password: str = Form(""),
                   user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    return _account_action(
        session, lambda: access.create_user(session, name=name, email=email, password=password, user_type=user_type),
        "Account created. Give the person the temporary password yourself; they must choose their own when they first sign in.")


@router.post("/admin/users/{user_id}/update")
def account_update(user_id: int, do: str = Form(""), user_type: str = Form(""), password: str = Form(""),
                   user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    account = _account_or_404(session, user_id)
    if do in ("close", "type") and account.user_id == user.user_id:
        return RedirectResponse("/admin/users?msg=" + quote("Not done. Ask another university administrator to change your own account."),
                                status_code=303)
    actions = {
        "close": (lambda: access.set_active(session, account, False), "Account closed. It can no longer sign in."),
        "open": (lambda: access.set_active(session, account, True), "Account opened again."),
        "type": (lambda: access.set_user_type(session, account, user_type), "Kind of account changed."),
        "password": (lambda: access.reset_password(session, account, password),
                     "Temporary password set. The person must choose their own at the next sign-in."),
    }
    if do not in actions:
        raise HTTPException(status_code=422, detail="unknown action")
    return _account_action(session, *actions[do])


@router.post("/admin/users/{user_id}/grants")
def account_grant(user_id: int, school_id: str = Form(""), department_id: str = Form(""), level: str = Form(""),
                  user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    account = _account_or_404(session, user_id)
    department = int(department_id) if department_id.isdigit() else None
    return _account_action(session, lambda: access.grant(session, account, school_id, department, level), "Access granted.")


@router.post("/admin/users/{user_id}/grants/{grant_id}/remove")
def account_revoke(user_id: int, grant_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    account = _account_or_404(session, user_id)
    return _account_action(session, lambda: access.revoke(session, account, grant_id), "Access removed.")


# --- a forgotten password --------------------------------------------------------

_RESET_SENT = ("If there is an account with that address, a link to choose a new password has been sent to it. "
               "The link works once, for 30 minutes.")


@router.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request, sent: str = ""):
    return templates.TemplateResponse(request, "forgot.html", {
        "available": emails.mail_is_configured(), "sent": _RESET_SENT if sent and emails.mail_is_configured() else ""})


@router.post("/forgot", response_class=HTMLResponse)
def forgot(request: Request, email: str = Form(""), session: Session = Depends(get_session)):
    """Email a reset link to the account's own address. The answer is the same whether or not there is such an account."""
    _same_origin(request)
    if not emails.mail_is_configured():
        return RedirectResponse("/forgot", status_code=303)
    made = access.start_password_reset(session, email)
    session.commit()
    if made is not None:
        account, token = made
        link = (settings.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")) + "/reset/" + token
        emails.send_to_staff(account.email, "RecruitAI: choose a new password", (
            f"Dear {account.name},\n\nA new password was asked for on your RecruitAI account. To choose one, open this link "
            f"within 30 minutes:\n\n{link}\n\nIt works once. If you did not ask for this, ignore this message: your password "
            f"has not been changed.\n\nHuman Resources\n{settings.INSTITUTION_NAME}\n"))
    return RedirectResponse("/forgot?sent=1", status_code=303)


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, token: str, session: Session = Depends(get_session)):
    live = access.reset_is_live(session, token)
    return templates.TemplateResponse(request, "reset.html", {
        "live": live, "token": token, "error": _carried(request, "reset").get("error", ""), "min": access.MIN_PASSWORD},
        status_code=200 if live else 410)


@router.post("/reset/{token}", response_class=HTMLResponse)
def reset(request: Request, token: str, new: str = Form(""), again: str = Form(""), session: Session = Depends(get_session)):
    _same_origin(request)
    try:
        if new != again:
            raise access.AccessError("The two passwords are not the same.")
        access.finish_password_reset(session, token, new)
    except access.AccessError as exc:
        session.rollback()
        return _back_to(f"/reset/{token}", "reset", error=str(exc))
    session.commit()
    return RedirectResponse("/login?said=changed", status_code=303)


# --- the interview panel's view (Section 17.5) -------------------------------------


def _shortlisted_or_404(session: Session, application_id: int) -> tuple[Application, HrDecision]:
    """An application HR has short-listed. Anything else does not exist as far as the panel's view goes."""
    a = session.get(Application, application_id)
    decided = gate2.final_decision(session, a) if a is not None else None
    if decided is None or decided.final_outcome != "SHORTLISTED":
        raise HTTPException(status_code=404, detail="application not found")
    return a, decided


@router.get("/interview", response_class=HTMLResponse)
def interview_list(request: Request, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    openings = {o.opening_id: o for o in session.scalars(select(JobOpening).where(access.opening_filter(user, interview=True)))}
    rows = []
    for a in session.scalars(select(Application).where(Application.opening_id.in_(openings), Application.status.in_(gate2.DECIDED))
                             .order_by(Application.opening_id.desc(), Application.application_id)):
        decided = gate2.final_decision(session, a)
        if decided is None or decided.final_outcome != "SHORTLISTED":
            continue
        personal = session.get(CandidatePersonalDetails, a.candidate_id)
        rows.append({"a": a, "o": openings[a.opening_id], "name": a.applicant_name or (personal.full_name if personal else None),
                     "post": decided.final_designation, "highlights": len(highlights.for_application(session, a))})
    return templates.TemplateResponse(request, "interview_list.html", {"rows": rows, "msg": msg})


@router.get("/interview/applications/{application_id}", response_class=HTMLResponse)
def interview_record(request: Request, application_id: int, user: Principal = Depends(current_user),
                     session: Session = Depends(get_session)):
    a, decided = _shortlisted_or_404(session, application_id)
    show = views.interviewer_visibility(session)
    session.commit()

    def rows(table, key):
        return session.scalars(select(table).where(table.application_id == a.application_id).order_by(key)).all()

    personal = session.get(CandidatePersonalDetails, a.candidate_id)
    evaluation = assessor_service.latest_evaluation(session, a.application_id)
    details = (evaluation.details or {}) if evaluation else {}
    # Only what the view allows is handed to the page at all, so a slip in the template cannot show the rest.
    context = {
        "a": a, "o": a.opening, "post": decided.final_designation, "show": show,
        "name": a.applicant_name or (personal.full_name if personal else None),
    }
    if show["personal.contact"]:
        context["contact"] = {"email": a.applicant_email or (personal.contact_email if personal else None),
                              "phone": a.applicant_phone or (personal.contact_phone if personal else None),
                              "address": personal.current_address if personal else None}
    if show["personal.category"]:
        context["category"] = {"category": a.category, "differently_abled": a.differently_abled}
    if show["personal.state"]:
        context["state"] = personal.state if personal else None
    if show["assessment.working"]:
        context["ranks"], context["plain_finding"] = details.get("ranks") or [], reporting.explain(a, evaluation)
    if show["assessment.scores"]:
        context["scores"] = {k: details.get(k) for k in ("experience_years", "experience_kinds", "research_score", "shortlist_score")}
    if show["assessment.policy"]:
        context["policy"] = details.get("policy") or []
    if show["highlights.*"]:
        context["highlights"] = highlights.for_application(session, a)
    if show["qualifications.*"]:
        context["qualifications"] = rows(CandidateQualification, CandidateQualification.qualification_id)
        context["e"] = a.extracted
    if show["experience.*"]:
        context["experience"] = rows(CandidateExperience, CandidateExperience.experience_id)
    if show["research.*"]:
        context["research"] = session.get(CandidateResearchProfile, a.candidate_id)
    if show["publications.*"]:
        context["publications"] = rows(CandidatePublication, CandidatePublication.publication_id)
    if show["achievements.*"]:
        context["achievements"], context["guidance"] = rows(CandidateAchievement, CandidateAchievement.id), rows(CandidateGuidance, CandidateGuidance.id)
    if show["events.*"]:
        context["events"] = rows(CandidateEvent, CandidateEvent.id)
    if show["teaching.*"]:
        context["subjects"], context["skills"] = rows(CandidateSubjectTaught, CandidateSubjectTaught.id), rows(CandidateSkill, CandidateSkill.id)
        context["memberships"] = rows(CandidateMembership, CandidateMembership.id)
    return templates.TemplateResponse(request, "interview_record.html", context)


@router.get("/interview/applications/{application_id}/resume")
def interview_resume(application_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    a, _ = _shortlisted_or_404(session, application_id)
    path = Path(a.resume_path)
    if not views.interviewer_visibility(session)["application.resume"] or not path.is_file() or path.suffix.lower() not in _RESUME_TYPES:
        raise HTTPException(status_code=404, detail="the resume file is not available")
    return FileResponse(path, media_type=_RESUME_TYPES[path.suffix.lower()],
                        filename=f"{a.reference}{path.suffix.lower()}", content_disposition_type="inline")


@router.get("/admin/views", response_class=HTMLResponse)
def views_form(request: Request, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    show = views.interviewer_visibility(session)
    session.commit()
    return templates.TemplateResponse(request, "views.html", {"parts": views.INTERVIEWER_PARTS, "show": show, "msg": msg})


@router.post("/admin/views")
async def views_save(request: Request, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    views.set_interviewer_visibility(session, {str(v) for v in form.getlist("shown")})
    session.commit()
    return RedirectResponse("/admin/views?msg=" + quote("Saved. The interview panel's view changed at once."), status_code=303)


# --- highlights, the university's own criteria, and the dashboard -------------------


@router.post("/hr/applications/{application_id}/highlights")
def highlight_add(application_id: int, description: str = Form(""), user: Principal = Depends(current_user),
                  session: Session = Depends(get_session)):
    a = _application_or_404(session, application_id)
    try:
        highlights.add(session, a, description, user.actor)
        text = "Highlight added."
    except highlights.HighlightError as exc:
        text = str(exc)
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote(text) + "#highlights", status_code=303)


@router.post("/hr/applications/{application_id}/highlights/{highlight_id}/remove")
def highlight_remove(application_id: int, highlight_id: int, user: Principal = Depends(current_user),
                     session: Session = Depends(get_session)):
    highlights.remove(session, _application_or_404(session, application_id), highlight_id)
    return RedirectResponse(f"/hr/applications/{application_id}?msg=" + quote("Highlight removed.") + "#highlights", status_code=303)


@router.get("/hr/policy", response_class=HTMLResponse)
def policy_page(request: Request, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "policy.html", {
        "msg": msg, "rules": session.scalars(select(UniversityPolicyRule).order_by(UniversityPolicyRule.policy_id)).all(),
        "norms": session.scalars(select(HighlightNorm).order_by(HighlightNorm.id)).all(),
        "schools": session.scalars(select(School).where(School.is_hiring_unit).order_by(School.name)).all(),
        "CRITERIA": policy.CRITERIA,
    })


@router.post("/hr/policy/rules")
def policy_add(school_id: str = Form(""), designation: str = Form(""), criterion: str = Form(""), value: str = Form(""),
               user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    try:
        policy.add_rule(session, school_id=school_id or None, designation=designation, criterion=criterion, value=value, actor=user.actor)
        text = "Criterion added. It applies to assessments made from now on; assess an application again to hold it against the new criterion."
    except policy.PolicyError as exc:
        text = "Not added. " + str(exc)
    return RedirectResponse("/hr/policy?msg=" + quote(text), status_code=303)


@router.post("/hr/policy/rules/{policy_id}/remove")
def policy_remove(policy_id: int, user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    policy.remove_rule(session, policy_id)
    return RedirectResponse("/hr/policy?msg=" + quote("Criterion removed. Assessments already made keep the result they recorded."), status_code=303)


@router.post("/hr/policy/norms")
def norm_set(school_id: str = Form(""), h_index: str = Form(""), citations: str = Form(""), impact_factor: str = Form(""),
             user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    try:
        row = highlights.set_norm(session, school_id or None, h_index, citations, impact_factor)
        text = "Norms saved. Highlights follow them at once." if row is not None else "Norms removed for that scope."
    except highlights.HighlightError as exc:
        text = "Not saved. " + str(exc)
    return RedirectResponse("/hr/policy?msg=" + quote(text), status_code=303)


@router.get("/hr/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, msg: str = "", user: Principal = Depends(current_user), session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "dashboard.html", {
        "d": reporting.dashboard(session), "today": date.today(), "msg": msg,
        "inbox_waiting": len(inbox.waiting(session)), "letters": sum(sum(c.values()) for c in emails.waiting_for_approval(session).values()),
        "due": jobs.due_count(session),
    })


# --- what a refused request is answered with -----------------------------------


# The JSON API answers in JSON; everything else is a page for a person.
_API_PREFIXES = ("/applications", "/schools", "/rules", "/health")
_PROBLEMS = {
    404: ("Page not found", "This page does not exist, or it is not one your account can see."),
    405: ("This address cannot be opened directly", "It is where a form on another page is sent. Go back and use the button there."),
    422: ("That could not be read", "Something in the address or the form was not what this page expects. Nothing was changed."),
    500: ("Something went wrong", "The fault is on our side, not in what you entered. Nothing more was changed. Try again; if it happens again, tell the administrator."),
}


def install(app) -> None:
    """Register how refusals and errors are answered, and the headers every answer carries."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    def _problem(request: Request, status: int, detail=None):
        if request.url.path.startswith(_API_PREFIXES):
            return JSONResponse({"detail": detail if detail is not None else _PROBLEMS.get(status, ("",))[0]}, status_code=status)
        title, text = _PROBLEMS.get(status, _PROBLEMS[500 if status >= 500 else 422])
        return templates.TemplateResponse(request, "error.html", {"title": title, "text": text}, status_code=status)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            # Pages hold personal data. The browser must not keep them: after signing out, Back asks the server
            # again and gets the sign-in page, where a kept copy would show the candidate's record to the next person.
            response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Frame-Options", "DENY")  # never shown inside another site's page
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")  # addresses here are not told to other sites
        return response

    @app.exception_handler(StarletteHTTPException)
    def _http_problem(request: Request, exc):
        return _problem(request, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    def _unreadable(request: Request, exc):
        return _problem(request, 422, exc.errors() if request.url.path.startswith(_API_PREFIXES) else None)

    @app.exception_handler(Exception)
    def _fault(request: Request, exc):
        logger.exception("unhandled_error path=%s", request.url.path)
        return _problem(request, 500)

    @app.exception_handler(access.NotSignedIn)
    def _to_sign_in(request: Request, exc):
        if request.method == "GET":
            target = request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse("/login?next=" + quote(target, safe=""), status_code=303)
        # A form sent after the session ended. Nothing was done with it. After signing in the person is taken
        # back to the page the form was on, which is the most that can be given back: what was typed is gone.
        came_from = urlparse(request.headers.get("referer", ""))
        back = came_from.path + ("?" + came_from.query if came_from.query else "") if came_from.netloc == request.headers.get("host") else "/"
        return RedirectResponse("/login?expired=1&next=" + quote(_safe_next(back), safe=""), status_code=303)

    @app.exception_handler(access.NotSetUp)
    def _to_setup(request: Request, exc):
        return RedirectResponse("/setup", status_code=303)

    @app.exception_handler(access.GoTo)
    def _elsewhere(request: Request, exc):
        return RedirectResponse(exc.location, status_code=303)

    @app.exception_handler(access.MustChangePassword)
    def _to_password(request: Request, exc):
        return RedirectResponse("/account/password", status_code=303)

    @app.exception_handler(access.Forbidden)
    def _refused(request: Request, exc):
        return templates.TemplateResponse(request, "refused.html", {"message": str(exc)}, status_code=403)

    @app.exception_handler(access.OutOfScope)
    def _not_found(request: Request, exc):
        # The same answer as for something that does not exist.
        return _problem(request, 404, "not found")


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
    refused = _carried(request, f"apply:{opening_id}")
    return templates.TemplateResponse(request, "apply_form.html", {
        "o": opening, "f": refused.get("form") or intake.ApplicantForm(), "accepting": intake.is_accepting(opening),
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
        session.rollback()
        return _back_to(f"/apply/{opening_id}", f"apply:{opening_id}", form=form)
    # To a page of its own, so that refreshing it does not send the application a second time. The page is found
    # by part of the resume file's fingerprint, which no one else can guess; it shows the reference and no more.
    return RedirectResponse(f"/apply/{opening_id}/received/{application.resume_sha256[:24]}", status_code=303)


@router.get("/apply/{opening_id}/received/{key}", response_class=HTMLResponse)
def apply_received(request: Request, opening_id: int, key: str, session: Session = Depends(get_session)):
    opening = _opening_or_404(session, opening_id)
    application = session.scalars(select(Application).where(
        Application.opening_id == opening_id, Application.resume_sha256.startswith(key, autoescape=True))
        .order_by(Application.application_id.desc())).first() if len(key) == 24 else None
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    ack = emails.active_draft(session, application.application_id, emails.ACKNOWLEDGEMENT)
    return templates.TemplateResponse(request, "apply_done.html", {
        "o": opening, "a": application, "acknowledged": ack is not None and emails.mail_is_configured()})
