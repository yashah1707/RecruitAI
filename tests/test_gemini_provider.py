"""Unit tests for GeminiProvider's pure response-processing logic.

No live API key or network call needed -- these test schema construction,
JSON-payload coercion, the grounding-verification post-processing step
(shared logic in llm.postprocess), and rate-limit error
detection. A real end-to-end call would need config.GEMINI_API_KEY set and
is out of scope for the automated suite.
"""

from __future__ import annotations

from datetime import date

from llm.interface import ExtractionResult
from llm.providers.gemini_provider import (
    GEMINI_EXTRACTION_SCHEMA,
    _is_non_retryable,
    _is_rate_limit_error,
    _to_extraction_result,
)


def test_schema_covers_every_extraction_field():
    from llm.interface import DETAIL_FIELDS, FIELD_NAMES

    assert set(GEMINI_EXTRACTION_SCHEMA.properties.keys()) == set(FIELD_NAMES) | set(DETAIL_FIELDS)


def test_schema_marks_non_optional_fields_non_nullable():
    # Non-Optional value types in the Pydantic schema: the model must always
    # supply a value for these.
    for name in ("highest_degree", "has_phd", "net_set_status", "publications_count"):
        value_schema = GEMINI_EXTRACTION_SCHEMA.properties[name].properties["value"]
        assert value_schema.nullable is not True, f"{name} should not be nullable"


def test_teaching_years_is_nullable_even_though_it_is_a_required_field():
    """Required means "must be reviewed if absent", not "must be invented".

    Measured against a human answer key, a non-nullable teaching_years_raw
    produced 3 of 5 total errors: with no way to say "not stated", the model
    emitted 0.0 / 8 / 8.3 for resumes that give no total anywhere.
    """
    from llm.interface import REQUIRED_FIELDS

    value_schema = GEMINI_EXTRACTION_SCHEMA.properties["teaching_years_raw"].properties["value"]
    assert value_schema.nullable is True
    assert "teaching_years_raw" in REQUIRED_FIELDS  # a null still routes to review


def test_schema_marks_optional_fields_nullable():
    for name in ("candidate_name", "marks_pct", "cgpa", "phd_award_date", "phd_regulation", "masters_award_date", "set_state", "study_leave_taken"):
        value_schema = GEMINI_EXTRACTION_SCHEMA.properties[name].properties["value"]
        assert value_schema.nullable is True, f"{name} should be nullable"


def _payload(**overrides) -> dict:
    base = {
        "candidate_name": {"value": "Jane Doe", "confidence": 0.9, "evidence": "Jane Doe"},
        "highest_degree": {"value": "PhD", "confidence": 0.9, "evidence": "PhD in Mathematics"},
        "marks_pct": {"value": None, "confidence": 0.0, "evidence": None},
        "cgpa": {"value": None, "confidence": 0.0, "evidence": None},
        "has_phd": {"value": True, "confidence": 0.9, "evidence": "PhD in Mathematics"},
        "phd_status": {"value": "COMPLETED", "confidence": 0.9, "evidence": "PhD in Mathematics"},
        "phd_award_date": {"value": "2020-01-01", "confidence": 0.8, "evidence": "awarded 2020"},
        "phd_regulation": {"value": None, "confidence": 0.0, "evidence": None},
        "masters_award_date": {"value": None, "confidence": 0.0, "evidence": None},
        "net_set_status": {"value": "NET", "confidence": 0.95, "evidence": "UGC-NET June 2015"},
        "set_state": {"value": None, "confidence": 0.0, "evidence": None},
        "study_leave_taken": {"value": None, "confidence": 0.0, "evidence": None},
        "teaching_years_raw": {"value": 5, "confidence": 0.8, "evidence": "5 years teaching"},
        "publications_count": {"value": 2, "confidence": 0.9, "evidence": "2 publications listed"},
        "publication_titles": {"value": ["Paper One", "Paper Two"], "confidence": 0.9, "evidence": "2 publications listed"},
        "publications_in_progress_count": {"value": 0, "confidence": 0.9, "evidence": None},
        "publications_in_progress_titles": {"value": [], "confidence": 0.9, "evidence": None},
    }
    base.update(overrides)
    return base


RESUME_TEXT = (
    "Jane Doe. PhD in Mathematics, awarded 2020. UGC-NET June 2015. "
    "5 years teaching. 2 publications listed."
)


def test_payload_coerces_to_extraction_result_with_correct_types():
    result = _to_extraction_result(_payload(), "{}")
    assert isinstance(result, ExtractionResult)
    assert result.phd_award_date.value == date(2020, 1, 1)
    assert result.has_phd.value is True
    assert result.teaching_years_raw.value == 5


def test_confidence_is_clamped_to_valid_range():
    payload = _payload(has_phd={"value": True, "confidence": 1.5, "evidence": "PhD in Mathematics"})
    result = _to_extraction_result(payload, "{}")
    assert result.has_phd.confidence == 1.0


def test_is_rate_limit_error_detects_429_status_code():
    class FakeError(Exception):
        code = 429

    assert _is_rate_limit_error(FakeError("quota exceeded")) is True


def test_is_rate_limit_error_detects_resource_exhausted_message():
    assert _is_rate_limit_error(Exception("RESOURCE_EXHAUSTED: quota")) is True


