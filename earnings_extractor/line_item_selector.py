"""Definition-driven line-item selection -- the generalizing fix for wrong-line
extractions.

The extractor reads a value and a quote, but it routinely picks a line that is
*adjacent in meaning* to the template field rather than the field itself:
"Total revenues **and other income**" for Total revenue (Marathon), "selling,
general and administrative expenses" for Operating expenses (PepsiCo), a single
"Total Expense" line for the operating-expense subtotal (CSX). A whitelist of
accepted phrasings does not survive the next filer's wording -- that is the
overfitting trap ``verifier.py`` already names.

This module fixes the *selection* without hand-written phrase lists. It hands the
model the candidate lines that actually appear on the cited page plus a written
**definition** of the metric (what to include, what to exclude) and asks it to
pick the one line that matches the definition, or to answer ``not_disclosed``.
Meaning generalizes where string-matching cannot: a model that understands "SG&A
is a *component*, not total operating expenses" rejects the trap regardless of
how the filer phrases it.

The model only *selects*; it never mints a number. Two deterministic cages keep
it honest:

* the returned value must be one of the candidate values lifted from the page
  (a closed set -- the model cannot invent a figure), and
* the returned value's digits must be grounded in the page text
  (``validation._grounding_status``).

Live runs only. Selections are pure functions of (definition, page text), so a
caller may cache them by input hash to keep recorded output reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from earnings_extractor.config import OpenAIConfig
from earnings_extractor.validation import _grounding_status, detect_table_scale

# Fields where the selector may swap one number for another. Restricted to the
# clean composite trap (Total revenue); for other defined fields the selector
# only blanks a component-only cell, never replaces a value the extractor found.
VALUE_REPLACEMENT_FIELDS = frozenset({"Total revenue"})

# A metric definition is the include/exclude contract the model selects against.
# Definitions, not example phrasings -- this is what generalizes to unseen docs.
FieldDefinition = str

FIELD_DEFINITIONS: dict[str, FieldDefinition] = {
    "Total revenue": (
        "The company's primary top-line revenue from sales or services for the "
        "reported period (a 'Total revenue', 'Net revenue', or 'Total net "
        "revenues' line).\n"
        "INCLUDE the single consolidated revenue line for the whole company.\n"
        "EXCLUDE a composite line that adds non-revenue items, e.g. 'Total "
        "revenues AND other income' or 'revenues and other income' -- that is "
        "revenue plus other income, not revenue. EXCLUDE segment-only revenue "
        "and prior-year columns."
    ),
    "Operating expenses": (
        "The company's TOTAL operating expenses for the reported period.\n"
        "INCLUDE a subtotal line such as 'Total operating expenses', 'Total "
        "costs and expenses', or 'Operating expenses' that sums the period's "
        "operating costs.\n"
        "EXCLUDE a single component of operating expense -- selling, general "
        "and administrative expenses (SG&A), cost of revenue/sales alone, "
        "research and development alone, or marketing alone. A component is NOT "
        "the total. If only components are shown and no operating-expense "
        "subtotal exists, answer not_disclosed."
    ),
}

SYSTEM_PROMPT = (
    "You select the ONE line from a financial statement that matches a metric's "
    "definition, for a client spreadsheet.\n"
    "You are given the metric definition and the candidate lines found on the "
    "cited page. Choose the single line whose meaning matches the definition.\n"
    "Rules:\n"
    "- Judge by MEANING against the definition, not by surface wording.\n"
    "- If a candidate is close but excluded by the definition (a component, a "
    "composite, a segment, a prior-year figure), do NOT select it.\n"
    "- If no candidate line matches the definition, set status='not_disclosed'.\n"
    "- Never invent a number. The value you return must be copied verbatim from "
    "one of the candidate lines.\n"
    "- Return the period's current-quarter figure, never a prior-year column."
)


@dataclass(frozen=True)
class CandidateLine:
    """One statement line offered to the selector: its label and raw value text."""

    label: str
    value_text: str


class LineSelection(BaseModel):
    """The model's pick of which candidate line matches the metric definition."""

    model_config = ConfigDict(extra="forbid")

    status: str  # "selected" | "not_disclosed"
    label: str | None = None
    value_text: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SelectionResult:
    """Caged outcome the pipeline can act on.

    ``status`` is ``"selected"`` only when the model picked a line AND both cages
    passed; ``"not_disclosed"`` when the model declined or no line matched;
    ``"rejected"`` when the model picked a value the cages could not ground (the
    selection is discarded and the cell routes to review).
    """

    status: str
    label: str | None = None
    value_text: str | None = None
    reason: str | None = None


