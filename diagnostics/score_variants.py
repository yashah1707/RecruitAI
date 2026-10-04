"""Score whatever variant rows exist. No API calls. Prints counts only.

Reads diagnostics/variant_<name>.csv (written incrementally by
table_variants.py) and scores each variant against the answer key on the
PAIRED set: resumes that extracted successfully in all three variants, so the
comparison is like for like. Says plainly when that set is partial.

Usage: python diagnostics/score_variants.py <resume_folder> <answer_key.xlsx>
"""
from __future__ import annotations

import csv
import difflib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber
import pymupdf

from app.excel_writer import export_name
from app.result_cache import cache_key
from app.run_stats import is_failed_row
from diagnostics.table_variants import RUN_LOG, VARIANTS, key_filenames, latest_rows, texts_for
from tools.score_accuracy import load_key, values_match

FOLDER = Path(sys.argv[1])
KEY_PATH = Path(sys.argv[2])

key = load_key(KEY_PATH)
filenames = key_filenames(KEY_PATH)
index = {fn: i for i, fn in enumerate(filenames, 1)}
rows = {v: latest_rows(v) for v in VARIANTS}
ok = {v: {fn for fn, r in rows[v].items() if not is_failed_row(r)} for v in VARIANTS}
paired = set(filenames)
for v in VARIANTS:
    paired &= ok[v]

print("== coverage ==")
for v in VARIANTS:
    print(f"{v:<9} extracted {len(ok[v])}/{len(filenames)} resumes   failed rows: {len(rows[v]) - len(ok[v])}")
partial = "" if len(paired) == len(filenames) else "   <-- PARTIAL RESULT"
print(f"paired set (extracted in all three): {len(paired)}/{len(filenames)} resumes{partial}")
print(f"paired file#: {sorted(index[f] for f in paired)}")


def score(v: str):
    per_field: dict[str, list[int]] = defaultdict(list)
    wrong_by_file: dict[int, int] = defaultdict(int)
    for (fn, field), expected in key.items():
        if fn not in paired or expected.strip().upper() == "UNCLEAR":
            continue
        got = rows[v][fn].get(export_name(field)) or rows[v][fn].get(field) or ""
        hit = int(values_match(expected, got))
        per_field[field].append(hit)
        if not hit:
            wrong_by_file[index[fn]] += 1
    return per_field, wrong_by_file


if paired:
    print(f"\n== accuracy on the paired set ({len(paired)}/{len(filenames)} resumes) ==")
    scored = {v: score(v) for v in VARIANTS}
    for v in VARIANTS:
        pf, wrong = scored[v]
        hits, total = sum(sum(h) for h in pf.values()), sum(len(h) for h in pf.values())
        print(f"{v:<9} {hits}/{total} = {100 * hits / total:.1f}%   wrong fields by file#: {dict(sorted(wrong.items()))}")

    print("\n== fields whose score differs from baseline ==")
    base = scored["baseline"][0]
    any_diff = False
    for v in ("sorted", "tables"):
        for field in sorted(base):
            b, s = sum(base[field]), sum(scored[v][0][field])
            if b != s:
                any_diff = True
                print(f"  {v:<7} {field:<28} baseline {b}/{len(base[field])} -> {s}/{len(base[field])}")
    if not any_diff:
        print("  none")

print("\n== are the variants really different inputs? (cache key per variant) ==")
print(f"{'file#':<7}{'sorted==baseline':<18}{'tables==baseline':<18}{'distinct keys'}")
shared = 0
for fn in filenames:
    t = texts_for(FOLDER / fn)
    keys = {v: cache_key(t[v], "p", "m") for v in VARIANTS}
    s_same, t_same = keys["sorted"] == keys["baseline"], keys["tables"] == keys["baseline"]
    shared += s_same + t_same
    print(f"{index[fn]:<7}{str(s_same):<18}{str(t_same):<18}{len(set(keys.values()))}")
print(f"variant texts identical to baseline (would share a cache entry): {shared} of {2 * len(filenames)}")


def multi_column_pages(pdf_path: Path) -> int:
    """Pages with text blocks in two well-separated x positions that overlap vertically."""
    n = 0
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            blocks = [b for b in page.get_text("blocks") if b[6] == 0 and len(b[4].strip()) > 25]
            left = [b for b in blocks if b[0] < page.rect.width * 0.4]
            right = [b for b in blocks if b[0] > page.rect.width * 0.45]
            if any(l[1] < r[3] and r[1] < l[3] for l in left for r in right):
                n += 1
    return n


def nontable_words(pdf_path: Path, sort: bool) -> list[str]:
    words: list[str] = []
    with pymupdf.open(pdf_path) as doc, pdfplumber.open(pdf_path) as pl:
        for page, ppage in zip(doc, pl.pages):
            boxes = [t.bbox for t in ppage.find_tables()]
            for w in page.get_text("words", sort=sort):
                cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
                if not any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in boxes):
                    words.append(w[4])
    return words


print("\n== does sort=True change NON-table content? (all 12, no API needed) ==")
print(f"{'file#':<7}{'layout':<12}{'mc_pages':<10}{'words':<8}{'same_words':<12}{'order_similarity'}")
for fn in filenames:
    pdf = FOLDER / fn
    mc = multi_column_pages(pdf)
    a, b = nontable_words(pdf, False), nontable_words(pdf, True)
    sim = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    print(f"{index[fn]:<7}{'multi-col' if mc else 'single-col':<12}{mc:<10}{len(a):<8}"
          f"{str(sorted(a) == sorted(b)):<12}{sim:.3f}")

if RUN_LOG.exists():
    print("\n== API errors per logged run ==")
    with RUN_LOG.open(newline="", encoding="utf-8") as f:
        runs = list(csv.DictReader(f))
    for r in runs:
        print(f"  {r['finished_at']}  requests={r['requests']} http_503={r['http_503']} "
              f"other_errors={r['other_errors']} failed_rows={r['failed_rows']}")
    print(f"  total logged: requests={sum(int(r['requests']) for r in runs)} "
          f"http_503={sum(int(r['http_503']) for r in runs)}")
