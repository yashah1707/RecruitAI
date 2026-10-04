"""Deterministic clean-up applied to every model's output, whatever provider.

Checks the model cannot be trusted to do on itself:

1. Drop evidence that isn't genuinely in the resume text.
2. File a `marks_pct` that is really a CGPA under `cgpa` instead.
3. Reject marks taken from the wrong row of the qualifications table.

Neither converts or computes anything — a rejected value becomes null and the
row routes to review, so a human supplies the real figure. Converting a CGPA
to a percentage would be arithmetic on candidate data, which this layer is
explicitly not allowed to do (the conversion factor varies by university, so
there is no single correct formula to apply anyway).
"""

from __future__ import annotations

import logging
import re

from llm.confidence import grounding_quality, references_lower_qualification
from llm.interface import FIELD_NAMES, ExtractionResult

logger = logging.getLogger("recruitai.postprocess")

# A percentage at or below this is not a credible exam percentage for a
# degree being offered for a faculty post — statutory minima sit at 50-55%.
# A number this low in a percentage field is a CGPA the model mislabelled
# (real examples seen: "8.2% M.E.", "9.13 CGPA", "8.79").
MAX_IMPLAUSIBLE_PERCENTAGE = 10.0

# Deliberately no keyword ("CGPA"/"GPA") check here. Grade-point scales top
# out at 10, so the number alone is decisive, and matching on nearby words
# actively misfires: an evidence span quoting a Master's row as
# "M.Tech ... GPA: 7.78" alongside a genuine 63.56 percentage would see the
# word "GPA" and reject a perfectly good percentage.

# Full containment: every word of the shorter title must appear in the longer
# one. A genuine re-listing repeats the title verbatim and appends metadata
# ("..., IJSRT, 2021"), so it scores exactly 1.0; anything less means a word
# actually differs, and one differing word is usually the whole point ("Part
# I" vs "Part II", "...for Vision" vs "...for Speech"). Anything looser
# merged distinct papers in testing, and understating a candidate's record is
# worse than leaving a near-duplicate for a human to notice.
TITLE_DUPLICATE_SIMILARITY = 1.0


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", title.lower()).strip()


# A re-listing needs to share at least this many words before containment is
# trusted, so that a short title ("Machine Learning") isn't swallowed by a
# longer unrelated one that happens to contain it.
MIN_SHARED_TITLE_TOKENS = 4


def _title_similarity(a: str, b: str) -> float:
    """Containment similarity between two publication titles.

    Containment, not Jaccard: a re-listing typically *adds* tokens (venue,
    year, "Level of participation"), so the plain title is a subset of the
    decorated one. Jaccard punishes exactly that -- the real pair
    "Deep Learning Approaches for Image Forgery Detection" vs the same title
    plus ", IJSRT, 2021" scores 0.78 on Jaccard and slips through, but 1.0
    on containment.
    """
    ta, tb = set(_normalize_title(a).split()), set(_normalize_title(b).split())
    if not ta or not tb:
        return 0.0
    shared = ta & tb
    if len(shared) < MIN_SHARED_TITLE_TOKENS:
        return 0.0
    return len(shared) / min(len(ta), len(tb))


def deduplicate_titles(titles: list[str]) -> tuple[list[str], int]:
    """Collapse re-listings of the same work. Returns (kept, removed_count).

    Real CVs list the same paper under "Publications" and again inside a
    projects/research table with different formatting. Each mention carries
    its own honest quote, so the evidence check cannot catch the double
    count -- only comparing the titles to each other can.
    """
    kept: list[str] = []
    removed = 0
    for title in titles:
        if not title or not title.strip():
            continue
        if any(_title_similarity(title, k) >= TITLE_DUPLICATE_SIMILARITY for k in kept):
            removed += 1
            continue
        kept.append(title)
    return kept, removed


# How much of a date the resume actually stated. A bare year normalised to
# "2015-01-01" reads in the export exactly like a verified full date, so a
# reviewer cannot tell "year only, verified" from "day-accurate, verified".
DATE_FIELDS_WITH_PRECISION = ("phd_award_date", "masters_award_date")

_MONTHS = (
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
)


