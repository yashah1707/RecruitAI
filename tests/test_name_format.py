"""candidate_name is written with each word capitalised. Synthetic names only."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.excel_writer import build_row
from llm.interface import ResumeRecord
from llm.postprocess import format_person_name, sanitize
from llm.providers.fake_provider import CANNED_RESULTS


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("SAMPLE EXAMPLETON", "Sample Exampleton"),
        ("sample exampleton", "Sample Exampleton"),
        ("SAMPLE R. EXAMPLETON", "Sample R. Exampleton"),
        ("SAMPLE GAJANAN EXAMPLETON", "Sample Gajanan Exampleton"),
        ("Sample Exampleton", "Sample Exampleton"),  # already right
        ("  SAMPLE   EXAMPLETON ", "Sample Exampleton"),  # spacing tidied
        ("s.k. exampleton", "S.K. Exampleton"),
        ("ANNE-MARIE EXAMPLETON", "Anne-Marie Exampleton"),
        ("MISS.SAMPLE A. EXAMPLETON", "Miss.Sample A. Exampleton"),
        ("DR. SAMPLE EXAMPLETON", "Dr. Sample Exampleton"),
    ],
)
def test_each_word_is_capitalised(raw, expected):
    assert format_person_name(raw) == expected


@pytest.mark.parametrize("name", ["Sample McExample", "Sample DSouza", "RK Exampleton", "Miss.Sample A. Exampleton"])
def test_deliberate_mixed_case_and_initials_are_left_alone(name):
    assert format_person_name(name) == name


def test_only_case_and_spacing_change_never_the_letters():
    raw = "SAMPLE  R. EXAMPLETON"
    out = format_person_name(raw)
    assert out.lower().split() == raw.lower().split()


def test_it_is_idempotent():
    once = format_person_name("SAMPLE R. EXAMPLETON")
    assert format_person_name(once) == once


def test_absent_name_stays_absent():
    assert format_person_name(None) is None
    assert format_person_name("") == ""


def _with_name(name: str):
    result = CANNED_RESULTS[0].model_copy(deep=True)
    result.candidate_name.value = name
    result.candidate_name.evidence = name
    return result


def test_sanitize_formats_the_name_and_keeps_the_verbatim_evidence():
    cleaned, _ = sanitize(_with_name("SAMPLE EXAMPLETON"), "SAMPLE EXAMPLETON\nM.Sc. Physics")
    assert cleaned.candidate_name.value == "Sample Exampleton"
    assert cleaned.candidate_name.evidence == "SAMPLE EXAMPLETON"  # still the resume's own text


def test_a_result_cached_before_the_rule_is_still_exported_formatted():
    record = ResumeRecord(
        source_filename="a.pdf", processed_at=datetime(2026, 10, 3), result=_with_name("SAMPLE EXAMPLETON")
    )
    assert build_row(record)["candidate_name"] == "Sample Exampleton"