def test_is_rate_limit_error_false_for_unrelated_errors():
    assert _is_rate_limit_error(Exception("connection refused")) is False


def _error_with_code(code: int) -> Exception:
    class FakeError(Exception):
        pass

    exc = FakeError(f"HTTP {code}")
    exc.code = code
    return exc


def test_config_errors_are_not_retried():
    """A 404 (wrong model name) or 401/403 (bad key) is permanent -- retrying
    burns quota and buries the actual fix under 'failed after N attempts'."""
    for code in (400, 401, 403, 404):
        assert _is_non_retryable(_error_with_code(code)) is True


def test_transient_errors_are_still_retried():
    for code in (429, 500, 503):
        assert _is_non_retryable(_error_with_code(code)) is False


# --- daily model rotation & quota failover ---------------------------------

from datetime import date as _date  # noqa: E402

from llm.providers.gemini_provider import (  # noqa: E402
    _default_concurrency,
    _is_daily_quota_exhausted,
    resolve_model,
)

POOL = ["gemini-3.5-flash", "gemini-3.6-flash"]


def test_auto_alternates_model_between_consecutive_days():
    """Each free model has its own daily allowance; alternating uses both
    instead of draining one."""
    picks = [resolve_model("auto", POOL, _date(2026, 8, 20 + d)) for d in range(4)]
    assert picks[0] != picks[1]
    assert picks[0] == picks[2]  # cycles with the pool length
    assert set(picks) == set(POOL)


def test_auto_is_stable_within_the_same_day():
    day = _date(2026, 8, 23)
    assert resolve_model("auto", POOL, day) == resolve_model("auto", POOL, day)


def test_explicit_model_name_is_used_verbatim():
    assert resolve_model("gemini-3.5-flash-lite", POOL) == "gemini-3.5-flash-lite"


def test_auto_with_empty_pool_is_a_clear_error():
    import pytest

    from llm.interface import ExtractionFailure

    with pytest.raises(ExtractionFailure, match="GEMINI_MODEL_POOL"):
        resolve_model("auto", [])


def test_daily_quota_error_is_distinguished_from_per_minute_throttling():
    """A per-minute 429 clears in seconds; a daily one does not. Retrying the
    latter wastes a full backoff cycle on every remaining file."""
    per_day = Exception("quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'")
    per_minute = Exception("429 RESOURCE_EXHAUSTED quotaId: 'GenerateRequestsPerMinute'")
    assert _is_daily_quota_exhausted(per_day) is True
    assert _is_daily_quota_exhausted(per_minute) is False


def test_concurrency_matches_the_models_rate_limit():
    # Measured on a real free-tier key: flash 5 RPM, flash-lite 15 RPM.
    assert _default_concurrency("gemini-3.5-flash") == 2
    assert _default_concurrency("gemini-3.6-flash") == 2
    assert _default_concurrency("gemini-3.5-flash-lite") == 4


def test_todays_rotated_model_is_tried_first_with_others_as_fallback():
    """Rotation must drive the actual call order -- otherwise the pool would
    always start at the same model and rotation would be cosmetic."""
    import config
    from llm.providers.gemini_provider import GeminiProvider

    if not config.GEMINI_API_KEY:
        import pytest

        pytest.skip("needs GEMINI_API_KEY to construct the client")
    provider = GeminiProvider()
    assert provider.pool[0] == provider.model
    assert set(provider.pool) == set(config.GEMINI_MODEL_POOL)


def test_provider_is_fully_constructed_before_any_call():
    """Guards a real bug: an __init__ edit dropped the system-prompt load,
    and the resulting AttributeError surfaced only as a retry failure at
    call time, disguised as a network problem."""
    import config
    from llm.providers.gemini_provider import GeminiProvider

    if not config.GEMINI_API_KEY:
        import pytest

        pytest.skip("needs GEMINI_API_KEY to construct the client")
    p = GeminiProvider()
    for attr in ("model", "pool", "timeout", "max_concurrency", "_client", "_system_prompt"):
        assert hasattr(p, attr), f"GeminiProvider missing {attr}"
    assert p._system_prompt.strip(), "system prompt should not be empty"


def test_api_suggested_retry_delay_is_honoured():
    """Guessing with pure exponential backoff either waits too little (wasting
    an attempt against a 5 RPM limit) or too long. The API tells us; use it."""
    from llm.providers.gemini_provider import _retry_delay_seconds

    assert _retry_delay_seconds(Exception("'retryDelay': '20s'")) == 20.0
    assert _retry_delay_seconds(Exception("Please retry in 45.6s.")) == 45.6
    assert _retry_delay_seconds(Exception("'retryDelay': '0s'")) == 0.0


def test_millisecond_retry_delay_is_converted_to_seconds():
    from llm.providers.gemini_provider import _retry_delay_seconds

    got = _retry_delay_seconds(Exception("Please retry in 627.45ms"))
    assert got is not None and abs(got - 0.62745) < 1e-6


def test_missing_retry_delay_falls_back_to_exponential_backoff():
    from llm.providers.gemini_provider import _retry_delay_seconds

    assert _retry_delay_seconds(Exception("429 rate limited, no hint given")) is None


def test_backoff_is_capped_so_a_batch_never_appears_hung():
    from llm.providers.gemini_provider import _MAX_BACKOFF_SECONDS

    assert _MAX_BACKOFF_SECONDS <= 60
