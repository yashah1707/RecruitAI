"""The checks for one rank, and the walk down the ranks (design document, Section 9.4).

Each requirement of a rule becomes one `Check` with one of three results:

    PASS     met on what is known
    FAIL     not met on what is known, whichever way the unknowns fall
    UNKNOWN  it depends on something not known; `detail` names it

A rank is met when every check passes, not met when any check fails, and
otherwise undecided. An undecided rank is never rounded either way: the
application goes to a person (MANUAL_REVIEW) with the open points listed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from backend.engine import scores
from backend.engine.experience import adjusted_years, service_years
from backend.engine.facts import AFTER, UNSURE, Bounds, Degree, Facts, Period
from backend.engine.rules import NO_OPENING, RANKS, UGC_OTHER_SECTIONS, Relaxation, Rule, RuleSet

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

SHORTLISTED, RE_CATEGORISED, NOT_ELIGIBLE, MANUAL_REVIEW = "SHORTLISTED", "RE_CATEGORISED", "NOT_ELIGIBLE", "MANUAL_REVIEW"

RANK_NAMES = {
    "ASSISTANT_PROFESSOR": "Assistant Professor", "ASSOCIATE_PROFESSOR": "Associate Professor",
    "PROFESSOR": "Professor", "SENIOR_PROFESSOR": "Senior Professor",
}

# cl. 7.3 of the AICTE (Degree) Regulation, 2019 is loaded as score rows; these
# two are read from there (backend/rules_data.py, AICTE_GRADE_RULES).
_AICTE_GRADES = "AICTE_7_3"


@dataclass
class Check:
    key: str
    label: str
    result: str
    detail: str
    clause: str
    page: str


@dataclass
class RankResult:
    designation: str
    result: str
    checks: list[Check] = field(default_factory=list)
    rule_version: str | None = None


@dataclass
class Decision:
    outcome: str
    applied_designation: str
    eligible_designation: str | None = None
    failing: Check | None = None  # why the applied rank was not met
    open_points: list[str] = field(default_factory=list)  # what a person has to settle
    ranks: list[RankResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rule_version_id: int | None = None
    rule_version: str | None = None
    experience_years: Bounds | None = None
    experience_kinds: list[str] = field(default_factory=list)  # which posts that figure counts
    research_score: Bounds | None = None
    shortlist_score: dict | None = None
    as_of: date | None = None
    as_of_basis: str | None = None


def at_least(value: Bounds, needed: float) -> str:
    if value.low >= needed:
        return PASS
    return FAIL if value.high is not None and value.high < needed else UNKNOWN


def _combine(results: list[str]) -> str:
    if FAIL in results:
        return FAIL
    return UNKNOWN if UNKNOWN in results else PASS


# --- individual requirements -------------------------------------------------


def _marks_check(facts: Facts, rule: Rule, relaxations: list[Relaxation]) -> Check:
    """Master's marks against the rule's floor, with the cl. 3.4 and cl. 3.5 relaxations."""
    floor = rule.min_marks_pct
    applies: list[Relaxation] = []  # relaxations that certainly apply
    might: list[str] = []  # ones that would apply if something unknown turned out so
    for r in relaxations:
        if r.applies_to_categories is not None:
            disabled_counts = "PwD" in r.applies_to_categories
            if facts.category in r.applies_to_categories or (disabled_counts and facts.differently_abled is True):
                applies.append(r)
            elif facts.category is None or (disabled_counts and facts.differently_abled is None):
                might.append(f"the category or disability status is not known ({r.authority_clause})")
        elif r.condition and r.condition.get("requires_phd"):
            cutoff = date.fromisoformat(r.condition["masters_awarded_before"])
            awarded = facts.masters_awarded
            if facts.has_phd and awarded is not None and awarded.latest < cutoff:
                applies.append(r)
            elif facts.has_phd and (awarded is None or awarded.earliest < cutoff):
                might.append(f"the Master's award date is not known to the day ({r.authority_clause})")
            elif facts.phd_standing == UNSURE and (awarded is None or awarded.earliest < cutoff):
                might.append(f"whether the Ph.D. was awarded by {facts.as_of_text} is not known ({r.authority_clause})")
    # The relaxations are alternatives, each lowering the same floor; they do not add up.
    relaxed = floor - max((r.relaxation_pct for r in applies), default=0.0)
    lowest = floor - max((r.relaxation_pct for r in relaxations), default=0.0) if might else relaxed
    cited = "; ".join([rule.authority_clause] + [r.authority_clause for r in applies])
    page = "; ".join([rule.authority_page] + [r.authority_page for r in applies])

    marks = facts.masters_marks_pct
    if marks is None:
        why = ("the Master's result is a CGPA, and only the awarding university's own conversion can place it "
               f"against {floor:g}% (cl. 3.6)") if facts.masters_cgpa is not None else "the Master's marks are not stated"
        return Check("marks", "Master's marks", UNKNOWN, why, cited, page)
    if marks >= relaxed:
        note = f" after the {applies[0].relaxation_pct:g}% relaxation ({applies[0].authority_clause})" if relaxed < floor and marks < floor else ""
        return Check("marks", "Master's marks", PASS, f"{marks:g}% against {relaxed:g}%{note}", cited, page)
    if marks >= lowest:
        return Check("marks", "Master's marks", UNKNOWN,
                     f"{marks:g}% is below {relaxed:g}% but would meet {lowest:g}% if a relaxation applies: " + "; ".join(might),
                     cited, page)
    return Check("marks", "Master's marks", FAIL, f"{marks:g}% against {relaxed:g}%", cited, page)


def _net_set_check(facts: Facts, rule: Rule) -> Check:
    """NET/SET/SLET for Assistant Professor, and the Ph.D. exemption (design document, Section 6.2)."""
    def check(result: str, detail: str) -> Check:
        return Check("net_set", "NET / SET / SLET", result, detail, rule.authority_clause, rule.authority_page)

    status = facts.net_set_status
    if status == "NET":
        return check(PASS, "NET, valid in every State")
    wrong_state = ""
    if status in ("SET", "SLET"):
        if facts.set_state == facts.institution_state:
            return check(PASS, f"{status} of {facts.set_state}, the institution's own State")
        if facts.set_state is None:
            return check(UNKNOWN, f"a {status} is held but its State is not known; it is valid only in its own State")
        wrong_state = f"the {status} is from {facts.set_state} and is valid only there; "
    if facts.has_phd:
        if facts.phd_regulation in ("2009", "2016"):
            return check(PASS, f"{wrong_state}exempt: Ph.D. awarded under the {facts.phd_regulation} Ph.D. Regulations")
        return check(UNKNOWN, f"{wrong_state}a Ph.D. is held, but whether it is one that exempts from NET/SET "
                              "(2009 or 2016 Regulations, the five certified conditions, or a foreign top-500 university) "
                              "has to be read from the certificate")
    if facts.phd_standing == UNSURE:
        return check(UNKNOWN, f"{wrong_state}a Ph.D. could exempt, but whether it was awarded on or before {facts.as_of_text} "
                              "is not on the record")
    if status is None:
        return check(UNKNOWN, "whether NET/SET/SLET is held is not on the record")
    return check(FAIL, f"{wrong_state}no NET, no SET or SLET of {facts.institution_state}, and no exempting Ph.D.")


def _phd_check(facts: Facts, rule: Rule) -> Check:
    if facts.has_phd:
        return Check("phd", "Ph.D.", PASS, "Ph.D. completed", rule.authority_clause, rule.authority_page)
    if facts.phd_standing == AFTER:
        return Check("phd", "Ph.D.", FAIL, f"a Ph.D. is required: it was awarded after {facts.as_of_text}, the date eligibility is counted on",
                     rule.authority_clause, rule.authority_page)
    if facts.phd_standing == UNSURE:
        return Check("phd", "Ph.D.", UNKNOWN, f"the Ph.D. is dated only to a period that includes {facts.as_of_text}, the date eligibility "
                     "is counted on; whether it was awarded on or before that date is not on the record",
                     rule.authority_clause, rule.authority_page)
    if facts.phd_status is None:
        return Check("phd", "Ph.D.", UNKNOWN, "the Ph.D. status is not on the record", rule.authority_clause, rule.authority_page)
    stage = {"NOT_APPLICABLE": "no doctoral study", "REGISTERED": "registered, not awarded",
             "PURSUING": "being pursued, not awarded", "THESIS_SUBMITTED": "thesis submitted, not awarded"}[facts.phd_status]
    return Check("phd", "Ph.D.", FAIL, f"a Ph.D. is required: {stage}", rule.authority_clause, rule.authority_page)


_SENIOR_POST_RE = re.compile(r"(?<!assistant )(?<!asst\. )(?<!asst )professor", re.IGNORECASE)
_PROFESSOR_ONLY_RE = re.compile(r"(?<!assistant )(?<!associate )(?<!asst\. )(?<!assoc\. )(?<!asst )(?<!assoc )professor", re.IGNORECASE)


def _is_associate_or_above(post) -> bool:
    return bool(_SENIOR_POST_RE.search(post.designation or ""))


def _is_professor(post) -> bool:
    return bool(_PROFESSOR_ONLY_RE.search(post.designation or ""))


def _years_check(key: str, label: str, value: Bounds, needed: float, how: str, rule: Rule, clause: str | None = None,
                 page: str | None = None) -> Check:
    result = at_least(value, needed)
    detail = f"{value} years against {needed:g}" + (f" ({how})" if how else "")
    return Check(key, label, result, detail, clause or rule.authority_clause, page or rule.authority_page)


def _publications_check(facts: Facts, rule: Rule) -> Check:
    n = facts.publications_count
    label = "Publications"
    if n is None:
        return Check("publications", label, UNKNOWN, "the number of publications is not on the record",
                     rule.authority_clause, rule.authority_page)
    result = PASS if n >= rule.min_publications else FAIL
    return Check("publications", label, result,
                 f"{n} stated on the resume against {rule.min_publications}; whether each is in a listed journal is for the document check",
                 rule.authority_clause, rule.authority_page)


_AWARDED_RE = re.compile(r"award|complet|guided|produced|supervised", re.IGNORECASE)
_NOT_YET_RE = re.compile(r"pursu|ongoing|on-going|register|submitted|under|in progress|current|guiding", re.IGNORECASE)


def doctoral_guided(facts: Facts) -> Bounds:
    """How many Ph.D. candidates the record shows as guided to the award of the degree."""
    low, high = 0, 0
    for item in facts.items:
        if item.kind != "GUIDANCE_PHD":
            continue
        text = item.status or ""
        if _NOT_YET_RE.search(text) and not _AWARDED_RE.search(text):
            continue
        sure = bool(_AWARDED_RE.search(text)) and not _NOT_YET_RE.search(text)
        if item.count is None:  # "guided Ph.D. students", with no number
            low, high = low + (1 if sure else 0), None
        else:
            low += item.count if sure else 0
            high = None if high is None else high + item.count
    return Bounds(low, high)


def _guided_check(facts: Facts, rule: Rule, needed: int) -> Check:
    value = doctoral_guided(facts)
    return Check("doctoral_guided", "Doctoral candidates guided", at_least(value, needed),
                 f"{value} shown as guided to the award against {needed}", rule.authority_clause, rule.authority_page)


# --- AICTE: first class, and the discipline routes ---------------------------


def _first_class(degree: Degree, rules: RuleSet) -> tuple[str, str]:
    """Whether one degree is First Class under AICTE cl. 7.3, and on what basis."""
    grades = rules.score_rows.get(_AICTE_GRADES, {})
    floor_pct = grades["FIRST_CLASS_60"].points if "FIRST_CLASS_60" in grades else None
    floor_cgpa = next((g.band_min for g in grades.values() if g.kind == "CONVERSION" and g.points == floor_pct), None)
    name = degree.name or degree.level
    division = (degree.division or "").lower()
    if "first" in division or "distinction" in division:
        return PASS, f"{name}: {degree.division}"
    if re.search(r"second|third|pass class", division):
        return FAIL, f"{name}: {degree.division}"
    if degree.marks_pct is not None and floor_pct is not None:
        return (PASS if degree.marks_pct >= floor_pct else FAIL), f"{name}: {degree.marks_pct:g}% against {floor_pct:g}%"
    if degree.cgpa is not None and floor_cgpa is not None:
        if degree.cgpa <= 4.0:
            return UNKNOWN, f"{name}: a grade point of {degree.cgpa:g} is not on a ten-point scale"
        return ((PASS if degree.cgpa >= floor_cgpa else FAIL),
                f"{name}: CGPA {degree.cgpa:g} against {floor_cgpa:g}, which the table equates to {floor_pct:g}%")
    return UNKNOWN, f"{name}: neither a class nor marks are stated"


def _degrees_of(facts: Facts, level: str) -> list[Degree]:
    found = [d for d in facts.degrees if d.level == level]
    if level == "PG":
        # The Master's marks read as a scored field stand in when the degree row has none.
        for d in found:
            if d.marks_pct is None and d.cgpa is None and d.division is None:
                d.marks_pct, d.cgpa = facts.masters_marks_pct, facts.masters_cgpa
    return found


_FIRST_CLASS_LEVELS = {
    "ANY_ONE_DEGREE": (("UG", "PG"), "any"), "BACHELORS_OR_MASTERS": (("UG", "PG"), "any"),
    "BOTH_DEGREES": (("UG", "PG"), "all"), "MASTERS": (("PG",), "any"), "MCA": (("PG",), "any"),
    "BACHELORS": (("UG",), "any"),
}


def _first_class_check(facts: Facts, mode: str, rule: Rule, rules: RuleSet) -> Check:
    levels, how = _FIRST_CLASS_LEVELS[mode]
    results, texts = [], []
    for level in levels:
        degrees = _degrees_of(facts, level)
        if not degrees:
            results.append(UNKNOWN)
            texts.append(f"no {'Bachelor' if level == 'UG' else 'Master'}'s degree is on the record")
            continue
        each = [_first_class(d, rules) for d in degrees]
        best = PASS if any(r == PASS for r, _ in each) else (UNKNOWN if any(r == UNKNOWN for r, _ in each) else FAIL)
        results.append(best)
        texts.extend(t for _, t in each)
    if how == "any":
        result = PASS if PASS in results else (UNKNOWN if UNKNOWN in results else FAIL)
    else:
        result = _combine(results)
    wording = {"any": "First Class in any one of the degrees", "all": "First Class in each degree"}[how]
    if len(levels) == 1:
        wording = "First Class in the " + ("Bachelor's" if levels[0] == "UG" else "Master's") + " degree"
    return Check("first_class", wording, result, "; ".join(texts), f"{rule.authority_clause}; AICTE cl. 7.3",
                 f"{rule.authority_page}; p. 39")


_HIGHER = {"UG": ("UG", "PG", "PhD", "Post-Doc"), "PG": ("PG", "PhD", "Post-Doc")}


def _holds_check(facts: Facts, level: str, rule: Rule) -> Check:
    label = "Bachelor's degree" if level == "UG" else "Master's degree"
    if _degrees_of(facts, level) or facts.highest_degree in _HIGHER[level]:
        result, detail = PASS, "held"
    elif facts.highest_degree is None:
        result, detail = UNKNOWN, "the qualifications are not on the record"
    else:
        result, detail = FAIL, f"the highest completed qualification is {facts.highest_degree}"
    return Check(f"holds_{level.lower()}", label, result, detail, rule.authority_clause, rule.authority_page)


_HANDLED_ROUTE_KEYS = {"first_class", "degrees", "relevant_branch"}


def _route_checks(facts: Facts, route: dict, rule: Rule, rules: RuleSet) -> list[Check]:
    """One AICTE cl. 5.1 route. What the record cannot show is left to a person, in the gazette's own words."""
    checks = []
    for level in route.get("degrees", ()):
        if level in ("UG", "PG"):
            checks.append(_holds_check(facts, level, rule))
        elif level == "MCA":
            checks.append(_holds_check(facts, "PG", rule))
    if route.get("first_class"):
        checks.append(_first_class_check(facts, route["first_class"], rule, rules))
    other = sorted(k for k in route if k not in _HANDLED_ROUTE_KEYS) + [d for d in route.get("degrees", ()) if d not in ("UG", "PG", "MCA")]
    if other:
        checks.append(Check("for_a_person", "Requirements a person must check", UNKNOWN,
                            f"not decided from a resume ({', '.join(other)}): {rule.notes}", rule.authority_clause, rule.authority_page))
    return checks


