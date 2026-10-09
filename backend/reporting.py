"""Stage 5 (Reporting): each outcome in plain words, and the digest for one opening.

Everything here is written by code from the engine's stored working and HR's
recorded decision. No model is called, so an explanation can never say
something the engine did not find. (A language model may reword a candidate
email afterwards, at a person's request; see backend/emails.py.)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import gate2, states
from backend.assessor_service import latest_evaluation
from backend.models import Application, CandidatePersonalDetails, EmailDraft, EvaluationResult, HrDecision, JobOpening

RANKS = {"ASSISTANT_PROFESSOR": "Assistant Professor", "ASSOCIATE_PROFESSOR": "Associate Professor",
         "PROFESSOR": "Professor", "SENIOR_PROFESSOR": "Senior Professor"}


def regulation_name(rule_version: str | None) -> str:
    """The instrument a rule version belongs to, as a person would name it."""
    if (rule_version or "").startswith("AICTE"):
        return "the AICTE (Degree) Regulation, 2019"
    if (rule_version or "").startswith("UGC"):
        return "the UGC Regulations, 2018, as amended"
    return "the regulations"


def _not_met(rank: dict) -> list[str]:
    """Each requirement of one rank that was not met: what, the figures, the clause and the page."""
    return [f"{c['label']}: {c['detail']} ({c['clause']}, {c['page']})" for c in rank.get("checks", []) if c["result"] == "FAIL"]


def explain(application: Application, evaluation: EvaluationResult | None) -> str:
    """The engine's finding for one application, in plain sentences with its citations."""
    if evaluation is None:
        return "Not yet assessed."
    details = evaluation.details or {}
    ranks = details.get("ranks") or []
    applied = RANKS.get(application.applied_designation, application.applied_designation)
    under = regulation_name(details.get("rule_version"))
    if evaluation.outcome == "MANUAL_REVIEW":
        points = details.get("open_points") or ["a point the record does not settle"]
        return "The rules could not settle this application. To be settled by a person: " + "; ".join(points) + "."
    if evaluation.outcome == "SHORTLISTED":
        checked = ", ".join(c["label"] for c in ranks[0]["checks"]) if ranks else ""
        return f"Meets every minimum requirement for {applied} under {under}" + (f" ({checked})." if checked else ".")
    reasons = "; ".join(_not_met(ranks[0])) if ranks else (evaluation.failing_clause or "")
    if evaluation.outcome == "RE_CATEGORISED":
        lower = RANKS.get(evaluation.eligible_designation, evaluation.eligible_designation)
        return (f"Does not meet the minimum requirements for {applied} under {under}. Not met: {reasons}. "
                f"Meets every minimum requirement for {lower}.")
    return (f"Does not meet the minimum requirements for {applied} under {under}, or for any lower post. "
            f"Not met for {applied}: {reasons}.")


def decision_text(decision: HrDecision | None) -> str:
    """HR's recorded decision in a sentence."""
    if decision is None:
        return "No HR decision yet."
    how = {"APPROVED": "Approved as assessed", "OVERRIDDEN": "Overridden by HR", "DECIDED": "Decided by HR"}[decision.action]
    if decision.final_outcome == "SHORTLISTED":
        what = f"short-listed for interview as {RANKS.get(decision.final_designation, 'the post found')}"
    else:
        what = "does not meet the minimum qualifications"
    return f"{how}: {what}." + (f" Justification: {decision.justification}" if decision.justification else "")


# The digest's sections, in the order HR acts on them. (title, what it means, states)
_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("decided", "Decided by HR", (states.HR_APPROVED, states.CONTACTED, states.INTERVIEW_SCHEDULED)),
    ("to_decide", "Assessed, waiting for an HR decision", (states.SHORTLISTED, states.RE_CATEGORISED, states.NOT_ELIGIBLE)),
    ("for_a_person", "The rules could not settle these; a person decides", (states.MANUAL_REVIEW,)),
    ("to_assess", "Read, waiting to be assessed", (states.EXTRACTED,)),
    ("to_check", "Fields waiting for a person to check", (states.PENDING_REVIEW,)),
    ("not_read", "Not read yet, or could not be read", (states.RECEIVED, states.PARSING, states.FAILED, states.NEEDS_JOB_MATCH)),
    ("withdrawn", "Withdrawn", (states.WITHDRAWN,)),
)


