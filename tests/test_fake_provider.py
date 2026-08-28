"""FakeProvider must stay a complete stand-in for the real thing.

With Gemini now the only real provider, this is the whole reason the test
suite can run at all without a network. The Round 2 `504 DEADLINE_EXCEEDED`
failures are the concrete argument: a live dependency in the test loop means
an outage or an exhausted daily quota fails the build for reasons that have
nothing to do with the code under test.

The risk this guards is silent drift -- a field gets added to
ExtractionResult, the real provider learns to return it, and FakeProvider
quietly doesn't, so every test using it exercises a shape the real system
never produces.
"""

from __future__ import annotations

import socket

import pytest

from llm.interface import FIELD_NAMES, ExtractionResult, LLMProvider
from llm.providers.fake_provider import CANNED_RESULTS, FakeProvider


def test_fake_provider_satisfies_the_provider_protocol():
    assert isinstance(FakeProvider(), LLMProvider)


def test_fake_provider_returns_a_real_extraction_result():
    result = FakeProvider().extract_fields("any resume text")
    assert isinstance(result, ExtractionResult)


def test_every_schema_field_is_populated_by_the_fake():
    """Catches the drift case: a new field added to the schema and to Gemini,
    but never taught to the fake."""
    result = FakeProvider().extract_fields("any resume text")
    for name in FIELD_NAMES:
        field = getattr(result, name, None)
        assert field is not None, f"FakeProvider does not populate {name}"
        assert hasattr(field, "value") and hasattr(field, "confidence")


def test_canned_results_cover_the_routing_outcomes_that_matter():
    from llm.confidence import evaluate

    outcomes = [evaluate(r) for r in CANNED_RESULTS]
    assert any(o.needs_review for o in outcomes), "no canned result exercises review routing"
    assert any(not o.needs_review for o in outcomes), "no canned result passes cleanly"


def test_canned_results_include_an_awarded_and_a_non_awarded_phd():
    statuses = {r.phd_status.value for r in CANNED_RESULTS}
    assert "COMPLETED" in statuses
    assert statuses - {"COMPLETED"}, "every canned result claims a completed PhD"


def test_results_are_independent_copies():
    """Callers mutate results (post-processing rewrites fields). A shared
    object would let one test's mutation leak into the next."""
    p = FakeProvider()
    first, second = p.extract_fields("a"), p.extract_fields("b")
    first.publications_count.value = 999
    assert second.publications_count.value != 999
    assert p.extract_fields("c").publications_count.value != 999


def test_the_fake_makes_no_network_connection(monkeypatch):
    """The point of the fake. Any socket use here means the suite has a live
    dependency it doesn't know about."""

    def explode(*args, **kwargs):
        raise AssertionError("FakeProvider attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    result = FakeProvider().extract_fields("resume text")
    assert result.candidate_name.value


def test_importing_the_fake_does_not_require_a_gemini_key(monkeypatch):
    """LLM_PROVIDER=fake must work with no credentials configured at all."""
    monkeypatch.setattr("config.GEMINI_API_KEY", "")
    import importlib

    import llm.providers.fake_provider as fp

    importlib.reload(fp)
    assert fp.FakeProvider().extract_fields("text") is not None
