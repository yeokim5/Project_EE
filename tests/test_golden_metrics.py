from collections import defaultdict

from evaluation.golden_metrics import (
    AUXILIARY_METRICS,
    PRIMARY_FIELDS,
    TEMPLATE_FIELDS,
)


def test_primary_fixture_covers_all_template_fields_per_document() -> None:
    fields_by_document: dict[str, set[str]] = defaultdict(set)
    for field in PRIMARY_FIELDS:
        fields_by_document[field.document_id].add(field.field_name)

    assert fields_by_document
    for fields in fields_by_document.values():
        assert fields == set(TEMPLATE_FIELDS)


def test_expected_values_have_source_evidence() -> None:
    for field in PRIMARY_FIELDS:
        if field.status == "expected_value":
            assert field.expected_value is not None
            assert field.source_page is not None
            assert field.source_quote


def test_expected_blank_fields_have_review_reason() -> None:
    blank_fields = [
        field for field in PRIMARY_FIELDS if field.status == "expected_blank_review"
    ]

    assert blank_fields
    for field in blank_fields:
        assert field.expected_value is None
        assert field.review_reason


def test_auxiliary_metrics_are_separate_from_primary_template_fields() -> None:
    auxiliary_names = {metric.metric_name for metric in AUXILIARY_METRICS}

    assert auxiliary_names
    assert auxiliary_names.isdisjoint(TEMPLATE_FIELDS)
