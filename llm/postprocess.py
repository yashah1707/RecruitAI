"""Deterministic clean-up applied to every model's output, whatever provider.

Two jobs, both of them checks the model cannot be trusted to do on itself:

1. Drop evidence that isn't genuinely in the resume text.
2. Drop a `marks_pct` that is really a CGPA on a 0-10 scale.

Neither converts or computes anything — a rejected value becomes null and the
row routes to review, so a human supplies the real figure. Converting a CGPA
to a percentage would be arithmetic on candidate data, which this layer is
explicitly not allowed to do (the conversion factor varies by university, so
there is no single correct formula to apply anyway).
"""

from __future__ import annotations

import logging

from llm.confidence import is_grounded
from llm.interface import FIELD_NAMES, ExtractionResult

logger = logging.getLogger("recruitai.postprocess")

# A percentage at or below this is not a credible exam percentage for a
# degree being offered for a faculty post — statutory minima sit at 50-55%.
# A number this low in a percentage field is a CGPA the model mislabelled
# (real examples seen: "8.2% M.E.", "9.13 CGPA", "8.79").
MAX_IMPLAUSIBLE_PERCENTAGE = 10.0

# Words that mark a grade-point value even when the number alone looks like a
# plausible percentage (e.g. "10 CGPA").
_CGPA_MARKERS = ("cgpa", "gpa", "grade point", "/10", "out of 10")


def looks_like_cgpa(value: float | None, evidence: str | None) -> bool:
    """Whether a marks_pct value is really a grade point, not a percentage."""
    if value is None:
        return False
    if value <= MAX_IMPLAUSIBLE_PERCENTAGE:
        return True
    haystack = (evidence or "").lower()
    return any(marker in haystack for marker in _CGPA_MARKERS)


def sanitize(result: ExtractionResult, resume_text: str) -> tuple[ExtractionResult, dict[str, int]]:
    """Return a cleaned copy of `result` plus a count of what was dropped.

    The counts carry no candidate data, so they are safe to log.
    """
    data = result.model_dump(mode="json")
    stats = {"ungrounded_evidence": 0, "cgpa_as_percentage": 0}

    for name in FIELD_NAMES:
        evidence = data[name]["evidence"]
        if evidence and not is_grounded(resume_text, evidence):
            data[name]["evidence"] = None
            stats["ungrounded_evidence"] += 1

    marks = data["marks_pct"]
    if looks_like_cgpa(marks["value"], marks["evidence"]):
        # Keep the evidence: it shows the reviewer the raw "8.2 CGPA" text so
        # they can enter the right figure without reopening the resume.
        marks["value"] = None
        marks["confidence"] = 0.0
        stats["cgpa_as_percentage"] += 1

    data["raw_llm_output"] = result.raw_llm_output
    return ExtractionResult(**data), stats


def log_stats(stats: dict[str, int]) -> None:
    dropped = {k: v for k, v in stats.items() if v}
    if dropped:
        logger.info("postprocess_dropped %s", dropped)
