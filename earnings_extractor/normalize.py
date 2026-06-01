"""Canonical runtime normalization for extracted metric rows.

The LLM reads numbers in whatever shape the source document uses: "$21.6
billion", "22,496", "(1,234)", "21.6%". Comparisons, validation, and the eval
all need one canonical representation, so every currency metric is normalized to
**USD millions** here (the scale the client template and golden values use), gross
margin to percentage points, and EPS to a plain per-share number. Doing this in
fixed code rather than asking the model to do the math keeps unit/scale handling
deterministic and auditable. Anything that cannot be parsed is flagged for review
rather than silently dropped.
"""

from __future__ import annotations

import re
from typing import Any

from earnings_extractor.schema import MetricRow

CURRENCY_TEMPLATE_FIELDS = {
    "Total revenue",
    "Net income",
    "Operating income",
    "Operating expenses",
    "Operating cash flow",
    "Capital expenditures",
    "Free cash flow",
    "Buybacks and dividends",
}
PERCENTAGE_FIELDS = {"Gross margin"}
EPS_FIELDS = {"Earnings per share"}


def normalize_metrics(metrics: list[MetricRow]) -> None:
    for metric in metrics:
        normalize_metric(metric)


def normalize_metric(metric: MetricRow) -> None:
    if metric.value in (None, ""):
        return

    if metric.metric_name in CURRENCY_TEMPLATE_FIELDS:
        normalized = normalize_currency_to_usd_millions(
            metric.value, metric.unit, metric.scale
        )
        if normalized is None:
            _flag(metric, "Could not normalize currency value.")
            return
        # Capex is reported as a negative cash-flow line in some documents and a
        # positive spend figure in others. The free-cash-flow reconciliation
        # check expects a positive magnitude, so normalize the sign here.
        if metric.metric_name == "Capital expenditures":
            normalized = abs(normalized)
        metric.value = normalized
        metric.unit = "USD"
        metric.scale = "millions"
    elif metric.metric_name in PERCENTAGE_FIELDS:
        normalized = _coerce_number(metric.value)
        if normalized is None:
            _flag(metric, "Could not normalize percentage value.")
            return
        metric.value = normalized
        metric.unit = "percentage points"
        metric.scale = None
    elif metric.metric_name in EPS_FIELDS:
        normalized = _coerce_number(metric.value)
        if normalized is None:
            _flag(metric, "Could not normalize EPS value.")
            return
        metric.value = normalized
        metric.unit = "USD/share"
        metric.scale = None


def normalize_currency_to_usd_millions(
    value: Any, unit: str | None, scale: str | None
) -> float | None:
    number, detected_scale = _coerce_number_and_scale(value)
    if number is None:
        return None

    descriptor = f"{unit or ''} {scale or ''} {detected_scale or ''}".lower()
    if any(token in descriptor for token in ("billion", "billions")):
        return number * 1000.0
    if re.search(r"\bb\b", descriptor):
        return number * 1000.0
    if any(token in descriptor for token in ("thousand", "thousands")):
        return number / 1000.0
    if re.search(r"\bk\b", descriptor):
        return number / 1000.0
    return number


def _coerce_number_and_scale(value: Any) -> tuple[float | None, str | None]:
    if isinstance(value, int | float):
        return float(value), None
    if not isinstance(value, str):
        return None, None

    cleaned = value.replace(",", "").strip()
    detected_scale = None
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    lower = cleaned.lower()
    for suffix, scale in (
        ("billions", "billion"),
        ("billion", "billion"),
        ("bn", "billion"),
        ("b", "billion"),
        ("millions", "million"),
        ("million", "million"),
        ("m", "million"),
        ("thousands", "thousand"),
        ("thousand", "thousand"),
        ("k", "thousand"),
    ):
        if lower.endswith(suffix):
            detected_scale = scale
            cleaned = cleaned[: -len(suffix)].strip()
            break
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1].strip()
    try:
        return float(cleaned), detected_scale
    except ValueError:
        return None, detected_scale


def _coerce_number(value: Any) -> float | None:
    number, _scale = _coerce_number_and_scale(value)
    return number


def _flag(metric: MetricRow, reason: str) -> None:
    metric.needs_review = True
    metric.review_reason = _append_reason(metric.review_reason, reason)


def _append_reason(existing: str | None, reason: str) -> str:
    if not existing:
        return reason
    if reason in existing:
        return existing
    return f"{existing}; {reason}"
