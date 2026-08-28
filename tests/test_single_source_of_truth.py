"""Guard: run counts may only be computed in app/run_stats.py.

This bug has now appeared twice, in two different disguises:

  1. A report claimed "0 extraction failures" for a run whose export
     contained failures -- a number quoted from memory rather than the file.
  2. The dashboard counted rows itself, so it could show a clean summary for
     a run the audit script would have flagged. Neither side knew.

Both were fixed by pointing everything at `app.run_stats`. This test is the
part meant to stop a third: it fails when any module outside `run_stats`
aggregates over `parse_error` or `needs_review` on its own.

Two deliberate choices, each from getting it wrong first:

* It parses with `ast` rather than matching text. A regex over neighbouring
  lines flagged `needs_review=True` on a single constructed record, and a
  guard that cries wolf on ordinary code gets suppressed -- worse than none.

* It watches pandas as hard as plain Python. The dashboard bug was
  `int(df["needs_review"].sum())`, and an earlier version of this guard
  caught that one only by luck: it missed boolean-mask indexing,
  `.shape[0]`, `.count()` and `.value_counts()` entirely. Native
  comprehensions were never the only shape this bug takes.

The file surface is an exclusion list, not an inclusion list, so a module
added in a new directory is covered the day it appears rather than the day
someone remembers to add the directory here. `config.py` sits at the repo
root and was silently outside an earlier inclusion-list version.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CANONICAL = REPO / "app" / "run_stats.py"

# Everything is scanned except these. Tests are exempt because they
# legitimately hand-build expected values to check the real counter against.
EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "tests", ".claude"}

FAILURE_TOKEN = "parse_error"
REVIEW_TOKEN = "needs_review"

# Anything that turns many rows into one answer -- Python builtins, pandas
# reductions, and the mask-producing helpers that always precede a count.
_AGGREGATING_NAMES = {
    # builtins
    "sum", "len", "any", "all", "max", "min", "Counter", "sorted",
    # pandas reductions / selections
    "count", "value_counts", "nunique", "mean", "median", "agg", "aggregate",
    "groupby", "query", "filter", "idxmax", "idxmin", "drop_duplicates",
    "notna", "notnull", "isna", "isnull", "tolist", "unique",
}

# `.shape[0]`, `.size`, `.empty` count rows without calling anything.
_SIZE_ATTRS = {"shape", "size", "empty"}


class _AggregationFinder(ast.NodeVisitor):
    """Collect aggregations whose source text mentions `token`.

    Ordinary single-row work -- assigning `row["parse_error"]`, reading
    `record.parse_error`, passing it to a constructor -- is deliberately left
    alone. Only turning a collection into a number is the thing that has to
    live in one place.
    """

    def __init__(self, source: str, token: str) -> None:
        self.source = source
        self.token = token
        self.hits: list[tuple[int, str]] = []

    def _segment(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.source, node) or ""

    def _record(self, node: ast.AST) -> None:
        if self.token in self._segment(node):
            line = self.source.splitlines()[node.lineno - 1].strip()
            self.hits.append((node.lineno, line))

    # comprehensions of every flavour
    def visit_ListComp(self, node):
        self._record(node)
        self.generic_visit(node)

    def visit_SetComp(self, node):
        self._record(node)
        self.generic_visit(node)

    def visit_DictComp(self, node):
        self._record(node)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node):
        self._record(node)
        self.generic_visit(node)

    def visit_Call(self, node):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in _AGGREGATING_NAMES:
            self._record(node)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        # Boolean-mask indexing: df[df.parse_error.notna()]. The giveaway is
        # that the index is an expression rather than a plain key -- a plain
        # key is row["parse_error"], which is single-row access and fine.
        index = node.slice
        if not isinstance(index, ast.Constant) and self.token in self._segment(index):
            self._record(node)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # df[mask].shape[0] / .size / .empty count without calling anything.
        if node.attr in _SIZE_ATTRS and self.token in self._segment(node.value):
            self._record(node)
        self.generic_visit(node)


def aggregating_mentions(source: str, token: str) -> list[tuple[int, str]]:
    finder = _AggregationFinder(source, token)
    finder.visit(ast.parse(source))
    # One aggregation matches on several nested nodes; report each line once.
    return sorted({hit for hit in finder.hits})


def _python_files():
    for path in REPO.rglob("*.py"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.resolve() == CANONICAL.resolve():
            continue
        yield path


def _offenders(token: str) -> list[str]:
    hits = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for line_no, snippet in aggregating_mentions(source, token):
            hits.append(f"{path.relative_to(REPO)}:{line_no}: {snippet}")
    return hits


def test_only_run_stats_aggregates_extraction_failures():
    offenders = _offenders(FAILURE_TOKEN)
    assert not offenders, (
        "these count extraction failures outside app/run_stats.py — import "
        "`is_failed_row` / `stats_from_rows` instead, so the product and the "
        "audit cannot disagree:\n  " + "\n  ".join(offenders)
    )


def test_only_run_stats_aggregates_the_review_count():
    offenders = _offenders(REVIEW_TOKEN)
    assert not offenders, (
        "these total up needs_review outside app/run_stats.py — use "
        "`stats_from_rows(...).review_count_excluding_failures`, which does "
        "not let a failed row masquerade as a reviewable one:\n  "
        + "\n  ".join(offenders)
    )


# --- the scanned surface must be everything, not a remembered list ----------

def test_the_scan_covers_every_directory_holding_python():
    """An inclusion list goes stale silently. This asserts the exclusion-list
    scan actually reaches every package in the repo -- including config.py at
    the root, which an earlier ("app", "llm", "tools") version missed."""
    scanned = {p.resolve() for p in _python_files()}
    expected = {
        p.resolve()
        for p in REPO.rglob("*.py")
        if not any(part in EXCLUDED_DIRS for part in p.parts)
        and p.resolve() != CANONICAL.resolve()
    }
    assert scanned == expected


def test_root_level_modules_are_scanned():
    scanned = {p.name for p in _python_files()}
    assert "config.py" in scanned, "root-level modules must be in the scanned surface"


def test_the_real_source_dirs_are_all_reached():
    scanned_dirs = {p.relative_to(REPO).parts[0] for p in _python_files()}
    for expected in ("app", "llm", "tools"):
        assert expected in scanned_dirs


# --- must catch: plain Python -----------------------------------------------

@pytest.mark.parametrize(
    "snippet",
    [
        "failed = [r for r in rows if r.get('parse_error')]",
        "n = sum(1 for r in rows if r['parse_error'])",
        "count = len([r for r in rows if r.get('parse_error')])",
        "bad = {r['source_filename'] for r in rows if r.get('parse_error')}",
    ],
)
def test_the_guard_catches_a_native_python_counter(snippet):
    source = f"def summarise(rows):\n    {snippet}\n    return 0\n"
    assert aggregating_mentions(source, FAILURE_TOKEN), f"missed: {snippet}"


# --- must catch: pandas, the shape the real dashboard bug took --------------

@pytest.mark.parametrize(
    "snippet,token",
    [
        # the actual bug that shipped
        ('total = int(df["needs_review"].sum())', REVIEW_TOKEN),
        # boolean-mask indexing, which an earlier guard missed entirely
        ('failed = df[df.parse_error.notna()]', FAILURE_TOKEN),
        ('n = df[df.parse_error.notna()].shape[0]', FAILURE_TOKEN),
        ('n = df[df["parse_error"].notna()].shape[0]', FAILURE_TOKEN),
        ('n = df.loc[df.needs_review].shape[0]', REVIEW_TOKEN),
        # reductions
        ('n = df["parse_error"].count()', FAILURE_TOKEN),
        ('n = df["parse_error"].notna().sum()', FAILURE_TOKEN),
        ('c = df.parse_error.value_counts()', FAILURE_TOKEN),
        ('flag = df["needs_review"].any()', REVIEW_TOKEN),
        ('n = df.query("parse_error.notna()").shape[0]', FAILURE_TOKEN),
        ('n = len(df[df["parse_error"].notna()])', FAILURE_TOKEN),
        ('g = df.groupby("parse_error").size()', FAILURE_TOKEN),
        ('n = df["needs_review"].nunique()', REVIEW_TOKEN),
    ],
)
def test_the_guard_catches_pandas_aggregation(snippet, token):
    """The dashboard bug was pandas, not a comprehension. A guard that only
    knows comprehensions would not have caught the bug it exists for."""
    source = f"def summarise(df):\n    {snippet}\n    return 0\n"
    assert aggregating_mentions(source, token), f"missed pandas pattern: {snippet}"


# --- must ignore: ordinary single-row work ----------------------------------

@pytest.mark.parametrize(
    "snippet",
    [
        "row['parse_error'] = _clean(record.parse_error)",
        "outcome = evaluate(record.result, record.parse_error, threshold)",
        "logger.info('parse_error file=%s', file_id)",
        "rec = ResumeRecord(source_filename=name, parse_error=None)",
        "score = CandidateScore(needs_review=True, review_reasons=None)",
        "if record.parse_error:\n        return None",
        "return {'parse_error': None, 'needs_review': False}",
    ],
)
def test_the_guard_leaves_ordinary_single_row_code_alone(snippet):
    """A guard that fires on normal code gets suppressed, which is worse than
    having no guard at all."""
    source = (
        "def handle(row, record, name, file_id, threshold, _clean, evaluate, "
        "logger, ResumeRecord, CandidateScore):\n    " + snippet + "\n"
    )
    assert not aggregating_mentions(source, FAILURE_TOKEN), f"false positive: {snippet}"
    assert not aggregating_mentions(source, REVIEW_TOKEN), f"false positive: {snippet}"


def test_the_canonical_module_exists_where_the_message_says_it_does():
    assert CANONICAL.exists(), f"{CANONICAL} is referenced by this guard but missing"


@pytest.mark.parametrize(
    "name", ["is_failed_row", "stats_from_rows", "stats_from_export", "assert_claim"]
)
def test_the_shared_helpers_stay_available(name):
    """These are what the failure message tells people to import."""
    import app.run_stats as rs

    assert hasattr(rs, name)
