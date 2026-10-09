"""Highlights: what sets a candidate apart, pointed out so HR need not read the whole record (Section 17.4).

Three are worked out by fixed rules, each time the record is shown, so they
are never out of date:

  * a Ph.D. from an institution the reference list marks PREMIER;
  * an h-index or citation count above the norm the university has set;
  * a first-author publication in a journal whose impact factor is above the norm.

The norms are the university's figures (`highlight_norms`), set on a page.
None is built in: with no norm set, the two research rules point nothing out.
A person may add a highlight of their own for anything the rules do not
anticipate; only those are stored (`candidate_highlights`, auto_generated false).

A highlight is a pointer for the reader, taken from what the resume states.
It is not verified and it decides nothing: no rule or score reads it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import (
    Application,
    CandidateHighlight,
    CandidatePublication,
    CandidateQualification,
    CandidateResearchProfile,
    HighlightNorm,
    InstitutionMaster,
    School,
)


class HighlightError(Exception):
    """A highlight or a norm cannot be saved as entered. The message is for the person."""


@dataclass
class Highlight:
    kind: str
    description: str
    automatic: bool
    highlight_id: int | None = None  # set for one a person added, so it can be removed


def norm_for(session: Session, school_id: str | None) -> HighlightNorm | None:
    """The school's own norm if it has one, else the university-wide one."""
    own = session.scalar(select(HighlightNorm).where(HighlightNorm.school_id == school_id)) if school_id else None
    return own or session.scalar(select(HighlightNorm).where(HighlightNorm.school_id.is_(None)))


def for_application(session: Session, application: Application) -> list[Highlight]:
    found: list[Highlight] = []
    app_id = application.application_id

    for q in session.scalars(select(CandidateQualification).where(
            CandidateQualification.application_id == app_id, CandidateQualification.degree_level == "PhD",
            CandidateQualification.institution_id.is_not(None))):
        institution = session.get(InstitutionMaster, q.institution_id)
        if institution is not None and institution.tier == "PREMIER":
            found.append(Highlight("PREMIER_PHD", f"Ph.D. from a premier institute: {institution.institution_name}", True))

    norm = norm_for(session, application.school_id)
    research = session.get(CandidateResearchProfile, application.candidate_id)
    if norm is not None and research is not None:
        if norm.min_h_index is not None and research.h_index is not None and research.h_index > norm.min_h_index:
            found.append(Highlight("RESEARCH_PROFILE", f"h-index of {research.h_index}, above the norm of {norm.min_h_index}", True))
        if norm.min_citations is not None and research.total_citations is not None and research.total_citations > norm.min_citations:
            found.append(Highlight("RESEARCH_PROFILE", f"{research.total_citations} citations, above the norm of {norm.min_citations}", True))
    if norm is not None and norm.min_impact_factor is not None:
        papers = session.scalars(select(CandidatePublication).where(
            CandidatePublication.application_id == app_id, CandidatePublication.is_first_author.is_(True),
            CandidatePublication.impact_factor > norm.min_impact_factor).order_by(CandidatePublication.impact_factor.desc())).all()
        for p in papers[:3]:
            found.append(Highlight("HIGH_IMPACT_PAPER", f"First-author publication, impact factor {p.impact_factor:g} "
                                                        f"(above the norm of {norm.min_impact_factor:g}): {p.title[:120]}", True))

    for row in session.scalars(select(CandidateHighlight).where(
            CandidateHighlight.candidate_id == application.candidate_id, CandidateHighlight.auto_generated.is_(False))
            .order_by(CandidateHighlight.id)):
        found.append(Highlight(row.highlight_type, row.description, False, row.id))
    return found


def add(session: Session, application: Application, description: str, actor: str) -> CandidateHighlight:
    description = " ".join((description or "").split())
    if len(description) < 5 or len(description) > 400:
        raise HighlightError("Write the highlight in 5 to 400 characters.")
    row = CandidateHighlight(candidate_id=application.candidate_id, highlight_type="ADDED_BY_HR", source_reference=actor[:80],
                             auto_generated=False, description=description)
    session.add(row)
    session.flush()
    return row


def remove(session: Session, application: Application, highlight_id: int) -> None:
    row = session.get(CandidateHighlight, highlight_id)
    if row is not None and row.candidate_id == application.candidate_id and not row.auto_generated:
        session.delete(row)
        session.flush()


def _number(text: str, label: str, whole: bool):
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = int(text) if whole else float(text)
    except ValueError:
        raise HighlightError(f"{label}: enter a {'whole ' if whole else ''}number, or leave it empty.") from None
    if value != value or value < 0 or value > 1_000_000:
        raise HighlightError(f"{label}: enter a number from 0 upwards.")
    return value


def set_norm(session: Session, school_id: str | None, h_index: str, citations: str, impact_factor: str) -> HighlightNorm | None:
    """Set the norms for one school, or for every school (`school_id` None). All three left empty removes the row."""
    if school_id is not None and session.get(School, school_id) is None:
        raise HighlightError("Choose a school, or every school.")
    values = (_number(h_index, "h-index", True), _number(citations, "Citations", True), _number(impact_factor, "Impact factor", False))
    condition = HighlightNorm.school_id == school_id if school_id else HighlightNorm.school_id.is_(None)
    row = session.scalar(select(HighlightNorm).where(condition))
    if all(v is None for v in values):
        if row is not None:
            session.delete(row)
            session.flush()
        return None
    if row is None:
        row = HighlightNorm(school_id=school_id)
        session.add(row)
    row.min_h_index, row.min_citations, row.min_impact_factor = values
    session.flush()
    return row
