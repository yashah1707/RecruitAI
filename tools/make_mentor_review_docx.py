"""Build the Word document a mentor uses to check the UGC and AICTE rules RecruitAI has loaded.

Generated from backend/rules_data.py, the same data the system loads, so the
document cannot drift from what the software actually uses.

    python tools/make_mentor_review_docx.py            writes docs/UGC_Rules_Mentor_Review.docx
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from backend import rules_data as R

OUT = Path(__file__).resolve().parent.parent / "docs" / "UGC_Rules_Mentor_Review.docx"
HEADER_FILL = "D9E2F3"
BOX = "☐"


def _num(v) -> str:
    return "" if v is None else f"{v:g}"


def _shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def table(doc, headers: list[str], rows: list[list[str]], widths_cm: list[float], size: float = 9) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        run = c.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(size)
        _shade(c, HEADER_FILL)
    _repeat_header(t.rows[0])
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(v))
            run.font.size = Pt(size)
            if v == BOX:
                cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                run.font.size = Pt(13)
    for row in t.rows:
        for i, w in enumerate(widths_cm):
            row.cells[i].width = Cm(w)
    doc.add_paragraph()


def para(doc, text: str, bold: bool = False, italic: bool = False, size: float = 10.5):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold, run.italic = bold, italic
    run.font.size = Pt(size)
    return p


def bullets(doc, items: list[str], style: str = "List Bullet") -> None:
    for item in items:
        p = doc.add_paragraph(style=style)
        p.add_run(item).font.size = Pt(10.5)


def labelled(doc, label: str, text: str) -> None:
    p = doc.add_paragraph()
    a = p.add_run(label)
    a.bold = True
    a.font.size = Pt(10.5)
    p.add_run(text).font.size = Pt(10.5)


_FIRST_CLASS = {
    "ANY_ONE_DEGREE": "First Class in any one of the degrees",
    "MASTERS": "First Class in the Master's degree",
    "BACHELORS": "First Class in the Bachelor's degree",
    "BOTH_DEGREES": "First Class in both the Bachelor's and the Master's degree",
    "BACHELORS_OR_MASTERS": "First Class in the Bachelor's or the Master's degree",
    "MCA": "First Class in the MCA",
}
_YEARS = {
    "min_professional_years": "{} years' professional experience",
    "min_professional_years_after_masters": "{} years' professional experience after the Master's",
    "min_relevant_years": "{} years' relevant experience",
    "min_relevant_years_after_mca": "{} years' relevant experience after the MCA",
    "min_industry_years": "{} years' industry experience at managerial level",
    "min_years_post_phd": "at least {} of the years after the Ph.D.",
    "min_years_as_associate_equivalent": "at least {} of the years at Associate Professor level",
}


def reading(criteria: dict | None) -> str:
    """The structured criteria the engine will use, in plain words, so the
    mentor can confirm our reading and not only the printed text."""
    if not criteria:
        return ""
    if "defer_to" in criteria:
        return "Assessed under the UGC Regulations, 2018 instead."
    if "routes" in criteria:
        return " OR ".join(f"({i}) {reading(r)}" for i, r in enumerate(criteria["routes"], start=1))
    parts = []
    if "first_class" in criteria:
        parts.append(_FIRST_CLASS[criteria["first_class"]])
    for key, text in _YEARS.items():
        if key in criteria:
            parts.append(text.format(criteria[key]))
    if "publication_routes" in criteria:
        routes = []
        for r in criteria["publication_routes"]:
            t = f"{r['min_publications_at_associate_level']} publications at Associate Professor level"
            if "min_phd_guided" in r:
                t += f" and {r['min_phd_guided']} Ph.D.s guided"
            routes.append(t)
        parts.append("either " + ", or ".join(routes))
    if criteria.get("relevant_branch"):
        parts.append("relevance of the branch is left to a person")
    return "; ".join(parts) + "."


def build() -> Document:
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Cm(29.7), Cm(21.0)
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Cm(1.8))
    section.top_margin = section.bottom_margin = Cm(1.6)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    title = doc.add_heading("RecruitAI: UGC and AICTE Rules Verification Sheet", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para(doc, "For review and sign-off by the project mentor", italic=True, size=12)
    para(doc, "MIT Art, Design & Technology University, Pune. B.Tech (Computer Science & Engineering) capstone project: "
              "RecruitAI, The Academic Hiring Agent.")
    table(doc, ["Student", "Mentor", "Date issued", "Date returned"], [["", "", "", ""]], [6.5, 6.5, 6.5, 6.5], size=10.5)

    # --- 1 -------------------------------------------------------------------
    doc.add_heading("1. Why you are being asked to check this", level=1)
    para(doc, "RecruitAI decides whether a faculty applicant meets the UGC minimum qualifications. It does not use an AI "
              "model for that decision. The decision is computed by ordinary program code from a table of rules, and "
              "every rule in that table was typed in from the UGC and AICTE gazettes.")
    para(doc, "If one number in that table is wrong, every candidate judged by it is judged wrongly, and the error will "
              "look correct because the software will cite a clause and page beside it. A second person reading the "
              "numbers against the gazette is the only reliable protection against that. This document lists every "
              "statutory value the system holds, with the gazette page it was read from, so that it can be checked "
              "line by line.")
    para(doc, "The UGC values were read from the gazette PDFs on 5 October 2026 and the AICTE values on 6 October 2026. "
              "They have been cross-checked by program against the gazette text, but not yet by a person other than "
              "the student.")

    # --- 2 -------------------------------------------------------------------
    doc.add_heading("2. What you need", level=1)
    para(doc, "The gazette PDFs below, from the UGC and AICTE websites. Page numbers in this document are the page "
              "numbers printed on the gazette pages (the UGC principal Regulations run from page 57 to page 111, and "
              "the English text of the AICTE Regulation from page 24 to page 49), not the page count shown by a PDF reader.")
    rows = [[v["code"], v["instrument_name"], v["gazette_ref"] or "", v["source_url"] or "Not fetched"]
            for v in R.RULE_VERSIONS if v["source_url"]]
    table(doc, ["Code used in this sheet", "Instrument", "Gazette reference", "Link"], rows, [3.6, 8.2, 7.2, 7.0], size=8.5)

    # --- 3 -------------------------------------------------------------------
    doc.add_heading("3. How to check this sheet", level=1)
    bullets(doc, [
        "Open the gazette named in the section heading at the page given in the Page column of each row.",
        "Compare the value in this sheet with the value printed in the gazette.",
        f"If they agree, tick the box ({BOX}) in the Check column.",
        "If they disagree, do not tick. Write the correct value in the Correction column, or in the margin.",
        "Sections 5 to 9 are checked this way. They take about one and a half hours in total.",
        "Section 10 is different. It lists questions the gazettes do not answer. For each one, please write your "
        "decision, or the name of the person or office who should decide.",
        "Section 11 is one finding about engineering posts that we need your advice on.",
        "Sign the declaration in Section 12 and return the document to the student.",
    ], style="List Number")
    para(doc, "You do not need to check the software, the code or the database. Only the values and the open questions.",
         italic=True)

    # --- 4 -------------------------------------------------------------------
    doc.add_heading("4. What we need from you", level=1)
    table(doc, ["#", "What we need", "Why it matters", "Where"], [
        ["1", "Confirmation that each value matches the gazette, or the correction where it does not.",
         "These values decide eligibility for every applicant.", "Sections 5 to 9"],
        ["2", "A decision on each open question, or a direction on who should decide it.",
         "The software cannot compute a Research Score or a short-listing score until these are settled. We have "
         "deliberately not guessed.", "Section 10"],
        ["3", "Confirmation that engineering and technology posts are assessed under the AICTE Regulation, and which "
              "later AICTE clarifications the university applies.",
         "Most applications received so far are for Computer Engineering posts. Under AICTE they need First Class in "
         "the Bachelor's or Master's degree, and no NET/SET; under UGC clause 4.1 they would need NET/SET.",
         "Section 11"],
        ["4", "Your signature and date on the declaration.",
         "It records that the rule table was independently verified, which the project report will cite.", "Section 12"],
        ["5", "Any rule you know of that is missing from this sheet.",
         "We transcribed only direct recruitment to the four teaching ranks. Promotion (Career Advancement Scheme), "
         "librarians, physical education and principals are out of scope by design.", "Section 12, remarks"],
    ], [1.0, 9.0, 11.0, 5.0])
    para(doc, "What we are not asking: you are not being asked to approve the software's decisions about any candidate, "
              "or to take responsibility for a hiring outcome. Under clause 3.1 and the Note to clause 4.1 the "
              "selection is made by the Selection Committee at interview; this system only prepares a short-list.",
         italic=True)

    # --- 5 -------------------------------------------------------------------
    doc.add_page_break()
    doc.add_heading("5. Instruments loaded", level=1)
    rows = [[v["code"], v["notification_no"] or "", str(v["notified_date"] or ""), str(v["effective_from"] or ""),
             v["note"] or "", BOX, ""] for v in R.RULE_VERSIONS]
    table(doc, ["Code", "Notification no.", "Notified", "In force from", "Note", "Check", "Correction"], rows,
          [3.6, 4.6, 2.3, 2.5, 8.2, 1.4, 3.4], size=8.5)
    para(doc, "The Draft UGC Regulations, 2025 are deliberately not loaded, because they were never notified.", italic=True)

    # --- 6 -------------------------------------------------------------------
    doc.add_heading("6. UGC: minimum qualifications for direct recruitment (clause 4.1)", level=1)
    para(doc, "Clause 4.1 applies to Arts, Commerce, Humanities, Education, Law, Social Sciences, Sciences, Languages, "
              "Library Science, Physical Education, and Journalism & Mass Communication.")
    UGC_RUBRIC = [r for r in R.RUBRIC_RULES if r["discipline_group"] == "GENERAL"]
    AICTE_RUBRIC = [r for r in R.RUBRIC_RULES if r["discipline_group"] != "GENERAL"]
    names = {"ASSISTANT_PROFESSOR": "Assistant Professor", "ASSOCIATE_PROFESSOR": "Associate Professor",
             "PROFESSOR": "Professor", "SENIOR_PROFESSOR": "Senior Professor"}
    rows = [[names[r["designation"]], "Yes" if r["requires_phd"] else "No", "Yes" if r["net_set_required"] else "No",
             _num(r["min_marks_pct"]), _num(r["min_years"]), _num(r["min_publications"]),
             _num(r["research_score_threshold"]), _num(r["min_doctoral_guided"]),
             r["authority_clause"], r["authority_page"], BOX, ""] for r in UGC_RUBRIC]
    table(doc, ["Post", "Ph.D. required", "NET/SET required", "Min. Master's %", "Min. years", "Min. publications",
                "Research Score", "Ph.D. scholars guided", "Clause", "Page", "Check", "Correction"],
          rows, [2.6, 1.5, 1.6, 1.6, 1.3, 1.9, 1.6, 1.7, 5.0, 2.6, 1.2, 3.4], size=8.5)
    para(doc, "How we read each row. Please confirm the reading as well as the number.", bold=True)
    for r in UGC_RUBRIC:
        labelled(doc, names[r["designation"]] + ": ", r["notes"])

    # --- 7 -------------------------------------------------------------------
    doc.add_heading("7. UGC: relaxations in marks", level=1)
    rows = []
    for r in R.RELAXATION_RULES:
        cond = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in (r["condition"] or {}).items())
        rows.append([r["description"], f"{_num(r['relaxation_pct'])}%", ", ".join(r["applies_to_levels"]),
                     ", ".join(r["applies_to_categories"] or []) or "Not category-based", cond,
                     r["authority_clause"], r["authority_page"], BOX, ""])
    table(doc, ["Relaxation as we read it", "Amount", "Degree levels", "Categories", "Condition", "Clause", "Page",
                "Check", "Correction"], rows, [9.0, 1.5, 1.8, 3.2, 3.6, 1.8, 1.4, 1.2, 2.5], size=8.5)

    # --- 8 -------------------------------------------------------------------
    doc.add_page_break()
    doc.add_heading("8. UGC: score tables (Appendix II)", level=1)
    doc.add_heading("8.1 Table 2: Research Score (pages 105 to 107)", level=2)
    para(doc, "Used as a pass mark: 75 for Associate Professor and 120 for Professor. The gazette has two columns. "
              "Column A is Sciences / Engineering / Agriculture / Medical / Veterinary Sciences. Column B is Languages / "
              "Humanities / Arts / Social Sciences / Library / Education / Physical Education / Commerce / Management "
              "and other related disciplines.")
    t2 = [r for r in R.TABLE_2 if r["kind"] in ("POINTS", "BAND")]
    rows, seen = [], set()
    for r in t2:
        if r["row_code"] == "PAPER_OTHER":
            continue
        a = b = _num(r["points"])
        if r["row_code"] == "PAPER_SCI_ENG":
            b = _num(next(x["points"] for x in t2 if x["row_code"] == "PAPER_OTHER"))
        rows.append([r["section"], r["description"], a, b, r["unit"] or "", r["authority_page"], BOX, ""])
        seen.add(r["row_code"])
    table(doc, ["S.N.", "Activity", "Column A", "Column B", "Unit", "Page", "Check", "Correction"],
          rows, [1.3, 12.2, 1.6, 1.6, 3.6, 1.5, 1.2, 3.0], size=8.5)
    para(doc, "Rows marked \"aug.\" are the list headed \"The Research score for research papers would be augmented "
              "as follows\" on page 106. See open question 1 in Section 10.", italic=True)

    doc.add_heading("8.2 Table 2: rules printed below the table (pages 106 to 107)", level=2)
    rows = []
    for r in R.TABLE_2:
        if r["kind"] in ("MULTIPLIER", "CAP", "CONSTRAINT"):
            value = "" if r["points"] is None else (f"{r['points'] * 100:g}%" if r["points"] < 1 else _num(r["points"]))
            rows.append([r["description"], value, r["authority_page"], BOX, ""])
    table(doc, ["Rule as we read it", "Value", "Page", "Check", "Correction"], rows, [15.5, 2.0, 1.8, 1.2, 5.5], size=8.5)

    doc.add_heading("8.3 Tables 3A and 3B: short-listing score for Assistant Professor (pages 107 to 109)", level=2)
    para(doc, "Table 3A is for universities and Table 3B is for colleges. MIT-ADT is a university, so the system will "
              "use Table 3A. Both are shown so that either can be checked. The 3rd Amendment, 2023 changed S.No. 3 from "
              "\"M.Phil.\" to \"M.Phil/LLM/M.Tech/M.Arch/M.E./M.V.Sc./M.D etc.\".")
    a = {r["row_code"]: r for r in R.TABLE_3A}
    b = {r["row_code"]: r for r in R.TABLE_3B}

    def cell(r) -> str:
        if r is None:
            return ""
        if r["kind"] == "CAP":
            return f"max {_num(r['max_points'])}"
        if r["kind"] == "CONSTRAINT":
            return "applies"
        out = _num(r["points"])
        if r["unit"]:
            out += f" {r['unit']}"
        if r["max_points"] is not None:
            out += f", max {_num(r['max_points'])}"
        return out

    rows = [[r["section"], r["description"], cell(r), cell(b.get(code)),
             f"{r['authority_page']} / {b[code]['authority_page']}" if code in b else r["authority_page"], BOX, ""]
            for code, r in a.items()]
    table(doc, ["S.N.", "Item", "Table 3A (universities)", "Table 3B (colleges)", "Pages (3A / 3B)", "Check", "Correction"],
          rows, [1.8, 10.6, 3.6, 3.6, 2.6, 1.2, 2.6], size=8.5)

    # --- 9: AICTE -------------------------------------------------------------
    doc.add_page_break()
    doc.add_heading("9. AICTE: minimum qualifications for direct recruitment in technical institutions", level=1)
    aicte = next(v for v in R.RULE_VERSIONS if v["code"] == R.AICTE)
    para(doc, "Source: " + aicte["instrument_name"] + ". " + aicte["notification_no"] + ". " + aicte["gazette_ref"] + ".")
    para(doc, "MIT-ADT's HR portal publishes pages 32 to 37 of this gazette under the label \"AICTE Norms\". The text "
              "there matches the gazette on the AICTE website. This Regulation sets no NET/SET requirement, no "
              "Research Score and no short-listing score.")
    doc.add_heading("9.1 Assistant Professor, by discipline (clause 5.1, pages 33 to 34)", level=2)
    groups = {"ENGINEERING_TECHNOLOGY": "Engineering / Technology", "MANAGEMENT": "Management", "PHARMACY": "Pharmacy",
              "MCA": "MCA", "HMCT": "Hotel Management and Catering Technology", "ARCHITECTURE": "Architecture",
              "TOWN_PLANNING": "Town Planning", "DESIGN": "Design", "FINE_ARTS": "Fine Arts",
              "SCIENCE_HUMANITIES": "Science and Humanities", "TECHNICAL": "All AICTE disciplines"}
    rows = [[groups[r["discipline_group"]], r["notes"], reading(r["criteria"]), r["authority_clause"].replace("AICTE ", ""),
             r["authority_page"], BOX, ""]
            for r in AICTE_RUBRIC if r["designation"] == "ASSISTANT_PROFESSOR"]
    table(doc, ["Discipline", "Requirement as printed", "How the software reads it", "Clause", "Page", "Check", "Correction"],
          rows, [3.0, 10.4, 6.2, 1.6, 1.3, 1.1, 2.4], size=8.5)
    para(doc, "Please check both columns: that the printed requirement matches the gazette, and that our reading of it "
              "is right. Where the gazette's sentence can be read two ways (Management and Design in particular), the "
              "third column shows which way the software reads it.", italic=True)
    doc.add_heading("9.2 Associate Professor and Professor, direct recruitment (clause 5.2, pages 35 to 36)", level=2)
    rows = [[names[r["designation"]], "Yes" if r["requires_phd"] else "No", _num(r["min_years"]), _num(r["min_publications"]),
             r["notes"], reading(r["criteria"]), r["authority_clause"].replace("AICTE ", ""), r["authority_page"], BOX, ""]
            for r in AICTE_RUBRIC if r["designation"] != "ASSISTANT_PROFESSOR"]
    table(doc, ["Post", "Ph.D. required", "Min. years", "Min. publications", "Requirement as printed", "How the software reads it",
                "Clause", "Page", "Check", "Correction"], rows, [2.2, 1.4, 1.1, 1.5, 8.4, 5.4, 1.8, 1.2, 1.1, 1.9], size=8.5)
    para(doc, "Senior Professor appears in this Regulation for promotion only (clause 5.2(e), page 37), so no direct "
              "recruitment rule is loaded for it.", italic=True)
    doc.add_heading("9.3 What counts as First Class, and how a CGPA is read (clause 7.3, page 39)", level=2)
    rows = [[r["description"], _num(r["band_min"]), _num(r["points"]) + "%", r["authority_page"], BOX, ""]
            for r in R.AICTE_GRADE_RULES]
    table(doc, ["Rule as printed", "Grade point", "Percentage", "Page", "Check", "Correction"], rows,
          [13.0, 2.2, 2.2, 1.6, 1.2, 5.8], size=8.5)

    # --- 10 ------------------------------------------------------------------
    doc.add_page_break()
    doc.add_heading("10. Questions the gazettes do not answer", level=1)
    para(doc, "We have not resolved any of these. The software stores the values exactly as printed and will not "
              "compute a score that depends on an unanswered question. For each one, please write your decision, or "
              "who should decide (for example the HR department or the Dean, Academic Affairs).")
    skip = {"ENGINEERING_NOT_IN_UGC_CL4"}
    for n, o in enumerate((o for o in R.OPEN_POINTS if o["code"] not in skip), start=1):
        doc.add_heading(f"Question {n}", level=3)
        labelled(doc, "Where in the gazette: ", o["where"])
        para(doc, o["question"])
        table(doc, ["Your decision, or who should decide"], [["\n\n"]], [26.0], size=10.5)

    # --- 10 ------------------------------------------------------------------
    doc.add_heading("11. A finding we need you to confirm: engineering and technology posts", level=1)
    eng = next(o for o in R.OPEN_POINTS if o["code"] == "ENGINEERING_NOT_IN_UGC_CL4")
    labelled(doc, "Where: ", eng["where"])
    para(doc, "Clause 4 of the UGC Regulations sets qualifications discipline by discipline: 4.1 for arts, sciences and "
              "related disciplines, 4.2 for music and performing arts, 4.3 for drama, 4.4 for yoga, and further clauses "
              "for occupational therapy, physiotherapy, librarians and physical education. There is no clause for "
              "engineering and technology.")
    para(doc, "UGC clause 1.1 (page 57) says that for technical education, among other fields, the norms laid down by "
              "the authority established for that field shall prevail. AICTE's 2019 Regulation (clause 1.2, page 25) "
              "applies to all degree-level technical institutions and universities imparting technical education.")
    para(doc, "Our reading is therefore that applicants to the computing, engineering and technology schools are "
              "assessed under the AICTE Regulation in Section 9, not under UGC clause 4.1. Our project design had "
              "described AICTE as an addition on top of the UGC rules. The practical difference is large:")
    table(doc, ["For an Assistant Professor post", "Under UGC clause 4.1", "Under AICTE clause 5.1(a)"], [
        ["NET / SET / SLET", "Required, unless exempt by a compliant Ph.D.", "Not required"],
        ["Marks", "55% at Master's level", "First Class (60%, or CGPA 6.75) in the Bachelor's or the Master's degree"],
        ["Ph.D.", "Not required", "Not required"],
    ], [8.0, 9.0, 9.0], size=9.5)
    para(doc, "We would like you to confirm three points:", bold=True)
    bullets(doc, [
        "Is this reading correct for MIT-ADT's schools of Computing, Artificial Intelligence, Engineering and Sciences, "
        "Bio-Engineering, Food Technology, Design, and the management and computer applications schools?",
        "Which later AICTE clarifications or amendments does the university apply for direct recruitment?",
        "The Maharashtra Government Resolution that HR publishes as \"UGC Norms\" differs from the UGC gazette in three "
        "places (see the last question in Section 10). Should HR be informed?",
    ])
    table(doc, ["Your confirmation or advice"], [["\n\n\n"]], [26.0], size=10.5)

    # --- 12 ------------------------------------------------------------------
    doc.add_heading("12. Declaration", level=1)
    para(doc, "I have compared the values in Sections 5 to 9 of this sheet with the UGC and AICTE gazette notifications "
              "listed in Section 2.")
    bullets(doc, [
        f"{BOX}  All values agree with the gazette.",
        f"{BOX}  All values agree except those I have corrected in the Correction column.",
        f"{BOX}  I have recorded my decision, or who should decide, for each question in Section 10.",
        f"{BOX}  I have given my confirmation or advice on Section 11.",
    ], style="List Paragraph")
    table(doc, ["Remarks, including any rule that is missing from this sheet"], [["\n\n\n"]], [26.0], size=10.5)
    table(doc, ["Name", "Designation", "Signature", "Date"], [["\n", "", "", ""]], [7.0, 7.0, 7.0, 5.0], size=10.5)
    para(doc, "This sheet is generated from the rule data the software loads (backend/rules_data.py). Corrections "
              "returned on it will be applied there, and a corrected sheet re-issued.", italic=True, size=9)
    return doc


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    build().save(OUT)
    print(f"wrote {OUT}")
