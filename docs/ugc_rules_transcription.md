# UGC and AICTE rules as loaded into RecruitAI: transcription for checking

This file is generated from `backend/rules_data.py` by `python -m backend.rules_seed --write-transcription`.
Do not edit it by hand. It lists every statutory value the system holds, with the gazette page it was read
from, so that someone can open the gazette beside it and confirm each line.

Read from the gazette PDFs on 2026-10-05 (UGC) and 2026-10-06 (AICTE). Page numbers are the printed gazette page numbers.

**Checked by:** ______________________  **Date:** ____________

## 1. Instruments

| Code | Instrument | Notification no. | Gazette | Notified | In force | Note |
|---|---|---|---|---|---|---|
| UGC-2018 | UGC (Minimum Qualifications for Appointment of Teachers and other Academic Staff in Universities and Colleges and other Measures for the Maintenance of Standards in Higher Education) Regulations, 2018 | F.1-2/2017(EC/PS) | Gazette of India, Extraordinary, Part III-Section 4, No. 271, 18 July 2018, pp. 57-111 | 2018-07-18 | 2018-07-18 | Principal Regulations. Current, as amended. |
| UGC-2018-AMD1-2021 | UGC (Minimum Qualifications ...) (1st Amendment) Regulations, 2021 |  |  |  |  | Its substitution of cl. 3.10 was deleted by the 2nd Amendment, 2023. Not fetched; not implemented. |
| UGC-2018-AMD2-2023 | UGC (Minimum Qualifications ...) (2nd Amendment) Regulations, 2023 | F. 9-1/2010(PS/MISC)Pt. Vol. II | Gazette of India, Extraordinary, Part III-Section 4, No. 468, 4 July 2023, CG-DL-E-04072023-247006 | 2023-06-30 | 2023-07-01 | Substitutes cl. 3.10: NET/SET/SLET is the minimum criterion for direct recruitment to Assistant Professor. |
| UGC-2018-AMD3-2023 | UGC (Minimum Qualifications ...) (3rd Amendment) Regulations, 2023 | F. No. 9-1/2010(PS/MISC)Pt. Vol.II | Gazette of India, Extraordinary, Part III-Section 4, No. 535, 1 August 2023, CG-DL-E-02082023-247776 | 2023-07-31 | 2023-08-01 | Amends cl. 3.12, cl. 6.3, Tables 3A/3B S.No. 3, and Note D below Table 3A. Its cl. 6.3 proviso was replaced by the 4th Amendment. |
| UGC-2018-AMD4-2024 | UGC (Minimum Qualifications ...) (4th Amendment) Regulations, 2024 (UIN: 2/2024) | F.9-1/2010(PS/MISC)Pt. Vol.II | Gazette of India, Extraordinary, Part III-Section 4, No. 405, 7 June 2024, CG-DL-E-07062024-254606 | 2024-06-06 | 2024-06-07 | Career Advancement Scheme promotions only (cl. 6.3 proviso). No effect on direct recruitment. |
| AICTE-DEGREE-2019 | AICTE Regulations on Pay Scales, Service Conditions and Minimum Qualifications for the Appointment of Teachers and other Academic Staff such as Library, Physical Education and Training & Placement Personnel in Technical Institutions and Measures for the Maintenance of Standards in Technical Education - (Degree) Regulation, 2019 | F. No. 61-1/RIFD/7th CPC/2016-17 | Gazette of India, Extraordinary, Part III-Section 4, No. 82, 1 March 2019 (English text pp. 24-49) | 2019-03-01 | 2019-03-01 | Read from the gazette on 2026-10-06. MIT-ADT's HR portal publishes pp. 32-37 of this gazette as 'AICTE Norms'. Later AICTE clarifications have not been read. |

Source files (SHA-256 of the PDF that was read):

- UGC-2018: <https://www.ugc.gov.in/pdfnews/4033931_UGC-Regulation_min_Qualification_Jul2018.pdf> `e010a3872eee21bd650819075a54fc404dcc551b1770b9868c4274598884c8f8`
- UGC-2018-AMD2-2023: <https://www.ugc.gov.in/pdfnews/5751331_UGC-Regulations-Minimum-Qualifications-2023.pdf> `a71a91d08a8def9934dfb88ad9b90911eb957b5273a3e292d66fe408dff9b099`
- UGC-2018-AMD3-2023: <https://www.ugc.gov.in/pdfnews/4299042_Appointment-of-Teachers-and-other-Academic-Staff-amendment-of-UGC-Regulations-2023.pdf> `e60bb221e334bd40a18ca4b8ab9c013ff9ee7be9f63ab0b933b8c32ccddc85cd`
- UGC-2018-AMD4-2024: <https://www.ugc.gov.in/pdfnews/6079221_4th-Amendment-Minimum-Qualifications-regulations.pdf> `916b4620037dfb731cba34eb66eb9c45cba1f721d4b609364799edf01c996a71`
- AICTE-DEGREE-2019: <https://www.aicte-india.org/sites/default/files/AICTE%20Degree%20Pay%2C%20Qualifications%20and%20Promotions.pdf> `6a007b77bdce6aa8ea3a1bb645690bd49721a60f06e4cb6df1d84f7cf87cebd1`

## 2. Thresholds for direct recruitment

