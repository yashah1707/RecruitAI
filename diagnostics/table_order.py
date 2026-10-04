"""Does PyMuPDF's page text keep table rows (and cells within a row) in order?

For each pdfplumber-detected table, locate every non-empty cell's text inside
PyMuPDF's page text and check the positions only ever increase in row-major
order. Prints counts only -- no resume content.
"""
import re
import sys
from pathlib import Path

import pdfplumber
import pymupdf

SRC = Path(sys.argv[1])
pdfs = sorted(SRC.glob("*.pdf"))


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


for i in (4, 14, 18, 20):
    p = pdfs[i - 1]
    with pymupdf.open(p) as mu, pdfplumber.open(p) as pl:
        for pn, (mpage, ppage) in enumerate(zip(mu, pl.pages), 1):
            page_text = norm(mpage.get_text())
            for t in ppage.find_tables():
                rows = t.extract()
                cells = [norm(c) for r in rows for c in r if c and len(norm(c)) >= 4]
                found = last = backwards = missing = 0
                for c in cells:
                    pos = page_text.find(c)
                    if pos < 0:
                        missing += 1
                        continue
                    found += 1
                    if pos < last:
                        backwards += 1
                    last = max(last, pos)
                if len(cells) >= 4:
                    print(f"id{i} p{pn} table_cells={len(cells)} found={found} "
                          f"out_of_order={backwards} not_found_contiguous={missing}")
