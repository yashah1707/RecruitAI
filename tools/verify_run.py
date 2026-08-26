"""Authoritative run statistics, computed from an export file.

Every number anyone reports about a run -- how many resumes extracted, how
many failed, what the accuracy was -- has to come from here, reading the
actual output file, rather than from whatever a console log said at the time.

Why this exists: the same 12-resume batch was run many times across a few
days, from two different entry points (the dashboard, and a scratch script),
with differing results as quota and timeouts varied. A percentage quoted
without saying which file it came from is unverifiable, and a failure count
carried over from a previous run is indistinguishable from a current one.
`assert_claim` turns that from a discipline problem into a check.

Usage:
    python tools/verify_run.py <export.csv>
    python tools/verify_run.py <export.csv> --expect-failures 0
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class RunClaimMismatch(AssertionError):
    """A stated figure does not match what the output file actually contains."""


@dataclass
class RunStats:
    source: str
    total_rows: int
    failed: list[str] = field(default_factory=list)

    @property
    def failure_count(self) -> int:
        return len(self.failed)

    @property
    def extracted_count(self) -> int:
        return self.total_rows - self.failure_count

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


def run_stats(export_csv: str | Path) -> RunStats:
    """Read a dashboard export and count what actually happened in it."""
    path = Path(export_csv)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    failed = [
        (r.get("source_filename") or "(unnamed)")
        for r in rows
        if (r.get("parse_error") or "").strip()
    ]
    return RunStats(source=path.name, total_rows=len(rows), failed=failed)


def assert_claim(
    stats: RunStats,
    *,
    expect_failures: int | None = None,
    expect_extracted: int | None = None,
    expect_total: int | None = None,
) -> None:
    """Raise unless the stated figures match the file. No silent rounding.

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("export_csv", type=Path)
    ap.add_argument("--expect-failures", type=int, default=None)
    ap.add_argument("--expect-extracted", type=int, default=None)
    ap.add_argument("--expect-total", type=int, default=None)
    args = ap.parse_args()

    stats = run_stats(args.export_csv)
    print(stats.summary())
    try:
        assert_claim(
            stats,
            expect_failures=args.expect_failures,
            expect_extracted=args.expect_extracted,
            expect_total=args.expect_total,
        )
    except RunClaimMismatch as exc:
        print(f"\nCLAIM MISMATCH: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if any(v is not None for v in (args.expect_failures, args.expect_extracted, args.expect_total)):
        print("\nclaims match the file")


if __name__ == "__main__":
    main()