Discipline group GENERAL is UGC cl. 4.1: Arts, Commerce, Humanities, Education, Law, Social Sciences, Sciences,
Languages, Library Science, Physical Education, and Journalism & Mass Communication. Every other group is
AICTE (Degree) Regulation, 2019, cl. 5.1 and 5.2, for technical institutions; TECHNICAL means all AICTE disciplines.

| Designation | Discipline group | Ph.D. required | NET/SET required | Min. Master's % | Min. years | Min. publications | Research Score | Doctoral candidates guided | Clause | Page | Check |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ASSISTANT_PROFESSOR | GENERAL | No | Yes | 55 |  |  |  |  | cl. 3.10 as substituted by the 2nd Amendment, 2023; cl. 3.3; cl. 3.4; cl. 4.1 I | 2nd Amd. p. 2; pp. 58-60 | ☐ |
| ASSOCIATE_PROFESSOR | GENERAL | Yes | No | 55 | 8 | 7 | 75 |  | cl. 3.8; cl. 4.1 II | p. 59; p. 60 | ☐ |
| PROFESSOR | GENERAL | Yes | No | 55 | 10 | 10 | 120 | 1 | cl. 3.7; cl. 3.4; cl. 4.1 III A | p. 59; pp. 60-61 | ☐ |
| SENIOR_PROFESSOR | GENERAL | Yes | No |  | 10 | 10 |  | 2 | cl. 4.1 IV | p. 61 | ☐ |
| ASSISTANT_PROFESSOR | ENGINEERING_TECHNOLOGY | No | No |  |  |  |  |  | AICTE cl. 5.1(a) | p. 33 | ☐ |
| ASSISTANT_PROFESSOR | MANAGEMENT | No | No |  |  |  |  |  | AICTE cl. 5.1(b) | p. 33 | ☐ |
| ASSISTANT_PROFESSOR | PHARMACY | No | No |  |  |  |  |  | AICTE cl. 5.1(c) | p. 33 | ☐ |
| ASSISTANT_PROFESSOR | MCA | No | No |  |  |  |  |  | AICTE cl. 5.1(d) | p. 33 | ☐ |
| ASSISTANT_PROFESSOR | HMCT | No | No |  |  |  |  |  | AICTE cl. 5.1(e) | p. 34 | ☐ |
| ASSISTANT_PROFESSOR | ARCHITECTURE | No | No |  |  |  |  |  | AICTE cl. 5.1(f) | p. 34 | ☐ |
| ASSISTANT_PROFESSOR | TOWN_PLANNING | No | No |  |  |  |  |  | AICTE cl. 5.1(g) | p. 34 | ☐ |
| ASSISTANT_PROFESSOR | DESIGN | No | No |  |  |  |  |  | AICTE cl. 5.1(h) | p. 34 | ☐ |
| ASSISTANT_PROFESSOR | FINE_ARTS | No | No |  |  |  |  |  | AICTE cl. 5.1(i) | p. 34 | ☐ |
| ASSISTANT_PROFESSOR | SCIENCE_HUMANITIES | No | No |  |  |  |  |  | AICTE cl. 5.1(j) | p. 34 | ☐ |
| ASSOCIATE_PROFESSOR | TECHNICAL | Yes | No |  | 8 | 6 |  |  | AICTE cl. 5.2(c)(i) | p. 35 | ☐ |
| PROFESSOR | TECHNICAL | Yes | No |  | 10 | 6 |  |  | AICTE cl. 5.2(d)(i) | p. 36 | ☐ |

Wording recorded with each threshold:

