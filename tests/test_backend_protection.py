"""Protection of stored data: encrypted resume files, encrypted backups, and a log without personal data.

Made-up files and keys only. SQLite by default; PostgreSQL when TEST_DATABASE_URL is set (backups of a
PostgreSQL database are covered by a manual check, since they need pg_dump and a server).
"""

from __future__ import annotations

import hashlib
import json
import logging

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from backend import backup, intake, jobs, settings, states, storage
from backend.logging_setup import describe_error, safe_path
from backend.models import Application
from llm.providers.fake_provider import FakeProvider
from tests.test_backend_foundation import _clean_result, _docx, client, engine, session  # noqa: F401
from tests.test_backend_intake import _opening, _storage  # noqa: F401

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def key(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", k)
    return k


# --- resume files ------------------------------------------------------------


def test_with_a_key_a_resume_is_stored_encrypted_under_the_hash_of_its_original_content(key, tmp_path):
    data = _docx()
    digest, path = storage.save_resume("cv.docx", data, tmp_path)
    on_disk = path.read_bytes()
    assert digest == hashlib.sha256(data).hexdigest() and path.name == f"{digest}.docx"
    assert not on_disk.startswith(b"PK") and storage.is_encrypted(on_disk)  # nothing readable on disk
    assert storage.read_stored(path) == data
    again_digest, again_path = storage.save_resume("other-name.docx", data, tmp_path)
    assert (again_digest, again_path) == (digest, path)  # the same file is still recognised as the same


def test_without_a_key_files_are_stored_as_uploaded_and_can_be_encrypted_later(tmp_path, monkeypatch):
    data = _docx()
    _, path = storage.save_resume("cv.docx", data, tmp_path)
    assert path.read_bytes() == data and storage.status(tmp_path) == {"encrypted": 0, "not_encrypted": 1}
    with pytest.raises(storage.KeyMissing):
        storage.encrypt_existing(tmp_path)

    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert storage.encrypt_existing(tmp_path) == {"encrypted_now": 1, "already_encrypted": 0}
    assert storage.is_encrypted(path.read_bytes()) and storage.read_stored(path) == data
    assert storage.encrypt_existing(tmp_path) == {"encrypted_now": 0, "already_encrypted": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_an_encrypted_file_needs_the_right_key(key, tmp_path, monkeypatch):
    _, path = storage.save_resume("cv.docx", _docx(), tmp_path)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    with pytest.raises(storage.KeyMissing, match="not set"):
        storage.read_stored(path)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(storage.KeyMissing, match="different key"):
        storage.read_stored(path)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "not-a-key")
    with pytest.raises(storage.KeyMissing, match="not a valid key"):
        storage.read_stored(path)


def test_a_new_key_is_written_once_and_never_replaces_one_in_use(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=x", encoding="utf-8")
    storage.write_new_key(env)
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "GEMINI_API_KEY=x"
    written = [l for l in lines if l.startswith("DATA_ENCRYPTION_KEY=")]
    assert len(written) == 1
    Fernet(written[0].split("=", 1)[1].encode())  # a valid key
    with pytest.raises(SystemExit):
        storage.write_new_key(env)
    assert env.read_text(encoding="utf-8").count("DATA_ENCRYPTION_KEY=") == 1


def test_an_encrypted_resume_is_read_and_assessed_as_usual(key, session):
    o = _opening(session)
    a = intake.hr_upload(session, o, [("cv.docx", _docx())])[0].application
    assert storage.is_encrypted(open(a.resume_path, "rb").read())
    jobs.run_due_jobs(session, FakeProvider(script=[_clean_result()]))
    assert a.status in (states.EXTRACTED, states.PENDING_REVIEW)


def test_a_missing_key_leaves_the_application_waiting_not_failed(key, session, monkeypatch):
    o = _opening(session)
    a = intake.hr_upload(session, o, [("cv.docx", _docx())])[0].application
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    provider = FakeProvider(script=[_clean_result()])
    jobs.run_due_jobs(session, provider)
    assert a.status == states.RECEIVED and provider.calls == 0  # nothing reached the model
    assert "stored file not opened" in a.transitions[-1].note and "encrypted" in a.transitions[-1].note
    job = session.scalars(jobs.select(jobs.Job)).one()
    assert job.status == jobs.PENDING  # it will be tried again once the key is put right


def test_the_resume_page_serves_the_original_file_and_says_so_when_it_cannot(key, client, engine, monkeypatch):
    with Session(engine) as s:
        o = _opening(s)
        a = intake.hr_upload(s, o, [("cv.docx", _docx())])[0].application
        s.commit()
        app_id = a.application_id
    r = client.get(f"/hr/applications/{app_id}/resume")
    assert r.status_code == 200 and r.content == _docx() and r.headers["content-type"] == DOCX
    assert f"APP-{app_id:06d}.docx" in r.headers["content-disposition"]
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    assert client.get(f"/hr/applications/{app_id}/resume").status_code == 503


# --- backups ------------------------------------------------------------------


@pytest.fixture
def sqlite_app(tmp_path, monkeypatch):
    """A small SQLite database and a storage folder, as the backup sees them."""
    import sqlite3

    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("create table t (x integer)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    store = tmp_path / "resumes"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setattr(settings, "STORAGE_DIR", store)
    monkeypatch.setattr(settings, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(settings, "BACKUP_KEEP", 2)
    storage.save_resume("cv.docx", _docx(), store)
    return tmp_path


def test_a_backup_holds_the_database_and_the_resumes_and_is_encrypted_with_the_key(key, sqlite_app):
    import sqlite3

    path = backup.make_backup()
    assert path.name.endswith(".zip.enc") and storage.is_encrypted(path.read_bytes())
    out = sqlite_app / "restored"
    manifest = backup.open_backup(path, out)
    assert manifest["database"] == "database.sqlite" and manifest["resume_files"] == 1 and manifest["resumes_encrypted"] == 1
    assert sqlite3.connect(out / "database.sqlite").execute("select x from t").fetchone() == (42,)
    restored = next((out / "resumes").iterdir())
    assert storage.read_stored(restored) == _docx()


def test_without_a_key_the_backup_is_a_plain_zip_and_old_backups_are_pruned(sqlite_app):
    from datetime import datetime, timedelta

    start = datetime(2026, 11, 1, 9, 0, 0)
    made = [backup.make_backup(start + timedelta(minutes=i)) for i in range(3)]
    assert all(p.suffix == ".zip" for p in made)
    assert backup.backups() == made[1:]  # BACKUP_KEEP is 2 here
    manifest = json.loads(__import__("zipfile").ZipFile(made[-1]).read("manifest.json"))
    assert manifest["resume_files"] == 1


def test_a_backup_cannot_be_opened_without_its_key(key, sqlite_app, monkeypatch):
    path = backup.make_backup()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(backup.BackupError, match="cannot be opened"):
        backup.open_backup(path, sqlite_app / "x")


def test_the_backup_command_reports_a_failure_without_a_traceback(sqlite_app, monkeypatch, capsys):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+psycopg://u:secret@localhost:1/none")
    monkeypatch.setattr(backup, "find_pg_dump", lambda: (_ for _ in ()).throw(backup.BackupError("pg_dump was not found")))
    assert backup.main([]) == 1
    out = capsys.readouterr().out
    assert "Backup failed: pg_dump was not found" in out and "secret" not in out


# --- the log -------------------------------------------------------------------


def test_an_error_is_described_by_its_type_and_place_never_its_message():
    try:
        raise ValueError("Key (email)=(someone@example.org) already exists")
    except ValueError as exc:
        text = describe_error(exc)
    assert "ValueError" in text and "test_backend_protection.py" in text
    assert "example.org" not in text and "already exists" not in text


def test_a_password_reset_token_in_a_path_is_not_logged():
    assert safe_path("/reset/Zx9_abcdefghijklmnopqrstuvwxyz0123") == "/reset/…"
    assert safe_path("/hr/applications/12/resume") == "/hr/applications/12/resume"


def test_an_unhandled_error_is_logged_without_personal_data(caplog):
    from fastapi.testclient import TestClient

    from backend.main import app

    path = "/__test_fault_for_logging"
    if not any(getattr(r, "path", None) == path for r in app.routes):
        def fault():
            raise RuntimeError("candidate someone@example.org could not be saved")
        app.add_api_route(path, fault, methods=["GET"])
    logger = logging.getLogger("recruitai")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.ERROR, logger="recruitai"):
            r = TestClient(app, raise_server_exceptions=False).get(path)
    finally:
        logger.removeHandler(caplog.handler)
    assert r.status_code == 500 and "example.org" not in r.text
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert "unhandled_error" in joined and "RuntimeError" in joined and "example.org" not in joined

# --- found in the review before commit ---------------------------------------


def test_a_mistyped_key_is_reported_at_start_up_not_on_the_first_upload(monkeypatch):
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "not-a-key")
    with pytest.raises(storage.KeyMissing, match="not a valid key"):
        storage.check_key()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    storage.check_key()  # no key at all is allowed: files are then stored as uploaded


def test_an_emailed_resume_that_cannot_be_opened_is_held_for_a_person(key, session, monkeypatch):
    from backend import inbox
    from backend.models import InboxMessage

    o = _opening(session)
    _, path = storage.save_resume("cv.docx", _docx())
    from datetime import datetime, timezone

    row = InboxMessage(message_id="<made-up@example.org>", received_at=datetime.now(timezone.utc), sender="sender@example.org", subject="Application",
                       kind=inbox.APPLICATION, classified_by="rule:test", status=inbox.NEEDS_JOB_MATCH,
                       attachment_name="cv.docx", attachment_path=str(path))
    session.add(row)
    session.flush()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    inbox.import_into(session, row, o)
    assert row.status == inbox.NEEDS_JOB_MATCH and "could not be opened" in row.note
    assert row.application_id is None


def test_an_error_names_only_our_own_code():
    import json as _json

    try:
        _json.loads("{not json")  # the error is raised inside the standard library
    except ValueError as exc:
        text = describe_error(exc)
    assert "test_backend_protection.py" in text and "decoder.py" not in text
