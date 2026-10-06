"""Database schema.

Follows Section 10.2 and Section 17 of the design document and the data-model
workbook. Where this file goes beyond them it says so:

* Reference data (regulators, schools) uses the document's own codes as keys
  ("UGC", "SCH-008"); everything transactional uses integer keys.
* A school has one primary regulator and an optional overlay, which is how
  "UGC + AICTE" is stored without a composite text value.
* Dates the resume states only partly ("2014", "2021-05") are kept as stated,
  in text columns, never padded to a full date. Phase 5 does the arithmetic.
* Four detail tables (events, achievements, guidance, memberships) have no
  counterpart in the workbook; the Reader extracts them, so they are stored.
* Rule and evaluation tables are created here and filled in Phases 2 and 5.

String columns with a fixed vocabulary are plain text checked in Python
(backend.states, llm.interface), not database enums, so adding a value is a
code change and a data change, never a schema migration.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- reference data ----------------------------------------------------------


class Regulator(Base):
    __tablename__ = "regulators"

    regulator_id: Mapped[str] = mapped_column(String(20), primary_key=True)  # "UGC", "BCI"
    name: Mapped[str] = mapped_column(String(120))
    norms_document_ref: Mapped[str | None] = mapped_column(String(300))
    # False means the engine flags the post for manual review and applies no
    # thresholds at all (cl. 1.1 proviso 1, p. 57).
    is_implemented: Mapped[bool] = mapped_column(Boolean, default=False)


class School(Base):
    __tablename__ = "schools"

    school_id: Mapped[str] = mapped_column(String(10), primary_key=True)  # "SCH-008"
    name: Mapped[str] = mapped_column(String(200), unique=True)
    faculty: Mapped[str] = mapped_column(String(120))
    regulator_id: Mapped[str] = mapped_column(ForeignKey("regulators.regulator_id"))
    overlay_regulator_id: Mapped[str | None] = mapped_column(ForeignKey("regulators.regulator_id"))
    # Configuration, not a constant: the count of hiring schools is whatever
    # this flag says it is (Section 7.4 design directive).
    is_hiring_unit: Mapped[bool] = mapped_column(Boolean, default=True)
    has_departments: Mapped[bool | None] = mapped_column(Boolean)  # None = to be confirmed with HR
    login_required: Mapped[bool] = mapped_column(Boolean, default=True)
    # Open point recorded against the school in Section 7.3 ("Confirm").
    confirmation_note: Mapped[str | None] = mapped_column(String(200))

    regulator: Mapped[Regulator] = relationship(foreign_keys=[regulator_id])
    overlay_regulator: Mapped[Regulator | None] = relationship(foreign_keys=[overlay_regulator_id])
    departments: Mapped[list["Department"]] = relationship(back_populates="school")


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("school_id", "name"),)

    department_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[str] = mapped_column(ForeignKey("schools.school_id"))
    name: Mapped[str] = mapped_column(String(200))

    school: Mapped[School] = relationship(back_populates="departments")


class InstitutionMaster(Base):
    __tablename__ = "institutions_master"

    institution_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_name: Mapped[str] = mapped_column(String(250), unique=True)
    tier: Mapped[str] = mapped_column(String(20))  # PREMIER / NATIONAL / STATE / OTHER
    category: Mapped[str] = mapped_column(String(40))
    # Other ways resumes write the same name ("IIT Bombay", "I.I.T. Mumbai").
    aliases: Mapped[list | None] = mapped_column(JSON)


# --- openings ----------------------------------------------------------------


class RecruitmentDrive(Base):
    __tablename__ = "recruitment_drives"

    drive_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[str] = mapped_column(ForeignKey("schools.school_id"))
    designation: Mapped[str] = mapped_column(String(40))
    advertisement_ref: Mapped[str | None] = mapped_column(String(80))
    advertisement_date: Mapped[date] = mapped_column(Date)
    closing_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")


class JobOpening(Base):
    __tablename__ = "job_openings"

    opening_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drive_id: Mapped[int | None] = mapped_column(ForeignKey("recruitment_drives.drive_id"))
    school_id: Mapped[str] = mapped_column(ForeignKey("schools.school_id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.department_id"))
    designation: Mapped[str] = mapped_column(String(40))
    title: Mapped[str | None] = mapped_column(String(200))
    # Which rule set applies: "GENERAL" (UGC cl. 4.1) or an AICTE discipline
    # group. Chosen by HR when the opening is created, never inferred from a
    # resume: it decides which regulation the candidate is judged under.
    discipline_group: Mapped[str] = mapped_column(String(40), default="GENERAL", server_default="GENERAL")
    closing_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    school: Mapped[School] = relationship()
    department: Mapped[Department | None] = relationship()
    drive: Mapped[RecruitmentDrive | None] = relationship()

    @property
    def reference(self) -> str:
        """The code a candidate or an email quotes to name this opening."""
        return f"OPN-{self.opening_id:05d}"


# --- candidates and applications --------------------------------------------


class Candidate(Base):
    """One row per unique applicant."""

    __tablename__ = "candidates"

    candidate_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Unique when known. A manual upload may arrive before the email is read
    # off the resume, so it can be empty at first.
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    applications: Mapped[list["Application"]] = relationship(
        back_populates="candidate", foreign_keys="Application.candidate_id"
    )


class Application(Base):
    """One row per submitted application.

    School, department, designation, category and study leave are form
    inputs. They are never inferred from the resume (Section 8).
    """

    __tablename__ = "applications"

    application_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id"))
    opening_id: Mapped[int | None] = mapped_column(ForeignKey("job_openings.opening_id"))
    school_id: Mapped[str] = mapped_column(ForeignKey("schools.school_id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.department_id"))
    applied_designation: Mapped[str] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(20))  # drives the cl. 3.4 relaxation
    differently_abled: Mapped[bool | None] = mapped_column(Boolean)
    study_leave_taken: Mapped[bool | None] = mapped_column(Boolean)  # asked on the form (Section 9.5)
    # What the applicant typed on the form. Kept separately from what the
    # Reader later extracts, so the two can be compared and neither overwrites
    # the other. Empty for an HR upload, where there was no form.
    applicant_name: Mapped[str | None] = mapped_column(String(200))
    applicant_email: Mapped[str | None] = mapped_column(String(254), index=True)
    applicant_phone: Mapped[str | None] = mapped_column(String(40))
    applicant_state: Mapped[str | None] = mapped_column(String(60))
    # Set when the email read from the resume already belongs to another
    # candidate. A person decides whether they are the same; nothing is merged
    # automatically.
    possible_duplicate_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.candidate_id", name="fk_applications_possible_duplicate")
    )
    status: Mapped[str] = mapped_column(String(30), default="RECEIVED", index=True)
    resume_source: Mapped[str] = mapped_column(String(20), default="MANUAL_UPLOAD")
    resume_filename: Mapped[str] = mapped_column(String(255))
    resume_path: Mapped[str] = mapped_column(String(500))
    resume_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    candidate: Mapped[Candidate] = relationship(back_populates="applications", foreign_keys=[candidate_id])
    opening: Mapped["JobOpening | None"] = relationship()
    school: Mapped[School] = relationship()
    @property
    def reference(self) -> str:
        """The number shown to the applicant on the confirmation page."""
        return f"APP-{self.application_id:06d}"

    transitions: Mapped[list["StateTransition"]] = relationship(
        back_populates="application", order_by="StateTransition.transition_id"
    )
    extracted: Mapped["ExtractedData | None"] = relationship(back_populates="application", uselist=False)


class Job(Base):
    """Background work waiting to be done. One kind so far: read an application.

    A table, not a message broker: the queue survives a restart, can be
    inspected with a query, and needs nothing installed beyond the database.
    """

    __tablename__ = "jobs"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.application_id"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="PENDING", index=True)  # PENDING / RUNNING / DONE / FAILED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    note: Mapped[str | None] = mapped_column(String(300))  # reason codes only, never personal data
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StateTransition(Base):
    """The audit trail of Section 9.3: every change of state, who made it, and when."""

    __tablename__ = "state_transitions"

    transition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.application_id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(30))  # None for the first record
    to_state: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(80))  # "agent:reader", "user:<id>", "system"
    note: Mapped[str | None] = mapped_column(String(500))  # never personal data
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    application: Mapped[Application] = relationship(back_populates="transitions")


class ReviewEdit(Base):
    """What a person did to one field at Gate 1: the audit trail of extraction review.

    Holds the value before and after, so it is candidate data and lives here,
    never in a log line or in `state_transitions.note`.
    """

    __tablename__ = "review_edits"

    edit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.application_id"), index=True)
    field: Mapped[str] = mapped_column(String(60))
    # CONFIRMED (kept as read) / CORRECTED / ENTERED (was empty) / LEFT_EMPTY
    # (not available from the resume) / SAME_PERSON / DIFFERENT_PERSON
    action: Mapped[str] = mapped_column(String(20))
    old_value: Mapped[str | None] = mapped_column(String(300))
    new_value: Mapped[str | None] = mapped_column(String(300))
    actor: Mapped[str] = mapped_column(String(80))
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExtractedData(Base):
    """The Reader's output for one application, retained for audit (Section 10.2)."""

    __tablename__ = "extracted_data"

    application_id: Mapped[int] = mapped_column(ForeignKey("applications.application_id"), primary_key=True)
    highest_degree: Mapped[str | None] = mapped_column(String(20))
    marks_pct: Mapped[float | None] = mapped_column(Float)
    cgpa: Mapped[float | None] = mapped_column(Float)
    has_phd: Mapped[bool | None] = mapped_column(Boolean)
    phd_status: Mapped[str | None] = mapped_column(String(20))
    phd_award_date: Mapped[date | None] = mapped_column(Date)
    phd_award_date_precision: Mapped[str | None] = mapped_column(String(10))
    phd_regulation: Mapped[str | None] = mapped_column(String(10))
    masters_award_date: Mapped[date | None] = mapped_column(Date)
    masters_award_date_precision: Mapped[str | None] = mapped_column(String(10))
    net_set_status: Mapped[str | None] = mapped_column(String(10))
    set_state: Mapped[str | None] = mapped_column(String(60))
    study_leave_taken: Mapped[bool | None] = mapped_column(Boolean)
    teaching_years: Mapped[float | None] = mapped_column(Float)
    publications_count: Mapped[int | None] = mapped_column(Integer)
    publications_in_progress_count: Mapped[int | None] = mapped_column(Integer)
    # {field: confidence} and {field: verbatim quote}, as the Reader produced them.
    confidence: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    model_used: Mapped[str | None] = mapped_column(String(60))
    lighter_model_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_llm_output: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    application: Mapped[Application] = relationship(back_populates="extracted")


