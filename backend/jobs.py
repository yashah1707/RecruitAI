"""The background queue for the Reader.

An application is stored the moment it arrives; reading it can take a minute
and can fail for reasons that clear up later (the model is overloaded, the
day's quota is spent). So reading is a job: queued on arrival, run by
`python -m backend.worker` or by HR pressing "Process queue", and tried again
later when the model was unavailable.

Nothing in this module runs by itself. A job only executes when one of those
two things asks for it, which is what keeps model calls under a person's
control.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.result_cache import ResultCache
from backend import states
from backend.models import Application, Job
from backend.reader_service import read_application
from llm.interface import LLMProvider

logger = logging.getLogger("recruitai.jobs")

READ_APPLICATION = "READ_APPLICATION"
PENDING, RUNNING, DONE, FAILED = "PENDING", "RUNNING", "DONE", "FAILED"

# After this many unavailable-model attempts the job stops retrying and waits
# for a person. The application itself stays RECEIVED: its file is fine.
MAX_ATTEMPTS = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def retry_delay(attempts: int, reason: str) -> timedelta:
    """How long to wait before trying again.

    A spent daily quota will not come back in minutes, so it waits an hour.
    An overloaded model usually recovers quickly: 2, 4, 8 ... minutes, capped
    at an hour.
    """
    if "quota" in reason:
        return timedelta(hours=1)
    return timedelta(minutes=min(2 ** attempts, 60))


def enqueue_read(session: Session, application: Application, run_after: datetime | None = None) -> Job:
    """Queue the Reader for an application, unless a job is already waiting."""
    existing = session.scalar(
        select(Job).where(
            Job.application_id == application.application_id,
            Job.kind == READ_APPLICATION,
            Job.status.in_((PENDING, RUNNING)),
        )
    )
    if existing is not None:
        return existing
    job = Job(kind=READ_APPLICATION, application_id=application.application_id, status=PENDING,
              run_after=run_after or _now())
    session.add(job)
    session.flush()
    return job


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; PostgreSQL returns aware ones.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def run_next_job(
    session: Session, provider: LLMProvider, cache: ResultCache | None = None, now: datetime | None = None
) -> Job | None:
    """Run the oldest due job. Returns it, or None when nothing is due."""
    now = now or _now()
    job = session.scalar(
        select(Job)
        .where(Job.status == PENDING, Job.run_after <= now)
        .order_by(Job.run_after, Job.job_id)
        .limit(1)
        .with_for_update(skip_locked=True)  # two workers never take the same job (PostgreSQL)
    )
    if job is None:
        return None

    job.status = RUNNING
    job.attempts += 1
    job.run_after = now  # doubles as "started at", for spotting an interrupted run
    session.commit()  # visible to the HR page while the model is being called

    application = session.get(Application, job.application_id, with_for_update=True)
    if application is None or application.status != states.RECEIVED:
        # Someone read or withdrew it in the meantime. Nothing to do.
        job.status, job.finished_at = DONE, now
        job.note = f"skipped: application is {application.status if application else 'missing'}"
        session.flush()
        return job

    state = read_application(session, application, provider, cache)
    if state == states.RECEIVED:
        # The model was unavailable. The reason is in the last audit row.
        reason = (application.transitions[-1].note or "reader_unavailable")[:200]
        if job.attempts >= MAX_ATTEMPTS:
            job.status, job.finished_at = FAILED, now
            job.note = f"gave up after {job.attempts} attempts: {reason}"
        else:
            job.status = PENDING
            job.run_after = now + retry_delay(job.attempts, reason)
            job.note = f"retry after attempt {job.attempts}: {reason}"
    else:
        job.status, job.finished_at = DONE, now
        job.note = f"application is {state}"
    session.flush()
    logger.info("job_run id=%s application=%s status=%s attempts=%d", job.job_id, job.application_id, job.status, job.attempts)
    return job


def run_due_jobs(
    session: Session, provider: LLMProvider, cache: ResultCache | None = None,
    limit: int = 50, now: datetime | None = None,
) -> list[Job]:
    """Run due jobs one after another, up to `limit`. Each job runs once per call."""
    ran: list[Job] = []
    seen: set[int] = set()
    while len(ran) < limit:
        job = run_next_job(session, provider, cache, now)
        if job is None or job.job_id in seen:
            break
        seen.add(job.job_id)
        ran.append(job)
        session.commit()
    return ran


# A job left RUNNING this long was interrupted (the server stopped mid-read).
STALE_AFTER = timedelta(minutes=20)


def release_stale_jobs(session: Session, now: datetime | None = None) -> int:
    """Put interrupted jobs back in the queue so they are not stuck forever."""
    now = now or _now()
    stale = [
        j for j in session.scalars(select(Job).where(Job.status == RUNNING))
        if now - _aware(j.run_after) > STALE_AFTER
    ]
    for job in stale:
        application = session.get(Application, job.application_id)
        if application is not None and application.status == states.PARSING:
            states.transition(session, application, states.RECEIVED, "system", note="reader_interrupted")
        job.status, job.run_after, job.note = PENDING, now, "requeued after an interrupted run"
    session.flush()
    return len(stale)


def requeue(session: Session, job: Job, now: datetime | None = None) -> bool:
    """Give a job that stopped retrying a fresh set of attempts. Returns whether it was requeued.

    Only a person does this, once whatever stopped the model (a spent quota,
    a wrong key) has been put right.
    """
    application = session.get(Application, job.application_id)
    if job.status != FAILED or application is None or application.status != states.RECEIVED:
        return False
    job.status, job.attempts, job.run_after, job.finished_at = PENDING, 0, now or _now(), None
    job.note = "requeued by HR"
    session.flush()
    return True


def summarise(ran: list[Job]) -> str:
    """What a run did to the applications, in words for the HR page."""
    if not ran:
        return "Nothing was waiting to be read."
    read = len([j for j in ran if j.note in ("application is EXTRACTED", "application is PENDING_REVIEW")])
    unreadable = len([j for j in ran if j.note == "application is FAILED"])
    waiting = len([j for j in ran if j.status == PENDING])
    stopped = len([j for j in ran if j.status == FAILED])
    parts = []
    if read:
        parts.append(f"Read {read} application(s).")
    if unreadable:
        parts.append(f"{unreadable} could not be read; see Needs attention.")
    if waiting:
        parts.append(f"{waiting} are waiting because the language model was unavailable; they will be retried.")
    if stopped:
        parts.append(f"{stopped} stopped retrying after repeated failures.")
    return " ".join(parts) or "Nothing was read."


def queue_summary(session: Session) -> dict[str, int]:
    rows = session.execute(select(Job.status, Job.job_id)).all()
    out = {PENDING: 0, RUNNING: 0, DONE: 0, FAILED: 0}
    for status, _ in rows:
        out[status] = out.get(status, 0) + 1
    return out


def due_count(session: Session, now: datetime | None = None) -> int:
    now = now or _now()
    return len(session.scalars(select(Job.job_id).where(Job.status == PENDING, Job.run_after <= now)).all())
