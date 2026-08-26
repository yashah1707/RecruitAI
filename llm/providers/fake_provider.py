"""A provider that returns canned results, so the app runs with no model.

Used to develop and demo the Streamlit UI and the Excel writer without waiting
on CPU inference, and to unit-test the confidence routing deterministically.
The canned set deliberately includes a clean row, a row that trips the
SET/SLET-without-state rule, and a row with a low-confidence required field.
"""

from __future__ import annotations

import json
from datetime import date
from itertools import cycle
from typing import Iterator

from llm.interface import ExtractionResult


def _f(value, confidence: float, evidence: str | None) -> dict:
    return {"value": value, "confidence": confidence, "evidence": evidence}


_ABSENT = {"value": None, "confidence": 0.0, "evidence": None}


def _canned() -> list[ExtractionResult]:
    """Three synthetic results. No real person's data appears here."""

    clean_net = ExtractionResult(
        candidate_name=_f("A. Synthetic Candidate", 0.96, "A. Synthetic Candidate"),
        highest_degree=_f("PG", 0.94, "M.Sc. Physics, 2015"),
        marks_pct=_f(68.4, 0.88, "M.Sc. Physics, 2015 — 68.4%"),
        cgpa=_ABSENT,
        has_phd=_f(False, 0.91, "No doctoral qualification listed"),
        phd_award_date=_ABSENT,
        phd_regulation=_ABSENT,
        masters_award_date=_f(date(2015, 6, 30), 0.85, "M.Sc. Physics, awarded June 2015"),
        net_set_status=_f("NET", 0.95, "UGC-NET (Physical Sciences), December 2016"),
        set_state=_ABSENT,
        study_leave_taken=_ABSENT,
        teaching_years_raw=_f(6.0, 0.82, "Assistant Professor, 2018-2024"),
        publications_count=_f(4, 0.79, "Publications: four peer-reviewed papers listed"),
        publication_titles=_f(["Paper A", "Paper B", "Paper C", "Paper D"], 0.79, "Publications: four peer-reviewed papers listed"),
        publications_in_progress_count=_f(0, 0.79, None),
        publications_in_progress_titles=_f([], 0.79, None),
        raw_llm_output="{}",
    )

    # SET claimed but the state could not be grounded — section 6 forces this
    # row to needs_review no matter what confidence the model reported.
    set_without_state = ExtractionResult(
        candidate_name=_f("B. Synthetic Candidate", 0.93, "B. Synthetic Candidate"),
        highest_degree=_f("PhD", 0.92, "Ph.D. in Commerce, 2019"),
        marks_pct=_f(72.0, 0.81, "M.Com. — 72%"),
        cgpa=_ABSENT,
        has_phd=_f(True, 0.97, "Ph.D. in Commerce, awarded 2019"),
        phd_award_date=_f(date(2019, 11, 12), 0.86, "Ph.D. awarded 12 November 2019"),
        phd_regulation=_ABSENT,
        masters_award_date=_f(date(2011, 5, 1), 0.8, "M.Com., May 2011"),
        net_set_status=_f("SET", 0.9, "Cleared State Eligibility Test in 2013"),
        set_state={"value": None, "confidence": 0.35, "evidence": None},
        study_leave_taken=_ABSENT,
        teaching_years_raw=_f(9.5, 0.84, "Lecturer since 2014"),
        publications_count=_f(11, 0.76, "11 publications listed under Research Output"),
        publication_titles=_f([f"Commerce Paper {i}" for i in range(1, 12)], 0.76, "11 publications listed under Research Output"),
        publications_in_progress_count=_f(1, 0.7, None),
        publications_in_progress_titles=_f(["A Commerce Paper Under Review"], 0.7, None),
        raw_llm_output="{}",
    )

    # A required field the model was not sure about at all.
    low_confidence = ExtractionResult(
        candidate_name=_f("C. Synthetic Candidate", 0.62, "C. Synthetic Candidate"),
        highest_degree=_f("PG", 0.71, "MA English"),
        marks_pct=_ABSENT,
        cgpa=_f(8.4, 0.8, "CGPA 8.4"),
        has_phd=_f(False, 0.74, "No Ph.D. entry found"),
        phd_award_date=_ABSENT,
        phd_regulation=_ABSENT,
        masters_award_date=_f(date(1989, 4, 1), 0.73, "M.A. English, April 1989"),
        net_set_status=_f("NONE", 0.7, "No NET/SET entry found"),
        set_state=_ABSENT,
        study_leave_taken=_ABSENT,
        teaching_years_raw={"value": 3.0, "confidence": 0.35, "evidence": None},
        publications_count=_f(0, 0.8, "No publications section"),
        publication_titles=_f([], 0.8, "No publications section"),
        publications_in_progress_count=_f(0, 0.8, None),
        publications_in_progress_titles=_f([], 0.8, None),
        raw_llm_output="{}",
    )

    for r in (clean_net, set_without_state, low_confidence):
        r.raw_llm_output = json.dumps(json.loads(r.model_dump_json(exclude={"raw_llm_output"})))
    return [clean_net, set_without_state, low_confidence]


CANNED_RESULTS: list[ExtractionResult] = _canned()


class FakeProvider:
    """Cycles through CANNED_RESULTS so a multi-file upload shows variety."""

    name = "fake"
    # No I/O at all, but kept serial so the canned results cycle in a
    # predictable order for tests and demos.
    max_concurrency = 1

    def __init__(self, results: list[ExtractionResult] | None = None) -> None:
        self._results = list(results or CANNED_RESULTS)
        self._cursor: Iterator[ExtractionResult] = cycle(self._results)

    def extract_fields(self, resume_text: str) -> ExtractionResult:
        return next(self._cursor).model_copy(deep=True)
