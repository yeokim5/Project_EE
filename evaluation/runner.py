"""CLI-facing eval runner."""

from __future__ import annotations

from pathlib import Path

from earnings_extractor.schema import DraftRun
from evaluation.scoring import ScoreReport, score_draft


def run(draft_path: Path, document_id: str) -> str:
    report = score(draft_path, document_id)
    return format_report(report)


def run_with_accuracy(draft_path: Path, document_id: str) -> tuple[str, float]:
    report = score(draft_path, document_id)
    return format_report(report), report.accuracy


def score(draft_path: Path, document_id: str) -> ScoreReport:
    draft = DraftRun.model_validate_json(draft_path.read_text(encoding="utf-8"))
    return score_draft(draft, document_id=document_id)


def format_report(report: ScoreReport) -> str:
    lines = [
        f"Document: {report.document_id}",
        f"Score: {report.passed}/{report.total}",
        f"Accuracy: {report.accuracy:.1%}",
    ]
    for field in report.fields:
        status = "PASS" if field.passed else "FAIL"
        lines.append(f"{status} {field.field_name}: {field.reason}")
    return "\n".join(lines) + "\n"
