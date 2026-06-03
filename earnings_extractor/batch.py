"""One-command batch lane: a folder of PDFs in, one Excel workbook out.

This is the path for "I have 20 PDFs and I just want a spreadsheet." It reuses
the exact same per-document engine as the review-first ``extract``/``export``
workflow -- ingest, classify, LLM draft, deterministic normalize/validate, and
the gated export -- but wraps each PDF so that one bad file (corrupt, encrypted,
scanned-image, or simply not an earnings document) is recorded and skipped
instead of sinking the whole batch.

Every value that *was* extracted is written to the client sheet immediately and
the workbook is clearly stamped ``DRAFT/UNREVIEWED`` (amber tab), because nothing
here has been through human review yet. A "Batch Status" sheet lists every input
file and what happened to it, so a missing company is visible rather than silent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Font

from earnings_extractor.config import load_openai_config
from earnings_extractor.export import export_reviewed_run
from earnings_extractor.pipeline import (
    build_draft_run,
    find_pdf_inputs,
    process_single_pdf,
)
from earnings_extractor.schema import LLMUsage, SourceDocument
from scripts.make_acceptance_decisions import build_acceptance_decisions

STATUS_EXTRACTED = "extracted"
STATUS_SKIPPED_NOT_EARNINGS = "skipped (not an earnings doc)"
STATUS_FAILED = "failed"


@dataclass
class FileStatus:
    file: str
    status: str
    document_type: str = ""
    metrics_found: int = 0
    needs_review: int = 0
    error: str = ""


@dataclass
class BatchSummary:
    out_xlsx: Path | None
    total: int = 0
    extracted: int = 0
    skipped: int = 0
    failed: int = 0
    statuses: list[FileStatus] = field(default_factory=list)

    def as_text(self) -> str:
        lines = [
            f"Processed {self.total} PDF(s): "
            f"{self.extracted} extracted, {self.skipped} skipped, "
            f"{self.failed} failed.",
        ]
        for status in self.statuses:
            detail = status.document_type or status.error or ""
            suffix = f" -- {detail}" if detail else ""
            lines.append(f"  [{status.status}] {status.file}{suffix}")
        if self.out_xlsx is not None:
            lines.append(f"Workbook: {self.out_xlsx}")
        return "\n".join(lines) + "\n"


def run_batch(
    input_path: Path,
    out_xlsx: Path,
    mode: str = "live",
    progress: Callable[[str], None] | None = None,
) -> BatchSummary:
    """Extract every PDF under ``input_path`` into a single workbook.

    Per-PDF failures are caught and reported; they never abort the batch.
    """

    if mode not in {"live", "recorded"}:
        raise ValueError(f"Unsupported mode: {mode}")

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input folder not found: {input_path}\n"
            "Create it and drop your PDFs inside, e.g.:\n"
            f"    mkdir -p {input_path} && cp *.pdf {input_path}/"
        )

    pdf_paths = find_pdf_inputs(input_path)
    if not pdf_paths:
        raise ValueError(f"No PDF files found in {input_path}")

    config = load_openai_config() if mode == "live" else None
    _emit(progress, f"Found {len(pdf_paths)} PDF(s) in {input_path}.")

    documents: list[SourceDocument] = []
    classifications: list = []
    selected_pages: dict[str, list[int]] = {}
    llm_usage: list[LLMUsage] = []
    all_metrics: list = []
    statuses: list[FileStatus] = []

    for index, pdf_path in enumerate(pdf_paths, start=1):
        name = pdf_path.name
        prefix = f"[{index}/{len(pdf_paths)}] {name}"
        _emit(progress, f"{prefix}: started.")
        try:
            processed = process_single_pdf(
                pdf_path,
                mode,
                config,
                progress=lambda message, prefix=prefix: _emit(
                    progress,
                    f"{prefix}: {message}",
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- isolate one bad PDF
            statuses.append(
                FileStatus(file=name, status=STATUS_FAILED, error=str(exc))
            )
            _emit(progress, f"{prefix}: failed -- {exc}")
            continue

        if processed.document is None:
            statuses.append(
                FileStatus(file=name, status=STATUS_SKIPPED_NOT_EARNINGS)
            )
            _emit(progress, f"{prefix}: skipped (not an earnings doc)")
            continue

        if processed.usage is not None:
            llm_usage.append(processed.usage)
        documents.append(processed.document)
        classifications.append(processed.classification)
        selected_pages[str(pdf_path)] = processed.selected_pages
        all_metrics.extend(processed.metrics)
        statuses.append(
            FileStatus(
                file=name,
                status=STATUS_EXTRACTED,
                document_type=processed.document.document_type,
                metrics_found=len(processed.metrics),
                needs_review=sum(
                    1 for metric in processed.metrics if metric.needs_review
                ),
            )
        )
        _emit(
            progress,
            (
                f"{prefix}: done - {len(processed.metrics)} metric(s), "
                f"{sum(1 for metric in processed.metrics if metric.needs_review)} "
                "need review."
            ),
        )

    summary = BatchSummary(
        out_xlsx=None,
        total=len(pdf_paths),
        extracted=sum(1 for s in statuses if s.status == STATUS_EXTRACTED),
        skipped=sum(
            1 for s in statuses if s.status == STATUS_SKIPPED_NOT_EARNINGS
        ),
        failed=sum(1 for s in statuses if s.status == STATUS_FAILED),
        statuses=statuses,
    )

    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    _emit(progress, f"Writing workbook to {out_xlsx}...")

    if not documents:
        # Nothing usable was extracted. Still produce a workbook so the user
        # gets a single, openable artifact that explains what happened.
        _write_status_only_workbook(out_xlsx, statuses)
        summary.out_xlsx = out_xlsx
        _emit(progress, f"Wrote {out_xlsx}.")
        return summary

    run_dir = out_xlsx.parent
    draft = build_draft_run(
        mode=mode,
        config=config,
        documents=documents,
        classifications=classifications,
        selected_pages=selected_pages,
        llm_usage=llm_usage,
        metrics=all_metrics,
    )
    draft_path = run_dir / "draft_metrics.json"
    draft_path.write_text(
        _dump_draft(draft),
        encoding="utf-8",
    )

    decisions = build_acceptance_decisions(draft)
    decisions_path = run_dir / "batch_decisions.json"
    decisions_path.write_text(
        _dump_model(decisions),
        encoding="utf-8",
    )

    # allow_unreviewed=True: populate every extracted value now and stamp the
    # workbook DRAFT/UNREVIEWED. A company missing a metric leaves a blank cell
    # (recorded in warnings + the status sheet) -- it never blocks the export.
    export_reviewed_run(
        run_dir,
        decisions_path=decisions_path,
        out_path=out_xlsx,
        allow_unreviewed=True,
        display_mode="batch",
    )

    _append_status_sheet(out_xlsx, statuses)
    summary.out_xlsx = out_xlsx
    _emit(progress, f"Wrote {out_xlsx}.")
    return summary


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _dump_draft(draft) -> str:
    import json

    return json.dumps(draft.model_dump(mode="json"), indent=2) + "\n"


def _dump_model(model) -> str:
    import json

    return json.dumps(model.model_dump(mode="json"), indent=2) + "\n"


_STATUS_HEADERS = [
    "Source file",
    "Status",
    "Document type",
    "Metrics found",
    "Needs review",
    "Error",
]


def _status_row(status: FileStatus) -> list:
    return [
        status.file,
        status.status,
        status.document_type,
        status.metrics_found if status.status == STATUS_EXTRACTED else "",
        status.needs_review if status.status == STATUS_EXTRACTED else "",
        status.error,
    ]


def _append_status_sheet(out_xlsx: Path, statuses: list[FileStatus]) -> None:
    workbook = load_workbook(out_xlsx)
    if "Batch Status" in workbook.sheetnames:
        del workbook["Batch Status"]
    # Put the status sheet first so it's the first thing the reviewer sees.
    worksheet = workbook.create_sheet("Batch Status", 0)
    worksheet.append(_STATUS_HEADERS)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for status in statuses:
        worksheet.append(_status_row(status))
    workbook.save(out_xlsx)


def _write_status_only_workbook(
    out_xlsx: Path, statuses: list[FileStatus]
) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Batch Status"
    worksheet.append(_STATUS_HEADERS)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for status in statuses:
        worksheet.append(_status_row(status))
    workbook.save(out_xlsx)
