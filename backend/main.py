"""The HTTP API.

Phase 1 exposes what is needed to prove the foundation end to end: create an
application with its form fields and resume, run the Reader on it, and read
back its record, state and audit trail. The screens and the public form are
later phases.

Run locally:  uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import config
from app.result_cache import ResultCache
from backend import states
from backend.db import get_session
from backend.models import (
    Application,
    Candidate,
    CandidatePersonalDetails,
    CandidatePublication,
    CandidateQualification,
    CandidateExperience,
    Department,
    School,
)
from backend.reader_service import read_application
from backend.storage import RejectedUpload, save_resume
from llm.interface import ExtractionFailure, LLMProvider

logger = logging.getLogger("recruitai.api")

DESIGNATIONS = ("ASSISTANT_PROFESSOR", "ASSOCIATE_PROFESSOR", "PROFESSOR", "SENIOR_PROFESSOR")
CATEGORIES = ("General", "SC", "ST", "OBC-NCL", "EWS", "PwD")
RESUME_SOURCES = ("WEB_FORM", "EMAIL", "GOOGLE_FORM", "MANUAL_UPLOAD")

app = FastAPI(title="RecruitAI", version="0.1.0")

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """The configured LLM provider, built on first use so the API starts
    (and health checks pass) even when no key is set."""
    global _provider
    if _provider is None:
        if config.LLM_PROVIDER == "fake":
            from llm.providers.fake_provider import FakeProvider

            _provider = FakeProvider()
        else:
            from llm.providers.gemini_provider import GeminiProvider

            try:
                _provider = GeminiProvider()
            except ExtractionFailure as exc:
                raise HTTPException(status_code=503, detail=f"LLM provider is not configured: {exc}") from exc
    return _provider


def get_cache() -> ResultCache:
    return ResultCache()


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@app.get("/schools")
def list_schools(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = session.scalars(select(School).order_by(School.school_id)).all()
    return [
        {
            "school_id": s.school_id,
            "name": s.name,
            "faculty": s.faculty,
            "regulator": s.regulator_id,
            "overlay_regulator": s.overlay_regulator_id,
            "is_hiring_unit": s.is_hiring_unit,
            "regulator_implemented": s.regulator.is_implemented,
        }
        for s in rows
    ]


def _application_summary(a: Application) -> dict[str, Any]:
    return {
        "application_id": a.application_id,
        "candidate_id": a.candidate_id,
        "school_id": a.school_id,
        "department_id": a.department_id,
        "applied_designation": a.applied_designation,
        "status": a.status,
        "resume_source": a.resume_source,
        "created_at": a.created_at.isoformat(),
    }


@app.post("/applications", status_code=201)
async def create_application(
    school_id: str = Form(...),
    applied_designation: str = Form(...),
    resume: UploadFile = File(...),
    department_id: int | None = Form(None),
    category: str | None = Form(None),
    differently_abled: bool | None = Form(None),
    study_leave_taken: bool | None = Form(None),
    resume_source: str = Form("MANUAL_UPLOAD"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create an application in RECEIVED state. The Reader is run separately."""
    school = session.get(School, school_id)
    if school is None or not school.is_hiring_unit:
        raise HTTPException(status_code=422, detail="unknown school, or not a hiring unit")
    if applied_designation not in DESIGNATIONS:
        raise HTTPException(status_code=422, detail=f"applied_designation must be one of {', '.join(DESIGNATIONS)}")
    if category is not None and category not in CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category must be one of {', '.join(CATEGORIES)}")
    if resume_source not in RESUME_SOURCES:
        raise HTTPException(status_code=422, detail=f"resume_source must be one of {', '.join(RESUME_SOURCES)}")
    if department_id is not None:
        department = session.get(Department, department_id)
        if department is None or department.school_id != school_id:
            raise HTTPException(status_code=422, detail="department does not belong to that school")

    try:
        digest, path = save_resume(resume.filename or "", await resume.read())
    except RejectedUpload as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    candidate = Candidate()
    application = Application(
        candidate=candidate,
        school_id=school_id,
        department_id=department_id,
        applied_designation=applied_designation,
        category=category,
        differently_abled=differently_abled,
        study_leave_taken=study_leave_taken,
        status=states.RECEIVED,
        resume_source=resume_source,
        # Only the last path component, and no longer than the column: a
        # browser may send a full path, and PostgreSQL rejects an over-long value.
        resume_filename=_safe_filename(resume.filename, path.suffix),
        resume_path=str(path),
        resume_sha256=digest,
    )
    session.add(application)
    states.record_initial(session, application, actor="system", note=f"source={resume_source}")
    session.flush()
    logger.info("application_created id=%s school=%s", application.application_id, school_id)
    return _application_summary(application)


def _safe_filename(name: str | None, suffix: str) -> str:
    base = Path((name or "").replace("\\", "/")).name or f"resume{suffix}"
    return base if len(base) <= 255 else base[: 255 - len(suffix)] + suffix


def _get_application(session: Session, application_id: int, lock: bool = False) -> Application:
    # `lock` takes a row lock (PostgreSQL), so two requests cannot both find
    # the application RECEIVED and run the Reader on it twice.
    application = session.get(Application, application_id, with_for_update=lock)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return application


@app.post("/applications/{application_id}/read")
def run_reader(
    application_id: int,
    session: Session = Depends(get_session),
    provider: LLMProvider = Depends(get_provider),
    cache: ResultCache = Depends(get_cache),
) -> dict[str, Any]:
    """Run Stage 2 on a RECEIVED application."""
    application = _get_application(session, application_id, lock=True)
    if application.status != states.RECEIVED:
        raise HTTPException(status_code=409, detail=f"application is {application.status}, not RECEIVED")
    read_application(session, application, provider, cache)
    return _application_summary(application)


@app.get("/applications/{application_id}")
def get_application(application_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    a = _get_application(session, application_id)
    out = _application_summary(a)
    e = a.extracted
    if e is not None:
        personal = session.get(CandidatePersonalDetails, a.candidate_id)
        out["candidate_name"] = personal.full_name if personal else None
        out["extracted"] = {
            "highest_degree": e.highest_degree,
            "marks_pct": e.marks_pct,
            "cgpa": e.cgpa,
            "phd_status": e.phd_status,
            "phd_regulation": e.phd_regulation,
            "net_set_status": e.net_set_status,
            "set_state": e.set_state,
            "teaching_years": e.teaching_years,
            "publications_count": e.publications_count,
            "needs_review": e.needs_review,
            "review_reasons": e.review_reasons,
            "model_used": e.model_used,
        }
        counts = {}
        for label, table in (
            ("qualifications", CandidateQualification),
            ("experience", CandidateExperience),
            ("publications", CandidatePublication),
        ):
            counts[label] = len(session.scalars(select(table).where(table.application_id == application_id)).all())
        out["record_counts"] = counts
    return out


@app.get("/applications/{application_id}/transitions")
def get_transitions(application_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    a = _get_application(session, application_id)
    return [
        {
            "from_state": t.from_state,
            "to_state": t.to_state,
            "actor": t.actor,
            "note": t.note,
            "occurred_at": t.occurred_at.isoformat(),
        }
        for t in a.transitions
    ]
