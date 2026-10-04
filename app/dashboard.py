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
from app.excel_writer import build_rows, date_as_stated, date_text, rows_for_display, write_workbook
from app.result_cache import ResultCache, cache_key
from app.resume_text import UnreadableResumeError, extract_text
from app.run_stats import retryable_filenames, stats_from_rows
from llm.confidence import evaluate
from llm.interface import ExtractionFailure, LLMProvider, ResumeRecord
from llm.postprocess import format_person_name
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


def process_resume(
    provider: LLMProvider, filename: str, data: bytes, cache: ResultCache | None = None
) -> ResumeRecord:
    """Extract one resume. Takes bytes, not a Streamlit upload object, so it
    is safe to call from a worker thread and reusable outside Streamlit.

    An unreadable file (scanned, corrupt) returns before the provider is
    reached -- the model is never called on empty text.
    """
    file_id = _log_id(filename)
    try:
        resume = extract_text(io.BytesIO(data), filename)
    except UnreadableResumeError as exc:
        logger.info("parse_error file=%s reason=%s", file_id, exc)
        return ResumeRecord.failed(filename, str(exc), failure_kind="unreadable")

    # Caching is opt-in per provider: only one that can name its model and
    # prompt version can be trusted to key an entry correctly.
    model_id = getattr(provider, "cache_model_id", None)
    key = (
        cache_key(resume.text, getattr(provider, "prompt_version", ""), model_id)
        if cache is not None and model_id
        else None
    )
    cached = cache.get(key) if key else None
    if cached is not None:
        logger.info("cache_hit file=%s", file_id)
        return ResumeRecord(source_filename=filename, processed_at=datetime.now(), result=cached)

    try:
        result = provider.extract_fields(resume.text)
    except ExtractionFailure as exc:
        logger.info("extraction_failed file=%s kind=%s reason=%s", file_id, exc.kind, exc)
        return ResumeRecord.failed(filename, f"extraction failed: {exc}", failure_kind=exc.kind)

    # A lighter-model result is a stopgap; caching it would pin the weaker
    # answer and stop a later run from replacing it with a proper one.
    if key and not result.lighter_model_fallback:
        cache.put(key, result)

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
    cache: ResultCache | None = None,
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
            records[i] = process_resume(provider, filename, data, cache)
            done += 1
            if on_progress:
                on_progress(done, len(uploads))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_resume, provider, filename, data, cache): i
                for i, (filename, data) in enumerate(uploads)
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    records[i] = future.result()
                except Exception as exc:  # a worker must never sink the batch
                    logger.warning("worker_failed file=%s", _log_id(uploads[i][0]))
                    records[i] = ResumeRecord.failed(
                        uploads[i][0], f"extraction failed: {exc}", failure_kind="api_unavailable"
                    )
                done += 1
                if on_progress:
                    on_progress(done, len(uploads))

    return [r for r in records if r is not None]


def retry_failed(
    provider: LLMProvider,
    records: list[ResumeRecord],
    uploads: dict[str, bytes],
    on_progress: Callable[[int, int], None] | None = None,
    cache: ResultCache | None = None,
) -> list[ResumeRecord]:
    """Re-run only the failures that could clear up, keeping everything else.

    Rows that already extracted are left untouched (no call, no cost);
    unreadable files and bad-config failures are skipped because a rerun
    cannot change them. Files no longer in `uploads` are left as they were.
    """
    wanted = set(retryable_filenames(records))
    todo = [(name, uploads[name]) for name in wanted if name in uploads]
    if not todo:
        return records
    fresh = {r.source_filename: r for r in process_batch(provider, todo, on_progress, cache)}
    return [fresh.get(r.source_filename, r) if r.source_filename in wanted else r for r in records]


# What each failure_kind means for the person looking at the banner.
FAILURE_KIND_HELP: dict[str, str] = {
    "api_unavailable": "Gemini was overloaded or unreachable — use Retry, it usually clears",
    "quota": "Gemini quota exhausted — wait for the reset or enable billing, then Retry",
    "bad_config": "API key or model name rejected — fix GEMINI_API_KEY / GEMINI_MODEL, restart",
}


def _failure_label(kind: str, message: str) -> str:
    """One line saying why a row failed and what to do about it."""
    if kind in FAILURE_KIND_HELP:
        return FAILURE_KIND_HELP[kind]
    if kind == "unreadable":
        # The message leads with the reason ("scanned or image-only, review
        # manually"); the bracketed detail after it is for the export.
        return message.split(" (")[0] or "unreadable file — review manually"
    return "failed — see the parse_error column"


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
                    "candidate_name": format_person_name(r.result.candidate_name.value),
                    # Same trimming as the main table: a bare "2026" must not
                    # show up here as a 1 January nobody wrote.
                    "phd_award_date": date_text(
                        date_as_stated(r.result.phd_award_date.value, r.result.phd_award_date_precision)
                    ) or "",
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
        + " · Extracts fields and computes a shortlisting score — no eligibility decision."
    )
    if model_name and "lite" in str(model_name).lower():
        st.info(
            "Running a **-lite** model. It has far more daily quota but was "
            "measurably less accurate on NET/SET status and PhD status in "
            "testing — fine for checking the UI, not for results you'll rely on.",
            icon="ℹ️",
        )
    cache = ResultCache()
    with st.sidebar:
        st.caption(f"Result cache: {cache.count()} resume(s) stored locally")
        if st.button("Clear result cache"):
            st.success(f"Cleared {cache.clear()} cached result(s).")
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

        records = process_batch(provider, uploads, on_progress, cache)
        progress.progress(1.0, text="Done")
        st.session_state["records"] = records

    records: list[ResumeRecord] | None = st.session_state.get("records")
    if not records:
        return

    rows = build_rows(records)
    # Same date convention as the workbook (DD-MM-YYYY), written out as text
    # because the table has no cell formats.
    df = pd.DataFrame(rows_for_display(rows))

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
            "Their rows are present but empty — see the `parse_error` and `failure_kind` columns. "
            "They are not included in the review count above.\n\n"
            + "\n".join(
                f"- {name} — {_failure_label(stats.failed_kinds.get(name, ''), stats.failed_reasons.get(name, ''))}"
                for name in stats.failed
            ),
            icon="🚫",
        )
        if retryable_filenames(records) and st.button(
            f"Retry {len(retryable_filenames(records))} failed resume(s)",
            help="Re-runs only the rows that failed for a reason that can clear up. "
            "Rows that already extracted are not sent again.",
        ):
            current = {f.name: f.getvalue() for f in uploaded_files}
            progress = st.progress(0.0, text="Retrying failed rows…")
            st.session_state["records"] = retry_failed(
                provider,
                records,
                current,
                lambda done, total: progress.progress(done / total, text=f"Retried {done} of {total}…"),
                cache,
            )
            st.rerun()
    # The ranking caveat is deliberately not a banner here. It still travels
    # with the workbook -- as a comment on the rank header and a full
    # how_scoring_works sheet -- which is what actually gets forwarded to
    # someone who hasn't been told what the score is. Repeating it on every
    # render for the person who built it just trains them to ignore it.
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
