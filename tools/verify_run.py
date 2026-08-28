"""Audit a downloaded export and check any figure claimed about it.

The counting itself lives in `app.run_stats`, which is also what the
dashboard renders from -- deliberately, so the numbers a reviewer sees in
the product and the numbers this script confirms cannot be computed two
different ways and quietly disagree.

Why this exists: the same 12-resume batch was run many times across a few
days, from two different entry points, with differing results as quota and
timeouts varied. A percentage quoted without saying which file it came from
is unverifiable, and a failure count carried over from a previous run is
indistinguishable from a current one. `--expect-failures` turns that from a
discipline problem into a check that exits non-zero.

Usage:
    python tools/verify_run.py <export.csv>
    python tools/verify_run.py <export.csv> --expect-failures 0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.run_stats import (  # noqa: E402
    RunClaimMismatch,
    RunStats,
    assert_claim,
    stats_from_export,
)

__all__ = ["RunClaimMismatch", "RunStats", "assert_claim", "run_stats"]


def run_stats(export_csv: str | Path) -> RunStats:
    """Read a dashboard export and count what actually happened in it."""
    return stats_from_export(export_csv)


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
