"""Matching a college or university named on a resume to `institutions_master`.

The master list (name, tier, category, other spellings) is HR's to supply.
Until it is loaded the table is empty and nothing matches, which is the
correct result: a tier is never guessed.

Load or update the list from a CSV with the columns
`institution_name, tier, category, aliases` (aliases separated by `;`):

    python -m backend.institutions path/to/institutions.csv
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import InstitutionMaster

TIERS: tuple[str, ...] = ("PREMIER", "NATIONAL", "STATE", "OTHER")

_NOT_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_NOISE_WORDS = frozenset({"the", "of", "and", "at"})


def match_key(name: str | None) -> str:
    """A name reduced to what identifies it: case, punctuation and filler words removed.

    "The Indian Institute of Technology, Bombay" and "Indian Institute Of
    Technology Bombay" give the same key. Nothing is abbreviated or expanded:
    "IIT Bombay" matches only if the list gives it as an alias.
    """
    words = _NOT_ALNUM_RE.sub(" ", (name or "").lower().replace("&", " and ")).split()
    return " ".join(w for w in words if w not in _NOISE_WORDS)


def match_institution(session: Session, *names: str | None) -> int | None:
    """The `institution_id` for the first of `names` found in the master list, or None.

    An exact match on the reduced name or on an alias, and nothing looser: a
    near match between two different colleges would put a candidate in the
    wrong tier.
    """
    wanted = [k for k in (match_key(n) for n in names) if k]
    if not wanted:
        return None
    index: dict[str, int] = {}
    for row in session.scalars(select(InstitutionMaster).order_by(InstitutionMaster.institution_id)):
        for spelling in (row.institution_name, *(row.aliases or [])):
            index.setdefault(match_key(spelling), row.institution_id)
    return next((index[k] for k in wanted if k in index), None)


def load_csv(session: Session, path: str | Path) -> dict[str, int]:
    """Insert or update the master list from a CSV file. Returns what it did."""
    added = updated = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            name = " ".join((row.get("institution_name") or "").split())[:250]
            tier = (row.get("tier") or "").strip().upper()
            if not name:
                continue
            if tier not in TIERS:
                raise ValueError(f"line {line}: tier must be one of {', '.join(TIERS)}")
            aliases = [" ".join(a.split()) for a in (row.get("aliases") or "").split(";") if a.strip()]
            existing = session.scalar(select(InstitutionMaster).where(InstitutionMaster.institution_name == name))
            if existing is None:
                existing = InstitutionMaster(institution_name=name)
                session.add(existing)
                added += 1
            else:
                updated += 1
            existing.tier, existing.aliases = tier, aliases or None
            existing.category = (row.get("category") or "").strip()[:40]
    session.flush()
    return {"added": added, "updated": updated}


if __name__ == "__main__":  # python -m backend.institutions <file.csv>
    from backend.db import get_engine

    if len(sys.argv) != 2:
        sys.exit("usage: python -m backend.institutions <file.csv>")
    with Session(get_engine()) as s:
        print(load_csv(s, sys.argv[1]))
        s.commit()