def date_precision(evidence: str | None) -> str | None:
    """Infer whether the source gave a year, a month, or a full date.

    Returns "year" | "month" | "full", or None when there is no evidence to
    judge from. Deliberately reads the evidence quote rather than the parsed
    value, because the parsed value has already had the missing parts filled
    in and can no longer tell us what was really there.
    """
    if not evidence:
        return None
    text = evidence.lower()
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", text) or re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text):
        return "full"
    if re.search(r"\b\d{1,2}(st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")", text):
        return "full"
    if any(m in text for m in _MONTHS):
        return "month"
    if re.search(r"\b(19|20)\d{2}\b", text):
        return "year"
    return None


def looks_like_cgpa(value: float | None, evidence: str | None) -> bool:
    """Whether a marks_pct value is really a grade point, not a percentage."""
    if value is None:
        return False
    return value <= MAX_IMPLAUSIBLE_PERCENTAGE


# Fields whose successful, grounded extraction shows the qualifications and
# eligibility content of the resume was actually readable. A confident "no
# NET/SET here" is only meaningful if there was something to search.
_SEARCH_COVERAGE_FIELDS = ("highest_degree", "has_phd", "masters_award_date", "teaching_years_raw")

# A negative finding can never be better-evidenced than this, because no quote
# can prove an absence. Caps the model's self-report rather than trusting it.
MAX_NEGATIVE_FINDING_CONFIDENCE = 0.85

# A value whose quote could not be located in the source text is, at best, a
# guess that happens to be right. Capped below the routing threshold so it
# always reaches a human rather than riding on the model's self-report.
MAX_UNGROUNDED_CONFIDENCE = 0.4

# Located only after punctuation was stripped. Could be a PDF decoding
# artifact or the model tidying as it copied; either way the quote is not
# quite verbatim, so it is a little less checkable by a human.
MAX_LOOSE_MATCH_CONFIDENCE = 0.8


def search_coverage(result: ExtractionResult) -> float:
    """How much qualification content was actually extracted, 0.0-1.0.

    Used to temper a `net_set_status = NONE`. The source plan calls NET/SET
    "the single highest-risk piece of logic in the whole system", and a flat
    self-reported 0.95 on a negative finding is indistinguishable from "the
    model didn't look properly" -- it just happens to be right when the
    resume genuinely has no NET/SET. Deriving it from how much of the resume
    parsed at all makes the number mean something.
    """
    grounded = sum(
        1
        for name in _SEARCH_COVERAGE_FIELDS
        if getattr(result, name).value is not None and getattr(result, name).evidence
    )
    return grounded / len(_SEARCH_COVERAGE_FIELDS)


def _temper_negative_net_set(data: dict, result: ExtractionResult) -> bool:
    """Scale a NONE's confidence by how much of the resume was searchable."""
    field = data["net_set_status"]
    if field["value"] != "NONE":
        return False
    coverage = search_coverage(result)
    tempered = round(min(field["confidence"], MAX_NEGATIVE_FINDING_CONFIDENCE) * coverage, 3)
    if tempered == field["confidence"]:
        return False
    field["confidence"] = tempered
    return True


_NAME_PART_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# An all-caps word this short inside an otherwise mixed-case name is a set of
# initials ("RK Sharma"), not a shouted word, and must stay as written.
_MAX_INITIALS_LETTERS = 3


def format_person_name(name: str | None) -> str | None:
    """Write a name the way a reviewer expects: each word capitalised.

    Resumes give names in whatever case the candidate typed -- "SAMPLE
    EXAMPLETON", "sample exampleton" -- and a column mixing those reads as
    sloppy data. This changes letter case and spacing only; no letter is
    added, removed or reordered, so the name is still exactly what the resume
    said. A word that is already mixed-case ("McDonald", "DSouza") is left
    alone, since its capitals are deliberate. Idempotent.
    """
    if not name or not name.strip():
        return name
    words = name.split()
    whole_name_is_caps = name.upper() == name

    def recase(word: str) -> str:
        letters = "".join(_NAME_PART_RE.findall(word))
        if not letters:
            return word
        if letters.islower() or (
            letters.isupper() and (whole_name_is_caps or len(letters) > _MAX_INITIALS_LETTERS)
        ):
            # Capitalise each run of letters, so "s.k." -> "S.K." and
            # "anne-marie" -> "Anne-Marie".
            return _NAME_PART_RE.sub(lambda m: m.group(0).capitalize(), word)
        return word

    return " ".join(recase(w) for w in words)


