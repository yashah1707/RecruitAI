"""What the engine knows about one application.

Plain data, built once from the database (`build_facts`) or by hand in a
test. Nothing here decides anything.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.engine.rules import NO_OPENING
from backend.models import (
    Application,
    CandidateAchievement,
    CandidateEvent,
    CandidateExperience,
    CandidateGuidance,
    CandidatePublication,
    CandidateQualification,
)


@dataclass(frozen=True)
class Bounds:
    """A quantity known only to lie between `low` and `high`. `high` None means no upper limit is known."""

    low: float
    high: float | None

    @classmethod
    def exactly(cls, value: float) -> "Bounds":
        return cls(value, value)

    @property
    def exact(self) -> float | None:
        return self.low if self.high is not None and abs(self.high - self.low) < 1e-9 else None

    def __str__(self) -> str:
        if self.exact is not None:
            return f"{self.low:g}"
        return f"at least {self.low:g}" if self.high is None else f"between {self.low:g} and {self.high:g}"


@dataclass(frozen=True)
class Period:
    """A date known to a year, a month or a day: the earliest and latest day it can mean."""

    earliest: date
    latest: date


_PARTIAL_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def parse_period(text: str | None) -> Period | None:
    """ "2014", "2014-06" or "2014-06-20" as the span of days it can mean. Anything else is unknown."""
    m = _PARTIAL_DATE_RE.match((text or "").strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), m.group(2), m.group(3)
    try:
        if day:
            exact = date(year, int(month), int(day))
            return Period(exact, exact)
        if month:
            return Period(date(year, int(month), 1), date(year, int(month), calendar.monthrange(year, int(month))[1]))
        return Period(date(year, 1, 1), date(year, 12, 31))
    except ValueError:
        return None


@dataclass
class Degree:
    level: str  # UG / PG / PhD
    name: str | None = None
    marks_pct: float | None = None
    cgpa: float | None = None
    division: str | None = None
    completed: Period | None = None
    registered: Period | None = None  # research degrees


@dataclass
class Post:
    kind: str  # TEACHING / RESEARCH / INDUSTRY / OTHER
    designation: str | None = None
    start: Period | None = None
    end: Period | None = None  # None with is_current False means not stated
    is_current: bool = False


@dataclass
class Paper:
    kind: str  # JOURNAL / CONFERENCE / BOOK_CHAPTER / BOOK / OTHER
    year: int | None = None
    cleared_review: bool = True
    author_count: int | None = None
    is_first_author: bool | None = None
    impact_factor: float | None = None


@dataclass
class Item:
    """An award, patent, project, talk or guidance statement, reduced to what Table 2 scores."""

    kind: str  # PATENT / AWARD / FUNDED_PROJECT / GRANT / TALK / GUIDANCE_PHD / GUIDANCE_PG
    level: str | None = None  # INTERNATIONAL / NATIONAL / STATE / UNIVERSITY
    status: str | None = None  # as stated
    amount_inr: float | None = None
    count: int | None = None


@dataclass
class Facts:
    designation: str
    discipline_group: str = "GENERAL"
    regulator_id: str = "UGC"
    regulator_implemented: bool = True
    as_of: date = field(default_factory=date.today)
    institution_state: str = "Maharashtra"
    # from the application form (or entered at Gate 1); None = not known
    category: str | None = None
    differently_abled: bool | None = None
    study_leave_taken: bool | None = None
    # from the reading, as checked at Gate 1
    highest_degree: str | None = None
    phd_status: str | None = None
    phd_regulation: str | None = None
    masters_marks_pct: float | None = None
    masters_cgpa: float | None = None
    masters_awarded: Period | None = None
    phd_awarded: Period | None = None
    net_set_status: str | None = None
    set_state: str | None = None
    stated_teaching_years: float | None = None
    publications_count: int | None = None
    degrees: list[Degree] = field(default_factory=list)
    posts: list[Post] = field(default_factory=list)
    papers: list[Paper] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    # Set these to override what the engine would work out (used by tests
    # that pin one quantity, and by nothing else).
    research_score: Bounds | None = None
    experience_years: Bounds | None = None

    @property
    def has_phd(self) -> bool:
        return self.phd_status == "COMPLETED"


def _period_from_date(value: date | None, precision: str | None) -> Period | None:
    if value is None:
        return None
    if precision == "year":
        return Period(date(value.year, 1, 1), date(value.year, 12, 31))
    if precision == "month":
        return Period(value.replace(day=1), value.replace(day=calendar.monthrange(value.year, value.month)[1]))
    return Period(value, value)


def build_facts(session: Session, application: Application, as_of: date | None = None) -> Facts:
    """Everything the engine may use for one application, read from the database."""
    from backend import settings

    e = application.extracted
    school = application.school
    opening = application.opening
    app_id = application.application_id

    def rows(table):
        return session.scalars(select(table).where(table.application_id == app_id)).all()

    f = Facts(
        designation=application.applied_designation,
        discipline_group=opening.discipline_group if opening is not None else NO_OPENING,
        regulator_id=school.regulator_id,
        regulator_implemented=school.regulator.is_implemented,
        as_of=as_of or date.today(),
        institution_state=settings.INSTITUTION_STATE,
        category=application.category,
        differently_abled=application.differently_abled,
        study_leave_taken=application.study_leave_taken,
    )
    if e is not None:
        f.highest_degree, f.phd_status, f.phd_regulation = e.highest_degree, e.phd_status, e.phd_regulation
        f.masters_marks_pct, f.masters_cgpa = e.marks_pct, e.cgpa
        f.masters_awarded = _period_from_date(e.masters_award_date, e.masters_award_date_precision)
        f.phd_awarded = _period_from_date(e.phd_award_date, e.phd_award_date_precision)
        f.net_set_status, f.set_state = e.net_set_status, e.set_state
        f.stated_teaching_years, f.publications_count = e.teaching_years, e.publications_count

    for q in rows(CandidateQualification):
        f.degrees.append(Degree(
            level=q.degree_level, name=q.degree, marks_pct=q.marks_pct, cgpa=q.cgpa, division=q.division,
            completed=parse_period(q.completion_stated), registered=parse_period(q.registration_stated),
        ))
    for x in rows(CandidateExperience):
        f.posts.append(Post(kind=x.experience_type, designation=x.designation_held, start=parse_period(x.start_stated),
                            end=parse_period(x.end_stated), is_current=x.is_current))
    for p in rows(CandidatePublication):
        f.papers.append(Paper(kind=p.publication_type, year=p.year, cleared_review=p.status in ("PUBLISHED", "ACCEPTED"),
                              author_count=p.author_count, is_first_author=p.is_first_author, impact_factor=p.impact_factor))
    for a in rows(CandidateAchievement):
        if a.kind in ("PATENT", "AWARD", "FUNDED_PROJECT", "GRANT"):
            f.items.append(Item(kind=a.kind, level=a.level, status=a.status, amount_inr=a.amount_inr))
    for v in rows(CandidateEvent):
        if v.role in ("RESOURCE_PERSON", "PRESENTED"):
            f.items.append(Item(kind="TALK", level=v.level))
    for g in rows(CandidateGuidance):
        if g.level in ("PHD", "PG"):
            f.items.append(Item(kind=f"GUIDANCE_{g.level}", status=g.description, count=g.student_count))
    return f
