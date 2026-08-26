"""Real extraction via a local Ollama model.

Structured output is forced through Ollama's `format` JSON-schema parameter —
the model cannot return free-form prose. On timeout or malformed output the
call is retried once, then raises ExtractionFailure so the caller routes the
row to needs_review instead of crashing the batch.

Per the PII constraint on this slice (MVP brief section 1.6), neither the
resume text nor any extracted value is logged — only lengths, status, and
routing-relevant facts.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import requests

import config
from llm.interface import ExtractionFailure, ExtractionResult, FieldWithConfidence
from llm.postprocess import log_stats, sanitize

logger = logging.getLogger("recruitai.ollama_provider")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extraction.md"

_NULLABLE_STRING = {"type": ["string", "null"]}
_NULLABLE_NUMBER = {"type": ["number", "null"]}
_NULLABLE_BOOL = {"type": ["boolean", "null"]}
_NULLABLE_DATE = {"type": ["string", "null"], "description": "YYYY-MM-DD or null"}


def _field_schema(value_schema: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": value_schema,
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence": _NULLABLE_STRING,
        },
        "required": ["value", "confidence", "evidence"],
    }


# Hand-built rather than derived from the Pydantic model: Ollama's structured
# decoding wants a plain JSON Schema with string-typed dates and explicit
# enum/null unions, which doesn't match what pydantic.model_json_schema()
# emits for `date | None` and `Literal[..., None]` out of the box.
EXTRACTION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "candidate_name": _field_schema(_NULLABLE_STRING),
        "highest_degree": _field_schema({"type": "string", "enum": ["UG", "PG", "PhD", "Post-Doc"]}),
        "marks_pct": _field_schema(_NULLABLE_NUMBER),
        "cgpa": _field_schema(_NULLABLE_NUMBER),
        "has_phd": _field_schema({"type": "boolean"}),
        "phd_award_date": _field_schema(_NULLABLE_DATE),
        "phd_regulation": _field_schema({"type": ["string", "null"], "enum": ["2009", "2016", None]}),
        "masters_award_date": _field_schema(_NULLABLE_DATE),
        "net_set_status": _field_schema({"type": "string", "enum": ["NET", "SET", "SLET", "NONE"]}),
        "set_state": _field_schema(_NULLABLE_STRING),
        "study_leave_taken": _field_schema(_NULLABLE_BOOL),
        "teaching_years_raw": _field_schema(_NULLABLE_NUMBER),
        "publications_count": _field_schema({"type": "integer"}),
        "publication_titles": _field_schema({"type": ["array", "null"], "items": {"type": "string"}}),
        "publications_in_progress_count": _field_schema({"type": "integer"}),
        "publications_in_progress_titles": _field_schema({"type": ["array", "null"], "items": {"type": "string"}}),
    },
    "required": [
        "candidate_name", "highest_degree", "marks_pct", "cgpa", "has_phd", "phd_award_date",
        "phd_regulation", "masters_award_date", "net_set_status", "set_state",
        "study_leave_taken", "teaching_years_raw", "publications_count", "publication_titles",
        "publications_in_progress_count", "publications_in_progress_titles",
    ],
}

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
    evidence = payload.get("evidence")
    return FieldWithConfidence(value=value, confidence=confidence, evidence=evidence)


def _to_extraction_result(payload: dict[str, Any], raw_text: str) -> ExtractionResult:
    fields = {name: _coerce_field(name, payload[name]) for name in EXTRACTION_JSON_SCHEMA["properties"]}
    return ExtractionResult(**fields, raw_llm_output=raw_text)


class OllamaProvider:
    """Talks to a local Ollama server at `config.OLLAMA_HOST`."""

    name = "ollama"
    # CPU-bound local inference: concurrent calls contend for the same
    # cores and finish no sooner, so extractions stay strictly serial.
    max_concurrency = 1

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.model = model or config.LLM_MODEL
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.timeout = timeout or config.LLM_TIMEOUT_SECONDS
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def _call_once(self, resume_text: str) -> ExtractionResult:
        response = requests.post(
            f"{self.host}/api/generate",
            json={
                "model": self.model,
                "system": self._system_prompt,
                "prompt": resume_text,
                "format": EXTRACTION_JSON_SCHEMA,
                "stream": False,
                # qwen3.5:4b is a "thinking" model: with thinking left on, it
                # writes the whole schema-constrained answer into Ollama's
                # `thinking` field and leaves `response` empty. Forcing a
                # direct answer is both correct and faster (no reasoning pass).
                "think": False,
                "options": {"temperature": 0},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        raw_text = response.json().get("response", "")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning("malformed_json_from_model length=%d", len(raw_text))
            raise ExtractionFailure("model returned malformed JSON") from exc
        result = _to_extraction_result(payload, raw_text)
        result, stats = sanitize(result, resume_text)
        log_stats(stats)
        return result

    def extract_fields(self, resume_text: str) -> ExtractionResult:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                return self._call_once(resume_text)
            except requests.Timeout as exc:
                last_error = exc
                logger.warning("ollama_timeout attempt=%d timeout_s=%s", attempt, self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("ollama_request_error attempt=%d", attempt)
            except ExtractionFailure as exc:
                last_error = exc
                logger.warning("ollama_extraction_failure attempt=%d", attempt)
        raise ExtractionFailure(f"extraction failed after 2 attempts: {last_error}")
