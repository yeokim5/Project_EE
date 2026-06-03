"""Validated data contracts for draft extraction artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DocumentType = Literal["earnings_report", "earnings_call_transcript", "unknown"]
PageStyle = Literal["table_heavy", "narrative", "mixed"]
ReviewStatus = Literal["pending", "approved", "rejected", "needs_fix", "not_applicable"]
RunMode = Literal["live", "recorded"]

TEMPLATE_FIELDS = (
    "Company Name",
    "Quarter",
    "Total revenue",
    "Earnings per share",
    "Net income",
    "Operating income",
    "Gross margin",
    "Operating expenses",
    "Buybacks and dividends",
)
MISSING_EVIDENCE_SOURCE_QUOTE = "No supporting source quote returned by extractor"


class PageClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    style: PageStyle
    char_count: int


class DocumentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str
    document_type: DocumentType
    page_count: int
    pages: list[PageClassification]


class MetricRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str | None = None
    company: str | None = None
    ticker: str | None = None
    document_type: DocumentType = "unknown"
    fiscal_period: str | None = None
    report_date: str | None = None
    metric_name: str
    metric_category: str | None = None
    segment: str | None = None
    value: float | str | None = None
    unit: str | None = None
    scale: str | None = None
    period: str | None = None
    gaap_or_non_gaap: str | None = None
    year_over_year_change: float | str | None = None
    source_page: int
    source_quote: str
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool = True
    review_reason: str | None = None
    review_status: ReviewStatus = "pending"
    reviewer_note: str | None = None

    @field_validator("metric_name")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class MetricsBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[MetricRow]


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str
    document_type: DocumentType
    page_count: int


class LLMUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str
    provider: str = "openai"
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    reasoning_tokens: int | None = None


class DraftRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    mode: RunMode
    model: str | None = None
    reasoning_effort: str | None = None
    documents: list[SourceDocument]
    classifications: list[DocumentClassification]
    selected_pages: dict[str, list[int]]
    llm_usage: list[LLMUsage] = Field(default_factory=list)
    metrics: list[MetricRow]


def new_run_id() -> str:
    """Unique identifier for an extraction run."""

    return uuid4().hex


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 timestamp for audit trails."""

    return datetime.now(timezone.utc).isoformat()


def repair_metric_batch(payload: Any) -> MetricsBatch:
    """Validate structured model output, repairing only harmless wrappers/casing."""

    if isinstance(payload, MetricsBatch):
        payload = payload.model_dump(mode="json")

    if isinstance(payload, list):
        payload = {"metrics": payload}
    elif isinstance(payload, dict) and "metrics" not in payload:
        for wrapper_key in ("data", "result", "output"):
            wrapped = payload.get(wrapper_key)
            if isinstance(wrapped, list):
                payload = {"metrics": wrapped}
                break
            if isinstance(wrapped, dict) and "metrics" in wrapped:
                payload = wrapped
                break

    if isinstance(payload, dict) and isinstance(payload.get("metrics"), list):
        payload = {
            **payload,
            "metrics": [_repair_metric_row(row) for row in payload["metrics"]],
        }

    return MetricsBatch.model_validate(payload)


def _repair_metric_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row

    repaired = dict(row)
    if isinstance(repaired.get("document_type"), str):
        repaired["document_type"] = repaired["document_type"].strip().lower()
    if isinstance(repaired.get("review_status"), str):
        repaired["review_status"] = repaired["review_status"].strip().lower()
    if not str(repaired.get("source_quote") or "").strip():
        _blank_and_flag(
            repaired,
            "Extractor returned this row without source_quote; value was blanked.",
        )
        repaired["source_quote"] = MISSING_EVIDENCE_SOURCE_QUOTE
    if not isinstance(repaired.get("source_page"), int):
        _blank_and_flag(
            repaired,
            "Extractor returned this row without source_page; value was blanked.",
        )
        repaired["source_page"] = 1
    return repaired


def _blank_and_flag(row: dict[str, Any], reason: str) -> None:
    row["value"] = None
    row["confidence"] = min(float(row.get("confidence") or 0.0), 0.0)
    row["needs_review"] = True
    existing = str(row.get("review_reason") or "").strip()
    row["review_reason"] = f"{existing}; {reason}" if existing else reason


def load_draft(path: Path) -> DraftRun:
    try:
        return DraftRun.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        raise
