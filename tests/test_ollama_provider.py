"""Unit tests for OllamaProvider's response-processing logic.

No live Ollama server needed — these test pure functions that operate on
already-parsed model output (JSON coercion, date parsing, and the
grounding-verification post-processing step). The one thing that does need a
real server (extract_fields end-to-end) is covered by
tests/test_extraction_accuracy.py instead.
"""

from __future__ import annotations

from datetime import date

from llm.interface import ExtractionResult


def _field(value, confidence, evidence):
    return {"value": value, "confidence": confidence, "evidence": evidence}


def _result(**overrides) -> ExtractionResult:
    base = dict(
        candidate_name=_field("Jane Doe", 0.9, "Jane Doe"),
        highest_degree=_field("PhD", 0.9, "PhD in Mathematics"),
        marks_pct=_field(None, 0.0, None),
        has_phd=_field(True, 0.9, "PhD in Mathematics"),
        phd_award_date=_field(date(2020, 1, 1), 0.8, "awarded 2020"),
        phd_regulation=_field(None, 0.0, None),
        masters_award_date=_field(None, 0.0, None),
        net_set_status=_field("NET", 0.95, "UGC-NET June 2015"),
        set_state=_field(None, 0.0, None),
        study_leave_taken=_field(None, 0.0, None),
        teaching_years_raw=_field(5, 0.8, "5 years teaching"),
        publications_count=_field(2, 0.9, "2 publications listed"),
        raw_llm_output="{}",
    )
    base.update(overrides)
    return ExtractionResult(**base)


RESUME_TEXT = (
    "Jane Doe. PhD in Mathematics, awarded 2020. UGC-NET June 2015. "
    "5 years teaching. 2 publications listed."
)