- **ASSISTANT_PROFESSOR / GENERAL:** Master's with 55% and NET/SLET/SET. Exempt from NET/SLET/SET: Ph.D. awarded under the 2009 or 2016 Ph.D. Regulations; pre-11 July 2009 registrations meeting five certified conditions; disciplines where NET/SLET/SET is not conducted. Route B: Ph.D. from a foreign university ranked in the world top 500 by QS, THE or ARWU at any time. SLET/SET is valid in the respective State only.
- **ASSOCIATE_PROFESSOR / GENERAL:** Eight years of teaching and/or research in a position equivalent to Assistant Professor; seven publications in peer-reviewed or UGC-listed journals; Research Score per Appendix II, Table 2.
- **PROFESSOR / GENERAL:** Ten years of teaching as Assistant/Associate/Professor and/or equivalent research experience, with evidence of having successfully guided a doctoral candidate. The 55% Master's floor is cl. 3.4 ('teachers ... at any level'); cl. 4.1 III does not restate it. Route B (outstanding professional with a Ph.D. and ten years' experience) is not encoded.
- **SENIOR_PROFESSOR / GENERAL:** Ten years as Professor or equivalent; selection on ten best publications and the award of Ph.D. degrees to at least two candidates under supervision in the last 10 years. Direct recruitment is capped at 10% of sanctioned Professor strength and needs favourable review from three eminent experts; neither can be checked from a resume. cl. 4.1 IV does not itself say 'Ph.D.'; it is implied by requiring ten years as Professor (cl. 3.7).
- **ASSISTANT_PROFESSOR / ENGINEERING_TECHNOLOGY:** B.E./B.Tech./B.S. and M.E./M.Tech./M.S. or Integrated M.Tech. in relevant branch with first class or equivalent in any one of the degrees.
- **ASSISTANT_PROFESSOR / MANAGEMENT:** Bachelor's Degree in any discipline and Master's Degree in Business Administration/PGDM/C.A./ICWA/M.Com. with First Class or equivalent and two years of professional experience after acquiring the Master's degree.
- **ASSISTANT_PROFESSOR / PHARMACY:** B.Pharm. and M.Pharm. in the relevant specialization with First Class or equivalent in any one of the two degrees.
- **ASSISTANT_PROFESSOR / MCA:** Route 1: B.E./B.Tech./B.S. and M.E./M.Tech./M.S. or Integrated M.Tech. in relevant branch with First Class or equivalent in any one of the degrees. Route 2: B.E., B.Tech. and MCA with First Class or equivalent in any one of the two degrees. Route 3: Graduation of three years' duration with Mathematics as a compulsory subject and MCA with First Class or equivalent, with 2 years of relevant experience after acquiring the MCA.
- **ASSISTANT_PROFESSOR / HMCT:** Route 1: minimum 4 years Bachelor's Degree in HMCT and Master's Degree in HMCT or in relevant disciplines with First Class or equivalent in any one of the two degrees. Route 2: minimum 4 years Bachelor's Degree in HMCT with First Class or equivalent and minimum of 5 years of relevant experience at a managerial level not less than Assistant Manager in a 4-star hotel or in a similar position in the hospitality/tourism industry.
- **ASSISTANT_PROFESSOR / ARCHITECTURE:** Route 1: B.Arch. and M.Arch. or equivalent Master's degree in an allied field with First Class in any one of the two degrees, and minimum 2 years' experience in the Architecture profession. Route 2: B.Arch. with First class or equivalent and minimum of 5 years' experience in the Architecture profession.
- **ASSISTANT_PROFESSOR / TOWN_PLANNING:** Bachelor's degree in Architecture/Planning/Civil Engineering or Master's degree in Geography/Economics/Sociology or equivalent AND Master of Planning or equivalent with First class or equivalent in either in Master of Planning or any above degrees with 2 years of relevant experience.
- **ASSISTANT_PROFESSOR / DESIGN:** Bachelor's Degree or minimum 4 year Diploma in any one of the streams of Design, Fine Arts, Applied Arts and Architecture or Bachelor's degree in Engineering with First class or equivalent AND Master's degree or equivalent Post Graduate Diploma in relevant disciplines with First Class or equivalent AND minimum 2 years of professional design experience in Industry/research organization/Design studios.
- **ASSISTANT_PROFESSOR / FINE_ARTS:** Bachelor's and Master's degree in the relevant branch with First Class or equivalent in any one of the two degrees and minimum 2 years of relevant professional experience.
- **ASSISTANT_PROFESSOR / SCIENCE_HUMANITIES:** The qualifications for recruitment and promotions for faculty in the disciplines of Basic Sciences, Social Science and Humanities shall be as per the UGC Notification No. F.1-2/2017(EC/PS) Dated 18th July, 2018 and UGC guidelines issued from time to time.
- **ASSOCIATE_PROFESSOR / TECHNICAL:** Ph.D. in the relevant field and First class or equivalent at either Bachelor's or Master's level in the relevant branch; at least 6 research publications in SCI journals/UGC/AICTE approved list of journals; minimum 8 years of experience in teaching/research/industry, of which at least 2 years shall be post-Ph.D. No Research Score is prescribed. HMCT has its own experience note, not encoded.
- **PROFESSOR / TECHNICAL:** Ph.D. in relevant field and First class or equivalent at either Bachelor's or Master's level in the relevant branch; minimum 10 years of experience in teaching/research/industry, of which at least 3 years at a post equivalent to Associate Professor; and EITHER at least 6 research publications at the level of Associate Professor and at least 2 successful Ph.D. guided as Supervisor/Co-supervisor, OR at least 10 research publications at the level of Associate Professor. min_publications holds the lower of the two routes.

Structured criteria held for the AICTE rows (what the engine will read):

