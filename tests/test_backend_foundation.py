"""Phase 1: schema, seed data, state machine, the Reader as a workflow step, and the API.

Runs on an in-memory SQLite database with the fake provider: no network, no
PostgreSQL needed. Synthetic resumes only.
"""

from __future__ import annotations

import io
import os

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend import states
from backend.db import Base, get_session, make_engine
from backend.main import app, get_cache, get_provider
from backend.models import (
    Application,
    Candidate,
    CandidateExperience,
    CandidatePersonalDetails,
    CandidateProfile,
    CandidatePublication,
    CandidateQualification,
    CandidateSkill,
    ExtractedData,
    Regulator,
    School,
    StateTransition,
)
from backend.reader_service import read_application
from backend.seed import SCHOOLS, seed_reference_data
from backend.storage import RejectedUpload, save_resume
from llm.interface import (
    EducationEntry,
    ExperienceEntry,
    ExtractionFailure,
    PublicationEntry,
)
from llm.providers.fake_provider import CANNED_RESULTS, FakeProvider

RESUME_LINES = [
    "Sample Exampleton",
    "M.Sc. Physics, Synthetic University, 2015 (68.4%)",
    "UGC-NET (Physical Sciences), December 2016",
    "Assistant Professor, Placeholder College, 2018-2024",
    "Publications: four peer-reviewed papers listed",
    "Email: sample.exampleton@example.org",
]


def _docx() -> bytes:
    d = Document()
    for line in RESUME_LINES:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _scanned_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(50, 50, 300, 200), fill=(0.8, 0.8, 0.8))
    data = doc.tobytes()
    doc.close()
    return data


def _clean_result():
    """A result with nothing to review, plus detail lists."""
    r = CANNED_RESULTS[0].model_copy(deep=True)
    r.education = [
        EducationEntry(level="UG", degree="B.Sc.", course="Physics", completion="2013", marks_pct=71.0),
        EducationEntry(level="PG", degree="M.Sc.", course="Physics", university="Synthetic University",
                       completion="2015-06", marks_pct=68.4),
    ]
    r.experience = [
        ExperienceEntry(designation="Assistant Professor", institution="Placeholder College",
                        kind="TEACHING", start="2018", end="PRESENT"),
    ]
    r.publications = [PublicationEntry(title="Paper A", kind="JOURNAL", year="2021")]
    r.skills = ["Python"]
    r.email = "Sample.Exampleton@Example.org"
    return r


@pytest.fixture
def engine():
    """In-memory SQLite by default. Set TEST_DATABASE_URL to run the same
    tests against PostgreSQL (a scratch database: every table is dropped)."""
    url = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_reference_data(s)
        s.commit()
    yield engine
    engine.dispose()
    if not url.startswith("sqlite"):
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _application(session, tmp_path, data=None, filename="cv.docx", **fields) -> Application:
    digest, path = save_resume(filename, data or _docx(), tmp_path)
    a = Application(
        candidate=Candidate(), school_id="SCH-008", applied_designation="ASSISTANT_PROFESSOR",
        resume_filename=filename, resume_path=str(path), resume_sha256=digest, **fields,
    )
    session.add(a)
    states.record_initial(session, a, actor="system")
    session.flush()
    return a


# --- schema and seed ---------------------------------------------------------


def test_seed_loads_the_schools_and_regulators_from_the_design_document(session):
    assert len(session.scalars(select(School)).all()) == len(SCHOOLS) == 21
    assert {r.regulator_id for r in session.scalars(select(Regulator))} == {"UGC", "AICTE", "BCI", "COA", "DGS", "NCTE"}


def test_only_ugc_and_aicte_are_implemented(session):
    implemented = {r.regulator_id for r in session.scalars(select(Regulator)) if r.is_implemented}
    assert implemented == {"UGC", "AICTE"}


def test_law_architecture_and_maritime_resolve_to_unimplemented_regulators(session):
    """cl. 1.1 proviso 1: these posts are flagged for manual review, not judged on UGC thresholds."""
    for school_id, regulator in (("SCH-007", "BCI"), ("SCH-006", "COA"), ("SCH-019", "DGS")):
        school = session.get(School, school_id)
        assert school.regulator_id == regulator and not school.regulator.is_implemented


def test_technical_schools_carry_aicte_as_an_overlay_on_ugc(session):
    computing = session.get(School, "SCH-008")
    assert computing.regulator_id == "UGC" and computing.overlay_regulator_id == "AICTE"
    assert session.get(School, "SCH-013").overlay_regulator_id is None


def test_seeding_twice_changes_nothing(session):
    seed_reference_data(session)
    session.commit()
    assert len(session.scalars(select(School)).all()) == 21


