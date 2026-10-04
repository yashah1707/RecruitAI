"""Stage 1-2 diagnostic: compare PyMuPDF and pdfplumber on the sample resumes.

No LLM calls. Prints aggregate stats only (no resume content, no names) and
writes them to diagnostics/extraction_stats.csv (gitignored via *.csv).
Files are identified by index, not name.
"""
import csv
import sys
from pathlib import Path

import fitz
import pdfplumber

SRC = Path(sys.argv[1])
OUT = Path(__file__).parent / "extraction_stats.csv"


def words(text: str) -> set[str]:
    return {w.lower() for w in text.split() if len(w) > 3}


rows = []
for i, pdf in enumerate(sorted(SRC.glob("*.pdf")), 1):
    with fitz.open(pdf) as doc:
        pages = len(doc)
        mu = "\n".join(p.get_text() for p in doc)
        images = sum(len(p.get_images()) for p in doc)
    with pdfplumber.open(pdf) as doc:
        pl = "\n".join((p.extract_text() or "") for p in doc.pages)
        tables = sum(len(p.find_tables()) for p in doc.pages)
    wm, wp = words(mu), words(pl)
    union = wm | wp
    rows.append({
        "id": i,
        "pages": pages,
        "pymupdf_chars": len(mu.strip()),
        "pdfplumber_chars": len(pl.strip()),
        "chars_per_page": round(len(mu.strip()) / max(pages, 1)),
        "images": images,
        "tables": tables,
        "word_overlap": round(len(wm & wp) / len(union), 2) if union else 0,
        "likely_scanned": len(mu.strip()) / max(pages, 1) < 200,
    })

with OUT.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)

for r in rows:
    print(r)
