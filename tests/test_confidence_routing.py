"""Routing rules from section 6, tested with no model running."""

from __future__ import annotations

from datetime import date

import pytest

from llm.confidence import evaluate, is_grounded, is_unverified
from llm.interface import ExtractionResult, FieldWithConfidence
from llm.providers.fake_provider import CANNED_RESULTS

THRESHOLD = 0.7


def _ok(value, confidence=0.95, evidence="quoted from the resume"):
    return {"value": value, "confidence": confidence, "evidence": evidence}


ABSENT = {"value": None, "confidence": 0.0, "evidence": None}


def build(**overrides) -> ExtractionResult:
    """A fully grounded, review-free baseline result; override one field per test."""
    base = dict(
        candidate_name=_ok("D. Synthetic Candidate"),
        highest_degree=_ok("PG"),
        marks_pct=_ok(70.0),
        cgpa=ABSENT,
        has_phd=_ok(False),
        phd_status=_ok("NOT_APPLICABLE"),
        phd_award_date=ABSENT,
        phd_regulation=ABSENT,
        masters_award_date=_ok(date(2012, 5, 1)),
        net_set_status=_ok("NET"),
        set_state=ABSENT,
        study_leave_taken=ABSENT,
        teaching_years_raw=_ok(5.0),
        publications_count=_ok(2),
        publication_titles=_ok(["Paper One", "Paper Two"]),
        publications_in_progress_count=_ok(0),
        publications_in_progress_titles=_ok([]),
        raw_llm_output="{}",
    )
    base.update(overrides)
    return ExtractionResult(**base)


def test_fully_grounded_result_does_not_need_review():
    assert evaluate(build(), threshold=THRESHOLD).needs_review is False


def test_absent_optional_fields_alone_do_not_trigger_review():
    """'Never guess' produces confidence 0.0 absences; those must not flag every row."""
    result = build(
        marks_pct=ABSENT,
        masters_award_date=ABSENT,
        study_leave_taken=ABSENT,
        phd_regulation=ABSENT,
    )
    assert evaluate(result, threshold=THRESHOLD).needs_review is False


