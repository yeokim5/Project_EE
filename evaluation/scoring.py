"""Field-level scoring for draft metrics against eval-only golden targets.

Golden values live exclusively in the ``evaluation`` package and are imported
only here, never by the extraction runtime. This separation is deliberate: it
guarantees the accuracy numbers measure what the model + deterministic pipeline
actually produced, not values that leaked in from the answer key. A test
(``tests/test_no_cheat_imports.py``) enforces that ``earnings_extractor`` never
imports ``evaluation``. Numeric fields pass within per-type tolerances;
fields that are expected to be blank/flagged for a document type pass only when
the row is genuinely blank and carries a review reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from earnings_extractor.schema import DraftRun, MetricRow
from evaluation.golden_metrics import PRIMARY_FIELDS, GoldenField
from evaluation.tolerances import (
    EPS_TOLERANCE,
    PERCENTAGE_POINT_TOLERANCE,
    currency_tolerance,
    normalize_text_match,
)


@dataclass(frozen=True)
class FieldScore:
    field_name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class ScoreReport:
    document_id: str
    passed: int
    total: int
    fields: list[FieldScore]

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total


def score_draft(draft: DraftRun, document_id: str) -> ScoreReport:
    expected_fields = [
        field for field in PRIMARY_FIELDS if field.document_id == document_id
    ]
    actual_by_name = {
        normalize_text_match(metric.metric_name): metric for metric in draft.metrics
    }

    field_scores = [
        score_field(field, actual_by_name.get(normalize_text_match(field.field_name)))
        for field in expected_fields
        if field.status != "not_scored"
    ]
    return ScoreReport(
        document_id=document_id,
        passed=sum(1 for score in field_scores if score.passed),
        total=len(field_scores),
        fields=field_scores,
    )


def score_field(expected: GoldenField, actual: MetricRow | None) -> FieldScore:
    if expected.status == "expected_blank_review":
        if actual is None:
            return FieldScore(
                expected.field_name,
                False,
                "missing expected blank/review metric row",
            )
        if actual.value in (None, "") and actual.needs_review and actual.review_reason:
            return FieldScore(expected.field_name, True, "blank and review-flagged")
        return FieldScore(expected.field_name, False, "unsupported value was filled")

    if actual is None:
        return FieldScore(expected.field_name, False, "missing metric")
    if not actual.source_quote or actual.source_page is None:
        return FieldScore(expected.field_name, False, "missing source evidence")
    if expected.expected_value is None:
        return FieldScore(expected.field_name, False, "fixture has no expected value")

    if expected.value_type == "currency_usd_millions":
        return _score_numeric(
            expected,
            actual.value,
            tolerance=currency_tolerance(float(expected.expected_value)),
        )
    if expected.value_type == "eps":
        return _score_numeric(expected, actual.value, tolerance=EPS_TOLERANCE)
    if expected.value_type == "percentage_points":
        return _score_numeric(
            expected,
            actual.value,
            tolerance=PERCENTAGE_POINT_TOLERANCE,
        )

    if normalize_text_match(str(actual.value)) == normalize_text_match(
        str(expected.expected_value)
    ):
        return FieldScore(expected.field_name, True, "text matched")
    return FieldScore(expected.field_name, False, "text differed")


def _score_numeric(
    expected: GoldenField, actual_value: Any, tolerance: float
) -> FieldScore:
    actual_number = _coerce_number(actual_value)
    expected_number = float(expected.expected_value)
    if actual_number is None:
        return FieldScore(expected.field_name, False, "actual value is not numeric")
    difference = abs(actual_number - expected_number)
    if difference <= tolerance:
        return FieldScore(
            expected.field_name,
            True,
            f"within tolerance ({difference:.4g} <= {tolerance:.4g})",
        )
    return FieldScore(
        expected.field_name,
        False,
        f"outside tolerance ({difference:.4g} > {tolerance:.4g})",
    )


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None

    cleaned = value.replace(",", "").strip()
    multiplier = 1.0
    if cleaned.startswith("$"):
        cleaned = cleaned[1:]
    if cleaned.lower().endswith("b"):
        multiplier = 1000.0
        cleaned = cleaned[:-1]
    elif cleaned.lower().endswith("m"):
        cleaned = cleaned[:-1]
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]

    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None
