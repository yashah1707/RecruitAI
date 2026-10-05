"""Tests for the run-statistics verifier.

This module exists so that reported figures cannot drift from the output
file they describe. The failure it guards against is subtle: the same batch
was run repeatedly over several days from two entry points, and a number
quoted without naming its source file is unfalsifiable. Every assertion here
is about refusing to let a stated figure pass unchecked.
"""

from __future__ import annotations

import csv

import pytest

from tools.verify_run import RunClaimMismatch, assert_claim, run_stats

COLUMNS = ["source_filename", "publications_count", "parse_error"]


def _export(tmp_path, rows):
    path = tmp_path / "export.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    return path


def test_counts_come_from_the_file(tmp_path):
    path = _export(tmp_path, [
        {"source_filename": "a.pdf", "publications_count": "3"},
        {"source_filename": "b.pdf", "parse_error": "504 DEADLINE_EXCEEDED"},
        {"source_filename": "c.pdf", "publications_count": "7"},
    ])
    stats = run_stats(path)
    assert stats.total_rows == 3
    assert stats.failure_count == 1
    assert stats.extracted_count == 2
    assert stats.failed == ["b.pdf"]


def test_an_overstated_success_count_is_rejected(tmp_path):
    """The exact mismatch this exists to prevent: reporting a clean run when
    the file records failures."""
    path = _export(tmp_path, [
        {"source_filename": "a.pdf"},
        {"source_filename": "b.pdf", "parse_error": "504 DEADLINE_EXCEEDED"},
        {"source_filename": "c.pdf", "parse_error": "504 DEADLINE_EXCEEDED"},
    ])
    with pytest.raises(RunClaimMismatch, match="claimed 0 failure"):
        assert_claim(run_stats(path), expect_failures=0)


def test_a_matching_claim_passes(tmp_path):
    path = _export(tmp_path, [
        {"source_filename": "a.pdf"},
        {"source_filename": "b.pdf", "parse_error": "504"},
    ])
    assert_claim(run_stats(path), expect_failures=1, expect_extracted=1, expect_total=2)


def test_the_mismatch_message_names_the_failing_files(tmp_path):
    """So the reader can check the claim themselves rather than trusting it."""
    path = _export(tmp_path, [
        {"source_filename": "cv_a.pdf", "parse_error": "504"},
        {"source_filename": "cv_b.pdf", "parse_error": "504"},
    ])
    with pytest.raises(RunClaimMismatch) as exc:
        assert_claim(run_stats(path), expect_failures=0)
    assert "cv_a.pdf" in str(exc.value)
    assert "cv_b.pdf" in str(exc.value)


def test_whitespace_only_parse_error_is_not_a_failure(tmp_path):
    path = _export(tmp_path, [{"source_filename": "a.pdf", "parse_error": "   "}])
    assert run_stats(path).failure_count == 0


def test_a_clean_run_reports_zero(tmp_path):
    path = _export(tmp_path, [{"source_filename": f"{i}.pdf"} for i in range(12)])
    stats = run_stats(path)
    assert (stats.total_rows, stats.failure_count, stats.extracted_count) == (12, 0, 12)
    assert_claim(stats, expect_failures=0, expect_extracted=12)


def test_summary_states_the_source_file(tmp_path):
    """A figure without its source file is unverifiable."""
    path = _export(tmp_path, [{"source_filename": "a.pdf"}])
    assert "export.csv" in run_stats(path).summary()


# --- the product and the audit must count identically -----------------------

def _mixed_records():
    """Three clean extractions and one file that produced nothing."""
    from datetime import datetime

    from llm.interface import ResumeRecord
    from llm.providers.fake_provider import FakeProvider

    p = FakeProvider()
    recs = [
        ResumeRecord(source_filename=f"ok{i}.pdf", processed_at=datetime.now(),
                     result=p.extract_fields("text"))
        for i in range(3)
    ]
    recs.append(ResumeRecord.failed("scanned.pdf", "no extractable text"))
    return recs


def test_dashboard_and_audit_report_the_same_counts(tmp_path):
    """The gap this closes: the dashboard used to count rows itself, so it
    could show a clean summary for a run the audit script would flag. Both
    now read app.run_stats, and this pins them to the same answer."""
    import csv as _csv

    from app.excel_writer import COLUMNS, build_rows
    from app.run_stats import stats_from_rows
    from tools.verify_run import run_stats

    records = _mixed_records()
    rows = build_rows(records)

    # what the dashboard renders
    on_screen = stats_from_rows(rows)

    # what the auditor gets from the downloaded export
    export = tmp_path / "export.csv"
    with open(export, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=list(COLUMNS))
        w.writeheader()
        for row in rows:
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})
    audited = run_stats(export)

    assert on_screen.total_rows == audited.total_rows
    assert on_screen.failure_count == audited.failure_count
    assert on_screen.extracted_count == audited.extracted_count
    assert sorted(on_screen.failed) == sorted(audited.failed)


def test_a_failure_is_not_hidden_inside_the_review_count():
    """A failed row is flagged needs_review, but there is nothing in it to
    review. Counting it as a review item overstates how much extracted."""
    from app.excel_writer import build_rows
    from app.run_stats import stats_from_rows

    stats = stats_from_rows(build_rows(_mixed_records()))
    assert stats.total_rows == 4
    assert stats.failure_count == 1
    assert stats.extracted_count == 3
    # 3 rows carry needs_review, but one of them is the failure
    assert stats.review_count_excluding_failures == stats.needs_review - 1


def test_the_summary_never_claims_more_extracted_than_the_file_holds(tmp_path):
    """assert_claim is what makes the numbers checkable rather than asserted."""
    import pytest

    from app.excel_writer import build_rows
    from app.run_stats import RunClaimMismatch, assert_claim, stats_from_rows

    stats = stats_from_rows(build_rows(_mixed_records()))
    assert_claim(stats, expect_extracted=3, expect_failures=1, expect_total=4)
    with pytest.raises(RunClaimMismatch):
        assert_claim(stats, expect_failures=0)
    with pytest.raises(RunClaimMismatch):
        assert_claim(stats, expect_extracted=4)


def test_records_and_rows_agree_before_and_after_row_building():
    """Counting the records directly and counting the built rows must match,
    so the number cannot shift as data moves through the pipeline."""
    from app.excel_writer import build_rows
    from app.run_stats import stats_from_records, stats_from_rows

    records = _mixed_records()
    from_records = stats_from_records(records)
    from_rows = stats_from_rows(build_rows(records))
    assert from_records.total_rows == from_rows.total_rows
    assert from_records.failure_count == from_rows.failure_count
    assert sorted(from_records.failed) == sorted(from_rows.failed)
