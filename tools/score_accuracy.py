"""Score an extraction run against the human-filled answer key.

This is the measurement the project otherwise lacks: the grounding check
proves the model quoted the resume honestly, but only a human-labelled key
can say whether the extracted *value* was right.

Usage:
    python tools/score_accuracy.py answer_key.xlsx <extraction_export.csv>

Rows whose correct_value is UNCLEAR are excluded from the denominator rather
than counted as failures — an unlabelled row is missing evidence about the
system, not evidence of a mistake.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook

from tools.verify_run import run_stats

UNCLEAR = "UNCLEAR"

# Deliberately no "1"/"0" here. They are how Excel sometimes renders a
# boolean, but they are also real values for teaching_years_raw and
# publications_count -- and mapping a numeric 0 to "false" made a correct
# "0 teaching years" score as a mismatch against "0.0".
_TRUE = {"true", "yes", "t"}
_FALSE = {"false", "no", "f"}


def normalize(value: str) -> str:
    """Compare on meaning, not formatting.

    The same fact can be written many ways across a spreadsheet and a CSV
    ("TRUE"/"true", "8"/"8.0", "2015-06-01"/"2015-06-01 00:00:00"), and none
    of those differences are extraction errors.
    """
    v = (value or "").strip()
    if not v:
        return ""
    low = v.lower()
    if low in _TRUE:
        return "true"
    if low in _FALSE:
        return "false"
    # numbers: 8 == 8.0
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except ValueError:
        pass
    # dates: drop any time component
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]
    return low


def values_match(expected: str, got: str) -> bool:
    """Whether two recorded values mean the same thing.

    Dates are compared at whichever precision is coarser. The extractor now
    reports a bare year as "2015" instead of fabricating "2015-01-01", and
    answer keys written before that change still hold the fabricated form --
    but a key saying "2015-01-01" for a resume that only ever said "2015"
    means the year, so scoring them as different would penalise the fix that
    stopped inventing the month and day.
    """
    a, b = normalize(expected), normalize(got)
    if a == b:
        return True
    if _is_dateish(a) and _is_dateish(b):
        return a[: min(len(a), len(b))] == b[: min(len(a), len(b))]
    return False


def _is_dateish(v: str) -> bool:
    """A full date, a year-month, or a bare year."""
    return bool(re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", v))


def load_key(path: Path) -> dict[tuple[str, str], str]:
    wb = load_workbook(path, data_only=True)
    ws = wb["answer_key"]
    headers = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(headers)}
    key: dict[tuple[str, str], str] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[idx["source_filename"]]:
            continue
        correct = row[idx["correct_value"]]
        correct = "" if correct is None else str(correct)
        key[(str(row[idx["source_filename"]]), str(row[idx["field"]]))] = correct
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("answer_key", type=Path)
    ap.add_argument("export_csv", type=Path)
    args = ap.parse_args()

    key = load_key(args.answer_key)
    with open(args.export_csv, newline="", encoding="utf-8") as f:
        rows = {r["source_filename"]: r for r in csv.DictReader(f)}

    per_field: dict[str, list[int]] = defaultdict(list)
    per_file: dict[str, list[int]] = defaultdict(list)
    skipped = 0
    mismatches: list[tuple[str, str, str, str]] = []
    # A file that never extracted (unreadable PDF, API quota exhausted) has no
    # values to be right or wrong about. Counting its blanks as wrong answers
    # would conflate "the extractor was unavailable" with "the model misread
    # the resume" — different problems, different fixes.
    # Counted by app.run_stats, the same code the dashboard renders from.
    # Re-deriving it here is how the product and the audit drifted apart once
    # already. One call, reused for the header further down.
    stats = run_stats(args.export_csv)
    failed_files = set(stats.failed)

    for (filename, field), expected in key.items():
        if expected.strip().upper() == UNCLEAR:
            skipped += 1
            continue
        if filename in failed_files:
            continue
        row = rows.get(filename)
        if row is None:
            continue
        actual = row.get(field, "")
        ok = int(values_match(expected, actual))
        per_field[field].append(ok)
        per_file[filename].append(ok)
        if not ok:
            mismatches.append((filename, field, expected, actual))

    def pct(hits: list[int]) -> str:
        return f"{sum(hits)}/{len(hits)}" + f"  {100 * sum(hits) / len(hits):5.0f}%" if hits else "  n/a"

    # Counts come from the file itself, never from a caller's memory of the
    # run: the same batch gets re-run often, and a percentage quoted without
    # its source file is unverifiable.
    print(f"\nAccuracy vs answer key: {args.export_csv.name}")
    print("=" * 58)
    print(stats.summary())
    print("-" * 58)
    print(f"{'FIELD':<26}{'CORRECT':>14}")
    print("-" * 58)
    for field in sorted(per_field, key=lambda f: sum(per_field[f]) / len(per_field[f])):
        print(f"{field:<26}{pct(per_field[field]):>14}")

    all_hits = [h for hits in per_field.values() for h in hits]
    print("-" * 58)
    print(f"{'OVERALL':<26}{pct(all_hits):>14}")
    if skipped:
        print(f"\n({skipped} field(s) marked UNCLEAR were excluded from scoring)")
    print(f"\nscored {len(per_file)} resume(s)")
    if failed_files:
        print(f"excluded {len(failed_files)} that failed to extract at all (not a value error):")
        for fn in sorted(failed_files):
            print(f"   - {fn}")

    if mismatches:
        print(f"\nMismatches ({len(mismatches)}):")
        for filename, field, expected, actual in mismatches[:40]:
            print(f"  {filename[:34]:<36}{field:<22} expected={expected or '(blank)'!s:<18} got={actual or '(blank)'}")
        if len(mismatches) > 40:
            print(f"  ... and {len(mismatches) - 40} more")


if __name__ == "__main__":
    main()
