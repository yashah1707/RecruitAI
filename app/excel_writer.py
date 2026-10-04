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
from llm.postprocess import (
    DATE_FIELDS_WITH_PRECISION,
    format_degree,
    format_person_name,
    format_phd_evidence,
    format_phd_status,
)
from config import CGPA_PERCENT_MULTIPLIER, CGPA_PERCENT_OFFSET, CONFIDENCE_THRESHOLD
from llm.confidence import evaluate
from llm.interface import FIELD_NAMES, FieldWithConfidence, ResumeRecord

SHEET_NAME = "candidates"

# candidate_name is shown as a single readable column; every other extracted
# field gets its value, confidence and evidence side by side.
#
# has_phd is not exported at all: it is exactly `phd_status == COMPLETED`
# restated as a boolean, so the column only repeated the one beside it. The
# field itself stays -- ranking and the manual-entry panel still read it.
NOT_EXPORTED: frozenset[str] = frozenset({"has_phd"})
_EVIDENCE_FIELDS: tuple[str, ...] = tuple(
    n for n in FIELD_NAMES if n != "candidate_name" and n not in NOT_EXPORTED
)

# Where the column a reviewer sees is spelled out more fully than the schema
# field behind it. The field name stays as it is everywhere in the code, the
# prompt and the answer key; only the exported header changes.
EXPORT_NAMES: dict[str, str] = {"marks_pct": "masters_percentage", "cgpa": "masters_cgpa"}

# Says whether masters_percentage is the resume's own figure or one worked
# out from the CGPA, so a converted number is never mistaken for a stated one.
PERCENTAGE_SOURCE_COLUMN = "masters_percentage_source"
SOURCE_STATED = "stated on resume"
SOURCE_CONVERTED = "converted from CGPA"


def cgpa_to_percentage(cgpa: float | None) -> float | None:
    """A 0-10 grade point as a percentage, by the configured formula.

    Deterministic arithmetic in Python -- the model is never asked to do it.
    Returns None for anything that is not a plausible 10-point CGPA, rather
    than converting a number on some other scale into a confident percentage.
    """
    if cgpa is None or not 0 < cgpa <= 10:
        return None
    pct = (cgpa - CGPA_PERCENT_OFFSET) * CGPA_PERCENT_MULTIPLIER
    return round(min(max(pct, 0.0), 100.0), 2)

# A review reason about a field with no column of its own is reported against
# the column that shows the same fact.
_REASON_COLUMN: dict[str, str] = {"has_phd": "phd_status"}


def export_name(field: str) -> str:
    return EXPORT_NAMES.get(field, field)


# The main sheet is for comparing and shortlisting, so it shows values, not
# the quotes behind them. Only the two columns that carry a course name keep
# a companion column. Every quote is still extracted, grounding-checked and
# used to decide needs_review / review_reasons, and each one remains readable
# in the raw_llm_output sheet for anyone auditing a value.
_KEEPS_COMPANION_COLUMN: frozenset[str] = frozenset({"highest_degree", "phd_status"})
_NO_EVIDENCE_COLUMN: frozenset[str] = frozenset(FIELD_NAMES) - _KEEPS_COMPANION_COLUMN

# These two "evidence" columns no longer hold a raw quote: they are reduced to
# the degree and its course in one house style, so they are headed for what
# they now contain.
EVIDENCE_EXPORT_NAMES: dict[str, str] = {
    "highest_degree": "highest_degree_course_name",
    "phd_status": "phd_course_name",
}


def evidence_column(field: str) -> str:
    return EVIDENCE_EXPORT_NAMES.get(field, f"{field}_evidence")

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
    # Value and evidence only. The per-field confidence number is not shown:
    # it still drives needs_review / review_reasons and the highlight on a
    # doubtful value, which is the part a reviewer acts on.
    *(
        col
        for name in _EVIDENCE_FIELDS
        for col in (
            ((export_name(name), PERCENTAGE_SOURCE_COLUMN) if name == "marks_pct" else (export_name(name),))
            if name in _NO_EVIDENCE_COLUMN
            else (export_name(name), evidence_column(name))
        )
    ),
    # Required by the failure-handling rule: a file that could not be read
    # still produces a row, and says why.
    "parse_error",
    # Why a failed row failed -- api_unavailable and quota are worth re-running,
    # unreadable (scanned/corrupt) and bad_config are not.
    "failure_kind",
    # Which model produced the row; a lighter-fallback row is also routed to
    # review (see llm.confidence).
    "extraction_model",
)

EVIDENCE_COLUMNS: tuple[str, ...] = tuple(
    evidence_column(n) for n in _EVIDENCE_FIELDS if n not in _NO_EVIDENCE_COLUMN
)

