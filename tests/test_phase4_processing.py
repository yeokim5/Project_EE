import shutil
from pathlib import Path

import pytest

from earnings_extractor.identity import resolve_company_identity
from earnings_extractor.ingest import PageText, read_pdf_metadata, read_pdf_pages
from earnings_extractor.normalize import normalize_metric, normalize_metrics
from earnings_extractor.pipeline import extract, inspect_draft
from earnings_extractor.schema import TEMPLATE_FIELDS, MetricRow
from earnings_extractor.validation import (
    PLACEHOLDER_SOURCE_QUOTE,
    check_free_cash_flow,
    complete_template_rows,
    enrich_capital_return_text,
    repair_table_scale,
    validate_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
TESLA = ROOT / "assesment_info" / "TSLA-Q2-2025-Update.pdf"
CITI = ROOT / "assesment_info" / "citi_earnings_q12025.pdf"


def test_recorded_mode_fails_clearly_for_uncassetted_supported_pdf(
    tmp_path: Path,
) -> None:
    copied_pdf = tmp_path / "uncassetted_citi.pdf"
    shutil.copy(CITI, copied_pdf)

    with pytest.raises(FileNotFoundError, match="No recorded extraction available"):
        extract(copied_pdf, tmp_path / "out", mode="recorded")


def test_recorded_output_emits_exact_template_rows(tmp_path: Path) -> None:
    draft_path = extract(TESLA, tmp_path, mode="recorded")
    draft = draft_path.read_text(encoding="utf-8")

    from earnings_extractor.schema import DraftRun

    parsed = DraftRun.model_validate_json(draft)
    template_rows = [m for m in parsed.metrics if m.metric_name in TEMPLATE_FIELDS]

    assert {m.metric_name for m in template_rows} == set(TEMPLATE_FIELDS)
    assert len(template_rows) == len(TEMPLATE_FIELDS)


def test_completion_adds_schema_safe_placeholder_rows() -> None:
    metrics: list[MetricRow] = []

    complete_template_rows(metrics, "earnings_report", [])

    row = next(m for m in metrics if m.metric_name == "Buybacks and dividends")
    assert row.value is None
    assert row.source_page == 1
    assert row.source_quote == PLACEHOLDER_SOURCE_QUOTE
    assert row.needs_review is True
    assert row.review_reason


def test_tesla_identity_scans_full_pages_for_on_page_evidence() -> None:
    pages = read_pdf_pages(TESLA)
    metadata = read_pdf_metadata(TESLA)

    identity = resolve_company_identity(
        pages=pages,
        metadata=metadata,
        source_file=str(TESLA),
        document_type="earnings_report",
    )

    assert identity is not None
    assert identity.name == "Tesla"
    assert identity.source_page == 3
    assert "Tesla" in identity.source_quote
    assert identity.needs_review is False


def test_citi_identity_uses_on_page_title_evidence() -> None:
    pages = read_pdf_pages(CITI)

    identity = resolve_company_identity(
        pages=pages,
        metadata=read_pdf_metadata(CITI),
        source_file=str(CITI),
        document_type="earnings_call_transcript",
    )

    assert identity is not None
    assert identity.name == "Citi"
    assert identity.source_page == 1
    assert "Citi First Quarter 2025 Earnings Call" in identity.source_quote


def test_filename_identity_uses_multi_token_name_and_source_casing() -> None:
    pages = [
        PageText(
            source_file="pdf_input/american_express_q1_2026.pdf",
            page_number=1,
            text="AMERICAN EXPRESS\nQ1 2026 RESULTS",
            char_count=32,
        )
    ]

    identity = resolve_company_identity(
        pages=pages,
        metadata={},
        source_file="pdf_input/american_express_q1_2026.pdf",
        document_type="earnings_report",
    )

    assert identity is not None
    assert identity.name == "American Express"
    assert identity.source_quote == "AMERICAN EXPRESS"
    assert identity.needs_review is False


def test_us_bancorp_identity_resolves_canonical_name_not_pronoun() -> None:
    # Regression: "us_bancorp_q1_2025.pdf" used to resolve to a stray lowercase
    # "us" pronoun on the page. It must become the canonical "U.S. Bancorp".
    pages = [
        PageText(
            source_file="pdf_input/us_bancorp_q1_2025.pdf",
            page_number=1,
            text=(
                "U.S. Bancorp reports first quarter 2025 results. "
                "Join us for the earnings call."
            ),
            char_count=80,
        )
    ]

    identity = resolve_company_identity(
        pages=pages,
        metadata={},
        source_file="pdf_input/us_bancorp_q1_2025.pdf",
        document_type="earnings_report",
    )

    assert identity is not None
    assert identity.name == "U.S. Bancorp"
    assert identity.name != "us"


def test_filename_identity_preserves_mixed_case_branding() -> None:
    pages = [
        PageText(
            source_file="pdf_input/blackrock_q1_2025.pdf",
            page_number=1,
            text="BlackRock Reports First Quarter 2025 Diluted EPS of $9.64",
            char_count=60,
        )
    ]

    identity = resolve_company_identity(
        pages=pages,
        metadata={},
        source_file="pdf_input/blackrock_q1_2025.pdf",
        document_type="earnings_report",
    )

    assert identity is not None
    assert identity.name == "BlackRock"
    assert "BlackRock Reports" in identity.source_quote


def test_currency_normalization_handles_billions_and_millions() -> None:
    revenue = _metric("Total revenue", "$21.6B", unit=None, scale=None)
    net_income = _metric("Net income", 4.1, unit="USD", scale="billions")
    expenses = _metric("Operating expenses", 13400, unit="USD", scale="millions")

    for metric in (revenue, net_income, expenses):
        normalize_metric(metric)

    assert revenue.value == 21600
    assert net_income.value == 4100
    assert expenses.value == 13400


def test_currency_normalization_uses_quote_to_prevent_double_billion_scaling() -> None:
    expenses = _metric("Operating expenses", 13900, unit="USD", scale="billion")
    expenses.source_quote = "Consolidated expenses were $13.9 billion, up 11"

    normalize_metric(expenses)

    assert expenses.value == 13900
    assert expenses.unit == "USD"
    assert expenses.scale == "millions"


def test_currency_normalization_handles_thousands_scale() -> None:
    # Smaller filers report financial statements "$ in thousands"; those values
    # must be divided down to the canonical USD-millions basis.
    revenue = _metric("Total revenue", "1,500,000", unit="USD", scale="thousands")
    net_income = _metric("Net income", 4100, unit="USD", scale="thousands")
    expenses = _metric("Operating expenses", "2,500K", unit=None, scale=None)

    for metric in (revenue, net_income, expenses):
        normalize_metric(metric)

    assert revenue.value == 1500
    assert net_income.value == 4.1
    assert expenses.value == 2.5


def test_percentage_eps_and_capex_normalization() -> None:
    margin = _metric("Gross margin", "17.2%")
    eps = _metric("Earnings per share", "$1.96")
    capex = _metric("Capital expenditures", "(2,394)", unit="USD", scale="millions")

    for metric in (margin, eps, capex):
        normalize_metric(metric)

    assert margin.value == 17.2
    assert eps.value == 1.96
    assert capex.value == 2394


def test_capital_return_enrichment_includes_split_dividend_fact() -> None:
    row = _metric("Buybacks and dividends", "$5.0 billion returned in 2025")
    pages = [
        PageText(
            source_file="blackrock_q4_2025.pdf",
            page_number=1,
            text=(
                "$5 billion returned to shareholders in 2025, including "
                "$1.6 billion worth of share repurchases\n"
                "10% increase in quarterly cash dividend to $5.73 per share "
                "approved by Board of Directors"
            ),
            char_count=180,
        )
    ]

    enrich_capital_return_text([row], pages)

    assert row.value == (
        "$5 billion returned in 2025, including $1.6 billion of repurchases "
        "and $5.73 quarterly cash dividend per share"
    )
    assert row.source_page == 1
    assert "$5.73 per share" in row.source_quote
    assert row.needs_review is True


def test_validation_flags_placeholder_and_low_confidence() -> None:
    row = _metric("Operating income", None)
    row.confidence = 0.0
    row.source_quote = PLACEHOLDER_SOURCE_QUOTE

    validate_metrics([row])

    assert row.needs_review is True
    assert "Confidence below" in (row.review_reason or "")
    assert "Missing source evidence" in (row.review_reason or "")


def test_free_cash_flow_check_passes_and_fails() -> None:
    passing = [
        _metric("Operating cash flow", 2540, unit="USD", scale="millions"),
        _metric("Capital expenditures", 2394, unit="USD", scale="millions"),
        _metric("Free cash flow", 146, unit="USD", scale="millions"),
    ]
    assert check_free_cash_flow(passing).status == "passed"

    failing = [
        _metric("Operating cash flow", 2540, unit="USD", scale="millions"),
        _metric("Capital expenditures", 2000, unit="USD", scale="millions"),
        _metric("Free cash flow", 146, unit="USD", scale="millions"),
    ]
    result = check_free_cash_flow(failing)

    assert result.status == "failed"
    assert all(metric.needs_review for metric in failing)


def test_validation_flags_quote_missing_from_cited_page() -> None:
    pages = [
        PageText(
            source_file="x.pdf",
            page_number=4,
            text="Total revenues 25,500 25,182 25,707 19,335 22,496 -12%",
            char_count=52,
        )
    ]
    on_page = _metric("Total revenue", 22496, unit="USD", scale="millions")
    on_page.source_page = 4
    on_page.source_quote = "Total revenues 25,500 25,182 25,707 19,335 22,496 -12%"

    off_page = _metric("Net income", 1172, unit="USD", scale="millions")
    off_page.source_page = 4
    off_page.source_quote = "Net income attributable fabricated 9,999"

    validate_metrics([on_page, off_page], pages)

    assert on_page.needs_review is False
    assert "Source quote not found" not in (on_page.review_reason or "")
    assert off_page.needs_review is True
    assert "Source quote not found on cited page 4" in (off_page.review_reason or "")


def test_blank_metric_with_negative_evidence_is_not_quote_flagged() -> None:
    pages = [
        PageText(
            source_file="x.pdf",
            page_number=4,
            text="Total revenues 25,500 25,182 25,707 19,335 22,496 -12%",
            char_count=52,
        )
    ]
    blank = _metric("Buybacks and dividends", None)
    blank.source_page = 4
    blank.source_quote = "No buybacks or dividends are listed in the provided pages."

    validate_metrics([blank], pages)

    assert blank.needs_review is True
    assert "Source quote not found" not in (blank.review_reason or "")
    assert "Template field is blank and requires review" in (blank.review_reason or "")


def test_inspect_counts_only_populated_evidence(tmp_path: Path) -> None:
    from earnings_extractor.schema import DraftRun

    populated = _metric("Total revenue", 22496, unit="USD", scale="millions")
    populated.source_quote = "Total revenues 22,496"
    blank_placeholder = _metric("Operating income", None)
    blank_placeholder.source_quote = PLACEHOLDER_SOURCE_QUOTE
    blank_negative_quote = _metric("Buybacks and dividends", None)
    blank_negative_quote.source_quote = "No buybacks or dividends are listed."

    draft = DraftRun(
        run_id="t",
        created_at="2026-05-30T00:00:00Z",
        mode="recorded",
        model="recorded",
        reasoning_effort=None,
        documents=[],
        classifications=[],
        selected_pages={},
        metrics=[populated, blank_placeholder, blank_negative_quote],
    )
    draft_path = tmp_path / "draft_metrics.json"
    draft_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")

    summary = inspect_draft(draft_path)

    assert "Metrics: 3" in summary
    assert "With evidence: 1/3" in summary


def test_value_grounding_flags_number_absent_from_quote() -> None:
    grounded = _metric("Net income", 1172, unit="USD", scale="millions")
    grounded.source_quote = "Net income attributable to common stockholders 1,172"

    ungrounded = _metric("Operating income", 923, unit="USD", scale="millions")
    ungrounded.source_quote = "Income from operations was strong this quarter"

    validate_metrics([grounded, ungrounded])

    assert "Reported value not found" not in (grounded.review_reason or "")
    assert ungrounded.needs_review is True
    assert "Reported value not found in its source quote" in (
        ungrounded.review_reason or ""
    )


def test_value_grounding_matches_scaled_billions_quote() -> None:
    # Normalized to USD millions, but the quote states billions.
    revenue = _metric("Total revenue", 21600, unit="USD", scale="millions")
    revenue.source_quote = "on $21.6 billion of revenues"

    validate_metrics([revenue])

    assert "Reported value not found" not in (revenue.review_reason or "")


def test_magnitude_check_flags_net_income_above_revenue() -> None:
    revenue = _metric("Total revenue", 22496, unit="USD", scale="millions")
    revenue.source_quote = "Total revenues 22,496"
    net_income = _metric("Net income", 99999, unit="USD", scale="millions")
    net_income.source_quote = "Net income 99,999"

    results = validate_metrics([revenue, net_income])
    magnitude = next(r for r in results if r.name == "value_magnitudes")

    assert magnitude.status == "failed"
    assert net_income.needs_review is True
    assert "exceeds total revenue" in (net_income.review_reason or "")


def test_value_grounding_flags_decimal_misparse_of_table_number() -> None:
    # Morgan Stanley regression: the extractor read the table number "17,739"
    # (in millions) as the decimal "17.739", 1000x too small. Significant digits
    # match, but with no unit word the scale gap is a misparse -- must not pass.
    revenue = _metric("Total revenue", 17.739, unit="USD", scale="millions")
    revenue.source_quote = "Net revenues 17,739 16,223 15,136"

    validate_metrics([revenue])

    assert revenue.needs_review is True
    assert "verify scale" in (revenue.review_reason or "")


def _thousands_page() -> PageText:
    return PageText(
        source_file="pdf_input/netflix_q4_2025.pdf",
        page_number=12,
        text=(
            "CONSOLIDATED STATEMENTS OF OPERATIONS (in thousands, except per "
            "share data)\nRevenues $ 12,050,762\nNet income $ 2,418,521"
        ),
        char_count=110,
    )


def test_repair_table_scale_fixes_decimal_misparse() -> None:
    # Netflix regression: "12,050,762" (thousands table) misread as "12.050762".
    metric = _metric("Total revenue", 12.050762, unit="USD", scale="millions")
    metric.source_page = 12
    metric.source_quote = "Revenues $ 12,050,762"

    repair_table_scale([metric], [_thousands_page()])
    normalize_metrics([metric])

    assert abs(float(metric.value) - 12050.762) < 0.01
    assert metric.needs_review is True
    assert "table scale" in (metric.review_reason or "")


def test_repair_table_scale_leaves_correct_value_untouched() -> None:
    metric = _metric("Total revenue", 12050.762, unit="USD", scale="millions")
    metric.source_page = 12
    metric.source_quote = "Revenues $ 12,050,762"

    repair_table_scale([metric], [_thousands_page()])

    assert abs(float(metric.value) - 12050.762) < 0.01
    assert metric.needs_review is False


def test_grounding_accepts_thousands_table_value_without_false_flag() -> None:
    metric = _metric("Total revenue", 12050.762, unit="USD", scale="millions")
    metric.source_page = 12
    metric.source_quote = "Revenues $ 12,050,762"

    validate_metrics([metric], [_thousands_page()])

    assert "scale" not in (metric.review_reason or "")
    assert "not found" not in (metric.review_reason or "").lower()


def test_bare_number_buybacks_flagged_for_review() -> None:
    # Citigroup regression: the narrative buybacks field held a raw 2100.0.
    # It must be routed to review, not treated as a finished cell.
    bare = _metric("Buybacks and dividends", 2100.0, unit=None, scale=None)
    bare.source_quote = "returned $2.1 billion to shareholders"
    narrative = _metric(
        "Buybacks and dividends", "$2.1B returned, including $1.6B buybacks"
    )
    narrative.source_quote = "$2.1B returned, including $1.6B buybacks"

    validate_metrics([bare, narrative])

    assert bare.needs_review is True
    assert "bare number" in (bare.review_reason or "")
    assert "bare number" not in (narrative.review_reason or "")


def test_value_grounding_allows_billions_quote_with_unit_word() -> None:
    # Counterpart: a genuine cross-scale match (21600 millions <-> "$21.6
    # billion") is justified by the unit word and must stay clean.
    revenue = _metric("Total revenue", 21600, unit="USD", scale="millions")
    revenue.source_quote = "on $21.6 billion of revenues"

    validate_metrics([revenue])

    assert "verify scale" not in (revenue.review_reason or "")
    assert "Reported value not found" not in (revenue.review_reason or "")


def test_magnitude_check_skips_without_numeric_revenue() -> None:
    net_income = _metric("Net income", 1172, unit="USD", scale="millions")
    net_income.source_quote = "Net income 1,172"

    results = validate_metrics([net_income])
    magnitude = next(r for r in results if r.name == "value_magnitudes")

    assert magnitude.status == "skipped"


def _metric(
    name: str,
    value: object,
    unit: str | None = None,
    scale: str | None = None,
) -> MetricRow:
    return MetricRow(
        document_type="earnings_report",
        metric_name=name,
        value=value,
        unit=unit,
        scale=scale,
        source_page=1,
        source_quote="source quote",
        confidence=0.99,
        needs_review=False,
    )
