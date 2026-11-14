"""Shared by every test: no test may reach a real mail server, use the real
encryption key, or write into the application's own log.

The project's own `.env` is loaded when the settings are imported, and on a
developer's machine it can name a working mail account and the key that
protects stored resumes. Tests that need such settings set made-up ones
themselves.
"""

import os
import tempfile

import pytest

# Before anything imports the settings: the tests' log goes to a scratch folder, not to logs/.
os.environ["LOG_DIR"] = os.path.join(tempfile.gettempdir(), "recruitai-test-logs")


@pytest.fixture(autouse=True)
def _no_real_mail(monkeypatch):
    from backend import settings

    for name in ("SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_REDIRECT_TO", "IMAP_HOST",
                 "DATA_ENCRYPTION_KEY"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "ACKNOWLEDGE_APPLICATIONS", True)
