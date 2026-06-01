from pathlib import Path

from earnings_extractor.locate import EvidenceRect, locate_evidence_bbox

ROOT = Path(__file__).resolve().parents[1]
TESLA = ROOT / "assesment_info" / "TSLA-Q2-2025-Update.pdf"
CITI = ROOT / "assesment_info" / "citi_earnings_q12025.pdf"


def test_citi_title_quote_resolves_to_a_rect_on_page_one() -> None:
    location = locate_evidence_bbox(CITI, 1, "Citi First Quarter 2025 Earnings Call")

    assert location.matched is True
    assert location.page_number == 1
    assert location.page_width > 0 and location.page_height > 0
    assert location.rects
    # The title sits in the top band of the page (top-left origin).
    rect = location.rects[0]
    assert isinstance(rect, EvidenceRect)
    assert 0 <= rect.x0 < rect.x1 <= location.page_width
    assert 0 <= rect.y0 < rect.y1 <= location.page_height
    assert rect.y0 < location.page_height / 2


def test_tesla_statement_quote_resolves_on_cited_page() -> None:
    # A wrapped financial-statement line should still resolve via the chunked
    # fallback, returning at least one rectangle on the cited page.
    quote = "Total revenues 25,500 25,182 25,707 19,335 22,496"
    location = locate_evidence_bbox(TESLA, 4, quote)

    assert location.matched is True
    assert location.rects
    for rect in location.rects:
        assert 0 <= rect.x0 <= rect.x1 <= location.page_width
        assert 0 <= rect.y0 <= rect.y1 <= location.page_height


def test_absent_quote_returns_no_rects() -> None:
    location = locate_evidence_bbox(
        CITI, 1, "this exact phrase does not appear anywhere zzqq"
    )

    assert location.matched is False
    assert location.rects == []


def test_out_of_range_page_returns_empty_location() -> None:
    location = locate_evidence_bbox(CITI, 9999, "Citi")

    assert location.matched is False
    assert location.rects == []
