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

from earnings_extractor.capital_return import is_bare_number
from earnings_extractor.ingest import PageText
from earnings_extractor.schema import TEMPLATE_FIELDS, DocumentType, MetricRow

PLACEHOLDER_SOURCE_QUOTE = "No supporting source quote found in selected source pages"
CAPITAL_RETURN_FIELD = "Buybacks and dividends"
LOW_CONFIDENCE_THRESHOLD = 0.75
CURRENCY_CHECK_TOLERANCE_USD_MILLIONS = 5.0

# Magnitude sanity bounds. These never change a value; they only route
# implausible extractions to human review.
MAGNITUDE_TOLERANCE_RATIO = 0.01
MAGNITUDE_BOUNDED_FIELDS = ("Net income", "Operating income")
GROSS_MARGIN_RANGE = (-100.0, 100.0)

# Matches a number token like "22,496", "21.6", "1.96", or "923".
_NUMBER_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# A unit word near a number justifies a power-of-ten gap between the normalized
# value and the quote token (value 21600 USD millions <-> "$21.6 billion"). A
# bare table number like "17,739" carries no such word, so a power-of-ten gap
# there is a misparse, not a legitimate rescale.
_SCALE_WORD_RE = re.compile(
    r"\b(?:trillion|billion|million|thousand)s?\b|\b[bmk]n\b",
    flags=re.IGNORECASE,
)

# Financial statements declare a table-wide scale ("amounts in thousands"). That
# header -- not a word beside the figure -- is what makes a bare "12,050,762"
# legitimately equal $12,050.762 million. Detected on the *cited* page only,
# since a single filing mixes scales (summary in millions, statements in
# thousands).
_TABLE_SCALE_PATTERNS = (
    ("thousand", re.compile(r"in thousands|thousands,\s*except|\$ in thousands", re.I)),
    ("million", re.compile(r"in millions|millions,\s*except|\$ in millions", re.I)),
)

# Integers written with thousands separators -- "12,050,762", "17,739".
_COMMA_INTEGER_RE = re.compile(r"\d{1,3}(?:,\d{3})+")

# Currency metrics eligible for table-scale repair (narrative/percent excluded).
_SCALE_REPAIR_FIELDS = (
    "Total revenue",
    "Net income",
    "Operating income",
    "Operating expenses",
    "Operating cash flow",
    "Capital expenditures",
    "Free cash flow",
)


def detect_table_scale(page_text: str | None) -> str | None:
    """Return ``"thousand"``/``"million"`` if the page declares a table scale."""

    if not page_text:
        return None
    for scale, pattern in _TABLE_SCALE_PATTERNS:
        if pattern.search(page_text):
            return scale
    return None


def repair_table_scale(metrics: list[MetricRow], pages: list[PageText] | None) -> None:
    """Repair decimal/comma misparses using the cited page's table scale.

    The language model occasionally reads a statement integer like "12,050,762"
    (in a thousands table) as the decimal "12.050762" -- 1,000,000x too small.
    When the value's significant digits match an integer in its own quote but the
    magnitude is a power of ten off, and the cited page declares a table scale,
    rewrite the value to that integer at the table's native scale. Normalization
    then yields the correct USD-millions figure. The row is flagged so a human
    still confirms the repair; an already-correct value is left untouched.
    """

    if not pages:
        return
    page_text_by_number = {page.page_number: page.text for page in pages}
    for metric in metrics:
        if metric.metric_name not in _SCALE_REPAIR_FIELDS:
            continue
        if isinstance(metric.value, bool) or not isinstance(metric.value, int | float):
            continue
        value = abs(float(metric.value))
        if value == 0 or not metric.source_quote:
            continue
        value_sig = _significant_digits(f"{value:.6f}")
        if not value_sig:
            continue
        scale = detect_table_scale(page_text_by_number.get(metric.source_page))
        if scale is None:
            continue
        for token in _COMMA_INTEGER_RE.findall(metric.source_quote):
            integer = float(token.replace(",", ""))
            if _significant_digits(str(int(integer))) != value_sig:
                continue
            expected_millions = integer / 1000.0 if scale == "thousand" else integer
            if _close(value, expected_millions):
                break  # already at the right scale -- nothing to repair
            sign = -1.0 if float(metric.value) < 0 else 1.0
            metric.value = sign * integer
            metric.scale = "thousands" if scale == "thousand" else "millions"
            metric.unit = "USD"
            _flag(
                metric,
                "Repaired a decimal/comma misparse using the page's table scale; "
                "verify the value against the source.",
            )
            break


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

    for page in pages:
        summary = _capital_return_summary(page.text)
        if summary is not None:
            value, quote = summary
            row.value = value
            row.unit = None
            row.scale = None
            row.source_page = page.page_number
            row.source_quote = quote
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
        ):
            status = _grounding_status(
                metric.value,
                metric.source_quote,
                detect_table_scale(page_text_by_number.get(metric.source_page)),
            )
            if status == "scale_mismatch":
                _flag(
                    metric,
                    "Reported value matches its source quote's digits but is a "
                    "power of ten away with no unit word to justify it; verify "
                    "scale (possible comma/decimal misparse).",
                )
            elif status == "not_found":
                _flag(metric, "Reported value not found in its source quote.")
        if metric.value in (None, "") and metric.metric_name in TEMPLATE_FIELDS:
            _flag(metric, "Template field is blank and requires review.")
        if metric.metric_name == CAPITAL_RETURN_FIELD and is_bare_number(
            metric.value
        ):
            _flag(
                metric,
                "Buybacks and dividends is a narrative field but holds a bare "
                "number; needs a verified buyback/dividend description before it "
                "can reach the client sheet.",
            )

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




