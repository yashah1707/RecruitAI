"""FakeProvider -> excel_writer, verified by reopening the written workbook.

No model runs in this file.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from app.excel_writer import (
    COLUMNS,
    CONFIDENCE_COLUMNS,
    EVIDENCE_COLUMNS,
    FORBIDDEN_COLUMN_NAMES,
    SHEET_NAME,
    build_row,
    write_workbook,
)
from llm.interface import FIELD_NAMES, ResumeRecord
from llm.providers.fake_provider import FakeProvider

THRESHOLD = 0.7


@pytest.fixture
def records() -> list[ResumeRecord]:
    """Three fake extractions plus one file that never reached the model."""
    provider = FakeProvider()
    recs = [
        ResumeRecord(
            source_filename=f"synthetic_{i}.pdf",
            processed_at=datetime(2026, 8, 22, 10, 30, i),
            result=provider.extract_fields("resume text"),
        )
        for i in range(3)
    ]
    recs.append(ResumeRecord.failed("scanned_image.pdf", "no extractable text in file"))
    return recs


@pytest.fixture
def sheet(records, tmp_path):
    path = write_workbook(records, tmp_path / "out.xlsx", threshold=THRESHOLD)
    return load_workbook(path)[SHEET_NAME]


def header(sheet) -> list[str]:
    return [c.value for c in sheet[1]]


def column_index(sheet, name: str) -> int:
    return header(sheet).index(name) + 1


def test_header_matches_the_declared_column_order(sheet):
    assert header(sheet) == list(COLUMNS)


def test_every_extracted_field_has_confidence_and_evidence_columns(sheet):
    names = header(sheet)
    for field in FIELD_NAMES:
        assert field in names
        if field == "candidate_name":
            continue
        assert f"{field}_confidence" in names
        assert f"{field}_evidence" in names


def test_no_rank_score_or_priority_column(sheet):
    """A computed ordering column is out of scope by design, not by oversight."""
    for name in header(sheet):
        assert name.lower() not in FORBIDDEN_COLUMN_NAMES
        assert "rank" not in name.lower()
        assert "score" not in name.lower()
        assert "priority" not in name.lower()


def test_one_row_per_uploaded_file_including_the_failed_one(sheet, records):
    assert sheet.max_row == len(records) + 1
    filenames = [sheet.cell(row=r, column=1).value for r in range(2, sheet.max_row + 1)]
    assert filenames == [r.source_filename for r in records]


def test_header_row_is_frozen_and_bold_with_autofilter(sheet):
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None
    assert sheet.auto_filter.ref.startswith("A1:")
    assert sheet.auto_filter.ref.endswith(str(sheet.max_row))
    assert all(cell.font.bold for cell in sheet[1])


def test_column_widths_are_set_not_left_at_default(sheet):
    widths = sheet.column_dimensions
    for i in range(1, len(COLUMNS) + 1):
        letter = widths[list(widths)[0]].__class__  # noqa: F841 - keep openpyxl import local
    from openpyxl.utils import get_column_letter

    for i in range(1, len(COLUMNS) + 1):
        dim = sheet.column_dimensions[get_column_letter(i)]
        assert dim.width and dim.width > 8


def test_low_confidence_cells_are_filled_and_high_confidence_ones_are_not(sheet):
    filled, unfilled = 0, 0
    for name in CONFIDENCE_COLUMNS:
        col = column_index(sheet, name)
        for r in range(2, sheet.max_row + 1):
            cell = sheet.cell(row=r, column=col)
            has_fill = cell.fill is not None and cell.fill.start_color.rgb == "00FFF2CC"
            if cell.value is not None and cell.value < THRESHOLD:
                assert has_fill, f"{name} row {r} below threshold but not highlighted"
                filled += 1
            elif cell.value is not None:
                assert not has_fill, f"{name} row {r} above threshold but highlighted"
                unfilled += 1
    assert filled > 0 and unfilled > 0


def test_needs_review_is_a_real_boolean_not_a_string(sheet):
    col = column_index(sheet, "needs_review")
    values = [sheet.cell(row=r, column=col).value for r in range(2, sheet.max_row + 1)]
    assert all(isinstance(v, bool) for v in values)
    assert True in values and False in values


def test_failed_file_row_is_empty_with_a_parse_error_and_needs_review(sheet):
    last = sheet.max_row
    row = {name: sheet.cell(row=last, column=i + 1).value for i, name in enumerate(COLUMNS)}
    assert row["source_filename"] == "scanned_image.pdf"
    assert row["needs_review"] is True
    assert row["parse_error"] == "no extractable text in file"
    assert row["candidate_name"] is None
    assert all(row[c] == 0.0 for c in CONFIDENCE_COLUMNS)
    assert all(row[c] is None for c in EVIDENCE_COLUMNS)


def test_dates_are_written_as_dates_so_the_column_sorts_chronologically(sheet):
    col = column_index(sheet, "masters_award_date")
    values = [sheet.cell(row=r, column=col).value for r in range(2, sheet.max_row + 1)]
    written = [v for v in values if v is not None]
    assert written, "expected at least one date from the canned results"
    assert all(isinstance(v, (date, datetime)) for v in written)


def test_absent_field_writes_blank_value_and_zero_confidence():
    """Absent means empty plus 0.0 — never a plausible-looking default."""
    record = ResumeRecord(
        source_filename="a.pdf",
        processed_at=datetime(2026, 8, 22),
        result=FakeProvider().extract_fields("x"),
    )
    row = build_row(record, threshold=THRESHOLD)
    assert row["phd_award_date"] is None
    assert row["phd_award_date_confidence"] == 0.0
    assert row["phd_award_date_evidence"] is None


def test_set_without_state_row_is_flagged_for_review(sheet):
    """The second canned result claims SET with no grounded state."""
    col = column_index(sheet, "needs_review")
    status_col = column_index(sheet, "net_set_status")
    for r in range(2, sheet.max_row + 1):
        if sheet.cell(row=r, column=status_col).value in ("SET", "SLET"):
            state = sheet.cell(row=r, column=column_index(sheet, "set_state")).value
            if state is None:
                assert sheet.cell(row=r, column=col).value is True


def test_evidence_text_is_carried_through_verbatim(sheet):
    col = column_index(sheet, "net_set_status_evidence")
    values = [sheet.cell(row=r, column=col).value for r in range(2, sheet.max_row + 1)]
    assert any(v and "UGC-NET" in v for v in values)


def test_review_reasons_column_exists_and_follows_needs_review():
    """needs_review says *that* a row needs checking; review_reasons says
    *which fields*, so a reviewer isn't hunting across 30+ columns."""
    from app.excel_writer import COLUMNS

    assert "review_reasons" in COLUMNS
    assert COLUMNS.index("review_reasons") == COLUMNS.index("needs_review") + 1