def test_low_confidence_required_field_triggers_review():
    outcome = evaluate(build(teaching_years_raw=_ok(5.0, confidence=0.4)), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "teaching_years_raw:low_confidence" in outcome.reasons


def test_required_field_confidence_exactly_at_threshold_is_accepted():
    outcome = evaluate(build(publications_count=_ok(2, confidence=THRESHOLD)), threshold=THRESHOLD)
    assert outcome.needs_review is False


def test_claimed_value_without_evidence_triggers_review():
    outcome = evaluate(build(highest_degree=_ok("PhD", 0.99, None)), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "highest_degree:no_evidence" in outcome.reasons


def test_claimed_optional_value_without_evidence_triggers_review():
    outcome = evaluate(build(marks_pct=_ok(81.0, 0.99, None)), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "marks_pct:no_evidence" in outcome.reasons


def test_low_confidence_optional_value_triggers_review():
    outcome = evaluate(build(study_leave_taken=_ok(True, 0.3)), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "study_leave_taken:low_confidence" in outcome.reasons


@pytest.mark.parametrize("status", ["SET", "SLET"])
def test_set_or_slet_without_state_always_needs_review(status):
    """Even a maximally confident SET must not pass without a grounded state."""
    outcome = evaluate(
        build(net_set_status=_ok(status, confidence=1.0), set_state=ABSENT),
        threshold=THRESHOLD,
    )
    assert outcome.needs_review
    assert f"set_state:required_for_{status.lower()}" in outcome.reasons


@pytest.mark.parametrize("status", ["SET", "SLET"])
def test_set_with_low_confidence_state_needs_review(status):
    outcome = evaluate(
        build(net_set_status=_ok(status), set_state=_ok("Maharashtra", confidence=0.4)),
        threshold=THRESHOLD,
    )
    assert outcome.needs_review


@pytest.mark.parametrize("status", ["SET", "SLET"])
def test_set_with_grounded_state_does_not_need_review(status):
    outcome = evaluate(
        build(net_set_status=_ok(status), set_state=_ok("Maharashtra")),
        threshold=THRESHOLD,
    )
    assert outcome.needs_review is False


def test_set_state_missing_but_status_net_is_fine():
    assert evaluate(build(net_set_status=_ok("NET"), set_state=ABSENT), threshold=THRESHOLD).needs_review is False


def test_parse_failure_still_produces_an_outcome_not_an_exception():
    outcome = evaluate(None, parse_error="no extractable text", threshold=THRESHOLD)
    assert outcome.needs_review
    assert "parse_error" in outcome.reasons
    assert "no_extraction" in outcome.reasons


def test_reasons_never_contain_extracted_values():
    """Reasons get logged; they must carry field names and rule names only."""
    outcome = evaluate(
        build(
            candidate_name=_ok("E. Synthetic Candidate", 0.2),
            set_state=_ok("Maharashtra", 0.1),
            net_set_status=_ok("SET"),
        ),
        threshold=THRESHOLD,
    )
    joined = " ".join(outcome.reasons)
    assert "Synthetic" not in joined
    assert "Maharashtra" not in joined


def test_is_unverified_ignores_absent_fields():
    assert is_unverified(FieldWithConfidence[str | None](value=None, confidence=0.0), THRESHOLD) is False
    assert is_unverified(FieldWithConfidence[str | None](value="x", confidence=0.1), THRESHOLD) is True


def test_canned_fake_results_cover_both_routing_outcomes():
    outcomes = [evaluate(r, threshold=THRESHOLD).needs_review for r in CANNED_RESULTS]
    assert True in outcomes and False in outcomes


RESUME_TEXT = "Jane Doe. M.Sc. Physics 2015. UGC-NET June 2016. 5 years teaching experience."


def test_is_grounded_true_for_real_substring():
    assert is_grounded(RESUME_TEXT, "UGC-NET June 2016") is True


def test_is_grounded_tolerates_whitespace_and_case_differences():
    # PDF extraction can collapse or add whitespace; that must not cause a
    # real quote to be treated as ungrounded.
    assert is_grounded(RESUME_TEXT, "ugc-net   june 2016") is True


def test_is_grounded_false_for_paraphrase_not_actually_in_text():
    """The real-data bug this guards against: models sometimes describe the
    document ("the header lists 'PhD'") instead of quoting it. That must not
    pass just because it sounds plausible."""
    assert is_grounded(RESUME_TEXT, "The document lists NET status in the header section") is False


def test_is_grounded_false_for_evidence_from_a_different_document():
    # The exact contamination bug found on real data: evidence that is
    # verbatim-real text, just not from *this* candidate's resume.
    assert is_grounded(RESUME_TEXT, "UGC-NET (Mathematical Sciences), June 2012") is False


def test_is_grounded_false_for_empty_or_none():
    assert is_grounded(RESUME_TEXT, "") is False
    assert is_grounded(RESUME_TEXT, "   ") is False
    assert is_grounded(RESUME_TEXT, None) is False


def test_is_grounded_tolerates_pdf_punctuation_extraction_artifacts():
    """Real-data bug: pdfplumber sometimes decodes a PDF's curly quotes as a
    U+FFFD replacement character. A model that reproduces the correct quote
    mark for text it read correctly must not be penalized for not also
    reproducing the source PDF's own extraction corruption."""
    resume_text = 'Published in IJIRST on �Advance Security System�, March-2016.'
    evidence = 'Published in IJIRST on “Advance Security System”, March-2016.'
    assert is_grounded(resume_text, evidence) is True


def test_is_grounded_false_for_literal_null_string():
    """Some models emit the text "null" instead of a real JSON null for
    evidence -- that must be treated as no evidence, not as a quote."""
    assert is_grounded(RESUME_TEXT, "null") is False
    assert is_grounded(RESUME_TEXT, "None") is False
    assert is_grounded(RESUME_TEXT, "Not Applicable") is False


def test_is_grounded_still_rejects_fabrication_after_punctuation_normalization():
    # Guards against the punctuation-tolerance fix accidentally making the
    # check too loose to catch genuine hallucination.
    assert is_grounded(RESUME_TEXT, "The document, clearly, lists NET-status!") is False


def test_net_set_status_none_does_not_require_evidence():
    """A candidate with no NET/SET has nothing to quote — demanding a
    verbatim quote proving an absence is unsatisfiable and would flag every
    row, destroying the signal value of needs_review."""
    outcome = evaluate(build(net_set_status=_ok("NONE", 0.95, None)), threshold=THRESHOLD)
    assert outcome.needs_review is False


def test_has_phd_false_does_not_require_evidence():
    outcome = evaluate(build(has_phd=_ok(False, 0.95, None)), threshold=THRESHOLD)
    assert outcome.needs_review is False


def test_absence_claims_are_still_confidence_checked():
    """Not evidence-checked is not the same as unchecked -- a low-confidence
    absence claim must still route to review."""
    outcome = evaluate(build(net_set_status=_ok("NONE", 0.3, None)), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "net_set_status:low_confidence" in outcome.reasons


def test_positive_claims_still_require_evidence():
    """The absence exemption must not leak into positive claims."""
    outcome = evaluate(build(net_set_status=_ok("NET", 0.95, None)), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "net_set_status:no_evidence" in outcome.reasons
    outcome = evaluate(build(has_phd=_ok(True, 0.95, None)), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "has_phd:no_evidence" in outcome.reasons


def test_absent_teaching_years_routes_to_review_rather_than_passing_silently():
    """Nullable so the model can admit it doesn't know -- but "don't know"
    on a required field still needs a human, it isn't a free pass."""
    outcome = evaluate(build(teaching_years_raw=ABSENT), threshold=THRESHOLD)
    assert outcome.needs_review
    assert "teaching_years_raw:missing_required" in outcome.reasons
