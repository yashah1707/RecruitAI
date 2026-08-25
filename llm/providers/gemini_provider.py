"""Cloud extraction via the Gemini API.

Same LLMProvider interface as OllamaProvider, same post-processing pipeline
(grounding verification via llm.confidence.is_grounded) — the only
differences are the transport and the schema dialect Gemini's structured
output expects (an OpenAPI-subset "Schema", not raw JSON Schema).

PII note (MVP brief section 1.6 / LLM Layer plan section 8): Google's free
tier permits using submitted prompts for model training. This provider does
not block real candidate data — that's a decision only the person running it
can make — but it logs a prominent warning on every call so that choice is
never silent.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import config
from llm.interface import ExtractionFailure, ExtractionResult, FieldWithConfidence
from llm.postprocess import log_stats, sanitize

logger = logging.getLogger("recruitai.gemini_provider")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extraction.md"

_MAX_RETRIES = 5
_BACKOFF_BASE_SECONDS = 2.0
# Never sleep longer than this on one attempt, however long the API asks for
# -- a batch that appears hung is worse than a row routed to review.
_MAX_BACKOFF_SECONDS = 45.0

# Gemini reports how long to wait, either as a structured retryDelay or in
# the prose message. Honouring it beats guessing with pure exponential
# backoff, which either waits too little (wasting an attempt) or too long.
_RETRY_DELAY_RE = re.compile(
    r"""'retryDelay':\s*'(?P<d_s>\d+(?:\.\d+)?)s'      # structured field
      | retry\s+in\s+(?P<p_ms>\d+(?:\.\d+)?)ms          # prose, milliseconds
      | retry\s+in\s+(?P<p_s>\d+(?:\.\d+)?)s            # prose, seconds""",
    re.VERBOSE,
)


def _retry_delay_seconds(exc: Exception) -> float | None:
    """How long the API asked us to wait, in seconds, or None if it didn't."""
    m = _RETRY_DELAY_RE.search(str(exc))
    if not m:
        return None
    if m.group("p_ms") is not None:
        return float(m.group("p_ms")) / 1000
    raw = m.group("d_s") or m.group("p_s")
    return float(raw) if raw is not None else None


def _field_schema(value_type: str, *, nullable: bool, enum: list[str] | None = None) -> dict:
    value_schema: dict[str, Any] = {"type": value_type}
    if nullable:
        value_schema["nullable"] = True
    if enum:
        value_schema["enum"] = enum
    return {
        "type": "OBJECT",
        "properties": {
            "value": value_schema,
            "confidence": {"type": "NUMBER"},
            "evidence": {"type": "STRING", "nullable": True},
        },
        "required": ["value", "confidence", "evidence"],
    }


# Gemini's structured-output schema is an OpenAPI 3.0 subset (uppercase
# type names, a `nullable` flag) rather than raw JSON Schema, so this can't
# be shared verbatim with ollama_provider.EXTRACTION_JSON_SCHEMA even though
# it describes the same fields.
GEMINI_EXTRACTION_SCHEMA = types.Schema(
    type="OBJECT",
    properties={
        "candidate_name": _field_schema("STRING", nullable=True),
        "highest_degree": _field_schema("STRING", nullable=False, enum=["UG", "PG", "PhD", "Post-Doc"]),
        "marks_pct": _field_schema("NUMBER", nullable=True),
        "has_phd": _field_schema("BOOLEAN", nullable=False),
        "phd_award_date": _field_schema("STRING", nullable=True),
        "phd_regulation": _field_schema("STRING", nullable=True, enum=["2009", "2016"]),
        "masters_award_date": _field_schema("STRING", nullable=True),
        "net_set_status": _field_schema("STRING", nullable=False, enum=["NET", "SET", "SLET", "NONE"]),
        "set_state": _field_schema("STRING", nullable=True),
        "study_leave_taken": _field_schema("BOOLEAN", nullable=True),
        "teaching_years_raw": _field_schema("NUMBER", nullable=True),
        "publications_count": _field_schema("INTEGER", nullable=False),
    },
    required=[
        "candidate_name", "highest_degree", "marks_pct", "has_phd", "phd_award_date",
        "phd_regulation", "masters_award_date", "net_set_status", "set_state",
        "study_leave_taken", "teaching_years_raw", "publications_count",
    ],
)

