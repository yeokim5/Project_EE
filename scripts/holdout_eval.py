"""Holdout evaluation harness -- measure generalization on unseen PDFs.

Runs the extraction pipeline over a folder of PDFs and scores every value against
a ground-truth CSV. Beyond raw accuracy it reports the metric that matters for
this system: of the values that are WRONG, how many did the pipeline flag for
review (caught) versus pass silently (the real failures). A wrong-but-flagged
cell is safe -- a human reviews it. A wrong-and-unflagged cell is the only true
failure. Zero silent errors means the workbook is trustworthy even where it is
imperfect.

Ground-truth CSV columns (header required):

    document_id,field_name,value_type,expected_value,status

  - document_id    PDF filename stem ("blackrock_q4_2025" for blackrock_q4_2025.pdf)
  - field_name     a client template field, e.g. "Total revenue"
  - value_type     currency_usd_millions | eps | percentage_points | text
  - expected_value the correct value (number or text); empty when status is blank
  - status         expected_value (default) | expected_blank_review

Usage:

    python -m scripts.holdout_eval --input pdf_holdout --truth truth.csv --mode live
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from earnings_extractor.config import load_openai_config
from earnings_extractor.pipeline import find_pdf_inputs, process_single_pdf
from earnings_extractor.schema import MetricRow
from evaluation.golden_metrics import GoldenField
from evaluation.scoring import score_field


@dataclass(frozen=True)
class CellResult:
    document_id: str
    field_name: str
    passed: bool
    flagged: bool
    expected: object
    actual: object
    reason: str


@dataclass
class HoldoutReport:
    cells: list[CellResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cells)

    @property
    def correct(self) -> int:
        return sum(1 for c in self.cells if c.passed)

    @property
    def wrong(self) -> list[CellResult]:
        return [c for c in self.cells if not c.passed]

    @property
    def silent_errors(self) -> list[CellResult]:
        # Wrong AND not flagged for review -- the only true failures.
        return [c for c in self.wrong if not c.flagged]

    @property
    def caught_errors(self) -> list[CellResult]:
        return [c for c in self.wrong if c.flagged]

    @property
    def flagged(self) -> list[CellResult]:
        return [c for c in self.cells if c.flagged]


def load_truth(path: Path) -> list[GoldenField]:
    fields: list[GoldenField] = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            status = (row.get("status") or "expected_value").strip()
            raw = (row.get("expected_value") or "").strip()
            value_type = (row.get("value_type") or "text").strip()
            expected: float | str | None
            if status == "expected_blank_review" or raw == "":
                expected = None
            elif value_type in {"currency_usd_millions", "eps", "percentage_points"}:
                expected = float(raw.replace(",", "").replace("$", ""))
            else:
                expected = raw
            fields.append(
                GoldenField(
                    document_id=row["document_id"].strip(),
                    source_file=Path(row["document_id"].strip()),
                    field_name=row["field_name"].strip(),
                    status=status,  # type: ignore[arg-type]
                    value_type=value_type,  # type: ignore[arg-type]
                    expected_value=expected,
                )
            )
    return fields


def extract_metrics_by_document(
    input_dir: Path, mode: str
) -> dict[str, list[MetricRow]]:
    config = load_openai_config() if mode == "live" else None
    by_document: dict[str, list[MetricRow]] = {}
    for pdf in find_pdf_inputs(input_dir):
        processed = process_single_pdf(pdf, mode, config)
        by_document[pdf.stem] = processed.metrics
    return by_document


def run_holdout(input_dir: Path, truth_csv: Path, mode: str) -> HoldoutReport:
    truth = load_truth(truth_csv)
    by_document = extract_metrics_by_document(input_dir, mode)
    report = HoldoutReport()
    for gold in truth:
        metrics = by_document.get(gold.document_id, [])
        actual = next(
            (m for m in metrics if m.metric_name == gold.field_name), None
        )
        score = score_field(gold, actual)
        report.cells.append(
            CellResult(
                document_id=gold.document_id,
                field_name=gold.field_name,
                passed=score.passed,
                flagged=bool(actual and actual.needs_review),
                expected=gold.expected_value,
                actual=actual.value if actual else None,
                reason=score.reason,
            )
        )
    return report


def format_report(report: HoldoutReport) -> str:
    if report.total == 0:
        return "No cells scored -- check the truth CSV and document_ids."

    lines: list[str] = []
    acc = 100.0 * report.correct / report.total
    lines.append(f"Holdout accuracy: {report.correct}/{report.total} = {acc:.1f}%")

    # Per-field accuracy.
    per_field: dict[str, list[bool]] = defaultdict(list)
    for cell in report.cells:
        per_field[cell.field_name].append(cell.passed)
    lines.append("\nPer-field accuracy:")
    for name, passes in sorted(per_field.items()):
        ok = sum(passes)
        pct = 100.0 * ok / len(passes)
        lines.append(f"  {name:24} {ok}/{len(passes)} = {pct:.0f}%")

    # The metric that matters: silent vs caught errors.
    lines.append("\nError safety (the metric that matters):")
    lines.append(f"  wrong cells:    {len(report.wrong)}")
    lines.append(f"  caught (flagged for review): {len(report.caught_errors)}")
    lines.append(f"  SILENT (wrong, NOT flagged): {len(report.silent_errors)}")
    flagged = report.flagged
    if flagged:
        precision = 100.0 * len(report.caught_errors) / len(flagged)
        lines.append(
            f"  flag precision: {len(report.caught_errors)}/{len(flagged)} "
            f"flagged cells were actually wrong ({precision:.0f}%)"
        )

    if report.silent_errors:
        lines.append("\n!! SILENT ERRORS (wrong and unflagged -- true failures):")
        for cell in report.silent_errors:
            lines.append(
                f"  {cell.document_id} / {cell.field_name}: "
                f"got {cell.actual!r}, expected {cell.expected!r}"
            )
    else:
        lines.append(
            "\nNo silent errors: every wrong value was flagged for review. "
            "Trustworthy even where imperfect."
        )

    if report.caught_errors:
        lines.append("\nCaught errors (wrong but flagged -- safe, need a human):")
        for cell in report.caught_errors:
            lines.append(
                f"  {cell.document_id} / {cell.field_name}: "
                f"got {cell.actual!r}, expected {cell.expected!r}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Folder of PDFs.")
    parser.add_argument("--truth", required=True, type=Path, help="Ground-truth CSV.")
    parser.add_argument(
        "--mode",
        choices=("live", "recorded"),
        default="recorded",
        help="live needs OPENAI_API_KEY; recorded replays bundled cassettes.",
    )
    args = parser.parse_args(argv)
    report = run_holdout(args.input, args.truth, args.mode)
    print(format_report(report))
    # Non-zero exit when a value is wrong AND unflagged -- the only true failure.
    return 1 if report.silent_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