- **ASSISTANT_PROFESSOR / ENGINEERING_TECHNOLOGY:** `{'first_class': 'ANY_ONE_DEGREE', 'degrees': ['UG', 'PG'], 'relevant_branch': True}`
- **ASSISTANT_PROFESSOR / MANAGEMENT:** `{'first_class': 'MASTERS', 'degrees': ['UG', 'PG'], 'min_professional_years_after_masters': 2}`
- **ASSISTANT_PROFESSOR / PHARMACY:** `{'first_class': 'ANY_ONE_DEGREE', 'degrees': ['UG', 'PG'], 'relevant_branch': True}`
- **ASSISTANT_PROFESSOR / MCA:** `{'routes': [{'first_class': 'ANY_ONE_DEGREE', 'degrees': ['UG', 'PG']}, {'first_class': 'ANY_ONE_DEGREE', 'degrees': ['UG', 'MCA']}, {'first_class': 'MCA', 'degrees': ['UG_3YR_WITH_MATHS', 'MCA'], 'min_relevant_years_after_mca': 2}]}`
- **ASSISTANT_PROFESSOR / HMCT:** `{'routes': [{'first_class': 'ANY_ONE_DEGREE', 'degrees': ['UG', 'PG']}, {'first_class': 'BACHELORS', 'degrees': ['UG'], 'min_industry_years': 5}]}`
- **ASSISTANT_PROFESSOR / ARCHITECTURE:** `{'routes': [{'first_class': 'ANY_ONE_DEGREE', 'degrees': ['UG', 'PG'], 'min_professional_years': 2}, {'first_class': 'BACHELORS', 'degrees': ['UG'], 'min_professional_years': 5}]}`
- **ASSISTANT_PROFESSOR / TOWN_PLANNING:** `{'first_class': 'ANY_ONE_DEGREE', 'min_relevant_years': 2}`
- **ASSISTANT_PROFESSOR / DESIGN:** `{'first_class': 'BOTH_DEGREES', 'degrees': ['UG', 'PG'], 'min_professional_years': 2}`
- **ASSISTANT_PROFESSOR / FINE_ARTS:** `{'first_class': 'ANY_ONE_DEGREE', 'degrees': ['UG', 'PG'], 'min_professional_years': 2}`
- **ASSISTANT_PROFESSOR / SCIENCE_HUMANITIES:** `{'defer_to': 'UGC-2018'}`
- **ASSOCIATE_PROFESSOR / TECHNICAL:** `{'first_class': 'BACHELORS_OR_MASTERS', 'min_years_post_phd': 2, 'experience_types': ['TEACHING', 'RESEARCH', 'INDUSTRY']}`
- **PROFESSOR / TECHNICAL:** `{'first_class': 'BACHELORS_OR_MASTERS', 'min_years_as_associate_equivalent': 3, 'experience_types': ['TEACHING', 'RESEARCH', 'INDUSTRY'], 'publication_routes': [{'min_publications_at_associate_level': 6, 'min_phd_guided': 2}, {'min_publications_at_associate_level': 10}]}`

## 3. Relaxations

| Code | Relaxation | Levels | Categories | Condition | Clause | Page | Check |
|---|---|---|---|---|---|---|---|
| RESERVED_CATEGORY_5PCT | 5% | UG, PG | SC, ST, OBC-NCL, PwD |  | cl. 3.4 I | p. 59 | ☐ |
| PHD_PRE_1991_MASTERS_5PCT | 5% | PG |  | requires_phd = True; masters_awarded_before = 1991-09-19 | cl. 3.5 | p. 59 | ☐ |

- **RESERVED_CATEGORY_5PCT:** 5% relaxation at Bachelor's as well as Master's level for Scheduled Caste, Scheduled Tribe, Other Backward Classes (Non-creamy Layer) and Differently-abled candidates, for eligibility and for assessing good academic record. On qualifying marks only, without any grace mark procedure.
- **PHD_PRE_1991_MASTERS_5PCT:** 5% relaxation (from 55% to 50%) for Ph.D. degree holders who obtained their Master's degree prior to 19 September 1991.

## 4. Appendix II, Table 2: Methodology for University and College Teachers for calculating Academic/Research Score (pp. 105-107)

