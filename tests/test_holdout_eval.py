"""The holdout harness scores unseen PDFs and separates silent vs caught errors."""

from __future__ import annotations

from pathlib import Path

from scripts.holdout_eval import format_report, run_holdout

ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "assesment_info"
DOC = "TSLA-Q2-2025-Update"


def _write_truth(path: Path, rows: list[str]) -> Path:
    header = "document_id,field_name,value_type,expected_value,status\n"
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_correct_truth_scores_all_passed_with_no_silent_errors(tmp_path: Path) -> None:
    truth = _write_truth(
        tmp_path / "truth.csv",
        [
            f"{DOC},Total revenue,currency_usd_millions,22496,expected_value",
            f"{DOC},Earnings per share,eps,0.33,expected_value",
            f"{DOC},Net income,currency_usd_millions,1172,expected_value",
        ],
    )

    report = run_holdout(INPUT_DIR, truth, mode="recorded")

    assert report.total == 3
    assert report.correct == 3
    assert report.silent_errors == []


def test_wrong_expected_surfaces_as_silent_error(tmp_path: Path) -> None:
    # A value the deterministic checks accept but disagrees with truth is the
    # case the harness must expose: wrong and unflagged.
    truth = _write_truth(
        tmp_path / "truth.csv",
        [f"{DOC},Total revenue,currency_usd_millions,99999,expected_value"],
    )

    report = run_holdout(INPUT_DIR, truth, mode="recorded")

    assert report.correct == 0
    assert len(report.silent_errors) == 1
    assert report.silent_errors[0].field_name == "Total revenue"
    assert "SILENT ERRORS" in format_report(report)
