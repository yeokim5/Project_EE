"""PDF ingestion helpers for source-document page text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class PageText:
    """Text extracted from one physical PDF page."""

    source_file: str
    page_number: int
    text: str
    char_count: int


def read_pdf_pages(
    pdf_path: Path | str, page_numbers: set[int] | None = None
) -> list[PageText]:
    """Return page-level text using pdfplumber's 1-based physical page numbers."""

    path = Path(pdf_path)
    pages: list[PageText] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if page_numbers is not None and page.page_number not in page_numbers:
                continue
            text = page.extract_text() or ""
            pages.append(
                PageText(
                    source_file=str(path),
                    page_number=page.page_number,
                    text=text,
                    char_count=len(text),
                )
            )
    return pages


def read_pdf_metadata(pdf_path: Path | str) -> dict[str, str]:
    """Return string-valued PDF metadata for deterministic enrichment."""

    with pdfplumber.open(Path(pdf_path)) as pdf:
        return {
            str(key): str(value)
            for key, value in (pdf.metadata or {}).items()
            if value is not None
        }
