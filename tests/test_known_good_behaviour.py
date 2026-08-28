"""Behaviour verified correct by hand against the source resumes.

Each case below was spot-checked by a human reading the actual CV, so these
are the things a future prompt tweak, model swap or post-processing change
must not silently break. They exercise the classification rules rather than
re-running a model, so they are fast and deterministic.

The distinctions here are the ones that were repeatedly got wrong before:
an entrance test is not NET/SET, and a PhD in progress is not a PhD.
"""

from __future__ import annotations

from datetime import date

import pytest

from llm.confidence import evaluate, references_lower_qualification
from llm.postprocess import deduplicate_titles, looks_like_cgpa, sanitize
from tests.test_postprocess import _f, _result

THRESHOLD = 0.7


# --- an entrance/eligibility test that is NOT NET/SET/SLET ------------------

@pytest.mark.parametrize(
    "credential",
    [
        # Nishant: a PhD *entrance* test, not a teaching-eligibility test.
        "Ph.D. Entrance Test (PET), Sant Gadge Baba Amravati University - Qualified",
        # Kavita: a school-teacher eligibility test, not NET/SET/SLET.
        "MPTET (Madhya Pradesh Teacher Eligibility Test) Qualified",
    ],
)
def test_other_eligibility_tests_are_not_treated_as_net_set(credential):
    """These are the traps: both contain the words "eligibility"/"test" and
    neither qualifies anyone for a faculty post under NET/SET rules."""
    text = f"Some resume text. {credential} M.Tech 2023."
    r = _result(
        net_set_status=_f("NONE", 0.95, credential),
        masters_award_date=_f(date(2023, 1, 1), 0.9, "M.Tech 2023"),
        teaching_years_raw=_f(0, 0.95, "Some resume text"),
    )
    result, _ = sanitize(r, text)
    assert result.net_set_status.value == "NONE"
    assert result.net_set_status.evidence == credential


# --- a PhD in progress is not a PhD -----------------------------------------

# The six candidates whose resumes exposed the gap: has_phd = False collapsed
# "thesis submitted, decision imminent" into the same value as "no doctoral
# activity at all". phd_status keeps them apart; has_phd is unchanged.
PHD_IN_PROGRESS_CASES = [
    ("Ph.D. (Thesis Submitted)", "THESIS_SUBMITTED"),                    # Aparna
    ("Ph. D. (Computer Engineering) Appearing", "PURSUING"),             # Mahendra
    ("Ph.D. MIT-ADT University, Pune Appearing", "PURSUING"),            # Chandan
    ("PhD Pursuing", "PURSUING"),                                        # Mayuri
    ("PhD Pursuing (Computer Science & Engineering)", "PURSUING"),       # Kavita
    ("PhD Scholar in Computer Science and Engineering", "PURSUING"),     # Anwesha
    ("2026 MGM University - PhD Purusing", "PURSUING"),                  # Bibave (sic)
]


@pytest.mark.parametrize("status_text,expected_status", PHD_IN_PROGRESS_CASES)
def test_a_phd_in_progress_does_not_count_as_awarded(status_text, expected_status):
    """Submitted / appearing / pursuing / scholar all mean "not yet awarded".
    Treating any of them as a completed PhD would grant an exemption the
    candidate has not earned."""
    text = f"Candidate resume. {status_text}. M.Tech 2015."
    r = _result(
        highest_degree=_f("PG", 0.95, status_text),
        has_phd=_f(False, 0.95, status_text),
        phd_status=_f(expected_status, 0.95, status_text),
        phd_award_date=_f(None, 0.0, None),
    )
    result, _ = sanitize(r, text)
    assert result.has_phd.value is False
    assert result.highest_degree.value == "PG"
    outcome = evaluate(result, threshold=THRESHOLD)
    # ...and with no PhD there is no regulation year to chase.
    assert "phd_regulation:manual_entry_required" not in outcome.reasons


@pytest.mark.parametrize("status_text,expected_status", PHD_IN_PROGRESS_CASES)
def test_phd_status_distinguishes_what_has_phd_flattens(status_text, expected_status):
    """The point of the field: every one of these is has_phd=False, but they
    are not the same situation, and a reviewer shortlisting on doctoral
    progress needs to see which is which."""
    text = f"Candidate resume. {status_text}. M.Tech 2015."
    r = _result(
        has_phd=_f(False, 0.95, status_text),
        phd_status=_f(expected_status, 0.95, status_text),
    )
    result, _ = sanitize(r, text)
    assert result.phd_status.value == expected_status
    assert result.phd_status.value != "NOT_APPLICABLE"  # not "no doctoral activity"
    assert result.has_phd.value is False                 # ...but still not a PhD