# --- uniform degree display --------------------------------------------------
#
# Resumes write the same qualification a dozen ways ("M.Tech.", "M. Tech",
# "Master of Technology", "ME.", "Master of Engineering (M.E)"), and a column
# that mixes them reads as unclean data. format_degree() rewrites the model's
# verbatim quote into one house style: "<degree> <Course In Title Case>".
#
# It only re-spells what the quote already says -- a known degree alias, a
# known abbreviation -- and never adds a course the resume did not state: a
# bare "Master of Engineering" becomes "M.E.", not "M.E. Computer Engineering".
# The verbatim quote is still what the grounding check runs on, and it stays
# in the raw_llm_output sheet.

_B = r"(?<![A-Za-z])"  # not preceded by a letter
_E = r"(?![A-Za-z])"  # not followed by a letter

# Canonical spelling -> pattern for every way it gets written. Order matters:
# longer / more specific degrees first.
_DEGREE_ALIASES: tuple[tuple[str, str], ...] = (
    ("Post-Doc", rf"{_B}post[\s-]*doc(?:toral|torate)?(?:\s+fellow(?:ship)?)?{_E}"),
    ("Ph.D.", rf"{_B}(?:ph\.?\s*d\.?|d\.?\s*phil\.?|doctor\s+of\s+philosophy|doctorate){_E}"),
    ("M.Phil.", rf"{_B}(?:m\.?\s*phil\.?|master(?:s|'s)?\s+of\s+philosophy){_E}"),
    ("M.Tech", rf"{_B}(?:m\.?\s*tech\.?|master(?:s|'s)?\s+(?:of|in)\s+technology){_E}"),
    ("M.E.", rf"{_B}(?:m\.?\s?e\.?|master(?:s|'s)?\s+(?:of|in)\s+engineering){_E}"),
    ("MCA", rf"{_B}(?:m\.?\s?c\.?\s?a\.?|master(?:s|'s)?\s+(?:of|in)\s+computer\s+applications?){_E}"),
    ("MBA", rf"{_B}(?:m\.?\s?b\.?\s?a\.?|master(?:s|'s)?\s+(?:of|in)\s+business\s+administration){_E}"),
    ("M.Sc.", rf"{_B}(?:m\.?\s*sc\.?|master(?:s|'s)?\s+(?:of|in)\s+science){_E}"),
    ("M.Com.", rf"{_B}(?:m\.?\s*com\.?|master(?:s|'s)?\s+(?:of|in)\s+commerce){_E}"),
    ("M.A.", rf"{_B}(?:m\.\s?a\.?|master(?:s|'s)?\s+(?:of|in)\s+arts){_E}"),
    ("B.Tech", rf"{_B}(?:b\.?\s*tech\.?|bat?chelor(?:s|'s)?\s+(?:of|in)\s+technology){_E}"),
    ("B.E.", rf"{_B}(?:b\.?\s?e\.?|bat?chelor(?:s|'s)?\s+(?:of|in)\s+engineering){_E}"),
    ("BCA", rf"{_B}(?:b\.?\s?c\.?\s?a\.?|bat?chelor(?:s|'s)?\s+(?:of|in)\s+computer\s+applications?){_E}"),
    ("B.Sc.", rf"{_B}(?:b\.?\s*sc\.?|bat?chelor(?:s|'s)?\s+(?:of|in)\s+science){_E}"),
    ("B.Com.", rf"{_B}(?:b\.?\s*com\.?|bat?chelor(?:s|'s)?\s+(?:of|in)\s+commerce){_E}"),
    ("B.A.", rf"{_B}(?:b\.\s?a\.?|bat?chelor(?:s|'s)?\s+(?:of|in)\s+arts){_E}"),
    ("Diploma", rf"{_B}diploma{_E}"),
)
_DEGREE_PATTERNS = tuple((canon, re.compile(p, re.IGNORECASE)) for canon, p in _DEGREE_ALIASES)

