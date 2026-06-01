"""Accuracy tolerances and text normalization for eval-only scoring."""

from __future__ import annotations

import re

EPS_TOLERANCE = 0.01
PERCENTAGE_POINT_TOLERANCE = 0.1


def currency_tolerance(expected_value: float) -> float:
    """Return USD-millions tolerance for a canonical currency value."""

    return max(1.0, 0.005 * expected_value)


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace so PDF line breaks do not break quote matching."""

    return re.sub(r"\s+", " ", text).strip()


def normalize_text_match(text: str) -> str:
    """Normalize human-readable text fields for exact-ish comparisons."""

    return normalize_whitespace(text).casefold()
