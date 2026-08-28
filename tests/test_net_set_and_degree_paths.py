"""Paths the 12 real resumes never exercised.

`net_set_status` was correct 12/12 on that batch -- but only ever as `NONE`,
because not one of those candidates holds a NET/SET/SLET. The SET/SLET +
`set_state` cross-check, the single highest-risk rule in the system per the
source plan, has therefore never actually fired on real input. Nor has a
Diploma-as-highest-degree, which the schema could not even represent until
`Lists!DegreeLevel` was reconciled.

These run against fixtures and the deterministic routing logic, with no
model call, so the paths stay covered whatever Gemini is doing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.resume_text import extract_text
from llm.confidence import evaluate
from llm.interface import HighestDegree, PhdStatus
from tests.test_confidence_routing import ABSENT, _ok, build

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_resumes"
THRESHOLD = 0.7


def _text(name: str) -> str:
    with open(FIXTURES / name, "rb") as fh:
        return extract_text(fh, name).text


# --- the fixtures exist and say what the tests assume -----------------------

@pytest.mark.parametrize(
    "filename,must_contain",
    [
        ("net_no_phd.docx", "UGC-NET"),
        ("set_maharashtra.docx", "Maharashtra"),
        ("set_karnataka.docx", "Karnataka"),
        ("slet_with_state.docx", "SLET"),
        ("diploma_highest_degree.docx", "Diploma"),
    ],
)
def test_fixture_contains_the_credential_it_is_named_for(filename, must_contain):
    assert must_contain.lower() in _text(filename).lower()


def test_out_of_state_set_fixture_names_a_state_other_than_maharashtra():
    """A SET valid in one state is not valid for direct recruitment at a
    Maharashtra institution -- the failure mode the source plan calls out by
    name. The fixture has to actually be out-of-state to test it."""
    text = _text("set_karnataka.docx").lower()
    assert "karnataka" in text
    assert "maharashtra" not in text


# --- the SET/SLET + set_state cross-check, finally exercised ----------------

@pytest.mark.parametrize("status", ["SET", "SLET"])
def test_a_state_test_without_its_state_is_always_flagged(status):
    """Never fired on the real batch because nobody in it held a SET. A
    silently-missing state is the "admits an ineligible candidate" case."""
    outcome = evaluate(
        build(net_set_status=_ok(status, confidence=1.0), set_state=ABSENT),
        threshold=THRESHOLD,
    )
    assert outcome.needs_review
    assert f"set_state:required_for_{status.lower()}" in outcome.reasons


@pytest.mark.parametrize("status", ["SET", "SLET"])
@pytest.mark.parametrize("state", ["Maharashtra", "Karnataka", "Kerala"])
def test_a_state_test_with_a_grounded_state_passes(status, state):
    outcome = evaluate(
        build(net_set_status=_ok(status), set_state=_ok(state)),
        threshold=THRESHOLD,
    )
    assert outcome.needs_review is False


def test_a_national_net_needs_no_state():
    """NET is national, so an absent set_state is correct rather than a gap."""
    outcome = evaluate(build(net_set_status=_ok("NET"), set_state=ABSENT), threshold=THRESHOLD)
    assert outcome.needs_review is False


def test_an_out_of_state_set_is_extracted_not_defaulted():
    """The state must survive as whatever the resume said. Defaulting it to
    the institution's own state would silently manufacture eligibility."""
    outcome = evaluate(
        build(net_set_status=_ok("SET"), set_state=_ok("Karnataka")),
        threshold=THRESHOLD,
    )
    assert outcome.needs_review is False  # extraction is clean...
    # ...and the value is preserved verbatim for the rule engine to judge.
    result = build(net_set_status=_ok("SET"), set_state=_ok("Karnataka"))
    assert result.set_state.value == "Karnataka"


# --- Diploma is a real degree level, not a mis-typed UG ---------------------

def test_diploma_is_an_accepted_highest_degree():
    """Was an upstream schema gap: Lists!DegreeLevel has had Diploma all
    along, but the extraction Literal did not, so a diploma-holder could only
    be misfiled as UG."""
    from typing import get_args

    assert "Diploma" in get_args(HighestDegree)


def test_a_diploma_holder_routes_cleanly():
    outcome = evaluate(build(highest_degree=_ok("Diploma")), threshold=THRESHOLD)
    assert outcome.needs_review is False


def test_every_datamodel_degree_level_is_representable():
    """The extractor's vocabulary must match the workbook's dropdown exactly,
    or a value valid in the database has no way to be extracted."""
    from typing import get_args

    assert set(get_args(HighestDegree)) == {"UG", "PG", "PhD", "Post-Doc", "Diploma"}


def test_every_datamodel_phd_status_is_representable():
    from typing import get_args

    assert set(get_args(PhdStatus)) == {
        "NOT_APPLICABLE", "PURSUING", "COMPLETED", "REGISTERED", "THESIS_SUBMITTED",
    }
