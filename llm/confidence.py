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
    return grounding_quality(resume_text, evidence) != "none"


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_whitespace_only(s: str) -> str:
    """Case and spacing folded, but punctuation left intact."""
    return _WHITESPACE_RE.sub(" ", s.lower()).strip()


def grounding_quality(resume_text: str, evidence: str | None) -> str:
    """How well an evidence quote locates in the source: exact/loose/none.

    The binary version of this check told us only whether the model invented
    the quote. But "verbatim" is the property that makes evidence checkable
    by a human in seconds, and a quote that only matches once punctuation is
    stripped is measurably weaker: it may be a genuine PDF artifact (curly
    quotes decoding to a replacement character) or the model tidying the
    source as it copied. Either way it is not quite verbatim, so it should
    not carry the same confidence as a quote that matches character for
    character.
    """
    if not evidence or not evidence.strip():
        return "none"
    if evidence.strip().lower() in _NULLISH_STRINGS:
        return "none"
    if _normalize_whitespace_only(evidence) in _normalize_whitespace_only(resume_text):
        return "exact"
    if _normalize_for_grounding(evidence) in _normalize_for_grounding(resume_text):
        return "loose"
    return _elided_grounding_quality(resume_text, evidence)


# A model citing a table row often skips the columns it doesn't need, joining
# the parts with an ellipsis: "2015-17 Amrutvahini College ... M.E.(Comp.Engg)"
# elides the "8.2%" sitting between them. Every fragment is genuinely in the
# resume, so rejecting the whole quote loses real evidence -- and on one real
# row it cost the date its precision, which silently reinstated a fabricated
# "2017-01-01". But the concatenation is not itself verbatim, so it can never
# grade better than "loose".
_ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.|…)\s*")

# Fragments shorter than this are too generic to prove anything ("of", "2015").
_MIN_ELIDED_FRAGMENT_CHARS = 4


def _elided_grounding_quality(resume_text: str, evidence: str) -> str:
    """Grade a quote whose parts are joined by an ellipsis."""
    fragments = [f for f in _ELLIPSIS_RE.split(evidence) if f.strip()]
    if len(fragments) < 2:
        return "none"
    haystack = _normalize_for_grounding(resume_text)
    for fragment in fragments:
        needle = _normalize_for_grounding(fragment)
        if len(needle) < _MIN_ELIDED_FRAGMENT_CHARS or needle not in haystack:
            return "none"
    return "loose"


# Marks belong to the qualification named in `highest_degree` -- normally the
# Master's, since that is what faculty eligibility is assessed on. Indian CVs
# list every qualification back to 10th standard in one table, each with its
# own percentage or CGPA, so an extractor that grabs "a percentage found in
# the qualifications section" reliably grabs the wrong row. Observed on a real
# resume: a candidate's 85.2% *Higher Secondary* mark was extracted as her
# marks_pct while her actual M.Tech result (GPA 7.78) sat in `cgpa`.
_LOWER_QUALIFICATION_MARKERS = (
    # school
    "h.s.c", "hsc", "s.s.c", "ssc", "higher secondary", "senior secondary",
    "secondary school", "intermediate", "matriculation", "10th", "12th",
    "class x", "class xii", "cbse", "icse", "state board", "school",
    # bachelor's
    "b.e.", "b.e ", "b.tech", "b. tech", "btech", "b.sc", "b. sc", "bsc",
    "b.a.", "b.com", "bca", "bachelor", "under graduate", "undergraduate",
)

# If the evidence names a Master's/doctoral qualification too, the lower-level
# word is probably incidental context (e.g. a table row that spans both), so
# don't reject on the lower-level marker alone.
_HIGHER_QUALIFICATION_MARKERS = (
    "m.e.", "m.e ", "m.tech", "m. tech", "mtech", "m.sc", "m. sc", "msc",
    "m.a.", "m.com", "mca", "master", "post graduate", "postgraduate",
    "ph.d", "phd", "doctoral", "post-doc",
)


def references_lower_qualification(evidence: str | None) -> bool:
    """Whether an evidence quote points at a school/bachelor's row.

    Used to reject marks scraped from the wrong line of a qualifications
    table. Deliberately conservative: a quote that also names a Master's or
    doctoral qualification is left alone, because the lower-level word is
    then most likely just neighbouring text in the same extracted span.
    """
    if not evidence:
        return False
    haystack = evidence.lower()
    if any(m in haystack for m in _HIGHER_QUALIFICATION_MARKERS):
        return False
    return any(m in haystack for m in _LOWER_QUALIFICATION_MARKERS)


def looks_like_cgpa(value: float | None, evidence: str | None) -> bool:
    """Whether a marks_pct value is really a grade point, not a percentage."""
    if value is None:
        return False
    if value <= MAX_IMPLAUSIBLE_PERCENTAGE:
        return True
    haystack = (evidence or "").lower()
    return any(marker in haystack for marker in _CGPA_MARKERS)


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
    # "No doctoral study" is the same kind of claim as has_phd being False:
    # a resume with no PhD has no line to quote for it.
    if name == "phd_status":
        return f.value == "NOT_APPLICABLE"
    # "No publications" and "nothing under review" are absences too: a
    # resume with no publications section has no line to quote for it.
    if name in ("publications_count", "publications_in_progress_count"):
        return f.value == 0
    if name in ("publication_titles", "publications_in_progress_titles"):
        return f.value == []
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

    # Extracted by the opt-in lighter model because the normal pool was down.
    # Lighter models were measurably worse on NET/SET and PhD status, so a
    # human should look at this row however confident it sounds.
    if result.lighter_model_fallback:
        reasons.append("extraction_model:lighter_model_fallback")

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
    # A marks field left with evidence but no value was rejected by
    # postprocess. Which rejection it was matters to the reviewer, so name
    # them apart. A field that is simply absent has no evidence either and
    # correctly raises nothing here -- plenty of resumes state no marks.
    for name in ("marks_pct", "cgpa"):
        f = getattr(result, name)
        if f.value is not None or not f.evidence:
            continue
        if references_lower_qualification(f.evidence):
            reasons.append(f"{name}:wrong_qualification_row")
        elif name == "marks_pct":
            reasons.append("marks_pct:cgpa_not_percentage")

    # phd_regulation is effectively unextractable: essentially no resume says
    # "awarded under the 2016 Regulations" in words, so it comes back null for
    # every PhD holder. Left as a plain null it looks like any other optional
    # blank and gets skipped, but it changes an eligibility outcome, so it has
    # to read as an outstanding question rather than an absent one. This is
    # the one field the pipeline asks a human to supply rather than find.
    if result.has_phd.value is True and result.phd_regulation.value is None:
        reasons.append("phd_regulation:manual_entry_required")

    status = result.net_set_status.value
    if status in ("SET", "SLET") and _set_state_is_unusable(result.set_state, threshold):
        reason = "set_state:required_for_" + str(status).lower()
        if reason not in reasons:
            reasons.append(reason)

    return ReviewOutcome(bool(reasons), reasons)
