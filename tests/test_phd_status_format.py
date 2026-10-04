"""phd_status and its quote are exported in readable, uniform words."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.excel_writer import build_row
from llm.interface import ResumeRecord
from llm.postprocess import format_phd_evidence, format_phd_status
from llm.providers.fake_provider import CANNED_RESULTS
from tools.score_accuracy import values_match


@pytest.mark.parametrize(
    "value, shown",
    [
        ("NOT_APPLICABLE", "NA"),
        ("PURSUING", "Pursuing"),
        ("REGISTERED", "Registered"),
        ("THESIS_SUBMITTED", "Thesis Submitted"),
        ("COMPLETED", "Completed"),
    ],
)
def test_status_is_written_in_words(value, shown):
    assert format_phd_status(value) == shown


@pytest.mark.parametrize(
    "status, raw, shown",
    [
        ("PURSUING", "Ph. D. (Computer Engineering) Appearing Appearing 2025-26", "Ph.D. Computer Engineering"),
        ("PURSUING", "PhD Scholar in Computer Science and Engineering", "Ph.D. Computer Science and Engineering"),
        ("PURSUING", "Phd Persuing", "Ph.D."),
        ("PURSUING", "Pursuing PhD", "Ph.D."),
        ("COMPLETED", "Completed", "Ph.D."),
        ("THESIS_SUBMITTED", "Ph.D. (Thesis Submitted)", "Ph.D."),
        ("PURSUING", "Ph.D. ... Computer Sci. & Engg. Appearing", "Ph.D. Computer Science and Engineering"),
    ],
)
def test_quote_is_reduced_to_the_phd_and_its_course(status, raw, shown):
    assert format_phd_evidence(status, raw) == shown


@pytest.mark.parametrize("raw", ["QUALIFICATION :", "Ph.D. Entrance Test (PET), Sample University – Qualified", None])
def test_not_applicable_shows_na_whatever_was_quoted(raw):
    assert format_phd_evidence("NOT_APPLICABLE", raw) == "NA"


def _row(status: str, evidence: str | None):
    result = CANNED_RESULTS[0].model_copy(deep=True)
    result.phd_status.value = status
    result.phd_status.evidence = evidence
    return build_row(ResumeRecord(source_filename="a.pdf", processed_at=datetime(2026, 10, 3), result=result))


def test_the_export_shows_the_formatted_status_and_quote():
    row = _row("THESIS_SUBMITTED", "Ph.D. (Physics) Thesis Submitted")
    assert row["phd_status"] == "Thesis Submitted"
    assert row["phd_course_name"] == "Ph.D. Physics"

    na = _row("NOT_APPLICABLE", "EDUCATIONAL QUALIFICATION")
    assert na["phd_status"] == "NA" and na["phd_course_name"] == "NA"


def test_answer_keys_in_the_schema_spelling_still_score_as_correct():
    assert values_match("THESIS_SUBMITTED", "Thesis Submitted")
    assert values_match("NOT_APPLICABLE", "NA")
    assert values_match("PURSUING", "Pursuing")
    assert not values_match("PURSUING", "Completed")