| S.N. | Item | Kind | Faculty group | Points | Unit | From | To (below) | Maximum | Categories | Page | Check |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Research papers in peer-reviewed or UGC-listed journals | POINTS | SCI_ENG | 8 | per paper |  |  |  |  | p. 105 | ☐ |
| 1 | Research papers in peer-reviewed or UGC-listed journals | POINTS | OTHER | 10 | per paper |  |  |  |  | p. 105 | ☐ |
| 2(a) | Book authored, published by an international publisher | POINTS | ALL | 12 | per book |  |  |  |  | p. 105 | ☐ |
| 2(a) | Book authored, published by a national publisher | POINTS | ALL | 10 | per book |  |  |  |  | p. 105 | ☐ |
| 2(a) | Chapter in an edited book | POINTS | ALL | 5 | per chapter |  |  |  |  | p. 105 | ☐ |
| 2(a) | Editor of a book by an international publisher | POINTS | ALL | 10 | per book |  |  |  |  | p. 105 | ☐ |
| 2(a) | Editor of a book by a national publisher | POINTS | ALL | 8 | per book |  |  |  |  | p. 105 | ☐ |
| 2(b) | Translation work: chapter or research paper | POINTS | ALL | 3 | per item |  |  |  |  | p. 105 | ☐ |
| 2(b) | Translation work: book | POINTS | ALL | 8 | per book |  |  |  |  | p. 105 | ☐ |
| 3(a) | Development of innovative pedagogy | POINTS | ALL | 5 |  |  |  |  |  | p. 105 | ☐ |
| 3(b) | Design of new curricula and courses | POINTS | ALL | 2 | per curriculum/course |  |  |  |  | p. 105 | ☐ |
| 3(c) | Development of a complete MOOC in 4 quadrants (4-credit course); for fewer credits, 05 marks per credit | POINTS | ALL | 20 | per course |  |  |  |  | p. 105 | ☐ |
| 3(c) | MOOC (developed in 4 quadrants), per module/lecture | POINTS | ALL | 5 | per module/lecture |  |  |  |  | p. 105 | ☐ |
| 3(c) | Content writer/subject matter expert for each module of a MOOC (at least one quadrant) | POINTS | ALL | 2 | per module |  |  |  |  | p. 105 | ☐ |
| 3(c) | Course coordinator for a MOOC (4-credit course); for fewer credits, 02 marks per credit | POINTS | ALL | 8 | per course |  |  |  |  | p. 105 | ☐ |
| 3(d) | Development of e-content in 4 quadrants for a complete course/e-book | POINTS | ALL | 12 | per course |  |  |  |  | p. 105 | ☐ |
| 3(d) | e-content (developed in 4 quadrants), per module | POINTS | ALL | 5 | per module |  |  |  |  | p. 105 | ☐ |
| 3(d) | Contribution to development of an e-content module in a complete course/paper/e-book (at least one quadrant) | POINTS | ALL | 2 | per module |  |  |  |  | p. 105 | ☐ |
| 3(d) | Editor of e-content for a complete course/paper/e-book | POINTS | ALL | 10 | per course |  |  |  |  | p. 105 | ☐ |
| 4(a) | Research guidance: Ph.D. degree awarded | POINTS | ALL | 10 | per degree awarded |  |  |  |  | p. 106 | ☐ |
| 4(a) | Research guidance: Ph.D. thesis submitted | POINTS | ALL | 5 | per thesis submitted |  |  |  |  | p. 106 | ☐ |
| 4(a) | Research guidance: M.Phil./P.G. dissertation | POINTS | ALL | 2 | per degree awarded |  |  |  |  | p. 106 | ☐ |
| 4(b) | Research project completed, more than 10 lakhs | POINTS | ALL | 10 | per project |  |  |  |  | p. 106 | ☐ |
| 4(b) | Research project completed, less than 10 lakhs | POINTS | ALL | 5 | per project |  |  |  |  | p. 106 | ☐ |
| 4(c) | Research project ongoing, more than 10 lakhs | POINTS | ALL | 5 | per project |  |  |  |  | p. 106 | ☐ |
| 4(c) | Research project ongoing, less than 10 lakhs | POINTS | ALL | 2 | per project |  |  |  |  | p. 106 | ☐ |
| 4(d) | Consultancy | POINTS | ALL | 3 |  |  |  |  |  | p. 106 | ☐ |
| 5(a) | Patent, international | POINTS | ALL | 10 | per patent |  |  |  |  | p. 106 | ☐ |
| 5(a) | Patent, national | POINTS | ALL | 7 | per patent |  |  |  |  | p. 106 | ☐ |
| 5(b) | Policy document, international | POINTS | ALL | 10 |  |  |  |  |  | p. 106 | ☐ |
| 5(b) | Policy document, national | POINTS | ALL | 7 |  |  |  |  |  | p. 106 | ☐ |
| 5(b) | Policy document, state | POINTS | ALL | 4 |  |  |  |  |  | p. 106 | ☐ |
| 5(c) | Award/fellowship, international | POINTS | ALL | 7 |  |  |  |  |  | p. 106 | ☐ |
| 5(c) | Award/fellowship, national | POINTS | ALL | 5 |  |  |  |  |  | p. 106 | ☐ |
| 6 | Invited lecture / resource person / paper presentation / full paper in proceedings: international (abroad) | POINTS | ALL | 7 |  |  |  |  |  | p. 106 | ☐ |
| 6 | Invited lecture / resource person / paper presentation / full paper in proceedings: international (within country) | POINTS | ALL | 5 |  |  |  |  |  | p. 106 | ☐ |
| 6 | Invited lecture / resource person / paper presentation / full paper in proceedings: national | POINTS | ALL | 3 |  |  |  |  |  | p. 106 | ☐ |
| 6 | Invited lecture / resource person / paper presentation / full paper in proceedings: state/university | POINTS | ALL | 2 |  |  |  |  |  | p. 106 | ☐ |
| aug. | Paper in refereed journal without impact factor | POINTS | ALL | 5 | augmentation, per paper |  |  |  |  | p. 106 | ☐ |
| aug. | Paper with impact factor less than 1 | BAND | ALL | 10 | augmentation, per paper | 0 | 1 |  |  | p. 106 | ☐ |
| aug. | Paper with impact factor between 1 and 2 | BAND | ALL | 15 | augmentation, per paper | 1 | 2 |  |  | p. 106 | ☐ |
| aug. | Paper with impact factor between 2 and 5 | BAND | ALL | 20 | augmentation, per paper | 2 | 5 |  |  | p. 106 | ☐ |
| aug. | Paper with impact factor between 5 and 10 | BAND | ALL | 25 | augmentation, per paper | 5 | 10 |  |  | p. 106 | ☐ |
| aug. | Paper with impact factor > 10 | BAND | ALL | 30 | augmentation, per paper | 10 |  |  |  | p. 106 | ☐ |
| (a) | Two authors: 70% of the total value of the publication for each author | MULTIPLIER | ALL | 0.7 |  |  |  |  |  | p. 106 | ☐ |
| (b) | More than two authors: 70% for the first/principal/corresponding author | MULTIPLIER | ALL | 0.7 |  |  |  |  |  | p. 106 | ☐ |
| (b) | More than two authors: 30% for each of the joint authors | MULTIPLIER | ALL | 0.3 |  |  |  |  |  | p. 106 | ☐ |
| note | Joint projects: Principal Investigator and Co-investigator get 50% each | MULTIPLIER | ALL | 0.5 |  |  |  |  |  | p. 106 | ☐ |
| note | Joint supervision of research students: 70% of the total score for supervisor and co-supervisor (7 marks each) | MULTIPLIER | ALL | 0.7 |  |  |  |  |  | p. 107 | ☐ |
| note | Combined score from 5(b) Policy Document and 6 Invited lectures/Resource Person/Paper presentation is capped at thirty percent of the teacher's total research score | CAP | ALL | 0.3 | share of total |  |  |  |  | p. 107 | ☐ |
| note | A paper presented and also published in proceedings or an edited book can be claimed only once | CONSTRAINT | ALL |  |  |  |  |  |  | p. 107 | ☐ |
| note | The research score shall be from a minimum of three categories out of six | CONSTRAINT | ALL | 3 | categories |  |  |  |  | p. 107 | ☐ |

