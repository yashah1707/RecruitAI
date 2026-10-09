"""Stages 3 and 4 as a workflow step: assess one EXTRACTED application and store the result.

The working is done by `backend.engine`. This module reads the facts and the
rules from the database, writes `evaluation_results`, and moves the
application to the state the decision names. No model is called.

    EXTRACTED -> ASSESSED -> SHORTLISTED       meets the rank applied for
                          -> RE_CATEGORISED    meets a lower rank
                          -> NOT_ELIGIBLE      meets no rank
    EXTRACTED -> MANUAL_REVIEW                 another regulator's post, or something
                                               only a person can settle

Every outcome, a clean pass included, still goes to HR for approval (Gate 2).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import states
from backend.engine.decision import MANUAL_REVIEW, Decision, decide
from backend.engine.facts import build_facts
from backend.engine.rules import load_rules
from backend.models import Application, EvaluationResult

logger = logging.getLogger("recruitai.assessor_service")

ACTOR = "agent:assessor"


def _details(d: Decision) -> dict:
    def bounds(b):
        return None if b is None else {"low": b.low, "high": b.high}

    return {
        "as_of": d.as_of.isoformat() if d.as_of else None,
        "as_of_basis": d.as_of_basis,
        "rule_version": d.rule_version,
        "open_points": d.open_points,
        "notes": d.notes,
        "experience_years": bounds(d.experience_years),
        "experience_kinds": d.experience_kinds,
        "research_score": bounds(d.research_score),
        "shortlist_score": d.shortlist_score,
        "ranks": [{"designation": r.designation, "result": r.result, "rule_version": r.rule_version,
                   "checks": [asdict(c) for c in r.checks]} for r in d.ranks],
    }


def assess_application(session: Session, application: Application, as_of: date | None = None) -> Decision:
    """Assess one application that is EXTRACTED; returns the decision it stored."""
    if application.status != states.EXTRACTED:
        raise states.IllegalTransition(f"only an EXTRACTED application can be assessed; this one is {application.status}")
    facts = build_facts(session, application, as_of)
    decision = decide(facts, load_rules(session, facts.discipline_group))

    failing = decision.failing
    exact = lambda b: None if b is None else b.exact  # noqa: E731
    session.add(EvaluationResult(
        application_id=application.application_id,
        outcome=decision.outcome,
        eligible_designation=decision.eligible_designation,
        was_recategorised=decision.outcome == "RE_CATEGORISED",
        failing_clause=(f"{failing.clause}: {failing.detail}"[:300] if failing else None),
        failing_clause_page=(failing.page[:40] if failing else None),
        rule_version_id=decision.rule_version_id,
        # A single figure only when it is known exactly; the bounds are in `details`.
        research_score=exact(decision.research_score),
        adjusted_experience_years=exact(decision.experience_years),
        details=_details(decision),
    ))

    # The audit trail names the clause, never the candidate's figures.
    cited = f"{failing.clause}, {failing.page}" if failing else None
    if decision.outcome == MANUAL_REVIEW:
        states.transition(session, application, states.MANUAL_REVIEW, ACTOR,
                          note=(cited or f"{len(decision.open_points)} point(s) for a person to settle")[:500])
    else:
        states.transition(session, application, states.ASSESSED, ACTOR, note=f"rules: {decision.rule_version}")
        states.transition(session, application, decision.outcome, ACTOR, note=cited[:500] if cited else None)
    session.flush()
    logger.info("application_assessed id=%s outcome=%s", application.application_id, decision.outcome)
    return decision


def assess_opening(session: Session, opening_id: int, as_of: date | None = None) -> dict[str, int]:
    """Assess every EXTRACTED application of one opening; returns how many ended in each outcome."""
    counts: dict[str, int] = {}
    waiting = session.scalars(
        select(Application)
        .where(Application.opening_id == opening_id, Application.status == states.EXTRACTED)
        .order_by(Application.application_id)
        .with_for_update()
    ).all()
    for application in waiting:
        outcome = assess_application(session, application, as_of).outcome
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def latest_evaluation(session: Session, application_id: int) -> EvaluationResult | None:
    return session.scalars(
        select(EvaluationResult).where(EvaluationResult.application_id == application_id)
        .order_by(EvaluationResult.evaluation_id.desc()).limit(1)
    ).first()
