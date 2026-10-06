"""The rule set that applies to a post, read from the database.

Which regulation a post falls under is HR's choice on the opening
(`discipline_group`), never something read off a resume. "GENERAL" is UGC
cl. 4.1. An AICTE group takes its Assistant Professor rule from AICTE cl. 5.1
for that discipline, and its Associate Professor and Professor rules from
cl. 5.2, which is one rule for every technical discipline ("TECHNICAL").
AICTE cl. 5.1(j) hands science and humanities faculty back to the UGC rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import RelaxationRule, RubricRule, RuleVersion, ScoreRule

# Disciplines the UGC Regulations, 2018 treat in their own clauses, not in cl. 4.1.
# Their thresholds are not loaded, so the engine applies none and says which clause governs.
# (clause, gazette page, what it covers), read from the gazette's own headings.
UGC_OTHER_SECTIONS: dict[str, tuple[str, str, str]] = {
    "UGC_4_2_PERFORMING_VISUAL_ARTS": ("cl. 4.2", "p. 61", "Music, performing arts, visual arts and other traditional Indian art forms"),
    "UGC_4_3_DRAMA": ("cl. 4.3", "p. 63", "Drama"),
    "UGC_4_4_YOGA": ("cl. 4.4", "p. 65", "Yoga"),
    "UGC_4_5_OCCUPATIONAL_THERAPY": ("cl. 4.5", "p. 66", "Occupational therapy"),
    "UGC_4_6_PHYSIOTHERAPY": ("cl. 4.6", "p. 66", "Physiotherapy"),
}
# An application with no opening has had no rule set chosen for it.
NO_OPENING = "NO_OPENING"

RANKS: tuple[str, ...] = ("SENIOR_PROFESSOR", "PROFESSOR", "ASSOCIATE_PROFESSOR", "ASSISTANT_PROFESSOR")


@dataclass
class Rule:
    """One row of `rubric_rules`, with the instrument it comes from."""

    designation: str
    discipline_group: str
    version_code: str
    rule_version_id: int | None
    authority_clause: str
    authority_page: str
    min_years: float | None = None
    min_publications: int | None = None
    requires_phd: bool = False
    min_marks_pct: float | None = None
    research_score_threshold: float | None = None
    net_set_required: bool = False
    min_doctoral_guided: int | None = None
    criteria: dict | None = None
    notes: str | None = None

    @property
    def is_aicte(self) -> bool:
        return self.version_code.startswith("AICTE")


@dataclass
class Relaxation:
    code: str
    relaxation_pct: float
    applies_to_categories: list[str] | None
    condition: dict | None
    authority_clause: str
    authority_page: str


@dataclass
class RuleSet:
    discipline_group: str
    by_designation: dict[str, Rule] = field(default_factory=dict)
    relaxations: list[Relaxation] = field(default_factory=list)
    # {table_code: {row_code: row}} for Appendix II.
    score_rows: dict[str, dict[str, ScoreRule]] = field(default_factory=dict)


def _rule(row: RubricRule, version: RuleVersion) -> Rule:
    return Rule(
        designation=row.designation, discipline_group=row.discipline_group, version_code=version.code,
        rule_version_id=version.rule_version_id, authority_clause=row.authority_clause, authority_page=row.authority_page,
        min_years=row.min_years, min_publications=row.min_publications, requires_phd=row.requires_phd,
        min_marks_pct=row.min_marks_pct, research_score_threshold=row.research_score_threshold,
        net_set_required=row.net_set_required, min_doctoral_guided=row.min_doctoral_guided,
        criteria=row.criteria, notes=row.notes,
    )


def load_rules(session: Session, discipline_group: str) -> RuleSet:
    """Every rule that can apply to a post in `discipline_group`. A rank with no rule is simply absent."""
    versions = {v.rule_version_id: v for v in session.scalars(select(RuleVersion))}
    rows: dict[tuple[str, str], RubricRule] = {
        (r.discipline_group, r.designation): r for r in session.scalars(select(RubricRule))
    }

    def find(group: str, designation: str) -> Rule | None:
        row = rows.get((group, designation))
        return _rule(row, versions[row.rule_version_id]) if row is not None else None

    rules = RuleSet(discipline_group=discipline_group)
    if discipline_group in UGC_OTHER_SECTIONS or discipline_group == NO_OPENING:
        return rules  # no rank has a rule; the decision says why
    own_ap = find(discipline_group, "ASSISTANT_PROFESSOR")
    defers_to_ugc = bool(own_ap and (own_ap.criteria or {}).get("defer_to"))
    for designation in RANKS:
        if discipline_group == "GENERAL" or defers_to_ugc:
            rule = find("GENERAL", designation)
        elif designation == "ASSISTANT_PROFESSOR":
            rule = own_ap
        else:
            rule = find("TECHNICAL", designation)
        if rule is not None:
            rules.by_designation[designation] = rule

    for r in session.scalars(select(RelaxationRule).order_by(RelaxationRule.relaxation_rule_id)):
        rules.relaxations.append(Relaxation(
            code=r.code, relaxation_pct=r.relaxation_pct, applies_to_categories=r.applies_to_categories,
            condition=r.condition, authority_clause=r.authority_clause, authority_page=r.authority_page,
        ))
    for r in session.scalars(select(ScoreRule).order_by(ScoreRule.sort_order)):
        rules.score_rows.setdefault(r.table_code, {})[r.row_code] = r
    return rules
