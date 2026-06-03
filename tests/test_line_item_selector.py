"""Selector behaviour proven without a live model.

A stub client returns a canned ``LineSelection`` so these tests exercise the
deterministic cages -- candidate matching and grounding -- and the definition
plumbing, deterministically and offline. The point: prove the selector REJECTS
the real wrong-line traps (Marathon "revenues and other income", PepsiCo SG&A)
and ACCEPTS the correct line, independent of any API.
"""

from __future__ import annotations

from types import SimpleNamespace

from earnings_extractor.config import OpenAIConfig
from earnings_extractor.line_item_selector import (
    LineSelection,
    SelectionResult,
    extract_candidate_lines,
    resolve_line_item,
)

CONFIG = OpenAIConfig(api_key="test", model="test-model", reasoning_effort="low")


class StubClient:
    """Mimics ``client.responses.parse`` and returns a preset verdict."""

    def __init__(self, verdict: LineSelection) -> None:
        self._verdict = verdict
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(output_parsed=self._verdict)


# Real Marathon income-statement shape: the trap line and the true revenue line.
MARATHON_PAGE = (
    "Sales and other operating revenues 34,200 31,520\n"
    "Income from equity method investments 120 95\n"
    "Net gain on disposal of assets 248 12\n"
    "Total revenues and other income 34,568 31,627\n"
    "Cost of revenues 30,100 29,400\n"
)

# Real PepsiCo shape: only SG&A is shown, no operating-expense subtotal.
PEPSICO_PAGE = (
    "Net revenue 17,919 18,250\n"
    "Cost of sales 7,929 8,000\n"
    "Selling, general and administrative expenses 7,410 7,100\n"
    "Operating profit 2,583 3,000\n"
)


def test_candidate_extraction_pulls_labelled_value_lines() -> None:
    candidates = extract_candidate_lines(MARATHON_PAGE, "Total revenue")
    labels = [c.label for c in candidates]
    assert "Total revenues and other income" in labels
    assert "Sales and other operating revenues" in labels


def test_selects_true_revenue_line_when_model_picks_it() -> None:
    verdict = LineSelection(
        status="selected",
        label="Sales and other operating revenues",
        value_text="Sales and other operating revenues 34,200 31,520",
        reason="primary operating revenue, excludes other income",
    )
    result = resolve_line_item(
        "Total revenue", MARATHON_PAGE, CONFIG, "live", client=StubClient(verdict)
    )
    assert isinstance(result, SelectionResult)
    assert result.status == "selected"
    assert "34,200" in (result.value_text or "")


def test_rejects_value_not_in_candidates() -> None:
    # Model hallucinates a figure absent from the page -> cage 1 discards it.
    verdict = LineSelection(
        status="selected",
        label="Total revenue",
        value_text="99,999",
        reason="made up",
    )
    result = resolve_line_item(
        "Total revenue", MARATHON_PAGE, CONFIG, "live", client=StubClient(verdict)
    )
    assert result is not None and result.status == "rejected"


def test_operating_expenses_not_disclosed_when_only_sga_present() -> None:
    # The correct answer for PepsiCo: only SG&A (a component) exists, so the
    # definition says not_disclosed. A competent model returns that; we assert
    # the selector surfaces it as a clean refusal, not a wrong number.
    verdict = LineSelection(
        status="not_disclosed",
        reason="only SG&A shown; SG&A is a component, not total operating expenses",
    )
    result = resolve_line_item(
        "Operating expenses", PEPSICO_PAGE, CONFIG, "live", client=StubClient(verdict)
    )
    assert result is not None and result.status == "not_disclosed"


def test_returns_none_for_field_without_definition() -> None:
    verdict = LineSelection(status="not_disclosed")
    result = resolve_line_item(
        "Earnings per share", PEPSICO_PAGE, CONFIG, "live", client=StubClient(verdict)
    )
    assert result is None


def test_returns_none_in_recorded_mode() -> None:
    verdict = LineSelection(status="selected", value_text="34,200")
    result = resolve_line_item(
        "Total revenue", MARATHON_PAGE, CONFIG, "recorded", client=StubClient(verdict)
    )
    assert result is None


# --- apply_line_item_selection: the in-place pipeline integration --------------

from earnings_extractor.line_item_selector import apply_line_item_selection


class MetricStub(SimpleNamespace):
    pass


class PageStub(SimpleNamespace):
    pass


class RoutingStubClient:
    """Returns a different verdict per metric, keyed off the prompt text."""

    def __init__(self, by_metric: dict[str, LineSelection]) -> None:
        self._by_metric = by_metric
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs: object) -> SimpleNamespace:
        prompt = str(kwargs.get("input"))
        for metric, verdict in self._by_metric.items():
            if f"Metric: {metric}" in prompt:
                return SimpleNamespace(output_parsed=verdict)
        return SimpleNamespace(output_parsed=LineSelection(status="not_disclosed"))


# A thousands-scale statement: header declares the scale, revenue line is raw.
THOUSANDS_PAGE_TEXT = (
    "Consolidated Statement of Operations (in thousands)\n"
    "Revenue 276,877 235,000\n"
    "Cost of revenues 89,000 80,000\n"
    "Operating expenses 129,525 120,000\n"
)