# Abbreviations that mean exactly one thing in a course name.
_COURSE_EXPANSIONS: dict[str, str] = {
    "sci": "Science", "engg": "Engineering", "eng": "Engineering", "engr": "Engineering",
    "comp": "Computer", "tech": "Technology", "info": "Information", "mech": "Mechanical",
    "elec": "Electrical", "electr": "Electrical", "comm": "Communication", "mgmt": "Management",
    "appl": "Applications", "maths": "Mathematics", "math": "Mathematics",
    "cse": "Computer Science and Engineering", "&": "and",
}
_COURSE_SMALL_WORDS = frozenset({"and", "of", "in", "for", "with"})
# Things that sit next to a degree in a table row but are not its course.
_COURSE_NOISE = frozenset({
    "honours", "honors", "hons", "degree", "first", "second", "third", "class", "division",
    "distinction", "cgpa", "gpa", "sgpa", "percentage", "percent", "marks", "pursuing",
    "completed", "passed", "awarded", "thesis", "submitted", "regular", "full", "time",
    # a "PhD Scholar ... Aug 2021 - Present" line: none of this is the course
    "scholar", "present", "till", "date", "onwards", "ongoing",
    "jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june",
    "jul", "july", "aug", "august", "sep", "sept", "september", "oct", "october", "nov",
    "november", "dec", "december",
})
# U+FFFD is what a PDF's en-dash becomes when its encoding is lost; it is a
# separator, never part of a word.
_ELLIPSIS_OR_BRACKETS_RE = re.compile(r"\.\.\.|…|�|[()\[\]{}|,;:/]|\s[-–—]\s")
_NUMBERISH_RE = re.compile(r"^[\d.%/–-]+$")  # 63.56, 78.9%, 2015, 2025-26


def format_degree(
    evidence: str | None,
    extra_noise: frozenset[str] = frozenset(),
    force_degree: str | None = None,
) -> str | None:
    """Rewrite a degree quote as "<degree> <Course>", in one uniform style.

    `force_degree` names the degree the quote is known to be about, for a
    quote that may not spell it out at all (a phd_status quote can be just
    "Completed"). `extra_noise` adds words to drop from the course.
    """
    if not evidence or not evidence.strip():
        return evidence
    text = " ".join(_ELLIPSIS_OR_BRACKETS_RE.sub(" ", evidence).split())

    degree = force_degree
    earliest = len(text) + 1
    for canon, pattern in _DEGREE_PATTERNS if force_degree is None else ():
        m = pattern.search(text)
        if m and m.start() < earliest:
            degree, earliest = canon, m.start()
    if degree is not None:
        # Drop every spelling of that degree ("Master of Engineering (M.E)"
        # names it twice) and keep the rest as the course.
        pattern = dict(_DEGREE_PATTERNS)[degree]
        text = " ".join(pattern.sub(" ", text).split())

    words: list[str] = []
    for raw in text.split():
        bare = raw.strip(".").lower()
        if not bare or _NUMBERISH_RE.match(bare) or bare in _COURSE_NOISE or bare in extra_noise:
            continue
        if bare in _COURSE_EXPANSIONS:
            words.append(_COURSE_EXPANSIONS[bare])
        elif bare in _COURSE_SMALL_WORDS:
            words.append(bare)
        elif raw.isupper() and len(raw) <= 5 and raw.isalpha():
            words.append(raw)  # an acronym: VLSI, IT, AI
        else:
            words.append(raw.strip(".").capitalize() if raw.strip(".").isalpha() else raw.strip("."))
    # "M.Tech in Computer Science" -> the joining word is not part of the course.
    while words and words[0] in _COURSE_SMALL_WORDS:
        words.pop(0)
    while words and words[-1] in _COURSE_SMALL_WORDS:
        words.pop()

    course = " ".join(words)
    return " ".join(part for part in (degree, course) if part) or None


# How each phd_status value is written for a reviewer. The schema value (and
# the DataModel's Lists!PhDStatus spelling) is unchanged underneath.
PHD_STATUS_LABELS: dict[str, str] = {
    "NOT_APPLICABLE": "NA",
    "PURSUING": "Pursuing",
    "REGISTERED": "Registered",
    "THESIS_SUBMITTED": "Thesis Submitted",
    "COMPLETED": "Completed",
}

