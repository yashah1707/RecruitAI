"""The statutory rules, transcribed from the UGC gazette.

Everything in this file was read from the gazette PDFs themselves on
2026-10-05 -- the principal Regulations of 18 July 2018 (gazette pp. 57-111)
and the 2nd, 3rd and 4th Amendments -- not from a summary and not from
memory. Each row carries the clause and printed gazette page it came from.
The files' SHA-256 hashes are recorded so the exact documents can be
identified again.

This is data, not logic. Nothing here decides anything: Phase 5's engine
reads these rows from the database. Where the gazette is ambiguous the
ambiguity is written down in OPEN_POINTS rather than resolved here.

To correct a value: change it here, run `python -m backend.rules_seed`, and
regenerate docs/ugc_rules_transcription.md (`--write-transcription`).
"""

from __future__ import annotations

from datetime import date

# --- instruments -------------------------------------------------------------

PRINCIPAL = "UGC-2018"
AMD1 = "UGC-2018-AMD1-2021"
AMD2 = "UGC-2018-AMD2-2023"
AMD3 = "UGC-2018-AMD3-2023"
AMD4 = "UGC-2018-AMD4-2024"
AICTE = "AICTE-DEGREE-2019"

RULE_VERSIONS: tuple[dict, ...] = (
    {
        "code": PRINCIPAL,
        "regulator_id": "UGC",
        "instrument_name": "UGC (Minimum Qualifications for Appointment of Teachers and other Academic Staff in "
        "Universities and Colleges and other Measures for the Maintenance of Standards in Higher "
        "Education) Regulations, 2018",
        "notification_no": "F.1-2/2017(EC/PS)",
        "gazette_ref": "Gazette of India, Extraordinary, Part III-Section 4, No. 271, 18 July 2018, pp. 57-111",
        "notified_date": date(2018, 7, 18),
        "effective_from": date(2018, 7, 18),  # cl. 1.3: in force from the date of notification
        "superseded_on": None,
        "note": "Principal Regulations. Current, as amended.",
        "source_url": "https://www.ugc.gov.in/pdfnews/4033931_UGC-Regulation_min_Qualification_Jul2018.pdf",
        "source_sha256": "e010a3872eee21bd650819075a54fc404dcc551b1770b9868c4274598884c8f8",
    },
    {
        "code": AMD1,
        "regulator_id": "UGC",
        "instrument_name": "UGC (Minimum Qualifications ...) (1st Amendment) Regulations, 2021",
        "notification_no": None,
        "gazette_ref": None,
        "notified_date": None,
        "effective_from": None,
        "superseded_on": date(2023, 7, 1),
        "note": "Its substitution of cl. 3.10 was deleted by the 2nd Amendment, 2023. Not fetched; not implemented.",
        "source_url": None,
        "source_sha256": None,
    },
    {
        "code": AMD2,
        "regulator_id": "UGC",
        "instrument_name": "UGC (Minimum Qualifications ...) (2nd Amendment) Regulations, 2023",
        "notification_no": "F. 9-1/2010(PS/MISC)Pt. Vol. II",
        "gazette_ref": "Gazette of India, Extraordinary, Part III-Section 4, No. 468, 4 July 2023, CG-DL-E-04072023-247006",
        "notified_date": date(2023, 6, 30),
        "effective_from": date(2023, 7, 1),
        "superseded_on": None,
        "note": "Substitutes cl. 3.10: NET/SET/SLET is the minimum criterion for direct recruitment to Assistant Professor.",
        "source_url": "https://www.ugc.gov.in/pdfnews/5751331_UGC-Regulations-Minimum-Qualifications-2023.pdf",
        "source_sha256": "a71a91d08a8def9934dfb88ad9b90911eb957b5273a3e292d66fe408dff9b099",
    },
    {
        "code": AMD3,
        "regulator_id": "UGC",
        "instrument_name": "UGC (Minimum Qualifications ...) (3rd Amendment) Regulations, 2023",
        "notification_no": "F. No. 9-1/2010(PS/MISC)Pt. Vol.II",
        "gazette_ref": "Gazette of India, Extraordinary, Part III-Section 4, No. 535, 1 August 2023, CG-DL-E-02082023-247776",
        "notified_date": date(2023, 7, 31),
        "effective_from": date(2023, 8, 1),  # in force on publication in the Official Gazette
        "superseded_on": None,
        "note": "Amends cl. 3.12, cl. 6.3, Tables 3A/3B S.No. 3, and Note D below Table 3A. "
        "Its cl. 6.3 proviso was replaced by the 4th Amendment.",
        "source_url": "https://www.ugc.gov.in/pdfnews/4299042_Appointment-of-Teachers-and-other-Academic-Staff-amendment-of-UGC-Regulations-2023.pdf",
        "source_sha256": "e60bb221e334bd40a18ca4b8ab9c013ff9ee7be9f63ab0b933b8c32ccddc85cd",
    },
    {
        "code": AMD4,
        "regulator_id": "UGC",
        "instrument_name": "UGC (Minimum Qualifications ...) (4th Amendment) Regulations, 2024 (UIN: 2/2024)",
        "notification_no": "F.9-1/2010(PS/MISC)Pt. Vol.II",
        "gazette_ref": "Gazette of India, Extraordinary, Part III-Section 4, No. 405, 7 June 2024, CG-DL-E-07062024-254606",
        "notified_date": date(2024, 6, 6),
        "effective_from": date(2024, 6, 7),
        "superseded_on": None,
        "note": "Career Advancement Scheme promotions only (cl. 6.3 proviso). No effect on direct recruitment.",
        "source_url": "https://www.ugc.gov.in/pdfnews/6079221_4th-Amendment-Minimum-Qualifications-regulations.pdf",
        "source_sha256": "916b4620037dfb731cba34eb66eb9c45cba1f721d4b609364799edf01c996a71",
    },
)

