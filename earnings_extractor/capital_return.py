"""Buyback/dividend narrative resolution.

The "Buybacks and dividends" template field is narrative, not numeric, so the
extractor's ``value`` for it is unreliable -- often a bare figure or ``None``
while the real disclosure sits in prose. Turning that prose into one clean
sentence is an open-ended language task with an unbounded set of phrasings, so
it is the one field where a language model earns its keep over hand-written
patterns.

The division of labour mirrors the rest of the pipeline:

* the language model *generates* a candidate sentence from the source text
  (``summarize_capital_return_live``), live runs only;
* deterministic code *validates* it -- every ``$`` figure in the summary must
  appear in the source (``_numbers_grounded``), or the candidate is discarded;
* a deterministic regex deriver (``narrative_from_quote``) is the offline and
  fallback path, so recorded runs stay byte-for-byte reproducible.

Nothing here decides whether a value reaches the client sheet -- the export gate
still requires human approval. This only chooses the *text* once a cell is
allowed to populate.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from earnings_extractor.config import OpenAIConfig

CAPITAL_RETURN_FIELD = "Buybacks and dividends"

# A bare currency/number token with no surrounding words -- "2100.0",
# "3,536.396", "$4000". A narrative field carrying one of these holds
# unconverted raw data, never a verifiable buyback/dividend sentence.
_BARE_NUMBER_RE = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?")

# Every "$<number>" the model emits must be traceable to the source text.
_SUMMARY_AMOUNT_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")

# A "$<number>" with an optional trailing scale word. A buyback/return amount of
# $100 or more with NO scale word ("$1,000", not "$1.0 billion") is a mis-render
# -- a billions figure written as plain millions -- so the narrative is not
# trustworthy and the deterministic quote reading should replace it.
_SCALED_AMOUNT_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(billions?|millions?|thousands?|trillions?|bn|mn|[bmk])?",
    flags=re.IGNORECASE,
)


def narrative_looks_mangled(text: str) -> bool:
    """True if the narrative is a broken rendering of a buyback/dividend figure.

    Two shapes: a large dollar amount with no scale word ("$1,000" for $1.0
    billion), or a bare numeric fragment with no proper "$<amount>" at all
    ("~17.6"). Either means the prose cannot be trusted and the deterministic
    quote reading should replace it.
    """

    for amount, scale in _SCALED_AMOUNT_RE.findall(text):
        try:
            value = float(amount.replace(",", ""))
        except ValueError:
            continue
        if value >= 100 and not scale:
            return True
    # Has digits but no "$<number>" -- a truncated fragment, not a real clause.
    if re.search(r"\d", text) and not re.search(r"\$\s*\d", text):
        return True
    return False

SYSTEM_PROMPT = (
    "You summarize share buybacks and dividends from earnings text into ONE short "
    "clause for a client spreadsheet (for example: '$3.5 billion of share "
    "repurchases; $0.50 dividend per share').\n"
    "Rules:\n"
    "- State the buyback dollar amount and/or the dividend, in a clean unit.\n"
    "- Use only numbers that appear in the text. Never invent or infer a figure.\n"
    "- Do NOT copy the raw sentence or a table line -- rewrite it.\n"
    "- Do NOT include unrelated figures (capital ratios, share counts, EPS, "
    "revenue).\n"
    "- If no buyback or dividend amount is disclosed, return null."
)


class CapitalReturnSummary(BaseModel):
    """Structured result for the buyback/dividend narrative pass."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None = None


def is_bare_number(value: Any) -> bool:
    """True if ``value`` is a lone number with no descriptive words around it."""

    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if isinstance(value, str):
        return bool(_BARE_NUMBER_RE.fullmatch(value.strip()))
    return False