def _best_route(facts: Facts, routes: list[dict], rule: Rule, rules: RuleSet) -> list[Check]:
    """The route that comes out best; a candidate needs only one."""
    order = {PASS: 0, UNKNOWN: 1, FAIL: 2}
    options = [_route_checks(facts, r, rule, rules) for r in routes]
    best = min(range(len(options)), key=lambda i: order[_combine([c.result for c in options[i]])])
    if len(options) > 1:
        for c in options[best]:
            c.label = f"Route {best + 1}: {c.label}"
    return options[best]


def _papers_since(facts: Facts, start: Period | None) -> Bounds:
    """Publications that cleared review after a date; a paper with no year may or may not count."""
    papers = [p for p in facts.papers if p.cleared_review]
    if start is None:
        return Bounds(0, 0)
    low = sum(1 for p in papers if p.year is not None and p.year > start.latest.year)
    high = sum(1 for p in papers if p.year is None or p.year >= start.earliest.year)
    return Bounds(low, high)


def _experience(facts: Facts, rule: Rule, kinds: tuple[str, ...]) -> tuple[Bounds, str, str, str]:
    """Countable years for a rank, how they were arrived at, and the clause and page that govern the counting.

    UGC cl. 3.11 (p. 59) takes the research-degree period out unless it was
    done in service without leave. The AICTE (Degree) Regulation, 2019 has no
    such provision, so nothing is deducted under it; its cl. 2.25 (p. 31) sets
    conditions for counting past service (regular appointment, equivalent
    grade, proper selection) that only documents can show.
    """
    if rule.is_aicte:
        years = facts.experience_years or service_years(facts, kinds)
        return (years, "posts as dated on the resume; the cl. 2.25 conditions for counting past service are for the document check",
                f"{rule.authority_clause}; AICTE cl. 2.25", f"{rule.authority_page}; p. 31")
    years, how = adjusted_years(facts, kinds)
    return years, how, f"{rule.authority_clause}; cl. 3.11", f"{rule.authority_page}; p. 59"


