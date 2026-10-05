"""Shared FastAPI dependencies: the LLM provider and the result cache."""

from __future__ import annotations

from fastapi import HTTPException

import config
from app.result_cache import ResultCache
from llm.interface import ExtractionFailure, LLMProvider

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """The configured LLM provider, built on first use so the application
    starts (and health checks pass) even when no key is set."""
    global _provider
    if _provider is None:
        if config.LLM_PROVIDER == "fake":
            from llm.providers.fake_provider import FakeProvider

            _provider = FakeProvider()
        else:
            from llm.providers.gemini_provider import GeminiProvider

            try:
                _provider = GeminiProvider()
            except ExtractionFailure as exc:
                raise HTTPException(status_code=503, detail=f"LLM provider is not configured: {exc}") from exc
    return _provider


def get_cache() -> ResultCache:
    return ResultCache()