Faculty groups: SCI_ENG = Sciences / Engineering / Agriculture / Medical / Veterinary Sciences; OTHER = Languages / Humanities / Arts / Social Sciences / Library / Education / Physical Education / Commerce / Management and other related disciplines; ALL = the same figure in both columns.

## 5. Appendix II, Table 3A: Criteria for Short-listing of Candidates for Interview for the Post of Assistant Professors in Universities (pp. 107-108)

| S.N. | Item | Kind | Faculty group | Points | Unit | From | To (below) | Maximum | Categories | Page | Check |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Graduation: 80% and above | BAND | ALL | 15 |  | 80 |  |  |  | p. 107 | ☐ |
| 1 | Graduation: 60% to less than 80% | BAND | ALL | 13 |  | 60 | 80 |  |  | p. 107 | ☐ |
| 1 | Graduation: 55% to less than 60% | BAND | ALL | 10 |  | 55 | 60 |  |  | p. 107 | ☐ |
| 1 | Graduation: 45% to less than 55% | BAND | ALL | 5 |  | 45 | 55 |  |  | p. 107 | ☐ |
| 2 | Post-Graduation: 80% and above | BAND | ALL | 25 |  | 80 |  |  |  | p. 107 | ☐ |
| 2 | Post-Graduation: 60% to less than 80% | BAND | ALL | 23 |  | 60 | 80 |  |  | p. 107 | ☐ |
| 2 | Post-Graduation: 55% to less than 60% | BAND | ALL | 20 |  | 55 | 60 |  |  | p. 107 | ☐ |
| 2 | Post-Graduation: 50% to less than 60%, in case of SC/ST/OBC (non-creamy layer)/PWD | BAND | ALL | 20 |  | 50 | 60 |  | SC, ST, OBC-NCL, PwD | p. 107 | ☐ |
| 3 | M.Phil./LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.: 60% and above | BAND | ALL | 7 |  | 60 |  |  |  | p. 107 | ☐ |
| 3 | M.Phil./LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.: 55% to less than 60% | BAND | ALL | 5 |  | 55 | 60 |  |  | p. 107 | ☐ |
| 4 | Ph.D. | POINTS | ALL | 30 |  |  |  |  |  | p. 107 | ☐ |
| 5 | NET with JRF | POINTS | ALL | 7 |  |  |  |  |  | p. 107 | ☐ |
| 5 | NET | POINTS | ALL | 5 |  |  |  |  |  | p. 107 | ☐ |
| 5 | SLET/SET | POINTS | ALL | 3 |  |  |  |  |  | p. 107 | ☐ |
| 6 | Research publications: 2 marks for each research publication in peer-reviewed or UGC-listed journals | POINTS | ALL | 2 | per publication |  |  | 10 |  | p. 107 | ☐ |
| 7 | Teaching / post-doctoral experience: 2 marks for one year each; reduced proportionately for less than one year | POINTS | ALL | 2 | per year |  |  | 10 |  | p. 107 | ☐ |
| 8 | Award: international/national level (international organisations, Government of India, Government of India recognised national-level bodies) | POINTS | ALL | 3 |  |  |  |  |  | p. 107 | ☐ |
| 8 | Award: state level (given by a State Government) | POINTS | ALL | 2 |  |  |  |  |  | p. 107 | ☐ |
| Note A(i) | M.Phil + Ph.D: maximum | CAP | ALL |  |  |  |  | 30 |  | p. 107 | ☐ |
| Note A(ii) | JRF/NET/SET: maximum | CAP | ALL |  |  |  |  | 7 |  | p. 107 | ☐ |
| Note A(iii) | Awards category: maximum | CAP | ALL |  |  |  |  | 3 |  | p. 107 | ☐ |
| Note C | Academic score | CAP | ALL |  |  |  |  | 80 |  | p. 108 | ☐ |
| Note C | Research publications | CAP | ALL |  |  |  |  | 10 |  | p. 108 | ☐ |
| Note C | Teaching experience | CAP | ALL |  |  |  |  | 10 |  | p. 108 | ☐ |
| Note C | Total | CAP | ALL |  |  |  |  | 100 |  | p. 108 | ☐ |
| Note D | SLET/SET score shall be valid for appointment in respective State Universities/Colleges/Institutions only | CONSTRAINT | ALL |  |  |  |  |  |  | 3rd Amd. p. 5 | ☐ |

## 6. Appendix II, Table 3B: Criteria for Short-listing of Candidates for Interview for the Post of Assistant Professors in Colleges (pp. 108-109)

