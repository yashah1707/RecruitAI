"""Named views of the same record (design document, Section 17.5).

HR sees every field. The interview panel sees what `view_field_visibility`
says it may, and that table is data: the university changes it on a page,
without a release.

Whether the panel sees contact details and reserved-category status is the
university's decision (Section 17.5 says so in terms). Until it is taken,
both are withheld, with the two things that would give them away: the resume
file, which carries the contact details, and the assessment's working, which
can name a category relaxation. Withholding is the choice that can be undone.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import ViewDefinition, ViewFieldVisibility

HR_VIEW, INTERVIEWER_VIEW = "HR_VIEW", "INTERVIEWER_VIEW"

# (entity, field, what it is in words, shown to the interview panel by default)
INTERVIEWER_PARTS: tuple[tuple[str, str, str, bool], ...] = (
    ("personal", "contact", "Phone number, email address and postal address", False),
    ("personal", "category", "Category and disability status", False),
    ("personal", "state", "State of residence", False),
    ("application", "resume", "The resume file itself (it carries the contact details)", False),
    ("assessment", "working", "The assessment's working, check by check (it can name a category relaxation)", False),
    ("assessment", "scores", "Experience counted, Research Score and short-listing score", True),
    ("assessment", "policy", "How the candidate stands against the university's own criteria", True),
    ("highlights", "*", "Highlights", True),
    ("qualifications", "*", "Qualifications", True),
    ("experience", "*", "Posts held", True),
    ("research", "*", "Research profile (citations, h-index)", True),
    ("publications", "*", "Publications", True),
    ("achievements", "*", "Patents, awards, projects and guidance", True),
    ("events", "*", "Seminars, workshops and courses", True),
    ("teaching", "*", "Subjects taught, skills and memberships", True),
)
_DESCRIPTIONS = {
    HR_VIEW: "Everything on the record. Not restricted by this table.",
    INTERVIEWER_VIEW: "What the interview panel sees of a short-listed candidate.",
}


def _key(entity: str, field: str) -> str:
    return f"{entity}.{field}"


def _view(session: Session, name: str) -> ViewDefinition:
    view = session.scalar(select(ViewDefinition).where(ViewDefinition.view_name == name))
    if view is None:
        view = ViewDefinition(view_name=name, description=_DESCRIPTIONS[name])
        session.add(view)
        session.flush()
    return view


def interviewer_visibility(session: Session) -> dict[str, bool]:
    """ "entity.field" -> whether the panel sees it. A part with no row yet gets its default, and the row is written."""
    view = _view(session, INTERVIEWER_VIEW)
    _view(session, HR_VIEW)
    stored = {_key(r.entity_name, r.field_name): r for r in session.scalars(
        select(ViewFieldVisibility).where(ViewFieldVisibility.view_id == view.view_id))}
    out = {}
    for entity, field, _, default in INTERVIEWER_PARTS:
        row = stored.get(_key(entity, field))
        if row is None:
            row = ViewFieldVisibility(view_id=view.view_id, entity_name=entity, field_name=field, is_visible=default)
            session.add(row)
        out[_key(entity, field)] = row.is_visible
    session.flush()
    return out


def set_interviewer_visibility(session: Session, shown: set[str]) -> None:
    """Record which parts the panel sees: exactly those named in `shown` ("entity.field")."""
    interviewer_visibility(session)  # every part has its row
    view = _view(session, INTERVIEWER_VIEW)
    for row in session.scalars(select(ViewFieldVisibility).where(ViewFieldVisibility.view_id == view.view_id)):
        row.is_visible = _key(row.entity_name, row.field_name) in shown
    session.flush()
