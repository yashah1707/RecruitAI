"""Phase 2: the statutory rules as data.

These tests pin the values to the gazette. The expected figures here were
typed from the gazette pages independently of backend/rules_data.py, so a
slip in either place shows up as a failure rather than agreeing with itself.
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import rules_data as R
from backend.db import Base, make_engine
from backend.models import RelaxationRule, RubricRule, RuleVersion, ScoreRule
from backend.rules_seed import TRANSCRIPTION_PATH, render_transcription, seed_rules
from backend.seed import seed_reference_data


@pytest.fixture
def session():
    url = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_reference_data(s)
        seed_rules(s)
        s.commit()
        yield s
    engine.dispose()
    if not url.startswith("sqlite"):
        Base.metadata.drop_all(engine)
        engine.dispose()


def _rubric(session, designation, group="GENERAL") -> RubricRule:
    return session.scalars(
        select(RubricRule).where(RubricRule.designation == designation, RubricRule.discipline_group == group)
    ).one()


def _points(session, table, row) -> ScoreRule:
    return session.scalars(select(ScoreRule).where(ScoreRule.table_code == table, ScoreRule.row_code == row)).one()


# --- instruments -------------------------------------------------------------


def test_the_instruments_are_loaded_with_their_dates(session):
    v = {r.code: r for r in session.scalars(select(RuleVersion))}
    assert set(v) == {
        "UGC-2018", "UGC-2018-AMD1-2021", "UGC-2018-AMD2-2023", "UGC-2018-AMD3-2023", "UGC-2018-AMD4-2024",
        "AICTE-DEGREE-2019",
    }
    assert v["AICTE-DEGREE-2019"].effective_from == date(2019, 3, 1) and v["AICTE-DEGREE-2019"].regulator_id == "AICTE"
    assert v["UGC-2018"].effective_from == date(2018, 7, 18)
    assert v["UGC-2018-AMD2-2023"].effective_from == date(2023, 7, 1)
    assert v["UGC-2018-AMD3-2023"].effective_from == date(2023, 8, 1)
    assert v["UGC-2018-AMD4-2024"].effective_from == date(2024, 6, 7)
    assert v["UGC-2018-AMD1-2021"].superseded_on == date(2023, 7, 1)


def test_the_2025_draft_is_not_loaded(session):
    """It was never notified. Loading it would apply rules that are not law."""
    assert not [r for r in session.scalars(select(RuleVersion)) if "2025" in r.code or "2025" in r.instrument_name]


def test_every_fetched_instrument_records_its_source_and_hash(session):
    for r in session.scalars(select(RuleVersion)):
        if r.code == "UGC-2018-AMD1-2021":
            assert r.source_url is None  # not fetched, and says so
            continue
        assert r.source_url.startswith(("https://www.ugc.gov.in/", "https://www.aicte-india.org/")) and len(r.source_sha256) == 64


# --- thresholds (cl. 4.1) ----------------------------------------------------


def test_assistant_professor_needs_net_set_and_55_percent_but_no_phd(session):
    """The post-2023 position: cl. 3.10 as substituted by the 2nd Amendment."""
    r = _rubric(session, "ASSISTANT_PROFESSOR")
    assert (r.requires_phd, r.net_set_required, r.min_marks_pct) == (False, True, 55.0)
    assert r.min_years is None and r.min_publications is None and r.research_score_threshold is None
    assert "2nd Amendment" in r.authority_clause
    version = session.get(RuleVersion, r.rule_version_id)
    assert version.code == "UGC-2018-AMD2-2023"


def test_associate_professor_thresholds(session):
    r = _rubric(session, "ASSOCIATE_PROFESSOR")
    assert (r.requires_phd, r.min_years, r.min_publications, r.research_score_threshold, r.min_marks_pct) == (
        True, 8.0, 7, 75.0, 55.0,
    )
    assert r.net_set_required is False and "p. 60" in r.authority_page


def test_professor_thresholds(session):
    r = _rubric(session, "PROFESSOR")
    assert (r.requires_phd, r.min_years, r.min_publications, r.research_score_threshold) == (True, 10.0, 10, 120.0)
    assert r.min_doctoral_guided == 1


def test_senior_professor_thresholds(session):
    r = _rubric(session, "SENIOR_PROFESSOR")
    assert (r.requires_phd, r.min_years, r.min_publications, r.min_doctoral_guided) == (True, 10.0, 10, 2)
    assert r.research_score_threshold is None  # cl. 4.1 IV sets no Research Score


def test_every_threshold_carries_a_clause_and_a_page(session):
    for r in session.scalars(select(RubricRule)):
        assert r.authority_clause.startswith(("cl.", "AICTE cl.")) and "p." in r.authority_page


# --- AICTE (Degree) Regulation, 2019 ----------------------------------------


def test_aicte_assistant_professor_in_engineering_needs_first_class_not_net(session):
    """cl. 5.1(a), p. 33: B.E./B.Tech and M.E./M.Tech with First Class in any one of the degrees.
    No NET/SET, no Ph.D., no 55% rule."""
    r = _rubric(session, "ASSISTANT_PROFESSOR", "ENGINEERING_TECHNOLOGY")
    assert (r.requires_phd, r.net_set_required, r.min_marks_pct, r.min_years) == (False, False, None, None)
    assert r.criteria["first_class"] == "ANY_ONE_DEGREE" and r.criteria["degrees"] == ["UG", "PG"]
    assert r.authority_clause == "AICTE cl. 5.1(a)" and r.authority_page == "p. 33"
    assert session.get(RuleVersion, r.rule_version_id).code == "AICTE-DEGREE-2019"


def test_aicte_and_ugc_assistant_professor_rules_sit_side_by_side(session):
    ugc = _rubric(session, "ASSISTANT_PROFESSOR", "GENERAL")
    aicte = _rubric(session, "ASSISTANT_PROFESSOR", "ENGINEERING_TECHNOLOGY")
    assert ugc.net_set_required is True and aicte.net_set_required is False
    assert ugc.min_marks_pct == 55.0 and aicte.min_marks_pct is None


def test_aicte_associate_professor(session):
    r = _rubric(session, "ASSOCIATE_PROFESSOR", "TECHNICAL")
    assert (r.requires_phd, r.min_years, r.min_publications, r.research_score_threshold) == (True, 8.0, 6, None)
    assert r.criteria["first_class"] == "BACHELORS_OR_MASTERS" and r.criteria["min_years_post_phd"] == 2
    assert r.authority_page == "p. 35"


def test_aicte_professor_has_two_publication_routes(session):
    r = _rubric(session, "PROFESSOR", "TECHNICAL")
    assert (r.requires_phd, r.min_years, r.research_score_threshold) == (True, 10.0, None)
    assert r.criteria["min_years_as_associate_equivalent"] == 3
    assert r.criteria["publication_routes"] == [
        {"min_publications_at_associate_level": 6, "min_phd_guided": 2},
        {"min_publications_at_associate_level": 10},
    ]


def test_aicte_has_no_direct_recruitment_rule_for_senior_professor(session):
    """cl. 5.2(e), p. 37 covers promotion to Senior Professor only."""
    rows = session.scalars(select(RubricRule).where(RubricRule.designation == "SENIOR_PROFESSOR")).all()
    assert [r.discipline_group for r in rows] == ["GENERAL"]


def test_aicte_science_and_humanities_faculty_defer_to_ugc(session):
    r = _rubric(session, "ASSISTANT_PROFESSOR", "SCIENCE_HUMANITIES")
    assert r.criteria == {"defer_to": "UGC-2018"} and r.authority_clause == "AICTE cl. 5.1(j)"


@pytest.mark.parametrize("group, routes", [("MCA", 3), ("HMCT", 2), ("ARCHITECTURE", 2)])
def test_aicte_disciplines_with_alternative_routes(session, group, routes):
    assert len(_rubric(session, "ASSISTANT_PROFESSOR", group).criteria["routes"]) == routes


def test_aicte_first_class_and_grade_point_table(session):
    """cl. 7.3, p. 39."""
    assert _points(session, "AICTE_7_3", "FIRST_CLASS_60").points == 60
    table = {r.band_min: r.points for r in session.scalars(select(ScoreRule).where(ScoreRule.table_code == "AICTE_7_3")) if r.kind == "CONVERSION"}
    assert table == {6.25: 55, 6.75: 60, 7.25: 65, 7.75: 70, 8.25: 75}


def test_the_display_conversion_agrees_with_the_aicte_table_at_every_listed_point(session):
    """The workbook shows a CGPA as (CGPA - 0.75) x 10. At the five grade points AICTE lists, that is the same figure."""
    from app.excel_writer import cgpa_to_percentage

    for r in session.scalars(select(ScoreRule).where(ScoreRule.table_code == "AICTE_7_3")):
        if r.kind == "CONVERSION":
            assert cgpa_to_percentage(r.band_min) == r.points


# --- relaxations -------------------------------------------------------------


def test_reserved_category_relaxation_includes_obc_ncl_and_both_degree_levels(session):
    r = session.scalars(select(RelaxationRule).where(RelaxationRule.code == "RESERVED_CATEGORY_5PCT")).one()
    assert r.relaxation_pct == 5.0
    assert set(r.applies_to_categories) == {"SC", "ST", "OBC-NCL", "PwD"}
    assert set(r.applies_to_levels) == {"UG", "PG"}
    assert r.authority_clause == "cl. 3.4 I" and r.authority_page == "p. 59"


def test_pre_1991_masters_relaxation_is_tied_to_the_exact_date(session):
    r = session.scalars(select(RelaxationRule).where(RelaxationRule.code == "PHD_PRE_1991_MASTERS_5PCT")).one()
    assert r.condition == {"requires_phd": True, "masters_awarded_before": "1991-09-19"}
    assert r.applies_to_categories is None and r.authority_clause == "cl. 3.5"


# --- Table 2: Research Score -------------------------------------------------

TABLE_2_EXPECTED = {
    "PAPER_SCI_ENG": 8, "PAPER_OTHER": 10,
    "BOOK_INTL": 12, "BOOK_NATIONAL": 10, "BOOK_CHAPTER": 5, "BOOK_EDITOR_INTL": 10, "BOOK_EDITOR_NATIONAL": 8,
    "TRANSLATION_CHAPTER": 3, "TRANSLATION_BOOK": 8,
    "PEDAGOGY": 5, "CURRICULA": 2,
    "MOOC_COMPLETE": 20, "MOOC_MODULE": 5, "MOOC_CONTENT_WRITER": 2, "MOOC_COORDINATOR": 8,
    "ECONTENT_COMPLETE": 12, "ECONTENT_MODULE": 5, "ECONTENT_CONTRIBUTION": 2, "ECONTENT_EDITOR": 10,
    "PHD_AWARDED": 10, "PHD_THESIS_SUBMITTED": 5, "MPHIL_PG_DISSERTATION": 2,
    "PROJECT_COMPLETED_GT10L": 10, "PROJECT_COMPLETED_LT10L": 5, "PROJECT_ONGOING_GT10L": 5, "PROJECT_ONGOING_LT10L": 2,
    "CONSULTANCY": 3,
    "PATENT_INTL": 10, "PATENT_NATIONAL": 7,
    "POLICY_INTL": 10, "POLICY_NATIONAL": 7, "POLICY_STATE": 4,
    "AWARD_INTL": 7, "AWARD_NATIONAL": 5,
    "TALK_INTL_ABROAD": 7, "TALK_INTL_INDIA": 5, "TALK_NATIONAL": 3, "TALK_STATE": 2,
    "IF_NONE": 5, "IF_LT1": 10, "IF_1_2": 15, "IF_2_5": 20, "IF_5_10": 25, "IF_GT10": 30,
}


@pytest.mark.parametrize("row, points", sorted(TABLE_2_EXPECTED.items()))
def test_table_2_points(session, row, points):
    assert _points(session, "TABLE_2", row).points == points


def test_table_2_differs_by_faculty_only_for_research_papers(session):
    rows = session.scalars(select(ScoreRule).where(ScoreRule.table_code == "TABLE_2")).all()
    assert {r.row_code for r in rows if r.faculty_group != "ALL"} == {"PAPER_SCI_ENG", "PAPER_OTHER"}


def test_table_2_authorship_shares_and_caps(session):
    assert _points(session, "TABLE_2", "SHARE_TWO_AUTHORS").points == 0.70
    assert _points(session, "TABLE_2", "SHARE_FIRST_AUTHOR").points == 0.70
    assert _points(session, "TABLE_2", "SHARE_JOINT_AUTHOR").points == 0.30
    assert _points(session, "TABLE_2", "SHARE_JOINT_PROJECT").points == 0.50
    cap = _points(session, "TABLE_2", "CAP_POLICY_AND_TALKS")
    assert cap.kind == "CAP" and cap.points == 0.30
    assert _points(session, "TABLE_2", "MIN_THREE_CATEGORIES").points == 3


def test_impact_factor_bands_are_contiguous(session):
    bands = [_points(session, "TABLE_2", c) for c in ("IF_LT1", "IF_1_2", "IF_2_5", "IF_5_10", "IF_GT10")]
    assert [(b.band_min, b.band_max) for b in bands] == [(0, 1), (1, 2), (2, 5), (5, 10), (10, None)]


# --- Tables 3A and 3B: short-listing score ----------------------------------

# (graduation bands, PG bands, M.Phil bands, PhD, JRF, NET, SET, publications max, teaching max)
TABLE_3_EXPECTED = {
    "TABLE_3A": ((15, 13, 10, 5), (25, 23, 20), (7, 5), 30, 7, 5, 3, 10, 10),
    "TABLE_3B": ((21, 19, 16, 10), (25, 23, 20), (7, 5), 25, 10, 8, 5, 6, 10),
}


@pytest.mark.parametrize("table", ["TABLE_3A", "TABLE_3B"])
def test_table_3_points(session, table):
    grad, pg, mphil, phd, jrf, net, set_, pubs_max, teach_max = TABLE_3_EXPECTED[table]
    get = lambda code: _points(session, table, code)  # noqa: E731
    assert tuple(get(c).points for c in ("GRAD_80", "GRAD_60_80", "GRAD_55_60", "GRAD_45_55")) == grad
    assert tuple(get(c).points for c in ("PG_80", "PG_60_80", "PG_55_60")) == pg
    assert tuple(get(c).points for c in ("MPHIL_60", "MPHIL_55_60")) == mphil
    assert (get("PHD").points, get("NET_JRF").points, get("NET").points, get("SLET_SET").points) == (phd, jrf, net, set_)
    assert (get("PUBLICATIONS").points, get("PUBLICATIONS").max_points) == (2, pubs_max)
    assert (get("TEACHING").points, get("TEACHING").max_points) == (2, teach_max)
    assert (get("AWARD_INTL_NATIONAL").points, get("AWARD_STATE").points) == (3, 2)


@pytest.mark.parametrize("table, academic, research, teaching", [("TABLE_3A", 80, 10, 10), ("TABLE_3B", 84, 6, 10)])
def test_table_3_totals_add_up_to_100(session, table, academic, research, teaching):
    get = lambda code: _points(session, table, code).max_points  # noqa: E731
    assert (get("TOTAL_ACADEMIC"), get("TOTAL_RESEARCH"), get("TOTAL_TEACHING")) == (academic, research, teaching)
    assert get("TOTAL") == 100


def test_table_3a_academic_maximum_is_reachable_from_its_own_rows(session):
    """Graduation 15 + PG 25 + (M.Phil + Ph.D capped at 30) + (JRF/NET/SET capped at 7) + awards 3 = 80."""
    get = lambda code: _points(session, "TABLE_3A", code)  # noqa: E731
    total = get("GRAD_80").points + get("PG_80").points + get("CAP_MPHIL_PHD").max_points + get("CAP_JRF_NET_SET").max_points + get("CAP_AWARDS").max_points
    assert total == get("TOTAL_ACADEMIC").max_points == 80


def test_reserved_categories_get_the_lower_pg_band(session):
    row = _points(session, "TABLE_3A", "PG_50_60_RESERVED")
    assert (row.band_min, row.band_max, row.points) == (50, 60, 20)
    assert set(row.applies_to_categories) == {"SC", "ST", "OBC-NCL", "PwD"}


def test_third_amendment_changes_are_attributed_to_the_third_amendment(session):
    amd3 = session.scalars(select(RuleVersion).where(RuleVersion.code == "UGC-2018-AMD3-2023")).one()
    row = _points(session, "TABLE_3A", "MPHIL_60")
    assert row.rule_version_id == amd3.rule_version_id and "M.Tech" in row.description and "M.E." in row.description
    assert _points(session, "TABLE_3A", "SET_STATE_ONLY").rule_version_id == amd3.rule_version_id


def test_bands_within_a_group_do_not_overlap(session):
    for table in ("TABLE_3A", "TABLE_3B"):
        for prefix in ("GRAD_", "MPHIL_"):
            rows = sorted(
                (r for r in session.scalars(select(ScoreRule).where(ScoreRule.table_code == table)) if r.row_code.startswith(prefix)),
                key=lambda r: r.band_min,
            )
            for lower, upper in zip(rows, rows[1:]):
                assert lower.band_max == upper.band_min, f"{table} {lower.row_code} / {upper.row_code}"


# --- loading and the transcription sheet ------------------------------------


def test_seeding_again_updates_in_place(session):
    before = len(session.scalars(select(ScoreRule)).all())
    seed_rules(session)
    session.commit()
    assert len(session.scalars(select(ScoreRule)).all()) == before == len(R.SCORE_RULES)
    assert len(session.scalars(select(RubricRule)).all()) == len(R.RUBRIC_RULES) == 16


def test_a_row_dropped_from_the_data_is_removed_from_the_database(session):
    """Otherwise a rule deleted from the source would keep being applied."""
    version = session.scalars(select(RuleVersion)).first()
    session.add(ScoreRule(rule_version_id=version.rule_version_id, table_code="TABLE_2", row_code="STALE_ROW",
                          section="x", description="left over", kind="POINTS", authority_page="p. 0"))
    session.add(RubricRule(rule_version_id=version.rule_version_id, designation="DEAN", discipline_group="GENERAL",
                           authority_clause="cl. x", authority_page="p. 0"))
    session.commit()
    seed_rules(session)
    session.commit()
    assert not session.scalars(select(ScoreRule).where(ScoreRule.row_code == "STALE_ROW")).all()
    assert not session.scalars(select(RubricRule).where(RubricRule.designation == "DEAN")).all()
    assert len(session.scalars(select(RuleVersion)).all()) == len(R.RULE_VERSIONS)  # instruments are never pruned


def test_every_score_row_has_a_page_and_a_known_kind():
    for r in R.SCORE_RULES:
        assert r["kind"] in {"POINTS", "BAND", "MULTIPLIER", "CAP", "CONSTRAINT", "CONVERSION"}
        assert "p." in r["authority_page"], r["row_code"]


def test_the_committed_transcription_sheet_matches_the_data():
    """The sheet a person checks against the gazette must be the data the system uses."""
    assert TRANSCRIPTION_PATH.read_text(encoding="utf-8") == render_transcription()


def test_open_points_are_written_down_not_resolved():
    codes = {o["code"] for o in R.OPEN_POINTS}
    assert {"T2_BASE_VS_IMPACT_FACTOR", "T3_PG_VS_SNO3_FOR_MTECH", "ENGINEERING_NOT_IN_UGC_CL4",
            "AICTE_CGPA_BETWEEN_TABLE_VALUES", "STATE_GR_DIFFERS_FROM_GAZETTE"} <= codes
    sheet = render_transcription()
    assert all(o["code"] in sheet for o in R.OPEN_POINTS)


def test_every_loaded_text_value_fits_its_column():
    """PostgreSQL rejects a value longer than its column; SQLite stores it
    silently. Checked here against the model so the suite catches it on
    either database, instead of the load failing on the real one."""
    from sqlalchemy import String

    def check(model, rows, skip=("version",)):
        limits = {c.name: c.type.length for c in model.__table__.columns if isinstance(c.type, String) and c.type.length}
        for row in rows:
            for key, value in row.items():
                if key in skip or not isinstance(value, str) or key not in limits:
                    continue
                assert len(value) <= limits[key], f"{model.__tablename__}.{key} is {len(value)} long, column holds {limits[key]}"

    check(RuleVersion, R.RULE_VERSIONS)
    check(RubricRule, R.RUBRIC_RULES)
    check(RelaxationRule, R.RELAXATION_RULES)
    check(ScoreRule, R.SCORE_RULES)
