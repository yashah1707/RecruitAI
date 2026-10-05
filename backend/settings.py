"""Backend settings, read once from the environment (and the project `.env`)."""

from __future__ import annotations

import os
from pathlib import Path

import config  # noqa: F401  -- importing it loads the project's .env

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# PostgreSQL is the target database. The default points at the instance
# defined in docker-compose.yml. Any SQLAlchemy URL works, which is how the
# test suite (and a machine without PostgreSQL) runs on SQLite instead.
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://recruitai:recruitai@localhost:5432/recruitai"
).strip()

# Where uploaded resumes are kept. They are personal data: the directory is
# gitignored and files are named by content hash, never by the candidate.
STORAGE_DIR: Path = Path(os.environ.get("STORAGE_DIR", "") or PROJECT_ROOT / "storage" / "resumes")

# The State whose SET/SLET is valid for appointment here (cl. 3.3, p. 58).
INSTITUTION_STATE: str = os.environ.get("INSTITUTION_STATE", "Maharashtra").strip()
