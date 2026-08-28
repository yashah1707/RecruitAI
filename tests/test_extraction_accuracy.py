"""Run the synthetic fixture set through the real Gemini model.

This is the one test that makes live API calls. It is a data point for the
report, not a CI gate: model output isn't byte-for-byte deterministic, so
there is no pass/fail assertion on values, just a printed per-field
comparison against what each fixture was built to contain.

Everything else in the suite runs against FakeProvider with no network, so a
Gemini outage or an exhausted quota cannot fail the build -- only this file
is skipped.

Run it explicitly:
    python -m pytest tests/test_extraction_accuracy.py -s -q
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

import config
from app.resume_text import extract_text
from llm.confidence import evaluate
from llm.interface import ExtractionFailure

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "synthetic_resumes"


@dataclass
class Case:
    filename: str
    expect_net_set_status: str | None = None
    expect_set_state_present: bool | None = None
    expect_has_phd: bool | None = None
    expect_phd_status: str | None = None
    expect_highest_degree: str | None = None
    expect_study_leave_taken_is_null: bool | None = None
    note: str = ""


CASES: list[Case] = [
    Case("net_no_phd.docx", expect_net_set_status="NET", expect_has_phd=False,
         note="positive NET path"),
    Case("set_maharashtra.docx", expect_net_set_status="SET", expect_set_state_present=True,
         note="SET + state; the set_state cross-check has something to check"),
    Case("set_karnataka.docx", expect_net_set_status="SET", expect_set_state_present=True,
         note="out-of-state SET must not be defaulted to Maharashtra"),
    Case("diploma_highest_degree.docx", expect_highest_degree="Diploma",
         note="Diploma is a real DegreeLevel, not a mis-typed UG"),
    Case("phd_concurrent_no_leave.docx", expect_has_phd=True,
         expect_study_leave_taken_is_null=True,
         note="resume never mentions leave -- must stay null, not guessed"),
    Case("masters_pre_1991.docx", expect_has_phd=True,
         note="masters_award_date should parse to 1989"),
    Case("masters_cgpa_school_percentage.docx",
         note="marks must come from the M.Tech row, not the 91.4% school row"),
]


def _live_calls_enabled() -> bool:
    return bool(config.GEMINI_API_KEY) and os.environ.get("LLM_PROVIDER", "gemini") != "fake"


def _run_report() -> list[dict]:
    from llm.providers.gemini_provider import GeminiProvider

    provider = GeminiProvider()
    rows = []
    for case in CASES:
        path = FIXTURES_DIR / case.filename
        if not path.exists():
            rows.append({"filename": case.filename, "note": case.note,
                         "error": "fixture missing", "checks": {}})
            continue
        with open(path, "rb") as fh:
            resume = extract_text(fh, case.filename)
        try:
            result = provider.extract_fields(resume.text)
        except ExtractionFailure as exc:
            rows.append({"filename": case.filename, "note": case.note,
                         "error": str(exc), "checks": {}})
            continue

        checks: dict[str, bool] = {}
        if case.expect_net_set_status is not None:
            checks["net_set_status"] = result.net_set_status.value == case.expect_net_set_status
        if case.expect_set_state_present is not None:
            checks["set_state_present"] = (result.set_state.value is not None) == case.expect_set_state_present
        if case.expect_has_phd is not None:
            checks["has_phd"] = result.has_phd.value == case.expect_has_phd
        if case.expect_phd_status is not None:
            checks["phd_status"] = result.phd_status.value == case.expect_phd_status
        if case.expect_highest_degree is not None:
            checks["highest_degree"] = result.highest_degree.value == case.expect_highest_degree
        if case.expect_study_leave_taken_is_null is not None:
            checks["study_leave_taken_is_null"] = (
                result.study_leave_taken.value is None
            ) == case.expect_study_leave_taken_is_null

        rows.append({
            "filename": case.filename,
            "note": case.note,
            "needs_review": evaluate(result).needs_review,
            "checks": checks,
        })
    return rows


def _print_report(rows: list[dict]) -> None:
    print(f"\nExtraction accuracy report — model={config.GEMINI_MODEL}\n" + "=" * 62)
    total = passed = 0
    for row in rows:
        print(f"\n{row['filename']}  ({row['note']})")
        if row.get("error"):
            print(f"  ERROR: {row['error'][:90]}")
            continue
        print(f"  needs_review={row['needs_review']}")
        for field, ok in row["checks"].items():
            print(f"  {'OK  ' if ok else 'MISS'} {field}")
            total += 1
            passed += int(ok)
    if total:
        print(f"\nOverall: {passed}/{total} field-level checks passed ({100 * passed / total:.0f}%)")
    print("=" * 62)


@pytest.mark.skipif(not _live_calls_enabled(), reason="needs GEMINI_API_KEY; skipped under LLM_PROVIDER=fake")
def test_extraction_accuracy_report():
    rows = _run_report()
    _print_report(rows)
    assert len(rows) == len(CASES)