_DATE_FIELDS = frozenset({"phd_award_date", "masters_award_date"})


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _coerce_field(name: str, payload: dict[str, Any]) -> FieldWithConfidence:
    value = payload.get("value")
    if name in _DATE_FIELDS and isinstance(value, str):
        value = _parse_date(value)
    confidence = payload.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return FieldWithConfidence(value=value, confidence=confidence, evidence=payload.get("evidence"))


def _to_extraction_result(payload: dict[str, Any], raw_text: str) -> ExtractionResult:
    fields = {name: _coerce_field(name, payload[name]) for name in GEMINI_EXTRACTION_SCHEMA.properties}
    return ExtractionResult(**fields, raw_llm_output=raw_text)


def _error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _is_rate_limit_error(exc: Exception) -> bool:
    if _error_code(exc) == 429:
        return True
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower()


def _is_daily_quota_exhausted(exc: Exception) -> bool:
    """Distinguish "too fast, slow down" from "you're out for the day".

    A per-minute 429 clears in seconds and is worth retrying. A daily-quota
    429 will not clear until tomorrow, so retrying it just burns time on
    every remaining file in the batch and buries the real message.
    """
    return "PerDay" in str(exc)


# Retrying these just burns time and quota: they mean the request itself is
# wrong (bad model name, bad/unauthorized key), not that the service was
# briefly unavailable. Fail immediately with the API's own message so the
# fix is obvious instead of buried under "failed after N attempts".
_NON_RETRYABLE_CODES = frozenset({400, 401, 403, 404})


def _is_non_retryable(exc: Exception) -> bool:
    return _error_code(exc) in _NON_RETRYABLE_CODES


def resolve_model(configured: str, pool: list[str], today: date | None = None) -> str:
    """Turn the configured model setting into a concrete model name.

    "auto" alternates across the pool by day-of-year, so consecutive days use
    different models and each one's separate daily quota gets used instead of
    hammering a single model until it runs dry. Anything else is taken
    literally.
    """
    if configured.lower() != "auto":
        return configured
    if not pool:
        raise ExtractionFailure("GEMINI_MODEL is 'auto' but GEMINI_MODEL_POOL is empty.")
    day = (today or date.today()).toordinal()
    return pool[day % len(pool)]


def _default_concurrency(model: str) -> int:
    """Pick a safe in-flight count from the model's free-tier RPM allowance.

    Free-tier limits observed on a real key: flash models get 5 requests per
    minute, flash-lite gets 15. Staying under the limit is worth more than
    raw parallelism here — a 429 costs a full backoff cycle, which is slower
    than just having queued the request.
    """
    return 4 if "lite" in model.lower() else 2


