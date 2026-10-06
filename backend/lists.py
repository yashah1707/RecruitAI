"""Fixed lists of answers, shared by the form, the Reader and extraction review."""

from __future__ import annotations

import re

# Lists!Category in the data-model workbook.
CATEGORIES: tuple[str, ...] = ("General", "SC", "ST", "OBC-NCL", "EWS", "PwD")

# Lists!IndianStates in the data-model workbook.
STATES: tuple[str, ...] = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep",
    "Puducherry", "Outside India",
)


# Lists!Discipline in the data-model workbook.
DISCIPLINES: tuple[str, ...] = (
    "Computer Science & Engineering", "Information Technology", "Artificial Intelligence",
    "Electronics & Communication", "Electrical Engineering", "Mechanical Engineering", "Civil Engineering",
    "Chemical Engineering", "Bio-Engineering", "Food Technology", "Architecture", "Law", "Commerce", "Management",
    "Fine Arts", "Performing Arts", "Design", "Film & Television", "Drama", "Humanities", "Vedic Sciences",
    "Education", "Allied Healthcare Sciences", "Maritime Studies", "Other",
)

# Wording that names a listed discipline plainly. Checked in this order, so
# "Computer Science (Artificial Intelligence)" is Artificial Intelligence and
# "VLSI Design" is Electronics, not Design. A course none of these names is
# left unplaced for a person, never filed under "Other" by default.
_DISCIPLINE_WORDING: tuple[tuple[str, str], ...] = (
    ("Artificial Intelligence", r"artificial intelligence|machine learning|\bai\b"),
    ("Information Technology", r"information technology|\bit\b"),
    ("Electronics & Communication", r"electronics|telecommunication|vlsi|\be\s?&\s?tc\b|\bentc\b|\bece\b"),
    ("Computer Science & Engineering", r"computer|\bcse\b"),
    ("Electrical Engineering", r"electrical"),
    ("Mechanical Engineering", r"mechanical"),
    ("Civil Engineering", r"civil"),
    ("Chemical Engineering", r"chemical"),
    ("Bio-Engineering", r"bio[\s-]?engineering|biotechnology|biomedical"),
    ("Food Technology", r"food"),
    ("Law", r"\blaw\b|\bll\.?\s?[bm]\b"),
    ("Commerce", r"commerce|accountancy"),
    ("Management", r"management|business administration|\bmba\b"),
    ("Fine Arts", r"fine arts?|applied arts?"),
    ("Film & Television", r"film|television"),
    ("Drama", r"drama|theatre"),
    ("Performing Arts", r"performing|music|dance"),
    ("Vedic Sciences", r"vedic|sanskrit"),
    ("Allied Healthcare Sciences", r"physiotherapy|nursing|healthcare|optometry|radiology"),
    ("Maritime Studies", r"maritime|marine|nautical"),
    ("Education", r"education"),
    ("Architecture", r"architecture"),
    ("Design", r"design"),
    ("Humanities", r"humanities|english|history|philosophy|sociology|psychology|political science"),
)
_DISCIPLINE_PATTERNS = tuple((name, re.compile(pattern)) for name, pattern in _DISCIPLINE_WORDING)


def listed_discipline(course: str | None) -> str | None:
    """The workbook discipline a course's wording names, or None."""
    text = " ".join((course or "").lower().split())
    return next((name for name, pattern in _DISCIPLINE_PATTERNS if pattern.search(text)), None) if text else None


def canonical_state(text: str | None) -> str | None:
    """The list's own spelling of a State named in free text, or None."""
    wanted = " ".join((text or "").replace("&", "and").lower().split())
    return next((s for s in STATES if s.lower() == wanted), None)
