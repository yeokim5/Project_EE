"""Deterministic citation locator.

Derives the on-page bounding rectangle(s) for a stored source quote so a viewer
can draw a pixel-accurate highlight over the supporting text. The LLM never
produces coordinates: the bbox is *derived* from the already-stored page number
plus quote plus the original PDF, so this module is purely additive and does not
touch the extraction path or recorded/cassette mode.

Coordinates use PyMuPDF's top-left origin in PDF points, which lines up with CSS
top-left origin. The locator also returns the page size so a renderer can scale
the rectangles to any zoom by expressing them as fractions of the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True)
class EvidenceRect:
    """One highlight rectangle in PDF points, top-left origin."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class EvidenceLocation:
    """Located highlight rectangles for a quote on its cited page."""

    page_number: int
    page_width: float
    page_height: float
    rects: list[EvidenceRect]

    @property
    def matched(self) -> bool:
        return bool(self.rects)


def locate_evidence_bbox(
    pdf_path: Path | str,
    page_number: int,
    quote: str,
) -> EvidenceLocation:
    """Locate the rectangle(s) covering ``quote`` on the cited page.

    Tries an exact search first, then progressively looser fallbacks so a quote
    whose whitespace differs from the PDF layout, or that wraps across lines,
    still resolves. Returns an empty rect list (``matched == False``) when no
    grounding is found, rather than guessing.
    """

    path = Path(pdf_path)
    page_index = page_number - 1
    with fitz.open(path) as document:
        if page_index < 0 or page_index >= document.page_count:
            return EvidenceLocation(page_number, 0.0, 0.0, [])
        page = document[page_index]
        width = float(page.rect.width)
        height = float(page.rect.height)
        rects = _search_page(page, quote)
        return EvidenceLocation(page_number, width, height, rects)


def _search_page(page: fitz.Page, quote: str) -> list[EvidenceRect]:
    cleaned = _normalize_ws(quote)
    if not cleaned:
        return []

    # 1) Exact phrase, then whitespace-normalized phrase.
    for needle in _dedupe([quote.strip(), cleaned]):
        found = _to_rects(page.search_for(needle))
        if found:
            return found

    # 2) Long quotes often wrap or contain layout noise. Search the longest
    #    word-runs of the quote and union their rectangles. This keeps the
    #    highlight tight around real, contiguous text rather than the whole page.
    words = cleaned.split(" ")
    for span in (8, 6, 4):
        if len(words) < span:
            continue
        unioned: list[EvidenceRect] = []
        for start in range(0, len(words) - span + 1, span):
            chunk = " ".join(words[start : start + span])
            unioned.extend(_to_rects(page.search_for(chunk)))
        if unioned:
            return unioned

    # 3) Last resort: anchor on the most distinctive number token in the quote.
    number = _longest_number(cleaned)
    if number:
        return _to_rects(page.search_for(number))
    return []


def _to_rects(quads: list) -> list[EvidenceRect]:
    rects: list[EvidenceRect] = []
    for quad in quads:
        rect = quad.rect if hasattr(quad, "rect") else quad
        rects.append(
            EvidenceRect(
                x0=float(rect.x0),
                y0=float(rect.y0),
                x1=float(rect.x1),
                y1=float(rect.y1),
            )
        )
    return rects


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _longest_number(text: str) -> str | None:
    tokens = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    if not tokens:
        return None
    return max(tokens, key=len)
