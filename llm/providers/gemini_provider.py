"""Cloud extraction via the Gemini API.

The only real provider. Structured output is forced through Gemini's
`response_schema`, so the model cannot return free-form prose, and every
response goes through the same deterministic post-processing in
llm.postprocess as any other source of extracted fields.

PII note (MVP brief section 1.6 / LLM Layer plan section 8): Google's free
tier permits using submitted prompts for model training. This provider does
not block real candidate data — that's a decision only the person running it
can make — but it logs a prominent warning on every call so that choice is
never silent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
from email.utils import parsedate_to_datetime
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, get_args

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import config
from llm.interface import (
    FIELD_NAMES,
    AchievementEntry,
    AchievementKind,
    ExperienceEntry,
    ExperienceKind,
    GuidanceEntry,
    GuidanceLevel,
    EducationEntry,
    EducationLevel,
    EventEntry,
    EventKind,
    EventRole,
    PublicationEntry,
    PublicationKind,
    PublicationStatus,
    ExtractionFailure,
    ExtractionResult,
    FieldWithConfidence,
    HighestDegree,
    PhdStatus,
)
from llm.postprocess import log_stats, sanitize

logger = logging.getLogger("recruitai.gemini_provider")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extraction.md"

_MAX_RETRIES = 5
# 503 "high demand" spikes are the failure that most often clears on its own
# given a little longer, so it gets one more attempt than anything else.
_MAX_RETRIES_UNAVAILABLE = 6
_BACKOFF_BASE_SECONDS = 3.0
# Never sleep longer than this on one attempt, however long the API asks for
# -- a batch that appears hung is worse than a row routed to review.
_MAX_BACKOFF_SECONDS = 45.0
# Spread of the multiplier applied to exponential backoff. Without jitter,
# every worker that failed together retries together and fails together again.
_JITTER_LOW, _JITTER_HIGH = 0.5, 1.5
# When the API names a wait, never go earlier than it asked; add only a little
# on top so workers released by the same hint don't all land on one instant.
_SUGGESTED_JITTER_FRACTION = 0.25

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


def _retry_after_header_seconds(exc: Exception) -> float | None:
    """The HTTP Retry-After header (seconds or HTTP-date), if the error has one."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:  # an exotic headers object must not mask the real error
        return None
    if raw is None:
        return None
    raw = str(raw).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _suggested_wait_seconds(exc: Exception) -> float | None:
    """What the API asked us to wait: the header wins, then the body hint."""
    header = _retry_after_header_seconds(exc)
    return header if header is not None else _retry_delay_seconds(exc)


def _backoff_seconds(attempt: int, suggested: float | None) -> float:
    """How long to sleep before attempt number `attempt + 1`.

    A wait the API asked for is a floor (plus a sliver of jitter); with no
    hint, exponential backoff with jitter. Both are capped so one bad stretch
    cannot stall the whole batch.
    """
    if suggested is not None:
        wait = suggested * (1 + random.uniform(0, _SUGGESTED_JITTER_FRACTION))
    else:
        wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) * random.uniform(_JITTER_LOW, _JITTER_HIGH)
    return min(wait, _MAX_BACKOFF_SECONDS)


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


def _publication_titles_schema() -> dict:
    """A list-valued field still needs the value/confidence/evidence shape."""
    return {
        "type": "OBJECT",
        "properties": {
            "value": {"type": "ARRAY", "nullable": True, "items": {"type": "STRING"}},
            "confidence": {"type": "NUMBER"},
            "evidence": {"type": "STRING", "nullable": True},
        },
        "required": ["value", "confidence", "evidence"],
    }


def _str(*, nullable: bool = True, enum: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "STRING"}
    if nullable:
        schema["nullable"] = True
    if enum:
        schema["enum"] = enum
    return schema


def _list_of(properties: dict[str, dict], required: list[str]) -> dict:
    return {"type": "ARRAY", "items": {"type": "OBJECT", "properties": properties, "required": required}}


