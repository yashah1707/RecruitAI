"""The application's log: one rotating file, plus the console, and never personal data.

Log lines name ids, states and kinds of failure, never a name, an address or a
resume's contents. An error's own message can carry such values (a database
error quotes the row it refused), so errors are logged by `describe_error`:
the type of the error and where in the code it happened, without its message.

The file is LOG_DIR/recruitai.log, rotated at 1 MB with five old files kept.
"""

from __future__ import annotations

import logging
import re
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend import settings

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_configured = False


def configure(level: int = logging.INFO) -> None:
    """Send the `recruitai` loggers to the log file and the console. Safe to call more than once."""
    global _configured
    if _configured:
        return
    log = logging.getLogger("recruitai")
    log.setLevel(level)
    log.propagate = False
    formatter = logging.Formatter(_FORMAT)
    try:
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(settings.LOG_DIR / "recruitai.log", maxBytes=1_000_000, backupCount=5,
                                           encoding="utf-8", delay=True)
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except OSError:
        pass  # a log folder that cannot be written must not stop the application; the console still gets it
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    log.addHandler(console)
    _configured = True


_SECRET_SEGMENT = re.compile(r"[A-Za-z0-9_\-]{24,}")


def safe_path(path: str) -> str:
    """A request path with long token-like segments (a password-reset link) blanked."""
    return _SECRET_SEGMENT.sub("…", path)


def describe_error(exc: BaseException) -> str:
    """The error's type and the innermost frames of our own code, without its message."""
    root = str(settings.PROJECT_ROOT.resolve()).lower()
    frames = [f for f in traceback.extract_tb(exc.__traceback__)
              if str(Path(f.filename).resolve()).lower().startswith(root) and "site-packages" not in f.filename]
    where = " <- ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(frames[-5:]))
    return f"{type(exc).__module__}.{type(exc).__name__} at {where or 'library code'}"
