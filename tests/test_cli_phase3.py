import json
from pathlib import Path

from earnings_extractor.cli import main
from earnings_extractor.config import OpenAIConfig
from earnings_extractor.extractor import LiveExtractionResult
from earnings_extractor.pipeline import find_pdf_inputs
from earnings_extractor.schema import DraftRun, LLMUsage, MetricsBatch

ROOT = Path(__file__).resolve().parents[1]
TESLA = ROOT / "assesment_info" / "TSLA-Q2-2025-Update.pdf"
INPUT_DIR = ROOT / "assesment_info"


def test_find_pdf_inputs_accepts_single_pdf() -> None:
    assert find_pdf_inputs(TESLA) == [TESLA]


def test_find_pdf_inputs_accepts_directory() -> None:
    pdfs = find_pdf_inputs(INPUT_DIR)

    assert TESLA in pdfs
    assert all(path.suffix == ".pdf" for path in pdfs)


def test_extract_recorded_mode_succeeds_without_api_key(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    def fail_config() -> None:
        raise AssertionError("recorded mode must not load OpenAI config")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("earnings_extractor.pipeline.load_openai_config", fail_config)

    exit_code = main(
        [
            "extract",
            str(TESLA),
            "--out",
            str(tmp_path),
            "--mode",
            "recorded",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "draft_metrics.json" in captured.out
    draft = DraftRun.model_validate_json(
        (tmp_path / "draft_metrics.json").read_text(encoding="utf-8")
    )
    assert draft.mode == "recorded"
    assert draft.llm_usage == []


def test_extract_live_mode_writes_openai_usage(
    tmp_path: Path, monkeypatch
) -> None:
    from earnings_extractor.pipeline import extract

    monkeypatch.setattr(
        "earnings_extractor.pipeline.load_openai_config",
        lambda: OpenAIConfig(
            api_key="test-key",
            model="test-model",
            reasoning_effort="low",
        ),
    )

    def fake_live_with_usage(*, pages, document_type, config, source_file):
        return LiveExtractionResult(
            metrics=MetricsBatch(metrics=[]),
            usage=LLMUsage(
                source_file=source_file,
                provider="openai",
                model=config.model,
                input_tokens=1000,
                output_tokens=200,
                total_tokens=1200,
                reasoning_tokens=50,
            ),
        )

    monkeypatch.setattr(
        "earnings_extractor.pipeline.extract_metrics_live_with_usage",
        fake_live_with_usage,
    )

    draft_path = extract(TESLA, tmp_path, mode="live")
    draft = DraftRun.model_validate_json(draft_path.read_text(encoding="utf-8"))

    assert draft.mode == "live"
    assert len(draft.llm_usage) == 1
    usage = draft.llm_usage[0]
    assert usage.source_file == str(TESLA)
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 200
    assert usage.total_tokens == 1200
    assert usage.reasoning_tokens == 50


def test_inspect_summarizes_draft_file(tmp_path: Path, capsys) -> None:
    draft_path = _write_sample_draft(tmp_path)

    exit_code = main(["inspect", str(draft_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Metrics: 1" in captured.out
    assert "With evidence: 1/1" in captured.out


def test_eval_prints_field_level_score(tmp_path: Path, capsys) -> None:
    draft_path = _write_sample_draft(tmp_path)

    exit_code = main(
        [
            "eval",
            "--draft",
            str(draft_path),
            "--document-id",
            "tesla_q2_2025",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Document: tesla_q2_2025" in captured.out
    assert "Score:" in captured.out


def test_eval_without_min_accuracy_is_report_only(tmp_path: Path, capsys) -> None:
    draft_path = _write_low_score_draft(tmp_path)

    exit_code = main(
        [
            "eval",
            "--draft",
            str(draft_path),
            "--document-id",
            "tesla_q2_2025",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Score:" in captured.out
    assert "below minimum" not in captured.err


def test_eval_min_accuracy_passes_for_recorded_tesla(tmp_path: Path) -> None:
    from earnings_extractor.pipeline import extract

    draft_path = extract(TESLA, tmp_path, mode="recorded")

    exit_code = main(
        [
            "eval",
            "--draft",
            str(draft_path),
            "--document-id",
            "tesla_q2_2025",
            "--min-accuracy",
            "0.9",
        ]
    )

    assert exit_code == 0


def test_eval_min_accuracy_fails_below_threshold(tmp_path: Path, capsys) -> None:
    draft_path = _write_low_score_draft(tmp_path)

    exit_code = main(
        [
            "eval",
            "--draft",
            str(draft_path),
            "--document-id",
            "tesla_q2_2025",
            "--min-accuracy",
            "0.9",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Accuracy" in captured.err
    assert "below minimum" in captured.err


def test_eval_min_accuracy_rejects_invalid_threshold(tmp_path: Path, capsys) -> None:
    draft_path = _write_sample_draft(tmp_path)

    exit_code = main(
        [
            "eval",
            "--draft",
            str(draft_path),
            "--document-id",
            "tesla_q2_2025",
            "--min-accuracy",
            "1.1",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "between 0.0 and 1.0" in captured.err


def _write_sample_draft(tmp_path: Path) -> Path:
    draft = DraftRun(
        run_id="test",
        created_at="2026-05-29T00:00:00Z",
        mode="live",
        model="test-model",
        reasoning_effort="low",
        documents=[
            {
                "source_file": str(TESLA),
                "document_type": "earnings_report",
                "page_count": 30,
            }
        ],
        classifications=[],
        selected_pages={str(TESLA): [4]},
        metrics=[
            {
                "company": "Tesla",
                "ticker": "TSLA",
                "document_type": "earnings_report",
                "fiscal_period": "Q2 2025",
                "report_date": None,
                "metric_name": "Total revenue",
                "metric_category": "income_statement",
                "segment": None,
                "value": 22496,
                "unit": "USD",
                "scale": "millions",
                "period": "quarter",
                "gaap_or_non_gaap": "GAAP",
                "year_over_year_change": None,
                "source_page": 4,
                "source_quote": (
                    "Total revenues 25,500 25,182 25,707 19,335 22,496 -12%"
                ),
                "confidence": 0.9,
                "needs_review": False,
                "review_reason": None,
                "review_status": "pending",
                "reviewer_note": None,
            }
        ],
    )
    draft_path = tmp_path / "draft_metrics.json"
    draft_path.write_text(
        json.dumps(draft.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return draft_path


def _write_low_score_draft(tmp_path: Path) -> Path:
    draft_path = _write_sample_draft(tmp_path)
    draft = DraftRun.model_validate_json(draft_path.read_text(encoding="utf-8"))
    draft.metrics[0].value = 1.0
    draft_path.write_text(
        json.dumps(draft.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return draft_path