# --- one rank ---------------------------------------------------------------


def check_rank(facts: Facts, rule: Rule, rules: RuleSet) -> RankResult:
    checks: list[Check] = []
    criteria = rule.criteria or {}
    kinds = tuple(criteria.get("experience_types") or ("TEACHING", "RESEARCH"))

    if facts.masters_standing != "HELD":
        # Not a clause of the Regulations: the date is the opening's own. A person confirms it either way.
        when = "after" if facts.masters_standing == AFTER else "in a period that includes"
        checks.append(Check("masters_by_date", "Master's degree held on the eligibility date", UNKNOWN,
                            f"the Master's degree is dated {when} {facts.as_of_text}, the date eligibility is counted on; "
                            "whether it can be counted is for a person to confirm from the certificate",
                            "Eligibility date of the opening", "set by HR"))
    if rule.is_aicte and rule.designation == "ASSISTANT_PROFESSOR":
        checks += _best_route(facts, criteria.get("routes") or [criteria], rule, rules)
    if rule.is_aicte and rule.designation != "ASSISTANT_PROFESSOR" and criteria.get("first_class"):
        checks.append(_first_class_check(facts, criteria["first_class"], rule, rules))
    if rule.min_marks_pct is not None:
        checks.append(_marks_check(facts, rule, rules.relaxations))
    if rule.net_set_required:
        checks.append(_net_set_check(facts, rule))
    if rule.requires_phd:
        checks.append(_phd_check(facts, rule))

    if rule.min_years is not None:
        if rule.designation == "SENIOR_PROFESSOR":
            years = service_years(facts, kinds, match=_is_professor)
            checks.append(_years_check("experience", "Years as Professor", years, rule.min_years, "", rule))
        else:
            years, how, clause, page = _experience(facts, rule, kinds)
            names = [k.lower() for k in kinds]
            what = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            checks.append(_years_check("experience", f"Experience ({what})", years, rule.min_years, how, rule, clause, page))
    if criteria.get("min_years_post_phd"):
        phd = next((d.completed for d in facts.degrees if d.level == "PhD" and d.completed), None) or facts.phd_awarded
        if phd is None:
            checks.append(Check("post_phd", "Experience after the Ph.D.", UNKNOWN if facts.has_phd or facts.phd_standing == UNSURE else FAIL,
                                "the date the Ph.D. was awarded is not on the record" if facts.has_phd else "no Ph.D. has been awarded",
                                rule.authority_clause, rule.authority_page))
        else:
            checks.append(_years_check("post_phd", "Experience after the Ph.D.", service_years(facts, kinds, after=phd),
                                       criteria["min_years_post_phd"], "", rule))
    if criteria.get("min_years_as_associate_equivalent"):
        years = service_years(facts, kinds, match=_is_associate_or_above)
        checks.append(_years_check("associate_level", "Years at Associate Professor level or above", years,
                                   criteria["min_years_as_associate_equivalent"],
                                   "posts titled Associate Professor or Professor; equivalence of other posts is for the committee", rule))

    if criteria.get("publication_routes"):
        first_senior = min((p.start for p in facts.posts if _is_associate_or_above(p) and p.start), key=lambda s: s.earliest, default=None)
        papers, guided = _papers_since(facts, first_senior), doctoral_guided(facts)
        options = []
        for route in criteria["publication_routes"]:
            parts = [at_least(papers, route["min_publications_at_associate_level"])]
            if route.get("min_phd_guided"):
                parts.append(at_least(guided, route["min_phd_guided"]))
            options.append(_combine(parts))
        result = PASS if PASS in options else (UNKNOWN if UNKNOWN in options else FAIL)
        wanted = " or ".join(
            f"{r['min_publications_at_associate_level']} publications" + (f" with {r['min_phd_guided']} Ph.D.s guided" if r.get("min_phd_guided") else "")
            for r in criteria["publication_routes"])
        checks.append(Check("publications_at_level", "Publications at Associate Professor level", result,
                            f"{papers} publications since first holding an Associate Professor post, {guided} Ph.D.s guided; needs {wanted}",
                            rule.authority_clause, rule.authority_page))
    elif rule.min_publications is not None:
        checks.append(_publications_check(facts, rule))

    if rule.research_score_threshold is not None:
        worked = None if facts.research_score else scores.research_score(facts, rules)
        score = facts.research_score or worked.total
        checks.append(Check("research_score", "Research Score (Appendix II, Table 2)", at_least(score, rule.research_score_threshold),
                            f"Research Score {score} against threshold {rule.research_score_threshold:g}, on what the resume claims",
                            rule.authority_clause, f"{rule.authority_page}; pp. 105-107"))
        needed = (rules.score_rows.get("TABLE_2", {}).get("MIN_THREE_CATEGORIES"))
        if worked is not None and needed is not None and worked.categories is not None:
            checks.append(Check("research_score_categories", "Research Score drawn from enough categories",
                                at_least(worked.categories, needed.points),
                                f"points in {worked.categories} of the six categories against {needed.points:g}; "
                                "category 3 (pedagogy, MOOCs, e-content) is not read from resumes",
                                "Appendix II, Table 2, note", needed.authority_page))
    if rule.min_doctoral_guided and not criteria.get("publication_routes"):
        checks.append(_guided_check(facts, rule, rule.min_doctoral_guided))
    if rule.designation == "SENIOR_PROFESSOR":
        checks.append(Check("for_a_person", "Requirements a person must check", UNKNOWN,
                            "the 10% limit on Senior Professor posts and the review by three eminent experts cannot be read from a resume",
                            rule.authority_clause, rule.authority_page))
    return RankResult(rule.designation, _combine([c.result for c in checks]), checks, rule.version_code)