def test_a_school_cannot_point_at_a_regulator_that_does_not_exist(session):
    session.add(School(school_id="SCH-999", name="Nowhere", faculty="None", regulator_id="XYZ"))
    with pytest.raises(IntegrityError):
        session.flush()


# --- state machine -----------------------------------------------------------


def test_every_state_in_the_document_is_defined_and_reachable():
    reachable = set(states.INITIAL_STATES)
    for targets in states.ALLOWED.values():
        reachable |= targets
    assert reachable == states.ALL_STATES


def test_a_legal_transition_updates_the_state_and_writes_the_audit_row(session, tmp_path):
    a = _application(session, tmp_path)
    states.transition(session, a, states.PARSING, "agent:reader")
    session.flush()
    assert a.status == states.PARSING
    assert [(t.from_state, t.to_state, t.actor) for t in a.transitions] == [
        (None, "RECEIVED", "system"),
        ("RECEIVED", "PARSING", "agent:reader"),
    ]


@pytest.mark.parametrize("target", [states.SHORTLISTED, states.HR_APPROVED, states.CONTACTED, states.EXTRACTED])
def test_an_application_cannot_skip_stages(session, tmp_path, target):
    a = _application(session, tmp_path)
    with pytest.raises(states.IllegalTransition):
        states.transition(session, a, target, "user:1")
    assert a.status == states.RECEIVED
    assert len(a.transitions) == 1  # nothing was recorded for the refused move


def test_nothing_reaches_contacted_without_hr_approval():
    """Gate 2 and Gate 3 (Section 9.7): the only way into CONTACTED is from HR_APPROVED."""
    into_contacted = {s for s, targets in states.ALLOWED.items() if states.CONTACTED in targets}
    assert into_contacted == {states.HR_APPROVED}
    into_approved = {s for s, targets in states.ALLOWED.items() if states.HR_APPROVED in targets}
    assert into_approved == {states.SHORTLISTED, states.RE_CATEGORISED, states.NOT_ELIGIBLE, states.MANUAL_REVIEW}


def test_terminal_states_have_no_way_out():
    assert states.ALLOWED[states.WITHDRAWN] == frozenset()
    assert states.ALLOWED[states.INTERVIEW_SCHEDULED] == frozenset()


# --- the Reader as a workflow step ------------------------------------------


def test_a_clean_extraction_ends_extracted_and_fills_the_tables(session, tmp_path):
    a = _application(session, tmp_path, category="SC")
    state = read_application(session, a, FakeProvider(script=[_clean_result()]))
    session.flush()

    assert state == a.status == states.EXTRACTED
    assert [t.to_state for t in a.transitions] == ["RECEIVED", "PARSING", "EXTRACTED"]

    e = session.get(ExtractedData, a.application_id)
    assert e.highest_degree == "PG" and e.net_set_status == "NET" and e.needs_review is False
    assert set(e.confidence) == set(e.evidence) and "highest_degree" in e.confidence

    quals = session.scalars(select(CandidateQualification).order_by(CandidateQualification.qualification_id)).all()
    assert [(q.degree_level, q.is_highest, q.year_of_completion, q.completion_stated) for q in quals] == [
        ("UG", False, 2013, "2013"), ("PG", True, 2015, "2015-06"),
    ]
    exp = session.scalars(select(CandidateExperience)).one()
    assert exp.is_current and exp.end_stated is None and exp.start_stated == "2018"
    assert exp.concurrent_with_study is None and exp.study_leave_taken is None  # never inferred
    assert session.scalars(select(CandidatePublication)).one().year == 2021
    assert session.scalars(select(CandidateSkill)).one().skill_name == "Python"

    personal = session.get(CandidatePersonalDetails, a.candidate_id)
    assert personal.category == "SC"  # from the form, not the resume
    assert session.get(CandidateProfile, a.candidate_id).highest_qualification_id == quals[1].qualification_id


def test_the_email_read_from_the_resume_becomes_the_candidates_email(session, tmp_path):
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    session.flush()
    assert a.candidate.email == "sample.exampleton@example.org"


def test_a_second_applicant_with_the_same_email_does_not_break_the_run(session, tmp_path):
    first = _application(session, tmp_path)
    read_application(session, first, FakeProvider(script=[_clean_result()]))
    second = _application(session, tmp_path)
    read_application(session, second, FakeProvider(script=[_clean_result()]))
    session.flush()
    assert second.status == states.EXTRACTED and second.candidate.email is None  # left for a person to merge


