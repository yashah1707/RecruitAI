"""Retry/backoff, failure kinds, the lighter-model fallback and the scanned-PDF path.

No network and no real sleeping. GeminiProvider is driven through a stub
client that replays a script of errors and responses, with time.sleep patched
to record the waits instead of taking them.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pymupdf
import pytest
from google.genai import errors as genai_errors

from app.dashboard import process_resume
from app.resume_text import SCANNED_LABEL, ScannedPdfError, UnreadableResumeError, extract_text
from llm.confidence import evaluate
from llm.interface import ExtractionFailure
from llm.providers import gemini_provider as gp
from llm.providers.fake_provider import FakeProvider
from tests.test_gemini_provider import RESUME_TEXT, _payload

GOOD = SimpleNamespace(text=json.dumps(_payload()))
POOL = ["model-a", "model-b"]


def _503() -> Exception:
    return genai_errors.ServerError(
        503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
    )


def _429(text: str = "per-minute limit") -> Exception:
    return genai_errors.ClientError(
        429, {"error": {"code": 429, "message": text, "status": "RESOURCE_EXHAUSTED"}}
    )


def _http(code: int) -> Exception:
    return genai_errors.ClientError(code, {"error": {"code": code, "message": "nope", "status": "X"}})


class _Models:
    def __init__(self, script):
        self.script = list(script)
        self.models_called: list[str] = []

    def generate_content(self, *, model, **_kwargs):
        self.models_called.append(model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(gp.time, "sleep", lambda s: waits.append(s))
    return waits


def _provider(monkeypatch, script, **kwargs):
    monkeypatch.setattr(gp.config, "GEMINI_MODEL_POOL", POOL)
    provider = gp.GeminiProvider(model="auto", api_key="test-key", **kwargs)
    provider._client = SimpleNamespace(models=_Models(script))
    return provider


def _calls(provider) -> int:
    return len(provider._client.models.models_called)


# --- 503: backoff with jitter, six attempts --------------------------------


def test_503_is_retried_with_growing_jittered_backoff_then_succeeds(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_503(), _503(), _503(), GOOD])
    result = p.extract_fields(RESUME_TEXT)

    assert result.model_used in POOL
    assert _calls(p) == 4
    assert len(sleeps) == 3
    for attempt, wait in enumerate(sleeps, start=1):
        centre = gp._BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
        assert centre * gp._JITTER_LOW <= wait <= min(centre * gp._JITTER_HIGH, gp._MAX_BACKOFF_SECONDS)


def test_503_gets_six_attempts_on_each_model_then_fails_as_api_unavailable(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_503()] * 12)
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)

    assert exc.value.kind == "api_unavailable"
    assert "6 attempts" in str(exc.value)
    assert _calls(p) == 12  # six on each of the two pool models
    assert len(sleeps) == 10  # no sleep after the last attempt on a model


def test_backoff_is_never_longer_than_the_cap(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_503()] * 6)
    with pytest.raises(ExtractionFailure):
        p.extract_fields(RESUME_TEXT)
    assert max(sleeps) <= gp._MAX_BACKOFF_SECONDS


def test_an_overloaded_model_hands_over_to_the_next_one_in_the_pool(monkeypatch, sleeps):
    """Seen live on two days running: one model answered 503 for minutes while the other was serving."""
    p = _provider(monkeypatch, [_503()] * 6 + [GOOD])
    result = p.extract_fields(RESUME_TEXT)
    assert result.model_used == p.pool[1] and p._client.models.models_called == [p.pool[0]] * 6 + [p.pool[1]]
    assert p._exhausted == set()  # overloaded now is not out for the day: the next resume starts from the first model again


def test_every_model_overloaded_is_still_a_failure_after_each_had_its_turn(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_503()] * 12)
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)
    assert exc.value.kind == "api_unavailable" and _calls(p) == 12
    assert set(p._client.models.models_called) == set(p.pool)


def test_a_timeout_does_not_burn_through_the_rest_of_the_pool(monkeypatch, sleeps):
    p = _provider(monkeypatch, [TimeoutError("slow")] * 5)
    with pytest.raises(ExtractionFailure):
        p.extract_fields(RESUME_TEXT)
    assert set(p._client.models.models_called) == {p.pool[0]}


# --- 429 ---------------------------------------------------------------------


def test_429_fails_as_quota_after_five_attempts(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_429()] * 5)
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)
    assert exc.value.kind == "quota"
    assert _calls(p) == 5


def test_retry_after_header_is_honoured_and_never_undercut(monkeypatch, sleeps):
    err = _429()
    err.response = SimpleNamespace(headers={"Retry-After": "7"})
    p = _provider(monkeypatch, [err, GOOD])
    p.extract_fields(RESUME_TEXT)

    assert len(sleeps) == 1
    assert 7 <= sleeps[0] <= 7 * (1 + gp._SUGGESTED_JITTER_FRACTION)


def test_header_wins_over_the_hint_in_the_error_body(monkeypatch, sleeps):
    err = _429("Please retry in 40s.")
    err.response = SimpleNamespace(headers={"retry-after": "3"})
    p = _provider(monkeypatch, [err, GOOD])
    p.extract_fields(RESUME_TEXT)
    assert sleeps[0] < 5


def test_body_hint_is_used_when_there_is_no_header(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_429("Please retry in 12s."), GOOD])
    p.extract_fields(RESUME_TEXT)
    assert 12 <= sleeps[0] <= 12 * (1 + gp._SUGGESTED_JITTER_FRACTION)


def test_a_huge_retry_after_is_still_capped(monkeypatch, sleeps):
    err = _429()
    err.response = SimpleNamespace(headers={"Retry-After": "600"})
    p = _provider(monkeypatch, [err, GOOD])
    p.extract_fields(RESUME_TEXT)
    assert sleeps[0] == gp._MAX_BACKOFF_SECONDS


def test_daily_quota_moves_to_the_next_model_without_waiting(monkeypatch, sleeps):
    daily = _429("quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'")
    p = _provider(monkeypatch, [daily, GOOD])
    result = p.extract_fields(RESUME_TEXT)

    assert sleeps == []
    assert result.model_used == p.pool[1]


def test_every_model_out_of_daily_quota_is_a_quota_failure(monkeypatch, sleeps):
    daily = "quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'"
    p = _provider(monkeypatch, [_429(daily), _429(daily)])
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)
    assert exc.value.kind == "quota"
    assert sleeps == []


# --- malformed JSON, timeouts, config errors ------------------------------


def test_malformed_json_is_retried_immediately(monkeypatch, sleeps):
    p = _provider(monkeypatch, [SimpleNamespace(text="not json {"), SimpleNamespace(text=""), GOOD])
    result = p.extract_fields(RESUME_TEXT)
    assert result.model_used in POOL
    assert _calls(p) == 3
    assert sleeps == []


def test_persistent_malformed_json_gives_up_as_api_unavailable(monkeypatch, sleeps):
    p = _provider(monkeypatch, [SimpleNamespace(text="nope")] * 5)
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)
    assert exc.value.kind == "api_unavailable"


def test_timeouts_are_retried_with_backoff(monkeypatch, sleeps):
    p = _provider(monkeypatch, [TimeoutError("read timed out"), GOOD])
    p.extract_fields(RESUME_TEXT)
    assert len(sleeps) == 1


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_config_errors_fail_immediately_as_bad_config(monkeypatch, sleeps, code):
    p = _provider(monkeypatch, [_http(code)])
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)
    assert exc.value.kind == "bad_config"
    assert _calls(p) == 1
    assert sleeps == []


def test_missing_api_key_is_bad_config(monkeypatch):
    monkeypatch.setattr(gp.config, "GEMINI_API_KEY", "")
    with pytest.raises(ExtractionFailure) as exc:
        gp.GeminiProvider()
    assert exc.value.kind == "bad_config"


# --- lighter-model fallback: opt-in, flagged ------------------------------


def test_fallback_is_off_by_default(monkeypatch, sleeps):
    monkeypatch.setattr(gp.config, "GEMINI_LIGHTER_FALLBACK", False)
    p = _provider(monkeypatch, [_503()] * 12 + [GOOD])
    with pytest.raises(ExtractionFailure):
        p.extract_fields(RESUME_TEXT)
    assert p.fallback_model not in p._client.models.models_called


def test_fallback_when_enabled_succeeds_and_is_flagged_for_review(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_503()] * 12 + [GOOD], lighter_fallback=True, fallback_model="model-lite")
    result = p.extract_fields(RESUME_TEXT)

    assert p._client.models.models_called[-1] == "model-lite"
    assert result.model_used == "model-lite"
    assert result.lighter_model_fallback is True
    outcome = evaluate(result)
    assert outcome.needs_review is True
    assert "extraction_model:lighter_model_fallback" in outcome.reasons


def test_a_normal_result_is_not_flagged_as_fallback(monkeypatch, sleeps):
    p = _provider(monkeypatch, [GOOD], lighter_fallback=True)
    result = p.extract_fields(RESUME_TEXT)
    assert result.lighter_model_fallback is False
    assert "extraction_model:lighter_model_fallback" not in evaluate(result).reasons


def test_failed_fallback_reports_the_original_failure(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_503()] * 6 + [_503()] * 6, lighter_fallback=True, fallback_model="model-lite")
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)
    assert exc.value.kind == "api_unavailable"


def test_bad_config_never_falls_back(monkeypatch, sleeps):
    p = _provider(monkeypatch, [_http(401), GOOD], lighter_fallback=True)
    with pytest.raises(ExtractionFailure) as exc:
        p.extract_fields(RESUME_TEXT)
    assert exc.value.kind == "bad_config"
    assert _calls(p) == 1


# --- failure kinds reach the record, through FakeProvider -----------------


def _record_for_failure(failure: ExtractionFailure):
    docx_provider = FakeProvider(script=[failure])
    from tests.test_batch_processing import _docx_bytes

    return process_resume(docx_provider, "cv.docx", _docx_bytes("Cand")), docx_provider


@pytest.mark.parametrize("kind", ["api_unavailable", "quota", "bad_config"])
def test_extraction_failure_kind_is_carried_onto_the_record(kind):
    record, _ = _record_for_failure(ExtractionFailure("boom", kind=kind))
    assert record.failure_kind == kind
    assert record.result is None
    assert "boom" in record.parse_error


# --- scanned / image-only PDFs ---------------------------------------------


def _image_only_pdf() -> bytes:
    """A page with a drawn shape and no text layer, like a scan."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 300, 200), fill=(0.8, 0.8, 0.8))
    data = doc.tobytes()
    doc.close()
    return data