| S.N. | Item | Kind | Faculty group | Points | Unit | From | To (below) | Maximum | Categories | Page | Check |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Graduation: 80% and above | BAND | ALL | 21 |  | 80 |  |  |  | p. 108 | ☐ |
| 1 | Graduation: 60% to less than 80% | BAND | ALL | 19 |  | 60 | 80 |  |  | p. 108 | ☐ |
| 1 | Graduation: 55% to less than 60% | BAND | ALL | 16 |  | 55 | 60 |  |  | p. 108 | ☐ |
| 1 | Graduation: 45% to less than 55% | BAND | ALL | 10 |  | 45 | 55 |  |  | p. 108 | ☐ |
| 2 | Post-Graduation: 80% and above | BAND | ALL | 25 |  | 80 |  |  |  | p. 108 | ☐ |
| 2 | Post-Graduation: 60% to less than 80% | BAND | ALL | 23 |  | 60 | 80 |  |  | p. 108 | ☐ |
| 2 | Post-Graduation: 55% to less than 60% | BAND | ALL | 20 |  | 55 | 60 |  |  | p. 108 | ☐ |
| 2 | Post-Graduation: 50% to less than 60%, in case of SC/ST/OBC (non-creamy layer)/PWD | BAND | ALL | 20 |  | 50 | 60 |  | SC, ST, OBC-NCL, PwD | p. 108 | ☐ |
| 3 | M.Phil./LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.: 60% and above | BAND | ALL | 7 |  | 60 |  |  |  | p. 108 | ☐ |
| 3 | M.Phil./LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.: 55% to less than 60% | BAND | ALL | 5 |  | 55 | 60 |  |  | p. 108 | ☐ |
| 4 | Ph.D. | POINTS | ALL | 25 |  |  |  |  |  | p. 108 | ☐ |
| 5 | NET with JRF | POINTS | ALL | 10 |  |  |  |  |  | p. 108 | ☐ |
| 5 | NET | POINTS | ALL | 8 |  |  |  |  |  | p. 108 | ☐ |
| 5 | SLET/SET | POINTS | ALL | 5 |  |  |  |  |  | p. 108 | ☐ |
| 6 | Research publications: 2 marks for each research publication in peer-reviewed or UGC-listed journals | POINTS | ALL | 2 | per publication |  |  | 6 |  | p. 108 | ☐ |
| 7 | Teaching / post-doctoral experience: 2 marks for one year each; reduced proportionately for less than one year | POINTS | ALL | 2 | per year |  |  | 10 |  | p. 108 | ☐ |
| 8 | Award: international/national level (international organisations, Government of India, Government of India recognised national-level bodies) | POINTS | ALL | 3 |  |  |  |  |  | p. 108 | ☐ |
| 8 | Award: state level (given by a State Government) | POINTS | ALL | 2 |  |  |  |  |  | p. 108 | ☐ |
| Note A(i) | M.Phil + Ph.D: maximum | CAP | ALL |  |  |  |  | 25 |  | p. 108 | ☐ |
| Note A(ii) | JRF/NET/SET: maximum | CAP | ALL |  |  |  |  | 10 |  | p. 108 | ☐ |
| Note A(iii) | Awards category: maximum | CAP | ALL |  |  |  |  | 3 |  | p. 108 | ☐ |
| Note C | Academic score | CAP | ALL |  |  |  |  | 84 |  | p. 109 | ☐ |
| Note C | Research publications | CAP | ALL |  |  |  |  | 6 |  | p. 109 | ☐ |
| Note C | Teaching experience | CAP | ALL |  |  |  |  | 10 |  | p. 109 | ☐ |
| Note C | Total | CAP | ALL |  |  |  |  | 100 |  | p. 109 | ☐ |
| Note D | SLET/SET score shall be valid for appointment in respective State Universities/Colleges/institutions only | CONSTRAINT | ALL |  |  |  |  |  |  | p. 109 | ☐ |

## 7. AICTE (Degree) Regulation, 2019, cl. 7.3: Class / Division (p. 39)

| S.N. | Item | Kind | Faculty group | Points | Unit | From | To (below) | Maximum | Categories | Page | Check |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 7.3 | If a class/division is not awarded, a minimum of 60% marks in aggregate is considered equivalent to first class/division | CONSTRAINT | ALL | 60 | percent |  |  |  |  | p. 39 | ☐ |
| 7.3 | Grade point 6.25 = equivalent percentage 55% | CONVERSION | ALL | 55 | percent | 6.25 |  |  |  | p. 39 | ☐ |
| 7.3 | Grade point 6.75 = equivalent percentage 60% | CONVERSION | ALL | 60 | percent | 6.75 |  |  |  | p. 39 | ☐ |
| 7.3 | Grade point 7.25 = equivalent percentage 65% | CONVERSION | ALL | 65 | percent | 7.25 |  |  |  | p. 39 | ☐ |
| 7.3 | Grade point 7.75 = equivalent percentage 70% | CONVERSION | ALL | 70 | percent | 7.75 |  |  |  | p. 39 | ☐ |
| 7.3 | Grade point 8.25 = equivalent percentage 75% | CONVERSION | ALL | 75 | percent | 8.25 |  |  |  | p. 39 | ☐ |

## 8. Points the gazette leaves unsettled

These are recorded, not resolved. Each needs a decision before a score that depends on it is computed.

### T2_BASE_VS_IMPACT_FACTOR

