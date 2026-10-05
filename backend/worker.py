"""Run queued Reader jobs.

    python -m backend.worker            process what is due, then exit
    python -m backend.worker --loop     keep running, checking every 15 seconds

Each job that reaches the model is one LLM request (more if the model asks to
be retried). On a free-tier key that is a limited daily allowance, so this is
started deliberately, never automatically.
"""

from __future__ import annotations

import logging
import sys
import time

from sqlalchemy.orm import Session

from app.result_cache import ResultCache
from backend.db import get_engine
from backend.jobs import run_due_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("recruitai.worker")

POLL_SECONDS = 15


def main() -> None:
    from backend.main import get_provider

    provider = get_provider()
    cache = ResultCache()
    loop = "--loop" in sys.argv
    engine = get_engine()
    while True:
        with Session(engine) as session:
            ran = run_due_jobs(session, provider, cache)
            session.commit()
        if ran:
            logger.info("ran %d job(s)", len(ran))
        if not loop:
            break
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