def test_scanned_pdf_is_labelled_not_just_failed():
    import io

    with pytest.raises(ScannedPdfError) as exc:
        extract_text(io.BytesIO(_image_only_pdf()), "scan.pdf")
    assert str(exc.value).startswith(SCANNED_LABEL)
    assert isinstance(exc.value, UnreadableResumeError)


def test_scanned_pdf_never_reaches_the_model():
    provider = FakeProvider()
    record = process_resume(provider, "scan.pdf", _image_only_pdf())

    assert provider.calls == 0
    assert record.result is None
    assert record.failure_kind == "unreadable"
    assert record.parse_error.startswith("scanned or image-only, review manually")


def test_scanned_pdf_row_is_routed_to_review_with_its_kind_in_the_export():
    from app.excel_writer import build_row

    record = process_resume(FakeProvider(), "scan.pdf", _image_only_pdf())
    row = build_row(record)
    assert row["failure_kind"] == "unreadable"
    assert row["needs_review"] is True
    assert row["parse_error"].startswith("scanned or image-only, review manually")


def test_a_corrupt_pdf_is_unreadable_but_not_called_scanned():
    import io

    with pytest.raises(UnreadableResumeError) as exc:
        extract_text(io.BytesIO(b"not a pdf at all"), "bad.pdf")
    assert not isinstance(exc.value, ScannedPdfError)
