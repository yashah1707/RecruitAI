"""Deterministic review routing. No model call, no arithmetic on candidate data.

This decides one thing: does a human need to look at this row before anyone
trusts it. It never decides eligibility, never scores and never orders
candidates.

The model's self-reported number is only part of the signal — a value with no
verbatim evidence behind it is treated as unverified whatever the number says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from config import CONFIDENCE_THRESHOLD
from llm.interface import (
    FIELD_NAMES,
    REQUIRED_FIELDS,
    ExtractionResult,
    FieldWithConfidence,
)

_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)
_NULLISH_STRINGS = frozenset({"null", "none", "n/a", "na", "not applicable", "not found", "not mentioned"})


def _normalize_for_grounding(s: str) -> str:
    # Strip everything but letters/digits, not just whitespace. Real PDFs
    # routinely mangle punctuation on extraction (curly quotes decoding to a
    # replacement character, en-dashes dropping, missing spaces between
    # words) -- a model that reproduces the *correct* punctuation for text it
    # read correctly must not be penalized for not also reproducing the
    # source PDF's extraction artifacts. Fabricated or paraphrased text still
    # won't share a long run of the same letters/digits with the source, so
    # this doesn't meaningfully weaken the check against genuine hallucination.
    return _NON_WORD_RE.sub("", s.lower())


def is_grounded(resume_text: str, evidence: str | None) -> bool:
    """Whether `evidence` genuinely appears in `resume_text`.

    A model claiming a field's `evidence` field is not proof by itself — on
    real resumes, models sometimes write a description of the document
    ("The document lists 'PhD' in the header section") instead of an actual
    quote from it, or emit the literal text "null" instead of a real JSON
    null. Both are exactly as untrustworthy as no evidence at all, so this
    does a punctuation/whitespace/case-insensitive substring check against
    the real source text rather than trusting the model's self-report.
    """
    if not evidence or not evidence.strip():
        return False
    if evidence.strip().lower() in _NULLISH_STRINGS:
        return False
    return _normalize_for_grounding(evidence) in _normalize_for_grounding(resume_text)


@dataclass
class ReviewOutcome:
    """Whether the row needs review, and the field-level reasons why.

    `reasons` names fields and rule names only — never extracted values — so it
    is safe to log.
    """

    needs_review: bool
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.needs_review


def is_low_confidence(f: FieldWithConfidence, threshold: float = CONFIDENCE_THRESHOLD) -> bool:
    return f.confidence < threshold


def is_unverified(f: FieldWithConfidence, threshold: float = CONFIDENCE_THRESHOLD) -> bool:
    """A claimed (non-null) value that is either low-confidence or unquoted."""
    if f.value is None:
        return False
    return f.confidence < threshold or f.evidence is None


def asserts_absence(name: str, f: FieldWithConfidence) -> bool:
    """Whether this field's value is a claim that something is *not* present.

    `net_set_status == "NONE"`, `has_phd is False` and
    `study_leave_taken is False` all mean "the resume does not show this".
    There is no verbatim quote that can prove an absence — a resume that has
    no NET/SET simply has nothing to cite — so demanding evidence for these
    is unsatisfiable and flags every row, which destroys the signal value of
    needs_review. These are still confidence-checked; they just aren't
    evidence-checked.
    """
    if name == "net_set_status":
        return f.value == "NONE"
    if name in ("has_phd", "study_leave_taken"):
        return f.value is False
    return False


def _set_state_is_unusable(
    set_state: FieldWithConfidence, threshold: float
) -> bool:
    return (
        set_state.value is None
        or set_state.confidence < threshold
        or set_state.evidence is None
    )


def evaluate(
    result: ExtractionResult | None,
    parse_error: str | None = None,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> ReviewOutcome:
    """Apply the routing rules to one extraction.

    Rules, in order:

    1. No result at all (unreadable file, extraction failure) -> review.
    2. A required field below threshold, or claimed without evidence -> review.
    3. An optional field the model *claimed* a value for, below threshold or
       without evidence -> review. Optional fields the resume is simply silent
       about are absent by design (confidence 0.0) and are not a review
       trigger on their own; that is what "never guess" produces.
    4. net_set_status is SET/SLET while set_state is missing or unusable ->
       always review, whatever confidence was reported.
    """

    reasons: list[str] = []

    if parse_error:
        reasons.append("parse_error")
    if result is None:
        reasons.append("no_extraction")
        return ReviewOutcome(True, reasons)

    for name in FIELD_NAMES:
        f: FieldWithConfidence = getattr(result, name)
        if name in REQUIRED_FIELDS:
            if f.value is None:
                reasons.append(f"{name}:missing_required")
            elif f.confidence < threshold:
                reasons.append(f"{name}:low_confidence")
            elif f.evidence is None and not asserts_absence(name, f):
                reasons.append(f"{name}:no_evidence")
        elif f.value is not None:
            if f.confidence < threshold:
                reasons.append(f"{name}:low_confidence")
            elif f.evidence is None and not asserts_absence(name, f):
                reasons.append(f"{name}:no_evidence")

    # A marks_pct that survived postprocessing as null while its evidence
    # still quotes a figure means the number was a CGPA we refused to treat
    # as a percentage. Say so, so the reviewer knows to enter it by hand
    # rather than assuming the resume was silent about marks.
    marks = result.marks_pct
    if marks.value is None and marks.evidence:
        reasons.append("marks_pct:cgpa_not_percentage")

    status = result.net_set_status.value
    if status in ("SET", "SLET") and _set_state_is_unusable(result.set_state, threshold):
        reason = "set_state:required_for_" + str(status).lower()
        if reason not in reasons:
            reasons.append(reason)

    return ReviewOutcome(bool(reasons), reasons)