AICTE_VERSION: dict = {
    "code": AICTE,
    "regulator_id": "AICTE",
    "instrument_name": "AICTE Regulations on Pay Scales, Service Conditions and Minimum Qualifications for the Appointment "
    "of Teachers and other Academic Staff such as Library, Physical Education and Training & Placement "
    "Personnel in Technical Institutions and Measures for the Maintenance of Standards in Technical "
    "Education - (Degree) Regulation, 2019",
    "notification_no": "F. No. 61-1/RIFD/7th CPC/2016-17",
    "gazette_ref": "Gazette of India, Extraordinary, Part III-Section 4, No. 82, 1 March 2019 (English text pp. 24-49)",
    "notified_date": date(2019, 3, 1),
    "effective_from": date(2019, 3, 1),  # cl. 1.4(a): qualifications in force from the date of the notification
    "superseded_on": None,
    "note": "Read from the gazette on 2026-10-06. MIT-ADT's HR portal publishes pp. 32-37 of this gazette as 'AICTE Norms'. "
    "Later AICTE clarifications have not been read.",
    "source_url": "https://www.aicte-india.org/sites/default/files/AICTE%20Degree%20Pay%2C%20Qualifications%20and%20Promotions.pdf",
    "source_sha256": "6a007b77bdce6aa8ea3a1bb645690bd49721a60f06e4cb6df1d84f7cf87cebd1",
}
RULE_VERSIONS = RULE_VERSIONS + (AICTE_VERSION,)

# --- thresholds per designation (cl. 4.1: Arts, Commerce, Humanities, Education,
# Law, Social Sciences, Sciences, Languages, Library Science, Physical Education,
# Journalism & Mass Communication) ------------------------------------------

RUBRIC_RULES: tuple[dict, ...] = (
    {
        "version": AMD2,
        "designation": "ASSISTANT_PROFESSOR",
        "min_years": None,
        "min_publications": None,
        "requires_phd": False,
        "min_marks_pct": 55.0,
        "research_score_threshold": None,
        "net_set_required": True,
        "min_doctoral_guided": None,
        "authority_clause": "cl. 3.10 as substituted by the 2nd Amendment, 2023; cl. 3.3; cl. 3.4; cl. 4.1 I",
        "authority_page": "2nd Amd. p. 2; pp. 58-60",
        "notes": "Master's with 55% and NET/SLET/SET. Exempt from NET/SLET/SET: Ph.D. awarded under the 2009 or "
        "2016 Ph.D. Regulations; pre-11 July 2009 registrations meeting five certified conditions; "
        "disciplines where NET/SLET/SET is not conducted. Route B: Ph.D. from a foreign university ranked "
        "in the world top 500 by QS, THE or ARWU at any time. SLET/SET is valid in the respective State only.",
    },
    {
        "version": PRINCIPAL,
        "designation": "ASSOCIATE_PROFESSOR",
        "min_years": 8.0,
        "min_publications": 7,
        "requires_phd": True,
        "min_marks_pct": 55.0,
        "research_score_threshold": 75.0,
        "net_set_required": False,
        "min_doctoral_guided": None,
        "authority_clause": "cl. 3.8; cl. 4.1 II",
        "authority_page": "p. 59; p. 60",
        "notes": "Eight years of teaching and/or research in a position equivalent to Assistant Professor; seven "
        "publications in peer-reviewed or UGC-listed journals; Research Score per Appendix II, Table 2.",
    },
    {
        "version": PRINCIPAL,
        "designation": "PROFESSOR",
        "min_years": 10.0,
        "min_publications": 10,
        "requires_phd": True,
        "min_marks_pct": 55.0,
        "research_score_threshold": 120.0,
        "net_set_required": False,
        "min_doctoral_guided": 1,
        "authority_clause": "cl. 3.7; cl. 3.4; cl. 4.1 III A",
        "authority_page": "p. 59; pp. 60-61",
        "notes": "Ten years of teaching as Assistant/Associate/Professor and/or equivalent research experience, with "
        "evidence of having successfully guided a doctoral candidate. The 55% Master's floor is cl. 3.4 "
        "('teachers ... at any level'); cl. 4.1 III does not restate it. Route B (outstanding professional "
        "with a Ph.D. and ten years' experience) is not encoded.",
    },
    {
        "version": PRINCIPAL,
        "designation": "SENIOR_PROFESSOR",
        "min_years": 10.0,
        "min_publications": 10,
        "requires_phd": True,
        "min_marks_pct": None,
        "research_score_threshold": None,
        "net_set_required": False,
        "min_doctoral_guided": 2,
        "authority_clause": "cl. 4.1 IV",
        "authority_page": "p. 61",
        "notes": "Ten years as Professor or equivalent; selection on ten best publications and the award of Ph.D. "
        "degrees to at least two candidates under supervision in the last 10 years. Direct recruitment is "
        "capped at 10% of sanctioned Professor strength and needs favourable review from three eminent "
        "experts; neither can be checked from a resume. cl. 4.1 IV does not itself say 'Ph.D.'; it is "
        "implied by requiring ten years as Professor (cl. 3.7).",
    },
)

for _row in RUBRIC_RULES:
    _row.setdefault("discipline_group", "GENERAL")
    _row.setdefault("criteria", None)


