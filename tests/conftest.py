"""Shared by every test: no test may reach a real mail server.

The project's own `.env` is loaded when the settings are imported, and on a
developer's machine it can name a working mail account. Tests that need mail
settings set made-up ones themselves.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_real_mail(monkeypatch):
    from backend import settings

    for name in ("SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_REDIRECT_TO", "IMAP_HOST"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "ACKNOWLEDGE_APPLICATIONS", True)
