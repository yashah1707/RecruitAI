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


def test_rank_and_score_columns_exist_and_lead_the_sheet(sheet):
    """Ranking was added on an explicit instruction, overriding the original
    no-leaderboard design. See app/ranking.py for what the number is not."""
    names = header(sheet)
    assert names[:3] == ["rank", "score", "score_completeness"]


def test_rows_are_ordered_by_rank(sheet):
    ranks = [sheet.cell(row=r, column=1).value for r in range(2, sheet.max_row + 1)]
    present = [r for r in ranks if r is not None]
    assert present == sorted(present)
    assert present == list(range(1, len(present) + 1))


def test_score_completeness_travels_with_every_score(sheet):
    """A score without its completeness is the misleading form of this column:
    it hides that the number may rest on mostly-missing data."""
    names = header(sheet)
    si, ci = names.index("score") + 1, names.index("score_completeness") + 1
    for r in range(2, sheet.max_row + 1):
        if sheet.cell(row=r, column=si).value is not None:
            assert sheet.cell(row=r, column=ci).value is not None


def test_needs_review_still_present_alongside_rank(sheet):
    """A high rank on unverified data must stay visibly unverified."""
    assert "needs_review" in header(sheet)
    assert "review_reasons" in header(sheet)


def test_workbook_carries_the_ranking_disclaimer(records, tmp_path):
    """The caveat has to survive the file being forwarded to someone who
    never saw the app."""
    from openpyxl import load_workbook

    from app.ranking import RANKING_DISCLAIMER

    path = write_workbook(records, tmp_path / "d.xlsx", threshold=THRESHOLD)
    wb = load_workbook(path)
    assert "how_scoring_works" in wb.sheetnames
    explain = " ".join(
        str(c.value) for row in wb["how_scoring_works"].iter_rows() for c in row if c.value
    )
    assert "not a hiring decision" in explain
    note = wb[SHEET_NAME].cell(row=1, column=1).comment
    assert note is not None and "not a hiring decision" in note.text


def test_one_row_per_uploaded_file_including_the_failed_one(sheet, records):
    """Every uploaded file gets a row. Order is by rank now, not upload
    order, so compare as sets."""
    assert sheet.max_row == len(records) + 1
    names = header(sheet)
    fi = names.index("source_filename") + 1
    filenames = [sheet.cell(row=r, column=fi).value for r in range(2, sheet.max_row + 1)]
    assert sorted(filenames) == sorted(r.source_filename for r in records)


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

    # "rank" is a 1-2 digit column; a wide one would just be padding.
    narrow = {"rank", "score"}
    for i in range(1, len(COLUMNS) + 1):
        dim = sheet.column_dimensions[get_column_letter(i)]
        floor = 5 if COLUMNS[i - 1] in narrow else 8
        assert dim.width and dim.width > floor


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


# --- date precision is disclosed, not fabricated (P2) -----------------------

def test_a_year_only_date_is_not_written_as_a_full_iso_date(tmp_path):
    """A bare "2015" parses to date(2015,1,1) and then reads in the export
    exactly like a day-accurate date. The output must not assert a January
    1st the resume never mentioned."""
    from datetime import datetime as _dt

    from app.excel_writer import build_row
    from tests.test_confidence_routing import _ok, build

    result = build(masters_award_date=_ok(date(2015, 1, 1), 0.85, "M.Tech 2015"))
    result.masters_award_date_precision = "year"
    row = build_row(
        ResumeRecord(source_filename="cv.pdf", processed_at=_dt.now(), result=result)
    )
    assert row["masters_award_date"] == "2015"


def test_a_month_precision_date_shows_year_and_month_only(tmp_path):
    from datetime import datetime as _dt

    from app.excel_writer import build_row
    from tests.test_confidence_routing import _ok, build

    result = build(masters_award_date=_ok(date(2021, 5, 1), 0.9, "Graduated: May 2021"))
    result.masters_award_date_precision = "month"
    row = build_row(
        ResumeRecord(source_filename="cv.pdf", processed_at=_dt.now(), result=result)
    )
    assert row["masters_award_date"] == "2021-05"


def test_a_genuinely_full_date_is_still_written_as_a_date(tmp_path):
    from datetime import datetime as _dt

    from app.excel_writer import build_row
    from tests.test_confidence_routing import _ok, build

    result = build(masters_award_date=_ok(date(2019, 11, 12), 0.9, "awarded 12 November 2019"))
    result.masters_award_date_precision = "full"
    row = build_row(
        ResumeRecord(source_filename="cv.pdf", processed_at=_dt.now(), result=result)
    )
    assert row["masters_award_date"] == date(2019, 11, 12)


# --- raw_llm_output is retrievable for audit (P2) ---------------------------

def test_raw_llm_output_is_retrievable_from_the_workbook(records, tmp_path):
    """The parsed columns are the product of a prompt, a schema and several
    post-processing rules. Only the model's own response distinguishes "the
    model said something false" from "we mangled something true"."""
    from openpyxl import load_workbook

    path = write_workbook(records, tmp_path / "out.xlsx", threshold=THRESHOLD)
    wb = load_workbook(path)
    assert "raw_llm_output" in wb.sheetnames
    sheet = wb["raw_llm_output"]
    filenames = [c.value for c in sheet["A"][1:]]
    for rec in records:
        assert rec.source_filename in filenames


def test_a_failed_extraction_still_gets_a_raw_output_row(tmp_path):
    from datetime import datetime as _dt

    from openpyxl import load_workbook

    recs = [ResumeRecord.failed("scanned.pdf", "no extractable text")]
    path = write_workbook(recs, tmp_path / "out.xlsx", threshold=THRESHOLD)
    sheet = load_workbook(path)["raw_llm_output"]
    assert sheet["A2"].value == "scanned.pdf"
    assert sheet["B2"].value  # carries the reason rather than being blank
