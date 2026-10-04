"""Table-order experiment on the answer-key resumes: the extraction step.

Variants of the text sent to the model:
  baseline  PyMuPDF get_text()                       (what the app does today)
  sorted    PyMuPDF get_text(sort=True)
  tables    baseline + pdfplumber tables appended as row-ordered text

Every (resume, variant) result is appended to diagnostics/variant_<name>.csv
the moment it completes, so a run that is stopped loses nothing. A rerun skips
pairs that already have a successful row and retries the ones that failed.
Scoring is a separate step: diagnostics/score_variants.py.

Prints counts only: files are shown as indexes into the answer key.

Usage:
  python diagnostics/table_variants.py <resume_folder> <answer_key.xlsx>
      [--files 3,8,11]     only these answer-key indexes
      [--limit N]          at most N resumes that still have work to do
      [--max-seconds S]    stop cleanly before starting a call after S seconds
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber
import pymupdf

from app.excel_writer import COLUMNS, build_row
from app.result_cache import ResultCache, cache_key
from app.run_stats import is_failed_row
from llm.interface import ExtractionFailure, ResumeRecord
from tools.score_accuracy import load_key

OUT = Path(__file__).parent
VARIANTS = ("baseline", "sorted", "tables")
RUN_LOG = OUT / "variant_runs.csv"


def variant_csv(variant: str) -> Path:
    return OUT / f"variant_{variant}.csv"


def key_filenames(key_path: Path) -> list[str]:
    return sorted({fn for fn, _ in load_key(key_path)})


def table_rows_text(pdf_path: Path) -> str:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.find_tables():
                for row in table.extract():
                    cells = [(c or "").replace("\n", " ").strip() for c in row]
                    if any(cells):
                        lines.append(" | ".join(cells))
                lines.append("")
    return "\n".join(lines).strip()


def texts_for(pdf_path: Path) -> dict[str, str]:
    with pymupdf.open(pdf_path) as doc:
        baseline = "\n".join(p.get_text() for p in doc)
        sorted_ = "\n".join(p.get_text(sort=True) for p in doc)
    tables = table_rows_text(pdf_path)
    with_tables = baseline + (f"\n\n--- TABLES (row order) ---\n{tables}" if tables else "")
    return {"baseline": baseline, "sorted": sorted_, "tables": with_tables}


def latest_rows(variant: str) -> dict[str, dict]:
    """The most recent row per file -- a retried failure supersedes the old row."""
    path = variant_csv(variant)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["source_filename"]: r for r in csv.DictReader(f)}


def append_row(variant: str, record: ResumeRecord) -> None:
    path = variant_csv(variant)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(COLUMNS))
        if new:
            w.writeheader()
        w.writerow(build_row(record))


class _Counter(logging.Handler):
    """Counts requests and 503s from the provider's own log lines."""

    def __init__(self) -> None:
        super().__init__()
        self.requests = 0
        self.unavailable = 0
        self.other_errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("sending_resume_to_gemini"):
            self.requests += 1
        elif msg.startswith("gemini_api_error"):
            if "code=503" in msg:
                self.unavailable += 1
            else:
                self.other_errors += 1
        elif msg.startswith("gemini_request_error"):
            self.other_errors += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("answer_key", type=Path)
    ap.add_argument("--files", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-seconds", type=float, default=0)
    args = ap.parse_args()

    from llm.providers.gemini_provider import GeminiProvider

    counter = _Counter()
    logging.getLogger("recruitai.gemini_provider").addHandler(counter)
    logging.getLogger("recruitai.gemini_provider").setLevel(logging.INFO)

    filenames = key_filenames(args.answer_key)
    index = {fn: i for i, fn in enumerate(filenames, 1)}
    wanted = {int(x) for x in args.files.split(",") if x.strip()} or set(index.values())

    provider = GeminiProvider()
    cache = ResultCache()
    done = {v: {fn for fn, r in latest_rows(v).items() if not is_failed_row(r)} for v in VARIANTS}
    start = time.monotonic()
    worked = cached = extracted = failed = 0
    stopped_early = False

    for fn in filenames:
        if index[fn] not in wanted:
            continue
        todo = [v for v in VARIANTS if fn not in done[v]]
        if not todo:
            continue
        if args.limit and worked >= args.limit:
            break
        worked += 1
        texts = texts_for(args.folder / fn)
        for v in todo:
            k = cache_key(texts[v], provider.prompt_version, provider.cache_model_id)
            hit = cache.get(k)
            if hit is not None:
                cached += 1
                append_row(v, ResumeRecord(source_filename=fn, processed_at=datetime.now(), result=hit))
                continue
            if args.max_seconds and time.monotonic() - start > args.max_seconds:
                stopped_early = True
                break
            try:
                result = provider.extract_fields(texts[v])
            except ExtractionFailure as exc:
                failed += 1
                append_row(v, ResumeRecord.failed(fn, f"extraction failed: {exc}", failure_kind=exc.kind))
                print(f"  file #{index[fn]} [{v}] FAILED kind={exc.kind}", flush=True)
                continue
            extracted += 1
            cache.put(k, result)
            append_row(v, ResumeRecord(source_filename=fn, processed_at=datetime.now(), result=result))
        if stopped_early:
            print(f"stopped at the time budget during file #{index[fn]}", flush=True)
            break
        print(f"file #{index[fn]} done", flush=True)

    new = not RUN_LOG.exists()
    with RUN_LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["finished_at", "requests", "http_503", "other_errors", "extracted", "from_cache", "failed_rows"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), counter.requests, counter.unavailable,
                    counter.other_errors, extracted, cached, failed])
    print(f"\nthis run: requests={counter.requests} http_503={counter.unavailable} "
          f"other_errors={counter.other_errors} extracted={extracted} from_cache={cached} failed_rows={failed}")


if __name__ == "__main__":
    main()