def test_a_result_needing_review_ends_pending_review_with_reasons_and_no_values(session, tmp_path):
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[CANNED_RESULTS[1].model_copy(deep=True)]))
    session.flush()
    assert a.status == states.PENDING_REVIEW
    note = a.transitions[-1].note
    assert "set_state:required_for_set" in note
    assert "Synthetic" not in note and "Commerce" not in note  # reason codes only


def test_a_scanned_resume_ends_failed_and_the_model_is_never_called(session, tmp_path):
    provider = FakeProvider()
    a = _application(session, tmp_path, data=_scanned_pdf(), filename="scan.pdf")
    read_application(session, a, provider)
    session.flush()
    assert a.status == states.FAILED and provider.calls == 0
    assert a.transitions[-1].note.startswith("unreadable: scanned or image-only")
    assert session.get(ExtractedData, a.application_id) is None


@pytest.mark.parametrize("kind", ["api_unavailable", "quota"])
def test_an_unavailable_model_sends_the_application_back_to_wait(session, tmp_path, kind):
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[ExtractionFailure("down", kind=kind)]))
    assert a.status == states.RECEIVED  # the file is fine; it can be read later
    assert [t.to_state for t in a.transitions] == ["RECEIVED", "PARSING", "RECEIVED"]
    assert a.transitions[-1].note == f"reader_unavailable: {kind}"

    read_application(session, a, FakeProvider(script=[_clean_result()]))
    assert a.status == states.EXTRACTED


def test_a_configuration_error_is_a_failure_not_a_retry(session, tmp_path):
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[ExtractionFailure("bad key", kind="bad_config")]))
    assert a.status == states.FAILED


def test_reading_again_replaces_the_rows_instead_of_duplicating_them(session, tmp_path):
    from backend.reader_service import store_extraction

    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[_clean_result()]))
    store_extraction(session, a, _clean_result(), False, [])
    session.flush()
    assert len(session.scalars(select(CandidateQualification)).all()) == 2
    assert len(session.scalars(select(ExtractedData)).all()) == 1


# --- storage -----------------------------------------------------------------


def test_resumes_are_stored_by_content_hash_not_by_name(tmp_path):
    data = _docx()  # built once: a .docx embeds a timestamp, so two builds can differ
    digest, path = save_resume("Sample_Exampleton_resume.docx", data, tmp_path)
    assert path.name == f"{digest}.docx" and "Exampleton" not in str(path.name)
    assert save_resume("another name.docx", data, tmp_path)[1] == path  # same content, stored once


@pytest.mark.parametrize("name, data", [("cv.exe", b"x"), ("cv.pdf", b""), ("cv", b"x")])
def test_unacceptable_uploads_are_rejected(tmp_path, name, data):
    with pytest.raises(RejectedUpload):
        save_resume(name, data, tmp_path)


# --- API ---------------------------------------------------------------------