def _aicte_ap(group: str, clause: str, page: str, wording: str, criteria: dict) -> dict:
    return {
        "version": AICTE, "designation": "ASSISTANT_PROFESSOR", "discipline_group": group,
        "min_years": None, "min_publications": None, "requires_phd": False, "min_marks_pct": None,
        "research_score_threshold": None, "net_set_required": False, "min_doctoral_guided": None,
        "authority_clause": f"AICTE cl. {clause}", "authority_page": page, "criteria": criteria, "notes": wording,
    }


# AICTE (Degree) Regulation, 2019, cl. 5.1 and 5.2: direct recruitment only.
# "First Class" is defined by cl. 7.3 (see AICTE_GRADE_RULES below).
AICTE_RUBRIC_RULES: tuple[dict, ...] = (
    _aicte_ap("ENGINEERING_TECHNOLOGY", "5.1(a)", "p. 33",
              "B.E./B.Tech./B.S. and M.E./M.Tech./M.S. or Integrated M.Tech. in relevant branch with first class or "
              "equivalent in any one of the degrees.",
              {"first_class": "ANY_ONE_DEGREE", "degrees": ["UG", "PG"], "relevant_branch": True}),
    _aicte_ap("MANAGEMENT", "5.1(b)", "p. 33",
              "Bachelor's Degree in any discipline and Master's Degree in Business Administration/PGDM/C.A./ICWA/M.Com. "
              "with First Class or equivalent and two years of professional experience after acquiring the Master's degree.",
              {"first_class": "MASTERS", "degrees": ["UG", "PG"], "min_professional_years_after_masters": 2}),
    _aicte_ap("PHARMACY", "5.1(c)", "p. 33",
              "B.Pharm. and M.Pharm. in the relevant specialization with First Class or equivalent in any one of the two degrees.",
              {"first_class": "ANY_ONE_DEGREE", "degrees": ["UG", "PG"], "relevant_branch": True}),
    _aicte_ap("MCA", "5.1(d)", "p. 33",
              "Route 1: B.E./B.Tech./B.S. and M.E./M.Tech./M.S. or Integrated M.Tech. in relevant branch with First Class or "
              "equivalent in any one of the degrees. Route 2: B.E., B.Tech. and MCA with First Class or equivalent in any one "
              "of the two degrees. Route 3: Graduation of three years' duration with Mathematics as a compulsory subject and "
              "MCA with First Class or equivalent, with 2 years of relevant experience after acquiring the MCA.",
              {"routes": [
                  {"first_class": "ANY_ONE_DEGREE", "degrees": ["UG", "PG"]},
                  {"first_class": "ANY_ONE_DEGREE", "degrees": ["UG", "MCA"]},
                  {"first_class": "MCA", "degrees": ["UG_3YR_WITH_MATHS", "MCA"], "min_relevant_years_after_mca": 2},
              ]}),
    _aicte_ap("HMCT", "5.1(e)", "p. 34",
              "Route 1: minimum 4 years Bachelor's Degree in HMCT and Master's Degree in HMCT or in relevant disciplines with "
              "First Class or equivalent in any one of the two degrees. Route 2: minimum 4 years Bachelor's Degree in HMCT "
              "with First Class or equivalent and minimum of 5 years of relevant experience at a managerial level not less "
              "than Assistant Manager in a 4-star hotel or in a similar position in the hospitality/tourism industry.",
              {"routes": [{"first_class": "ANY_ONE_DEGREE", "degrees": ["UG", "PG"]},
                          {"first_class": "BACHELORS", "degrees": ["UG"], "min_industry_years": 5}]}),
    _aicte_ap("ARCHITECTURE", "5.1(f)", "p. 34",
              "Route 1: B.Arch. and M.Arch. or equivalent Master's degree in an allied field with First Class in any one of "
              "the two degrees, and minimum 2 years' experience in the Architecture profession. Route 2: B.Arch. with First "
              "class or equivalent and minimum of 5 years' experience in the Architecture profession.",
              {"routes": [{"first_class": "ANY_ONE_DEGREE", "degrees": ["UG", "PG"], "min_professional_years": 2},
                          {"first_class": "BACHELORS", "degrees": ["UG"], "min_professional_years": 5}]}),
    _aicte_ap("TOWN_PLANNING", "5.1(g)", "p. 34",
              "Bachelor's degree in Architecture/Planning/Civil Engineering or Master's degree in Geography/Economics/"
              "Sociology or equivalent AND Master of Planning or equivalent with First class or equivalent in either in "
              "Master of Planning or any above degrees with 2 years of relevant experience.",
              {"first_class": "ANY_ONE_DEGREE", "min_relevant_years": 2}),
    _aicte_ap("DESIGN", "5.1(h)", "p. 34",
              "Bachelor's Degree or minimum 4 year Diploma in any one of the streams of Design, Fine Arts, Applied Arts and "
              "Architecture or Bachelor's degree in Engineering with First class or equivalent AND Master's degree or "
              "equivalent Post Graduate Diploma in relevant disciplines with First Class or equivalent AND minimum 2 years "
              "of professional design experience in Industry/research organization/Design studios.",
              {"first_class": "BOTH_DEGREES", "degrees": ["UG", "PG"], "min_professional_years": 2}),
    _aicte_ap("FINE_ARTS", "5.1(i)", "p. 34",
              "Bachelor's and Master's degree in the relevant branch with First Class or equivalent in any one of the two "
              "degrees and minimum 2 years of relevant professional experience.",
              {"first_class": "ANY_ONE_DEGREE", "degrees": ["UG", "PG"], "min_professional_years": 2}),
    _aicte_ap("SCIENCE_HUMANITIES", "5.1(j)", "p. 34",
              "The qualifications for recruitment and promotions for faculty in the disciplines of Basic Sciences, "
              "Social Science and Humanities shall be as per the UGC Notification No. F.1-2/2017(EC/PS) Dated 18th "
              "July, 2018 and UGC guidelines issued from time to time.",
              {"defer_to": "UGC-2018"}),
    {
        "version": AICTE, "designation": "ASSOCIATE_PROFESSOR", "discipline_group": "TECHNICAL",
        "min_years": 8.0, "min_publications": 6, "requires_phd": True, "min_marks_pct": None,
        "research_score_threshold": None, "net_set_required": False, "min_doctoral_guided": None,
        "authority_clause": "AICTE cl. 5.2(c)(i)", "authority_page": "p. 35",
        "criteria": {"first_class": "BACHELORS_OR_MASTERS", "min_years_post_phd": 2,
                     "experience_types": ["TEACHING", "RESEARCH", "INDUSTRY"]},
        "notes": "Ph.D. in the relevant field and First class or equivalent at either Bachelor's or Master's level in the "
        "relevant branch; at least 6 research publications in SCI journals/UGC/AICTE approved list of journals; "
        "minimum 8 years of experience in teaching/research/industry, of which at least 2 years shall be post-Ph.D. "
        "No Research Score is prescribed. HMCT has its own experience note, not encoded.",
    },
    {
        "version": AICTE, "designation": "PROFESSOR", "discipline_group": "TECHNICAL",
        "min_years": 10.0, "min_publications": 6, "requires_phd": True, "min_marks_pct": None,
        "research_score_threshold": None, "net_set_required": False, "min_doctoral_guided": None,
        "authority_clause": "AICTE cl. 5.2(d)(i)", "authority_page": "p. 36",
        "criteria": {"first_class": "BACHELORS_OR_MASTERS", "min_years_as_associate_equivalent": 3,
                     "experience_types": ["TEACHING", "RESEARCH", "INDUSTRY"],
                     "publication_routes": [{"min_publications_at_associate_level": 6, "min_phd_guided": 2},
                                            {"min_publications_at_associate_level": 10}]},
        "notes": "Ph.D. in relevant field and First class or equivalent at either Bachelor's or Master's level in the "
        "relevant branch; minimum 10 years of experience in teaching/research/industry, of which at least 3 years at a "
        "post equivalent to Associate Professor; and EITHER at least 6 research publications at the level of Associate "
        "Professor and at least 2 successful Ph.D. guided as Supervisor/Co-supervisor, OR at least 10 research "
        "publications at the level of Associate Professor. min_publications holds the lower of the two routes.",
    },
)
RUBRIC_RULES = RUBRIC_RULES + AICTE_RUBRIC_RULES