# --- candidate record (Section 17) ------------------------------------------


class CandidateProfile(Base):
    __tablename__ = "candidate_profile"

    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id"), primary_key=True)
    highest_qualification_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_qualifications.qualification_id", name="fk_profile_highest_qual")
    )
    resume_source: Mapped[str] = mapped_column(String(20))
    raw_resume_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CandidatePersonalDetails(Base):
    __tablename__ = "candidate_personal_details"

    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id"), primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(20))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    current_address: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str | None] = mapped_column(String(60))
    differently_abled_flag: Mapped[bool | None] = mapped_column(Boolean)


class _CandidateItem:
    """Columns shared by every per-candidate list table."""

    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id"), index=True)
    # Which application's resume this row was read from, so a re-read replaces
    # exactly its own rows.
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.application_id"), index=True)
    # Whether the item's key text was found in the resume (llm.postprocess).
    found_in_resume: Mapped[bool] = mapped_column(Boolean, default=True)


class CandidateQualification(_CandidateItem, Base):
    __tablename__ = "candidate_qualifications"

    qualification_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    degree_level: Mapped[str] = mapped_column(String(10))  # UG / PG / PhD / Post-Doc
    degree: Mapped[str | None] = mapped_column(String(200))  # as written on the resume
    discipline: Mapped[str | None] = mapped_column(String(200))  # course / specialisation, as written
    # The same course placed on the workbook's Discipline list, when its
    # wording names one plainly (backend.lists.listed_discipline). Else empty.
    discipline_listed: Mapped[str | None] = mapped_column(String(60))
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions_master.institution_id"))
    college_name: Mapped[str | None] = mapped_column(String(250))
    university_name: Mapped[str | None] = mapped_column(String(250))
    year_of_completion: Mapped[int | None] = mapped_column(Integer)
    completion_stated: Mapped[str | None] = mapped_column(String(10))  # "2014", "2021-05", "2014-05-20"
    marks_pct: Mapped[float | None] = mapped_column(Float)
    cgpa: Mapped[float | None] = mapped_column(Float)
    division: Mapped[str | None] = mapped_column(String(80))
    is_highest: Mapped[bool] = mapped_column(Boolean, default=False)
    phd_status: Mapped[str] = mapped_column(String(20), default="NOT_APPLICABLE")
    phd_regulation_year: Mapped[str | None] = mapped_column(String(10))
    thesis_title: Mapped[str | None] = mapped_column(String(500))
    guide: Mapped[str | None] = mapped_column(String(200))
    registration_stated: Mapped[str | None] = mapped_column(String(10))