def digest(session: Session, opening: JobOpening) -> dict:
    """Everything about one opening on one page: where each application stands, and why."""
    sections = {key: {"title": title, "rows": []} for key, title, _ in _SECTIONS}
    where = {state: key for key, _, group in _SECTIONS for state in group}
    totals = {"applications": 0, "shortlisted": 0, "not_eligible": 0, "emails_drafted": 0, "emails_approved": 0, "emails_sent": 0}
    applications = session.scalars(
        select(Application).where(Application.opening_id == opening.opening_id).order_by(Application.application_id)
    ).all()
    for a in applications:
        totals["applications"] += 1
        personal = session.get(CandidatePersonalDetails, a.candidate_id)
        evaluation = latest_evaluation(session, a.application_id)
        current = a.status in gate2.AWAITING_HR or a.status in (states.HR_APPROVED, states.CONTACTED, states.INTERVIEW_SCHEDULED)
        decided = gate2.final_decision(session, a)
        email = session.scalars(
            select(EmailDraft).where(EmailDraft.application_id == a.application_id, EmailDraft.status != "DISCARDED",
                                     EmailDraft.kind == "DECISION")
            .order_by(EmailDraft.draft_id.desc()).limit(1)
        ).first()
        if decided is not None:
            totals["shortlisted" if decided.final_outcome == "SHORTLISTED" else "not_eligible"] += 1
        if email is not None:
            totals[{"DRAFT": "emails_drafted", "APPROVED": "emails_approved", "SENT": "emails_sent"}[email.status]] += 1
        sections[where.get(a.status, "not_read")]["rows"].append({
            "a": a, "name": a.applicant_name or (personal.full_name if personal else None),
            "finding": explain(a, evaluation) if current else None,
            "decision": decision_text(decided) if decided else None,
            "email": email.status if email else None,
        })
    return {"sections": [sections[key] for key, _, _ in _SECTIONS if sections[key]["rows"]], "totals": totals}


# --- the university dashboard (Section 17.8) ------------------------------------

_STAGES = (
    ("to_read", (states.RECEIVED, states.PARSING, states.NEEDS_JOB_MATCH)), ("to_check", (states.PENDING_REVIEW,)),
    ("to_assess", (states.EXTRACTED,)), ("to_decide", tuple(gate2.AWAITING_HR)), ("unreadable", (states.FAILED,)),
    ("withdrawn", (states.WITHDRAWN,)),
)
_STAGE_OF = {state: key for key, group in _STAGES for state in group}


def _blank() -> dict[str, int]:
    return {"applications": 0, "shortlisted": 0, "not_eligible": 0, **{key: 0 for key, _ in _STAGES}}


def dashboard(session: Session) -> dict:
    """Counts across the university: by school, by opening (with its advertisement), and by the channel applications came through.

    Counts only. No candidate is named and nothing is ranked.
    """
    by_school: dict[str, dict] = {}
    by_opening: dict[int, dict] = {}
    by_channel: dict[str, int] = {}
    openings = session.scalars(select(JobOpening).order_by(JobOpening.opening_id.desc())).all()
    for o in openings:
        school = by_school.setdefault(o.school_id, {"school": o.school, "openings": 0, **_blank()})
        school["openings"] += 1
        by_opening[o.opening_id] = {"o": o, **_blank()}
    for a in session.scalars(select(Application).order_by(Application.application_id)):
        buckets = [by_school.setdefault(a.school_id, {"school": a.school, "openings": 0, **_blank()})]
        if a.opening_id in by_opening:
            buckets.append(by_opening[a.opening_id])
        decided = gate2.final_decision(session, a)
        for bucket in buckets:
            bucket["applications"] += 1
            if decided is not None:
                bucket["shortlisted" if decided.final_outcome == "SHORTLISTED" else "not_eligible"] += 1
            elif a.status in _STAGE_OF:
                bucket[_STAGE_OF[a.status]] += 1
        by_channel[a.resume_source] = by_channel.get(a.resume_source, 0) + 1
    totals = _blank()
    for row in by_school.values():
        for key in totals:
            totals[key] += row[key]
    return {"schools": sorted(by_school.values(), key=lambda row: row["school"].name), "openings": list(by_opening.values()),
            "channels": by_channel, "totals": totals}
