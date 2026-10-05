"""Stage 2 (Reader) as a workflow step: read one application's resume and
store what was extracted.

The extraction itself is the existing library code -- app.resume_text and an
LLMProvider. This module only decides which state the application moves to
and writes the result into the tables, in one transaction.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.result_cache import ResultCache, cache_key
from app.resume_text import UnreadableResumeError, extract_text
from backend import states
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
    CandidateSkill,
    CandidateSubjectTaught,
    ExtractedData,
)
from llm.confidence import evaluate
from llm.interface import FIELD_NAMES, RETRYABLE_FAILURE_KINDS, ExtractionFailure, ExtractionResult, LLMProvider
from llm.postprocess import format_person_name

logger = logging.getLogger("recruitai.reader_service")

ACTOR = "agent:reader"

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_LEVEL_ORDER = {"UG": 0, "PG": 1, "PhD": 2, "Post-Doc": 3}
_ITEM_TABLES = (
    CandidateQualification, CandidateExperience, CandidatePublication, CandidateSubjectTaught,
    CandidateSkill, CandidateEvent, CandidateAchievement, CandidateGuidance, CandidateMembership,
)


def _year(text: str | None) -> int | None:
    m = _YEAR_RE.search(text or "")
    return int(m.group(1)) if m else None


def _cut(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text[:limit] or None


def read_application(
    session: Session, application: Application, provider: LLMProvider, cache: ResultCache | None = None
) -> str:
    """Run the Reader on one application; returns the state it ends in.

    RECEIVED -> PARSING -> EXTRACTED        every required field is trustworthy
                        -> PENDING_REVIEW   a person must check or complete fields (Gate 1)
                        -> FAILED           the file cannot be read (scanned, corrupt)
                        -> RECEIVED         the model was unavailable; try again later
    """
    states.transition(session, application, states.PARSING, ACTOR)

    try:
        data = Path(application.resume_path).read_bytes()
        resume = extract_text(io.BytesIO(data), application.resume_filename)
    except (UnreadableResumeError, OSError) as exc:
        # The reason names the cause, never the candidate.
        states.transition(session, application, states.FAILED, ACTOR, note=_cut(f"unreadable: {exc}", 500))
        return application.status

    model_id = getattr(provider, "cache_model_id", None)
    key = cache_key(resume.text, getattr(provider, "prompt_version", ""), model_id) if cache and model_id else None
    result = cache.get(key) if key else None
    if result is None:
        try:
            result = provider.extract_fields(resume.text)
        except ExtractionFailure as exc:
            if exc.kind in RETRYABLE_FAILURE_KINDS:
                states.transition(session, application, states.RECEIVED, ACTOR, note=f"reader_unavailable: {exc.kind}")
            else:
                states.transition(session, application, states.FAILED, ACTOR, note=f"reader_error: {exc.kind}")
            return application.status
        if key and not result.lighter_model_fallback:
            cache.put(key, result)

    outcome = evaluate(result)
    store_extraction(session, application, result, outcome.needs_review, outcome.reasons)
    states.transition(
        session,
        application,
        states.PENDING_REVIEW if outcome.needs_review else states.EXTRACTED,
        ACTOR,
        note=_cut("; ".join(outcome.reasons), 500),  # field and rule names only, no values
    )
    logger.info(
        "application_read id=%s state=%s reasons=%d", application.application_id, application.status, len(outcome.reasons)
    )
    return application.status


def store_extraction(
    session: Session, application: Application, result: ExtractionResult, needs_review: bool, reasons: list[str]
) -> None:
    """Write one ExtractionResult into the tables, replacing this application's earlier rows."""
    app_id, cand_id = application.application_id, application.candidate_id

    # The profile points at one qualification row; let go of it before the
    # rows are replaced, or the delete breaks the reference.
    profile = session.get(CandidateProfile, cand_id)
    if profile is not None and profile.highest_qualification_id is not None:
        profile.highest_qualification_id = None
        session.flush()
    for table in _ITEM_TABLES:
        session.execute(delete(table).where(table.application_id == app_id))
    existing = session.get(ExtractedData, app_id)
    if existing is not None:
        session.delete(existing)
        session.flush()

    session.add(
        ExtractedData(
            application_id=app_id,
            highest_degree=result.highest_degree.value,
            marks_pct=result.marks_pct.value,
            cgpa=result.cgpa.value,
            has_phd=result.has_phd.value,
            phd_status=result.phd_status.value,
            phd_award_date=result.phd_award_date.value,
            phd_award_date_precision=result.phd_award_date_precision,
            phd_regulation=result.phd_regulation.value,
            masters_award_date=result.masters_award_date.value,
            masters_award_date_precision=result.masters_award_date_precision,
            net_set_status=result.net_set_status.value,
            set_state=_cut(result.set_state.value, 60),
            study_leave_taken=result.study_leave_taken.value,
            teaching_years=result.teaching_years_raw.value,
            publications_count=result.publications_count.value,
            publications_in_progress_count=result.publications_in_progress_count.value,
            confidence={n: getattr(result, n).confidence for n in FIELD_NAMES},
            evidence={n: getattr(result, n).evidence for n in FIELD_NAMES},
            needs_review=needs_review,
            review_reasons=list(reasons),
            model_used=_cut(result.model_used, 60),
            lighter_model_fallback=result.lighter_model_fallback,
            raw_llm_output=result.raw_llm_output,
        )
    )

    personal = session.get(CandidatePersonalDetails, cand_id) or CandidatePersonalDetails(candidate_id=cand_id)
    personal.full_name = _cut(format_person_name(result.candidate_name.value), 200)
    personal.contact_email = _cut((result.email or "").lower() or None, 254)
    personal.contact_phone = _cut(result.phone, 40)
    # Category and disability status are form inputs; copy them, never infer them.
    personal.category = application.category
    personal.differently_abled_flag = application.differently_abled
    session.add(personal)

    # A manual upload starts with no email on record. Adopt the one read from
    # the resume unless another candidate already holds it -- that is a
    # possible duplicate applicant, which Phase 3 resolves with a person.
    candidate = application.candidate
    if personal.contact_email and candidate.email is None:
        holder = session.scalar(select(Candidate).where(Candidate.email == personal.contact_email))
        if holder is None:
            candidate.email = personal.contact_email

    highest_level = max((_LEVEL_ORDER.get(e.level, -1) for e in result.education), default=-1)
    qualifications = []
    for e in result.education:
        is_phd = e.level == "PhD"
        q = CandidateQualification(
            candidate_id=cand_id, application_id=app_id, found_in_resume=e.found_in_resume,
            degree_level=e.level, degree=_cut(e.degree, 200), discipline=_cut(e.course, 200),
            college_name=_cut(e.college, 250), university_name=_cut(e.university, 250),
            year_of_completion=_year(e.completion), completion_stated=_cut(e.completion, 10),
            marks_pct=e.marks_pct, cgpa=e.cgpa, division=_cut(e.division, 80),
            is_highest=_LEVEL_ORDER.get(e.level, -1) == highest_level,
            phd_status=(result.phd_status.value or "NOT_APPLICABLE") if is_phd else "NOT_APPLICABLE",
            phd_regulation_year=result.phd_regulation.value if is_phd else None,
            thesis_title=_cut(e.thesis_title, 500) if is_phd else None,
            guide=_cut(e.guide, 200) if is_phd else None,
            registration_stated=_cut(e.registration, 10) if is_phd else None,
        )
        qualifications.append(q)
        session.add(q)

    for x in result.experience:
        current = (x.end or "").strip().upper() == "PRESENT"
        session.add(CandidateExperience(
            candidate_id=cand_id, application_id=app_id, found_in_resume=x.found_in_resume,
            employer_name=_cut(x.institution, 250), designation_held=_cut(x.designation, 200),
            start_stated=_cut(x.start, 10), end_stated=None if current else _cut(x.end, 10),
            is_current=current, duration_stated=_cut(x.duration, 80), experience_type=x.kind,
        ))
    for p in result.publications:
        session.add(CandidatePublication(
            candidate_id=cand_id, application_id=app_id, found_in_resume=p.found_in_resume,
            title=_cut(p.title, 600) or "", publication_type=p.kind, status=p.status,
            indexing=_cut(p.indexing, 80), venue_name=_cut(p.venue, 400), year=_year(p.year),
        ))
    for v in result.events:
        session.add(CandidateEvent(
            candidate_id=cand_id, application_id=app_id, found_in_resume=v.found_in_resume,
            kind=v.kind, title=_cut(v.title, 500) or "", role=v.role,
            organiser=_cut(v.organiser, 300), duration_stated=_cut(v.duration, 80), year=_year(v.year),
        ))
    for a in result.achievements:
        session.add(CandidateAchievement(
            candidate_id=cand_id, application_id=app_id, found_in_resume=a.found_in_resume,
            kind=a.kind, title=_cut(a.title, 500) or "", details=_cut(a.details, 500),
            year=_year(a.year), status=_cut(a.status, 60),
        ))
    for g in result.guidance:
        session.add(CandidateGuidance(
            candidate_id=cand_id, application_id=app_id, found_in_resume=g.found_in_resume,
            level=g.level, description=_cut(g.description, 500) or "", student_count=g.count,
        ))
    for s in result.subjects_taught:
        session.add(CandidateSubjectTaught(candidate_id=cand_id, application_id=app_id, subject_name=_cut(s, 250) or ""))
    for s in result.skills:
        session.add(CandidateSkill(candidate_id=cand_id, application_id=app_id, skill_name=_cut(s, 200) or ""))
    for m in result.memberships:
        session.add(CandidateMembership(candidate_id=cand_id, application_id=app_id, membership=_cut(m, 300) or ""))

    session.flush()
    profile = profile or CandidateProfile(
        candidate_id=cand_id, resume_source=application.resume_source, raw_resume_path=application.resume_path
    )
    profile.raw_resume_path = application.resume_path
    top = next((q for q in qualifications if q.is_highest), None)
    profile.highest_qualification_id = top.qualification_id if top else None
    session.add(profile)
    session.flush()
