"""Draft completion, validation, and deterministic consistency checks.

Review flags exist so a human only has to look at values that actually warrant
it. Validation never edits an extracted value -- it only sets ``needs_review``
with a reason. A row is flagged when confidence is below threshold, the source
quote is missing, the quote is not found on the cited page, the reported number
is not grounded in its own quote, a required template field is blank, the field
is unsupported for the document type, or a consistency/magnitude check fails.
Everything else can pass through, which keeps the reviewer's queue short and
trustworthy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from earnings_extractor.ingest import PageText
from earnings_extractor.schema import TEMPLATE_FIELDS, DocumentType, MetricRow

PLACEHOLDER_SOURCE_QUOTE = "No supporting source quote found in selected source pages"
LOW_CONFIDENCE_THRESHOLD = 0.75
CURRENCY_CHECK_TOLERANCE_USD_MILLIONS = 5.0

# Magnitude sanity bounds. These never change a value; they only route
# implausible extractions to human review.
MAGNITUDE_TOLERANCE_RATIO = 0.01
MAGNITUDE_BOUNDED_FIELDS = ("Net income", "Operating income")
GROSS_MARGIN_RANGE = (-100.0, 100.0)

# Matches a number token like "22,496", "21.6", "1.96", or "923".
_NUMBER_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    reason: str


def complete_template_rows(
    metrics: list[MetricRow],
    document_type: DocumentType,
    selected_pages: list[PageText],
) -> None:
    existing = {metric.metric_name for metric in metrics}
    first_page = selected_pages[0].page_number if selected_pages else 1
    for field in TEMPLATE_FIELDS:
        if field in existing:
            continue
        metrics.append(
            MetricRow(
                document_type=document_type,
                metric_name=field,
                metric_category="template",
                value=None,
                source_page=first_page,
                source_quote=PLACEHOLDER_SOURCE_QUOTE,
                confidence=0.0,
                needs_review=True,
                review_reason=_unsupported_reason(field, document_type),
                review_status="pending",
            )
        )


def enrich_capital_return_text(metrics: list[MetricRow], pages: list[PageText]) -> None:
    row = next((m for m in metrics if m.metric_name == "Buybacks and dividends"), None)
    if row is None:
        return

    pattern = re.compile(
        r"returned\s+\$([\d.]+)\s+billion\s+in\s+capital.*?"
        r"including\s+\$([\d.]+)\s+billion\s+of\s+buybacks",
        re.IGNORECASE,
    )
    for page in pages:
        for line in page.text.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            capital, buybacks = match.groups()
            row.value = f"${capital}B capital returned, including ${buybacks}B buybacks"
            row.unit = None
            row.scale = None
            row.source_page = page.page_number
            row.source_quote = _trim_quote(line)
            row.confidence = 0.85
            row.needs_review = True
            row.review_reason = (
                "Auto-extracted from a capital-return sentence; verify the "
                "buyback/dividend split against the cited source."
            )
            return


def repair_source_pages(
    metrics: list[MetricRow], pages: list[PageText] | None
) -> None:
    """Snap each metric's cited page to the page that actually contains its quote.

    Live-mode safety net: language models occasionally return a correct quote
    paired with the wrong page number. Before the citation validator flags such
    a row, search every page's text for the quote and, when it resolves to
    exactly one page, move ``source_page`` there.

    This reuses the same whitespace-normalized page text the validator trusts,
    so a successful repair guarantees the "quote not found on cited page" check
    then passes. The downstream highlight (``locate_evidence_bbox``) is keyed off
    ``source_page`` as well, so the on-page highlight lands on the right page too.

    Conservative by design: a quote that matches zero pages (nothing to repair
    to) or several pages (ambiguous — e.g. a repeated header line) is left
    untouched for the validator to flag, rather than guessing.
    """

    page_text_by_number = _normalized_page_text(pages)
    if not page_text_by_number:
        return
    for metric in metrics:
        if metric.value in (None, ""):
            continue
        quote = metric.source_quote
        if not quote or quote == PLACEHOLDER_SOURCE_QUOTE:
            continue
        needle = _normalize_ws(quote).lower()
        if not needle:
            continue
        if needle in page_text_by_number.get(metric.source_page, ""):
            continue  # cited page already contains the quote — nothing to fix
        matches = [
            number
            for number, text in page_text_by_number.items()
            if needle in text
        ]
        if len(matches) == 1:
            metric.source_page = matches[0]


def validate_metrics(
    metrics: list[MetricRow],
    pages: list[PageText] | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    page_text_by_number = _normalized_page_text(pages)
    for metric in metrics:
        if metric.confidence < LOW_CONFIDENCE_THRESHOLD:
            _flag(metric, f"Confidence below {LOW_CONFIDENCE_THRESHOLD}.")
        if not metric.source_quote or metric.source_quote == PLACEHOLDER_SOURCE_QUOTE:
            _flag(metric, "Missing source evidence.")
        elif (
            metric.value not in (None, "")
            and page_text_by_number
            and not _quote_on_cited_page(metric, page_text_by_number)
        ):
            _flag(
                metric,
                f"Source quote not found on cited page {metric.source_page}.",
            )
        if (
            isinstance(metric.value, int | float)
            and metric.source_quote
            and metric.source_quote != PLACEHOLDER_SOURCE_QUOTE
            and not _value_appears_in_quote(metric.value, metric.source_quote)
        ):
            _flag(metric, "Reported value not found in its source quote.")
        if metric.value in (None, "") and metric.metric_name in TEMPLATE_FIELDS:
            _flag(metric, "Template field is blank and requires review.")

    results.append(check_free_cash_flow(metrics))
    results.append(check_value_magnitudes(metrics))
    return results


def _normalized_page_text(pages: list[PageText] | None) -> dict[int, str]:
    """Whitespace-normalized, lowercased page text keyed by physical page number."""

    if not pages:
        return {}
    return {page.page_number: _normalize_ws(page.text).lower() for page in pages}


def _quote_on_cited_page(
    metric: MetricRow, page_text_by_number: dict[int, str]
) -> bool:
    page_text = page_text_by_number.get(metric.source_page)
    if page_text is None:
        return False
    return _normalize_ws(metric.source_quote).lower() in page_text


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def check_free_cash_flow(metrics: list[MetricRow]) -> CheckResult:
    by_name = {metric.metric_name: metric for metric in metrics}
    required = [
        by_name.get("Operating cash flow"),
        by_name.get("Capital expenditures"),
        by_name.get("Free cash flow"),
    ]
    if any(
        metric is None or not isinstance(metric.value, int | float)
        for metric in required
    ):
        return CheckResult(
            name="free_cash_flow",
            status="skipped",
            reason="Missing numeric operating cash flow, capex, or free cash flow.",
        )

    ocf, capex, fcf = required
    assert ocf is not None and capex is not None and fcf is not None
    difference = abs(float(ocf.value) - float(capex.value) - float(fcf.value))
    if difference <= CURRENCY_CHECK_TOLERANCE_USD_MILLIONS:
        return CheckResult(
            name="free_cash_flow",
            status="passed",
            reason=(
                "Operating cash flow minus capital expenditures reconciles "
                "to free cash flow."
            ),
        )

    reason = (
        "Operating cash flow minus capital expenditures does not reconcile "
        "to free cash flow within "
        f"{CURRENCY_CHECK_TOLERANCE_USD_MILLIONS} USD millions."
    )
    for metric in (ocf, capex, fcf):
        _flag(metric, reason)
    return CheckResult(name="free_cash_flow", status="failed", reason=reason)


def check_value_magnitudes(metrics: list[MetricRow]) -> CheckResult:
    """Flag template values whose magnitude is implausible against revenue.

    Deterministic sanity bounds only. Net income and operating income should not
    exceed total revenue, and gross margin should sit in a sane percentage range.
    Skips cleanly when there is no numeric revenue to bound against.
    """

    by_name = {metric.metric_name: metric for metric in metrics}
    revenue = _numeric_value(by_name.get("Total revenue"))
    if revenue is None or revenue <= 0:
        return CheckResult(
            name="value_magnitudes",
            status="skipped",
            reason="No positive numeric total revenue to bound other metrics.",
        )

    failures: list[str] = []
    bound = revenue * (1.0 + MAGNITUDE_TOLERANCE_RATIO)
    for field in MAGNITUDE_BOUNDED_FIELDS:
        metric = by_name.get(field)
        magnitude = _numeric_value(metric)
        if metric is not None and magnitude is not None and abs(magnitude) > bound:
            _flag(
                metric,
                f"{field} magnitude exceeds total revenue; verify value/scale.",
            )
            failures.append(field)

    margin_metric = by_name.get("Gross margin")
    margin = _numeric_value(margin_metric)
    low, high = GROSS_MARGIN_RANGE
    if margin_metric is not None and margin is not None and not low <= margin <= high:
        _flag(margin_metric, "Gross margin is outside the plausible -100..100 range.")
        failures.append("Gross margin")

    if failures:
        return CheckResult(
            name="value_magnitudes",
            status="failed",
            reason="Implausible magnitudes: " + ", ".join(failures) + ".",
        )
    return CheckResult(
        name="value_magnitudes",
        status="passed",
        reason="Net income, operating income, and gross margin are within bounds.",
    )


def _numeric_value(metric: MetricRow | None) -> float | None:
    if metric is None or not isinstance(metric.value, int | float):
        return None
    return float(metric.value)


def _value_appears_in_quote(value: float, quote: str) -> bool:
    """True if the reported number is grounded in its own source quote.

    Compares scale-invariant significant digits, so a normalized 21600 (USD
    millions) still matches a "$21.6 billion" quote and 22496 matches "22,496".
    """

    target = _significant_digits(f"{abs(float(value)):.4f}")
    if not target:
        return True
    return any(
        _significant_digits(token.replace(",", "")) == target
        for token in _NUMBER_TOKEN_RE.findall(quote)
    )


def _significant_digits(text: str) -> str:
    digits = re.sub(r"[^\d.]", "", text)
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    return digits.replace(".", "").strip("0")


def _unsupported_reason(field: str, document_type: DocumentType) -> str:
    if document_type == "earnings_call_transcript" and field in {
        "Operating income",
        "Gross margin",
    }:
        return (
            f"{field} is not meaningful or clearly supported for this bank "
            "transcript."
        )
    return f"{field} was not reported in the selected source pages."


def _flag(metric: MetricRow, reason: str) -> None:
    metric.needs_review = True
    metric.review_reason = _append_reason(metric.review_reason, reason)


def _append_reason(existing: str | None, reason: str) -> str:
    if not existing:
        return reason
    if reason in existing:
        return existing
    return f"{existing}; {reason}"


def _trim_quote(line: str, limit: int = 240) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"
