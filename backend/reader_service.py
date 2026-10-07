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
from backend.institutions import match_institution
from backend.lists import canonical_state, listed_discipline
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

    reasons = list(evaluate(result).reasons)
    if not _net_set_matters(application):
        # Under an AICTE discipline rule there is no NET/SET requirement, so neither the State of a
        # SET nor the Regulations a Ph.D. was awarded under changes anything. Not asked for.
        reasons = [r for r in reasons if r not in _NET_SET_ONLY_REASONS]
    if application.applicant_name:
        # The applicant typed their own name; how well it was read off the resume no longer matters.
        reasons = [r for r in reasons if not r.startswith("candidate_name:")]
    store_extraction(session, application, result, bool(reasons), reasons)
    # Known only once the record is stored: what the form would have supplied,
    # and whether this looks like someone already held.
    reasons += application_reasons(session, application)
    extracted = session.get(ExtractedData, application.application_id)
    extracted.review_reasons, extracted.needs_review = list(reasons), bool(reasons)
    states.transition(
        session,
        application,
        states.PENDING_REVIEW if reasons else states.EXTRACTED,
        ACTOR,
        note=_cut("; ".join(reasons), 500),  # field and rule names only, no values
    )
    logger.info("application_read id=%s state=%s reasons=%d", application.application_id, application.status, len(reasons))
    return application.status


_NET_SET_ONLY_REASONS = frozenset({
    "phd_regulation:manual_entry_required", "set_state:required_for_set", "set_state:required_for_slet",
})
# Rule sets that follow the UGC Regulations, where NET/SET is a requirement.
_UGC_RULED = ("GENERAL", "SCIENCE_HUMANITIES")


def _net_set_matters(application: Application) -> bool:
    opening = application.opening
    group = opening.discipline_group if opening is not None else "GENERAL"
    return group in _UGC_RULED or group.startswith("UGC_")


FORM_ANSWER_MISSING = "form_answer_missing"
POSSIBLE_DUPLICATE = "candidate:possible_duplicate"


def application_reasons(session: Session, application: Application) -> list[str]:
    """Reasons for review that come from the application, not from the reading."""
    return [POSSIBLE_DUPLICATE] if application.possible_duplicate_candidate_id is not None else []


