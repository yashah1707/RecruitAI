"""Deterministic clean-up applied to every model's output, whatever provider.

Checks the model cannot be trusted to do on itself:

1. Drop evidence that isn't genuinely in the resume text.
2. File a `marks_pct` that is really a CGPA under `cgpa` instead.
3. Reject marks taken from the wrong row of the qualifications table.

Neither converts or computes anything — a rejected value becomes null and the
row routes to review, so a human supplies the real figure. Converting a CGPA
to a percentage would be arithmetic on candidate data, which this layer is
explicitly not allowed to do (the conversion factor varies by university, so
there is no single correct formula to apply anyway).
"""

from __future__ import annotations

import logging
import re

from llm.confidence import grounding_quality, references_lower_qualification
from llm.interface import FIELD_NAMES, ExtractionResult

logger = logging.getLogger("recruitai.postprocess")

# A percentage at or below this is not a credible exam percentage for a
# degree being offered for a faculty post — statutory minima sit at 50-55%.
# A number this low in a percentage field is a CGPA the model mislabelled
# (real examples seen: "8.2% M.E.", "9.13 CGPA", "8.79").
MAX_IMPLAUSIBLE_PERCENTAGE = 10.0

# Deliberately no keyword ("CGPA"/"GPA") check here. Grade-point scales top
# out at 10, so the number alone is decisive, and matching on nearby words
# actively misfires: an evidence span quoting a Master's row as
# "M.Tech ... GPA: 7.78" alongside a genuine 63.56 percentage would see the
# word "GPA" and reject a perfectly good percentage.

# Full containment: every word of the shorter title must appear in the longer
# one. A genuine re-listing repeats the title verbatim and appends metadata
# ("..., IJSRT, 2021"), so it scores exactly 1.0; anything less means a word
# actually differs, and one differing word is usually the whole point ("Part
# I" vs "Part II", "...for Vision" vs "...for Speech"). Anything looser
# merged distinct papers in testing, and understating a candidate's record is
# worse than leaving a near-duplicate for a human to notice.
TITLE_DUPLICATE_SIMILARITY = 1.0


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", title.lower()).strip()


# A re-listing needs to share at least this many words before containment is
# trusted, so that a short title ("Machine Learning") isn't swallowed by a
# longer unrelated one that happens to contain it.
MIN_SHARED_TITLE_TOKENS = 4


def _title_similarity(a: str, b: str) -> float:
    """Containment similarity between two publication titles.

    Containment, not Jaccard: a re-listing typically *adds* tokens (venue,
    year, "Level of participation"), so the plain title is a subset of the
    decorated one. Jaccard punishes exactly that -- the real pair
    "Deep Learning Approaches for Image Forgery Detection" vs the same title
    plus ", IJSRT, 2021" scores 0.78 on Jaccard and slips through, but 1.0
    on containment.
    """
    ta, tb = set(_normalize_title(a).split()), set(_normalize_title(b).split())
    if not ta or not tb:
        return 0.0
    shared = ta & tb
    if len(shared) < MIN_SHARED_TITLE_TOKENS:
        return 0.0
    return len(shared) / min(len(ta), len(tb))


def deduplicate_titles(titles: list[str]) -> tuple[list[str], int]:
    """Collapse re-listings of the same work. Returns (kept, removed_count).

    Real CVs list the same paper under "Publications" and again inside a
    projects/research table with different formatting. Each mention carries
    its own honest quote, so the evidence check cannot catch the double
    count -- only comparing the titles to each other can.
    """
    kept: list[str] = []
    removed = 0
    for title in titles:
        if not title or not title.strip():
            continue
        if any(_title_similarity(title, k) >= TITLE_DUPLICATE_SIMILARITY for k in kept):
            removed += 1
            continue
        kept.append(title)
    return kept, removed


# How much of a date the resume actually stated. A bare year normalised to
# "2015-01-01" reads in the export exactly like a verified full date, so a
# reviewer cannot tell "year only, verified" from "day-accurate, verified".
DATE_FIELDS_WITH_PRECISION = ("phd_award_date", "masters_award_date")

