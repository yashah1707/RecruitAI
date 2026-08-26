"""Turn a run's ResumeRecords into one formatted workbook.

One row per uploaded file, successfully parsed or not, sorted by rank. The
rank/score columns are a shortlisting heuristic computed deterministically in
app.ranking, never by the LLM -- see app/ranking.py's module docstring for
what this number is and, more importantly, what it is not: a hiring decision.
Every row keeps needs_review and review_reasons next to its rank so unverified
data stays visibly suspect even at the top of the list.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.ranking import RANKING_DISCLAIMER, CandidateScore, rank_candidates
from llm.postprocess import DATE_FIELDS_WITH_PRECISION
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
    "rank",
    "score",
    "score_completeness",
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

LOW_CONFIDENCE_FILL = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
HEADER_FILL = PatternFill("solid", start_color="E8E8E8", end_color="E8E8E8")

_WIDTHS: dict[str, int] = {
    "rank": 7,
    "score": 8,
    "score_completeness": 18,
    "source_filename": 30,
    "processed_at": 20,
    "review_reasons": 46,
    "candidate_name": 26,
    "needs_review": 13,
    "parse_error": 34,
}

# A rank built on data that is mostly missing, or that a human hasn't checked,
# is the specific way this column misleads. Both get flagged in the sheet.
INCOMPLETE_SCORE_FILL = PatternFill("solid", start_color="FCE4D6", end_color="FCE4D6")
MIN_TRUSTWORTHY_COMPLETENESS = 0.6
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


def build_row(
    record: ResumeRecord,
    threshold: float = CONFIDENCE_THRESHOLD,
    scored: CandidateScore | None = None,
) -> dict[str, Any]:
    """One record -> one flat dict keyed by COLUMNS."""
    outcome = evaluate(record.result, record.parse_error, threshold)
    row: dict[str, Any] = {c: None for c in COLUMNS}
    if scored is not None:
        row["rank"] = scored.rank
        row["score"] = scored.score
        row["score_completeness"] = round(scored.completeness, 2)
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

    # Show a partially-known date as what the resume actually said. Writing
    # date(2015, 1, 1) for a bare "2015" asserts a January 1st the source
    # never mentioned, and nothing in the row lets a reviewer tell that apart
    # from a genuinely day-accurate date.
    for name in DATE_FIELDS_WITH_PRECISION:
        precision = getattr(result, f"{name}_precision", None)
        value = getattr(result, name).value
        if value is None or precision in (None, "full"):
            continue
        row[name] = value.strftime("%Y") if precision == "year" else value.strftime("%Y-%m")
    return row


def build_rows(
    records: Iterable[ResumeRecord], threshold: float = CONFIDENCE_THRESHOLD
) -> list[dict[str, Any]]:
    """Rows ordered by rank (best first), failed extractions last.

    Ranking happens here rather than in the caller so the preview table and
    the workbook can never disagree about the order.
    """
    records = list(records)
    scores = {s.source_filename: s for s in rank_candidates(records)}
    rows = [build_row(r, threshold, scores.get(r.source_filename)) for r in records]
    rows.sort(key=lambda row: (row["rank"] is None, row["rank"] or 0))
    return rows


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

    # The caveat has to travel with the file: a workbook gets forwarded and
    # opened long after whoever generated it explained what the number means.
    ws.cell(row=1, column=1).comment = Comment(RANKING_DISCLAIMER, "RecruitAI")

    for row in rows:
        ws.append([row[c] for c in COLUMNS])

    index = {name: i + 1 for i, name in enumerate(COLUMNS)}
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=index["processed_at"]).number_format = "yyyy-mm-dd hh:mm:ss"
        ws.cell(row=r, column=index["score"]).number_format = "0.0"
        completeness = ws.cell(row=r, column=index["score_completeness"])
        completeness.number_format = "0%"
        if isinstance(completeness.value, (int, float)) and completeness.value < MIN_TRUSTWORTHY_COMPLETENESS:
            # Most of this candidate's score inputs were missing, so the rank
            # reflects absent data more than it reflects the candidate.
            completeness.fill = INCOMPLETE_SCORE_FILL
            ws.cell(row=r, column=index["score"]).fill = INCOMPLETE_SCORE_FILL
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

    _add_scoring_sheet(wb)
    _add_raw_output_sheet(wb, records)
    return wb


def _add_raw_output_sheet(wb: Workbook, records: Sequence[ResumeRecord]) -> None:
    """Keep the model's own response alongside the parsed row, for audit.

    The parsed columns are the product of a prompt, a schema and several
    deterministic post-processing rules. When one of those is wrong, the only
    way to tell "the model said something false" from "we mangled something
    true" is to read what the model actually returned. Kept on its own sheet
    rather than in the main view, which is for reviewing candidates.
    """
    ws = wb.create_sheet("raw_llm_output")
    ws.append(["source_filename", "raw_llm_output"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL

    for record in records:
        raw = record.result.raw_llm_output if record.result else None
        ws.append([
            _clean(record.source_filename),
            _truncate_for_cell(raw) if raw else (_clean(record.parse_error) or "(no response)"),
        ])

    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 140
    ws.freeze_panes = "A2"


# Excel refuses to store a cell longer than this.
_MAX_CELL_CHARS = 32767


def _truncate_for_cell(text: str) -> str:
    cleaned = ILLEGAL_CHARACTERS_RE.sub(" ", str(text))
    if len(cleaned) <= _MAX_CELL_CHARS:
        return cleaned
    notice = "... [truncated: exceeded Excel's cell limit]"
    return cleaned[: _MAX_CELL_CHARS - len(notice)] + notice


def _add_scoring_sheet(wb: Workbook) -> None:
    """Spell out the formula in the workbook itself.

    A score nobody can inspect is a score nobody can challenge, which is
    exactly how a made-up heuristic starts getting treated as authoritative.
    """
    ws = wb.create_sheet("how_scoring_works")
    ws.append(["How the rank and score are calculated"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    for line in RANKING_DISCLAIMER.split(". "):
        if line.strip():
            ws.append([line.strip().rstrip(".") + "."])
    ws.append([])
    ws.append(["Component", "Max points", "How it is scored"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
    from app.ranking import DEFAULT_COMPONENTS

    how_by_name = {
        "teaching_years": "0 years -> 0 pts, 20+ years -> full points (linear)",
        "publications": "0 -> 0 pts, 20+ -> full points (linear)",
        "academic_marks": "marks_pct (0-100) or cgpa (0-10), whichever the resume gives; they share this budget and are never converted into each other",
        "phd": "full points if an awarded PhD, 0 otherwise",
        "net_set": "full points for NET/SET/SLET, 0 for NONE",
    }
    for comp in DEFAULT_COMPONENTS:
        ws.append([comp.name, comp.max_points, how_by_name.get(comp.name, "")])
    ws.append([])
    ws.append(["What counts as a publication"])
    ws[ws.max_row][0].font = Font(bold=True)
    ws.append([
        "publications_count counts work that has CLEARED peer review: published, "
        "in press / early access, or explicitly accepted."
    ])
    ws.append([
        "Work still submitted / under review / in preparation is counted separately in "
        "publications_in_progress_count, not dropped."
    ])
    ws.append([
        "Rationale: eligibility for Associate Professor and above requires peer-reviewed "
        "or UGC-listed work, so unreviewed work must not inflate the counted total. "
        "'Accepted' counts because it has passed review, which is the substantive bar."
    ])
    ws.append([
        "Counted: journal articles, conference papers, and book chapters / books. "
        "NOT counted: patents, copyright registrations, professional memberships, "
        "conferences or FDPs attended, reviewer and committee roles, certifications, "
        "awards, projects and grants."
    ])
    ws.append(["Only publications_count feeds the score; the in-progress figure is shown for context."])
    ws.append([])
    ws.append(["A field the resume never states is EXCLUDED from the score, not counted as zero."])
    ws.append(["score_completeness shows how many of the 5 components actually had data."])
    ws.append(["A low completeness means the score reflects missing data more than the candidate."])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 62