# --- relaxations -------------------------------------------------------------

RELAXATION_RULES: tuple[dict, ...] = (
    {
        "version": PRINCIPAL,
        "code": "RESERVED_CATEGORY_5PCT",
        "relaxation_pct": 5.0,
        "applies_to_levels": ["UG", "PG"],
        "applies_to_categories": ["SC", "ST", "OBC-NCL", "PwD"],
        "condition": None,
        "description": "5% relaxation at Bachelor's as well as Master's level for Scheduled Caste, Scheduled Tribe, "
        "Other Backward Classes (Non-creamy Layer) and Differently-abled candidates, for eligibility and "
        "for assessing good academic record. On qualifying marks only, without any grace mark procedure.",
        "authority_clause": "cl. 3.4 I",
        "authority_page": "p. 59",
    },
    {
        "version": PRINCIPAL,
        "code": "PHD_PRE_1991_MASTERS_5PCT",
        "relaxation_pct": 5.0,
        "applies_to_levels": ["PG"],
        "applies_to_categories": None,
        "condition": {"requires_phd": True, "masters_awarded_before": "1991-09-19"},
        "description": "5% relaxation (from 55% to 50%) for Ph.D. degree holders who obtained their Master's degree "
        "prior to 19 September 1991.",
        "authority_clause": "cl. 3.5",
        "authority_page": "p. 59",
    },
)

# --- score tables (Appendix II) ---------------------------------------------
#
# kind:  POINTS      a fixed award (optionally per unit, optionally capped)
#        BAND        an award for a percentage falling in [band_min, band_max)
#        MULTIPLIER  a share applied to another row's points
#        CAP         an upper limit on a group of rows
#        CONSTRAINT  a condition on the score as a whole
# faculty_group: SCI_ENG = Sciences / Engineering / Agriculture / Medical / Veterinary Sciences
#                OTHER   = Languages / Humanities / Arts / Social Sciences / Library / Education /
#                          Physical Education / Commerce / Management & other related disciplines
#                ALL     = both columns carry the same figure


def _r(table, code, section, description, kind, page, version=PRINCIPAL, **kw) -> dict:
    row = {
        "version": version, "table_code": table, "row_code": code, "section": section,
        "description": description, "kind": kind, "authority_page": page,
        "faculty_group": "ALL", "points": None, "unit": None, "band_min": None, "band_max": None,
        "max_points": None, "applies_to_categories": None,
    }
    row.update(kw)
    return row


T2 = "TABLE_2"
_T2_AUTH = "Appendix II, Table 2"

