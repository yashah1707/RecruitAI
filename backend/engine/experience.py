"""Years of service, from the dated posts on the record.

A resume rarely gives exact dates. "2014 to 2019" is anything from just over
four years to just under six, so the result is a lower and an upper bound.
The lower bound assumes each post began as late and ended as early as its
dates allow; the upper bound the opposite. Overlapping posts are counted
once.

Clause 3.11 (UGC Regulations, 2018, p. 59) then adjusts it: time taken to
acquire an M.Phil. or Ph.D. is not counted as experience, unless the research
degree was pursued in active service without taking any kind of leave.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from backend.engine.facts import Bounds, Facts, Period, Post

DAYS_PER_YEAR = 365.25
_Interval = tuple[date, date]


def _union_days(intervals: list[_Interval]) -> int:
    total, end_of_last = 0, None
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if end_of_last is None or start > end_of_last:
            total += (end - start).days
            end_of_last = end
        elif end > end_of_last:
            total += (end - end_of_last).days
            end_of_last = end
    return total


def _clip(intervals: list[_Interval], start: date, end: date) -> list[_Interval]:
    return [(max(a, start), min(b, end)) for a, b in intervals if min(b, end) > max(a, start)]


def _outside(intervals: list[_Interval], periods: list[_Interval]) -> list[_Interval]:
    """What is left of `intervals` once every one of `periods` is cut out."""
    for cut_start, cut_end in periods:
        kept = []
        for a, b in intervals:
            if cut_end <= a or cut_start >= b:
                kept.append((a, b))
                continue
            if a < cut_start:
                kept.append((a, cut_start))
            if cut_end < b:
                kept.append((cut_end, b))
        intervals = kept
    return intervals


def _intervals(posts: list[Post], as_of: date) -> tuple[list[_Interval], list[_Interval], bool]:
    """(shortest reading, longest reading, whether some post could not be dated)."""
    shortest, longest, undated = [], [], False
    for p in posts:
        if p.start is None:
            undated = True
            continue
        if p.is_current:
            shortest.append((p.start.latest, as_of))
            longest.append((p.start.earliest, as_of))
        elif p.end is not None:
            shortest.append((p.start.latest, p.end.earliest))
            longest.append((p.start.earliest, p.end.latest))
        else:  # started, but the resume does not say when it ended
            undated = True
            longest.append((p.start.earliest, as_of))
    return shortest, longest, undated


def service_years(
    facts: Facts, kinds: tuple[str, ...], match: Callable[[Post], bool] | None = None, after: Period | None = None
) -> Bounds:
    """Years in posts of the given kinds; optionally only posts `match` accepts, or only time after a date."""
    posts = [p for p in facts.posts if p.kind in kinds and (match is None or match(p))]
    shortest, longest, undated = _intervals(posts, facts.as_of)
    if after is not None:
        shortest, longest = _clip(shortest, after.latest, facts.as_of), _clip(longest, after.earliest, facts.as_of)
    low, high = _union_days(shortest) / DAYS_PER_YEAR, _union_days(longest) / DAYS_PER_YEAR

    plain = match is None and after is None and "TEACHING" in kinds
    stated = facts.stated_teaching_years if plain else None
    if not shortest and not longest and stated is not None:
        return Bounds.exactly(stated)  # no post is dated; the total the resume states is all there is
    if undated:
        return Bounds(round(low, 2), None if stated is None else round(max(high, stated), 2))
    return Bounds(round(low, 2), round(high, 2))


_RESEARCH_DEGREE_RE = re.compile(r"ph\.?\s*d|m\.?\s*phil|doctor", re.IGNORECASE)


def adjusted_years(facts: Facts, kinds: tuple[str, ...]) -> tuple[Bounds, str]:
    """Service in posts of `kinds` after the cl. 3.11 adjustment, and one sentence saying what was done."""
    if facts.experience_years is not None:
        return facts.experience_years, "as supplied"
    gross = service_years(facts, kinds)
    research = [d for d in facts.degrees if d.level == "PhD" or _RESEARCH_DEGREE_RE.search(d.name or "")]
    if not research and facts.phd_status in (None, "NOT_APPLICABLE"):
        return gross, "no research degree, so nothing is deducted"

    # The clause has two limbs. The time taken to acquire the degree is not
    # experience: that removes research posts held during it (a research
    # scholar's years are the degree) and, where leave was taken, teaching
    # posts too. Teaching done alongside the degree without any leave counts.
    leave = facts.study_leave_taken
    teaching = [p for p in facts.posts if p.kind in kinds and p.kind == "TEACHING"]
    other = [p for p in facts.posts if p.kind in kinds and p.kind != "TEACHING"]
    t_short, t_long, t_undated = _intervals(teaching, facts.as_of)
    o_short, o_long, o_undated = _intervals(other, facts.as_of)
    if not (t_short or t_long or o_short or o_long):
        # No post is dated; only the stated total is known.
        return (gross if leave is False else Bounds(0.0, gross.high)), "no post is dated, so the research-degree period cannot be set against the service"

    widest, narrowest, period_known = [], [], bool(research)
    for d in research:
        end = d.completed or (Period(facts.as_of, facts.as_of) if facts.phd_status != "COMPLETED" else None)
        if d.registered is None or end is None:
            period_known = False
            continue
        widest.append((d.registered.earliest, end.latest))
        narrowest.append((d.registered.latest, end.earliest))

    # Lower bound: the degree took as long as its dates allow, and everything that can fall inside it does.
    if period_known:
        low_parts = _outside(o_short, widest) + (t_short if leave is False else _outside(t_short, widest))
    else:
        low_parts = t_short if leave is False else []
    # Upper bound: the degree was as short as its dates allow; with no dates, nothing is known to fall inside it.
    high_parts = (_outside(o_long, narrowest) if period_known else o_long) + (
        t_long if leave is not True else (_outside(t_long, narrowest) if period_known else t_long))
    low = _union_days(low_parts) / DAYS_PER_YEAR
    high = None if gross.high is None else (
        gross.high if (t_undated or o_undated) else min(gross.high, _union_days(high_parts) / DAYS_PER_YEAR))

    note = {True: "study leave was taken, so service during the research degree is deducted (cl. 3.11)",
            False: "teaching done alongside the research degree without leave counts (cl. 3.11)",
            None: "whether study leave was taken is not known, so it is worked out both ways (cl. 3.11)"}[leave]
    if other:
        note += "; research posts held during the degree are not counted"
    if not period_known:
        note += "; the dates of the research degree are not stated"
    return Bounds(round(low, 2), None if high is None else round(high, 2)), note