class CandidateExperience(_CandidateItem, Base):
    __tablename__ = "candidate_experience"

    experience_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employer_name: Mapped[str | None] = mapped_column(String(250))
    designation_held: Mapped[str | None] = mapped_column(String(200))
    qualification_level_at_time: Mapped[str | None] = mapped_column(String(10))
    discipline: Mapped[str | None] = mapped_column(String(200))
    start_stated: Mapped[str | None] = mapped_column(String(10))
    end_stated: Mapped[str | None] = mapped_column(String(10))  # None when current or unknown
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_stated: Mapped[str | None] = mapped_column(String(80))
    experience_type: Mapped[str] = mapped_column(String(20))
    # The cl. 3.11 flags (Section 9.5). Not inferred. `concurrent_with_study`
    # is set only when the resume says so in words; otherwise both stay empty
    # until the form, a reviewer or the engine supplies them.
    concurrent_with_study: Mapped[bool | None] = mapped_column(Boolean)
    study_leave_taken: Mapped[bool | None] = mapped_column(Boolean)


class CandidateResearchProfile(Base):
    __tablename__ = "candidate_research_profile"

    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id"), primary_key=True)
    scopus_author_id: Mapped[str | None] = mapped_column(String(40))
    orcid_id: Mapped[str | None] = mapped_column(String(40))
    google_scholar_id: Mapped[str | None] = mapped_column(String(80))
    total_citations: Mapped[int | None] = mapped_column(Integer)
    h_index: Mapped[int | None] = mapped_column(Integer)
    i10_index: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CandidatePublication(_CandidateItem, Base):
    __tablename__ = "candidate_publications"

    publication_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(600))
    publication_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="PUBLISHED")
    indexing: Mapped[str | None] = mapped_column(String(80))
    venue_name: Mapped[str | None] = mapped_column(String(400))
    impact_factor: Mapped[float | None] = mapped_column(Float)
    citation_count: Mapped[int | None] = mapped_column(Integer)
    year: Mapped[int | None] = mapped_column(Integer)
    is_first_author: Mapped[bool | None] = mapped_column(Boolean)
    author_count: Mapped[int | None] = mapped_column(Integer)