TABLE_2: tuple[dict, ...] = (
    _r(T2, "PAPER_SCI_ENG", "1", "Research papers in peer-reviewed or UGC-listed journals", "POINTS", "p. 105",
       faculty_group="SCI_ENG", points=8, unit="per paper"),
    _r(T2, "PAPER_OTHER", "1", "Research papers in peer-reviewed or UGC-listed journals", "POINTS", "p. 105",
       faculty_group="OTHER", points=10, unit="per paper"),
    _r(T2, "BOOK_INTL", "2(a)", "Book authored, published by an international publisher", "POINTS", "p. 105", points=12, unit="per book"),
    _r(T2, "BOOK_NATIONAL", "2(a)", "Book authored, published by a national publisher", "POINTS", "p. 105", points=10, unit="per book"),
    _r(T2, "BOOK_CHAPTER", "2(a)", "Chapter in an edited book", "POINTS", "p. 105", points=5, unit="per chapter"),
    _r(T2, "BOOK_EDITOR_INTL", "2(a)", "Editor of a book by an international publisher", "POINTS", "p. 105", points=10, unit="per book"),
    _r(T2, "BOOK_EDITOR_NATIONAL", "2(a)", "Editor of a book by a national publisher", "POINTS", "p. 105", points=8, unit="per book"),
    _r(T2, "TRANSLATION_CHAPTER", "2(b)", "Translation work: chapter or research paper", "POINTS", "p. 105", points=3, unit="per item"),
    _r(T2, "TRANSLATION_BOOK", "2(b)", "Translation work: book", "POINTS", "p. 105", points=8, unit="per book"),
    _r(T2, "PEDAGOGY", "3(a)", "Development of innovative pedagogy", "POINTS", "p. 105", points=5),
    _r(T2, "CURRICULA", "3(b)", "Design of new curricula and courses", "POINTS", "p. 105", points=2, unit="per curriculum/course"),
    _r(T2, "MOOC_COMPLETE", "3(c)", "Development of a complete MOOC in 4 quadrants (4-credit course); for fewer credits, 05 marks per credit",
       "POINTS", "p. 105", points=20, unit="per course"),
    _r(T2, "MOOC_MODULE", "3(c)", "MOOC (developed in 4 quadrants), per module/lecture", "POINTS", "p. 105", points=5, unit="per module/lecture"),
    _r(T2, "MOOC_CONTENT_WRITER", "3(c)", "Content writer/subject matter expert for each module of a MOOC (at least one quadrant)",
       "POINTS", "p. 105", points=2, unit="per module"),
    _r(T2, "MOOC_COORDINATOR", "3(c)", "Course coordinator for a MOOC (4-credit course); for fewer credits, 02 marks per credit",
       "POINTS", "p. 105", points=8, unit="per course"),
    _r(T2, "ECONTENT_COMPLETE", "3(d)", "Development of e-content in 4 quadrants for a complete course/e-book", "POINTS", "p. 105", points=12, unit="per course"),
    _r(T2, "ECONTENT_MODULE", "3(d)", "e-content (developed in 4 quadrants), per module", "POINTS", "p. 105", points=5, unit="per module"),
    _r(T2, "ECONTENT_CONTRIBUTION", "3(d)", "Contribution to development of an e-content module in a complete course/paper/e-book (at least one quadrant)",
       "POINTS", "p. 105", points=2, unit="per module"),
    _r(T2, "ECONTENT_EDITOR", "3(d)", "Editor of e-content for a complete course/paper/e-book", "POINTS", "p. 105", points=10, unit="per course"),
    _r(T2, "PHD_AWARDED", "4(a)", "Research guidance: Ph.D. degree awarded", "POINTS", "p. 106", points=10, unit="per degree awarded"),
    _r(T2, "PHD_THESIS_SUBMITTED", "4(a)", "Research guidance: Ph.D. thesis submitted", "POINTS", "p. 106", points=5, unit="per thesis submitted"),
    _r(T2, "MPHIL_PG_DISSERTATION", "4(a)", "Research guidance: M.Phil./P.G. dissertation", "POINTS", "p. 106", points=2, unit="per degree awarded"),
    _r(T2, "PROJECT_COMPLETED_GT10L", "4(b)", "Research project completed, more than 10 lakhs", "POINTS", "p. 106", points=10, unit="per project"),
    _r(T2, "PROJECT_COMPLETED_LT10L", "4(b)", "Research project completed, less than 10 lakhs", "POINTS", "p. 106", points=5, unit="per project"),
    _r(T2, "PROJECT_ONGOING_GT10L", "4(c)", "Research project ongoing, more than 10 lakhs", "POINTS", "p. 106", points=5, unit="per project"),
    _r(T2, "PROJECT_ONGOING_LT10L", "4(c)", "Research project ongoing, less than 10 lakhs", "POINTS", "p. 106", points=2, unit="per project"),
    _r(T2, "CONSULTANCY", "4(d)", "Consultancy", "POINTS", "p. 106", points=3),
    _r(T2, "PATENT_INTL", "5(a)", "Patent, international", "POINTS", "p. 106", points=10, unit="per patent"),
    _r(T2, "PATENT_NATIONAL", "5(a)", "Patent, national", "POINTS", "p. 106", points=7, unit="per patent"),
    _r(T2, "POLICY_INTL", "5(b)", "Policy document, international", "POINTS", "p. 106", points=10),
    _r(T2, "POLICY_NATIONAL", "5(b)", "Policy document, national", "POINTS", "p. 106", points=7),
    _r(T2, "POLICY_STATE", "5(b)", "Policy document, state", "POINTS", "p. 106", points=4),
    _r(T2, "AWARD_INTL", "5(c)", "Award/fellowship, international", "POINTS", "p. 106", points=7),
    _r(T2, "AWARD_NATIONAL", "5(c)", "Award/fellowship, national", "POINTS", "p. 106", points=5),
    _r(T2, "TALK_INTL_ABROAD", "6", "Invited lecture / resource person / paper presentation / full paper in proceedings: international (abroad)",
       "POINTS", "p. 106", points=7),
    _r(T2, "TALK_INTL_INDIA", "6", "Invited lecture / resource person / paper presentation / full paper in proceedings: international (within country)",
       "POINTS", "p. 106", points=5),
    _r(T2, "TALK_NATIONAL", "6", "Invited lecture / resource person / paper presentation / full paper in proceedings: national", "POINTS", "p. 106", points=3),
    _r(T2, "TALK_STATE", "6", "Invited lecture / resource person / paper presentation / full paper in proceedings: state/university",
       "POINTS", "p. 106", points=2),
    # "The Research score for research papers would be augmented as follows"
    _r(T2, "IF_NONE", "aug.", "Paper in refereed journal without impact factor", "POINTS", "p. 106", points=5, unit="augmentation, per paper"),
    _r(T2, "IF_LT1", "aug.", "Paper with impact factor less than 1", "BAND", "p. 106", points=10, band_min=0, band_max=1, unit="augmentation, per paper"),
    _r(T2, "IF_1_2", "aug.", "Paper with impact factor between 1 and 2", "BAND", "p. 106", points=15, band_min=1, band_max=2, unit="augmentation, per paper"),
    _r(T2, "IF_2_5", "aug.", "Paper with impact factor between 2 and 5", "BAND", "p. 106", points=20, band_min=2, band_max=5, unit="augmentation, per paper"),
    _r(T2, "IF_5_10", "aug.", "Paper with impact factor between 5 and 10", "BAND", "p. 106", points=25, band_min=5, band_max=10, unit="augmentation, per paper"),
    _r(T2, "IF_GT10", "aug.", "Paper with impact factor > 10", "BAND", "p. 106", points=30, band_min=10, unit="augmentation, per paper"),
    _r(T2, "SHARE_TWO_AUTHORS", "(a)", "Two authors: 70% of the total value of the publication for each author", "MULTIPLIER", "p. 106", points=0.70),
    _r(T2, "SHARE_FIRST_AUTHOR", "(b)", "More than two authors: 70% for the first/principal/corresponding author", "MULTIPLIER", "p. 106", points=0.70),
    _r(T2, "SHARE_JOINT_AUTHOR", "(b)", "More than two authors: 30% for each of the joint authors", "MULTIPLIER", "p. 106", points=0.30),
    _r(T2, "SHARE_JOINT_PROJECT", "note", "Joint projects: Principal Investigator and Co-investigator get 50% each", "MULTIPLIER", "p. 106", points=0.50),
    _r(T2, "SHARE_JOINT_SUPERVISION", "note", "Joint supervision of research students: 70% of the total score for supervisor and co-supervisor (7 marks each)",
       "MULTIPLIER", "p. 107", points=0.70),
    _r(T2, "CAP_POLICY_AND_TALKS", "note", "Combined score from 5(b) Policy Document and 6 Invited lectures/Resource Person/Paper presentation is "
       "capped at thirty percent of the teacher's total research score", "CAP", "p. 107", points=0.30, unit="share of total"),
    _r(T2, "ONCE_ONLY", "note", "A paper presented and also published in proceedings or an edited book can be claimed only once", "CONSTRAINT", "p. 107"),
    _r(T2, "MIN_THREE_CATEGORIES", "note", "The research score shall be from a minimum of three categories out of six", "CONSTRAINT", "p. 107", points=3, unit="categories"),
)