# Words in a phd_status quote that say the status, which the status column
# already shows -- they are not part of the course name.
_PHD_STATUS_WORDS = frozenset({
    "appearing", "pursuing", "persuing", "pursing", "purusing", "pursuring", "ongoing", "scholar", "research",
    "candidate", "registered", "enrolled", "status", "expected", "since", "from", "till",
    "date", "year", "work", "progress", "ph", "d", "phd",
})


def format_phd_status(value: str | None) -> str | None:
    return PHD_STATUS_LABELS.get(value, value) if value else value


def format_phd_evidence(status: str | None, evidence: str | None) -> str | None:
    """The phd_status quote as "Ph.D. <Course>", or "NA" when there is no PhD.

    For NOT_APPLICABLE the model quotes whatever shows it looked (a section
    heading, an entrance-test line); that is noise to a reviewer, so the cell
    just says NA. Otherwise the quote is reduced to the degree and its course,
    in the same style as highest_degree_evidence -- the status word itself is
    in the status column.
    """
    if status == "NOT_APPLICABLE":
        return "NA"
    if not status or not evidence or not evidence.strip():
        return evidence
    return format_degree(evidence, extra_noise=_PHD_STATUS_WORDS, force_degree="Ph.D.")


def _check_details(data: dict, resume_text: str) -> int:
    """Ground the detail lists against the resume; returns how many failed.

    The same rule as every other field -- nothing the resume does not say --
    applied per item instead of per quote. A record (degree, publication,
    event) whose key text cannot be found is kept but marked
    `found_in_resume = False`, so a reviewer sees it flagged rather than
    silently losing or silently trusting it. A bare string (a subject, a
    skill) has nothing else to it, so an unfound one is dropped. Re-listed
    publications and repeated strings are collapsed.
    """
    not_found = 0

    def found(text: str | None) -> bool:
        return bool(text) and grounding_quality(resume_text, text) != "none"

    for entry in data.get("education") or []:
        entry["found_in_resume"] = found(entry.get("degree")) or found(entry.get("course"))
    for entry in data.get("events") or []:
        entry["found_in_resume"] = found(entry.get("title"))

    publications = data.get("publications") or []
    kept_titles, _ = deduplicate_titles([p["title"] for p in publications])
    unique, seen = [], set()
    for entry in publications:
        if entry["title"] in kept_titles and entry["title"] not in seen:
            seen.add(entry["title"])
            entry["found_in_resume"] = found(entry["title"])
            unique.append(entry)
    data["publications"] = unique

    for entry in data.get("experience") or []:
        entry["found_in_resume"] = found(entry.get("institution")) or found(entry.get("designation"))
    for entry in data.get("achievements") or []:
        entry["found_in_resume"] = found(entry.get("title"))
    for entry in data.get("guidance") or []:
        entry["found_in_resume"] = found(entry.get("description"))

    for group in ("education", "publications", "events", "experience", "achievements", "guidance"):
        not_found += sum(1 for e in data.get(group) or [] if not e["found_in_resume"])

    # A contact detail that is not in the resume is not the candidate's: drop it.
    for name in ("email", "phone"):
        if data.get(name) and not found(data[name]):
            data[name] = None
            not_found += 1

    for name in ("subjects_taught", "skills", "memberships"):
        kept, seen_lower = [], set()
        for item in data.get(name) or []:
            if item.lower() in seen_lower:
                continue
            seen_lower.add(item.lower())
            if found(item):
                kept.append(item)
            else:
                not_found += 1
        data[name] = kept
    return not_found