def narrative_from_quote(quote: str | None) -> str | None:
    """Deterministically synthesize a buyback/dividend sentence from a quote.

    The offline/fallback path. Returns ``None`` when the quote names no
    buyback/return amount, so a genuine non-disclosure stays "Not disclosed".
    """

    if not quote:
        return None
    text = re.sub(r"\s+", " ", quote).strip()
    if not text or "No supporting" in text:
        return None

    # Explicit "$X billion" repurchased or returned (Wells Fargo, Citi, Morgan
    # Stanley). "~" and casing vary, so match loosely.
    match = re.search(r"~?\s*\$\s*([\d.]+)\s*billion", text, flags=re.IGNORECASE)
    if match:
        amount = match.group(1)
        if re.search(r"return", text, flags=re.IGNORECASE):
            return f"${amount} billion returned to shareholders"
        if re.search(r"repurchas|buyback|common stock", text, flags=re.IGNORECASE):
            return f"${amount} billion of common stock repurchased"
        return f"${amount} billion returned"

    # Cash-flow statement line "Repurchases of common stock (3,536,396)" -- the
    # figure is in thousands of dollars (Netflix financials), so /1e6 -> billions.
    match = re.search(
        r"repurchases of common stock\s*\(?\$?([\d,]{5,})\)?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        thousands = float(match.group(1).replace(",", ""))
        return f"${thousands / 1_000_000.0:.1f} billion of share repurchases"

    return None


def summarize_capital_return_live(
    quote: str,
    context_text: str,
    config: OpenAIConfig,
    client: Any | None = None,
) -> str | None:
    """Ask the model for a one-line buyback/dividend summary, grounded in text.

    Live runs only. The returned sentence is *not* trusted blindly: the caller
    (``resolve_capital_return_narrative``) re-checks that every figure is present
    in the source before using it.
    """

    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=config.api_key)

    response = client.responses.parse(
        model=config.model,
        reasoning={"effort": config.reasoning_effort},
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Cited quote:\n{quote}\n\n"
                    f"Surrounding page text:\n{context_text}"
                ),
            },
        ],
        text_format=CapitalReturnSummary,
    )
    parsed = response.output_parsed
    if parsed is None or not parsed.summary:
        return None
    summary = re.sub(r"\s+", " ", parsed.summary).strip()
    return summary or None


def resolve_capital_return_narrative(
    value: Any,
    quote: str,
    context_text: str,
    config: OpenAIConfig | None,
    mode: str,
    client: Any | None = None,
) -> str | None:
    """Pick the best narrative for a buyback cell, model first, regex fallback.

    Returns ``None`` (meaning "leave the existing value untouched") when ``value``
    is already a usable narrative string. Otherwise returns a synthesized
    sentence, or ``None`` if neither path finds a disclosed amount.
    """

    if (
        isinstance(value, str)
        and not is_bare_number(value)
        and not narrative_looks_mangled(value)
    ):
        return None  # extractor already gave a clean, usable narrative -- keep it
    # A mangled narrative (e.g. "$1,000" for $1.0 billion) falls through to the
    # deterministic quote reading below, which formats the figure correctly.

    # Deterministic first: for the structured quote shapes it recognizes it is
    # clean, uniform, and reproducible -- and never stitches unrelated figures
    # together the way a free-form model can. The model is the tail net, not the
    # default, so known shapes never depend on a live call.
    deterministic = narrative_from_quote(quote)
    if deterministic is not None:
        return deterministic

    # Only prose the regex cannot parse reaches the model, live runs only.
    if mode == "live" and config is not None:
        try:
            candidate = summarize_capital_return_live(
                quote, context_text, config, client
            )
        except Exception:
            candidate = None
        if (
            candidate
            and _numbers_grounded(candidate, quote, context_text)
            and not narrative_looks_mangled(candidate)
        ):
            return candidate

    return None


def _numbers_grounded(summary: str, *sources: str) -> bool:
    """True if every ``$`` figure in ``summary`` appears in some source text.

    Deterministic guard against a hallucinated amount: the model may phrase the
    sentence freely, but the numbers must come from the document.
    """

    haystack = " ".join(_digits(text) for text in sources)
    for raw in _SUMMARY_AMOUNT_RE.findall(summary):
        if _digits(raw) not in haystack:
            return False
    return True


def _digits(text: str) -> str:
    return re.sub(r"[^\d]", "", text)
