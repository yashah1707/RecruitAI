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


class ExtractionResult(BaseModel):
    """The MVP subset of `extracted_data`.

    School, department and designation-applied-for are deliberately absent:
    those are form inputs in the full system and must never be inferred from
    resume text.
    """

    candidate_name: FieldWithConfidence[str | None]
    highest_degree: FieldWithConfidence[str]  # UG / PG / PhD / Post-Doc
    marks_pct: FieldWithConfidence[float | None]
    # Kept separate from marks_pct rather than converted into it: CGPA->%
    # conversion factors differ by university (x10 at some, x9.5 at others),
    # so any single formula would misstate a real candidate's marks. Storing
    # the grade point on its own scale loses no information and invents none.
    cgpa: FieldWithConfidence[float | None]
    has_phd: FieldWithConfidence[bool]
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


# Ordered once here so the workbook, the confidence routing and the UI all
# agree on field order without repeating the list.
FIELD_NAMES: tuple[str, ...] = (
    "candidate_name",
    "highest_degree",
    "marks_pct",
    "cgpa",
    "has_phd",
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


class ExtractionFailure(Exception):
    """The provider could not produce a usable ExtractionResult.

    Callers turn this into a row with a parse_error and needs_review = TRUE;
    it must never drop an uploaded resume silently.
    """


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

    @classmethod
    def failed(cls, source_filename: str, parse_error: str) -> "ResumeRecord":
        return cls(
            source_filename=source_filename,
            processed_at=datetime.now(),
            result=None,
            parse_error=parse_error,
        )
