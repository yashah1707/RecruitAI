"""Reference data: regulators and schools.

Transcribed from Sections 5.3 and 7.3 of the design document. These are seed
values for configuration tables, not constants: HR has yet to confirm the
school list (Section 7.4), and correcting it is an edit to these rows.

Idempotent: running it again updates existing rows and adds missing ones.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import InstitutionMaster, Regulator, School

# (id, name, norms document, is_implemented)
REGULATORS: tuple[tuple[str, str, str | None, bool], ...] = (
    ("UGC", "University Grants Commission", "UGC Regulations, 2018, as amended", True),
    ("AICTE", "All India Council for Technical Education", "AICTE norms (overlay on the UGC floor)", True),
    ("BCI", "Bar Council of India", "BCI norms", False),
    ("COA", "Council of Architecture", "COA norms", False),
    ("DGS", "Directorate General of Shipping", "DG Shipping norms", False),
    ("NCTE", "National Council for Teacher Education", None, False),
)

# (id, name, faculty, primary regulator, overlay, open point to confirm with HR)
SCHOOLS: tuple[tuple[str, str, str, str, str | None, str | None], ...] = (
    ("SCH-001", "MIT School of Fine Arts & Applied Arts", "Arts, Fine Arts and Performing Arts", "UGC", None, None),
    ("SCH-002", "MIT Vishwashanti Sangeet Kala Academy", "Arts, Fine Arts and Performing Arts", "UGC", None, None),
    ("SCH-003", "MIT School of Film & Television", "Film and Media Studies", "UGC", None, None),
    ("SCH-004", "MIT School of Drama", "Film and Media Studies", "UGC", None, None),
    ("SCH-005", "MIT Institute of Design", "Design", "UGC", "AICTE", None),
    ("SCH-006", "MIT School of Architecture & Planning", "Architecture and Planning", "COA", None, None),
    ("SCH-007", "MIT School of Law", "Law", "BCI", None, None),
    ("SCH-008", "MIT School of Computing", "Computer Science and IT", "UGC", "AICTE", None),
    ("SCH-009", "MIT School of Artificial Intelligence", "Computer Science and IT", "UGC", "AICTE", None),
    ("SCH-010", "MIT School of Engineering and Sciences", "Engineering and Sciences", "UGC", "AICTE", None),
    ("SCH-011", "MIT School of Education & Research", "Humanities and Social Sciences", "UGC", None,
     "NCTE may govern teacher-education posts; confirm with HR"),
    ("SCH-012", "MIT School of Vedic Sciences", "Humanities and Social Sciences", "UGC", None, None),
    ("SCH-013", "MIT School of Humanities", "Humanities and Social Sciences", "UGC", None, None),
    ("SCH-014", "MIT School of Indian Civil Services", "Humanities and Social Sciences", "UGC", None, None),
    ("SCH-015", "School of Allied Healthcare Sciences", "Allied Healthcare Sciences", "UGC", None,
     "Governing council for faculty qualifications to be confirmed with HR"),
    ("SCH-016", "School of Business & Computer Applications", "Commerce and Management", "UGC", "AICTE", None),
    ("SCH-017", "College of Management & Computer Applications", "Commerce and Management", "UGC", "AICTE", None),
    ("SCH-018", "Centre of Distance and Online Education", "Commerce and Management", "UGC", None,
     "UGC ODL Regulations; confirm whether it recruits teaching faculty in its own right"),
    ("SCH-019", "Maharashtra Academy of Naval Education & Training", "Maritime Studies", "DGS", None, None),
    ("SCH-020", "MIT School of Bio-Engineering Sciences and Research", "Technology", "UGC", "AICTE", None),
    ("SCH-021", "MIT School of Food Technology", "Technology", "UGC", "AICTE", None),
)


# The four rows of the data-model workbook's Institutions_Master sheet, as
# given there. A starting point only: the list is HR's to extend, with
# `python -m backend.institutions <file.csv>`. No institution is added here
# from general knowledge, and none of these has an alias.
INSTITUTIONS: tuple[tuple[str, str, str], ...] = (
    ("Indian Institute of Technology Bombay", "PREMIER", "IIT"),
    ("National Institute of Technology Trichy", "PREMIER", "NIT"),
    ("Indian Institute of Science", "PREMIER", "IISc"),
    ("Savitribai Phule Pune University", "STATE", "State University"),
)


def seed_reference_data(session: Session) -> dict[str, int]:
    """Insert or update regulators, schools and institutions; returns how many of each exist."""
    for regulator_id, name, ref, implemented in REGULATORS:
        row = session.get(Regulator, regulator_id) or Regulator(regulator_id=regulator_id)
        row.name, row.norms_document_ref, row.is_implemented = name, ref, implemented
        session.add(row)
    session.flush()

    for school_id, name, faculty, regulator, overlay, note in SCHOOLS:
        row = session.get(School, school_id) or School(school_id=school_id)
        row.name, row.faculty = name, faculty
        row.regulator_id, row.overlay_regulator_id = regulator, overlay
        row.confirmation_note = note
        if row.is_hiring_unit is None:
            row.is_hiring_unit = True
        if row.login_required is None:
            row.login_required = True
        session.add(row)
    session.flush()

    # Added if missing, and otherwise left alone: HR may have changed a tier.
    known = set(session.scalars(select(InstitutionMaster.institution_name)))
    for name, tier, category in INSTITUTIONS:
        if name not in known:
            session.add(InstitutionMaster(institution_name=name, tier=tier, category=category))
    session.flush()
    return {"regulators": len(REGULATORS), "schools": len(SCHOOLS), "institutions": len(INSTITUTIONS)}


if __name__ == "__main__":  # python -m backend.seed
    from sqlalchemy.orm import Session as _Session

    from backend.db import get_engine

    with _Session(get_engine()) as s:
        print(seed_reference_data(s))
        s.commit()
