from earnings_extractor.schema import (
    MISSING_EVIDENCE_SOURCE_QUOTE,
    DraftRun,
    MetricsBatch,
    repair_metric_batch,
)

VALID_ROW = {
    "company": "Tesla",
    "ticker": "TSLA",
    "document_type": "earnings_report",
    "fiscal_period": "Q2 2025",
    "report_date": None,
    "metric_name": "Total revenue",
    "metric_category": "income_statement",
    "segment": None,
    "value": 22496,
    "unit": "USD",
    "scale": "millions",
    "period": "quarter",
    "gaap_or_non_gaap": "GAAP",
    "year_over_year_change": None,
    "source_page": 4,
    "source_quote": "Total revenues 25,500 25,182 25,707 19,335 22,496 -12%",
    "confidence": 0.9,
    "needs_review": False,
    "review_reason": None,
    "review_status": "pending",
    "reviewer_note": None,
}


def test_valid_structured_payload_passes() -> None:
    batch = repair_metric_batch({"metrics": [VALID_ROW]})

    assert batch.metrics[0].metric_name == "Total revenue"


def test_list_wrapped_payload_repairs_to_metric_batch() -> None:
    batch = repair_metric_batch([VALID_ROW])

    assert len(batch.metrics) == 1


def test_missing_source_quote_blanks_and_flags_row() -> None:
    row = dict(VALID_ROW)
    row.pop("source_quote")

    batch = repair_metric_batch({"metrics": [row]})

    metric = batch.metrics[0]
    assert metric.value is None
    assert metric.source_quote == MISSING_EVIDENCE_SOURCE_QUOTE
    assert metric.confidence == 0
    assert metric.needs_review is True
    assert "without source_quote" in (metric.review_reason or "")


def test_empty_source_quote_blanks_and_flags_row() -> None:
    row = {**VALID_ROW, "source_quote": "   "}

    batch = repair_metric_batch({"metrics": [row]})

    metric = batch.metrics[0]
    assert metric.value is None
    assert metric.source_quote == MISSING_EVIDENCE_SOURCE_QUOTE
    assert metric.needs_review is True


def test_empty_source_quote_repairs_after_pydantic_parse() -> None:
    parsed = MetricsBatch.model_validate(
        {"metrics": [{**VALID_ROW, "source_quote": ""}]}
    )

    batch = repair_metric_batch(parsed)

    metric = batch.metrics[0]
    assert metric.value is None
    assert metric.source_quote == MISSING_EVIDENCE_SOURCE_QUOTE
    assert metric.needs_review is True


def test_missing_source_page_blanks_and_flags_row() -> None:
    row = dict(VALID_ROW)
    row.pop("source_page")

    batch = repair_metric_batch({"metrics": [row]})

    metric = batch.metrics[0]
    assert metric.value is None
    assert metric.source_page == 1
    assert metric.needs_review is True
    assert "without source_page" in (metric.review_reason or "")


def test_enum_casing_is_safely_normalized() -> None:
    row = {
        **VALID_ROW,
        "document_type": "EARNINGS_REPORT",
        "review_status": "PENDING",
    }

    batch = repair_metric_batch({"metrics": [row]})

    assert batch.metrics[0].document_type == "earnings_report"
    assert batch.metrics[0].review_status == "pending"


def test_repair_does_not_fill_financial_values() -> None:
    row = dict(VALID_ROW)
    row.pop("value")

    batch = repair_metric_batch({"metrics": [row]})

    assert batch.metrics[0].value is None


def test_draft_run_defaults_missing_llm_usage_for_older_artifacts() -> None:
    draft = DraftRun.model_validate(
        {
            "run_id": "test",
            "created_at": "2026-05-31T00:00:00Z",
            "mode": "recorded",
            "model": "recorded",
            "reasoning_effort": None,
            "documents": [],
            "classifications": [],
            "selected_pages": {},
            "metrics": [VALID_ROW],
        }
    )

    assert draft.llm_usage == []