# Gemini's structured-output schema is an OpenAPI 3.0 subset -- uppercase
# type names and a `nullable` flag -- not plain JSON Schema, so it is written
# out longhand here rather than derived from the Pydantic model.
GEMINI_EXTRACTION_SCHEMA = types.Schema(
    type="OBJECT",
    properties={
        "candidate_name": _field_schema("STRING", nullable=True),
        "highest_degree": _field_schema("STRING", nullable=False, enum=list(get_args(HighestDegree))),
        "marks_pct": _field_schema("NUMBER", nullable=True),
        "cgpa": _field_schema("NUMBER", nullable=True),
        "has_phd": _field_schema("BOOLEAN", nullable=False),
        "phd_status": _field_schema("STRING", nullable=False, enum=list(get_args(PhdStatus))),
        "phd_award_date": _field_schema("STRING", nullable=True),
        "phd_regulation": _field_schema("STRING", nullable=True, enum=["2009", "2016"]),
        "masters_award_date": _field_schema("STRING", nullable=True),
        "net_set_status": _field_schema("STRING", nullable=False, enum=["NET", "SET", "SLET", "NONE"]),
        "set_state": _field_schema("STRING", nullable=True),
        "study_leave_taken": _field_schema("BOOLEAN", nullable=True),
        "teaching_years_raw": _field_schema("NUMBER", nullable=True),
        "publications_count": _field_schema("INTEGER", nullable=False),
        "publication_titles": _publication_titles_schema(),
        "publications_in_progress_count": _field_schema("INTEGER", nullable=False),
        "publications_in_progress_titles": _publication_titles_schema(),
        # Detail records: plain lists, no per-item confidence. Each item is
        # checked against the resume text in llm.postprocess instead.
        "education": _list_of(
            {
                "level": _str(enum=list(get_args(EducationLevel)), nullable=False),
                "degree": _str(), "course": _str(), "college": _str(), "university": _str(),
                "marks_pct": {"type": "NUMBER", "nullable": True},
                "cgpa": {"type": "NUMBER", "nullable": True},
                "division": _str(), "completion": _str(),
                "thesis_title": _str(), "guide": _str(), "registration": _str(),
            },
            required=["level"],
        ),
        "publications": _list_of(
            {
                "title": _str(nullable=False),
                "kind": _str(enum=list(get_args(PublicationKind)), nullable=False),
                "venue": _str(), "year": _str(),
                "status": _str(enum=list(get_args(PublicationStatus)), nullable=False),
                "indexing": _str(),
            },
            required=["title", "kind", "status"],
        ),
        "events": _list_of(
            {
                "kind": _str(enum=list(get_args(EventKind)), nullable=False),
                "title": _str(nullable=False),
                "role": _str(enum=list(get_args(EventRole)), nullable=False),
                "organiser": _str(), "duration": _str(), "year": _str(),
            },
            required=["kind", "title", "role"],
        ),
        "subjects_taught": {"type": "ARRAY", "items": {"type": "STRING"}},
        "skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "experience": _list_of(
            {
                "designation": _str(), "institution": _str(),
                "kind": _str(enum=list(get_args(ExperienceKind)), nullable=False),
                "start": _str(), "end": _str(), "duration": _str(),
            },
            required=["kind"],
        ),
        "achievements": _list_of(
            {
                "kind": _str(enum=list(get_args(AchievementKind)), nullable=False),
                "title": _str(nullable=False),
                "details": _str(), "year": _str(), "status": _str(),
            },
            required=["kind", "title"],
        ),
        "guidance": _list_of(
            {
                "level": _str(enum=list(get_args(GuidanceLevel)), nullable=False),
                "description": _str(nullable=False),
                "count": {"type": "INTEGER", "nullable": True},
            },
            required=["level", "description"],
        ),
        "memberships": {"type": "ARRAY", "items": {"type": "STRING"}},
        "email": _str(),
        "phone": _str(),
    },
    required=[
        "education", "publications", "events", "subjects_taught", "skills",
        "experience", "achievements", "guidance", "memberships", "email", "phone",
        "candidate_name", "highest_degree", "marks_pct", "cgpa", "has_phd", "phd_status", "phd_award_date",
        "phd_regulation", "masters_award_date", "net_set_status", "set_state",
        "study_leave_taken", "teaching_years_raw", "publications_count", "publication_titles",
        "publications_in_progress_count", "publications_in_progress_titles",
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
    fields = {name: _coerce_field(name, payload[name]) for name in FIELD_NAMES}
    details = {
        "education": _coerce_items(payload.get("education"), EducationEntry),
        "publications": _coerce_items(payload.get("publications"), PublicationEntry),
        "events": _coerce_items(payload.get("events"), EventEntry),
        "subjects_taught": _coerce_strings(payload.get("subjects_taught")),
        "skills": _coerce_strings(payload.get("skills")),
        "experience": _coerce_items(payload.get("experience"), ExperienceEntry),
        "achievements": _coerce_items(payload.get("achievements"), AchievementEntry),
        "guidance": _coerce_items(payload.get("guidance"), GuidanceEntry),
        "memberships": _coerce_strings(payload.get("memberships")),
        "email": _coerce_text(payload.get("email")),
        "phone": _coerce_text(payload.get("phone")),
    }
    return ExtractionResult(**fields, **details, raw_llm_output=raw_text)


def _coerce_items(raw: Any, model: type) -> list:
    """Parse a detail list leniently: one malformed item is dropped, not
    allowed to fail the whole resume."""
    items = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            items.append(model(**entry))
        except Exception:  # pydantic ValidationError, or a non-dict entry
            logger.warning("detail_item_dropped type=%s", model.__name__)
    return items


def _coerce_text(raw: Any) -> str | None:
    return raw.strip() or None if isinstance(raw, str) else None


def _coerce_strings(raw: Any) -> list[str]:
    return [s.strip() for s in raw if isinstance(s, str) and s.strip()] if isinstance(raw, list) else []


def _error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _is_rate_limit_error(exc: Exception) -> bool:
    if _error_code(exc) == 429:
        return True
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower()


def _is_unavailable(exc: Exception) -> bool:
    """A 503 / "high demand" response."""
    return _error_code(exc) == 503 or "UNAVAILABLE" in str(exc)


class _DailyQuotaExhausted(Exception):
    """Internal: this model is out for the day. Never leaves the provider."""


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
        raise ExtractionFailure("GEMINI_MODEL is 'auto' but GEMINI_MODEL_POOL is empty.", kind="bad_config")
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

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        lighter_fallback: bool | None = None,
        fallback_model: str | None = None,
    ) -> None:
        key = api_key or config.GEMINI_API_KEY
        if not key:
            raise ExtractionFailure(
                "GEMINI_API_KEY is not set. Get a key at https://aistudio.google.com/apikey "
                "and set the GEMINI_API_KEY environment variable.",
                kind="bad_config",
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
        self.lighter_fallback = config.GEMINI_LIGHTER_FALLBACK if lighter_fallback is None else lighter_fallback
        self.fallback_model = fallback_model or config.GEMINI_FALLBACK_MODEL
        # What a cached result is only valid for. The prompt version changes
        # whenever the extraction prompt does; the model id covers the pool
        # as configured, not today's rotated pick, so rotating models does
        # not throw away a day's worth of perfectly good results.
        self.prompt_version = hashlib.sha256(self._system_prompt.encode("utf-8")).hexdigest()[:12]
        self.cache_model_id = (
            configured if configured.lower() != "auto" else "auto:" + ",".join(sorted(config.GEMINI_MODEL_POOL))
        )

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

    def _attempt_model(self, resume_text: str, model: str) -> ExtractionResult:
        """Call one model, retrying transient errors with backoff and jitter.

        Raises _DailyQuotaExhausted when the model is out for the day, and
        ExtractionFailure (carrying a `kind`) when retries run out or the
        request itself is wrong.
        """
        attempt = 0
        while True:
            attempt += 1
            kind = "api_unavailable"
            suggested: float | None = None
            backoff = True
            limit = _MAX_RETRIES
            try:
                result = self._call_once(resume_text, model)
                result.model_used = model
                return result
            except ExtractionFailure as exc:
                # Malformed JSON despite schema constraints. Sampling can
                # produce valid JSON next time, and it isn't load-related, so
                # retry straight away rather than waiting.
                last_error: Exception = exc
                backoff = False
                logger.warning("gemini_extraction_failure attempt=%d", attempt)
            except genai_errors.APIError as exc:
                last_error = exc
                if _is_non_retryable(exc):
                    logger.warning("gemini_config_error code=%s", _error_code(exc))
                    raise ExtractionFailure(
                        f"Gemini rejected the request (HTTP {_error_code(exc)}): {exc}. "
                        "This is a configuration problem (model name or API key), "
                        "not a transient failure — check GEMINI_MODEL and GEMINI_API_KEY.",
                        kind="bad_config",
                    ) from exc
                if _is_daily_quota_exhausted(exc):
                    logger.warning("gemini_daily_quota_exhausted model=%s", model)
                    raise _DailyQuotaExhausted(model) from exc
                suggested = _suggested_wait_seconds(exc)
                if _is_rate_limit_error(exc):
                    kind = "quota"
                elif _is_unavailable(exc):
                    limit = _MAX_RETRIES_UNAVAILABLE
                logger.warning("gemini_api_error code=%s attempt=%d", _error_code(exc), attempt)
            except Exception as exc:  # network/timeout errors from the SDK
                last_error = exc
                logger.warning("gemini_request_error attempt=%d", attempt)

            if attempt >= limit:
                raise ExtractionFailure(
                    f"extraction failed after {attempt} attempts: {last_error}", kind=kind
                ) from last_error
            if backoff:
                wait = _backoff_seconds(attempt, suggested)
                logger.warning(
                    "gemini_backoff attempt=%d wait_s=%.1f source=%s",
                    attempt, wait, "api" if suggested is not None else "exponential",
                )
                time.sleep(wait)

    def extract_fields(self, resume_text: str) -> ExtractionResult:
        """Extract one resume, falling over to the next pool model on quota.

        Each free-tier model has its own separate daily allowance, so when
        one runs out the batch keeps going on the next rather than failing
        every remaining file. If the whole pool fails and the opt-in lighter
        fallback is enabled, one more try is made on that model and the
        result is flagged as such.
        """
        failure: ExtractionFailure | None = None
        while True:
            model = self._next_usable_model()
            if model is None:
                if failure is None:
                    failure = ExtractionFailure(
                        "Gemini daily quota is exhausted for every model in "
                        f"GEMINI_MODEL_POOL ({', '.join(self.pool)}). This will not "
                        "clear until the quota resets. Wait for the reset, enable "
                        "billing for higher limits, or switch GEMINI_MODEL to a "
                        "'-lite' model (much larger daily allowance, lower accuracy).",
                        kind="quota",
                    )
                break
            try:
                return self._attempt_model(resume_text, model)
            except _DailyQuotaExhausted:
                # Out for the day on this model; take it out of rotation and
                # try the next one immediately.
                self._mark_exhausted(model)
            except ExtractionFailure as exc:
                if exc.kind == "bad_config":
                    raise
                # Retries exhausted for a transient reason -- don't burn the
                # rest of the pool on what is probably the same fault.
                failure = exc
                break

        if self.lighter_fallback and self.fallback_model and self.fallback_model not in self.pool:
            logger.warning("gemini_lighter_fallback model=%s", self.fallback_model)
            try:
                result = self._attempt_model(resume_text, self.fallback_model)
            except (_DailyQuotaExhausted, ExtractionFailure):
                pass  # report the original failure, not the fallback's
            else:
                result.lighter_model_fallback = True
                return result
        raise failure