# A number token like "34,568", "3.96", "21.6", "1,253".
_NUMBER_RE = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?")


def extract_candidate_lines(
    page_text: str, metric_name: str, max_lines: int = 40
) -> list[CandidateLine]:
    """Pull statement lines from page text that plausibly bear ``metric_name``.

    A candidate is any line that contains both word characters (a label) and a
    number token (a value). This is deliberately broad -- the model does the
    semantic narrowing; this step only bounds the prompt and guarantees every
    offered value is present on the page, which the cage later re-checks.
    """

    candidates: list[CandidateLine] = []
    for raw in page_text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or not re.search(r"[A-Za-z]", line):
            continue
        numbers = _NUMBER_RE.findall(line)
        if not numbers:
            continue
        label = _NUMBER_RE.sub("", line).strip(" .:-\t")
        if not label:
            continue
        candidates.append(CandidateLine(label=label, value_text=line))
        if len(candidates) >= max_lines:
            break
    return candidates


def select_line_item_live(
    metric_name: str,
    candidates: list[CandidateLine],
    config: OpenAIConfig,
    client: Any | None = None,
) -> LineSelection | None:
    """Ask the model which candidate line matches the metric's definition.

    Returns the raw (un-caged) model verdict, or ``None`` when there is no
    definition for the field or no candidates to choose from. The caller is
    ``resolve_line_item`` which applies the grounding cages.
    """

    definition = FIELD_DEFINITIONS.get(metric_name)
    if definition is None or not candidates:
        return None

    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=config.api_key)

    listing = "\n".join(
        f"- label: {c.label!r} | line: {c.value_text!r}" for c in candidates
    )
    user = (
        f"Metric: {metric_name}\n"
        f"Definition:\n{definition}\n\n"
        f"Candidate lines on the cited page:\n{listing}"
    )
    response = client.responses.parse(
        model=config.model,
        reasoning={"effort": config.reasoning_effort},
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        text_format=LineSelection,
    )
    return response.output_parsed


def resolve_line_item(
    metric_name: str,
    page_text: str,
    config: OpenAIConfig | None,
    mode: str,
    client: Any | None = None,
) -> SelectionResult | None:
    """Pick the definition-correct line for ``metric_name``, fully caged.

    Returns ``None`` when the field has no definition (the selector does not
    apply) or the run is not live. Otherwise returns a ``SelectionResult`` whose
    status is ``selected`` only if the model chose a candidate line whose value
    is both one of the offered candidates and grounded in the page text.
    """

    if mode != "live" or config is None:
        return None
    if metric_name not in FIELD_DEFINITIONS:
        return None

    candidates = extract_candidate_lines(page_text, metric_name)
    if not candidates:
        return None

    try:
        verdict = select_line_item_live(metric_name, candidates, config, client)
    except Exception:
        return None
    if verdict is None:
        return None

    if verdict.status == "not_disclosed":
        return SelectionResult(status="not_disclosed", reason=verdict.reason)

    # Cage 1: the chosen value must be one of the candidate lines we offered --
    # the model cannot return a figure that was not on the page.
    chosen = _match_candidate(verdict.value_text, candidates)
    if chosen is None:
        return SelectionResult(
            status="rejected",
            reason="Selected value is not one of the page's candidate lines.",
        )

    # Cage 2: the chosen value's digits must be grounded in the chosen line.
    number = _first_number(chosen.value_text)
    if number is not None and _grounding_status(number, chosen.value_text) != "grounded":
        return SelectionResult(
            status="rejected",
            reason="Selected value is not grounded in its source line.",
        )

    return SelectionResult(
        status="selected",
        label=chosen.label,
        value_text=chosen.value_text,
        reason=verdict.reason,
    )


