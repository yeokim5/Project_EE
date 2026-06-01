from scripts.inspect_phase2_inputs import (
    check_excel_template,
    check_pdf_evidence,
    quote_present,
)


def test_quote_matching_normalizes_whitespace() -> None:
    assert quote_present("EPS\nof   $1.96", "EPS of $1.96")


def test_pdf_evidence_is_present_on_cited_pages() -> None:
    assert check_pdf_evidence() == []


def test_excel_template_contract_matches_client_reply() -> None:
    assert check_excel_template() == []