def test_has_phd_is_realigned_when_it_contradicts_phd_status():
    """phd_status is the richer field, so it wins a disagreement. A model
    claiming has_phd=true beside "Thesis Submitted" must not award a degree
    that hasn't been conferred."""
    text = "Ph.D. (Thesis Submitted). M.Tech 2015."
    r = _result(
        has_phd=_f(True, 0.95, "Ph.D. (Thesis Submitted)"),
        phd_status=_f("THESIS_SUBMITTED", 0.95, "Ph.D. (Thesis Submitted)"),
    )
    result, stats = sanitize(r, text)
    assert result.has_phd.value is False
    assert stats["has_phd_realigned_to_phd_status"] == 1


def test_a_phd_entrance_test_is_not_doctoral_study():
    """Nishant: PET is an entrance exam, so there is no doctoral study to
    record -- NOT_APPLICABLE, not PURSUING."""
    ev = "Ph.D. Entrance Test (PET), Sant Gadge Baba Amravati University - Qualified"
    r = _result(has_phd=_f(False, 0.95, ev), phd_status=_f("NOT_APPLICABLE", 0.95, ev))
    result, _ = sanitize(r, f"resume text {ev}")
    assert result.phd_status.value == "NOT_APPLICABLE"
    assert result.has_phd.value is False


# --- marks that were read from the right row --------------------------------

@pytest.mark.parametrize(
    "evidence,value",
    [
        ("M.Tech. (Computer) First Class 63.56 2015", 63.56),        # Mahendra
        ("M.E.(CSE/SE) Dr.B.A.M.University, Aurangabad. 2017 61.12%", 61.12),  # suvarna
        ("Master of Engineering 2015 ... 78.9", 78.9),               # Supriya
        ("Masters of Technology - M.Tech. (Computer science) Marks 66.65%", 66.65),  # Kavita
    ],
)
def test_masters_percentages_are_accepted_unchanged(evidence, value):
    r = _result(marks_pct=_f(value, 0.95, evidence))
    result, stats = sanitize(r, f"resume text {evidence}")
    assert result.marks_pct.value == value
    assert stats["marks_from_wrong_qualification"] == 0
    assert not looks_like_cgpa(value, evidence)


@pytest.mark.parametrize(
    "evidence,value",
    [
        ("M.Tech ... 9.13 CGPA", 9.13),   # Nishant
        ("M. Tech ... 8.79", 8.79),       # Abhishek
        ("ME. (Computer Engineering) 9.20 CGPA", 9.20),  # Mayuri
        ("M.E.( Comp. Engg) 8.2%", 8.2),  # Bibave -- CGPA written with a % sign
    ],
)
def test_masters_grade_points_are_filed_as_cgpa_not_percentages(evidence, value):
    r = _result(marks_pct=_f(value, 0.95, evidence))
    result, _ = sanitize(r, f"resume text {evidence}")
    assert result.cgpa.value == value
    assert result.marks_pct.value is None


# --- study leave was never guessed ------------------------------------------

def test_study_leave_stays_null_when_the_resume_is_silent():
    """Correct for all 12 source resumes: none states it, none was guessed.
    Guessing either direction changes an experience calculation downstream."""
    result, _ = sanitize(_result(study_leave_taken=_f(None, 0.0, None)), "resume text")
    assert result.study_leave_taken.value is None
    outcome = evaluate(result, threshold=THRESHOLD)
    assert not any(r.startswith("study_leave_taken") for r in outcome.reasons)


# --- publication counts verified by hand ------------------------------------

def test_distinct_publication_lists_are_counted_as_listed():
    """Mahendra's 25 and Aparna's 15 were counted by hand against long, messy
    tables. Dedup must not shave real entries off a genuine list."""
    titles = [f"A Distinct Study of Topic Number {i} in Engineering" for i in range(25)]
    kept, removed = deduplicate_titles(titles)
    assert removed == 0
    assert len(kept) == 25


