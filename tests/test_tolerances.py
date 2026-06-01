from evaluation.tolerances import (
    EPS_TOLERANCE,
    PERCENTAGE_POINT_TOLERANCE,
    currency_tolerance,
    normalize_text_match,
    normalize_whitespace,
)


def test_currency_tolerance_uses_larger_of_one_million_or_half_percent() -> None:
    assert currency_tolerance(100) == 1.0
    assert currency_tolerance(21_600) == 108.0


def test_eval_tolerance_constants_match_spec() -> None:
    assert EPS_TOLERANCE == 0.01
    assert PERCENTAGE_POINT_TOLERANCE == 0.1


def test_text_normalization_helpers() -> None:
    assert normalize_whitespace(" Citi\n First\tQuarter ") == "Citi First Quarter"
    assert normalize_text_match(" Q1  2025 ") == "q1 2025"
