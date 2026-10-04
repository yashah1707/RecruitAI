"""What actually happened in a run — the single source of truth for counts.

Both the dashboard the reviewer looks at and the `verify_run` audit script
read their numbers from here. That is the whole point of the module: when
the product and the audit each did their own counting, the product could
show a clean summary for a run the audit would have flagged, and neither
would notice they disagreed.

The counts are derived from `parse_error`, exactly as the export is, so a
figure on screen and the same figure recomputed from the downloaded CSV
cannot drift apart.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from llm.interface import ResumeRecord


class RunClaimMismatch(AssertionError):
    """A stated figure does not match what the run actually contains."""


@dataclass
class RunStats:
    source: str
    total_rows: int
    failed: list[str] = field(default_factory=list)
    needs_review: int = 0
    # filename -> failure_kind / parse_error text, for failed rows only. Lets
    # the banner say *why* each one failed without recounting anything.
    failed_kinds: dict[str, str] = field(default_factory=dict)
    failed_reasons: dict[str, str] = field(default_factory=dict)

    @property
    def failure_count(self) -> int:
        return len(self.failed)

    @property
    def extracted_count(self) -> int:
        return self.total_rows - self.failure_count

    @property
    def review_count_excluding_failures(self) -> int:
        """Rows a human can actually review.

        A failed row is flagged needs_review too, but there is nothing in it
        to check -- reporting it alongside genuine review items overstates
        how much was extracted and hides the failure entirely.
        """
        return max(0, self.needs_review - self.failure_count)

    def summary(self) -> str:
        lines = [
            f"run: {self.source}",
            f"  rows in file      : {self.total_rows}",
            f"  extracted         : {self.extracted_count}",
            f"  failed to extract : {self.failure_count}",
        ]
        for name in self.failed:
            lines.append(f"      - {name}")
        return "\n".join(lines)


def is_failed_row(row: dict[str, Any]) -> bool:
    """Whether one exported row represents a resume that produced no data.

    The single definition of "failed". Everything that filters or counts
    failures -- the dashboard summary, the audit script, the answer-key
    builder -- goes through this, so none of them can develop a slightly
    different idea of what counts as a failure.
    """
    return bool(str(row.get("parse_error") or "").strip())


def _stats_from_dicts(rows: Sequence[dict[str, Any]], source: str) -> RunStats:
    failed = [
        (r.get("source_filename") or "(unnamed)")
        for r in rows
        if is_failed_row(r)
    ]
    needs_review = sum(
        1 for r in rows if str(r.get("needs_review", "")).strip().lower() in ("true", "1")
    )
    failed_rows = [r for r in rows if is_failed_row(r)]
    return RunStats(
        source=source,
        total_rows=len(rows),
        failed=failed,
        needs_review=needs_review,
        failed_kinds={
            (r.get("source_filename") or "(unnamed)"): str(r.get("failure_kind") or "unknown")
            for r in failed_rows
        },
        failed_reasons={
            (r.get("source_filename") or "(unnamed)"): str(r.get("parse_error") or "")
            for r in failed_rows
        },
    )


def stats_from_rows(rows: Sequence[dict[str, Any]], source: str = "current run") -> RunStats:
    """Count an in-memory batch — what the dashboard renders from."""
    return _stats_from_dicts(rows, source)


def stats_from_records(records: Iterable[ResumeRecord], source: str = "current run") -> RunStats:
    """Count ResumeRecords directly, before they become spreadsheet rows."""
    records = list(records)
    failed = [r.source_filename for r in records if (r.parse_error or "").strip()]
    return RunStats(source=source, total_rows=len(records), failed=failed)


def retryable_filenames(records: Iterable[ResumeRecord]) -> list[str]:
    """Failed rows worth re-running as they are.

    Unreadable files (scanned, corrupted) fail identically every time, and a
    bad key or model name needs fixing first; only API unavailability and
    quota exhaustion can come right by simply trying again. A failure with no
    recorded kind is retried, since it can't be shown to be permanent.
    """
    return [
        r.source_filename
        for r in records
        if (r.parse_error or "").strip() and r.failure_kind not in ("unreadable", "bad_config")
    ]


def stats_from_export(export_csv: str | Path) -> RunStats:
    """Count a downloaded export — what the audit script reads."""
    path = Path(export_csv)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return _stats_from_dicts(rows, path.name)


def assert_claim(
    stats: RunStats,
    *,
    expect_failures: int | None = None,
    expect_extracted: int | None = None,
    expect_total: int | None = None,
) -> None:
    """Raise unless the stated figures match the run. No silent rounding.

    Deliberately raises rather than warns: a report that overstates how much
    extracted is worse than no report, because it looks like evidence.
    """
    problems = []
    if expect_failures is not None and expect_failures != stats.failure_count:
        problems.append(
            f"claimed {expect_failures} failure(s) but {stats.source} contains "
            f"{stats.failure_count}: {stats.failed}"
        )
    if expect_extracted is not None and expect_extracted != stats.extracted_count:
        problems.append(
            f"claimed {expect_extracted} extracted but {stats.source} contains "
            f"{stats.extracted_count}"
        )
    if expect_total is not None and expect_total != stats.total_rows:
        problems.append(
            f"claimed {expect_total} rows but {stats.source} contains {stats.total_rows}"
        )
    if problems:
        raise RunClaimMismatch("; ".join(problems))