_MONTHS = (
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
)


def date_precision(evidence: str | None) -> str | None:
    """Infer whether the source gave a year, a month, or a full date.

    Returns "year" | "month" | "full", or None when there is no evidence to
    judge from. Deliberately reads the evidence quote rather than the parsed
    value, because the parsed value has already had the missing parts filled
    in and can no longer tell us what was really there.
    """
    if not evidence:
        return None
    text = evidence.lower()
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", text) or re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text):
        return "full"
    if re.search(r"\b\d{1,2}(st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")", text):
        return "full"
    if any(m in text for m in _MONTHS):
        return "month"
    if re.search(r"\b(19|20)\d{2}\b", text):
        return "year"
    return None


def looks_like_cgpa(value: float | None, evidence: str | None) -> bool:
    """Whether a marks_pct value is really a grade point, not a percentage."""
    if value is None:
        return False
    return value <= MAX_IMPLAUSIBLE_PERCENTAGE


# Fields whose successful, grounded extraction shows the qualifications and
# eligibility content of the resume was actually readable. A confident "no
# NET/SET here" is only meaningful if there was something to search.
_SEARCH_COVERAGE_FIELDS = ("highest_degree", "has_phd", "masters_award_date", "teaching_years_raw")

# A negative finding can never be better-evidenced than this, because no quote
# can prove an absence. Caps the model's self-report rather than trusting it.
MAX_NEGATIVE_FINDING_CONFIDENCE = 0.85

# A value whose quote could not be located in the source text is, at best, a
# guess that happens to be right. Capped below the routing threshold so it
# always reaches a human rather than riding on the model's self-report.
MAX_UNGROUNDED_CONFIDENCE = 0.4

# Located only after punctuation was stripped. Could be a PDF decoding
# artifact or the model tidying as it copied; either way the quote is not
# quite verbatim, so it is a little less checkable by a human.
MAX_LOOSE_MATCH_CONFIDENCE = 0.8


def search_coverage(result: ExtractionResult) -> float:
    """How much qualification content was actually extracted, 0.0-1.0.

    Used to temper a `net_set_status = NONE`. The source plan calls NET/SET
    "the single highest-risk piece of logic in the whole system", and a flat
    self-reported 0.95 on a negative finding is indistinguishable from "the
    model didn't look properly" -- it just happens to be right when the
    resume genuinely has no NET/SET. Deriving it from how much of the resume
    parsed at all makes the number mean something.
    """
    grounded = sum(
        1
        for name in _SEARCH_COVERAGE_FIELDS
        if getattr(result, name).value is not None and getattr(result, name).evidence
    )
    return grounded / len(_SEARCH_COVERAGE_FIELDS)


def _temper_negative_net_set(data: dict, result: ExtractionResult) -> bool:
    """Scale a NONE's confidence by how much of the resume was searchable."""
    field = data["net_set_status"]
    if field["value"] != "NONE":
        return False
    coverage = search_coverage(result)
    tempered = round(min(field["confidence"], MAX_NEGATIVE_FINDING_CONFIDENCE) * coverage, 3)
    if tempered == field["confidence"]:
        return False
    field["confidence"] = tempered
    return True