class CandidateSubjectTaught(_CandidateItem, Base):
    __tablename__ = "candidate_subjects_taught"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_name: Mapped[str] = mapped_column(String(250))
    course_level: Mapped[str | None] = mapped_column(String(10))
    years_taught: Mapped[float | None] = mapped_column(Float)


class CandidateSkill(_CandidateItem, Base):
    __tablename__ = "candidate_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(200))
    proficiency: Mapped[str | None] = mapped_column(String(20))


class CandidateHobby(_CandidateItem, Base):
    __tablename__ = "candidate_hobbies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hobby: Mapped[str] = mapped_column(String(200))


class CandidateHighlight(Base):
    __tablename__ = "candidate_highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id"), index=True)
    highlight_type: Mapped[str] = mapped_column(String(40))
    source_reference: Mapped[str | None] = mapped_column(String(80))
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(String(400))


# Beyond the workbook: lists the Reader extracts that it has no sheet for.


class CandidateEvent(_CandidateItem, Base):
    __tablename__ = "candidate_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(20))
    organiser: Mapped[str | None] = mapped_column(String(300))
    duration_stated: Mapped[str | None] = mapped_column(String(80))
    year: Mapped[int | None] = mapped_column(Integer)
    level: Mapped[str | None] = mapped_column(String(20))  # INTERNATIONAL / NATIONAL / STATE / UNIVERSITY


class CandidateAchievement(_CandidateItem, Base):
    __tablename__ = "candidate_achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    details: Mapped[str | None] = mapped_column(String(500))
    year: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(60))
    level: Mapped[str | None] = mapped_column(String(20))
    amount_stated: Mapped[str | None] = mapped_column(String(80))  # "Rs. 12.5 Lakhs", as written
    amount_inr: Mapped[float | None] = mapped_column(Float)  # the same in rupees, when it is unambiguous


class CandidateGuidance(_CandidateItem, Base):
    __tablename__ = "candidate_guidance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(String(500))
    student_count: Mapped[int | None] = mapped_column(Integer)


class CandidateMembership(_CandidateItem, Base):
    __tablename__ = "candidate_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    membership: Mapped[str] = mapped_column(String(300))


# --- rules and evaluation (filled in Phases 2 and 5) ------------------------


