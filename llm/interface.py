"""Core LLM-layer types: the provider protocol and the extraction schema.

Nothing in this module computes eligibility, compares a value to a threshold,
or ranks candidates. It defines the shape of what a model is allowed to return:
a fixed set of fields, each carrying its own confidence and a verbatim evidence
quote from the resume text.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")

NetSetStatus = Literal["NET", "SET", "SLET", "NONE"]
# Both lists are the DataModel workbook's own dropdowns (Lists!DegreeLevel and
# Lists!PhDStatus), reused verbatim so the extractor and the eventual database
# cannot drift apart on spelling or membership.
HighestDegree = Literal["UG", "PG", "PhD", "Post-Doc", "Diploma"]
PhdStatus = Literal["NOT_APPLICABLE", "PURSUING", "COMPLETED", "REGISTERED", "THESIS_SUBMITTED"]
PhdRegulation = Literal["2009", "2016"]
DatePrecision = Literal["year", "month", "full"]


class FieldWithConfidence(BaseModel, Generic[T]):
    """One extracted value, its confidence, and the text it was grounded in.

    `evidence` is a verbatim snippet from the resume. A value with no evidence
    is not trustworthy regardless of the confidence the model reported, and
    `llm.confidence` treats it that way.
    """

    value: T
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str | None = None

    @field_validator("evidence", mode="before")
    @classmethod
    def _blank_evidence_is_no_evidence(cls, v: str | None) -> str | None:
        # A model occasionally returns evidence="" instead of the schema's
        # null for "not grounded" — an empty/whitespace string is not a
        # verbatim quote, so it must be indistinguishable from no evidence at
        # all. This is enforced here (not just in llm.confidence) so every
        # provider and every caller sees the same normalized value.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def is_absent(self) -> bool:
        return self.value is None


def absent(confidence: float = 0.0) -> dict:
    """The canonical 'resume does not state this' field payload.

    Absent means confidence 0.0 and no evidence — never a plausible default.
    """
    return {"value": None, "confidence": confidence, "evidence": None}


# --- detail records ----------------------------------------------------------
#
# The scalar fields above answer "is this candidate eligible / how do they
# rank". These lists hold the fuller record a reviewer reads when comparing
# shortlisted people: each degree, each publication, each FDP. They are
# extracted as the resume states them and never feed the score or the review
# routing. Instead of a confidence and a quote per item, each item carries
# `found_in_resume`: a deterministic check (llm.postprocess) that its key
# text really appears in the resume, so an invented item is visibly marked.

EducationLevel = Literal["UG", "PG", "PhD"]
PublicationKind = Literal["JOURNAL", "CONFERENCE", "BOOK_CHAPTER", "BOOK", "OTHER"]
PublicationStatus = Literal["PUBLISHED", "ACCEPTED", "UNDER_REVIEW", "SUBMITTED", "IN_PREPARATION"]
EventKind = Literal["FDP", "STTP", "WORKSHOP", "SEMINAR", "WEBINAR", "CONFERENCE", "TRAINING", "COURSE", "OTHER"]
EventRole = Literal["ATTENDED", "ORGANISED", "RESOURCE_PERSON", "PRESENTED", "OTHER"]


class EducationEntry(BaseModel):
    level: EducationLevel
    degree: str | None = None  # as written: "Master of Engineering (M.E)"
    course: str | None = None  # specialisation: "Computer Engineering"
    college: str | None = None
    university: str | None = None
    marks_pct: float | None = None
    cgpa: float | None = None
    division: str | None = None  # "First Class with Distinction"
    completion: str | None = None  # "YYYY", "YYYY-MM" or "YYYY-MM-DD"; null if not completed/stated
    # Ph.D. only
    thesis_title: str | None = None
    guide: str | None = None
    registration: str | None = None  # same shape as `completion`
    found_in_resume: bool = True


class PublicationEntry(BaseModel):
    title: str
    kind: PublicationKind = "OTHER"
    venue: str | None = None  # journal / conference / publisher name
    year: str | None = None
    status: PublicationStatus = "PUBLISHED"
    indexing: str | None = None  # "Scopus", "SCI", "UGC CARE", as the resume says
    # The author names as the resume lists them for this paper, in order. The
    # model copies; counting them is done in Python (llm.postprocess), which
    # fills author_count. Empty when the resume gives no author list.
    authors: list[str] = Field(default_factory=list)
    author_count: int | None = None
    # True/False only when the author list is given; otherwise null.
    is_first_author: bool | None = None
    impact_factor: float | None = None  # only if the resume states one for this paper
    found_in_resume: bool = True


# How wide an award, talk or event was, when the resume says so in words
# ("International Conference on ...", "State Level Best Teacher Award").
# Table 2 of the UGC Regulations scores these levels differently.
ScopeLevel = Literal["INTERNATIONAL", "NATIONAL", "STATE", "UNIVERSITY"]
CourseLevel = Literal["UG", "PG", "PhD", "Diploma"]


class EventEntry(BaseModel):
    kind: EventKind = "OTHER"
    title: str
    role: EventRole = "ATTENDED"
    organiser: str | None = None
    duration: str | None = None  # "5 days", "One week", as stated
    year: str | None = None
    level: ScopeLevel | None = None
    found_in_resume: bool = True


ExperienceKind = Literal["TEACHING", "INDUSTRY", "RESEARCH", "OTHER"]
AchievementKind = Literal["PATENT", "AWARD", "FUNDED_PROJECT", "GRANT", "OTHER"]
GuidanceLevel = Literal["PHD", "PG", "UG", "OTHER"]


class ExperienceEntry(BaseModel):
    designation: str | None = None
    institution: str | None = None
    kind: ExperienceKind = "OTHER"
    start: str | None = None  # "YYYY", "YYYY-MM" or "YYYY-MM-DD"
    end: str | None = None  # same shape, or "PRESENT"
    duration: str | None = None  # as the resume states it: "3 years 2 months"
    # True only when the resume itself says this post was held while a degree
    # was being pursued ("Ph.D. (part-time) while working as ..."). Never
    # worked out from dates here: comparing date ranges is the engine's job.
    concurrent_with_study: bool | None = None
    found_in_resume: bool = True


class AchievementEntry(BaseModel):
    kind: AchievementKind = "OTHER"
    title: str
    details: str | None = None  # funding agency, amount, patent number, awarding body
    year: str | None = None
    status: str | None = None  # "Granted", "Published", "Ongoing", "Completed", as stated
    level: ScopeLevel | None = None
    # A funded project's amount exactly as written ("Rs. 12.5 Lakhs"). Turned
    # into rupees by llm.postprocess.parse_amount_inr, never by the model.
    amount: str | None = None
    amount_inr: float | None = None
    found_in_resume: bool = True


class GuidanceEntry(BaseModel):
    level: GuidanceLevel = "OTHER"
    description: str  # as stated: "Guided 12 M.E. dissertations"
    count: int | None = None
    found_in_resume: bool = True


class SubjectEntry(BaseModel):
    name: str
    level: CourseLevel | None = None  # only if the resume says which programme it was taught to


class ResearchProfile(BaseModel):
    """Identifiers and metrics the candidate states about their own research."""

    scopus_author_id: str | None = None
    orcid_id: str | None = None
    google_scholar_id: str | None = None
    total_citations: int | None = None
    h_index: int | None = None
    i10_index: int | None = None


# What the model is asked to return besides the scored fields. `subjects_taught`
# is no longer asked for: it is the names from `subjects`, filled in by code.
DETAIL_FIELDS: tuple[str, ...] = (
    "education", "publications", "events", "subjects", "skills",
    "experience", "achievements", "guidance", "memberships", "email", "phone",
    "research_profile", "state",
)


class ExtractionResult(BaseModel):
    """The MVP subset of `extracted_data`.

    School, department and designation-applied-for are deliberately absent:
    those are form inputs in the full system and must never be inferred from
    resume text.
    """

    candidate_name: FieldWithConfidence[str | None]
    # Diploma was missing here while Lists!DegreeLevel has had it all along --
    # an upstream gap, not just an untested path: a diploma-holder had no
    # correct value available and could only be misfiled as UG.
    highest_degree: FieldWithConfidence[HighestDegree]
    marks_pct: FieldWithConfidence[float | None]
    # Kept separate from marks_pct rather than converted into it: CGPA->%
    # conversion factors differ by university (x10 at some, x9.5 at others),
    # so any single formula would misstate a real candidate's marks. Storing
    # the grade point on its own scale loses no information and invents none.
    cgpa: FieldWithConfidence[float | None]
    has_phd: FieldWithConfidence[bool]
    # has_phd collapses several materially different states into False. On the
    # 12-resume batch that hid "thesis submitted, decision imminent" behind the
    # same value as "no doctoral activity at all" -- a real difference when
    # shortlisting. phd_status keeps the distinction; has_phd stays exactly as
    # it was and is simply COMPLETED restated as a boolean, so nothing
    # downstream that already reads has_phd changes behaviour.
    phd_status: FieldWithConfidence[PhdStatus]
    phd_award_date: FieldWithConfidence[date | None]
    phd_regulation: FieldWithConfidence[PhdRegulation | None]
    masters_award_date: FieldWithConfidence[date | None]
    net_set_status: FieldWithConfidence[NetSetStatus]
    set_state: FieldWithConfidence[str | None]
    study_leave_taken: FieldWithConfidence[bool | None]
    # Nullable, unlike the original spec's `float`: a non-nullable number
    # leaves the model no way to say "the resume never states this", so it is
    # forced to invent one — which measured as 3 of 5 errors against the
    # human answer key (0.0, 8, 8.3 all conjured from resumes that give no
    # total). Section 1.5's "never guess a value the resume doesn't state" is
    # a hard constraint, so absence has to be expressible. It stays in
    # REQUIRED_FIELDS, so a null still routes the row to review rather than
    # passing silently.
    teaching_years_raw: FieldWithConfidence[float | None]
    publications_count: FieldWithConfidence[int]
    # The titles behind publications_count. Counting is the model's weakest
    # numeric task on real CVs, which routinely re-list the same work across
    # a "Publications" list and a separate projects/research table -- each
    # mention carries its own honest quote, so evidence checks cannot catch
    # the double-count. Holding the titles lets Python dedupe them instead.
    publication_titles: FieldWithConfidence[list[str] | None]
    # Work that has not yet cleared peer review, kept separate rather than
    # dropped. Section 6.1 requires "peer-reviewed or UGC-listed" work for
    # Associate Professor / Professor, so SUBMITTED / UNDER_REVIEW / DRAFT
    # must not inflate publications_count -- but a reviewer still needs to
    # see "10 published + 3 under review" rather than a bare 10, and the
    # DataModel's Candidate_Publications.publication_status enum exists
    # precisely so downstream logic can make that call for itself.
    # ACCEPTED counts as published: it has passed peer review, which is the
    # substantive bar, even though it is not yet in print.
    publications_in_progress_count: FieldWithConfidence[int]
    publications_in_progress_titles: FieldWithConfidence[list[str] | None]
    # How much of each date the resume actually stated. A bare "2015" parses
    # to date(2015, 1, 1), which then reads in the export exactly like a
    # verified full date -- a reviewer cannot tell "year only, verified" from
    # "day-accurate, verified". Derived in llm.postprocess from the evidence
    # quote (the parsed value has already had the gaps filled in and can no
    # longer say what was really there), so it is not a model-reported field.
    phd_award_date_precision: DatePrecision | None = None
    masters_award_date_precision: DatePrecision | None = None
    raw_llm_output: str = ""
    # Which model actually produced this result, and whether it was the opt-in
    # lighter fallback rather than the normal pool. Set by the provider, never
    # by the model; the fallback flag routes the row to review.
    model_used: str | None = None
    lighter_model_fallback: bool = False
    # Detail records (see above). Empty by default, so results stored before
    # these existed still load.
    education: list[EducationEntry] = Field(default_factory=list)
    publications: list[PublicationEntry] = Field(default_factory=list)
    events: list[EventEntry] = Field(default_factory=list)
    subjects_taught: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    achievements: list[AchievementEntry] = Field(default_factory=list)
    guidance: list[GuidanceEntry] = Field(default_factory=list)
    memberships: list[str] = Field(default_factory=list)
    # Contact details. Personal data: exported to the workbook because the
    # user asked for it, never written to logs.
    email: str | None = None
    phone: str | None = None
    # Added in Phase 4. `subjects` carries the level each subject was taught
    # at; `subjects_taught` stays as the plain names for the workbook.
    subjects: list[SubjectEntry] = Field(default_factory=list)
    research_profile: ResearchProfile = Field(default_factory=ResearchProfile)
    # The State in the candidate's own address, only when the resume writes
    # it. An application-form answer always outranks it.
    state: str | None = None
    # Whether the resume text contains a NET/SET/SLET mention at all. Found by
    # a text search in llm.postprocess, not reported by the model.
    net_set_mentioned_in_text: bool | None = None


# Ordered once here so the workbook, the confidence routing and the UI all
# agree on field order without repeating the list.
FIELD_NAMES: tuple[str, ...] = (
    "candidate_name",
    "highest_degree",
    "marks_pct",
    "cgpa",
    "has_phd",
    "phd_status",
    "phd_award_date",
    "phd_regulation",
    "masters_award_date",
    "net_set_status",
    "set_state",
    "study_leave_taken",
    "teaching_years_raw",
    "publications_count",
    "publication_titles",
    "publications_in_progress_count",
    "publications_in_progress_titles",
)

# Fields the model is always expected to ground in the resume. Everything else
# is legitimately absent on many resumes (a candidate with no Ph.D. has no
# award date), so absence alone must not flag the row for review.
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"highest_degree", "has_phd", "net_set_status", "teaching_years_raw", "publications_count"}
)

OPTIONAL_FIELDS: frozenset[str] = frozenset(FIELD_NAMES) - REQUIRED_FIELDS


FailureKind = Literal["api_unavailable", "quota", "unreadable", "bad_config"]

# Worth re-running later without changing anything. "unreadable" will fail the
# same way every time, and "bad_config" needs a human to fix the key or model.
RETRYABLE_FAILURE_KINDS: frozenset[str] = frozenset({"api_unavailable", "quota"})


class ExtractionFailure(Exception):
    """The provider could not produce a usable ExtractionResult.

    Callers turn this into a row with a parse_error and needs_review = TRUE;
    it must never drop an uploaded resume silently. `kind` says what to do
    about it: wait and retry (api_unavailable, quota) or fix something first
    (bad_config).
    """

    def __init__(self, message: str = "", kind: FailureKind = "api_unavailable") -> None:
        super().__init__(message)
        self.kind: FailureKind = kind


@runtime_checkable
class LLMProvider(Protocol):
    """The one capability this slice needs from a model."""

    name: str

    # How many extractions may run at once against this backend. A local
    # CPU-bound model must stay at 1 (parallel calls just thrash the same
    # cores and slow everything down); a cloud API can overlap requests.
    # Declared by the provider because only it knows its own constraint.
    max_concurrency: int

    def extract_fields(self, resume_text: str) -> ExtractionResult: ...


class ResumeRecord(BaseModel):
    """One uploaded file and whatever came back for it — one row in the sheet.

    Carries the per-run metadata (`source_filename`, `processed_at`,
    `parse_error`) that the extraction schema itself has no business holding,
    so a file that failed to parse still produces a row.
    """

    source_filename: str
    processed_at: datetime
    result: ExtractionResult | None = None
    parse_error: str | None = None
    failure_kind: FailureKind | None = None

    @classmethod
    def failed(
        cls, source_filename: str, parse_error: str, failure_kind: FailureKind | None = None
    ) -> "ResumeRecord":
        return cls(
            source_filename=source_filename,
            processed_at=datetime.now(),
            result=None,
            parse_error=parse_error,
            failure_kind=failure_kind,
        )