def test_replacement_converts_thousands_to_millions() -> None:
    # Selector re-confirms the revenue line on a thousands table. The raw figure
    # 276,877 must land as 276.877 USD millions, not 276,877 -- the bug that
    # corrupted Cognex/Danaos. Here it matches the extractor's 277 -> skip.
    metric = MetricStub(
        metric_name="Total revenue", value=277.0, unit="USD", scale="millions",
        source_page=1, source_quote="x", needs_review=False, review_reason=None,
    )
    pages = [PageStub(page_number=1, text=THOUSANDS_PAGE_TEXT)]
    verdict = LineSelection(status="selected", label="Revenue", value_text="Revenue 276,877 235,000")
    changed = apply_line_item_selection(
        [metric], pages, CONFIG, "live", client=RoutingStubClient({"Total revenue": verdict})
    )
    assert changed == 0  # 276,877 thousands == 277 millions -> agrees, no change
    assert metric.value == 277.0


def test_keeps_extractor_value_when_present_in_selected_line() -> None:
    # Amazon column-order guard: line is "Total net sales 143,313 155,667" with
    # prior-year first. The extractor already has the current 155,667; the
    # selector must NOT swap it for the first (prior-year) number 143,313.
    page = "Consolidated Statements (in millions)\nTotal net sales 143,313 155,667\n"
    metric = MetricStub(
        metric_name="Total revenue", value=155667.0, unit="USD", scale="millions",
        source_page=1, source_quote="x", needs_review=False, review_reason=None,
    )
    pages = [PageStub(page_number=1, text=page)]
    verdict = LineSelection(status="selected", label="Total net sales", value_text="Total net sales 143,313 155,667")
    changed = apply_line_item_selection(
        [metric], pages, CONFIG, "live", client=RoutingStubClient({"Total revenue": verdict})
    )
    assert changed == 0
    assert metric.value == 155667.0


def test_skips_transcript_documents() -> None:
    # Citi guard: on a transcript the value lives in prose. The selector must not
    # run -- the extractor's value is kept.
    prose = "of $1.96 and an RoTCE of 9.1% on $21.6 billion of revenues, generating\n"
    metric = MetricStub(
        metric_name="Total revenue", value=21600.0, unit="USD", scale="millions",
        source_page=1, source_quote="x", needs_review=False, review_reason=None,
        document_type="earnings_call_transcript",
    )
    pages = [PageStub(page_number=1, text=prose)]
    verdict = LineSelection(status="selected", value_text="of $1.96 and an RoTCE of 9.1% on $21.6 billion of revenues")
    changed = apply_line_item_selection(
        [metric], pages, CONFIG, "live", client=RoutingStubClient({"Total revenue": verdict})
    )
    assert changed == 0
    assert metric.value == 21600.0


def test_magnitude_guard_blocks_implausible_swap() -> None:
    # Even in a report, a >3x jump (21,600 -> 1.96 from a prose first-number) is
    # rejected; the plausible extracted value stays.
    prose = "...RoTCE of 9.1% on $21.6 billion of revenues...\n"
    metric = MetricStub(
        metric_name="Total revenue", value=21600.0, unit="USD", scale="millions",
        source_page=1, source_quote="x", needs_review=False, review_reason=None,
    )
    pages = [PageStub(page_number=1, text=prose)]
    verdict = LineSelection(status="selected", value_text="9.1% on $21.6 billion of revenues")
    changed = apply_line_item_selection(
        [metric], pages, CONFIG, "live", client=RoutingStubClient({"Total revenue": verdict})
    )
    assert changed == 0
    assert metric.value == 21600.0


def test_operating_expenses_value_is_never_swapped() -> None:
    # The Amazon regression guard: even if the selector picks a different opex
    # line, a correct numeric opex is NOT replaced. Only blanking is allowed.
    metric = MetricStub(
        metric_name="Operating expenses", value=137262.0, unit="USD", scale="millions",
        source_page=1, source_quote="x", needs_review=False, review_reason=None,
    )
    pages = [PageStub(page_number=1, text=THOUSANDS_PAGE_TEXT)]
    verdict = LineSelection(status="selected", label="Operating expenses", value_text="Operating expenses 129,525")
    changed = apply_line_item_selection(
        [metric], pages, CONFIG, "live", client=RoutingStubClient({"Operating expenses": verdict})
    )
    assert changed == 0
    assert metric.value == 137262.0  # untouched


def test_operating_expenses_blanked_when_not_disclosed() -> None:
    # The PepsiCo win: SG&A-only page -> selector returns not_disclosed -> the
    # wrong number is blanked and flagged, not shipped.
    metric = MetricStub(
        metric_name="Operating expenses", value=7410.0, unit="USD", scale="millions",
        source_page=1, source_quote="x", needs_review=False, review_reason=None,
    )
    pages = [PageStub(page_number=1, text=PEPSICO_PAGE)]
    verdict = LineSelection(status="not_disclosed", reason="only SG&A present")
    changed = apply_line_item_selection(
        [metric], pages, CONFIG, "live", client=RoutingStubClient({"Operating expenses": verdict})
    )
    assert changed == 1
    assert metric.value is None
    assert metric.needs_review is True