def sanitize(result: ExtractionResult, resume_text: str) -> tuple[ExtractionResult, dict[str, int]]:
    """Return a cleaned copy of `result` plus a count of what was dropped.

    The counts carry no candidate data, so they are safe to log.
    """
    data = result.model_dump(mode="json")
    stats = {"ungrounded_evidence": 0, "cgpa_moved_from_marks_pct": 0, "marks_from_wrong_qualification": 0, "net_set_none_tempered": 0, "duplicate_publications_removed": 0, "loose_evidence_match": 0, "has_phd_realigned_to_phd_status": 0}

    for name in FIELD_NAMES:
        evidence = data[name]["evidence"]
        if not evidence:
            continue
        quality = grounding_quality(resume_text, evidence)
        if quality == "loose":
            # Located, but not character-for-character. Still usable evidence
            # -- keep the quote -- yet not as checkable as a verbatim one, so
            # it should not sit at the same confidence as an exact match.
            data[name]["confidence"] = round(
                min(data[name]["confidence"], MAX_LOOSE_MATCH_CONFIDENCE), 3
            )
            stats["loose_evidence_match"] += 1
        elif quality == "none":
            data[name]["evidence"] = None
            # Nulling the quote alone left the model's own 0.95 sitting next
            # to a value nothing supports, which is how confidence ended up
            # clustered on {0.9, 0.95, 0.98} regardless of extraction
            # quality. An ungrounded value cannot be better than uncertain.
            data[name]["confidence"] = round(
                min(data[name]["confidence"], MAX_UNGROUNDED_CONFIDENCE), 3
            )
            stats["ungrounded_evidence"] += 1

    marks, cgpa = data["marks_pct"], data["cgpa"]

    # Wrong-row rejection runs first: a mark lifted from the 12th-standard
    # line is wrong whichever scale it is on, so there is nothing worth
    # rescuing by moving it to `cgpa`.
    for name, field in (("marks_pct", marks), ("cgpa", cgpa)):
        if field["value"] is not None and references_lower_qualification(field["evidence"]):
            # Evidence is deliberately kept: it shows the reviewer exactly
            # which row was rejected ("Higher Secondary ... 85.2%") so they
            # can enter the right figure, and it lets llm.confidence tell
            # this case apart from a resume that states no marks at all.
            field["value"] = None
            field["confidence"] = 0.0
            stats["marks_from_wrong_qualification"] += 1

    if looks_like_cgpa(marks["value"], marks["evidence"]):
        # Move it rather than discard it. Dropping the number entirely used
        # to cost real candidates their whole marks score -- four of twelve
        # resumes in testing stated a CGPA (9.13, 9.20, 8.79, 8.2) and were
        # scored as having no marks at all. The value is real data, it was
        # just filed under the wrong scale.
        if cgpa["value"] is None:
            cgpa.update(marks)
        marks["value"] = None
        marks["confidence"] = 0.0
        marks["evidence"] = None
        stats["cgpa_moved_from_marks_pct"] += 1

    # Collapse re-listed publications and correct each count to match. Both
    # lists get the same treatment: a work re-listed under "Research Work"
    # double-counts whether or not it has cleared review yet.
    for titles_field, count_field in (
        ("publication_titles", "publications_count"),
        ("publications_in_progress_titles", "publications_in_progress_count"),
    ):
        titles = data[titles_field]["value"]
        if not titles:
            continue
        kept, removed = deduplicate_titles(titles)
        if not removed:
            continue
        data[titles_field]["value"] = kept
        data[count_field]["value"] = len(kept)
        # The model's own count is no longer the source of truth, so its
        # confidence in that count no longer applies either.
        data[count_field]["confidence"] = round(
            min(data[count_field]["confidence"], 0.75), 3
        )
        stats["duplicate_publications_removed"] += removed

    for name in DATE_FIELDS_WITH_PRECISION:
        data[f"{name}_precision"] = date_precision(data[name]["evidence"]) if data[name]["value"] else None

    # has_phd is COMPLETED restated as a boolean. The model reports both, so
    # they can disagree ("Thesis Submitted" with has_phd=true); phd_status is
    # the richer field and wins. Nothing downstream changes -- has_phd keeps
    # exactly the meaning it always had.
    if data["phd_status"]["value"] is not None:
        derived = data["phd_status"]["value"] == "COMPLETED"
        if data["has_phd"]["value"] != derived:
            data["has_phd"]["value"] = derived
            stats["has_phd_realigned_to_phd_status"] += 1

    if _temper_negative_net_set(data, result):
        stats["net_set_none_tempered"] += 1

    data["candidate_name"]["value"] = format_person_name(data["candidate_name"]["value"])

    stats["detail_items_not_found"] = _check_details(data, resume_text)

    data["raw_llm_output"] = result.raw_llm_output
    return ExtractionResult(**data), stats


def log_stats(stats: dict[str, int]) -> None:
    dropped = {k: v for k, v in stats.items() if v}
    if dropped:
        logger.info("postprocess_dropped %s", dropped)
