"""Language-model verification tier: flags semantic mismatches, never edits."""

from __future__ import annotations

from dataclasses import dataclass

from earnings_extractor.config import OpenAIConfig
from earnings_extractor.schema import MetricRow
from earnings_extractor.verifier import (
    VerificationVerdict,
    verify_template_metrics,
)

CONFIG = OpenAIConfig(api_key="test", model="gpt-test", reasoning_effort="low")


@dataclass
class _FakeResponse:
    output_parsed: VerificationVerdict | None


class _FakeClient:
    """Returns a fixed verdict for every call; counts calls."""

    def __init__(self, verdict: VerificationVerdict | None, *, raises: bool = False):
        self._verdict = verdict
        self._raises = raises
        self.calls = 0
        self.responses = self

    def parse(self, **_: object) -> _FakeResponse:
        self.calls += 1
        if self._raises:
            raise RuntimeError("model unavailable")
        return _FakeResponse(self._verdict)


def _metric(name: str, value: object, **kw: object) -> MetricRow:
    return MetricRow(
        document_type="earnings_report",
        metric_name=name,
        value=value,
        source_page=1,
        source_quote=kw.get("quote", "source quote"),  # type: ignore[arg-type]
        confidence=0.99,
        needs_review=False,
        fiscal_period=kw.get("period"),  # type: ignore[arg-type]
    )


def test_disagreement_flags_the_cell_with_the_issue() -> None:
    # Citigroup regression shape: the value is the full-year figure, not the
    # quarter. Deterministic rules pass it; the verifier catches the period.
    metric = _metric(
        "Buybacks and dividends",
        "$17.6 billion returned",
        quote="RETURNED ~$17.6 BILLION ... IN 2025 (~$5.6 BILLION IN THE QUARTER)",
        period="Q4 2025",
    )
    client = _FakeClient(
        VerificationVerdict(agrees=False, issue="full-year figure, not the quarter")
    )

    flagged = verify_template_metrics([metric], CONFIG, "live", client)

    assert flagged == 1
    assert metric.needs_review is True
    assert "full-year figure, not the quarter" in (metric.review_reason or "")


def test_agreement_leaves_the_cell_untouched() -> None:
    metric = _metric("Total revenue", 10543.0, quote="Revenues $ 10,542,801")
    client = _FakeClient(VerificationVerdict(agrees=True))

    flagged = verify_template_metrics([metric], CONFIG, "live", client)

    assert flagged == 0
    assert metric.needs_review is False
    assert metric.review_reason is None


def test_verifier_never_changes_the_value() -> None:
    metric = _metric("Net income", 2471.0, quote="net income $ 2,471")
    client = _FakeClient(VerificationVerdict(agrees=False, issue="wrong line item"))

    verify_template_metrics([metric], CONFIG, "live", client)

    assert metric.value == 2471.0  # flagged, but the number is never edited


def test_recorded_mode_runs_no_verification() -> None:
    metric = _metric("Total revenue", 10543.0, quote="Revenues $ 10,542,801")
    client = _FakeClient(VerificationVerdict(agrees=False, issue="should not run"))

    flagged = verify_template_metrics([metric], None, "recorded", client)

    assert flagged == 0
    assert client.calls == 0
    assert metric.needs_review is False


def test_model_outage_does_not_fail_extraction() -> None:
    metric = _metric("Total revenue", 10543.0, quote="Revenues $ 10,542,801")
    client = _FakeClient(None, raises=True)

    flagged = verify_template_metrics([metric], CONFIG, "live", client)

    assert flagged == 0  # swallowed -- verification is a net, not a gate


def test_unverifiable_and_blank_cells_are_skipped() -> None:
    company = _metric("Company Name", "Citigroup")
    blank = _metric("Operating income", None)
    client = _FakeClient(VerificationVerdict(agrees=False, issue="x"))

    verify_template_metrics([company, blank], CONFIG, "live", client)

    assert client.calls == 0  # identity + blank cells are not value checks