def missing_form_answers(session: Session, application: Application) -> list[str]:
    """The form answers this application does not have (an HR upload came with no form).

    Not a reason to hold it back. Category and disability status matter only
    to the cl. 3.4 relaxation, and study leave only to cl. 3.11; where one of
    them would change an outcome the engine says so and names it. They are
    still worth entering for the record, and are never read off the resume.
    """
    personal = session.get(CandidatePersonalDetails, application.candidate_id)
    missing = []
    if application.category is None:
        missing.append("category")
    if personal is None or personal.state is None:
        missing.append("state")
    if application.differently_abled is None:
        missing.append("differently_abled")
    if application.study_leave_taken is None and application.resume_source != "WEB_FORM":
        missing.append("study_leave_taken")  # on the form, "not applicable" is stored as empty
    return missing


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
    extracted_email = (result.email or "").strip().lower() or None
    # What the applicant typed on the form outranks what was read off the
    # resume: they were asked directly. The resume fills what the form left
    # empty, which for an HR upload is everything.
    personal.full_name = _cut(application.applicant_name or format_person_name(result.candidate_name.value), 200)
    personal.contact_email = _cut(application.applicant_email or extracted_email, 254)
    personal.contact_phone = _cut(application.applicant_phone or result.phone, 40)
    # The State on the resume's own address is used only where the form gave
    # none, and only when it is one of the listed States.
    personal.state = application.applicant_state or canonical_state(result.state) or personal.state
    # Category and disability status are form inputs; copy them, never infer them.
    personal.category = application.category
    personal.differently_abled_flag = application.differently_abled
    session.add(personal)

    # Is this someone we already hold? The email on the resume is the test.
    # An HR upload starts with no email: adopt the resume's, unless another
    # candidate already has it. A match is only ever flagged for a person to
    # decide -- two people can share an address, and nothing is merged here.
    candidate = application.candidate
    application.possible_duplicate_candidate_id = None
    if extracted_email:
        holder = session.scalar(select(Candidate).where(Candidate.email == extracted_email))
        if holder is not None and holder.candidate_id != candidate.candidate_id:
            application.possible_duplicate_candidate_id = holder.candidate_id
        elif holder is None and candidate.email is None:
            candidate.email = extracted_email

    highest_level = max((_LEVEL_ORDER.get(e.level, -1) for e in result.education), default=-1)
    qualifications = []
    for e in result.education:
        is_phd = e.level == "PhD"
        q = CandidateQualification(
            candidate_id=cand_id, application_id=app_id, found_in_resume=e.found_in_resume,
            degree_level=e.level, degree=_cut(e.degree, 200), discipline=_cut(e.course, 200),
            institution_id=match_institution(session, e.college, e.university),
            discipline_listed=listed_discipline(e.course) or listed_discipline(e.degree),
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
            concurrent_with_study=x.concurrent_with_study,
        ))
    for p in result.publications:
        session.add(CandidatePublication(
            candidate_id=cand_id, application_id=app_id, found_in_resume=p.found_in_resume,
            title=_cut(p.title, 600) or "", publication_type=p.kind, status=p.status,
            indexing=_cut(p.indexing, 80), venue_name=_cut(p.venue, 400), year=_year(p.year),
            impact_factor=p.impact_factor, is_first_author=p.is_first_author, author_count=p.author_count,
        ))
    for v in result.events:
        session.add(CandidateEvent(
            candidate_id=cand_id, application_id=app_id, found_in_resume=v.found_in_resume,
            kind=v.kind, title=_cut(v.title, 500) or "", role=v.role,
            organiser=_cut(v.organiser, 300), duration_stated=_cut(v.duration, 80), year=_year(v.year),
            level=v.level,
        ))
    for a in result.achievements:
        session.add(CandidateAchievement(
            candidate_id=cand_id, application_id=app_id, found_in_resume=a.found_in_resume,
            kind=a.kind, title=_cut(a.title, 500) or "", details=_cut(a.details, 500),
            year=_year(a.year), status=_cut(a.status, 60),
            level=a.level, amount_stated=_cut(a.amount, 80), amount_inr=a.amount_inr,
        ))
    for g in result.guidance:
        session.add(CandidateGuidance(
            candidate_id=cand_id, application_id=app_id, found_in_resume=g.found_in_resume,
            level=g.level, description=_cut(g.description, 500) or "", student_count=g.count,
        ))
    levels = {s.name: s.level for s in result.subjects}
    for s in result.subjects_taught:
        session.add(CandidateSubjectTaught(
            candidate_id=cand_id, application_id=app_id, subject_name=_cut(s, 250) or "", course_level=levels.get(s),
        ))
    for s in result.skills:
        session.add(CandidateSkill(candidate_id=cand_id, application_id=app_id, skill_name=_cut(s, 200) or ""))
    for m in result.memberships:
        session.add(CandidateMembership(candidate_id=cand_id, application_id=app_id, membership=_cut(m, 300) or ""))

    # What the candidate states about their own research record. Kept per
    # candidate; a later resume replaces only what it states.
    stated = {k: v for k, v in result.research_profile.model_dump().items() if v is not None}
    if stated:
        research = session.get(CandidateResearchProfile, cand_id) or CandidateResearchProfile(candidate_id=cand_id)
        for name, value in stated.items():
            setattr(research, name, _cut(value, 80 if name == "google_scholar_id" else 40) if isinstance(value, str) else value)
        session.add(research)

    session.flush()
    profile = profile or CandidateProfile(
        candidate_id=cand_id, resume_source=application.resume_source, raw_resume_path=application.resume_path
    )
    profile.raw_resume_path = application.resume_path
    top = next((q for q in qualifications if q.is_highest), None)
    profile.highest_qualification_id = top.qualification_id if top else None
    session.add(profile)
    session.flush()
