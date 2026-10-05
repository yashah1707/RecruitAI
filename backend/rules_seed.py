"""Load the statutory rules in backend/rules_data.py into the database, and
write the human-checkable transcription sheet.

    python -m backend.rules_seed                          load / refresh the rules
    python -m backend.rules_seed --write-transcription    also rewrite docs/ugc_rules_transcription.md

Loading is idempotent: rows are matched on their codes and updated in place,
so a corrected value replaces the old one and nothing is duplicated.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import rules_data as R
from backend.models import RelaxationRule, RubricRule, RuleVersion, ScoreRule

TRANSCRIPTION_PATH = Path(__file__).resolve().parent.parent / "docs" / "ugc_rules_transcription.md"


def seed_rules(session: Session) -> dict[str, int]:
    versions: dict[str, RuleVersion] = {}
    for v in R.RULE_VERSIONS:
        row = session.scalar(select(RuleVersion).where(RuleVersion.code == v["code"])) or RuleVersion(code=v["code"])
        for key, value in v.items():
            setattr(row, key, value)
        session.add(row)
        versions[v["code"]] = row
    session.flush()

    for r in R.RUBRIC_RULES:
        version_id = versions[r["version"]].rule_version_id
        # One current rule per designation and discipline group: a rule that
        # moves to a later instrument replaces the earlier row instead of
        # sitting beside it.
        row = session.scalar(
            select(RubricRule).where(
                RubricRule.designation == r["designation"], RubricRule.discipline_group == r["discipline_group"]
            )
        ) or RubricRule()
        row.rule_version_id = version_id
        for key, value in r.items():
            if key != "version":
                setattr(row, key, value)
        session.add(row)

    for r in R.RELAXATION_RULES:
        row = session.scalar(select(RelaxationRule).where(RelaxationRule.code == r["code"])) or RelaxationRule()
        row.rule_version_id = versions[r["version"]].rule_version_id
        for key, value in r.items():
            if key != "version":
                setattr(row, key, value)
        session.add(row)

    for order, r in enumerate(R.SCORE_RULES):
        row = session.scalar(
            select(ScoreRule).where(ScoreRule.table_code == r["table_code"], ScoreRule.row_code == r["row_code"])
        ) or ScoreRule()
        row.rule_version_id = versions[r["version"]].rule_version_id
        row.sort_order = order
        for key, value in r.items():
            if key != "version":
                setattr(row, key, float(value) if key in ("points", "band_min", "band_max", "max_points") and value is not None else value)
        session.add(row)

    session.flush()

    # A row renamed or removed in rules_data.py must not linger in the
    # database as a rule nobody can see in the source. Instruments are kept:
    # stored evaluations cite them.
    keep_rubric = {(r["designation"], r["discipline_group"]) for r in R.RUBRIC_RULES}
    for row in session.scalars(select(RubricRule)):
        if (row.designation, row.discipline_group) not in keep_rubric:
            session.delete(row)
    keep_relax = {r["code"] for r in R.RELAXATION_RULES}
    for row in session.scalars(select(RelaxationRule)):
        if row.code not in keep_relax:
            session.delete(row)
    keep_score = {(r["table_code"], r["row_code"]) for r in R.SCORE_RULES}
    for row in session.scalars(select(ScoreRule)):
        if (row.table_code, row.row_code) not in keep_score:
            session.delete(row)
    session.flush()
    return {
        "rule_versions": len(R.RULE_VERSIONS),
        "rubric_rules": len(R.RUBRIC_RULES),
        "relaxation_rules": len(R.RELAXATION_RULES),
        "score_rules": len(R.SCORE_RULES),
    }


# --- the transcription sheet -------------------------------------------------


def _num(value) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def _yn(value: bool) -> str:
    return "Yes" if value else "No"


def render_transcription() -> str:
    """The rules as a document a person can check line by line against the gazette."""
    out: list[str] = [
        "# UGC and AICTE rules as loaded into RecruitAI: transcription for checking",
        "",
        "This file is generated from `backend/rules_data.py` by `python -m backend.rules_seed --write-transcription`.",
        "Do not edit it by hand. It lists every statutory value the system holds, with the gazette page it was read",
        "from, so that someone can open the gazette beside it and confirm each line.",
        "",
        "Read from the gazette PDFs on 2026-10-05 (UGC) and 2026-10-06 (AICTE). Page numbers are the printed gazette page numbers.",
        "",
        "**Checked by:** ______________________  **Date:** ____________",
        "",
        "## 1. Instruments",
        "",
        "| Code | Instrument | Notification no. | Gazette | Notified | In force | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in R.RULE_VERSIONS:
        out.append(
            f"| {v['code']} | {v['instrument_name']} | {v['notification_no'] or ''} | {v['gazette_ref'] or ''} | "
            f"{v['notified_date'] or ''} | {v['effective_from'] or ''} | {v['note'] or ''} |"
        )
    out += ["", "Source files (SHA-256 of the PDF that was read):", ""]
    for v in R.RULE_VERSIONS:
        if v["source_url"]:
            out.append(f"- {v['code']}: <{v['source_url']}> `{v['source_sha256']}`")

    out += [
        "",
        "## 2. Thresholds for direct recruitment",
        "",
        "Discipline group GENERAL is UGC cl. 4.1: Arts, Commerce, Humanities, Education, Law, Social Sciences, Sciences,",
        "Languages, Library Science, Physical Education, and Journalism & Mass Communication. Every other group is",
        "AICTE (Degree) Regulation, 2019, cl. 5.1 and 5.2, for technical institutions; TECHNICAL means all AICTE disciplines.",
        "",
        "| Designation | Discipline group | Ph.D. required | NET/SET required | Min. Master's % | Min. years | "
        "Min. publications | Research Score | Doctoral candidates guided | Clause | Page | Check |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in R.RUBRIC_RULES:
        out.append(
            f"| {r['designation']} | {r['discipline_group']} | {_yn(r['requires_phd'])} | {_yn(r['net_set_required'])} | "
            f"{_num(r['min_marks_pct'])} | {_num(r['min_years'])} | {_num(r['min_publications'])} | "
            f"{_num(r['research_score_threshold'])} | {_num(r['min_doctoral_guided'])} | {r['authority_clause']} | "
            f"{r['authority_page']} | ☐ |"
        )
    out += ["", "Wording recorded with each threshold:", ""]
    for r in R.RUBRIC_RULES:
        out.append(f"- **{r['designation']} / {r['discipline_group']}:** {r['notes']}")
    out += ["", "Structured criteria held for the AICTE rows (what the engine will read):", ""]
    for r in R.RUBRIC_RULES:
        if r["criteria"]:
            out.append(f"- **{r['designation']} / {r['discipline_group']}:** `{r['criteria']}`")

    out += [
        "",
        "## 3. Relaxations",
        "",
        "| Code | Relaxation | Levels | Categories | Condition | Clause | Page | Check |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in R.RELAXATION_RULES:
        cond = "; ".join(f"{k} = {v}" for k, v in (r["condition"] or {}).items())
        out.append(
            f"| {r['code']} | {_num(r['relaxation_pct'])}% | {', '.join(r['applies_to_levels'])} | "
            f"{', '.join(r['applies_to_categories'] or [])} | {cond} | {r['authority_clause']} | {r['authority_page']} | ☐ |"
        )
    out += [""]
    for r in R.RELAXATION_RULES:
        out.append(f"- **{r['code']}:** {r['description']}")

    for n, table in enumerate(("TABLE_2", "TABLE_3A", "TABLE_3B", "AICTE_7_3"), start=4):
        out += [
            "",
            f"## {n}. {R.TABLE_TITLES[table]}",
            "",
            "| S.N. | Item | Kind | Faculty group | Points | Unit | From | To (below) | Maximum | Categories | Page | Check |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in R.SCORE_RULES:
            if r["table_code"] != table:
                continue
            out.append(
                f"| {r['section']} | {r['description']} | {r['kind']} | {r['faculty_group']} | {_num(r['points'])} | "
                f"{r['unit'] or ''} | {_num(r['band_min'])} | {_num(r['band_max'])} | {_num(r['max_points'])} | "
                f"{', '.join(r['applies_to_categories'] or [])} | {r['authority_page']} | ☐ |"
            )
        if table == "TABLE_2":
            out += [
                "",
                "Faculty groups: SCI_ENG = Sciences / Engineering / Agriculture / Medical / Veterinary Sciences; "
                "OTHER = Languages / Humanities / Arts / Social Sciences / Library / Education / Physical Education / "
                "Commerce / Management and other related disciplines; ALL = the same figure in both columns.",
            ]

    out += ["", "## 8. Points the gazette leaves unsettled", "", "These are recorded, not resolved. Each needs a decision before a score that depends on it is computed.", ""]
    for o in R.OPEN_POINTS:
        out += [f"### {o['code']}", "", f"*Where:* {o['where']}", "", o["question"], "", "**Decision:** ______________________", ""]
    return "\n".join(out).rstrip() + "\n"


if __name__ == "__main__":
    from backend.db import get_engine

    with Session(get_engine()) as s:
        print(seed_rules(s))
        s.commit()
    if "--write-transcription" in sys.argv:
        TRANSCRIPTION_PATH.write_text(render_transcription(), encoding="utf-8")
        print(f"wrote {TRANSCRIPTION_PATH.name}")
