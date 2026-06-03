"""Buyback/dividend narrative resolution: model pass + deterministic guards."""

from __future__ import annotations

from dataclasses import dataclass

from earnings_extractor.capital_return import (
    CapitalReturnSummary,
    is_bare_number,
    narrative_from_quote,
    narrative_looks_mangled,
    resolve_capital_return_narrative,
)
from earnings_extractor.config import OpenAIConfig

CONFIG = OpenAIConfig(api_key="test", model="gpt-test", reasoning_effort="low")
# Deterministic deriver recognizes this shape; the model must never be called.
WF_QUOTE = "Repurchased 46.3 million shares, or $4.0 billion, of common stock"
# Phrasing the regex cannot parse ("4.0bn", no "$ ... billion"): the tail the
# model exists to cover.
TAIL_QUOTE = "Returned 4.0bn to investors entirely through buybacks this period"


@dataclass
class _FakeResponse:
    output_parsed: CapitalReturnSummary | None


class _FakeClient:
    """Minimal stand-in for the OpenAI client used by the narrative pass."""

    def __init__(self, summary: str | None, *, raises: bool = False) -> None:
        self._summary = summary
        self._raises = raises
        self.calls = 0
        self.responses = self

    def parse(self, **_: object) -> _FakeResponse:
        self.calls += 1
        if self._raises:
            raise RuntimeError("model unavailable")
        return _FakeResponse(CapitalReturnSummary(summary=self._summary))


def test_is_bare_number_distinguishes_numbers_from_prose() -> None:
    assert is_bare_number(2100.0) is True
    assert is_bare_number("3,536.396") is True
    assert is_bare_number("$4000") is True
    assert is_bare_number("$4.0 billion of common stock repurchased") is False
    assert is_bare_number(None) is False


def test_deterministic_first_handles_known_shape_without_model() -> None:
    # The structured Wells Fargo quote is the regex's job. Even live, the model
    # must not be called -- deterministic output is clean and reproducible.
    client = _FakeClient("model output that should be ignored")

    result = resolve_capital_return_narrative(
        value=4000.0,
        quote=WF_QUOTE,
        context_text=WF_QUOTE,
        config=CONFIG,
        mode="live",
        client=client,
    )

    assert result == "$4.0 billion of common stock repurchased"
    assert client.calls == 0


def test_model_fills_tail_the_regex_cannot_parse() -> None:
    client = _FakeClient("$4.0 billion returned to shareholders")

    result = resolve_capital_return_narrative(
        value=None,
        quote=TAIL_QUOTE,
        context_text=TAIL_QUOTE,
        config=CONFIG,
        mode="live",
        client=client,
    )

    assert result == "$4.0 billion returned to shareholders"
    assert client.calls == 1


def test_model_hallucinated_number_is_rejected() -> None:
    # "$9.9 billion" is absent from the source; the grounding guard drops it.
    # Deterministic could not parse this tail quote either, so the cell is empty.
    client = _FakeClient("$9.9 billion returned to shareholders")

    result = resolve_capital_return_narrative(
        value=None,
        quote=TAIL_QUOTE,
        context_text=TAIL_QUOTE,
        config=CONFIG,
        mode="live",
        client=client,
    )

    assert result is None


def test_model_failure_on_tail_quote_yields_no_cell() -> None:
    client = _FakeClient(None, raises=True)

    result = resolve_capital_return_narrative(
        value=None,
        quote=TAIL_QUOTE,
        context_text=TAIL_QUOTE,
        config=CONFIG,
        mode="live",
        client=client,
    )

    assert result is None


def test_recorded_mode_never_calls_the_model() -> None:
    client = _FakeClient("should not be used")

    # Known shape resolves deterministically; tail shape stays empty (no model).
    assert (
        resolve_capital_return_narrative(
            value=4000.0,
            quote=WF_QUOTE,
            context_text=WF_QUOTE,
            config=None,
            mode="recorded",
            client=client,
        )
        == "$4.0 billion of common stock repurchased"
    )
    assert (
        resolve_capital_return_narrative(
            value=None,
            quote=TAIL_QUOTE,
            context_text=TAIL_QUOTE,
            config=None,
            mode="recorded",
            client=client,
        )
        is None
    )
    assert client.calls == 0


def test_narrative_looks_mangled_detects_unscaled_large_amount() -> None:
    # "$1,000" for $1.0 billion -- a large amount with no scale word.
    assert narrative_looks_mangled("Repurchases $1,000; quarterly dividend $0.925")
    # Clean narratives with scale words (or small per-share figures) are fine.
    assert not narrative_looks_mangled(
        "$5.0 billion returned, including $1.6 billion of repurchases; $5.73 dividend"
    )
    assert not narrative_looks_mangled("$375 million of share repurchases")
    assert not narrative_looks_mangled("$0.50 dividend per common share")
    # A bare numeric fragment with no "$<amount>" ("~17.6") is also malformed.
    assert narrative_looks_mangled("~17.6")
    assert not narrative_looks_mangled("Not disclosed in this release")


def test_malformed_fragment_is_replaced_from_quote() -> None:
    # Citigroup regression: the buyback cell rendered as the fragment "~17.6".
    result = resolve_capital_return_narrative(
        value="~17.6",
        quote="RETURNED ~$17.6 BILLION IN COMMON SHARE REPURCHASES AND DIVIDENDS",
        context_text="...",
        config=CONFIG,
        mode="recorded",
        client=_FakeClient("ignored"),
    )

    assert result == "$17.6 billion returned to shareholders"


def test_mangled_narrative_is_replaced_from_quote() -> None:
    # Morgan Stanley regression: the model wrote "$1.0 billion" as "$1,000".
    # The deterministic quote reading must override it.
    result = resolve_capital_return_narrative(
        value="Repurchases $1,000; quarterly dividend $0.925 per share",
        quote="The Firm repurchased $1.0 billion of its outstanding common stock",
        context_text="...",
        config=CONFIG,
        mode="live",
        client=_FakeClient("ignored"),
    )

    assert result == "$1.0 billion of common stock repurchased"


def test_existing_narrative_value_is_left_untouched() -> None:
    client = _FakeClient("$1.0 billion of common stock repurchased")

    result = resolve_capital_return_narrative(
        value="$5.0 billion returned, including $1.6 billion of repurchases",
        quote="...",
        context_text="...",
        config=CONFIG,
        mode="live",
        client=client,
    )

    assert result is None  # keep the extractor's usable narrative
    assert client.calls == 0


def test_deterministic_deriver_returns_none_without_amount() -> None:
    assert narrative_from_quote("does not mention buybacks or dividends") is None
    assert narrative_from_quote("executing its share repurchase program") is None