def sanitize(result: ExtractionResult, resume_text: str) -> tuple[ExtractionResult, dict[str, int]]:
    """Return a cleaned copy of `result` plus a count of what was dropped.

    The counts carry no candidate data, so they are safe to log.
    """
    data = result.model_dump(mode="json")
    stats = {"ungrounded_evidence": 0, "cgpa_moved_from_marks_pct": 0, "marks_from_wrong_qualification": 0, "net_set_none_tempered": 0, "duplicate_publications_removed": 0, "loose_evidence_match": 0, "has_phd_realigned_to_phd_status": 0}

    for name in FIELD_NAMES:
        evidence = data[name]["evidence"]
        if not evidence:
            continue
        quality = grounding_quality(resume_text, evidence)
        if quality == "loose":
            # Located, but not character-for-character. Still usable evidence
            # -- keep the quote -- yet not as checkable as a verbatim one, so
            # it should not sit at the same confidence as an exact match.
            data[name]["confidence"] = round(
                min(data[name]["confidence"], MAX_LOOSE_MATCH_CONFIDENCE), 3
            )
            stats["loose_evidence_match"] += 1
        elif quality == "none":
            data[name]["evidence"] = None
            # Nulling the quote alone left the model's own 0.95 sitting next
            # to a value nothing supports, which is how confidence ended up
            # clustered on {0.9, 0.95, 0.98} regardless of extraction
            # quality. An ungrounded value cannot be better than uncertain.
            data[name]["confidence"] = round(
                min(data[name]["confidence"], MAX_UNGROUNDED_CONFIDENCE), 3
            )
            stats["ungrounded_evidence"] += 1

    marks, cgpa = data["marks_pct"], data["cgpa"]

    # Wrong-row rejection runs first: a mark lifted from the 12th-standard
    # line is wrong whichever scale it is on, so there is nothing worth
    # rescuing by moving it to `cgpa`.
    for name, field in (("marks_pct", marks), ("cgpa", cgpa)):
        if field["value"] is not None and references_lower_qualification(field["evidence"]):
            # Evidence is deliberately kept: it shows the reviewer exactly
            # which row was rejected ("Higher Secondary ... 85.2%") so they
            # can enter the right figure, and it lets llm.confidence tell
            # this case apart from a resume that states no marks at all.
            field["value"] = None
            field["confidence"] = 0.0
            stats["marks_from_wrong_qualification"] += 1

    if looks_like_cgpa(marks["value"], marks["evidence"]):
        # Move it rather than discard it. Dropping the number entirely used
        # to cost real candidates their whole marks score -- four of twelve
        # resumes in testing stated a CGPA (9.13, 9.20, 8.79, 8.2) and were
        # scored as having no marks at all. The value is real data, it was
        # just filed under the wrong scale.
        if cgpa["value"] is None:
            cgpa.update(marks)
        marks["value"] = None
        marks["confidence"] = 0.0
        marks["evidence"] = None
        stats["cgpa_moved_from_marks_pct"] += 1

    # Collapse re-listed publications and correct each count to match. Both
    # lists get the same treatment: a work re-listed under "Research Work"
    # double-counts whether or not it has cleared review yet.
    for titles_field, count_field in (
        ("publication_titles", "publications_count"),
        ("publications_in_progress_titles", "publications_in_progress_count"),
    ):
        titles = data[titles_field]["value"]
        if not titles:
            continue
        kept, removed = deduplicate_titles(titles)
        if not removed:
            continue
        data[titles_field]["value"] = kept
        data[count_field]["value"] = len(kept)
        # The model's own count is no longer the source of truth, so its
        # confidence in that count no longer applies either.
        data[count_field]["confidence"] = round(
            min(data[count_field]["confidence"], 0.75), 3
        )
        stats["duplicate_publications_removed"] += removed

    for name in DATE_FIELDS_WITH_PRECISION:
        data[f"{name}_precision"] = date_precision(data[name]["evidence"]) if data[name]["value"] else None

    # has_phd is COMPLETED restated as a boolean. The model reports both, so
    # they can disagree ("Thesis Submitted" with has_phd=true); phd_status is
    # the richer field and wins. Nothing downstream changes -- has_phd keeps
    # exactly the meaning it always had.
    if data["phd_status"]["value"] is not None:
        derived = data["phd_status"]["value"] == "COMPLETED"
        if data["has_phd"]["value"] != derived:
            data["has_phd"]["value"] = derived
            stats["has_phd_realigned_to_phd_status"] += 1

    if _temper_negative_net_set(data, result):
        stats["net_set_none_tempered"] += 1

    data["raw_llm_output"] = result.raw_llm_output
    return ExtractionResult(**data), stats


def log_stats(stats: dict[str, int]) -> None:
    dropped = {k: v for k, v in stats.items() if v}
    if dropped:
        logger.info("postprocess_dropped %s", dropped)
