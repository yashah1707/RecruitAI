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
from app.resume_text import UnreadableResumeError, extract_text
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
    if config.LLM_PROVIDER == "gemini":
        from llm.providers.gemini_provider import GeminiProvider

        return GeminiProvider()
    from llm.providers.ollama_provider import OllamaProvider

    return OllamaProvider()


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
        "Extracts fields only — no eligibility decision, no rank/score."
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

    review_count = int(df["needs_review"].sum())
    st.subheader("Preview")
    st.caption(
        f"{len(df)} row(s), {review_count} flagged for review. "
        "Sort or filter any column yourself — there is no rank or score column."
    )
    st.dataframe(df, use_container_width=True, height=420)

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
