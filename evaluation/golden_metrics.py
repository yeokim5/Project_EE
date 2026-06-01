"""Evidence-backed golden targets for the eval harness.

This module is intentionally outside ``earnings_extractor``. Runtime extraction
code must never import or read these expected values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FieldStatus = Literal["expected_value", "expected_blank_review", "not_scored"]
ValueType = Literal[
    "currency_usd_millions",
    "eps",
    "free_text",
    "percentage_points",
    "text",
]

ROOT = Path(__file__).resolve().parents[1]
ASSESSMENT_INFO = ROOT / "assesment_info"

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


@dataclass(frozen=True)
class GoldenField:
    document_id: str
    source_file: Path
    field_name: str
    status: FieldStatus
    value_type: ValueType
    expected_value: float | str | None = None
    unit: str | None = None
    source_page: int | None = None
    source_quote: str = ""
    review_reason: str = ""


@dataclass(frozen=True)
class AuxiliaryMetric:
    document_id: str
    source_file: Path
    metric_name: str
    expected_value: float | str
    value_type: ValueType
    unit: str | None
    source_page: int
    source_quote: str


TESLA_PDF = ASSESSMENT_INFO / "TSLA-Q2-2025-Update.pdf"
CITI_PDF = ASSESSMENT_INFO / "citi_earnings_q12025.pdf"

PRIMARY_FIELDS: tuple[GoldenField, ...] = (
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Company Name",
        status="expected_value",
        value_type="text",
        expected_value="Tesla",
        source_page=1,
        source_quote="Q2 2025 Update",
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Quarter",
        status="expected_value",
        value_type="text",
        expected_value="Q2 2025",
        source_page=1,
        source_quote="Q2 2025 Update",
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Total revenue",
        status="expected_value",
        value_type="currency_usd_millions",
        expected_value=22496,
        unit="USD millions",
        source_page=4,
        source_quote="Total revenues 25,500 25,182 25,707 19,335 22,496 -12%",
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Earnings per share",
        status="expected_value",
        value_type="eps",
        expected_value=0.33,
        unit="USD per diluted share",
        source_page=4,
        source_quote=(
            "EPS attributable to common stockholders, diluted (GAAP) "
            "0.40 0.62 0.60 0.12 0.33 -18%"
        ),
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Net income",
        status="expected_value",
        value_type="currency_usd_millions",
        expected_value=1172,
        unit="USD millions",
        source_page=4,
        source_quote=(
            "Net income attributable to common stockholders (GAAP) "
            "1,400 2,173 2,128 409 1,172 -16%"
        ),
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Operating income",
        status="expected_value",
        value_type="currency_usd_millions",
        expected_value=923,
        unit="USD millions",
        source_page=4,
        source_quote="Income from operations 1,605 2,717 1,583 399 923 -42%",
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Gross margin",
        status="expected_value",
        value_type="percentage_points",
        expected_value=17.2,
        unit="percentage points",
        source_page=4,
        source_quote="Total GAAP gross margin 18.0% 19.8% 16.3% 16.3% 17.2%",
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Operating expenses",
        status="expected_value",
        value_type="currency_usd_millions",
        expected_value=2955,
        unit="USD millions",
        source_page=4,
        source_quote="Operating expenses 2,973 2,280 2,596 2,754 2,955 -1%",
    ),
    GoldenField(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        field_name="Buybacks and dividends",
        status="expected_blank_review",
        value_type="free_text",
        review_reason=(
            "No clearly supported buyback/dividend template value found in "
            "inspected Tesla pages; do not guess."
        ),
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Company Name",
        status="expected_value",
        value_type="text",
        expected_value="Citi",
        source_page=1,
        source_quote="Citi First Quarter 2025 Earnings Call",
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Quarter",
        status="expected_value",
        value_type="text",
        expected_value="Q1 2025",
        source_page=1,
        source_quote="Citi First Quarter 2025 Earnings Call",
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Total revenue",
        status="expected_value",
        value_type="currency_usd_millions",
        expected_value=21600,
        unit="USD millions",
        source_page=3,
        source_quote="on $21.6 billion of revenues",
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Earnings per share",
        status="expected_value",
        value_type="eps",
        expected_value=1.96,
        unit="USD per diluted share",
        source_page=3,
        source_quote="EPS of $1.96",
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Net income",
        status="expected_value",
        value_type="currency_usd_millions",
        expected_value=4100,
        unit="USD millions",
        source_page=3,
        source_quote="net income of $4.1 billion",
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Operating income",
        status="expected_blank_review",
        value_type="currency_usd_millions",
        review_reason=(
            "Not clearly supported as a firmwide bank metric in the transcript "
            "excerpts; do not infer."
        ),
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Gross margin",
        status="expected_blank_review",
        value_type="percentage_points",
        review_reason=(
            "Not meaningful for a bank transcript in the same way as an "
            "industrial company; do not infer."
        ),
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Operating expenses",
        status="expected_value",
        value_type="currency_usd_millions",
        expected_value=13400,
        unit="USD millions",
        source_page=3,
        source_quote="Expenses of $13.4 billion",
    ),
    GoldenField(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        field_name="Buybacks and dividends",
        status="expected_value",
        value_type="free_text",
        expected_value="$2.8B capital returned, including $1.75B buybacks",
        source_page=2,
        source_quote=(
            "returned $2.8 billion in capital to our shareholders including "
            "$1.75 billion of buybacks"
        ),
    ),
)

AUXILIARY_METRICS: tuple[AuxiliaryMetric, ...] = (
    AuxiliaryMetric(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        metric_name="Operating cash flow",
        expected_value=2540,
        value_type="currency_usd_millions",
        unit="USD millions",
        source_page=4,
        source_quote=(
            "Net cash provided by operating activities "
            "3,612 6,255 4,814 2,156 2,540 -30%"
        ),
    ),
    AuxiliaryMetric(
        document_id="tesla_q2_2025",
        source_file=TESLA_PDF,
        metric_name="Free cash flow",
        expected_value=146,
        value_type="currency_usd_millions",
        unit="USD millions",
        source_page=4,
        source_quote="Free cash flow 1,340 2,742 2,034 664 146 -89%",
    ),
    AuxiliaryMetric(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        metric_name="RoTCE",
        expected_value=9.1,
        value_type="percentage_points",
        unit="percentage points",
        source_page=3,
        source_quote="RoTCE of 9.1%",
    ),
    AuxiliaryMetric(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        metric_name="CET1 ratio",
        expected_value=13.4,
        value_type="percentage_points",
        unit="percentage points",
        source_page=2,
        source_quote="CET1 ratio of 13.4%",
    ),
    AuxiliaryMetric(
        document_id="citi_q1_2025",
        source_file=CITI_PDF,
        metric_name="Cost of credit",
        expected_value=2700,
        value_type="currency_usd_millions",
        unit="USD millions",
        source_page=3,
        source_quote="Cost of credit was $2.7 billion",
    ),
)
