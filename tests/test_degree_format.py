"""highest_degree_evidence is shown as "<degree> <Course>" in one uniform style."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.excel_writer import build_row
from llm.interface import ResumeRecord
from llm.postprocess import format_degree
from llm.providers.fake_provider import CANNED_RESULTS


@pytest.mark.parametrize(
    "raw, expected",
    [
        # every spelling of a degree collapses to one
        ("M.Tech. (Computer)", "M.Tech Computer"),
        ("M. Tech (Computer Engineering)", "M.Tech Computer Engineering"),
        ("MTech Computer Engineering", "M.Tech Computer Engineering"),
        ("Master of Technology in Computer Engineering", "M.Tech Computer Engineering"),
        ("ME. (Computer Engineering)", "M.E. Computer Engineering"),
        ("M.E. Computer Engineering", "M.E. Computer Engineering"),
        ("Master of Engineering", "M.E."),
        ("Masters of Engineering", "M.E."),
        ("Master of Engineering (M.E)", "M.E."),
        ("Ph.D. [Computer Sci. & Engg.]", "Ph.D. Computer Science and Engineering"),
        ("PhD in Computer Science and Engineering", "Ph.D. Computer Science and Engineering"),
        ("Doctor of Philosophy (Mathematics)", "Ph.D. Mathematics"),
        ("M.Sc. Physics", "M.Sc. Physics"),
        ("MCA", "MCA"),
        ("B.E. (Mech. Engg.)", "B.E. Mechanical Engineering"),
        # no "..." and no punctuation left over: a single space
        ("M.Tech ... Computer Science & Engineering", "M.Tech Computer Science and Engineering"),
        ("M.Tech … Computer Science & Engineering", "M.Tech Computer Science and Engineering"),
        ("M.Tech - Computer Science, Engineering", "M.Tech Computer Science Engineering"),
        # case is made uniform
        ("m.tech computer science engineering", "M.Tech Computer Science Engineering"),
        ("M.TECH COMPUTER ENGINEERING", "M.Tech Computer Engineering"),
        # marks, class, year and "honours" are not the course
        ("M.Tech. (Computer) First Class 63.56 2015", "M.Tech Computer"),
        ("M.Tech (Honours)", "M.Tech"),
        ("M.E. Computer Engineering 78.9%", "M.E. Computer Engineering"),
        # acronyms survive
        ("M.Tech VLSI Design", "M.Tech VLSI Design"),
        ("M.Tech (CSE)", "M.Tech Computer Science and Engineering"),
    ],
)
def test_degree_is_written_in_one_style(raw, expected):
    assert format_degree(raw) == expected


def test_no_ellipsis_brackets_or_double_spaces_ever_come_out():
    for raw in ("M.Tech ... (Computer  Sci. & Engg.)", "Ph.D. [ Physics ] ...", "M.E … ( Civil )"):
        out = format_degree(raw)
        assert "..." not in out and "…" not in out
        assert not set("()[]{}") & set(out)
        assert "  " not in out and out == out.strip()


def test_a_course_is_never_invented():
    """Uniform format, not uniform content: a resume that names no course gets none."""
    assert format_degree("Master of Engineering") == "M.E."


def test_words_inside_a_course_are_not_mistaken_for_a_degree():
    # "me" in "Mechanical" and "be" in "Embedded" must not match M.E. / B.E.
    assert format_degree("M.Tech Mechanical Engineering") == "M.Tech Mechanical Engineering"
    assert format_degree("M.Tech Embedded Systems") == "M.Tech Embedded Systems"


def test_it_is_idempotent():
    for raw in ("Ph.D. [Computer Sci. & Engg.]", "ME. (Computer Engineering)", "Master of Engineering (M.E)"):
        once = format_degree(raw)
        assert format_degree(once) == once


def test_absent_stays_absent():
    assert format_degree(None) is None
    assert format_degree("") == ""


def test_unrecognised_degree_is_still_tidied_not_dropped():
    assert format_degree("Fellowship (Data  Science)") == "Fellowship Data Science"


def test_the_workbook_row_shows_the_uniform_form():
    result = CANNED_RESULTS[0].model_copy(deep=True)
    result.highest_degree.evidence = "M.Sc. (Physics) First Class 68.4 2015"
    record = ResumeRecord(source_filename="a.pdf", processed_at=datetime(2026, 10, 3), result=result)
    assert build_row(record)["highest_degree_course_name"] == "M.Sc. Physics"
    # the stored result is untouched -- only the display is reformatted
    assert result.highest_degree.evidence == "M.Sc. (Physics) First Class 68.4 2015"