LOW_CONFIDENCE_FILL = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
HEADER_FILL = PatternFill("solid", start_color="E8E8E8", end_color="E8E8E8")

_WIDTHS: dict[str, int] = {
    "rank": 7,
    PERCENTAGE_SOURCE_COLUMN: 26,
    "highest_degree_course_name": 44,
    "phd_course_name": 44,
    "masters_percentage": 19,
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
_DEFAULT_WIDTHS = {"": 16, "_evidence": 52}

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
        # Named as the column is headed, so the reviewer can find it.
        field_name = export_name(_REASON_COLUMN.get(field_name, field_name))
        part = f"{field_name} ({code.replace('_', ' ')})" if code else field_name
        if part not in parts:
            parts.append(part)
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
    row["failure_kind"] = record.failure_kind

    result = record.result
    if result is None:
        # Failed row: every extracted field stays empty.
        return row

    # Formatted here as well as in postprocess so results already in the cache
    # (stored before the rule existed) come out in the same form.
    row["candidate_name"] = format_person_name(_clean(result.candidate_name.value))
    row["extraction_model"] = result.model_used
    for name in _EVIDENCE_FIELDS:
        f: FieldWithConfidence = getattr(result, name)
        row[export_name(name)] = _value_for_cell(f)
        if name not in _NO_EVIDENCE_COLUMN:
            row[evidence_column(name)] = _clean(f.evidence)

    # Degree and course in one house style ("M.Tech Computer Engineering"),
    # so the column reads uniformly across resumes. The model's verbatim
    # quote is what was grounding-checked; it remains in raw_llm_output.
    row[evidence_column("highest_degree")] = _clean(
        _fuller_course_name(format_degree(result.highest_degree.evidence), result, result.highest_degree.value)
    )

    # Every candidate with Master's marks gets a percentage: the resume's own
    # where it gives one, otherwise worked out from the CGPA. The source
    # column says which, and the CGPA itself stays in its own column.
    pct_col = export_name("marks_pct")
    if row[pct_col] is not None:
        row[PERCENTAGE_SOURCE_COLUMN] = SOURCE_STATED
    else:
        converted = cgpa_to_percentage(result.cgpa.value)
        if converted is not None:
            row[pct_col] = converted
            row[PERCENTAGE_SOURCE_COLUMN] = SOURCE_CONVERTED

    # PhD status in readable words ("Thesis Submitted", "NA") and its quote
    # reduced to "Ph.D. <Course>", matching the degree column's style.
    row["phd_status"] = format_phd_status(result.phd_status.value)
    phd_course = format_phd_evidence(result.phd_status.value, result.phd_status.evidence)
    if result.phd_status.value != "NOT_APPLICABLE":
        phd_course = _fuller_course_name(phd_course, result, "PhD")
    row[evidence_column("phd_status")] = _clean(phd_course)

    # Show a partially-known date as what the resume actually said. Writing
    # date(2015, 1, 1) for a bare "2015" asserts a January 1st the source
    # never mentioned, and nothing in the row lets a reviewer tell that apart
    # from a genuinely day-accurate date.
    for name in DATE_FIELDS_WITH_PRECISION:
        row[name] = date_as_stated(getattr(result, name).value, getattr(result, f"{name}_precision", None))
    return row


# --- the one date convention -------------------------------------------------
#
# Every date in the workbook and the dashboard is written day-month-year:
#   full date    DD-MM-YYYY        e.g. 12-11-2019
#   month known  MM-YYYY           e.g. 05-2021
#   year only    YYYY              e.g. 2015
#   timestamps   DD-MM-YYYY HH:MM  e.g. 04-10-2026 12:54
# A date is never padded out to a day or month the resume did not state.
DATE_FORMAT = "%d-%m-%Y"
MONTH_FORMAT = "%m-%Y"
YEAR_FORMAT = "%Y"
DATETIME_FORMAT = "%d-%m-%Y %H:%M"
EXCEL_DATE_FORMAT = "dd-mm-yyyy"
EXCEL_DATETIME_FORMAT = "dd-mm-yyyy hh:mm"


def _fuller_course_name(current: str | None, result: Any, level: str | None) -> str | None:
    """Prefer the education list's course name when it says more.

    The one-line quote behind this column sometimes stops at the degree
    ("M.Tech") while the same resume's education entry carries the course
    ("M.Tech Computer Science"). Both come from the resume; the main sheet
    should not show less than the education sheet does. Only an entry for the
    same degree is used, and only if it is longer -- it never replaces one
    degree with another.
    """
    degree = (current or "").split(" ")[0]
    best = current
    for entry in getattr(result, "education", None) or []:
        if entry.level != level or not entry.found_in_resume:
            continue
        candidate = format_degree(" ".join(p for p in (entry.degree, entry.course) if p))
        if not candidate:
            continue
        if (not degree or candidate.split(" ")[0] == degree) and len(candidate) > len(best or ""):
            best = candidate
    return best


def date_as_stated(value: date | None, precision: str | None) -> Any:
    """A date trimmed to what the resume actually gave.

    A full date stays a real date (so the Excel column sorts and filters
    chronologically; the cell format shows it as DD-MM-YYYY). A partial one
    becomes text: "05-2021" or "2015".
    """
    if value is None or precision in (None, "full"):
        return value
    return value.strftime(YEAR_FORMAT) if precision == "year" else value.strftime(MONTH_FORMAT)


def date_text(value: Any) -> Any:
    """Any date-like cell value as text in the house convention, for display
    where there is no cell format to do it (the dashboard table)."""
    if isinstance(value, datetime):
        return value.strftime(DATETIME_FORMAT)
    if isinstance(value, date):
        return value.strftime(DATE_FORMAT)
    return value


def rows_for_display(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with every date written out as text in the house convention."""
    return [{k: date_text(v) for k, v in row.items()} for row in rows]


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
    doubtful = {
        _clean(rec.source_filename): {
            name for name in _EVIDENCE_FIELDS if getattr(rec.result, name).confidence < threshold
        }
        for rec in records
        if rec.result is not None
    }
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=index["processed_at"]).number_format = EXCEL_DATETIME_FORMAT
        ws.cell(row=r, column=index["score"]).number_format = "0.0"
        completeness = ws.cell(row=r, column=index["score_completeness"])
        completeness.number_format = "0%"
        if isinstance(completeness.value, (int, float)) and completeness.value < MIN_TRUSTWORTHY_COMPLETENESS:
            # Most of this candidate's score inputs were missing, so the rank
            # reflects absent data more than it reflects the candidate.
            completeness.fill = INCOMPLETE_SCORE_FILL
            ws.cell(row=r, column=index["score"]).fill = INCOMPLETE_SCORE_FILL
        for name in _EVIDENCE_FIELDS:
            cell = ws.cell(row=r, column=index[export_name(name)])
            if isinstance(cell.value, (date, datetime)):
                cell.number_format = EXCEL_DATE_FORMAT
            # A stated value below the routing threshold is highlighted in
            # place, so a reviewer spots it without a confidence column.
            # Blank cells are left alone: absent is not the same as doubtful.
            filename = ws.cell(row=r, column=index["source_filename"]).value
            # A converted percentage is only as doubtful as the CGPA it came from.
            source = "cgpa" if (
                name == "marks_pct"
                and ws.cell(row=r, column=index[PERCENTAGE_SOURCE_COLUMN]).value == SOURCE_CONVERTED
            ) else name
            if cell.value is not None and source in doubtful.get(filename, ()):
                cell.fill = LOW_CONFIDENCE_FILL

    last_col = get_column_letter(len(COLUMNS))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{last_col}{max(ws.max_row, 1)}"
    for name, i in index.items():
        ws.column_dimensions[get_column_letter(i)].width = _width_for(name)

    # Imported here: detail_sheets builds on this module's styles and helpers.
    from app.detail_sheets import add_detail_sheets

    add_detail_sheets(wb, records)
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
        "academic_marks": "the Master's percentage (0-100) or CGPA (0-10) as stated on the resume, whichever it gives; they share this budget. The score uses the stated figure on its own scale, not the converted percentage shown in masters_percentage",
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
    ws.append(["How masters_percentage is filled"])
    ws[ws.max_row][0].font = Font(bold=True)
    ws.append([
        "Where the resume states a percentage for the Master's degree, that figure is shown "
        f"({PERCENTAGE_SOURCE_COLUMN} = '{SOURCE_STATED}')."
    ])
    ws.append([
        "Where it states only a CGPA, the percentage is calculated as "
        f"(CGPA - {CGPA_PERCENT_OFFSET:g}) x {CGPA_PERCENT_MULTIPLIER:g} "
        f"({PERCENTAGE_SOURCE_COLUMN} = '{SOURCE_CONVERTED}'). Universities use different "
        "conversion formulas, so this is an approximation, not the candidate's official percentage."
    ])
    ws.append([])
    ws.append(["A field the resume never states is EXCLUDED from the score, not counted as zero."])
    ws.append(["score_completeness shows how many of the 5 components actually had data."])
    ws.append(["A low completeness means the score reflects missing data more than the candidate."])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 62