*Where:* Table 2, S.N. 1 and the 'augmented as follows' list (pp. 105-106)

S.N. 1 gives 08 (or 10) points per paper. The list below the table then says the score for research papers 'would be augmented': 5 points without impact factor, 10 for IF below 1, up to 30 for IF above 10. The gazette does not say whether the impact-factor points are added to the 8/10 or replace them.

**Decision:** ______________________

### T2_IMPACT_FACTOR_BOUNDARIES

*Where:* Table 2, impact-factor list (p. 106)

'Between 1 and 2', 'between 2 and 5' and 'between 5 and 10' share their end values, so an impact factor of exactly 1, 2, 5 or 10 falls in two bands. Stored here as lower bound inclusive, upper bound exclusive.

**Decision:** ______________________

### T3_PG_VS_SNO3_FOR_MTECH

*Where:* Table 3A/3B, S.No. 2 and S.No. 3 as amended by the 3rd Amendment, 2023

S.No. 3 now reads 'M.Phil/LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.'. For a candidate whose only post-graduate degree is an M.Tech or M.E., the gazette does not say whether that degree scores under S.No. 2 (Post-Graduation), S.No. 3, or both.

**Decision:** ______________________

### T3_CGPA

*Where:* Table 3A/3B, S.No. 1-3; cl. 3.6 (p. 59)

The bands are in percentages. cl. 3.6 accepts 'a relevant grade which is regarded as equivalent of 55%' but gives no conversion. A CGPA needs the awarding university's own conversion before it can be banded.

**Decision:** ______________________

### T3_GRAD_BELOW_45_AND_RESERVED

*Where:* Table 3A/3B, S.No. 1

No score is given for Graduation below 45%, and the reserved-category lower bound is written into the Post-Graduation row only. Stored as written.

**Decision:** ______________________

### ENGINEERING_NOT_IN_UGC_CL4

*Where:* UGC cl. 1.1 (p. 57) and cl. 4.1 to 4.8 (pp. 59-70); AICTE (Degree) Regulation, 2019, cl. 1.2 (p. 25) and cl. 5.1 (p. 33)

UGC clause 4 has no section for Engineering and Technology, and cl. 1.1 says the technical-education authority's norms prevail. AICTE's 2019 Regulation applies to 'all degree level technical institutions and universities ... imparting technical education'. Our reading is that posts in the computing, engineering and technology schools are assessed under AICTE cl. 5.1 and 5.2 and not under UGC cl. 4.1: no NET/SET and no 55% rule, but First Class in the Bachelor's or the Master's degree. Is that the reading the university applies?

**Decision:** ______________________

### AICTE_CGPA_BETWEEN_TABLE_VALUES

*Where:* AICTE cl. 7.3 (p. 39)

The table gives five grade points only (6.25 = 55% up to 8.25 = 75%). They lie on a straight line, percentage = (CGPA - 0.75) x 10, but the gazette does not state a formula or say how to read a CGPA between or above the listed values, or a CGPA on a scale other than 10.

**Decision:** ______________________

### AICTE_RELEVANT_BRANCH

*Where:* AICTE cl. 5.1(a) (p. 33); cl. 7.4 (p. 40)

Degrees must be 'in relevant branch'. cl. 7.4 leaves interdisciplinary and new nomenclatures to the selection committee. The software cannot decide relevance; it will show the degree and course and leave this to a person.

**Decision:** ______________________

### AICTE_CLASS_NOT_STATED

*Where:* AICTE cl. 5.1 and cl. 7.3

Many resumes state neither a class nor marks for a degree. First Class then cannot be confirmed from the resume and must come from the marksheet.

**Decision:** ______________________

### AICTE_NO_RELAXATION_OR_SHORTLIST_SCORE

*Where:* AICTE (Degree) Regulation, 2019, English text pp. 24-49

The Regulation contains no relaxation for reserved categories and no short-listing score comparable to UGC Tables 3A/3B. Does the university apply the UGC relaxation (cl. 3.4) or the UGC short-listing table to AICTE-governed posts, or neither?

**Decision:** ______________________

### AICTE_LATER_CLARIFICATIONS

*Where:* AICTE website: clarifications on qualifications, pay scales and service conditions

AICTE has issued clarifications after 2019. They have not been read. Which of them does the university treat as binding for direct recruitment?

**Decision:** ______________________

### STATE_GR_DIFFERS_FROM_GAZETTE

*Where:* Maharashtra G.R. Misc-2018/C.R.56/18/UNI-1, pp. 5-8 (published on the HR portal as 'UGC Norms')

The G.R. extract differs from the UGC gazette in three places: its cl. 4.10 still makes a Ph.D. mandatory for Assistant Professor from 01.07.2021 (substituted by the UGC 2nd Amendment, 2023); its cl. 4.11 omits the sentence in UGC cl. 3.11 that service spent pursuing a research degree while teaching, without leave, counts as experience; and it requires the thesis to be evaluated by 'two examiners' where UGC cl. 3.3 says 'two external examiners'. The system follows the UGC gazette. Should HR be told?

**Decision:** ______________________

### GRADE_EQUIVALENT_55

*Where:* cl. 3.4, cl. 3.6 (p. 59)

55% 'or an equivalent grade in a point-scale'. Which CGPA counts as equivalent is set by each university, not by the Regulations.

**Decision:** ______________________