class RuleVersion(Base):
    """One notified instrument. A new notification is a new row, not a rewrite."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("code", name="uq_rule_versions_code"),)

    rule_version_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))  # "UGC-2018-AMD2-2023"
    regulator_id: Mapped[str] = mapped_column(ForeignKey("regulators.regulator_id"))
    instrument_name: Mapped[str] = mapped_column(String(500))
    notification_no: Mapped[str | None] = mapped_column(String(120))
    gazette_ref: Mapped[str | None] = mapped_column(String(200))
    notified_date: Mapped[date | None] = mapped_column(Date)
    effective_from: Mapped[date | None] = mapped_column(Date)
    superseded_on: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300))
    # Where the text was read from, and the hash of that exact file, so a
    # transcription can be checked against the same document later.
    source_url: Mapped[str | None] = mapped_column(String(300))
    source_sha256: Mapped[str | None] = mapped_column(String(64))


class RubricRule(Base):
    """Versioned thresholds for one designation, each carrying its own citation."""

    __tablename__ = "rubric_rules"
    __table_args__ = (
        UniqueConstraint("rule_version_id", "designation", "discipline_group", name="uq_rubric_rules_version_designation_group"),
    )

    rubric_rule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.rule_version_id"))
    designation: Mapped[str] = mapped_column(String(40))
    # UGC cl. 4.1 is one rule for a list of disciplines ("GENERAL"). AICTE sets
    # a different rule per discipline: ENGINEERING_TECHNOLOGY, MANAGEMENT, MCA...
    discipline_group: Mapped[str] = mapped_column(String(40), default="GENERAL")
    min_years: Mapped[float | None] = mapped_column(Float)
    min_publications: Mapped[int | None] = mapped_column(Integer)
    requires_phd: Mapped[bool] = mapped_column(Boolean, default=False)
    min_marks_pct: Mapped[float | None] = mapped_column(Float)
    research_score_threshold: Mapped[float | None] = mapped_column(Float)
    net_set_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # Professor: evidence of one doctoral candidate guided; Senior Professor: two.
    min_doctoral_guided: Mapped[int | None] = mapped_column(Integer)
    authority_clause: Mapped[str] = mapped_column(String(160))
    authority_page: Mapped[str] = mapped_column(String(60))
    # Requirements that do not fit the common columns: which degree must be
    # First Class, years after the Ph.D., alternative routes. Structured so
    # the engine can read them; the gazette wording is in `notes`.
    criteria: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)


class RelaxationRule(Base):
    """A relaxation of the marks threshold (cl. 3.4, cl. 3.5), as data."""

    __tablename__ = "relaxation_rules"
    __table_args__ = (UniqueConstraint("rule_version_id", "code"),)

    relaxation_rule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.rule_version_id"))
    code: Mapped[str] = mapped_column(String(40))
    relaxation_pct: Mapped[float] = mapped_column(Float)
    applies_to_levels: Mapped[list] = mapped_column(JSON)  # ["UG", "PG"]
    applies_to_categories: Mapped[list | None] = mapped_column(JSON)  # None = not category-based
    condition: Mapped[dict | None] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text)
    authority_clause: Mapped[str] = mapped_column(String(160))
    authority_page: Mapped[str] = mapped_column(String(60))


class ScoreRule(Base):
    """One row of a UGC Appendix II score table, with the page it was read from.

    `kind` says how to read the row: POINTS (a fixed or per-unit award), BAND
    (an award for a value in [band_min, band_max)), MULTIPLIER (a share of
    another row's points), CAP (an upper limit) or CONSTRAINT (a condition on
    the score as a whole). See backend/rules_data.py.
    """

    __tablename__ = "score_rules"
    __table_args__ = (UniqueConstraint("table_code", "row_code"),)

    score_rule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.rule_version_id"))
    table_code: Mapped[str] = mapped_column(String(20), index=True)  # TABLE_2 / TABLE_3A / TABLE_3B
    row_code: Mapped[str] = mapped_column(String(40))
    section: Mapped[str] = mapped_column(String(20))  # the S.N. or note it sits under
    description: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(12))
    faculty_group: Mapped[str] = mapped_column(String(10), default="ALL")
    points: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))
    band_min: Mapped[float | None] = mapped_column(Float)
    band_max: Mapped[float | None] = mapped_column(Float)
    max_points: Mapped[float | None] = mapped_column(Float)
    applies_to_categories: Mapped[list | None] = mapped_column(JSON)
    authority_page: Mapped[str] = mapped_column(String(40))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class EvaluationResult(Base):
    """The deterministic engine's output, traceable to a clause and gazette page."""

    __tablename__ = "evaluation_results"

    evaluation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.application_id"), index=True)
    research_score: Mapped[float | None] = mapped_column(Float)
    adjusted_experience_years: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(30))
    eligible_designation: Mapped[str | None] = mapped_column(String(40))
    was_recategorised: Mapped[bool] = mapped_column(Boolean, default=False)
    failing_clause: Mapped[str | None] = mapped_column(String(300))
    failing_clause_page: Mapped[str | None] = mapped_column(String(40))
    rule_version_id: Mapped[int | None] = mapped_column(ForeignKey("rule_versions.rule_version_id"))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