# --- the decision ------------------------------------------------------------


def _no_rule(designation: str, group: str) -> str:
    """Why no threshold was applied. Never a finding about the candidate."""
    rank = RANK_NAMES.get(designation, designation)
    if group in UGC_OTHER_SECTIONS:
        clause, page, what = UGC_OTHER_SECTIONS[group]
        return (f"{what} has its own rule in the UGC Regulations, 2018 ({clause}, {page}), which is not loaded; "
                "cl. 4.1 was not applied in its place")
    if group == NO_OPENING:
        return "the application is not attached to an opening, so the rule set that applies has not been chosen"
    if designation == "SENIOR_PROFESSOR":
        return ("the AICTE (Degree) Regulation, 2019 gives no direct-recruitment rule for Senior Professor "
                "(its Table 1, p. 26, lists the post as filled by promotion)")
    return f"no rule is loaded for {rank} under {group}; this is a gap in the rule tables, not a finding about the candidate"


def _add_shortlist_score(d: Decision, facts: Facts, rules: RuleSet) -> None:
    s = scores.shortlist_score(facts, rules)
    d.shortlist_score = {"table": "TABLE_3A", "low": s.total.low, "high": s.total.high, "notes": s.notes,
                         "lines": [{"label": l.label, "low": l.low, "high": l.high} for l in s.lines]}


