"""The application: the JSON API here, and the web pages in backend/web.py.

The JSON API creates an application with its form fields and resume, runs
the Reader on it, and reads back its record, state and audit trail. The
pages HR and applicants use are in backend/web.py.

Run locally:  uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import config
from app.result_cache import ResultCache
from backend import states
from backend.db import get_session
from backend.deps import get_cache, get_provider  # noqa: F401  (re-exported for tests)
from backend.models import (
    Application,
    Candidate,
    CandidatePersonalDetails,
    CandidatePublication,
    CandidateQualification,
    CandidateExperience,
    Department,
    RelaxationRule,
    RubricRule,
    RuleVersion,
    School,
    ScoreRule,
)
from backend.reader_service import read_application
from backend.storage import RejectedUpload, save_resume
from backend.web import current_user, install as install_access_handlers, router as web_router
from llm.interface import LLMProvider

logger = logging.getLogger("recruitai.api")

DESIGNATIONS = ("ASSISTANT_PROFESSOR", "ASSOCIATE_PROFESSOR", "PROFESSOR", "SENIOR_PROFESSOR")
CATEGORIES = ("General", "SC", "ST", "OBC-NCL", "EWS", "PwD")
RESUME_SOURCES = ("WEB_FORM", "EMAIL", "GOOGLE_FORM", "MANUAL_UPLOAD")

# The interactive API pages (/docs, /redoc) and the schema are switched off: they would list the API to anyone, signed in or not.
app = FastAPI(title="RecruitAI", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
app.include_router(web_router)
install_access_handlers(app)


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


@app.get("/rules")
def get_rules(session: Session = Depends(get_session)) -> dict[str, Any]:
    """The statutory thresholds and relaxations as loaded, each with its citation."""
    versions = {v.rule_version_id: v for v in session.scalars(select(RuleVersion))}
    return {
        "instruments": [
            {
                "code": v.code, "instrument": v.instrument_name, "gazette_ref": v.gazette_ref,
                "effective_from": v.effective_from.isoformat() if v.effective_from else None, "note": v.note,
            }
            for v in sorted(versions.values(), key=lambda v: v.rule_version_id)
        ],
        "thresholds": [
            {
                "designation": r.designation, "discipline_group": r.discipline_group, "criteria": r.criteria,
                "requires_phd": r.requires_phd, "net_set_required": r.net_set_required,
                "min_marks_pct": r.min_marks_pct, "min_years": r.min_years, "min_publications": r.min_publications,
                "research_score_threshold": r.research_score_threshold, "min_doctoral_guided": r.min_doctoral_guided,
                "authority_clause": r.authority_clause, "authority_page": r.authority_page,
                "rule_version": versions[r.rule_version_id].code,
            }
            for r in session.scalars(select(RubricRule).order_by(RubricRule.rubric_rule_id))
        ],
        "relaxations": [
            {
                "code": r.code, "relaxation_pct": r.relaxation_pct, "levels": r.applies_to_levels,
                "categories": r.applies_to_categories, "condition": r.condition,
                "authority_clause": r.authority_clause, "authority_page": r.authority_page,
            }
            for r in session.scalars(select(RelaxationRule).order_by(RelaxationRule.relaxation_rule_id))
        ],
    }


@app.get("/rules/score-tables/{table_code}")
def get_score_table(table_code: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """One UGC Appendix II table (TABLE_2, TABLE_3A or TABLE_3B), row by row."""
    rows = session.scalars(
        select(ScoreRule).where(ScoreRule.table_code == table_code.upper()).order_by(ScoreRule.sort_order)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="no such score table")
    return [
        {
            "row_code": r.row_code, "section": r.section, "description": r.description, "kind": r.kind,
            "faculty_group": r.faculty_group, "points": r.points, "unit": r.unit, "band_min": r.band_min,
            "band_max": r.band_max, "max_points": r.max_points, "categories": r.applies_to_categories,
            "authority_page": r.authority_page,
        }
        for r in rows
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


@app.post("/applications", status_code=201, dependencies=[Depends(current_user)])
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


@app.post("/applications/{application_id}/read", dependencies=[Depends(current_user)])
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


@app.get("/applications/{application_id}", dependencies=[Depends(current_user)])
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


@app.get("/applications/{application_id}/transitions", dependencies=[Depends(current_user)])
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
