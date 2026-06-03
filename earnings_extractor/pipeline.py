"""Draft extraction pipeline -- the fixed orchestration around the model call.

Per document the stages run in a fixed order, and the order matters:

    ingest -> classify -> structured LLM extraction -> complete template rows
    -> resolve company identity -> enrich capital-return text -> normalize
    -> (live only) repair mis-cited pages -> validate -> assemble draft

The LLM only does the fuzzy reading (numbers + a source quote + a self-reported
confidence). Everything that has to be reliable -- unit/scale conversion,
identity cleanup, filling in unsupported template fields, consistency checks, and
review flagging -- is deterministic code that runs after the model returns.

Confidence is treated as a review-triage signal, not as proof. A high model
confidence never lets a value skip validation, and a value is routed to human
review whenever its evidence is missing, its quote is not on the cited page, the
number is not grounded in its own quote, or a consistency/magnitude check fails.
The model's confidence is just one of several reasons a row can be flagged.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from earnings_extractor.capital_return import (
    CAPITAL_RETURN_FIELD,
    resolve_capital_return_narrative,
)
from earnings_extractor.classify import classify_document, select_extraction_pages
from earnings_extractor.config import OpenAIConfig, load_openai_config
from earnings_extractor.extractor import extract_metrics_live_with_usage
from earnings_extractor.identity import apply_company_identity, resolve_company_identity
from earnings_extractor.ingest import PageText, read_pdf_metadata, read_pdf_pages
from earnings_extractor.line_item_selector import apply_line_item_selection
from earnings_extractor.normalize import normalize_metrics
from earnings_extractor.recorded import extract_metrics_recorded
from earnings_extractor.schema import (
    DocumentType,
    DraftRun,
    LLMUsage,
    MetricRow,
    MetricsBatch,
    SourceDocument,
    new_run_id,
    utc_now_iso,
)
from earnings_extractor.validation import (
    PLACEHOLDER_SOURCE_QUOTE,
    complete_template_rows,
    enrich_capital_return_text,
    repair_source_pages,
    repair_table_scale,
    validate_metrics,
)
from earnings_extractor.verifier import verify_template_metrics


def find_pdf_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise ValueError(f"Input file must be a PDF: {input_path}")
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.glob("*.pdf"))
    raise FileNotFoundError(input_path)


@dataclass
class ProcessedDocument:
    """Everything one PDF contributes to a draft run.

    ``document`` is reserved for future hard-skips. In normal batch/live runs,
    ambiguous classification is treated as an extraction hint, not a reason to
    skip the file.
    """

    source_file: str
    document: SourceDocument | None
    classification: object
    selected_pages: list[int]
    usage: LLMUsage | None
    metrics: list


def process_single_pdf(
    pdf_path: Path,
    mode: str,
    config: OpenAIConfig | None,
    progress: Callable[[str], None] | None = None,
) -> ProcessedDocument:
    """Run the full per-document pipeline for one PDF.

    This is the unit of work shared by the single-run ``extract`` path and the
    resilient ``batch`` path. It raises on any genuine failure (unreadable PDF,
    failed model call, validation error); the batch runner wraps it so one bad
    file never sinks the others.
    """

    _emit(progress, "reading PDF...")
    pages = read_pdf_pages(pdf_path)
    metadata = read_pdf_metadata(pdf_path)
    _emit(progress, "checking document...")
    classification = classify_document(pages)
    # For user-supplied batch folders, classification is audit metadata and page
    # context only. It must not decide whether extraction runs, and it must not
    # over-specialize the prompt. The product goal is the same nine-field
    # extraction for earnings releases, reports, and call transcripts.
    document_type: DocumentType = "earnings_report"
    if classification.document_type == "unknown":
        _emit(progress, "document type uncertain; extracting anyway...")
    pages_for_extraction = select_extraction_pages(pages)
    _emit(progress, f"extracting metrics ({mode})...")
    batch, usage = _with_heartbeat(
        lambda: _extract_document_metrics(
            pdf_path=pdf_path,
            pages=pages_for_extraction,
            document_type=document_type,
            mode=mode,
            config=config,
        ),
        progress=progress,
        message="running",
        enabled=mode == "live",
    )
    metrics = list(batch.metrics)
    for metric in metrics:
        metric.source_file = str(pdf_path)
        if metric.document_type == "unknown":
            metric.document_type = document_type
    _emit(progress, "preparing template rows...")
    complete_template_rows(
        metrics,
        document_type,
        pages_for_extraction,
    )
    _emit(progress, "resolving company...")
    identity = resolve_company_identity(
        pages=pages,
        metadata=metadata,
        source_file=str(pdf_path),
        document_type=document_type,
    )
    apply_company_identity(metrics, identity)
    enrich_capital_return_text(metrics, pages)
    _apply_capital_return_narrative(metrics, pages, config, mode)
    _emit(progress, "normalizing...")
    repair_table_scale(metrics, pages)
    normalize_metrics(metrics)
    # Definition-driven line-item correction (live only): the plain extractor
    # routinely picks a line adjacent in meaning to the template field -- a
    # composite ("revenues and other income") for Total revenue, a component
    # (SG&A) for Operating expenses. Re-select the definition-matching line from
    # the cited page, caged so the value stays grounded. Runs after normalize so
    # the chosen line inherits the same validated table scale; flags every change.
    if mode == "live":
        _emit(progress, "checking line items...")
        apply_line_item_selection(metrics, pages, config, mode)
    # Live extractions can mis-cite a page; snap each quote to the page that
    # actually contains it before the citation validator runs. Recorded
    # cassettes are already page-correct, so this stays live-only to keep
    # demo output byte-for-byte deterministic.
    if mode == "live":
        _emit(progress, "checking citations...")
        repair_source_pages(metrics, pages)
    _emit(progress, "validating...")
    validate_metrics(metrics, pages)
    # Language-model safety net: on live runs, have the model re-check each
    # populated client value against its quote and flag semantic errors the
    # deterministic rules cannot enumerate (wrong line item, full-year vs the
    # quarter). It only adds review flags -- it never edits a value -- and is
    # live-only so recorded output stays reproducible.
    if mode == "live":
        _emit(progress, "verifying values...")
        verify_template_metrics(metrics, config, mode)

    return ProcessedDocument(
        source_file=str(pdf_path),
        document=SourceDocument(
            source_file=str(pdf_path),
            document_type=document_type,
            page_count=len(pages),
        ),
        classification=classification,
        selected_pages=[page.page_number for page in pages_for_extraction],
        usage=usage,
        metrics=metrics,
    )


def _apply_capital_return_narrative(
    metrics: list[MetricRow],
    pages: list[PageText],
    config: OpenAIConfig | None,
    mode: str,
) -> None:
    """Fill the buybacks cell with a grounded narrative when the value is weak.

    Runs after the deterministic ``enrich_capital_return_text``: only rows it
    could not phrase (a bare number or ``None``) reach the language model, which
    turns the cited page's prose into one sentence. The model is live-only and
    its output is number-checked against the source; recorded runs and misses
    fall back to the deterministic quote deriver. The row stays ``needs_review``
    -- this changes the text, never the export approval gate.
    """

    row = next(
        (m for m in metrics if m.metric_name == CAPITAL_RETURN_FIELD), None
    )
    if row is None:
        return
    context = next(
        (p.text for p in pages if p.page_number == row.source_page),
        "\n".join(p.text for p in pages),
    )
    narrative = resolve_capital_return_narrative(
        value=row.value,
        quote=row.source_quote or "",
        context_text=context,
        config=config,
        mode=mode,
    )
    if narrative is None:
        return
    row.value = narrative
    row.unit = None
    row.scale = None
    row.needs_review = True
    row.review_reason = _append_capital_return_reason(row.review_reason)


def _append_capital_return_reason(existing: str | None) -> str:
    reason = (
        "Buyback/dividend summary synthesized from the source text; verify the "
        "amount and buyback/dividend split against the citation."
    )
    if not existing:
        return reason
    if reason in existing:
        return existing
    return f"{existing}; {reason}"


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _with_heartbeat(
    operation: Callable[[], tuple[MetricsBatch, LLMUsage | None]],
    progress: Callable[[str], None] | None,
    message: str,
    enabled: bool,
    interval_seconds: float = 2.0,
) -> tuple[MetricsBatch, LLMUsage | None]:
    if progress is None or not enabled:
        return operation()

    started = time.monotonic()
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(interval_seconds):
            elapsed = int(time.monotonic() - started)
            _emit(progress, f"{message} - {elapsed}s elapsed...")

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        return operation()
    finally:
        stop.set()
        thread.join(timeout=0.1)


def extract(input_path: Path, out_dir: Path, mode: str) -> Path:
    if mode not in {"live", "recorded"}:
        raise ValueError(f"Unsupported mode: {mode}")

    config = load_openai_config() if mode == "live" else None
    pdf_paths = find_pdf_inputs(input_path)
    if not pdf_paths:
        raise ValueError(f"No PDF files found in {input_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    documents: list[SourceDocument] = []
    classifications = []
    selected_pages: dict[str, list[int]] = {}
    llm_usage: list[LLMUsage] = []
    all_metrics = []

    for pdf_path in pdf_paths:
        processed = process_single_pdf(pdf_path, mode, config)
        if processed.document is None:
            continue
        if processed.usage is not None:
            llm_usage.append(processed.usage)
        documents.append(processed.document)
        classifications.append(processed.classification)
        selected_pages[str(pdf_path)] = processed.selected_pages
        all_metrics.extend(processed.metrics)

    if not documents:
        raise ValueError(f"No supported earnings PDFs found in {input_path}")

    draft = build_draft_run(
        mode=mode,
        config=config,
        documents=documents,
        classifications=classifications,
        selected_pages=selected_pages,
        llm_usage=llm_usage,
        metrics=all_metrics,
    )
    draft_path = out_dir / "draft_metrics.json"
    draft_path.write_text(
        json.dumps(draft.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return draft_path


def build_draft_run(
    mode: str,
    config: OpenAIConfig | None,
    documents: list[SourceDocument],
    classifications: list,
    selected_pages: dict[str, list[int]],
    llm_usage: list[LLMUsage],
    metrics: list,
) -> DraftRun:
    return DraftRun(
        run_id=new_run_id(),
        created_at=utc_now_iso(),
        mode=mode,
        model=config.model if config is not None else "recorded",
        reasoning_effort=config.reasoning_effort if config is not None else None,
        documents=documents,
        classifications=classifications,
        selected_pages=selected_pages,
        llm_usage=llm_usage,
        metrics=metrics,
    )


def _extract_document_metrics(
    pdf_path: Path,
    pages: list[PageText],
    document_type: str,
    mode: str,
    config: OpenAIConfig | None,
) -> tuple[MetricsBatch, LLMUsage | None]:
    if mode == "recorded":
        return extract_metrics_recorded(pdf_path), None
    if config is None:
        raise RuntimeError("OpenAI config is required for live mode")
    result = extract_metrics_live_with_usage(
        pages=pages,
        document_type=document_type,
        config=config,
        source_file=str(pdf_path),
    )
    return result.metrics, result.usage


def inspect_draft(draft_path: Path) -> str:
    try:
        draft = DraftRun.model_validate_json(draft_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ValueError(f"Invalid draft file: {draft_path}") from exc

    total = len(draft.metrics)
    with_evidence = sum(
        1
        for metric in draft.metrics
        if metric.value not in (None, "")
        and metric.source_quote
        and metric.source_quote != PLACEHOLDER_SOURCE_QUOTE
    )
    needs_review = sum(1 for metric in draft.metrics if metric.needs_review)
    documents = ", ".join(doc.source_file for doc in draft.documents)

    return (
        f"Draft: {draft_path}\n"
        f"Documents: {documents}\n"
        f"Metrics: {total}\n"
        f"With evidence: {with_evidence}/{total}\n"
        f"Needs review: {needs_review}/{total}\n"
    )
