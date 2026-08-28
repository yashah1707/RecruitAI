"""Streamlit UI: upload resumes, extract fields, preview, download the workbook.

This file is deliberately thin — it wires uploads to `resume_text`,
`llm.providers`, and `excel_writer`, all of which are plain Python and unaware
Streamlit exists. That keeps the extraction/Excel logic reusable if the
frontend is later rebuilt in React per the full design.
"""

from __future__ import annotations

import hashlib
import io
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable

# `streamlit run app/dashboard.py` puts this file's directory (app/) on
# sys.path, not the project root — add the root so `config` and `app.*` /
# `llm.*` imports resolve the same way they do under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

import config
from app.excel_writer import build_rows, write_workbook
from app.ranking import RANKING_DISCLAIMER
from app.resume_text import UnreadableResumeError, extract_text
from app.run_stats import stats_from_rows
from llm.confidence import evaluate
from llm.interface import ExtractionFailure, LLMProvider, ResumeRecord
from llm.providers.fake_provider import FakeProvider

# PII rule (brief section 1.6): only field-level confidence and routing
# decisions are logged, never raw resume text or extracted values. Real
# resume filenames often carry the candidate's name (e.g.
# "Jane_Doe_resume.pdf") — that counts as PII too, so logs never carry the
# filename itself, only a short one-way hash a human can't read a name out of.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("recruitai.dashboard")


def _log_id(filename: str) -> str:
    return hashlib.sha256(filename.encode("utf-8")).hexdigest()[:10]

st.set_page_config(page_title="RecruitAI — Resume Extraction", layout="wide")


@st.cache_resource
def get_provider() -> LLMProvider:
    if config.LLM_PROVIDER == "fake":
        return FakeProvider()
    from llm.providers.gemini_provider import GeminiProvider

    return GeminiProvider()


def process_resume(provider: LLMProvider, filename: str, data: bytes) -> ResumeRecord:
    """Extract one resume. Takes bytes, not a Streamlit upload object, so it
    is safe to call from a worker thread and reusable outside Streamlit."""
    file_id = _log_id(filename)
    try:
        resume = extract_text(io.BytesIO(data), filename)
    except UnreadableResumeError as exc:
        logger.info("parse_error file=%s reason=%s", file_id, exc)
        return ResumeRecord.failed(filename, str(exc))

    try:
        result = provider.extract_fields(resume.text)
    except ExtractionFailure as exc:
        logger.info("extraction_failed file=%s reason=%s", file_id, exc)
        return ResumeRecord.failed(filename, f"extraction failed: {exc}")

    outcome = evaluate(result)
    logger.info(
        "extracted file=%s needs_review=%s reasons=%s",
        file_id,
        outcome.needs_review,
        ",".join(outcome.reasons) or "-",
    )
    return ResumeRecord(source_filename=filename, processed_at=datetime.now(), result=result)


def process_batch(
    provider: LLMProvider,
    uploads: list[tuple[str, bytes]],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ResumeRecord]:
    """Extract every uploaded resume, respecting the provider's concurrency.

    Results come back in upload order regardless of completion order, so the
    spreadsheet rows always match the order the user uploaded files in.
    """
    workers = max(1, min(getattr(provider, "max_concurrency", 1), len(uploads)))
    records: list[ResumeRecord | None] = [None] * len(uploads)
    done = 0

    if workers == 1:
        for i, (filename, data) in enumerate(uploads):
            records[i] = process_resume(provider, filename, data)
            done += 1
            if on_progress:
                on_progress(done, len(uploads))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_resume, provider, filename, data): i
                for i, (filename, data) in enumerate(uploads)
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    records[i] = future.result()
                except Exception as exc:  # a worker must never sink the batch
                    logger.warning("worker_failed file=%s", _log_id(uploads[i][0]))
                    records[i] = ResumeRecord.failed(uploads[i][0], f"extraction failed: {exc}")
                done += 1
                if on_progress:
                    on_progress(done, len(uploads))

    return [r for r in records if r is not None]


