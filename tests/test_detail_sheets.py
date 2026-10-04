"""Detail sheets: ranking, education, publications, events, teaching/skills.

Synthetic data only. Checks that the lists are grounded against the resume
text, and that what reaches the sheets is in the house format rather than
however the resume happened to type it.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from openpyxl import load_workbook

from app.detail_sheets import (
    ACHIEVEMENT_COLUMNS,
    CONTACT_COLUMNS,
    EXPERIENCE_COLUMNS,
    GUIDANCE_COLUMNS,
    achievement_rows,
    approx_years,
    contact_rows,
    experience_rows,
    guidance_rows,
    EDUCATION_COLUMNS,
    EVENT_COLUMNS,
    NOT_FOUND,
    PUBLICATION_COLUMNS,
    RANKING_COLUMNS,
    SHEET_NAMES,
    TEACHING_COLUMNS,
    education_rows,
    event_rows,
    label,
    partial_date,
    publication_rows,
    ranking_rows,
    teaching_rows,
    title_text,
    year_only,
)
from app.excel_writer import write_workbook
from llm.interface import (
    AchievementEntry,
    EducationEntry,
    EventEntry,
    ExperienceEntry,
    GuidanceEntry,
    PublicationEntry,
    ResumeRecord,
)
from llm.postprocess import sanitize
from llm.providers.fake_provider import CANNED_RESULTS
from llm.providers.gemini_provider import _to_extraction_result
from tests.test_gemini_provider import RESUME_TEXT, _payload

RESUME = """SAMPLE EXAMPLETON
QUALIFICATION :
2014  Master of Engineering (M.E)  Computer Engineering  FICTIONAL UNIVERSITY  8.2 CGPA First Class
2009  Bachelor of Engineering (B.E.)  Computer Science and Engineering  placeholder institute of technology  66.5%
Ph.D. Computer Engg. Example State University Pursuing, Guide: DR. A. B. SAMPLEGUIDE
Publications
1. A Study of Synthetic Things, International Journal of Examples, 2021 (Scopus)
2. ANOTHER PAPER ABOUT PLACEHOLDERS, Proc. of Sample Conference 2019
FDP on Machine Learning, organised by SAMPLE COLLEGE OF ENGINEERING, 5 days, 2022
Subjects taught: DATA STRUCTURES, operating systems
Skills: Python, C++, MATLAB
EXPERIENCE
ASSISTANT PROFESSOR, SAMPLE COLLEGE OF ENGINEERING, June 2016 - till date
Lecturer, Placeholder Polytechnic, 2012 to 2016
Patent: A Synthetic Device For Testing (Application No. 2021000001), Published 2021
Best Teacher Award, Sample College, 2023
Guided 12 M.E. dissertations
Life Member, ISTE
Email: SAMPLE.EXAMPLETON@EXAMPLE.ORG   Mobile: +91 98765 00000
"""


def _result():
    r = CANNED_RESULTS[0].model_copy(deep=True)
    r.candidate_name.value = "SAMPLE EXAMPLETON"
    r.education = [
        EducationEntry(level="PhD", degree="Ph.D.", course="Computer Engg.", university="Example State University",
                       guide="DR. A. B. SAMPLEGUIDE", registration="2021-07"),
        EducationEntry(level="UG", degree="Bachelor of Engineering (B.E.)", course="Computer Science and Engineering",
                       college="placeholder institute of technology", marks_pct=66.5, completion="2009"),
        EducationEntry(level="PG", degree="Master of Engineering (M.E)", course="Computer Engineering",
                       university="FICTIONAL UNIVERSITY", cgpa=8.2, division="First Class", completion="2014-05-20"),
    ]
    r.publications = [
        PublicationEntry(title="A Study of Synthetic Things", kind="JOURNAL",
                         venue="International Journal of Examples", year="2021", indexing="Scopus"),
        PublicationEntry(title="ANOTHER PAPER ABOUT PLACEHOLDERS", kind="CONFERENCE",
                         venue="Proc. of Sample Conference 2019", year="2019"),
        PublicationEntry(title="A Paper The Resume Never Mentions", kind="JOURNAL"),
    ]
    r.events = [
        EventEntry(kind="FDP", title="FDP on Machine Learning", role="ATTENDED",
                   organiser="SAMPLE COLLEGE OF ENGINEERING", duration="5 days", year="2022"),
    ]
    r.experience = [
        ExperienceEntry(designation="ASSISTANT PROFESSOR", institution="SAMPLE COLLEGE OF ENGINEERING",
                        kind="TEACHING", start="2016-06", end="PRESENT"),
        ExperienceEntry(designation="Lecturer", institution="Placeholder Polytechnic", kind="TEACHING",
                        start="2012", end="2016"),
        ExperienceEntry(designation="Chief Wizard", institution="Nowhere Industries", kind="INDUSTRY"),
    ]
    r.achievements = [
        AchievementEntry(kind="PATENT", title="A Synthetic Device For Testing",
                         details="Application No. 2021000001", year="Published 2021", status="published"),
        AchievementEntry(kind="AWARD", title="Best Teacher Award", details="Sample College", year="2023"),
    ]
    r.guidance = [GuidanceEntry(level="PG", description="Guided 12 M.E. dissertations", count=12)]
    r.memberships = ["Life Member, ISTE", "Fellow of the Imaginary Society"]
    r.email = "SAMPLE.EXAMPLETON@EXAMPLE.ORG"
    r.phone = "+91 98765 00000"
    r.subjects_taught = ["DATA STRUCTURES", "operating systems", "Quantum Basket Weaving"]
    r.skills = ["Python", "C++", "MATLAB", "python"]
    return r


@pytest.fixture
def records():
    cleaned, _ = sanitize(_result(), RESUME)
    return [
        ResumeRecord(source_filename="a.pdf", processed_at=datetime(2026, 10, 4), result=cleaned),
        ResumeRecord.failed("scan.pdf", "scanned or image-only, review manually", failure_kind="unreadable"),
    ]


# --- grounding ---------------------------------------------------------------


def test_an_item_the_resume_does_not_contain_is_kept_but_marked(records):
    pubs = records[0].result.publications
    assert [p.found_in_resume for p in pubs] == [True, True, False]
    rows = publication_rows(records)
    assert [r[PUBLICATION_COLUMNS.index("check")] for r in rows] == [None, None, NOT_FOUND]


def test_ungrounded_and_repeated_strings_are_dropped(records):
    result = records[0].result
    assert result.subjects_taught == ["DATA STRUCTURES", "operating systems"]  # invented subject gone
    assert result.skills == ["Python", "C++", "MATLAB"]  # "python" repeated


def test_a_relisted_publication_appears_once():
    r = _result()
    r.publications.append(PublicationEntry(title="A Study of Synthetic Things", kind="JOURNAL"))
    cleaned, _ = sanitize(r, RESUME)
    assert [p.title for p in cleaned.publications].count("A Study of Synthetic Things") == 1


# --- formatting --------------------------------------------------------------


def test_education_is_one_formatted_row_per_degree_in_order(records):
    rows = [dict(zip(EDUCATION_COLUMNS, r)) for r in education_rows(records)]
    assert [r["level"] for r in rows] == ["Bachelor's", "Master's", "Ph.D."]
    ug, pg, phd = rows
    assert ug["degree_and_course"] == "B.E. Computer Science and Engineering"
    assert ug["college"] == "Placeholder Institute of Technology"
    assert ug["percentage"] == 66.5 and ug["completed"] == "2009"
    assert pg["degree_and_course"] == "M.E. Computer Engineering"
    assert pg["university"] == "Fictional University"
    assert pg["cgpa"] == 8.2 and pg["division"] == "First Class" and pg["completed"] == "20-05-2014"
    assert phd["degree_and_course"] == "Ph.D. Computer Engineering"
    assert phd["phd_guide"] == "Dr. A. B. Sampleguide" and phd["phd_registered"] == "07-2021"
    assert ug["phd_guide"] is None and all(r["candidate_name"] == "Sample Exampleton" for r in rows)


def test_publications_are_numbered_and_tidied(records):
    rows = [dict(zip(PUBLICATION_COLUMNS, r)) for r in publication_rows(records)]
    assert [r["no"] for r in rows] == [1, 2, 3]
    assert rows[0]["type"] == "Journal" and rows[0]["indexing"] == "Scopus" and rows[0]["year"] == "2021"
    assert rows[1]["title"] == "Another Paper About Placeholders"  # all-caps title made readable
    assert rows[1]["type"] == "Conference" and rows[0]["status"] == "Published"


def test_events_and_teaching_rows(records):
    ev = dict(zip(EVENT_COLUMNS, event_rows(records)[0]))
    assert ev["type"] == "FDP" and ev["role"] == "Attended"
    assert ev["organiser"] == "Sample College of Engineering" and ev["duration"] == "5 days" and ev["year"] == "2022"
    t = dict(zip(TEACHING_COLUMNS, teaching_rows(records)[0]))
    assert t["subjects_taught"] == "Data Structures, Operating Systems" and t["subjects_count"] == 2
    assert t["skills"] == "Python, C++, MATLAB" and t["skills_count"] == 3


def test_ranking_rows_break_the_score_into_its_parts(records):
    row = dict(zip(RANKING_COLUMNS, ranking_rows(records)[0]))
    assert row["rank"] == 1 and row["out_of"] == 100
    parts = [v for k, v in row.items() if "(max" in k and v is not None]
    assert round(sum(parts), 1) == row["score"]


def test_failed_resumes_do_not_appear_in_detail_sheets(records):
    for builder in (ranking_rows, education_rows, publication_rows, event_rows, teaching_rows):
        assert all("scan.pdf" not in [str(c) for c in row] for row in builder(records))


@pytest.mark.parametrize(
    "raw, shown",
    [
        ("PUNE UNIVERSITY", "Pune University"),
        ("MIT ADT UNIVERSITY, PUNE", "MIT ADT University, Pune"),
        ("  placeholder   college of engineering , ", "Placeholder College of Engineering"),
        ("Savitribai Phule Pune University", "Savitribai Phule Pune University"),  # already fine
        ("IEEE Access", "IEEE Access"),
        (None, None),
    ],
)
def test_title_text(raw, shown):
    assert title_text(raw) == shown


def test_dates_follow_the_house_convention():
    assert partial_date("2014-05-20") == "20-05-2014"
    assert partial_date("2021-05") == "05-2021"
    assert partial_date("2014") == "2014"
    assert partial_date("Appearing") == "Appearing"
    assert year_only("June 2019") == "2019" and year_only("2019-20") == "2019"
    assert label("BOOK_CHAPTER") == "Book Chapter" and label("RESOURCE_PERSON") == "Resource Person"
    assert label("STTP") == "STTP"


# --- the workbook ------------------------------------------------------------


def test_workbook_has_the_five_sheets_after_the_main_one(records, tmp_path):
    wb = load_workbook(write_workbook(records, tmp_path / "out.xlsx"))
    assert wb.sheetnames[0] == "candidates"
    assert tuple(wb.sheetnames[1:10]) == SHEET_NAMES == (
        "ranking", "education", "publications", "seminars_workshops", "teaching_skills",
        "experience", "patents_awards_projects", "guidance_memberships", "contact_details",
    )
    assert {"how_scoring_works", "raw_llm_output"} <= set(wb.sheetnames)
    all_columns = (
        RANKING_COLUMNS, EDUCATION_COLUMNS, PUBLICATION_COLUMNS, EVENT_COLUMNS, TEACHING_COLUMNS,
        EXPERIENCE_COLUMNS, ACHIEVEMENT_COLUMNS, GUIDANCE_COLUMNS, CONTACT_COLUMNS,
    )
    for name, columns in zip(SHEET_NAMES, all_columns):
        ws = wb[name]
        assert [c.value for c in ws[1]] == list(columns)
        assert ws.freeze_panes == "C2" and ws.auto_filter.ref
    assert wb["education"].max_row == 4 and wb["publications"].max_row == 4


def test_a_result_without_detail_lists_still_lists_its_publication_titles(tmp_path):
    """Results cached before the detail lists existed must still produce a usable sheet."""
    old = CANNED_RESULTS[0].model_copy(deep=True)
    rec = [ResumeRecord(source_filename="old.pdf", processed_at=datetime(2026, 10, 4), result=old)]
    rows = [dict(zip(PUBLICATION_COLUMNS, r)) for r in publication_rows(rec)]
    assert [r["title"] for r in rows] == ["Paper A", "Paper B", "Paper C", "Paper D"]
    assert education_rows(rec) == [] and event_rows(rec) == []
    write_workbook(rec, tmp_path / "old.xlsx")


# --- parsing the model's response -------------------------------------------


def test_detail_lists_are_parsed_and_a_malformed_item_is_dropped_not_fatal():
    payload = _payload(
        education=[{"level": "PG", "degree": "M.Sc.", "course": "Physics"}, {"level": "NOT_A_LEVEL"}, "garbage"],
        publications=[{"title": "T", "kind": "JOURNAL", "status": "PUBLISHED"}, {"kind": "JOURNAL"}],
        events=[{"kind": "FDP", "title": "E", "role": "ATTENDED"}],
        subjects_taught=["  Maths ", "", None],
        skills=["Python"],
    )
    result = _to_extraction_result(payload, "{}")
    assert [e.level for e in result.education] == ["PG"]
    assert [p.title for p in result.publications] == ["T"]
    assert result.subjects_taught == ["Maths"] and result.skills == ["Python"]


def test_a_response_with_no_detail_lists_still_parses():
    result = _to_extraction_result(_payload(), "{}")
    assert result.education == [] and result.publications == [] and result.skills == []
    cleaned, _ = sanitize(result, RESUME_TEXT)
    assert cleaned.events == []


# --- experience, achievements, guidance, contact ----------------------------


def test_experience_rows_are_formatted_and_dated(records):
    rows = [dict(zip(EXPERIENCE_COLUMNS, r)) for r in experience_rows(records)]
    current, earlier, invented = rows
    assert current["designation"] == "Assistant Professor"
    assert current["institution"] == "Sample College of Engineering" and current["type"] == "Teaching"
    assert current["from"] == "06-2016" and current["to"] == "Present"
    assert current["approx_years"] == 10.3  # June 2016 to October 2026, when it was processed
    assert earlier["from"] == "2012" and earlier["to"] == "2016" and earlier["approx_years"] == 4.0
    assert invented["check"] == NOT_FOUND and invented["approx_years"] is None
    assert all(r["teaching_years_stated"] == 6.0 for r in rows)


def test_approx_years_never_guesses():
    as_of = datetime(2026, 10, 4)
    assert approx_years("2019-01", "2021-07", as_of) == 2.5
    assert approx_years("2019", "2021-07", as_of) == 2.0  # year-only start: whole years
    assert approx_years(None, "2021", as_of) is None
    assert approx_years("2019", None, as_of) is None
    assert approx_years("2022", "2019", as_of) is None  # dates the wrong way round


def test_achievements_and_guidance_rows(records):
    patent, award = (dict(zip(ACHIEVEMENT_COLUMNS, r)) for r in achievement_rows(records))
    assert patent["type"] == "Patent" and patent["year"] == "2021" and patent["status"] == "Published"
    assert award["type"] == "Award" and award["details"] == "Sample College"
    rows = [dict(zip(GUIDANCE_COLUMNS, r)) for r in guidance_rows(records)]
    assert rows[0]["category"] == "Research guidance" and rows[0]["level"] == "Master's" and rows[0]["students"] == 12
    assert [r["detail"] for r in rows if r["category"] == "Professional membership"] == ["Life Member, ISTE"]


def test_contact_details_are_exported_only_when_they_are_in_the_resume(records):
    row = dict(zip(CONTACT_COLUMNS, contact_rows(records)[0]))
    assert row["email"] == "sample.exampleton@example.org" and row["phone"] == "+91 98765 00000"

    r = _result()
    r.email, r.phone = "someone.else@example.org", "+91 11111 11111"
    cleaned, _ = sanitize(r, RESUME)
    assert cleaned.email is None and cleaned.phone is None


# --- formatting defects found on the first real run --------------------------


@pytest.mark.parametrize(
    "raw, shown",
    [
        ("One Week", "1 week"), ("Two Week", "2 weeks"), ("five day", "5 days"), ("Five Days", "5 days"),
        ("5-day", "5 days"), ("1 day", "1 day"), ("12 weeks", "12 weeks"), ("8 Hrs", "8 hours"),
        ("3 years 2 months", "3 years 2 months"),  # not a single count: left as written
        (None, None),
    ],
)
def test_durations_are_written_one_way(raw, shown):
    from app.detail_sheets import duration_text

    assert duration_text(raw) == shown


@pytest.mark.parametrize(
    "raw, shown",
    [
        ("Worked as a Lecturer", "Lecturer"),
        ("Working as Assistant Professor", "Assistant Professor"),
        ("Assistant professor", "Assistant Professor"),
        ("Assistant Professor - Computer Science & Engineering", "Assistant Professor"),
        ("Assistant Professor (Course Code 28)", "Assistant Professor"),
        ("HEAD OF DEPARTMENT", "Head of Department"),
        ("IT Trainer", "IT Trainer"),
    ],
)
def test_designations_are_the_post_alone(raw, shown):
    from app.detail_sheets import designation_text

    assert designation_text(raw) == shown


@pytest.mark.parametrize(
    "raw, shown",
    [
        ("First", "First Class"), ("Division 1", "First Class"), ("FIRST CLASS", "First Class"),
        ("First Class with Distinction", "First Class with Distinction"), ("Division 2", "Second Class"),
        ("Distinction", "Distinction"), ("Appearing", None), (None, None),
    ],
)
def test_divisions_are_written_one_way(raw, shown):
    from app.detail_sheets import division_text

    assert division_text(raw) == shown


def test_lost_dash_characters_and_date_noise_do_not_reach_the_sheet():
    from app.detail_sheets import tidy
    from llm.postprocess import format_degree, format_phd_evidence

    assert tidy("08.01.2010�10.01.2012") == "08.01.2010-10.01.2012"
    assert format_phd_evidence("PURSUING", "PhD Scholar in Computer Science and Engineering Aug 2021�Present") == (
        "Ph.D. Computer Science and Engineering"
    )
    assert format_degree("Batchelor of Engineering Computer Science") == "B.E. Computer Science"


def test_main_sheet_course_name_is_filled_from_the_education_list():
    from app.excel_writer import build_row

    r = CANNED_RESULTS[0].model_copy(deep=True)
    r.highest_degree.value, r.highest_degree.evidence = "PG", "M.Tech"
    r.phd_status.value, r.phd_status.evidence = "PURSUING", "Pursuing PhD"
    r.education = [
        EducationEntry(level="PG", degree="MBA", course="Finance"),  # a different Master's: must not be used
        EducationEntry(level="PG", degree="M.Tech", course="Computer Science"),
        EducationEntry(level="PhD", degree="Ph.D.", course="Computer Engg."),
    ]
    row = build_row(ResumeRecord(source_filename="a.pdf", processed_at=datetime(2026, 10, 4), result=r))
    assert row["highest_degree_course_name"] == "M.Tech Computer Science"
    assert row["phd_course_name"] == "Ph.D. Computer Engineering"