def test_lower_qualification_detection_does_not_fire_on_masters_rows():
    for evidence in (
        "M.Tech. (Computer) First Class 63.56 2015",
        "Master of Engineering 2015",
        "M.E.( Comp. Engg)",
        "Ph.D. [Computer Sci. & Engg.] Sage University",
    ):
        assert references_lower_qualification(evidence) is False


# --- elided quotes and the date precision that depends on them --------------

BIBAVE_SOURCE = (
    "Education\n2026\nMGM University\n-\nPhD Purusing\n2015-17\n"
    "Amrutvahini College Of Engg, Sangamner\n8.2%\nM.E.( Comp. Engg)\n2012-15\n"
)
BIBAVE_QUOTE = "2015-17 Amrutvahini College Of Engg, Sangamner ... M.E.( Comp. Engg)"


def test_a_quote_elided_with_an_ellipsis_still_grounds():
    """A model citing a table row skips the columns it doesn't need. Every
    fragment is genuinely in the resume, so rejecting the whole quote throws
    away real evidence."""
    from llm.confidence import grounding_quality

    assert grounding_quality(BIBAVE_SOURCE, BIBAVE_QUOTE) == "loose"


def test_an_elided_quote_never_grades_better_than_loose():
    """The fragments are real but the concatenation is not verbatim, so it
    must not score as highly as a single exact span."""
    from llm.confidence import grounding_quality

    assert grounding_quality(BIBAVE_SOURCE, "Amrutvahini College Of Engg") == "exact"
    assert grounding_quality(BIBAVE_SOURCE, BIBAVE_QUOTE) == "loose"


def test_an_ellipsis_cannot_smuggle_in_a_fabricated_fragment():
    from llm.confidence import grounding_quality

    assert grounding_quality(BIBAVE_SOURCE, "2015-17 Amrutvahini ... Harvard University") == "none"
    assert grounding_quality(BIBAVE_SOURCE, "of ... in") == "none"


@pytest.mark.parametrize(
    "evidence,expected",
    [
        ("2015-17 Amrutvahini College Of Engg", "year"),   # YYYY-YY range
        ("2012-15 Amrutvahini", "year"),
        ("M.Tech 2023", "year"),
        ("Graduated: May 2021", "month"),
        ("awarded 12 November 2019", "full"),
    ],
)
def test_hyphenated_year_ranges_are_read_as_year_precision(evidence, expected):
    """A "2015-17" range states no month, so the output must not imply one.
    This is what silently reinstated a fabricated "2017-01-01" when the quote
    behind it failed to ground."""
    from llm.postprocess import date_precision

    assert date_precision(evidence) == expected


def test_the_elided_quote_yields_year_precision_end_to_end():
    """The two fixes have to work together: the quote must ground so that
    precision can be derived from it at all."""
    from llm.confidence import grounding_quality
    from llm.postprocess import date_precision

    assert grounding_quality(BIBAVE_SOURCE, BIBAVE_QUOTE) != "none"
    assert date_precision(BIBAVE_QUOTE) == "year"


# --- what counts as a publication -------------------------------------------

def test_book_chapters_are_not_collapsed_into_paper_titles():
    """Abhishek's 13 = 9 journal/conference papers + 4 book chapters. The
    chapters are distinct works and dedup must leave them alone."""
    from llm.postprocess import deduplicate_titles

    titles = [f"A Journal Paper on Topic {i} in Computing" for i in range(9)] + [
        "Book Chapter on Machine Learning Foundations",
        "Book Chapter on Deep Learning Architectures",
        "Book Chapter on Natural Language Processing",
        "Book Chapter on Computer Vision Systems",
    ]
    kept, removed = deduplicate_titles(titles)
    assert removed == 0
    assert len(kept) == 13


def test_the_prompt_states_the_publication_inclusion_rule_explicitly():
    """The rule has to be written down, not inferred from how one candidate's
    resume happened to be laid out."""
    from pathlib import Path

    prompt = Path("llm/prompts/extraction.md").read_text(encoding="utf-8").lower()
    for included in ("journal articles", "conference papers", "book chapter"):
        assert included in prompt, f"prompt does not state that {included} count"
    for excluded in ("patent", "copyright registration", "membership", "attended"):
        assert excluded in prompt, f"prompt does not state that {excluded} is excluded"