def _render_manual_entry_panel(records: list[ResumeRecord]) -> None:
    """Surface the one field a human must supply, rather than find.

    phd_regulation (2009 vs 2016 Regulations) is effectively never written on
    a resume, so it comes back null for every PhD holder. As a blank cell
    among thirty other columns it reads like an absent optional field and
    gets skipped -- but it changes an eligibility outcome downstream, so it
    needs to look like an open question.

    The award date and institution are shown purely to save the reviewer a
    lookup. They are NOT used to infer the regulation year: the two
    Regulations overlap in time and only the awarding university can say
    which applied, so guessing it here would fabricate an eligibility input.
    """
    pending = [
        r
        for r in records
        if r.result and r.result.has_phd.value is True and r.result.phd_regulation.value is None
    ]
    if not pending:
        return

    st.subheader("Needs manual entry")
    st.caption(
        f"{len(pending)} PhD-holding candidate(s) need `phd_regulation` "
        "supplied by hand — resumes almost never state it, so it cannot be "
        "extracted. Check with the awarding university; do not infer it from "
        "the award year."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "source_filename": r.source_filename,
                    "candidate_name": r.result.candidate_name.value,
                    "phd_award_date": r.result.phd_award_date.value,
                    "awarding institution (from evidence)": (r.result.phd_award_date.evidence
                                                            or r.result.has_phd.evidence or ""),
                    "phd_regulation": "— enter 2009 or 2016 —",
                }
                for r in pending
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def main() -> None:
    st.title("RecruitAI — Resume → Excel Dashboard")
    provider = get_provider()
    # Show the actual model, not just the provider: with GEMINI_MODEL=auto the
    # model rotates daily and fails over on quota, so "which model produced
    # this run" is not something the user can infer from config alone.
    model_name = getattr(provider, "model", None)
    st.caption(
        f"Provider: **{config.LLM_PROVIDER}**"
        + (f" · Model: **{model_name}**" if model_name else "")
        + f" · Confidence threshold: **{config.CONFIDENCE_THRESHOLD}** · "
        "Extracts fields and computes a shortlisting score — no eligibility decision."
    )
    if model_name and "lite" in str(model_name).lower():
        st.info(
            "Running a **-lite** model. It has far more daily quota but was "
            "measurably less accurate on NET/SET status and PhD status in "
            "testing — fine for checking the UI, not for results you'll rely on.",
            icon="ℹ️",
        )
    if config.LLM_PROVIDER == "gemini":
        st.warning(
            "Cloud provider active: resume text is sent to the Gemini API. "
            "On the free tier, Google's terms permit using submitted prompts "
            "for model training. Do not upload real candidate resumes here "
            "unless you've accepted that tradeoff.",
            icon="⚠️",
        )

    uploaded_files = st.file_uploader(
        "Upload faculty resumes (PDF or DOCX)",
        type=["pdf", "docx"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info("Upload one or more resumes to begin.")
        return

    if st.button(f"Extract fields from {len(uploaded_files)} resume(s)", type="primary"):
        # Read the bytes here, on Streamlit's own thread, so the workers only
        # ever touch plain data.
        uploads = [(f.name, f.getvalue()) for f in uploaded_files]
        workers = max(1, min(getattr(provider, "max_concurrency", 1), len(uploads)))
        progress = st.progress(0.0, text=f"Starting ({workers} at a time)…")

        def on_progress(done: int, total: int) -> None:
            progress.progress(done / total, text=f"Processed {done} of {total}…")

        records = process_batch(provider, uploads, on_progress)
        progress.progress(1.0, text="Done")
        st.session_state["records"] = records

    records: list[ResumeRecord] | None = st.session_state.get("records")
    if not records:
        return

    rows = build_rows(records)
    df = pd.DataFrame(rows)

    # Counted by app.run_stats, the same code tools/verify_run.py audits the
    # downloaded export with. Computing it here independently is how a clean
    # summary could once be shown for a run the audit would have flagged.
    stats = stats_from_rows(rows)

    st.subheader("Preview")
    st.caption(
        f"{stats.extracted_count} of {stats.total_rows} resume(s) extracted, "
        f"ranked best-first — {stats.review_count_excluding_failures} flagged for review. "
        "Sort or filter any column yourself to compare on a single factor."
    )
    if stats.failure_count:
        # Never let a failure hide inside the review count: those rows have
        # nothing in them to review, and a reviewer who cannot see them will
        # assume every uploaded resume was read.
        st.error(
            f"**{stats.failure_count} of {stats.total_rows} resume(s) produced no data.** "
            "Their rows are present but empty — see the `parse_error` column. "
            "They are not included in the review count above.\n\n"
            + "\n".join(f"- {name}" for name in stats.failed),
            icon="🚫",
        )
    st.warning(RANKING_DISCLAIMER, icon="⚠️")
    st.dataframe(df, use_container_width=True, height=420)
    _render_manual_entry_panel(records)

    workbook_path = write_workbook(records, "recruitai_extraction.xlsx")
    with open(workbook_path, "rb") as f:
        st.download_button(
            "Download Excel workbook",
            data=f.read(),
            file_name=workbook_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