def _table_3(table: str, page: str, grad: tuple, phd: float, jrf: float, net: float, set_: float, pubs_max: float,
             cap_mphil_phd: float, cap_net: float, academic: float, research: float, teaching: float, c_page: str) -> tuple[dict, ...]:
    reserved = ["SC", "ST", "OBC-NCL", "PwD"]
    return (
        _r(table, "GRAD_80", "1", "Graduation: 80% and above", "BAND", page, points=grad[0], band_min=80),
        _r(table, "GRAD_60_80", "1", "Graduation: 60% to less than 80%", "BAND", page, points=grad[1], band_min=60, band_max=80),
        _r(table, "GRAD_55_60", "1", "Graduation: 55% to less than 60%", "BAND", page, points=grad[2], band_min=55, band_max=60),
        _r(table, "GRAD_45_55", "1", "Graduation: 45% to less than 55%", "BAND", page, points=grad[3], band_min=45, band_max=55),
        _r(table, "PG_80", "2", "Post-Graduation: 80% and above", "BAND", page, points=25, band_min=80),
        _r(table, "PG_60_80", "2", "Post-Graduation: 60% to less than 80%", "BAND", page, points=23, band_min=60, band_max=80),
        _r(table, "PG_55_60", "2", "Post-Graduation: 55% to less than 60%", "BAND", page, points=20, band_min=55, band_max=60),
        _r(table, "PG_50_60_RESERVED", "2", "Post-Graduation: 50% to less than 60%, in case of SC/ST/OBC (non-creamy layer)/PWD",
           "BAND", page, points=20, band_min=50, band_max=60, applies_to_categories=reserved),
        _r(table, "MPHIL_60", "3", "M.Phil./LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.: 60% and above", "BAND", page, version=AMD3, points=7, band_min=60),
        _r(table, "MPHIL_55_60", "3", "M.Phil./LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.: 55% to less than 60%", "BAND", page,
           version=AMD3, points=5, band_min=55, band_max=60),
        _r(table, "PHD", "4", "Ph.D.", "POINTS", page, points=phd),
        _r(table, "NET_JRF", "5", "NET with JRF", "POINTS", page, points=jrf),
        _r(table, "NET", "5", "NET", "POINTS", page, points=net),
        _r(table, "SLET_SET", "5", "SLET/SET", "POINTS", page, points=set_),
        _r(table, "PUBLICATIONS", "6", "Research publications: 2 marks for each research publication in peer-reviewed or UGC-listed journals",
           "POINTS", page, points=2, unit="per publication", max_points=pubs_max),
        _r(table, "TEACHING", "7", "Teaching / post-doctoral experience: 2 marks for one year each; reduced proportionately for less than one year",
           "POINTS", page, points=2, unit="per year", max_points=10),
        _r(table, "AWARD_INTL_NATIONAL", "8", "Award: international/national level (international organisations, Government of India, "
           "Government of India recognised national-level bodies)", "POINTS", page, points=3),
        _r(table, "AWARD_STATE", "8", "Award: state level (given by a State Government)", "POINTS", page, points=2),
        _r(table, "CAP_MPHIL_PHD", "Note A(i)", "M.Phil + Ph.D: maximum", "CAP", page, max_points=cap_mphil_phd),
        _r(table, "CAP_JRF_NET_SET", "Note A(ii)", "JRF/NET/SET: maximum", "CAP", page, max_points=cap_net),
        _r(table, "CAP_AWARDS", "Note A(iii)", "Awards category: maximum", "CAP", page, max_points=3),
        _r(table, "TOTAL_ACADEMIC", "Note C", "Academic score", "CAP", c_page, max_points=academic),
        _r(table, "TOTAL_RESEARCH", "Note C", "Research publications", "CAP", c_page, max_points=research),
        _r(table, "TOTAL_TEACHING", "Note C", "Teaching experience", "CAP", c_page, max_points=teaching),
        _r(table, "TOTAL", "Note C", "Total", "CAP", c_page, max_points=academic + research + teaching),
    )


