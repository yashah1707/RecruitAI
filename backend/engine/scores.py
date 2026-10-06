"""Appendix II of the UGC Regulations, 2018: the Research Score and the short-listing score.

Every figure is read from `score_rules` (backend/rules_data.py), by row
code. Nothing is typed in here.

Both scores are worked out as a lower and an upper bound, because a resume
leaves much unsaid (how many authors a paper had, whether a project was
completed) and because the gazette itself is unsettled in places. Where it
is, the two readings are both taken and the bound spans them:

    T2_BASE_VS_IMPACT_FACTOR   impact-factor points replace, or add to, the 8/10 per paper
    T3_PG_VS_SNO3_FOR_MTECH    an M.Tech/M.E. scores under S.No. 2, S.No. 3, or both
    T3_CGPA                    a CGPA cannot be placed in a percentage band

What the Reader does not extract is not scored at all: Table 2 category 3
(pedagogy, MOOCs, e-content), consultancy and policy documents. The score is
therefore "on what the resume claims", and is checked at document
verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.engine.experience import service_years
from backend.engine.facts import Bounds, Facts, Paper
from backend.engine.rules import RuleSet

LAKH = 100_000


@dataclass
class Line:
    label: str
    low: float
    high: float | None


@dataclass
class Score:
    total: Bounds
    lines: list[Line] = field(default_factory=list)
    categories: Bounds | None = None  # Table 2: how many of the six categories carry points
    notes: list[str] = field(default_factory=list)


class _Sum:
    """Adds lower and upper bounds; one unknown upper bound makes the total's unknown."""

    def __init__(self) -> None:
        self.low, self.high = 0.0, 0.0

    def add(self, low: float, high: float | None) -> None:
        self.low += low
        self.high = None if high is None or self.high is None else self.high + high

    @property
    def bounds(self) -> Bounds:
        return Bounds(round(self.low, 2), None if self.high is None else round(self.high, 2))


def _share(paper: Paper, t: dict) -> tuple[float, float]:
    """The author's share of a publication's points (Table 2, notes (a) and (b))."""
    n = paper.author_count
    lead, joint = t["SHARE_FIRST_AUTHOR"].points, t["SHARE_JOINT_AUTHOR"].points
    if n == 1:
        return 1.0, 1.0
    if n == 2:
        return t["SHARE_TWO_AUTHORS"].points, t["SHARE_TWO_AUTHORS"].points
    if n is None:
        return joint, 1.0
    # More than two: the first, principal or corresponding author takes the larger share.
    # A resume shows who is first, not who is corresponding.
    return (lead, lead) if paper.is_first_author else (joint, lead)


def _impact_points(impact_factor: float | None, t: dict) -> float:
    if impact_factor is None:
        return t["IF_NONE"].points
    for row in t.values():
        if row.row_code.startswith("IF_") and row.kind == "BAND":
            if row.band_min <= impact_factor and (row.band_max is None or impact_factor < row.band_max):
                return row.points
    return t["IF_NONE"].points


_GRANTED_RE = re.compile(r"grant", re.IGNORECASE)
_COMPLETED_RE = re.compile(r"complet", re.IGNORECASE)
_ONGOING_RE = re.compile(r"ongoing|on-going|in progress|sanction", re.IGNORECASE)
_AWARDED_RE = re.compile(r"award|complet|guided|produced|supervised", re.IGNORECASE)
_SUBMITTED_RE = re.compile(r"submitted", re.IGNORECASE)
_NOT_YET_RE = re.compile(r"pursu|ongoing|on-going|register|under|in progress|current|guiding", re.IGNORECASE)


