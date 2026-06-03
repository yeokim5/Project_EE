"""Optional language-model verification tier for extracted values.

Deterministic validation (``validation.py``) catches *known* error shapes:
scale mismatches, magnitude bounds, missing citations. It cannot catch the open
set -- a value drawn from the wrong line item, or the full-year figure where the
column wants the quarter (the Citigroup "$17.6 billion returned" case). Writing a
regex per such failure is the overfitting trap.

This tier is the general net for that open set. For each populated client cell it
asks a model one judgment question -- *does this value correctly represent its
quote, for this metric and period?* -- and, when the model disagrees, **adds a
review flag**. It never edits a value and never overrides the deterministic
checks; it only routes more suspects to a human. So a wrong verdict costs a
needless review, never a silent bad number.

Live runs only, so recorded output stays byte-for-byte reproducible.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from earnings_extractor.config import OpenAIConfig
from earnings_extractor.schema import MetricRow

# Value-bearing client fields worth a semantic check. Identity (Company Name)
# and the scalar Quarter are resolved and normalized deterministically already.
VERIFIABLE_FIELDS = (
    "Total revenue",
    "Earnings per share",
    "Net income",
    "Operating income",
    "Gross margin",
    "Operating expenses",
    "Buybacks and dividends",
)

VERIFIER_SYSTEM_PROMPT = (
    "You audit ONE extracted financial value against its source quote. Decide "
    "whether the value correctly represents the quote for the stated metric and "
    "fiscal period.\n"
    "Set agrees=false when any of these is true:\n"
    "- the number does not match the quote, or its scale/magnitude is wrong;\n"
    "- it reports a different period than asked (e.g. full-year instead of the "
    "quarter);\n"
    "- it is taken from the wrong line item for the metric.\n"
    "When agrees=false, give a one-line issue. Judge only from the quote -- never "
    "use outside knowledge or assume facts the quote does not state."
)


class VerificationVerdict(BaseModel):
    """A model's judgment on whether one value matches its source quote."""

    model_config = ConfigDict(extra="forbid")

    agrees: bool
    issue: str | None = None


def verify_metric_live(
    metric: MetricRow,
    config: OpenAIConfig,
    client: Any | None = None,
) -> VerificationVerdict | None:
    """Ask the model whether one metric's value matches its quote."""

    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=config.api_key)

    scale = f" {metric.scale}" if metric.scale else ""
    unit = f" {metric.unit}" if metric.unit else ""
    user = (
        f"Metric: {metric.metric_name}\n"
        f"Fiscal period: {metric.fiscal_period or 'the reported quarter'}\n"
        f"Extracted value: {metric.value}{unit}{scale}\n"
        f"Source quote: {metric.source_quote}"
    )
    response = client.responses.parse(
        model=config.model,
        reasoning={"effort": config.reasoning_effort},
        input=[
            {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        text_format=VerificationVerdict,
    )
    return response.output_parsed


def verify_template_metrics(
    metrics: list[MetricRow],
    config: OpenAIConfig | None,
    mode: str,
    client: Any | None = None,
) -> int:
    """Flag populated client cells whose value the model judges wrong.

    Returns the number of cells the verifier flagged. A model error or outage is
    swallowed per row -- verification is a safety net, never a gate, so it must
    not fail an extraction.
    """

    if mode != "live" or config is None:
        return 0

    flagged = 0
    for metric in metrics:
        if metric.metric_name not in VERIFIABLE_FIELDS:
            continue
        if metric.value in (None, "") or not metric.source_quote:
            continue
        try:
            verdict = verify_metric_live(metric, config, client)
        except Exception:
            continue
        if verdict is not None and not verdict.agrees:
            issue = (verdict.issue or "value may not match its source quote").strip()
            _flag(metric, f"Verifier flagged: {issue}")
            flagged += 1
    return flagged


def _flag(metric: MetricRow, reason: str) -> None:
    metric.needs_review = True
    existing = (metric.review_reason or "").strip()
    if reason in existing:
        return
    metric.review_reason = f"{existing}; {reason}" if existing else reason