@pytest.fixture
def client(engine, tmp_path, monkeypatch):
    from backend import settings

    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "resumes")
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _session():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    provider = FakeProvider(script=[_clean_result()])
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_provider] = lambda: provider
    app.dependency_overrides[get_cache] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _post(client, **overrides):
    data = {"school_id": "SCH-008", "applied_designation": "ASSISTANT_PROFESSOR", "category": "General"}
    data.update(overrides)
    data = {k: v for k, v in data.items() if v is not None}
    return client.post(
        "/applications", data=data,
        files={"resume": ("cv.docx", _docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )


def test_health_reports_the_database(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_schools_endpoint_lists_all_21_with_their_regulators(client):
    schools = client.get("/schools").json()
    assert len(schools) == 21
    law = next(s for s in schools if s["school_id"] == "SCH-007")
    assert law["regulator"] == "BCI" and law["regulator_implemented"] is False


def test_an_application_goes_from_upload_to_extracted_through_the_api(client):
    created = _post(client)
    assert created.status_code == 201
    app_id = created.json()["application_id"]
    assert created.json()["status"] == "RECEIVED"

    read = client.post(f"/applications/{app_id}/read")
    assert read.status_code == 200 and read.json()["status"] == "EXTRACTED"

    record = client.get(f"/applications/{app_id}").json()
    assert record["candidate_name"] == "A. Synthetic Candidate"
    assert record["extracted"]["net_set_status"] == "NET"
    assert record["record_counts"] == {"qualifications": 2, "experience": 1, "publications": 1}

    trail = client.get(f"/applications/{app_id}/transitions").json()
    assert [(t["from_state"], t["to_state"], t["actor"]) for t in trail] == [
        (None, "RECEIVED", "system"),
        ("RECEIVED", "PARSING", "agent:reader"),
        ("PARSING", "EXTRACTED", "agent:reader"),
    ]


def test_the_reader_cannot_be_run_twice_on_the_same_application(client):
    app_id = _post(client).json()["application_id"]
    assert client.post(f"/applications/{app_id}/read").status_code == 200
    again = client.post(f"/applications/{app_id}/read")
    assert again.status_code == 409 and "EXTRACTED" in again.json()["detail"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"school_id": "SCH-999"},
        {"applied_designation": "DEAN"},
        {"category": "Royalty"},
        {"department_id": "12345"},
        {"resume_source": "CARRIER_PIGEON"},
    ],
)
def test_form_fields_outside_the_allowed_values_are_refused(client, overrides):
    assert _post(client, **overrides).status_code == 422


def test_an_unsupported_file_is_refused_and_creates_nothing(client, engine):
    r = client.post(
        "/applications",
        data={"school_id": "SCH-008", "applied_designation": "PROFESSOR"},
        files={"resume": ("cv.txt", b"plain text", "text/plain")},
    )
    assert r.status_code == 422
    with Session(engine) as s:
        assert s.scalars(select(Application)).all() == [] and s.scalars(select(StateTransition)).all() == []


def test_unknown_application_is_a_404(client):
    assert client.get("/applications/4242").status_code == 404
    assert client.post("/applications/4242/read").status_code == 404


# --- migrations --------------------------------------------------------------


def test_migrations_build_exactly_the_schema_the_models_describe(tmp_path):
    """A model changed without a migration would leave a deployed database
    behind the code. `alembic check` fails if the two have drifted."""
    from alembic import command
    from alembic.config import Config
    from alembic.util.exc import CommandError

    cfg = Config("alembic.ini")
    cfg.cmd_opts = type("Opts", (), {"x": [f"url=sqlite:///{(tmp_path / 'm.db').as_posix()}"]})()
    command.upgrade(cfg, "head")
    try:
        command.check(cfg)
    except CommandError as exc:  # pragma: no cover - only on drift
        pytest.fail(f"models and migrations have drifted: {exc}")


# --- values longer than their columns (PostgreSQL enforces lengths; SQLite does not) ---


def test_over_long_text_from_the_model_is_cut_to_fit_not_allowed_to_fail_the_write(session, tmp_path):
    r = _clean_result()
    r.set_state.value = "S" * 300
    r.model_used = "m" * 300
    r.education[0].degree = "D" * 900
    r.experience[0].institution = "I" * 900
    r.publications[0].title = "T" * 2000
    r.skills = ["K" * 900]
    a = _application(session, tmp_path)
    read_application(session, a, FakeProvider(script=[r]))
    session.flush()
    assert a.status in (states.EXTRACTED, states.PENDING_REVIEW)
    assert len(session.get(ExtractedData, a.application_id).set_state) == 60
    assert len(session.scalars(select(CandidatePublication)).one().title) == 600


def test_an_over_long_or_path_like_filename_is_stored_safely(client, engine):
    name = "/".join(["C:", "Users", "someone", "Desktop", "x" * 400 + ".docx"]).replace("/", chr(92))
    r = client.post(
        "/applications",
        data={"school_id": "SCH-008", "applied_designation": "PROFESSOR"},
        files={"resume": (name, _docx(), "application/octet-stream")},
    )
    assert r.status_code == 201
    with Session(engine) as s:
        stored = s.scalars(select(Application)).one().resume_filename
    assert len(stored) <= 255 and stored.endswith(".docx") and "Users" not in stored


# --- rules API ---------------------------------------------------------------


def test_rules_endpoints_serve_thresholds_and_score_tables_with_citations(client, engine):
    from backend.rules_seed import seed_rules

    with Session(engine) as s:
        seed_rules(s)
        s.commit()
    rules = client.get("/rules").json()
    ap = next(t for t in rules["thresholds"] if t["designation"] == "ASSISTANT_PROFESSOR" and t["discipline_group"] == "GENERAL")
    assert ap["net_set_required"] is True and ap["requires_phd"] is False and ap["rule_version"] == "UGC-2018-AMD2-2023"
    eng = next(t for t in rules["thresholds"] if t["discipline_group"] == "ENGINEERING_TECHNOLOGY")
    assert eng["net_set_required"] is False and eng["criteria"]["first_class"] == "ANY_ONE_DEGREE"
    assert eng["rule_version"] == "AICTE-DEGREE-2019"
    assert len(rules["instruments"]) == 6 and len(rules["relaxations"]) == 2

    table = client.get("/rules/score-tables/table_3a").json()
    assert next(r for r in table if r["row_code"] == "PHD")["points"] == 30
    assert all(r["authority_page"] for r in table)
    assert client.get("/rules/score-tables/TABLE_9").status_code == 404