def decide(facts: Facts, rules: RuleSet) -> Decision:
    """Applied rank first, then each lower rank in turn, so a candidate lands at the highest rank they meet."""
    applied = facts.designation
    d = Decision(outcome=MANUAL_REVIEW, applied_designation=applied, as_of=facts.as_of, as_of_basis=facts.as_of_basis)
    d.notes.extend(facts.notes)
    if not facts.regulator_implemented:
        # cl. 1.1, proviso 1: another regulator's norms govern, and none of these thresholds is applied.
        d.open_points.append(f"{facts.regulator_id} post: its norms are outside what the engine implements; no UGC or AICTE rule was applied")
        d.failing = Check("regulator", "Governing regulator", UNKNOWN, d.open_points[0], "cl. 1.1", "p. 57")
        return d
    if applied not in rules.by_designation:
        d.open_points.append(_no_rule(applied, rules.discipline_group))
        return d

    rule = rules.by_designation[applied]
    d.rule_version_id, d.rule_version = rule.rule_version_id, rule.version_code
    if rule.is_aicte:
        d.notes.append("Assessed under the AICTE (Degree) Regulation, 2019, as chosen for this opening. "
                       "Whether each degree is in the relevant branch is for the selection committee (AICTE cl. 7.4).")
    elif rules.discipline_group != "GENERAL":
        d.notes.append("AICTE cl. 5.1(j) refers science and humanities faculty to the UGC Regulations, 2018.")
    # The figure shown beside the outcome. Where the rank applied for has no experience requirement of
    # its own, it is counted as the regulation counts it for the ranks that do: AICTE cl. 5.2 counts
    # teaching, research and industry; UGC cl. 4.1 counts teaching and research.
    kinds = tuple((rule.criteria or {}).get("experience_types")
                  or (("TEACHING", "RESEARCH", "INDUSTRY") if rule.is_aicte else ("TEACHING", "RESEARCH")))
    d.experience_years, d.experience_kinds = _experience(facts, rule, kinds)[0], list(kinds)
    if not rule.is_aicte:
        # The UGC scores. AICTE prescribes neither a Research Score nor a short-listing score.
        d.research_score = facts.research_score or scores.research_score(facts, rules).total
        if applied == "ASSISTANT_PROFESSOR":
            _add_shortlist_score(d, facts, rules)

    first = check_rank(facts, rule, rules)
    d.ranks.append(first)
    if first.result == PASS:
        d.outcome, d.eligible_designation = SHORTLISTED, applied
        return d
    if first.result == UNKNOWN:
        d.open_points = [f"{c.label}: {c.detail}" for c in first.checks if c.result == UNKNOWN]
        return d

    d.failing = next(c for c in first.checks if c.result == FAIL)
    for rank in RANKS[RANKS.index(applied) + 1:]:
        if rank not in rules.by_designation:
            continue
        lower = check_rank(facts, rules.by_designation[rank], rules)
        d.ranks.append(lower)
        if lower.result == PASS:
            d.outcome, d.eligible_designation = RE_CATEGORISED, rank
            if rank == "ASSISTANT_PROFESSOR" and not rules.by_designation[rank].is_aicte:
                _add_shortlist_score(d, facts, rules)  # they will be short-listed as Assistant Professor candidates
            return d
        if lower.result == UNKNOWN:
            d.open_points = [f"{RANK_NAMES[rank]}: {c.label}: {c.detail}" for c in lower.checks if c.result == UNKNOWN]
            return d
    d.outcome = NOT_ELIGIBLE
    return d
