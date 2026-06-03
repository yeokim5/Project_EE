"""Live structured LLM extraction adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from earnings_extractor.config import OpenAIConfig
from earnings_extractor.ingest import PageText
from earnings_extractor.schema import (
    TEMPLATE_FIELDS,
    LLMUsage,
    MetricsBatch,
    repair_metric_batch,
)

SYSTEM_PROMPT = """You extract draft financial metrics from public earnings PDFs.

Return only the requested structured fields. Every metric must include concise
source evidence copied from the provided page text. If a value is unsupported,
include the metric with value null, needs_review true, and a review_reason.
Do not guess missing values.
"""


@dataclass(frozen=True)
class LiveExtractionResult:
    metrics: MetricsBatch
    usage: LLMUsage | None


def extract_metrics_live(
    pages: list[PageText],
    document_type: str,
    config: OpenAIConfig,
) -> MetricsBatch:
    return extract_metrics_live_with_usage(
        pages=pages,
        document_type=document_type,
        config=config,
        source_file=_source_file_from_pages(pages),
    ).metrics


def extract_metrics_live_with_usage(
    pages: list[PageText],
    document_type: str,
    config: OpenAIConfig,
    source_file: str,
) -> LiveExtractionResult:
    client = OpenAI(api_key=config.api_key)
    response = client.responses.parse(
        model=config.model,
        reasoning={"effort": config.reasoning_effort},
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(pages, document_type)},
        ],
        text_format=MetricsBatch,
    )
    metrics = repair_metric_batch(response.output_parsed)
    usage = _parse_openai_usage(response.usage, config.model, source_file)
    return LiveExtractionResult(metrics=metrics, usage=usage)


def _parse_openai_usage(
    usage: Any,
    model: str,
    source_file: str,
) -> LLMUsage | None:
    if usage is None:
        return None

    input_tokens = _usage_value(usage, "input_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    if input_tokens is None or output_tokens is None or total_tokens is None:
        return None

    details = _usage_value(usage, "output_tokens_details")
    reasoning_tokens = _usage_value(details, "reasoning_tokens")

    return LLMUsage(
        source_file=source_file,
        provider="openai",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _usage_value(payload: Any, key: str) -> Any:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _source_file_from_pages(pages: list[PageText]) -> str:
    if not pages:
        return "unknown"
    return pages[0].source_file


def _build_user_prompt(pages: list[PageText], document_type: str) -> str:
    page_blocks = "\n\n".join(
        f"--- PAGE {page.page_number} ({page.source_file}) ---\n{page.text}"
        for page in pages
    )
    fields = "\n".join(f"- {field}" for field in TEMPLATE_FIELDS)
    return f"""Document type: {document_type}

Extract these client template fields when supported:
{fields}

Also extract auxiliary validation metrics when directly supported by source text:
- Operating cash flow
- Capital expenditures
- Free cash flow

Internal schema notes:
- PERIOD: extract the CURRENT QUARTER's figure for every metric. Earnings
  statements print the quarter beside full-year, year-to-date, nine-month, or
  prior-year columns -- never take an annual / YTD / prior-period value for a
  quarterly metric. A quarterly revenue near a full-year revenue 3-4x larger
  means you are reading the wrong column. ``fiscal_period`` and ``value`` must
  both be the single most recent quarter;
- currency values: COPY the figure exactly as printed, keeping any thousands
  separators, into ``value`` as a string -- e.g. a statement line "Revenues $
  12,050,762" must be value "12,050,762". Do NOT convert, divide, round, or move
  the decimal point, and never reinterpret a thousands separator as a decimal
  point. Deterministic code does the math;
- ``scale`` records the figure's printed magnitude so code can normalize it:
  "thousands" when the statement says "in thousands" / "amounts in thousands",
  "millions" when it says "in millions", "billions" when the figure itself is
  written like "$21.6 billion". When unsure, copy the exact words near the
  figure rather than guessing;
- ``unit`` is the currency code (USD);
- percentages should be percentage points;
- Company Name should use a real on-page source quote that contains the company
  name. If the provided page text does not contain the company name, leave the
  value null and set needs_review true rather than relying on world knowledge;
- for Net income, prefer the figure attributable to common stockholders (GAAP)
  when both that and a broader consolidated net income line are present;
- review_status should be pending;
- source_page must be the 1-based page number from the provided page block;
- source_quote must be short text from the page that contains the value.

Page text:
{page_blocks}
"""