def apply_line_item_selection(
    metrics: list,
    pages: list,
    config: OpenAIConfig | None,
    mode: str,
    client: Any | None = None,
) -> int:
    """Correct wrong-line extractions in place for defined fields.

    For each metric whose field has a definition (Total revenue, Operating
    expenses), re-select the definition-matching line from the cited page:

    * ``selected`` and different from the current value -> replace the value and
      quote with the chosen line and flag for review;
    * ``not_disclosed`` while a value is present -> the extractor filled a
      component/composite the definition rejects; blank it and flag, so a wrong
      number becomes a correct "needs review" instead of reaching the client.

    The replacement value inherits the metric's existing unit/scale: the chosen
    line sits in the same statement/table as the line the extractor first read,
    so normalization already validated that scale. Returns how many cells changed.
    Live-only; never edits a value the cages could not ground.
    """

    if mode != "live" or config is None:
        return 0

    page_text_by_number = {p.page_number: p.text for p in pages}
    full_text = "\n".join(p.text for p in pages)
    changed = 0
    for metric in metrics:
        if metric.metric_name not in FIELD_DEFINITIONS:
            continue
        page_text = page_text_by_number.get(metric.source_page) or full_text
        if not extract_candidate_lines(page_text, metric.metric_name):
            page_text = full_text
        result = resolve_line_item(
            metric.metric_name, page_text, config, mode, client
        )
        if result is None:
            continue

        if result.status == "selected":
            # Swapping one number for another is only safe where the trap is a
            # clean composite (Total revenue's "...and other income"). For other
            # fields the extractor's pick is usually right and a swap risks a
            # regression (e.g. Amazon's correct total operating expenses), so the
            # selector only *blanks* those via the not_disclosed branch below.
            if metric.metric_name not in VALUE_REPLACEMENT_FIELDS:
                continue
            # If the extractor's value already appears in the chosen line, the
            # extractor was on the right line AND the right column -- keep it.
            # A multi-column statement line ("Total net sales 143,313 155,667")
            # lists prior-year first; blindly taking the first number would swap
            # the correct current figure for last year's. Only override when the
            # extractor's number is absent, i.e. it used a different, wrong line.
            current = metric.value
            if isinstance(current, int | float):
                current_digits = _digits(f"{abs(float(current)):.0f}")
                if current_digits and current_digits in _digits(result.value_text or ""):
                    continue
            number = _first_number(result.value_text or "")
            if number is None:
                continue
            # Convert the raw page figure to USD millions using the cited page's
            # own table scale, then set unit/scale explicitly. The chosen line
            # shares the statement's scale; without this a thousands-table figure
            # (Cognex/Danaos "276,877") would land 1000x too large and reach
            # export with no unit. This mirrors normalize's millions convention.
            page_scale = detect_table_scale(page_text_by_number.get(metric.source_page))
            value_millions = number / 1000.0 if page_scale == "thousand" else number
            current = metric.value
            if isinstance(current, int | float) and abs(
                float(current) - value_millions
            ) <= max(1.0, abs(value_millions) * 0.001):
                continue  # selector agrees with the extractor -- nothing to do
            metric.value = value_millions
            metric.unit = "USD"
            metric.scale = "millions"
            metric.source_quote = (result.value_text or metric.source_quote)[:240]
            metric.needs_review = True
            metric.review_reason = _append_reason(
                metric.review_reason,
                "Line-item selector replaced the value with the "
                f"definition-matching line ('{result.label}'); verify against "
                "the source.",
            )
            changed += 1
        elif result.status == "not_disclosed" and metric.value not in (None, ""):
            metric.value = None
            metric.needs_review = True
            metric.review_reason = _append_reason(
                metric.review_reason,
                "Line-item selector found no line matching the metric definition "
                "(only components or composites present); value blanked pending "
                "review.",
            )
            changed += 1
    return changed


def _append_reason(existing: str | None, reason: str) -> str:
    if not existing:
        return reason
    if reason in existing:
        return existing
    return f"{existing}; {reason}"


def _match_candidate(
    value_text: str | None, candidates: list[CandidateLine]
) -> CandidateLine | None:
    """Return the candidate whose value digits match the model's returned value.

    Tolerant of the model echoing the label, the line, or just the number: a
    candidate matches when the model's digit string is a substring of, or equal
    to, the candidate line's digit string.
    """

    if not value_text:
        return None
    needle = _digits(value_text)
    if not needle:
        return None
    for candidate in candidates:
        haystack = _digits(candidate.value_text)
        if needle and needle in haystack:
            return candidate
    return None


def _first_number(text: str) -> float | None:
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    try:
        return float(match.group(0).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _digits(text: str) -> str:
    return re.sub(r"[^\d]", "", text)
