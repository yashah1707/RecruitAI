"""Turn a run's ResumeRecords into one formatted workbook.

One row per uploaded file, successfully parsed or not. The sheet is built so a
human can sort and filter it themselves on whichever column they care about —
there is deliberately no rank, score or priority column, because ordering
candidates by a computed number misrepresents an interview-decided process.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from config import CONFIDENCE_THRESHOLD
from llm.confidence import evaluate
from llm.interface import FIELD_NAMES, FieldWithConfidence, ResumeRecord

SHEET_NAME = "candidates"

# candidate_name is shown as a single readable column; every other extracted
# field gets its value, confidence and evidence side by side.
_EVIDENCE_FIELDS: tuple[str, ...] = tuple(n for n in FIELD_NAMES if n != "candidate_name")

# Column names mirror the eventual extracted_data field names, so moving this
# to Postgres later is a rename and not a redesign.
COLUMNS: tuple[str, ...] = (
    "source_filename",
    "processed_at",
    "candidate_name",
    "needs_review",
    # Sits next to needs_review because it's that column's explanation: which
    # specific fields a reviewer must check. Without it a TRUE means hunting
    # across 30+ columns for the blank evidence cell. The LLM-layer plan
    # (section 9) requires surfacing "only the specific low-confidence
    # fields... not the whole record", which needs a column to surface into.
    "review_reasons",
    *(
        col
        for name in _EVIDENCE_FIELDS
        for col in (name, f"{name}_confidence", f"{name}_evidence")
    ),
    # Required by the failure-handling rule: a file that could not be read
    # still produces a row, and says why.
    "parse_error",
)

CONFIDENCE_COLUMNS: tuple[str, ...] = tuple(c for c in COLUMNS if c.endswith("_confidence"))
EVIDENCE_COLUMNS: tuple[str, ...] = tuple(c for c in COLUMNS if c.endswith("_evidence"))

# Names that would turn this sheet into a leaderboard. Asserted against in the
# tests so nobody reintroduces one by accident.
FORBIDDEN_COLUMN_NAMES: frozenset[str] = frozenset(
    {"rank", "ranking", "score", "total_score", "priority", "grade", "position", "shortlist"}
)

LOW_CONFIDENCE_FILL = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
HEADER_FILL = PatternFill("solid", start_color="E8E8E8", end_color="E8E8E8")

_WIDTHS: dict[str, int] = {
    "source_filename": 30,
    "processed_at": 20,
    "review_reasons": 46,
    "candidate_name": 26,
    "needs_review": 13,
    "parse_error": 34,
}
_DEFAULT_WIDTHS = {"": 16, "_confidence": 12, "_evidence": 52}

_MAX_EVIDENCE_CHARS = 500


def _clean(text: str | None) -> str | None:
    """Strip characters openpyxl refuses to write, and cap evidence length."""
    if text is None:
        return None
    cleaned = ILLEGAL_CHARACTERS_RE.sub(" ", str(text)).strip()
    if len(cleaned) > _MAX_EVIDENCE_CHARS:
        cleaned = cleaned[: _MAX_EVIDENCE_CHARS - 1].rstrip() + "…"
    return cleaned or None


def _format_reasons(reasons: list[str]) -> str | None:
    """Turn routing reason codes into something an HR reviewer can read.

    "publications_count:no_evidence" -> "publications_count (no evidence)".
    Field names are kept verbatim so the reviewer can find the column; only
    the rule code is humanised. Carries no extracted values, so this column
    is safe to share.
    """
    if not reasons:
        return None
    parts = []
    for reason in reasons:
        field_name, _, code = reason.partition(":")
        parts.append(f"{field_name} ({code.replace('_', ' ')})" if code else field_name)
    return "; ".join(parts)


def _value_for_cell(f: FieldWithConfidence) -> Any:
    value = f.value
    if isinstance(value, (date, datetime, bool, int, float)) or value is None:
        return value
    return _clean(str(value))


def build_row(record: ResumeRecord, threshold: float = CONFIDENCE_THRESHOLD) -> dict[str, Any]:
    """One record -> one flat dict keyed by COLUMNS."""
    outcome = evaluate(record.result, record.parse_error, threshold)
    row: dict[str, Any] = {c: None for c in COLUMNS}
    row["source_filename"] = _clean(record.source_filename)
    row["processed_at"] = record.processed_at
    row["needs_review"] = bool(outcome.needs_review)
    row["review_reasons"] = _format_reasons(outcome.reasons)
    row["parse_error"] = _clean(record.parse_error)

    result = record.result
    if result is None:
        # Failed row: every extracted field stays empty at confidence 0.0.
        for name in _EVIDENCE_FIELDS:
            row[f"{name}_confidence"] = 0.0
        return row

    row["candidate_name"] = _clean(result.candidate_name.value)
    for name in _EVIDENCE_FIELDS:
        f: FieldWithConfidence = getattr(result, name)
        row[name] = _value_for_cell(f)
        row[f"{name}_confidence"] = round(float(f.confidence), 3)
        row[f"{name}_evidence"] = _clean(f.evidence)
    return row


def build_rows(
    records: Iterable[ResumeRecord], threshold: float = CONFIDENCE_THRESHOLD
) -> list[dict[str, Any]]:
    return [build_row(r, threshold) for r in records]


def _width_for(column: str) -> int:
    if column in _WIDTHS:
        return _WIDTHS[column]
    for suffix, width in _DEFAULT_WIDTHS.items():
        if suffix and column.endswith(suffix):
            return width
    return _DEFAULT_WIDTHS[""]


def write_workbook(
    records: Sequence[ResumeRecord],
    path: str | Path,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> Path:
    """Write the formatted workbook and return the path it was written to."""
    path = Path(path)
    wb = build_workbook(records, threshold)
    wb.save(path)
    return path


def build_workbook(
    records: Sequence[ResumeRecord], threshold: float = CONFIDENCE_THRESHOLD
) -> Workbook:
    rows = build_rows(records, threshold)

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    ws.append(list(COLUMNS))
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center")

    for row in rows:
        ws.append([row[c] for c in COLUMNS])

    index = {name: i + 1 for i, name in enumerate(COLUMNS)}
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=index["processed_at"]).number_format = "yyyy-mm-dd hh:mm:ss"
        for name in _EVIDENCE_FIELDS:
            cell = ws.cell(row=r, column=index[name])
            if isinstance(cell.value, (date, datetime)):
                cell.number_format = "yyyy-mm-dd"
            conf = ws.cell(row=r, column=index[f"{name}_confidence"])
            conf.number_format = "0.00"
            # Below the routing threshold, so a reviewer spots it without
            # opening the evidence column.
            if isinstance(conf.value, (int, float)) and conf.value < threshold:
                conf.fill = LOW_CONFIDENCE_FILL

    last_col = get_column_letter(len(COLUMNS))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{last_col}{max(ws.max_row, 1)}"
    for name, i in index.items():
        ws.column_dimensions[get_column_letter(i)].width = _width_for(name)

    return wb
