"""Generate the ground-truth answer-key workbook a human fills in.

The point of the answer key is to answer "how accurate is this system?" with
a number instead of an impression. It is built once, by hand, and then
`tools/score_accuracy.py` measures any extraction run against it.

Usage:
    python tools/make_answer_key.py <extraction_export.csv> [-o answer_key.xlsx]
    python tools/make_answer_key.py <extraction_export.csv> --blank

By default each `correct_*` cell is pre-filled with what the model said, so
the reviewer only edits what is wrong. That is much faster, but it does
invite rubber-stamping — pass --blank for an empty key if you would rather
trade time for a bias-free ground truth.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from llm.interface import FIELD_NAMES

# candidate_name is excluded: it is obvious from the file and not an
# eligibility input, so labelling it costs time and teaches nothing.
LABEL_FIELDS: tuple[str, ...] = tuple(f for f in FIELD_NAMES if f != "candidate_name")

HEADER_FILL = PatternFill("solid", start_color="E8E8E8", end_color="E8E8E8")
EDIT_FILL = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
NOTE_FILL = PatternFill("solid", start_color="DDEBF7", end_color="DDEBF7")

GUIDANCE: dict[str, str] = {
    "highest_degree": "UG / PG / PhD / Post-Doc. Highest degree AWARDED — 'PhD pursuing' is still PG.",
    "marks_pct": "Percentage 0-100 only. If the resume gives CGPA (e.g. 8.2/10), leave blank and note the CGPA.",
    "has_phd": "TRUE only if the PhD is AWARDED. Pursuing / thesis submitted / scholar = FALSE.",
    "phd_award_date": "YYYY-MM-DD. Blank if no PhD awarded.",
    "phd_regulation": "2009 or 2016 only if the resume says so. Usually blank.",
    "masters_award_date": "YYYY-MM-DD. Use the first of the month/year if only a year is given.",
    "net_set_status": "NET / SET / SLET / NONE. NONE if the resume never mentions one.",
    "set_state": "Only if status is SET or SLET — the state that test belongs to.",
    "study_leave_taken": "TRUE/FALSE only if the resume explicitly mentions study leave. Usually blank.",
    "teaching_years_raw": "Total teaching years as stated. Do not adjust for anything.",
    "publications_count": "How many publications are listed.",
}


def build(rows: list[dict], blank: bool) -> Workbook:
    wb = Workbook()

    ws = wb.active
    ws.title = "answer_key"
    headers = ["source_filename", "field", "model_said", "model_evidence", "correct_value", "notes"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL

    for row in rows:
        if row.get("parse_error"):
            continue  # nothing to label on a file that never extracted
        for field in LABEL_FIELDS:
            model_value = (row.get(field) or "").strip()
            evidence = (row.get(f"{field}_evidence") or "").strip()
            ws.append([
                row["source_filename"],
                field,
                model_value,
                evidence[:300],
                "" if blank else model_value,
                "",
            ])

    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=5).fill = EDIT_FILL   # correct_value
        ws.cell(row=r, column=6).fill = NOTE_FILL   # notes
        ws.cell(row=r, column=4).alignment = Alignment(wrap_text=True, vertical="top")

    for col, width in enumerate((38, 22, 16, 60, 18, 30), start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:F{ws.max_row}"

    # A short instructions sheet, so the workbook explains itself if it is
    # opened weeks later or by someone else.
    guide = wb.create_sheet("how_to_fill")
    guide.append(["How to fill in this answer key"])
    guide["A1"].font = Font(bold=True, size=14)
    for line in [
        "",
        "Goal: record what each field SHOULD be, by reading the actual resume.",
        "This becomes the yardstick for measuring extraction accuracy.",
        "",
        "For every row:",
        "  1. Open the resume named in column A.",
        "  2. Check the field in column B against what the resume really says.",
        "  3. Column E (yellow) is your answer:",
        "       - model was right  -> leave it as is",
        "       - model was wrong  -> type the correct value",
        "       - field is genuinely absent from the resume -> leave EMPTY",
        "       - you cannot tell  -> type UNCLEAR (it gets excluded from scoring)",
        "  4. Column F (blue) is optional notes for anything odd.",
        "",
        "IMPORTANT: column E is pre-filled with the model's own answer to save",
        "typing. Do not just skim and accept it -- if you do, the accuracy score",
        "only measures the system against itself and means nothing. Check each",
        "value against the PDF.",
        "",
        "Field-by-field rules:",
    ]:
        guide.append([line])
    for field, rule in GUIDANCE.items():
        guide.append([f"  {field}", rule])
    guide.column_dimensions["A"].width = 30
    guide.column_dimensions["B"].width = 95
    return wb


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("export_csv", help="a CSV exported from the dashboard")
    ap.add_argument("-o", "--out", default="answer_key.xlsx")
    ap.add_argument("--blank", action="store_true", help="leave correct_value empty (avoids anchoring bias)")
    args = ap.parse_args()

    with open(args.export_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    wb = build(rows, blank=args.blank)
    out = Path(args.out)
    wb.save(out)

    labelled = sum(1 for r in rows if not r.get("parse_error"))
    print(f"wrote {out}")
    print(f"  {labelled} resumes x {len(LABEL_FIELDS)} fields = {labelled * len(LABEL_FIELDS)} rows to check")
    print(f"  pre-filled: {'no (blank)' if args.blank else 'yes -- verify each against the PDF'}")


if __name__ == "__main__":
    main()
