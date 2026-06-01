from pathlib import Path

from earnings_extractor.classify import (
    classify_document,
    classify_page,
    select_extraction_pages,
)
from earnings_extractor.ingest import PageText, read_pdf_pages


def _page(number: int, text: str) -> PageText:
    return PageText(
        source_file="synthetic.pdf",
        page_number=number,
        text=text,
        char_count=len(text),
    )

ROOT = Path(__file__).resolve().parents[1]
TESLA = ROOT / "assesment_info" / "TSLA-Q2-2025-Update.pdf"
CITI = ROOT / "assesment_info" / "citi_earnings_q12025.pdf"


def test_tesla_update_deck_is_earnings_report() -> None:
    pages = read_pdf_pages(TESLA, page_numbers={1, 4})

    classification = classify_document(pages)

    assert classification.document_type == "earnings_report"


def test_citi_transcript_is_earnings_call_transcript() -> None:
    pages = read_pdf_pages(CITI, page_numbers={1, 3})

    classification = classify_document(pages)

    assert classification.document_type == "earnings_call_transcript"


def test_tesla_financial_summary_page_is_table_heavy() -> None:
    page = read_pdf_pages(TESLA, page_numbers={4})[0]

    classification = classify_page(page)

    assert classification.style == "table_heavy"


def test_citi_transcript_page_is_narrative() -> None:
    page = read_pdf_pages(CITI, page_numbers={3})[0]

    classification = classify_page(page)

    assert classification.style == "narrative"


def test_non_source_admin_pdf_is_not_misclassified_as_transcript() -> None:
    # An assignment / admin PDF can describe an earnings-call transcript without
    # being a source document. The non-source marker must force "unknown" so it
    # is never extracted as if it were an earnings filing.
    pages = [
        _page(
            1,
            "Take home assignment\n"
            "Operator: prepared remarks from the earnings call transcript are "
            "described below as the problem statement for candidate review.",
        )
    ]

    classification = classify_document(pages)

    assert classification.document_type == "unknown"


def test_back_loaded_letter_classifies_as_earnings_report() -> None:
    # A shareholder letter whose financial statements come after several pages
    # of narrative must not be dropped just because the head window is prose.
    narrative = "Fellow shareholders, this quarter we grew engagement and revenue."
    pages = [_page(i, narrative) for i in range(1, 9)]
    pages.append(
        _page(
            9,
            "Consolidated Statements of Operations (unaudited)\n"
            "Total revenues 10,540 Net income 2,890",
        )
    )

    classification = classify_document(pages)

    assert classification.document_type == "earnings_report"


def test_back_loaded_statements_are_selected_for_extraction() -> None:
    narrative = "Fellow shareholders, our business performed well this quarter."
    pages = [_page(i, narrative) for i in range(1, 9)]
    pages.append(
        _page(
            9,
            "Consolidated Statements of Operations (unaudited)\n"
            "Total revenue 10,540 Operating income 1,200 "
            "Net income 2,890 Earnings per share 1.45",
        )
    )

    selected = {page.page_number for page in select_extraction_pages(pages)}

    assert 1 in selected
    assert 9 in selected


def test_narrative_mention_of_earnings_call_stays_a_report() -> None:
    # Reports often invite readers to an upcoming earnings call; that mention
    # must not flip a statement-bearing report into a transcript.
    pages = [
        _page(1, "Join our earnings call webcast next week. Fellow shareholders,"),
        _page(2, "Consolidated Statements of Operations (unaudited)\nNet income 500"),
    ]

    classification = classify_document(pages)

    assert classification.document_type == "earnings_report"