TABLE_3A: tuple[dict, ...] = _table_3(
    "TABLE_3A", "p. 107", grad=(15, 13, 10, 5), phd=30, jrf=7, net=5, set_=3, pubs_max=10,
    cap_mphil_phd=30, cap_net=7, academic=80, research=10, teaching=10, c_page="p. 108",
) + (
    _r("TABLE_3A", "SET_STATE_ONLY", "Note D", "SLET/SET score shall be valid for appointment in respective State "
       "Universities/Colleges/Institutions only", "CONSTRAINT", "3rd Amd. p. 5", version=AMD3),
)

TABLE_3B: tuple[dict, ...] = _table_3(
    "TABLE_3B", "p. 108", grad=(21, 19, 16, 10), phd=25, jrf=10, net=8, set_=5, pubs_max=6,
    cap_mphil_phd=25, cap_net=10, academic=84, research=6, teaching=10, c_page="p. 109",
) + (
    _r("TABLE_3B", "SET_STATE_ONLY", "Note D", "SLET/SET score shall be valid for appointment in respective State "
       "Universities/Colleges/institutions only", "CONSTRAINT", "p. 109"),
)

# AICTE cl. 7.3 (p. 39): what counts as First Class, and how a CGPA is read.
G = "AICTE_7_3"
AICTE_GRADE_RULES: tuple[dict, ...] = (
    _r(G, "FIRST_CLASS_60", "7.3", "If a class/division is not awarded, a minimum of 60% marks in aggregate is considered "
       "equivalent to first class/division", "CONSTRAINT", "p. 39", version=AICTE, points=60, unit="percent"),
    _r(G, "GP_6_25", "7.3", "Grade point 6.25 = equivalent percentage 55%", "CONVERSION", "p. 39", version=AICTE, band_min=6.25, points=55, unit="percent"),
    _r(G, "GP_6_75", "7.3", "Grade point 6.75 = equivalent percentage 60%", "CONVERSION", "p. 39", version=AICTE, band_min=6.75, points=60, unit="percent"),
    _r(G, "GP_7_25", "7.3", "Grade point 7.25 = equivalent percentage 65%", "CONVERSION", "p. 39", version=AICTE, band_min=7.25, points=65, unit="percent"),
    _r(G, "GP_7_75", "7.3", "Grade point 7.75 = equivalent percentage 70%", "CONVERSION", "p. 39", version=AICTE, band_min=7.75, points=70, unit="percent"),
    _r(G, "GP_8_25", "7.3", "Grade point 8.25 = equivalent percentage 75%", "CONVERSION", "p. 39", version=AICTE, band_min=8.25, points=75, unit="percent"),
)

SCORE_RULES: tuple[dict, ...] = TABLE_2 + TABLE_3A + TABLE_3B + AICTE_GRADE_RULES

TABLE_TITLES: dict[str, str] = {
    "TABLE_2": "Appendix II, Table 2: Methodology for University and College Teachers for calculating Academic/Research Score (pp. 105-107)",
    "TABLE_3A": "Appendix II, Table 3A: Criteria for Short-listing of Candidates for Interview for the Post of Assistant Professors in Universities (pp. 107-108)",
    "TABLE_3B": "Appendix II, Table 3B: Criteria for Short-listing of Candidates for Interview for the Post of Assistant Professors in Colleges (pp. 108-109)",
    "AICTE_7_3": "AICTE (Degree) Regulation, 2019, cl. 7.3: Class / Division (p. 39)",
}

# --- what the gazette leaves unsettled --------------------------------------
#
# These are not resolved in this file. Each needs a decision from the mentor
# or HR before Phase 5 computes a score that depends on it.

