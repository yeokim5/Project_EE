import json
from pathlib import Path

import pytest

from earnings_extractor.cli import main
from earnings_extractor.pipeline import extract
from earnings_extractor.review import (
    ReviewDecision,
    ReviewDecisionsFile,
    build_review_items,
    load_review_decisions,
    write_review_artifacts,
)
from earnings_extractor.schema import DraftRun, MetricRow
from earnings_extractor.validation import PLACEHOLDER_SOURCE_QUOTE

ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "assesment_info"


def test_review_cli_writes_artifacts_for_combined_recorded_run(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "run"
    decisions_path = run_dir / "review_decisions.json"
    extract(INPUT_DIR, run_dir, mode="recorded")

    exit_code = main(
        [
            "review",
            str(run_dir),
            "--demo-decisions",
            str(decisions_path),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "review_queue.json" in captured.out
    assert (run_dir / "review_queue.json").exists()
    assert (run_dir / "evidence_report.md").exists()
    assert (run_dir / "review.html").exists()
    assert decisions_path.exists()

    queue = json.loads((run_dir / "review_queue.json").read_text(encoding="utf-8"))
    assert len(queue["items"]) == 21
    assert any(item["requires_attention"] for item in queue["items"])
    assert any(not item["requires_attention"] for item in queue["items"])

    decisions = load_review_decisions(decisions_path, expected_run_id=queue["run_id"])
    assert decisions.is_demo is True


def test_default_review_html_path_and_demo_rules(tmp_path: Path) -> None:
    draft = _write_draft(
        tmp_path,
        [
            _metric("Total revenue", 22496, needs_review=False),
            _metric("Buybacks and dividends", None, needs_review=True),
            _metric(
                "Operating income",
                923,
                needs_review=True,
                source_quote=PLACEHOLDER_SOURCE_QUOTE,
            ),
        ],
    )

    artifacts = write_review_artifacts(tmp_path, demo_decisions_out=tmp_path / "d.json")
    queue = json.loads(artifacts.review_queue_path.read_text(encoding="utf-8"))
    decisions = load_review_decisions(
        artifacts.review_decisions_path or tmp_path / "missing.json",
        expected_run_id=draft.run_id,
    )

    assert artifacts.review_html_path == tmp_path / "review.html"
    assert [item["requires_attention"] for item in queue["items"]] == [
        False,
        True,
        True,
    ]
    statuses = {
        decision.metric_id: decision.review_status for decision in decisions.decisions
    }
    notes = {
        decision.metric_id: decision.reviewer_note for decision in decisions.decisions
    }
    assert statuses[f"{draft.run_id}:0000"] == "approved"
    assert statuses[f"{draft.run_id}:0001"] == "not_applicable"
    assert "Demo shortcut" in (notes[f"{draft.run_id}:0001"] or "")
    assert statuses[f"{draft.run_id}:0002"] == "needs_fix"


def test_requires_attention_adds_source_resolution_failure(tmp_path: Path) -> None:
    draft = DraftRun(
        run_id="source-fail",
        created_at="2026-05-31T00:00:00Z",
        mode="recorded",
        model="recorded",
        reasoning_effort=None,
        documents=[
            {
                "source_file": "one.pdf",
                "document_type": "earnings_report",
                "page_count": 1,
            },
            {
                "source_file": "two.pdf",
                "document_type": "earnings_report",
                "page_count": 1,
            },
        ],
        classifications=[],
        selected_pages={},
        metrics=[
            _metric(
                "Total revenue",
                10,
                company="UnknownCo",
                ticker="ZZ",
                needs_review=False,
            )
        ],
    )

    items = build_review_items(draft)

    assert items[0].source_file == "unknown"
    assert items[0].source_resolution_failed is True
    assert items[0].requires_attention is True


def test_load_review_decisions_accepts_browser_shaped_human_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "review_decisions.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "created_at": "2026-05-31T00:00:00Z",
                "is_demo": False,
                "decisions": [
                    {
                        "metric_id": "run-1:0000",
                        "review_status": "approved",
                        "reviewer_note": "Looks good.",
                        "reviewed_at": "2026-05-31T00:01:00Z",
                        "reviewer": "Analyst",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    decisions = load_review_decisions(path, expected_run_id="run-1")

    assert decisions.is_demo is False
    assert decisions.decisions[0].review_status == "approved"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "run_id": "run-1",
                "created_at": "2026-05-31T00:00:00Z",
                "is_demo": False,
                "decisions": [
                    {
                        "metric_id": "run-1:0000",
                        "review_status": "pending",
                        "reviewer_note": None,
                        "reviewed_at": "2026-05-31T00:01:00Z",
                        "reviewer": "Analyst",
                    }
                ],
            },
            "Invalid review decisions file",
        ),
        (
            {
                "run_id": "run-1",
                "created_at": "2026-05-31T00:00:00Z",
                "is_demo": False,
                "decisions": [
                    {
                        "metric_id": "run-1:0000",
                        "review_status": "approved",
                        "reviewer_note": None,
                        "reviewed_at": "2026-05-31T00:01:00Z",
                        "reviewer": "Analyst",
                    },
                    {
                        "metric_id": "run-1:0000",
                        "review_status": "rejected",
                        "reviewer_note": None,
                        "reviewed_at": "2026-05-31T00:02:00Z",
                        "reviewer": "Analyst",
                    },
                ],
            },
            "Duplicate review decision",
        ),
    ],
)
def test_load_review_decisions_rejects_invalid_files(
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "review_decisions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_review_decisions(path, expected_run_id="run-1")


def test_load_review_decisions_rejects_mismatched_run_id(tmp_path: Path) -> None:
    decisions = ReviewDecisionsFile(
        run_id="other-run",
        created_at="2026-05-31T00:00:00Z",
        is_demo=False,
        decisions=[
            ReviewDecision(
                metric_id="other-run:0000",
                review_status="approved",
                reviewer_note=None,
                reviewed_at="2026-05-31T00:01:00Z",
                reviewer="Analyst",
            )
        ],
    )
    path = tmp_path / "review_decisions.json"
    path.write_text(decisions.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="run_id does not match"):
        load_review_decisions(path, expected_run_id="run-1")


def test_review_html_escapes_embedded_json(tmp_path: Path) -> None:
    _write_draft(
        tmp_path,
        [
            _metric(
                "Total revenue",
                22496,
                company="Bad </script> Co",
                source_quote="Source quote with </script> marker",
            )
        ],
    )

    artifacts = write_review_artifacts(tmp_path)
    html = artifacts.review_html_path.read_text(encoding="utf-8")

    assert "Bad </script> Co" not in html
    assert "Source quote with </script> marker" not in html
    assert "Bad \\u003c/script> Co" in html
    assert "Source quote with \\u003c/script> marker" in html


def _write_draft(tmp_path: Path, metrics: list[MetricRow]) -> DraftRun:
    draft = DraftRun(
        run_id="test-run",
        created_at="2026-05-31T00:00:00Z",
        mode="recorded",
        model="recorded",
        reasoning_effort=None,
        documents=[
            {
                "source_file": "test.pdf",
                "document_type": "earnings_report",
                "page_count": 1,
            }
        ],
        classifications=[],
        selected_pages={"test.pdf": [1]},
        metrics=metrics,
    )
    (tmp_path / "draft_metrics.json").write_text(
        draft.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return draft


def _metric(
    name: str,
    value: object,
    *,
    company: str | None = "Tesla",
    ticker: str | None = "TSLA",
    needs_review: bool = False,
    source_quote: str = "source quote",
) -> MetricRow:
    return MetricRow(
        company=company,
        ticker=ticker,
        document_type="earnings_report",
        metric_name=name,
        value=value,
        unit="USD" if isinstance(value, int | float) else None,
        scale="millions" if isinstance(value, int | float) else None,
        source_page=1,
        source_quote=source_quote,
        confidence=0.99 if not needs_review else 0.5,
        needs_review=needs_review,
        review_reason="Needs human review." if needs_review else None,
    )
