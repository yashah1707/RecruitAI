"""Turn an uploaded resume file into plain text.

The full design assumes resume text arrives already converted upstream by a
Reader Agent that doesn't exist yet in this slice, so this module is
deliberately dumb: PDF and DOCX in, plain text out, or a clean failure. No
OCR — a scanned/image-only PDF is a failure, not a silent empty extraction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import pdfplumber
import pymupdf
from docx import Document

logger = logging.getLogger("recruitai.resume_text")

# Below this many non-whitespace characters, treat extraction as having failed
# rather than trust a near-empty result (e.g. a scanned PDF with one stray
# text layer artifact).
MIN_TEXT_CHARS = 20


class UnreadableResumeError(Exception):
    """The file could not be turned into usable text.

    Raised for corrupted files, unsupported formats, and text-free (scanned
    image) PDFs alike — callers must not call the LLM on empty text, and must
    still produce a row with this message as `parse_error`.
    """


class ScannedPdfError(UnreadableResumeError):
    """Every PDF reader opened the file fine and found no text in it.

    That is a scanned or image-only document, not a broken one. It needs a
    person (or OCR, which this tool deliberately does not do), and it must
    say so rather than read like a failed extraction.
    """


SCANNED_LABEL = "scanned or image-only, review manually"


@dataclass
class ResumeText:
    text: str
    source_filename: str


def _extract_pdf_pymupdf(fileobj: BinaryIO) -> str:
    fileobj.seek(0)
    with pymupdf.open(stream=fileobj.read(), filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _extract_pdf_pdfplumber(fileobj: BinaryIO) -> str:
    fileobj.seek(0)
    with pdfplumber.open(fileobj) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _extract_pdf(fileobj: BinaryIO) -> str:
    """PyMuPDF first, pdfplumber as a fallback.

    Measured across 23 real faculty resumes, PyMuPDF was never worse and was
    dramatically better on the layouts that drop inter-word spaces: three
    files went from ~180-250 glued-token-per-1000 down to under 6. Glued text
    ("PhDScholarinComputerScienceandEngineering") makes the evidence column
    unreadable for a human reviewer, which is the whole point of that column.

    pdfplumber stays as a fallback because the two libraries fail on
    different malformed files, and a second opinion costs milliseconds
    against an LLM call measured in seconds.
    """
    errors: list[str] = []
    opened_without_error = 0
    for name, extractor in (("pymupdf", _extract_pdf_pymupdf), ("pdfplumber", _extract_pdf_pdfplumber)):
        try:
            text = extractor(fileobj)
        except Exception as exc:  # each library raises its own error types
            errors.append(f"{name}: {exc}")
            continue
        if len(text.strip()) >= MIN_TEXT_CHARS:
            return text
        opened_without_error += 1
        errors.append(f"{name}: no text layer")
    if opened_without_error == 2:
        # Both libraries read the file and agree there is nothing to read.
        raise ScannedPdfError(f"{SCANNED_LABEL} ({'; '.join(errors)})")
    raise UnreadableResumeError(f"could not read PDF ({'; '.join(errors)})")


def _extract_docx(fileobj: BinaryIO) -> str:
    try:
        doc = Document(fileobj)
    except Exception as exc:
        raise UnreadableResumeError(f"could not read DOCX: {exc}") from exc
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def extract_text(fileobj: BinaryIO, filename: str) -> ResumeText:
    """Read `fileobj` (a PDF or DOCX) and return its plain text.

    Raises UnreadableResumeError for unsupported extensions, corrupted files,
    or files with no extractable text (e.g. scanned images) — callers route
    that to a needs_review row with a parse_error, never a crash and never a
    call to the LLM on empty text.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        text = _extract_pdf(fileobj)
    elif suffix == ".docx":
        text = _extract_docx(fileobj)
    else:
        raise UnreadableResumeError(f"unsupported file type: {suffix or '(none)'}")

    if len(text.strip()) < MIN_TEXT_CHARS:
        raise UnreadableResumeError(
            "no extractable text found (possibly a scanned image with no text layer)"
        )
    return ResumeText(text=text, source_filename=filename)
