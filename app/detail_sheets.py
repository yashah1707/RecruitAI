"""The workbook's detail sheets: ranking, education, publications, events,
teaching and skills.

The `candidates` sheet is one wide row per person, for filtering and
shortlisting. These sheets are what a reviewer reads next: the score broken
into its parts, and each degree, publication and event on its own row.

Everything here is presentation. The values come from the extraction as the
resume states them; this module only puts them in one house style -- degree
spellings, capitalisation, the DD-MM-YYYY date convention, readable labels --
and never adds a fact. An item whose key text could not be found in the
resume (llm.postprocess) is kept and marked in the `check` column.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from app.excel_writer import HEADER_FILL, _clean
from app.ranking import DEFAULT_COMPONENTS, rank_candidates
from llm.interface import ExtractionResult, ResumeRecord
from llm.postprocess import format_degree, format_person_name, format_phd_status

NOT_FOUND = "Not found in resume text - verify"

_LEVEL_LABELS = {"UG": "Bachelor's", "PG": "Master's", "PhD": "Ph.D."}
_LEVEL_ORDER = {"UG": 0, "PG": 1, "PhD": 2}
_KEEP_UPPER = frozenset({"FDP", "STTP"})
_SMALL_WORDS = frozenset({"and", "of", "in", "for", "the", "on", "at", "to", "with", "a", "an", "by"})
_EDGE_JUNK = " \t\r\n,;:-–—•·*\"'“”‘’"
_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


# --- formatting --------------------------------------------------------------


def tidy(text: str | None) -> str | None:
    """Collapse whitespace and strip stray bullets/punctuation from the ends."""
    if text is None:
        return None
    # U+FFFD is a PDF en-dash whose encoding was lost: show it as a dash.
    cleaned = " ".join(str(text).replace("�", "-").split()).strip(_EDGE_JUNK)
    return cleaned or None


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
}
_DURATION_RE = re.compile(
    r"^(?P<n>\d+(?:\.\d+)?|[a-z]+)[\s-]*(?P<unit>hour|hr|day|week|month|year|yr)s?\b\.?$", re.IGNORECASE
)
_UNITS = {"hr": "hour", "yr": "year"}


def duration_text(text: str | None) -> str | None:
    """"One Week", "five day", "5-day", "1 day" -> "1 week", "5 days", "5 days", "1 day".

    Anything that is not a plain count of a unit (a date range, "3 years 2
    months") is left as written, only tidied.
    """
    cleaned = tidy(text)
    if not cleaned:
        return cleaned
    m = _DURATION_RE.match(cleaned)
    if not m:
        return cleaned
    raw = m.group("n").lower()
    if raw in _NUMBER_WORDS:
        n: float = _NUMBER_WORDS[raw]
    else:
        try:
            n = float(raw)
        except ValueError:
            return cleaned
    unit = _UNITS.get(m.group("unit").lower(), m.group("unit").lower())
    return f"{n:g} {unit}{'' if n == 1 else 's'}"


_DESIGNATION_PREFIX_RE = re.compile(
    r"^(?:presently|currently)?\s*(?:work(?:ed|ing)|serv(?:ed|ing)|join(?:ed)?)\s+as\s+(?:an?\s+|the\s+)?",
    re.IGNORECASE,
)
# "Assistant Professor - Computer Science & Engineering": what follows is the
# department, which is not the post.
_DESIGNATION_SUFFIX_RE = re.compile(r"\s+[-–—]\s+.*$|\s*\(.*$|,.*$")


def designation_text(text: str | None) -> str | None:
    """The post alone, capitalised: no "Worked as a", no department after it."""
    cleaned = tidy(text)
    if not cleaned:
        return cleaned
    cleaned = _DESIGNATION_SUFFIX_RE.sub("", _DESIGNATION_PREFIX_RE.sub("", cleaned)).strip(_EDGE_JUNK)
    shouting = cleaned.upper() == cleaned  # typed in capitals: no word is a deliberate acronym
    words = []
    for i, w in enumerate(cleaned.split()):
        if w.lower() in _SMALL_WORDS and i:
            words.append(w.lower())
        elif w.isupper() and len(w) <= 4 and not shouting:
            words.append(w)  # "IT Trainer", "HOD"
        else:
            words.append(w.capitalize())
    return " ".join(words) or None


_DIVISION_RANKS = (
    (("first", "1st", "1", "i"), "First Class"),
    (("second", "2nd", "2", "ii"), "Second Class"),
    (("third", "3rd", "3", "iii", "pass"), "Pass Class"),
)


def division_text(text: str | None) -> str | None:
    """"First", "Division 1", "FIRST CLASS" -> "First Class"; distinction kept.

    A status word that landed in the division column ("Appearing") is not a
    division and is blanked.
    """
    cleaned = tidy(text)
    if not cleaned:
        return cleaned
    words = set(re.findall(r"[a-z0-9]+", cleaned.lower()))
    if words & {"appearing", "pursuing", "awaited", "ongoing"}:
        return None
    distinction = " with Distinction" if "distinction" in words else ""
    for markers, shown in _DIVISION_RANKS:
        if words & set(markers):
            return shown + distinction
    return "Distinction" if distinction else title_text(cleaned)


def title_text(text: str | None) -> str | None:
    """A name or title in consistent capitalisation.

    Text that is already mixed-case is trusted as written (only tidied). Text
    typed entirely in capitals or entirely in lower case is put in title case,
    keeping short all-caps words as acronyms ("MIT", "SGB") and joining words
    ("of", "and") in lower case.
    """
    cleaned = tidy(text)
    if not cleaned:
        return cleaned
    letters = "".join(_WORD_RE.findall(cleaned))
    if not letters or not (letters.isupper() or letters.islower()):
        return cleaned
    was_upper = letters.isupper()

    def recase(match: re.Match) -> str:
        word = match.group(0)
        if word.lower() in _SMALL_WORDS and match.start() != 0:
            return word.lower()
        if was_upper and len(word) <= 3:
            return word  # an acronym in an all-caps line
        return word.capitalize()

    return _WORD_RE.sub(recase, cleaned)


def partial_date(text: str | None) -> str | None:
    """"2014-05-20" / "2021-05" / "2014" in the house convention; other text tidied."""
    cleaned = tidy(text)
    if not cleaned:
        return cleaned
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", cleaned)
    if not m:
        return cleaned
    year, month, day = m.groups()
    if day:
        return f"{day}-{month}-{year}"
    return f"{month}-{year}" if month else year


def year_only(text: str | None) -> str | None:
    cleaned = tidy(text)
    if not cleaned:
        return cleaned
    m = _YEAR_RE.search(cleaned)
    return m.group(0) if m else cleaned


def label(code: str | None) -> str | None:
    """"BOOK_CHAPTER" -> "Book Chapter", "RESOURCE_PERSON" -> "Resource Person", "FDP" stays."""
    if not code:
        return code
    return code if code in _KEEP_UPPER else code.replace("_", " ").title()


def degree_and_course(degree: str | None, course: str | None) -> str | None:
    return format_degree(" ".join(p for p in (degree, course) if p)) or None


def _check(found: bool) -> str | None:
    return None if found else NOT_FOUND


def _join(items: Sequence[str]) -> str | None:
    cleaned = [t for t in (tidy(i) for i in items) if t]
    return ", ".join(cleaned) or None


# --- row builders (plain data, so they are testable without a workbook) -----


def _ranked(records: Sequence[ResumeRecord]) -> list[tuple[int, str, ExtractionResult, ResumeRecord]]:
    """(rank, candidate name, result, record) for every extracted record, best first."""
    ranks = {s.source_filename: s for s in rank_candidates(list(records))}
    out = []
    for record in records:
        if record.result is None:
            continue
        name = format_person_name(_clean(record.result.candidate_name.value)) or _clean(record.source_filename)
        out.append((ranks[record.source_filename].rank, name, record.result, record))
    return sorted(out, key=lambda item: item[0])


RANKING_COLUMNS = (
    "rank", "candidate_name", "score", "out_of", "score_completeness",
    *(f"{c.name} (max {c.max_points:g})" for c in DEFAULT_COMPONENTS),
    "needs_review", "review_reasons", "source_filename",
)


def ranking_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    from app.excel_writer import _format_reasons
    from llm.confidence import evaluate

    scores = {s.source_filename: s for s in rank_candidates(list(records))}
    rows = []
    for rank, name, result, record in _ranked(records):
        s = scores[record.source_filename]
        points = []
        for comp in DEFAULT_COMPONENTS:
            p = comp.to_points(*[getattr(result, f).value for f in comp.fields])
            points.append(None if p is None else round(p, 1))
        outcome = evaluate(result, record.parse_error)
        rows.append([
            rank, name, s.score, s.max_possible, round(s.completeness, 2), *points,
            bool(outcome.needs_review), _format_reasons(outcome.reasons), _clean(record.source_filename),
        ])
    return rows


EDUCATION_COLUMNS = (
    "rank", "candidate_name", "level", "degree_and_course", "college", "university",
    "percentage", "cgpa", "division", "completed", "phd_status", "phd_thesis_title",
    "phd_guide", "phd_registered", "check",
)


def education_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    rows = []
    for rank, name, result, _ in _ranked(records):
        for e in sorted(result.education, key=lambda e: _LEVEL_ORDER.get(e.level, 9)):
            is_phd = e.level == "PhD"
            rows.append([
                rank, name, _LEVEL_LABELS.get(e.level, e.level),
                degree_and_course(e.degree, e.course),
                title_text(e.college), title_text(e.university),
                e.marks_pct, e.cgpa, division_text(e.division), partial_date(e.completion),
                format_phd_status(result.phd_status.value) if is_phd else None,
                title_text(e.thesis_title) if is_phd else None,
                format_person_name(tidy(e.guide)) if is_phd else None,
                partial_date(e.registration) if is_phd else None,
                _check(e.found_in_resume),
            ])
    return rows


PUBLICATION_COLUMNS = (
    "rank", "candidate_name", "published_count", "in_progress_count", "no",
    "title", "type", "journal_or_conference", "year", "status", "indexing", "check",
)


def publication_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    rows = []
    for rank, name, result, _ in _ranked(records):
        head = [rank, name, result.publications_count.value, result.publications_in_progress_count.value]
        if result.publications:
            for i, p in enumerate(result.publications, start=1):
                rows.append([
                    *head, i, title_text(p.title), label(p.kind), title_text(p.venue),
                    year_only(p.year), label(p.status), tidy(p.indexing), _check(p.found_in_resume),
                ])
            continue
        # A result from before the detail lists existed still has its titles.
        titles = [(t, "PUBLISHED") for t in (result.publication_titles.value or [])]
        titles += [(t, "UNDER_REVIEW") for t in (result.publications_in_progress_titles.value or [])]
        if not titles:
            rows.append([*head, None, None, None, None, None, None, None, None])
        for i, (t, status) in enumerate(titles, start=1):
            rows.append([*head, i, title_text(t), None, None, None, label(status), None, None])
    return rows


EVENT_COLUMNS = (
    "rank", "candidate_name", "no", "type", "title", "role", "organiser", "duration", "year", "check",
)


def event_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    rows = []
    for rank, name, result, _ in _ranked(records):
        for i, e in enumerate(result.events, start=1):
            rows.append([
                rank, name, i, label(e.kind), title_text(e.title), label(e.role),
                title_text(e.organiser), duration_text(e.duration), year_only(e.year), _check(e.found_in_resume),
            ])
    return rows


TEACHING_COLUMNS = (
    "rank", "candidate_name", "teaching_years", "subjects_count", "subjects_taught", "skills_count", "skills",
)


def teaching_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    rows = []
    for rank, name, result, _ in _ranked(records):
        subjects = [s for s in (title_text(x) for x in result.subjects_taught) if s]
        skills = [s for s in (tidy(x) for x in result.skills) if s]
        rows.append([
            rank, name, result.teaching_years_raw.value,
            len(subjects), _join(subjects), len(skills), _join(skills),
        ])
    return rows


EXPERIENCE_COLUMNS = (
    "rank", "candidate_name", "teaching_years_stated", "no", "designation", "institution",
    "type", "from", "to", "approx_years", "duration_as_stated", "check",
)

_PARTIAL_RE = re.compile(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")


def _year_month(text: str | None) -> tuple[int, int | None] | None:
    m = _PARTIAL_RE.fullmatch((text or "").strip())
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else None


def approx_years(start: str | None, end: str | None, as_of: datetime) -> float | None:
    """Length of a post in years, from its stated dates. Plain arithmetic.

    Month-accurate when both ends give a month, otherwise whole years. A
    current post is measured to the day the resume was processed. None when
    either end is unknown -- a gap is not filled with a guess.
    """
    a = _year_month(start)
    b = (as_of.year, as_of.month) if (end or "").strip().upper() == "PRESENT" else _year_month(end)
    if not a or not b:
        return None
    if a[1] and b[1]:
        months = (b[0] - a[0]) * 12 + (b[1] - a[1])
        return round(months / 12, 1) if months >= 0 else None
    years = b[0] - a[0]
    return float(years) if years >= 0 else None


def experience_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    rows = []
    for rank, name, result, record in _ranked(records):
        for i, e in enumerate(result.experience, start=1):
            present = (e.end or "").strip().upper() == "PRESENT"
            rows.append([
                rank, name, result.teaching_years_raw.value, i,
                designation_text(e.designation), title_text(e.institution), label(e.kind),
                partial_date(e.start), "Present" if present else partial_date(e.end),
                approx_years(e.start, e.end, record.processed_at), duration_text(e.duration),
                _check(e.found_in_resume),
            ])
    return rows


CONTACT_COLUMNS = ("rank", "candidate_name", "email", "phone", "source_filename")


def contact_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    return [
        [rank, name, (result.email or "").strip().lower() or None, tidy(result.phone), _clean(record.source_filename)]
        for rank, name, result, record in _ranked(records)
    ]


ACHIEVEMENT_COLUMNS = ("rank", "candidate_name", "no", "type", "title", "details", "year", "status", "check")


def achievement_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    rows = []
    for rank, name, result, _ in _ranked(records):
        for i, a in enumerate(result.achievements, start=1):
            rows.append([
                rank, name, i, label(a.kind), title_text(a.title), tidy(a.details),
                year_only(a.year), title_text(a.status), _check(a.found_in_resume),
            ])
    return rows


GUIDANCE_COLUMNS = ("rank", "candidate_name", "category", "level", "students", "detail", "check")
_GUIDANCE_LEVELS = {"PHD": "Ph.D.", "PG": "Master's", "UG": "Bachelor's", "OTHER": "Other"}


def guidance_rows(records: Sequence[ResumeRecord]) -> list[list[Any]]:
    rows = []
    for rank, name, result, _ in _ranked(records):
        for g in result.guidance:
            rows.append([
                rank, name, "Research guidance", _GUIDANCE_LEVELS.get(g.level, g.level),
                g.count, tidy(g.description), _check(g.found_in_resume),
            ])
        for m in result.memberships:
            rows.append([rank, name, "Professional membership", None, None, tidy(m), None])
    return rows


# --- writing -----------------------------------------------------------------

# (sheet name, columns, row builder, {column: width}, columns to wrap)
_SHEETS = (
    ("ranking", RANKING_COLUMNS, ranking_rows,
     {"candidate_name": 28, "review_reasons": 46, "source_filename": 34, "score_completeness": 18}, ("review_reasons",)),
    ("education", EDUCATION_COLUMNS, education_rows,
     {"candidate_name": 28, "degree_and_course": 40, "college": 38, "university": 34, "division": 24,
      "phd_thesis_title": 50, "phd_guide": 26, "phd_status": 16, "check": 30},
     ("degree_and_course", "college", "university", "phd_thesis_title")),
    ("publications", PUBLICATION_COLUMNS, publication_rows,
     {"candidate_name": 28, "title": 70, "journal_or_conference": 48, "type": 14, "status": 14,
      "indexing": 18, "check": 30, "published_count": 16, "in_progress_count": 17},
     ("title", "journal_or_conference")),
    ("seminars_workshops", EVENT_COLUMNS, event_rows,
     {"candidate_name": 28, "title": 64, "organiser": 44, "role": 16, "duration": 14, "check": 30},
     ("title", "organiser")),
    ("teaching_skills", TEACHING_COLUMNS, teaching_rows,
     {"candidate_name": 28, "subjects_taught": 80, "skills": 70, "teaching_years": 15,
      "subjects_count": 15, "skills_count": 12},
     ("subjects_taught", "skills")),
    ("experience", EXPERIENCE_COLUMNS, experience_rows,
     {"candidate_name": 28, "designation": 30, "institution": 48, "type": 12, "from": 12, "to": 12,
      "teaching_years_stated": 22, "approx_years": 14, "duration_as_stated": 22, "check": 30},
     ("designation", "institution")),
    ("patents_awards_projects", ACHIEVEMENT_COLUMNS, achievement_rows,
     {"candidate_name": 28, "type": 16, "title": 64, "details": 50, "status": 16, "check": 30},
     ("title", "details")),
    ("guidance_memberships", GUIDANCE_COLUMNS, guidance_rows,
     {"candidate_name": 28, "category": 26, "level": 12, "detail": 80, "check": 30}, ("detail",)),
    ("contact_details", CONTACT_COLUMNS, contact_rows,
     {"candidate_name": 28, "email": 36, "phone": 20, "source_filename": 34}, ()),
)

SHEET_NAMES = tuple(s[0] for s in _SHEETS)


def add_detail_sheets(wb: Workbook, records: Sequence[ResumeRecord], first_index: int = 1) -> None:
    """Insert the detail sheets right after the main sheet, in a fixed order."""
    for offset, (name, columns, builder, widths, wrapped) in enumerate(_SHEETS):
        ws = wb.create_sheet(name, first_index + offset)
        ws.append(list(columns))
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(vertical="center")
        for row in builder(records):
            ws.append([_clean(v) if isinstance(v, str) else v for v in row])

        wrap_idx = {columns.index(c) + 1 for c in wrapped}
        for i, column in enumerate(columns, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(column, max(10, len(column) + 3))
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=cell.column in wrap_idx)
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(ws.max_row, 1)}"
