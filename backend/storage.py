"""Where uploaded resume files are kept, and how they are protected on disk.

Files are named by the SHA-256 of their content, so the same file uploaded
twice is stored once, and nothing about the candidate appears in a path.

When DATA_ENCRYPTION_KEY is set, every file is written encrypted (Fernet:
AES-128 in CBC mode with an HMAC-SHA256 check). The name is still the hash of
the original content, so duplicate detection is unchanged. Reading goes
through `read_stored`, which accepts both encrypted files and files stored
before encryption was turned on; `python -m backend.storage --encrypt-existing`
encrypts the latter in place.

Losing the key means losing every encrypted resume and backup. Keep a copy of
it somewhere other than this computer.

    python -m backend.storage --new-key            writes a new key into .env (refuses if one is set)
    python -m backend.storage --encrypt-existing   encrypts files stored before the key was set
    python -m backend.storage --status             counts encrypted and unencrypted files
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from backend import settings

ALLOWED_SUFFIXES = frozenset({".pdf", ".docx"})
MAX_BYTES = 15 * 1024 * 1024
# Every Fernet token starts with these bytes (version byte 0x80, base64-encoded).
_TOKEN_PREFIX = b"gAAAAA"


class RejectedUpload(Exception):
    """The file is not something the Reader accepts."""


class KeyMissing(Exception):
    """A file is encrypted but no key is configured, or the configured key does not open it."""


def _fernet(key: str | None = None):
    from cryptography.fernet import Fernet

    key = (key if key is not None else settings.DATA_ENCRYPTION_KEY).strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise KeyMissing("DATA_ENCRYPTION_KEY is not a valid key; create one with python -m backend.storage --new-key") from exc


def check_key() -> None:
    """Raise KeyMissing when DATA_ENCRYPTION_KEY is set but is not a usable key. Called when the application starts,
    so a mistyped key stops it with a clear message instead of failing every upload later."""
    _fernet()


def is_encrypted(data: bytes) -> bool:
    return data.startswith(_TOKEN_PREFIX)


def encrypt(data: bytes, key: str | None = None) -> bytes:
    """`data` encrypted with the configured key, or unchanged when no key is set."""
    f = _fernet(key)
    return f.encrypt(data) if f is not None else data


def decrypt(data: bytes, key: str | None = None) -> bytes:
    """The original bytes of something `encrypt` produced; plain data is returned as it is."""
    if not is_encrypted(data):
        return data
    from cryptography.fernet import InvalidToken

    f = _fernet(key)
    if f is None:
        raise KeyMissing("this file is encrypted and DATA_ENCRYPTION_KEY is not set")
    try:
        return f.decrypt(data)
    except InvalidToken as exc:
        raise KeyMissing("this file was encrypted with a different key") from exc


def read_stored(path: str | Path) -> bytes:
    """The original content of a stored file, decrypted if it was stored encrypted."""
    return decrypt(Path(path).read_bytes())


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_resume(filename: str, data: bytes, directory: Path | None = None) -> tuple[str, Path]:
    """Store `data`; returns (sha256 of the original content, path)."""
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
        _write_atomic(path, encrypt(data))
    return digest, path


def _stored_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES)


def status(directory: Path | None = None) -> dict[str, int]:
    counts = {"encrypted": 0, "not_encrypted": 0}
    for p in _stored_files(directory or settings.STORAGE_DIR):
        with p.open("rb") as fh:
            counts["encrypted" if is_encrypted(fh.read(len(_TOKEN_PREFIX))) else "not_encrypted"] += 1
    return counts


def encrypt_existing(directory: Path | None = None) -> dict[str, int]:
    """Encrypt, in place, every stored file that is not encrypted yet. Needs the key."""
    if _fernet() is None:
        raise KeyMissing("DATA_ENCRYPTION_KEY is not set; nothing can be encrypted")
    counts = {"encrypted_now": 0, "already_encrypted": 0}
    for p in _stored_files(directory or settings.STORAGE_DIR):
        data = p.read_bytes()
        if is_encrypted(data):
            counts["already_encrypted"] += 1
            continue
        sealed = encrypt(data)
        if decrypt(sealed) != data:  # never replace a file with something that does not open again
            raise KeyMissing(f"encryption check failed for {p.name}; nothing was changed for it")
        _write_atomic(p, sealed)
        counts["encrypted_now"] += 1
    return counts


def write_new_key(env_file: Path | None = None) -> Path:
    """Add a new DATA_ENCRYPTION_KEY to .env. Refuses if one is already there, so a key in use is never replaced."""
    from cryptography.fernet import Fernet

    env_file = env_file or settings.PROJECT_ROOT / ".env"
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    for line in text.splitlines():
        if line.strip().startswith("DATA_ENCRYPTION_KEY=") and line.split("=", 1)[1].strip():
            raise SystemExit(f"{env_file.name} already has a DATA_ENCRYPTION_KEY. It was not changed.")
    key = Fernet.generate_key().decode()
    sep = "" if not text or text.endswith("\n") else "\n"
    env_file.write_text(text + sep + "\n# Encrypts stored resumes and backups. Keep a copy somewhere safe: without it they cannot be opened.\n"
                        f"DATA_ENCRYPTION_KEY={key}\n", encoding="utf-8")
    return env_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.storage", description="Encryption of stored resume files.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--new-key", action="store_true", help="write a new DATA_ENCRYPTION_KEY into .env")
    group.add_argument("--encrypt-existing", action="store_true", help="encrypt files stored before the key was set")
    group.add_argument("--status", action="store_true", help="count encrypted and unencrypted files")
    args = parser.parse_args(argv)
    if args.new_key:
        path = write_new_key()
        print(f"A new key was written to {path.name}. Copy that line somewhere safe, then restart the application.")
        return 0
    try:
        counts = encrypt_existing() if args.encrypt_existing else status()
    except KeyMissing as exc:
        print(exc)
        return 1
    print(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