def research_score(facts: Facts, rules: RuleSet) -> Score:
    """Table 2, on what the resume claims."""
    t = rules.score_rows.get("TABLE_2", {})
    if not t:
        return Score(Bounds(0, None), notes=["Table 2 is not loaded"])
    cats = [_Sum() for _ in range(7)]  # index 1..6 = the table's six categories

    # 1. Research papers. Which column applies (8 or 10) depends on the faculty, which the opening does not record.
    # The lower bound takes the "replaces" reading, which does not use the base at all.
    base_high = max(t["PAPER_SCI_ENG"].points, t["PAPER_OTHER"].points)
    for p in (p for p in facts.papers if p.cleared_review):
        s_low, s_high = _share(p, t)
        if p.kind == "JOURNAL":
            extra = _impact_points(p.impact_factor, t)
            cats[1].add(extra * s_low, (base_high + extra) * s_high)  # replaces, or adds to, the base
        elif p.kind == "BOOK":
            cats[2].add(t["BOOK_EDITOR_NATIONAL"].points * s_low, t["BOOK_INTL"].points * s_high)
        elif p.kind == "BOOK_CHAPTER":
            cats[2].add(t["BOOK_CHAPTER"].points * s_low, t["BOOK_CHAPTER"].points * s_high)
        elif p.kind == "CONFERENCE":
            # A full paper in proceedings. It may be the same work as a presentation listed
            # elsewhere, which can be claimed only once, so it adds to the upper bound alone.
            cats[6].add(0, t["TALK_INTL_ABROAD"].points)

    for item in facts.items:
        status = item.status or ""
        if item.kind == "GUIDANCE_PHD":
            each_low, each_high = 0.0, t["PHD_AWARDED"].points
            if _SUBMITTED_RE.search(status) and not _AWARDED_RE.search(status):
                each_low = each_high = t["PHD_THESIS_SUBMITTED"].points
            elif _NOT_YET_RE.search(status) and not _AWARDED_RE.search(status):
                each_high = 0.0
            elif _AWARDED_RE.search(status) and not _NOT_YET_RE.search(status):
                each_low = t["PHD_AWARDED"].points
            joint = t["SHARE_JOINT_SUPERVISION"].points  # a co-supervised student earns each supervisor this share
            cats[4].add(each_low * joint * (item.count or 1), None if item.count is None and each_high else each_high * (item.count or 1))
        elif item.kind == "GUIDANCE_PG":
            points = t["MPHIL_PG_DISSERTATION"].points
            cats[4].add(points * (item.count or 1) if _AWARDED_RE.search(status) else 0,
                        None if item.count is None else points * item.count)
        elif item.kind in ("FUNDED_PROJECT", "GRANT"):
            big, small = ("PROJECT_{}_GT10L", "PROJECT_{}_LT10L")
            done, running = _COMPLETED_RE.search(status), _ONGOING_RE.search(status)
            sizes = [big] if (item.amount_inr or 0) > 10 * LAKH else [small] if item.amount_inr is not None else [small, big]
            stages = ["COMPLETED"] if done and not running else ["ONGOING"] if running and not done else ["ONGOING", "COMPLETED"]
            values = [t[size.format(stage)].points for size in sizes for stage in stages]
            cats[4].add(min(values) * t["SHARE_JOINT_PROJECT"].points, max(values))  # a co-investigator gets half
        elif item.kind == "PATENT":
            national, international = t["PATENT_NATIONAL"].points, t["PATENT_INTL"].points
            low, high = {"INTERNATIONAL": (international, international), "NATIONAL": (national, national)}.get(
                item.level, (national, international))
            cats[5].add(low if _GRANTED_RE.search(status) else 0, high)
        elif item.kind == "AWARD":
            low, high = {"INTERNATIONAL": (t["AWARD_INTL"].points,) * 2, "NATIONAL": (t["AWARD_NATIONAL"].points,) * 2,
                         "STATE": (0, 0), "UNIVERSITY": (0, 0)}.get(item.level, (0, t["AWARD_INTL"].points))
            cats[5].add(low, high)
        elif item.kind == "TALK":
            low, high = {
                "INTERNATIONAL": (t["TALK_INTL_INDIA"].points, t["TALK_INTL_ABROAD"].points),  # abroad or not is not read
                "NATIONAL": (t["TALK_NATIONAL"].points,) * 2,
                "STATE": (t["TALK_STATE"].points,) * 2, "UNIVERSITY": (t["TALK_STATE"].points,) * 2,
            }.get(item.level, (t["TALK_STATE"].points, t["TALK_INTL_ABROAD"].points))
            cats[6].add(low, high)

    # Policy documents and talks together may not exceed 30% of the total. Policy documents are
    # not read, so this caps the talks: total = rest + min(talks, rest x cap / (1 - cap)).
    cap = t["CAP_POLICY_AND_TALKS"].points
    rest_low = sum(c.low for c in cats[1:6])
    rest_high = None if any(c.high is None for c in cats[1:6]) else sum(c.high for c in cats[1:6])
    low = rest_low + min(cats[6].low, rest_low * cap / (1 - cap))
    high = None if rest_high is None or cats[6].high is None else rest_high + min(cats[6].high, rest_high * cap / (1 - cap))

    names = ["", "1. Research papers", "2. Books and chapters", "3. Pedagogy, MOOCs, e-content (not read from resumes)",
             "4. Research guidance and projects", "5. Patents and awards", "6. Invited lectures and presentations"]
    lines = [Line(names[i], round(cats[i].low, 2), None if cats[i].high is None else round(cats[i].high, 2)) for i in range(1, 7)]
    # Category 3 is not read, so it may or may not carry points: it widens the upper count only.
    with_points = Bounds(sum(1 for c in cats[1:] if c.low > 0), sum(1 for c in cats[1:] if c.high is None or c.high > 0) + 1)
    return Score(Bounds(round(low, 2), None if high is None else round(high, 2)), lines, with_points,
                 ["Impact-factor points are taken both as replacing and as adding to the base points (open point T2_BASE_VS_IMPACT_FACTOR).",
                  "Category 3, consultancy and policy documents are not read from resumes and are not scored."])


