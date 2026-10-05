"""Where uploaded resume files are kept.

Files are named by the SHA-256 of their content, so the same file uploaded
twice is stored once, and nothing about the candidate appears in a path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from backend import settings

ALLOWED_SUFFIXES = frozenset({".pdf", ".docx"})
MAX_BYTES = 15 * 1024 * 1024


class RejectedUpload(Exception):
    """The file is not something the Reader accepts."""


def save_resume(filename: str, data: bytes, directory: Path | None = None) -> tuple[str, Path]:
    """Store `data`; returns (sha256, path)."""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise RejectedUpload(f"unsupported file type {suffix or '(none)'}; upload a PDF or DOCX")
    if not data:
        raise RejectedUpload("the uploaded file is empty")
    if len(data) > MAX_BYTES:
        raise RejectedUpload("the uploaded file is larger than 15 MB")
    digest = hashlib.sha256(data).hexdigest()
    directory = directory or settings.STORAGE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}{suffix}"
    if not path.exists():
        path.write_bytes(data)
    return digest, path
