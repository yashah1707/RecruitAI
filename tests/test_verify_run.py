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
        {"source_filename": "kavita.pdf", "parse_error": "504"},
        {"source_filename": "suvarna.pdf", "parse_error": "504"},
    ])
    with pytest.raises(RunClaimMismatch) as exc:
        assert_claim(run_stats(path), expect_failures=0)
    assert "kavita.pdf" in str(exc.value)
    assert "suvarna.pdf" in str(exc.value)


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