def _capital_return_summary(text: str) -> tuple[str, str] | None:
    page_text = _normalize_ws(text)
    capital_patterns = (
        (
            re.compile(
                r"\$([\d.]+)\s+billion\s+returned\s+to\s+shareholders\s+"
                r"in\s+(\d{4}),\s+including\s+\$([\d.]+)\s+billion\s+"
                r"(?:worth\s+of\s+)?share\s+.{0,160}?repurchases",
                flags=re.IGNORECASE,
            ),
            "returned_with_year",
        ),
        (
            re.compile(
                r"returned\s+\$([\d.]+)\s+billion\s+in\s+capital.*?"
                r"including\s+\$([\d.]+)\s+billion\s+of\s+"
                r"(?:buybacks|share\s+repurchases|repurchases)",
                flags=re.IGNORECASE,
            ),
            "returned_capital",
        ),
    )
    dividend = _quarterly_dividend_per_share(page_text)
    for pattern, kind in capital_patterns:
        match = pattern.search(page_text)
        if not match:
            continue
        if kind == "returned_with_year":
            capital, year, repurchases = match.groups()
            value = (
                f"${capital} billion returned in {year}, including "
                f"${repurchases} billion of repurchases"
            )
        else:
            capital, repurchases = match.groups()
            value = (
                f"${capital}B capital returned, including "
                f"${repurchases}B buybacks"
            )
        quote = _trim_quote(match.group(0))
        if dividend is not None:
            dividend_value, dividend_quote = dividend
            value += f" and ${dividend_value} quarterly cash dividend per share"
            quote = _trim_quote(f"{quote}; {dividend_quote}")
        return value, quote
    return None


def _quarterly_dividend_per_share(text: str) -> tuple[str, str] | None:
    patterns = (
        re.compile(
            r"quarterly\s+cash\s+dividend\s+(?:was\s+)?(?:increased\s+)?"
            r"(?:\d+%\s+)?(?:to|of)?\s*\$([\d.]+)\s+per\s+share",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(?:\d+%\s+)?increase\s+in\s+quarterly\s+cash\s+dividend\s+"
            r"(?:to|of)\s+\$([\d.]+)\s+per\s+share",
            flags=re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1), _trim_quote(match.group(0))
    return None


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= max(1.0, abs(right) * 0.001)


def _grounding_status(
    value: float, quote: str, table_scale: str | None = None
) -> str:
    """Classify how a reported number relates to the numbers in its quote.

    Significant-digit matching alone is scale-blind: a misparsed ``17.739``
    (comma read as a decimal point) shares digits with the quote token
    ``17,739`` even though it is 1000x too small. To tell a legitimate
    cross-scale match from a decimal/comma error, this compares magnitudes too.

    Returns one of:

    ``"grounded"``
        A quote token matches the value's significant digits at the same scale
        (ratio ~1), or a power of ten apart **with a unit word present** -- e.g.
        a normalized ``21600`` (USD millions) grounded in ``"$21.6 billion"``.
    ``"scale_mismatch"``
        A quote token shares the value's significant digits but sits a power of
        ten away with no unit word to justify it -- the Morgan Stanley
        ``17.739`` vs ``"17,739"`` failure. Routed to review, never passed.
    ``"not_found"``
        No quote token shares the value's significant digits.
    """

    target_value = abs(float(value))
    target_sig = _significant_digits(f"{target_value:.6f}")
    if not target_sig:
        return "grounded"

    has_scale_word = bool(_SCALE_WORD_RE.search(quote))
    digit_match_found = False
    for token in _NUMBER_TOKEN_RE.findall(quote):
        cleaned = token.replace(",", "")
        if _significant_digits(cleaned) != target_sig:
            continue
        digit_match_found = True
        try:
            token_value = abs(float(cleaned))
        except ValueError:
            continue
        if token_value == 0 or target_value == 0:
            return "grounded"
        ratio = max(token_value, target_value) / min(token_value, target_value)
        if ratio < 1.5:
            return "grounded"  # same scale -- an exact match
        if has_scale_word:
            return "grounded"  # a power of ten apart, justified by units
        # A table-wide scale header justifies the gap, but only at the exact
        # implied scale: a USD-millions value must equal the quote integer / 1000
        # (thousands table) or the integer itself (millions table).
        if table_scale == "thousand" and _close(target_value, token_value / 1000.0):
            return "grounded"
        if table_scale == "million" and _close(target_value, token_value):
            return "grounded"

    if digit_match_found:
        return "scale_mismatch"
    return "not_found"


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
