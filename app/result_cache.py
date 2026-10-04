"""Per-file extraction cache, so a rerun only pays for what failed.

A successful extraction is stored under a key made of the resume text hash,
the extraction prompt version and the model setting. Change any of the three
and the old entry simply stops matching -- nothing is ever served for a
different prompt or model than the one that produced it.

Only successes are cached. A failed row is never written, so rerunning the
same files retries exactly the failures and nothing else. Results produced by
the opt-in lighter-model fallback are not cached either: caching one would
pin a lower-accuracy answer and stop a later run from replacing it.

PII: an entry holds the extracted fields (names, qualifications) for a real
candidate, so the directory is gitignored and local-only, like the exports.
The resume text itself is never stored -- only its hash.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from llm.interface import ExtractionResult

logger = logging.getLogger("recruitai.result_cache")

# Bump when the stored shape or the post-processing rules change in a way that
# makes old entries wrong. Cheaper than hoping nobody forgets to clear the cache.
CACHE_FORMAT_VERSION = "2"

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "extractions"


def cache_key(resume_text: str, prompt_version: str, model_id: str) -> str:
    text_hash = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()
    material = "|".join((CACHE_FORMAT_VERSION, text_hash, prompt_version, model_id))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ResultCache:
    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else DEFAULT_CACHE_DIR

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> ExtractionResult | None:
        path = self._path(key)
        try:
            return ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValidationError, ValueError):
            # A damaged or outdated entry is a miss, never a crash.
            logger.warning("cache_entry_unreadable key=%s", key[:10])
            return None

    def put(self, key: str, result: ExtractionResult) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a worker thread never reads a half-written file.
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(result.model_dump_json())
            os.replace(tmp, self._path(key))
        except OSError:
            logger.warning("cache_write_failed key=%s", key[:10])
            Path(tmp).unlink(missing_ok=True)

    def count(self) -> int:
        return sum(1 for _ in self.directory.glob("*.json")) if self.directory.exists() else 0

    def clear(self) -> int:
        """Delete every cached entry; returns how many were removed."""
        removed = 0
        if self.directory.exists():
            for path in self.directory.glob("*.json"):
                path.unlink(missing_ok=True)
                removed += 1
        return removed
