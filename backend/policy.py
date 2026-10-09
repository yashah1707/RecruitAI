"""The university's own criteria, on top of the statutory minimum (design document, Section 17.7).

The Regulations set a floor. The university may ask for more: a higher
marks cut-off, a minimum h-index, a Ph.D. where the Regulations do not
require one. Those criteria are rows in `university_policy_rules`, entered
on a page; none is built in.

Standing instruction of Section 17.7: this layer "may add criteria or raise a
threshold, but must never be used to admit a candidate the statutory rule
engine has marked NOT_ELIGIBLE". It is kept by construction. The criteria are
checked after the statutory decision is made, their results are stored and
shown beside it, and nothing here can change that decision or the
application's state. A person reads both and decides (Gate 2).

Each criterion is checked by fixed code against the same facts the engine
used, with the same three answers: met, not met, or cannot be told.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.engine.decision import FAIL, PASS, RANK_NAMES, UNKNOWN, Decision, at_least
from backend.engine.facts import UNSURE, Bounds, Facts
from backend.models import Application, CandidateResearchProfile, School, UniversityPolicyRule

# criterion -> (what it is in words, whether it takes a figure)
CRITERIA: dict[str, tuple[str, bool]] = {
    "MIN_MASTERS_MARKS_PCT": ("Master's marks of at least (per cent)", True),
    "MIN_EXPERIENCE_YEARS": ("Experience of at least (years), counted as for the statutory rule", True),
    "MIN_PUBLICATIONS": ("Publications that have cleared review, at least", True),
    "MIN_H_INDEX": ("h-index of at least", True),
    "MIN_CITATIONS": ("Total citations of at least", True),
    "PHD_REQUIRED": ("A Ph.D. is required", False),
}
_LIMITS = {"MIN_MASTERS_MARKS_PCT": 100, "MIN_EXPERIENCE_YEARS": 60, "MIN_PUBLICATIONS": 2000, "MIN_H_INDEX": 500, "MIN_CITATIONS": 1_000_000}


class PolicyError(Exception):
    """A criterion cannot be saved as entered. The message is for the person."""


def add_rule(session: Session, *, school_id: str | None, designation: str, criterion: str, value: str, actor: str) -> UniversityPolicyRule:
    if school_id is not None and session.get(School, school_id) is None:
        raise PolicyError("Choose a school, or every school.")
    if designation not in RANK_NAMES:
        raise PolicyError("Choose the post the criterion is for.")
    if criterion not in CRITERIA:
        raise PolicyError("Choose a criterion.")
    stored = "yes"
    if CRITERIA[criterion][1]:
        try:
            number = float((value or "").strip())
        except ValueError:
            raise PolicyError("Enter the figure as a number.") from None
        if number != number or number <= 0 or number > _LIMITS[criterion]:
            raise PolicyError(f"Enter a figure above 0 and up to {_LIMITS[criterion]:g}.")
        stored = f"{number:g}"
    same_scope = UniversityPolicyRule.school_id == school_id if school_id else UniversityPolicyRule.school_id.is_(None)
    existing = session.scalar(select(UniversityPolicyRule).where(
        same_scope, UniversityPolicyRule.designation == designation, UniversityPolicyRule.criterion_name == criterion))
    if existing is not None:
        raise PolicyError("That criterion is already set for that post and school. Remove it first to change the figure.")
    row = UniversityPolicyRule(school_id=school_id, designation=designation, criterion_name=criterion, criterion_value=stored, created_by=actor[:80])
    session.add(row)
    session.flush()
    return row


def remove_rule(session: Session, policy_id: int) -> None:
    row = session.get(UniversityPolicyRule, policy_id)
    if row is not None:
        session.delete(row)
        session.flush()


def rules_for(session: Session, school_id: str, designation: str) -> list[UniversityPolicyRule]:
    """The criteria in force for a post in a school: the school's own and those set for every school. All have to be met."""
    return list(session.scalars(select(UniversityPolicyRule).where(
        or_(UniversityPolicyRule.school_id == school_id, UniversityPolicyRule.school_id.is_(None)),
        UniversityPolicyRule.designation == designation).order_by(UniversityPolicyRule.policy_id)))


def _check(criterion: str, value: str, facts: Facts, decision: Decision, research: CandidateResearchProfile | None) -> tuple[str, str]:
    """(result, what was compared)."""
    if criterion == "PHD_REQUIRED":
        if facts.has_phd:
            return PASS, "Ph.D. completed"
        if facts.phd_status is None or facts.phd_standing == UNSURE:
            return UNKNOWN, "whether a Ph.D. is held on the date eligibility is counted on is not on the record"
        return FAIL, "no Ph.D. has been awarded"
    needed = float(value)

    def compare(have, what: str) -> tuple[str, str]:
        if have is None:
            return UNKNOWN, f"{what} is not stated on the resume"
        return (PASS if have >= needed else FAIL), f"{have:g} against {needed:g}"

    if criterion == "MIN_MASTERS_MARKS_PCT":
        if facts.masters_marks_pct is None and facts.masters_cgpa is not None:
            return UNKNOWN, "the Master's result is a CGPA; only the awarding university's conversion gives a percentage"
        return compare(facts.masters_marks_pct, "the Master's percentage")
    if criterion == "MIN_PUBLICATIONS":
        return compare(facts.publications_count, "the number of publications")
    if criterion == "MIN_H_INDEX":
        return compare(research.h_index if research else None, "the h-index")
    if criterion == "MIN_CITATIONS":
        return compare(research.total_citations if research else None, "the citation count")
    years: Bounds | None = decision.experience_years
    if years is None:
        return UNKNOWN, "experience was not counted for this assessment"
    return at_least(years, needed), f"{years} years against {needed:g}"


def evaluate(session: Session, application: Application, facts: Facts, decision: Decision) -> list[dict]:
    """How the candidate stands against each university criterion for the post the statutory rules place them at.

    Nothing is returned for a candidate the statutory rules find not eligible,
    or could not place: there is no post to hold the criteria against, and
    they could not help such a candidate in any case.
    """
    post = decision.eligible_designation
    if post is None:
        return []
    research = session.get(CandidateResearchProfile, application.candidate_id)
    results = []
    for rule in rules_for(session, application.school_id, post):
        if rule.criterion_name not in CRITERIA:
            continue
        result, detail = _check(rule.criterion_name, rule.criterion_value, facts, decision, research)
        label, takes_figure = CRITERIA[rule.criterion_name]
        results.append({
            "criterion": rule.criterion_name, "label": label + (f" {float(rule.criterion_value):g}" if takes_figure else ""),
            "result": result, "detail": detail, "post": post,
            "scope": "this school" if rule.school_id else "every school",
        })
    return results


def summary(results: list[dict] | None) -> str:
    """One line for a list: "University criteria: 1 not met, 1 open", or "" when there are none."""
    if not results:
        return ""
    failed = len([r for r in results if r["result"] == FAIL])
    unknown = len([r for r in results if r["result"] == UNKNOWN])
    if not failed and not unknown:
        return "University criteria: all met"
    parts = ([f"{failed} not met"] if failed else []) + ([f"{unknown} open"] if unknown else [])
    return "University criteria: " + ", ".join(parts)