# --- Table 3A ---------------------------------------------------------------

_SNO3_DEGREE_RE = re.compile(r"m\.?\s*phil|ll\.?\s*m|m\.?\s*tech|m\.?\s*arch|m\.?\s*e\b|master of (technology|engineering|architecture|laws|philosophy)|m\.?\s*v\.?\s*sc|m\.?\s*d\b", re.IGNORECASE)
_MPHIL_RE = re.compile(r"m\.?\s*phil|master of philosophy", re.IGNORECASE)


def _band(t: dict, prefix: str, pct: float | None, category: str | None = None) -> tuple[float, float]:
    """Points for a percentage in the rows whose code starts with `prefix`; unknown marks span every band."""
    rows = [r for r in t.values() if r.row_code.startswith(prefix) and r.kind == "BAND"]
    open_rows = [r for r in rows if not r.applies_to_categories or (category in (r.applies_to_categories or []))]
    if pct is None:
        return 0.0, max((r.points for r in rows), default=0.0)
    hit = [r.points for r in open_rows if r.band_min <= pct and (r.band_max is None or pct < r.band_max)]
    return (max(hit), max(hit)) if hit else (0.0, 0.0)


def shortlist_score(facts: Facts, rules: RuleSet, table: str = "TABLE_3A") -> Score:
    """Table 3A: the score for short-listing Assistant Professor candidates for interview at a university.

    An aid for calling candidates to interview, never a ranking that selects
    (cl. 4.1 Note): selection rests on the interview alone.
    """
    t = rules.score_rows.get(table, {})
    if not t:
        return Score(Bounds(0, None), notes=[f"{table} is not loaded"])
    lines: list[Line] = []

    def best(level: str, prefix: str) -> tuple[float, float]:
        degrees = [d for d in facts.degrees if d.level == level]
        scored = [_band(t, prefix, d.marks_pct, facts.category) for d in degrees] or [_band(t, prefix, None)]
        return max(s[0] for s in scored), max(s[1] for s in scored)

    grad = best("UG", "GRAD_")
    lines.append(Line("1. Graduation", *grad))

    # A Master's that is also on the S.No. 3 list (M.Tech, M.E., LL.M. ...) may score under
    # S.No. 2, under S.No. 3, or both: the 3rd Amendment does not say. Both ends are taken.
    masters = [d for d in facts.degrees if d.level == "PG"]
    for d in masters:
        if d.marks_pct is None and d.cgpa is None:
            d.marks_pct = facts.masters_marks_pct
    pg_low = pg_high = sno3_low = sno3_high = 0.0
    for d in masters:
        as_pg, as_sno3 = _band(t, "PG_", d.marks_pct, facts.category), _band(t, "MPHIL_", d.marks_pct)
        if _MPHIL_RE.search(d.name or ""):
            sno3_low, sno3_high = max(sno3_low, as_sno3[0]), max(sno3_high, as_sno3[1])
        elif _SNO3_DEGREE_RE.search(d.name or ""):
            pg_low, pg_high = max(pg_low, min(as_pg[0], as_sno3[0])), max(pg_high, as_pg[1])
            sno3_high = max(sno3_high, as_sno3[1])
        else:
            pg_low, pg_high = max(pg_low, as_pg[0]), max(pg_high, as_pg[1])
    if not masters:
        pg_low, pg_high = _band(t, "PG_", facts.masters_marks_pct, facts.category)
    lines.append(Line("2. Post-Graduation", pg_low, pg_high))

    phd = t["PHD"].points if facts.has_phd else 0.0
    cap = t["CAP_MPHIL_PHD"].max_points
    lines.append(Line("3-4. M.Phil. and similar, and Ph.D.", min(sno3_low + phd, cap), min(sno3_high + phd, cap)))

    net = {"NET": (t["NET"].points, t["NET_JRF"].points)}.get(facts.net_set_status, (0.0, 0.0))  # JRF is not read
    if facts.net_set_status in ("SET", "SLET"):
        # Note D: a SET/SLET counts only for appointment in its own State.
        own = facts.set_state == facts.institution_state
        net = (t["SLET_SET"].points if own else 0.0, t["SLET_SET"].points if own or facts.set_state is None else 0.0)
    lines.append(Line("5. NET / SET", min(net[0], t["CAP_JRF_NET_SET"].max_points), min(net[1], t["CAP_JRF_NET_SET"].max_points)))

    pubs = t["PUBLICATIONS"]
    counted = facts.publications_count
    lines.append(Line("6. Research publications", *((0.0, pubs.max_points) if counted is None else
                                                   (min(counted * pubs.points, pubs.max_points),) * 2)))

    teach = t["TEACHING"]
    # Teaching certainly counts. A research post counts only if it was post-doctoral, which the
    # record does not say, so research posts widen the upper bound alone.
    surely = facts.experience_years or service_years(facts, ("TEACHING",))
    perhaps = facts.experience_years or service_years(facts, ("TEACHING", "RESEARCH"))
    lines.append(Line("7. Teaching / post-doctoral experience", min(surely.low * teach.points, teach.max_points),
                      teach.max_points if perhaps.high is None else min(perhaps.high * teach.points, teach.max_points)))

    a_low = a_high = 0.0
    for item in (i for i in facts.items if i.kind == "AWARD"):
        low, high = {"INTERNATIONAL": (t["AWARD_INTL_NATIONAL"].points,) * 2, "NATIONAL": (t["AWARD_INTL_NATIONAL"].points,) * 2,
                     "STATE": (t["AWARD_STATE"].points,) * 2, "UNIVERSITY": (0.0, 0.0)}.get(
                         item.level, (0.0, t["AWARD_INTL_NATIONAL"].points))
        a_low, a_high = max(a_low, low), max(a_high, high)
    lines.append(Line("8. Awards", min(a_low, t["CAP_AWARDS"].max_points), min(a_high, t["CAP_AWARDS"].max_points)))

    total = Bounds(round(sum(l.low for l in lines), 2), round(sum(l.high for l in lines), 2))
    for l in lines:
        l.low, l.high = round(l.low, 2), round(l.high, 2)
    return Score(total, lines, notes=[
        "A short-listing aid only: selection is on the interview (cl. 4.1 Note).",
        "Marks given as a CGPA cannot be placed in a percentage band, so that row spans every band (open point T3_CGPA).",
        "An M.Tech/M.E./LL.M. is scored both under S.No. 2 and under S.No. 3 (open point T3_PG_VS_SNO3_FOR_MTECH).",
    ])
