"""Counts-only diagnostics. Prints no resume content.

Part 1: ids 12, 13, 17 -- word/spacing metrics for PyMuPDF vs pdfplumber.
Part 2: table structure (rows x cols, page, vertical position) for resumes with tables.
ids are 1-based positions in sorted(*.pdf), same as extraction_check.py.
"""
import re
import sys
from pathlib import Path

import pdfplumber
import pymupdf

SRC = Path(sys.argv[1])
pdfs = sorted(SRC.glob("*.pdf"))


def metrics(text: str) -> dict:
    toks = text.split()
    return {
        "words": len(toks),
        "longest_token": max((len(t) for t in toks), default=0),
        "nonspace_chars": len(re.sub(r"\s", "", text)),
        "tokens_gt20": sum(len(t) > 20 for t in toks),
    }


print("== PART 1: spacing vs missing content ==")
for i in (12, 13, 17):
    p = pdfs[i - 1]
    with pymupdf.open(p) as d:
        mu = "\n".join(pg.get_text() for pg in d)
    with pdfplumber.open(p) as d:
        pl = "\n".join((pg.extract_text() or "") for pg in d.pages)
    print(i, "pymupdf  ", metrics(mu))
    print(i, "pdfplumber", metrics(pl))

print("\n== PART 2: table structure ==")
for i in (4, 14, 18, 20):
    p = pdfs[i - 1]
    with pdfplumber.open(p) as d:
        for pn, pg in enumerate(d.pages, 1):
            for t in pg.find_tables():
                rows = t.extract()
                ncols = max((len(r) for r in rows), default=0)
                nonempty = sum(1 for r in rows for c in r if c and c.strip())
                x0, top, x1, bottom = t.bbox
                print(f"id{i} page{pn} rows={len(rows)} cols={ncols} "
                      f"filled_cells={nonempty}/{len(rows) * ncols} top_y={round(top)}")
    # does PyMuPDF text order follow top-to-bottom within each page? (block y monotonic)
    with pymupdf.open(p) as d:
        for pn, pg in enumerate(d, 1):
            ys = [b[1] for b in pg.get_text("blocks") if b[6] == 0]
            inversions = sum(1 for a, b in zip(ys, ys[1:]) if b < a - 5)
            print(f"id{i} page{pn} pymupdf_blocks={len(ys)} y_order_breaks={inversions}")