OPEN_POINTS: tuple[dict, ...] = (
    {
        "code": "T2_BASE_VS_IMPACT_FACTOR",
        "where": "Table 2, S.N. 1 and the 'augmented as follows' list (pp. 105-106)",
        "question": "S.N. 1 gives 08 (or 10) points per paper. The list below the table then says the score for research "
        "papers 'would be augmented': 5 points without impact factor, 10 for IF below 1, up to 30 for IF above 10. "
        "The gazette does not say whether the impact-factor points are added to the 8/10 or replace them.",
    },
    {
        "code": "T2_IMPACT_FACTOR_BOUNDARIES",
        "where": "Table 2, impact-factor list (p. 106)",
        "question": "'Between 1 and 2', 'between 2 and 5' and 'between 5 and 10' share their end values, so an impact "
        "factor of exactly 1, 2, 5 or 10 falls in two bands. Stored here as lower bound inclusive, upper bound exclusive.",
    },
    {
        "code": "T3_PG_VS_SNO3_FOR_MTECH",
        "where": "Table 3A/3B, S.No. 2 and S.No. 3 as amended by the 3rd Amendment, 2023",
        "question": "S.No. 3 now reads 'M.Phil/LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.'. For a candidate whose only "
        "post-graduate degree is an M.Tech or M.E., the gazette does not say whether that degree scores under "
        "S.No. 2 (Post-Graduation), S.No. 3, or both.",
    },
    {
        "code": "T3_CGPA",
        "where": "Table 3A/3B, S.No. 1-3; cl. 3.6 (p. 59)",
        "question": "The bands are in percentages. cl. 3.6 accepts 'a relevant grade which is regarded as equivalent of 55%' "
        "but gives no conversion. A CGPA needs the awarding university's own conversion before it can be banded.",
    },
    {
        "code": "T3_GRAD_BELOW_45_AND_RESERVED",
        "where": "Table 3A/3B, S.No. 1",
        "question": "No score is given for Graduation below 45%, and the reserved-category lower bound is written into "
        "the Post-Graduation row only. Stored as written.",
    },
    {
        "code": "ENGINEERING_NOT_IN_UGC_CL4",
        "where": "UGC cl. 1.1 (p. 57) and cl. 4.1 to 4.8 (pp. 59-70); AICTE (Degree) Regulation, 2019, cl. 1.2 (p. 25) and cl. 5.1 (p. 33)",
        "question": "UGC clause 4 has no section for Engineering and Technology, and cl. 1.1 says the technical-education "
        "authority's norms prevail. AICTE's 2019 Regulation applies to 'all degree level technical institutions and "
        "universities ... imparting technical education'. Our reading is that posts in the computing, engineering "
        "and technology schools are assessed under AICTE cl. 5.1 and 5.2 and not under UGC cl. 4.1: no NET/SET and "
        "no 55% rule, but First Class in the Bachelor's or the Master's degree. Is that the reading the university applies?",
    },
    {
        "code": "AICTE_CGPA_BETWEEN_TABLE_VALUES",
        "where": "AICTE cl. 7.3 (p. 39)",
        "question": "The table gives five grade points only (6.25 = 55% up to 8.25 = 75%). They lie on a straight line, "
        "percentage = (CGPA - 0.75) x 10, but the gazette does not state a formula or say how to read a CGPA "
        "between or above the listed values, or a CGPA on a scale other than 10.",
    },
    {
        "code": "AICTE_RELEVANT_BRANCH",
        "where": "AICTE cl. 5.1(a) (p. 33); cl. 7.4 (p. 40)",
        "question": "Degrees must be 'in relevant branch'. cl. 7.4 leaves interdisciplinary and new nomenclatures to the "
        "selection committee. The software cannot decide relevance; it will show the degree and course and leave "
        "this to a person.",
    },
    {
        "code": "AICTE_CLASS_NOT_STATED",
        "where": "AICTE cl. 5.1 and cl. 7.3",
        "question": "Many resumes state neither a class nor marks for a degree. First Class then cannot be confirmed from "
        "the resume and must come from the marksheet.",
    },
    {
        "code": "AICTE_NO_RELAXATION_OR_SHORTLIST_SCORE",
        "where": "AICTE (Degree) Regulation, 2019, English text pp. 24-49",
        "question": "The Regulation contains no relaxation for reserved categories and no short-listing score comparable to "
        "UGC Tables 3A/3B. Does the university apply the UGC relaxation (cl. 3.4) or the UGC short-listing table to "
        "AICTE-governed posts, or neither?",
    },
    {
        "code": "AICTE_LATER_CLARIFICATIONS",
        "where": "AICTE website: clarifications on qualifications, pay scales and service conditions",
        "question": "AICTE has issued clarifications after 2019. They have not been read. Which of them does the university "
        "treat as binding for direct recruitment?",
    },
    {
        "code": "STATE_GR_DIFFERS_FROM_GAZETTE",
        "where": "Maharashtra G.R. Misc-2018/C.R.56/18/UNI-1, pp. 5-8 (published on the HR portal as 'UGC Norms')",
        "question": "The G.R. extract differs from the UGC gazette in three places: its cl. 4.10 still makes a Ph.D. mandatory "
        "for Assistant Professor from 01.07.2021 (substituted by the UGC 2nd Amendment, 2023); its cl. 4.11 omits "
        "the sentence in UGC cl. 3.11 that service spent pursuing a research degree while teaching, without leave, "
        "counts as experience; and it requires the thesis to be evaluated by 'two examiners' where UGC cl. 3.3 says "
        "'two external examiners'. The system follows the UGC gazette. Should HR be told?",
    },
    {
        "code": "GRADE_EQUIVALENT_55",
        "where": "cl. 3.4, cl. 3.6 (p. 59)",
        "question": "55% 'or an equivalent grade in a point-scale'. Which CGPA counts as equivalent is set by each "
        "university, not by the Regulations.",
    },
)