def test_review_reasons_names_the_failing_fields():
    from datetime import datetime

    from app.excel_writer import build_row
    from llm.interface import ResumeRecord
    from tests.test_confidence_routing import _ok, build

    result = build(publications_count=_ok(3, 0.95, None))  # claimed, unquoted
    record = ResumeRecord(
        source_filename="cv.pdf", processed_at=datetime.now(), result=result
    )
    row = build_row(record)
    assert row["needs_review"] is True
    assert "publications_count" in row["review_reasons"]
    assert "no evidence" in row["review_reasons"]


def test_review_reasons_is_blank_for_a_clean_row():
    from datetime import datetime

    from app.excel_writer import build_row
    from llm.interface import ResumeRecord
    from tests.test_confidence_routing import build

    record = ResumeRecord(
        source_filename="cv.pdf", processed_at=datetime.now(), result=build()
    )
    row = build_row(record)
    assert row["needs_review"] is False
    assert row["review_reasons"] is None


def test_review_reasons_carries_no_extracted_values():
    """This column gets shared with reviewers; it must name fields and rules,
    never the candidate's data."""
    from datetime import datetime

    from app.excel_writer import build_row
    from llm.interface import ResumeRecord
    from tests.test_confidence_routing import _ok, build

    result = build(
        net_set_status=_ok("SET"), set_state=_ok("Maharashtra", 0.2)
    )
    record = ResumeRecord(
        source_filename="cv.pdf", processed_at=datetime.now(), result=result
    )
    row = build_row(record)
    assert "Maharashtra" not in (row["review_reasons"] or "")