class GeminiProvider:
    """Talks to the Gemini API. Requires config.GEMINI_API_KEY."""

    name = "gemini"
    # Set per-model in __init__: the free tier's per-minute allowance differs
    # sharply between model families (measured on a real key: 5 RPM for
    # flash, 15 RPM for flash-lite), and exceeding it costs a retry cycle on
    # every file in the batch.
    max_concurrency = 2

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float | None = None) -> None:
        key = api_key or config.GEMINI_API_KEY
        if not key:
            raise ExtractionFailure(
                "GEMINI_API_KEY is not set. Get a key at https://aistudio.google.com/apikey "
                "and set the GEMINI_API_KEY environment variable."
            )
        configured = model or config.GEMINI_MODEL
        self.model = resolve_model(configured, config.GEMINI_MODEL_POOL)
        # Order the pool so today's rotated pick is tried first and the others
        # act as fallbacks -- otherwise rotation would have no effect on which
        # model actually serves the requests.
        if configured.lower() == "auto":
            pool = list(config.GEMINI_MODEL_POOL)
            start = pool.index(self.model)
            self.pool = pool[start:] + pool[:start]
        else:
            self.pool = [configured]
        self.timeout = timeout or config.GEMINI_TIMEOUT_SECONDS
        self.max_concurrency = config.GEMINI_MAX_CONCURRENCY or _default_concurrency(self.model)
        # Models whose daily quota is known to be gone. Shared across threads
        # so one worker's discovery spares the rest of the batch from
        # rediscovering it file by file.
        self._exhausted: set[str] = set()
        self._lock = threading.Lock()
        self._client = genai.Client(api_key=key)
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def _next_usable_model(self) -> str | None:
        """A pool model that hasn't hit its daily cap yet, or None."""
        with self._lock:
            return next((m for m in self.pool if m not in self._exhausted), None)

    def _mark_exhausted(self, model: str) -> None:
        with self._lock:
            self._exhausted.add(model)
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def _call_once(self, resume_text: str, model: str) -> ExtractionResult:
        # PII note: this is the one line in the whole pipeline where real
        # candidate text leaves the local machine. Logged, never silent.
        logger.warning(
            "sending_resume_to_gemini_free_tier chars=%d model=%s "
            "(free-tier prompts may be used by Google for model training)",
            len(resume_text),
            model,
        )
        response = self._client.models.generate_content(
            model=model,
            contents=resume_text,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                response_mime_type="application/json",
                response_schema=GEMINI_EXTRACTION_SCHEMA,
                temperature=0,
                http_options=types.HttpOptions(timeout=int(self.timeout * 1000)),
            ),
        )
        raw_text = response.text or ""
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning("malformed_json_from_gemini length=%d", len(raw_text))
            raise ExtractionFailure("model returned malformed JSON") from exc
        result = _to_extraction_result(payload, raw_text)
        result, stats = sanitize(result, resume_text)
        log_stats(stats)
        return result

    def extract_fields(self, resume_text: str) -> ExtractionResult:
        """Extract one resume, falling over to the next pool model on quota.

        Each free-tier model has its own separate daily allowance, so when
        one runs out the batch keeps going on the next rather than failing
        every remaining file.
        """
        last_error: Exception | None = None

        while True:
            model = self._next_usable_model()
            if model is None:
                raise ExtractionFailure(
                    "Gemini daily quota is exhausted for every model in "
                    f"GEMINI_MODEL_POOL ({', '.join(self.pool)}). This will not "
                    "clear until the quota resets. Wait for the reset, enable "
                    "billing for higher limits, switch GEMINI_MODEL to a "
                    "'-lite' model (much larger daily allowance, lower "
                    "accuracy), or set LLM_PROVIDER=ollama to run locally "
                    f"with no quota at all. Last error: {last_error}"
                )

            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    return self._call_once(resume_text, model)
                except ExtractionFailure as exc:
                    # Malformed JSON despite schema constraints -- retry, since
                    # Gemini's sampling can produce valid JSON on a later attempt
                    # even at temperature 0 (matches OllamaProvider's behavior).
                    last_error = exc
                    logger.warning("gemini_extraction_failure attempt=%d", attempt)
                except genai_errors.APIError as exc:
                    last_error = exc
                    if _is_non_retryable(exc):
                        logger.warning("gemini_config_error code=%s", _error_code(exc))
                        raise ExtractionFailure(
                            f"Gemini rejected the request (HTTP {_error_code(exc)}): {exc}. "
                            "This is a configuration problem (model name or API key), "
                            "not a transient failure — check GEMINI_MODEL and GEMINI_API_KEY."
                        ) from exc
                    if _is_daily_quota_exhausted(exc):
                        # Out for the day on this model; take it out of
                        # rotation and try the next one immediately.
                        logger.warning("gemini_daily_quota_exhausted model=%s", model)
                        self._mark_exhausted(model)
                        break
                    if _is_rate_limit_error(exc):
                        suggested = _retry_delay_seconds(exc)
                        backoff = min(
                            suggested if suggested is not None else _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                            _MAX_BACKOFF_SECONDS,
                        )
                        logger.warning(
                            "gemini_rate_limited attempt=%d backoff_s=%.1f source=%s",
                            attempt, backoff, "api" if suggested is not None else "exponential",
                        )
                        time.sleep(backoff)
                    else:
                        logger.warning("gemini_api_error attempt=%d", attempt)
                except Exception as exc:  # network/timeout errors from the SDK
                    last_error = exc
                    logger.warning("gemini_request_error attempt=%d", attempt)
            else:
                # Retries exhausted for a non-quota reason -- don't silently
                # burn the rest of the pool on what is probably the same fault.
                raise ExtractionFailure(
                    f"extraction failed after {_MAX_RETRIES} attempts: {last_error}"
                )
