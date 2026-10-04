"""The per-file result cache and the "retry failed" path.

Driven through a scripted FakeProvider, so a rerun's cost is observable as a
call count: the whole point is that only failed rows are sent again.
"""

from __future__ import annotations

import pytest

from app.dashboard import process_batch, retry_failed
from app.result_cache import ResultCache, cache_key
from app.run_stats import retryable_filenames, stats_from_rows
from app.excel_writer import build_rows
from llm.interface import ExtractionFailure
from llm.providers.fake_provider import CANNED_RESULTS, FakeProvider
from tests.test_batch_processing import _docx_bytes

UPLOADS = [(f"cv{i}.docx", _docx_bytes(f"Cand{i}")) for i in range(4)]
UPLOAD_MAP = dict(UPLOADS)


def _ok():
    return CANNED_RESULTS[0].model_copy(deep=True)


def _provider(script, **kwargs):
    return FakeProvider(script=script, cache_model_id="m1", prompt_version="p1", **kwargs)


@pytest.fixture
def cache(tmp_path):
    return ResultCache(tmp_path / "cache")


def test_key_changes_with_text_prompt_version_and_model():
    base = cache_key("resume text", "p1", "m1")
    assert base == cache_key("resume text", "p1", "m1")
    assert base != cache_key("resume text!", "p1", "m1")
    assert base != cache_key("resume text", "p2", "m1")
    assert base != cache_key("resume text", "p1", "m2")


def test_successes_are_cached_and_a_second_run_makes_no_calls(cache):
    p = _provider([_ok()] * 4)
    first = process_batch(p, UPLOADS, cache=cache)
    assert p.calls == 4 and cache.count() == 4

    p2 = _provider([])
    second = process_batch(p2, UPLOADS, cache=cache)
    assert p2.calls == 0
    assert [r.result.candidate_name.value for r in second] == [r.result.candidate_name.value for r in first]


def test_failures_are_never_cached(cache):
    p = _provider([ExtractionFailure("down", kind="api_unavailable")] * 4)
    records = process_batch(p, UPLOADS, cache=cache)
    assert all(r.result is None for r in records)
    assert cache.count() == 0


def test_changing_prompt_or_model_misses_the_cache(cache):
    process_batch(_provider([_ok()] * 4), UPLOADS, cache=cache)

    other_prompt = FakeProvider(script=[_ok()] * 4, cache_model_id="m1", prompt_version="p2")
    process_batch(other_prompt, UPLOADS, cache=cache)
    assert other_prompt.calls == 4

    other_model = FakeProvider(script=[_ok()] * 4, cache_model_id="m2", prompt_version="p1")
    process_batch(other_model, UPLOADS, cache=cache)
    assert other_model.calls == 4


def test_lighter_model_results_are_not_cached(cache):
    lite = _ok()
    lite.lighter_model_fallback = True
    process_batch(_provider([lite] * 4), UPLOADS, cache=cache)
    assert cache.count() == 0


def test_provider_without_cache_identity_bypasses_the_cache(cache):
    p = FakeProvider(script=[_ok()] * 4)  # no cache_model_id
    process_batch(p, UPLOADS, cache=cache)
    assert cache.count() == 0


def test_clear_removes_everything(cache):
    process_batch(_provider([_ok()] * 4), UPLOADS, cache=cache)
    assert cache.clear() == 4
    assert cache.count() == 0
    assert cache.clear() == 0


def test_a_damaged_entry_is_a_miss_not_a_crash(cache):
    process_batch(_provider([_ok()] * 4), UPLOADS, cache=cache)
    for path in cache.directory.glob("*.json"):
        path.write_text("{ not valid", encoding="utf-8")
    p = _provider([_ok()] * 4)
    records = process_batch(p, UPLOADS, cache=cache)
    assert p.calls == 4
    assert all(r.result is not None for r in records)


def test_cache_holds_hashes_not_resume_text(cache):
    process_batch(_provider([_ok()] * 4), UPLOADS, cache=cache)
    names = [p.name for p in cache.directory.glob("*.json")]
    assert all(len(n) == 69 for n in names)  # sha256 hex + ".json"
    assert not any("Cand" in n for n in names)


# --- retry failed -----------------------------------------------------------


def _mixed_run(cache):
    script = [
        _ok(),
        ExtractionFailure("overloaded", kind="api_unavailable"),
        _ok(),
        ExtractionFailure("out of quota", kind="quota"),
    ]
    return process_batch(_provider(script), UPLOADS, cache=cache)


def test_retry_reruns_only_the_failed_rows(cache):
    records = _mixed_run(cache)
    assert sorted(retryable_filenames(records)) == ["cv1.docx", "cv3.docx"]

    p = _provider([_ok(), _ok()])
    fixed = retry_failed(p, records, UPLOAD_MAP, cache=cache)

    assert p.calls == 2  # the two that failed; the two good rows were not sent
    assert all(r.result is not None for r in fixed)
    assert [r.source_filename for r in fixed] == [r.source_filename for r in records]
    assert retryable_filenames(fixed) == []


def test_retry_that_fails_again_keeps_the_row_failed_with_its_kind(cache):
    records = _mixed_run(cache)
    p = _provider([ExtractionFailure("still down", kind="api_unavailable"), _ok()])
    again = retry_failed(p, records, UPLOAD_MAP, cache=cache)

    still_failed = [r for r in again if r.result is None]
    assert len(still_failed) == 1
    assert still_failed[0].failure_kind == "api_unavailable"


def test_retry_skips_unreadable_and_bad_config_rows(cache):
    unreadable = ("scan.pdf", b"%PDF-1.4 garbage")
    uploads = UPLOADS[:1] + [unreadable]
    records = process_batch(_provider([_ok()]), uploads, cache=cache)
    assert records[1].failure_kind == "unreadable"
    assert retryable_filenames(records) == []

    p = _provider([])
    assert retry_failed(p, records, dict(uploads), cache=cache) == records
    assert p.calls == 0


def test_bad_config_failures_are_not_retried(cache):
    records = process_batch(_provider([ExtractionFailure("bad key", kind="bad_config")] * 4), UPLOADS, cache=cache)
    assert retryable_filenames(records) == []


def test_retry_does_nothing_when_the_originals_are_gone(cache):
    records = _mixed_run(cache)
    p = _provider([])
    assert retry_failed(p, records, {}, cache=cache) == records
    assert p.calls == 0


def test_run_stats_reports_each_failures_kind(cache):
    stats = stats_from_rows(build_rows(_mixed_run(cache)))
    assert stats.failed_kinds == {"cv1.docx": "api_unavailable", "cv3.docx": "quota"}
    assert "overloaded" in stats.failed_reasons["cv1.docx"]
