"""Run the synthetic fixture set through the real Ollama model.

This is a data point for the report, not a CI gate: LLM output isn't
byte-for-byte deterministic, so there's no pass/fail assertion here, just a
printed per-field comparison against the expected values each fixture was
built to contain. Requires a running Ollama server with `config.LLM_MODEL`
pulled — skips cleanly if the server isn't reachable.

Run directly for a readable report:
    python -m pytest tests/test_extraction_accuracy.py -s -q
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__":
    # Running this file directly (not via `python -m pytest`) puts tests/ on
    # sys.path instead of the project root; add the root so `config` and
    # `app.*` / `llm.*` imports resolve the same way they do under pytest.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests

import config
from app.resume_text import extract_text
from llm.confidence import evaluate
from llm.interface import ExtractionFailure
from llm.providers.ollama_provider import OllamaProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "synthetic_resumes"


@dataclass
class Case:
    filename: str
    expect_net_set_status: str | None = None
    expect_set_state_present: bool | None = None
    expect_has_phd: bool | None = None
    expect_study_leave_taken_is_null: bool | None = None
    note: str = ""


CASES: list[Case] = [
    Case("net_no_phd.docx", expect_net_set_status="NET", expect_has_phd=False),
    Case(
        "set_maharashtra.docx",
        expect_net_set_status="SET",
        expect_set_state_present=True,
        note="state should resolve to Maharashtra",
    ),
    Case(
        "set_karnataka.docx",
        expect_net_set_status="SET",
        expect_set_state_present=True,
        note="state should resolve to Karnataka, not be dropped or defaulted",
    ),
    Case(
        "phd_concurrent_no_leave.docx",
        expect_has_phd=True,
        expect_study_leave_taken_is_null=True,
        note="resume never mentions leave — must come back null, not guessed",
    ),
    Case("masters_pre_1991.docx", expect_has_phd=True, note="masters_award_date should parse to 1989"),
]


def _server_reachable() -> bool:
    try:
        requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=3)
        return True
    except requests.RequestException:
        return False


def _run_report() -> list[dict]:
    provider = OllamaProvider()
    rows = []
    for case in CASES:
        path = FIXTURES_DIR / case.filename
        with open(path, "rb") as fh:
            resume = extract_text(fh, case.filename)

        try:
            result = provider.extract_fields(resume.text)
        except ExtractionFailure as exc:
            # One slow/failed fixture must not sink the whole report — this
            # mirrors how the dashboard itself never lets one bad file crash
            # a batch (llm.confidence.evaluate routes it to needs_review).
            rows.append(
                {
                    "filename": case.filename,
                    "note": case.note,
                    "needs_review": True,
                    "checks": {},
                    "accuracy": None,
                    "extraction_error": str(exc),
                }
            )
            continue

        outcome = evaluate(result)

        checks: dict[str, bool] = {}
        if case.expect_net_set_status is not None:
            checks["net_set_status"] = result.net_set_status.value == case.expect_net_set_status
        if case.expect_set_state_present is not None:
            checks["set_state_present"] = (result.set_state.value is not None) == case.expect_set_state_present
        if case.expect_has_phd is not None:
            checks["has_phd"] = result.has_phd.value == case.expect_has_phd
        if case.expect_study_leave_taken_is_null is not None:
            checks["study_leave_taken_is_null"] = (
                result.study_leave_taken.value is None
            ) == case.expect_study_leave_taken_is_null

        rows.append(
            {
                "filename": case.filename,
                "note": case.note,
                "needs_review": outcome.needs_review,
                "checks": checks,
                "accuracy": sum(checks.values()) / len(checks) if checks else None,
            }
        )
    return rows


def _print_report(rows: list[dict]) -> None:
    print(f"\nExtraction accuracy report — model={config.LLM_MODEL}\n" + "=" * 60)
    total, passed = 0, 0
    for row in rows:
        print(f"\n{row['filename']}  ({row['note']})")
        if row.get("extraction_error"):
            print(f"  EXTRACTION FAILED: {row['extraction_error']}")
            continue
        print(f"  needs_review={row['needs_review']}")
        for field, ok in row["checks"].items():
            print(f"  {'OK  ' if ok else 'MISS'} {field}")
            total += 1
            passed += int(ok)
    if total:
        print(f"\nOverall: {passed}/{total} field-level checks passed ({100 * passed / total:.0f}%)")
    print("=" * 60)


@pytest.mark.skipif(not _server_reachable(), reason="Ollama server not reachable at OLLAMA_HOST")
def test_extraction_accuracy_report():
    rows = _run_report()
    _print_report(rows)
    assert len(rows) == len(CASES)


if __name__ == "__main__":
    if not _server_reachable():
        print(f"Ollama server not reachable at {config.OLLAMA_HOST} — start it and pull {config.LLM_MODEL} first.")
    else:
        _print_report(_run_report())
