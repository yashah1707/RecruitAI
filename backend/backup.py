"""Back up the database and the stored resumes into one file, and keep the last few.

    python -m backend.backup                 make a backup now
    python -m backend.backup --list          list the backups kept
    python -m backend.backup --open FILE DIR unpack a backup into DIR, for restoring

A backup is one zip holding `database.dump` (PostgreSQL custom format, made by
pg_dump; or `database.sqlite` for a SQLite database), the `resumes/` folder and
`manifest.json`. When DATA_ENCRYPTION_KEY is set the whole zip is encrypted
and saved as `.zip.enc`: it holds every candidate's data, so it is treated like
the data itself. Only the last BACKUP_KEEP backups are kept (default 14).

To restore a PostgreSQL backup, unpack it with --open, then
    pg_restore --clean --if-exists -d <database> DIR/database.dump
and copy DIR/resumes/ back into the storage folder. The user manual has the steps.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from backend import settings, storage
from backend.logging_setup import configure, describe_error

logger = logging.getLogger("recruitai.backup")
_PREFIX = "recruitai-backup-"


class BackupError(Exception):
    """The backup could not be made or opened. The message says why, without personal data."""


def find_pg_dump() -> str:
    candidates = []
    if settings.PG_BIN:
        candidates.append(str(Path(settings.PG_BIN) / ("pg_dump.exe" if os.name == "nt" else "pg_dump")))
    found = shutil.which("pg_dump")
    if found:
        candidates.append(found)
    candidates += sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe"), reverse=True)
    for c in candidates:
        if Path(c).is_file():
            return c
    raise BackupError("pg_dump was not found. Set PG_BIN in .env to the PostgreSQL bin folder.")


def _dump_database(url: str, into: Path) -> str:
    """Write the database into `into`; returns the file name used inside the backup."""
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        if not u.database or u.database == ":memory:":
            raise BackupError("an in-memory SQLite database cannot be backed up")
        target = into / "database.sqlite"
        src = sqlite3.connect(u.database)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)  # a consistent copy even while the application is running
        finally:
            dst.close()
            src.close()
        return target.name
    if u.get_backend_name() != "postgresql":
        raise BackupError(f"backing up a {u.get_backend_name()} database is not supported")
    target = into / "database.dump"
    cmd = [find_pg_dump(), "--format=custom", "--no-owner", f"--file={target}",
           f"--host={u.host or 'localhost'}", f"--port={u.port or 5432}", f"--username={u.username or 'postgres'}", u.database]
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password  # never on the command line, where other programs can see it
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"pg_dump failed (exit code {result.returncode}); check that PostgreSQL is running")
    return target.name


def make_backup(now: datetime | None = None) -> Path:
    """Make one backup and prune old ones. Returns the backup file."""
    now = now or datetime.now()
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="recruitai-backup-") as tmp:
        tmp_dir = Path(tmp)
        db_name = _dump_database(settings.DATABASE_URL, tmp_dir)
        resumes = [p for p in sorted(settings.STORAGE_DIR.glob("*")) if p.is_file()] if settings.STORAGE_DIR.is_dir() else []
        manifest = {"made": now.isoformat(timespec="seconds"), "database": db_name, "resume_files": len(resumes),
                    "resumes_encrypted": storage.status()["encrypted"] if resumes else 0}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_dir / db_name, db_name)
            for p in resumes:
                zf.write(p, f"resumes/{p.name}")  # stored as they are on disk, encrypted or not
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    data = buffer.getvalue()
    sealed = storage.encrypt(data)
    suffix = ".zip.enc" if sealed is not data else ".zip"
    path = settings.BACKUP_DIR / f"{_PREFIX}{stamp}{suffix}"
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(sealed)
    os.replace(tmp_path, path)
    pruned = prune()
    logger.info("backup_made file=%s bytes=%d resumes=%d encrypted=%s pruned=%d", path.name, len(sealed), len(resumes),
                suffix == ".zip.enc", pruned)
    return path


def backups() -> list[Path]:
    if not settings.BACKUP_DIR.is_dir():
        return []
    return sorted(p for p in settings.BACKUP_DIR.iterdir() if p.name.startswith(_PREFIX) and p.suffix in (".zip", ".enc"))


def prune(keep: int | None = None) -> int:
    keep = settings.BACKUP_KEEP if keep is None else keep
    old = backups()[:-keep] if keep > 0 else []
    for p in old:
        p.unlink()
    return len(old)


def open_backup(path: Path, into: Path) -> dict:
    """Decrypt and unpack a backup into `into`; returns its manifest."""
    try:
        data = storage.decrypt(path.read_bytes())
    except storage.KeyMissing as exc:
        raise BackupError(f"the backup cannot be opened: {exc}") from exc
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            target = (into / name).resolve()
            if into.resolve() not in target.parents:  # never write outside the folder asked for
                raise BackupError("the backup holds an unexpected path; nothing more was unpacked")
            zf.extract(name, into)
        return json.loads(zf.read("manifest.json"))


def main(argv: list[str] | None = None) -> int:
    configure()
    parser = argparse.ArgumentParser(prog="python -m backend.backup", description="Back up the database and stored resumes.")
    parser.add_argument("--list", action="store_true", help="list the backups kept")
    parser.add_argument("--open", nargs=2, metavar=("FILE", "DIR"), help="unpack a backup into DIR")
    args = parser.parse_args(argv)
    try:
        if args.list:
            for p in backups():
                print(f"{p.name}  {p.stat().st_size // 1024} KB")
            return 0
        if args.open:
            manifest = open_backup(Path(args.open[0]), Path(args.open[1]))
            print(f"Unpacked into {args.open[1]}: {manifest['database']} and {manifest['resume_files']} resume file(s), "
                  f"made {manifest['made']}.")
            return 0
        path = make_backup()
        print(f"Backup made: {path}")
        if path.suffix == ".zip":
            print("It is NOT encrypted. Set DATA_ENCRYPTION_KEY (python -m backend.storage --new-key) to encrypt backups.")
        return 0
    except (BackupError, storage.KeyMissing) as exc:
        logger.error("backup_failed reason=%s", exc)
        print(f"Backup failed: {exc}")
        return 1
    except Exception as exc:  # the scheduled task's log should say what broke, without personal data
        logger.error("backup_failed error=%s", describe_error(exc))
        print("Backup failed. See logs/recruitai.log.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
