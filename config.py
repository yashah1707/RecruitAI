"""Runtime configuration, read once from the environment at import time.

Every value here is a config value, never a magic number inlined at a call site
(see the MVP brief section 6). Nothing in this module talks to a provider or a
file — it just resolves settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

# Loads variables from a .env file in the project root, if one exists, into
# the process environment -- without overriding a variable that's already
# set in the real environment. Lets GEMINI_API_KEY live in a local, untracked
# file instead of being typed into a terminal every session.
load_dotenv(Path(__file__).resolve().parent / ".env")

ProviderName = Literal["ollama", "fake", "gemini"]


def _get_str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Which LLMProvider implementation the app instantiates. "fake" runs the whole
# app end-to-end with zero model calls; "ollama" is the real local extraction.
LLM_PROVIDER: str = _get_str("LLM_PROVIDER", "ollama").lower()

# Local Ollama model tag. CPU-only inference on the target dev machine.
LLM_MODEL: str = _get_str("LLM_MODEL", "qwen3.5:4b")

# Ollama HTTP endpoint.
OLLAMA_HOST: str = _get_str("OLLAMA_HOST", "http://localhost:11434")

# Client-side timeout per model call, in seconds. CPU inference is slow; the
# brief requires 60s or more. Measured ~140s for a short resume with
# qwen3.5:4b on the target i5-1145G7 CPU-only machine, so the default leaves
# real headroom rather than sitting right at the brief's 60s floor.
LLM_TIMEOUT_SECONDS: float = _get_float("LLM_TIMEOUT_SECONDS", 240.0)

# A field at or above this confidence is trusted; below it the row is routed to
# needs_review and the cell is highlighted in the workbook.
CONFIDENCE_THRESHOLD: float = _get_float("CONFIDENCE_THRESHOLD", 0.7)

# Cloud fallback (Gemini API). Google's free tier permits using submitted
# prompts for model training — GeminiProvider logs a prominent warning on
# every call rather than silently sending real candidate data. Empty by
# default; must be set explicitly to use LLM_PROVIDER=gemini.
GEMINI_API_KEY: str = _get_str("GEMINI_API_KEY", "")
# "auto" alternates between the models in GEMINI_MODEL_POOL by date, and
# fails over to the next one when a model's daily quota runs out. Each free
# model has its own separate 20/day allowance, so alternating two of them
# gives 40/day without any manual switching. Set an explicit model name here
# to pin one instead.
GEMINI_MODEL: str = _get_str("GEMINI_MODEL", "auto")

# Both are free-tier models with good extraction accuracy. Deliberately no
# "-lite" entries: those have far more daily quota but were measurably wrong
# on NET/SET status and PhD status, the two highest-risk fields in this
# system, so they are opt-in via GEMINI_MODEL rather than part of rotation.
GEMINI_MODEL_POOL: list[str] = [
    m.strip() for m in _get_str("GEMINI_MODEL_POOL", "gemini-3.5-flash,gemini-3.6-flash").split(",") if m.strip()
]

GEMINI_TIMEOUT_SECONDS: float = _get_float("GEMINI_TIMEOUT_SECONDS", 60.0)
# 0 means "let the provider pick from the model's rate limit"; set a number
# to override (e.g. after enabling billing, which raises the RPM ceiling).
GEMINI_MAX_CONCURRENCY: int = int(_get_float("GEMINI_MAX_CONCURRENCY", 0))
