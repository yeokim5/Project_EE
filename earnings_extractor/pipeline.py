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
from pathlib import Path

from pydantic import ValidationError

from earnings_extractor.classify import classify_document, select_extraction_pages
from earnings_extractor.config import OpenAIConfig, load_openai_config
from earnings_extractor.extractor import extract_metrics_live_with_usage
from earnings_extractor.identity import apply_company_identity, resolve_company_identity
from earnings_extractor.ingest import PageText, read_pdf_metadata, read_pdf_pages
from earnings_extractor.normalize import normalize_metrics
from earnings_extractor.recorded import extract_metrics_recorded
from earnings_extractor.schema import (
    DraftRun,
    LLMUsage,
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
    validate_metrics,
)


def find_pdf_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise ValueError(f"Input file must be a PDF: {input_path}")
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.glob("*.pdf"))
    raise FileNotFoundError(input_path)


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
        pages = read_pdf_pages(pdf_path)
        metadata = read_pdf_metadata(pdf_path)
        classification = classify_document(pages)
        if classification.document_type == "unknown":
            continue
        pages_for_extraction = select_extraction_pages(pages)
        batch, usage = _extract_document_metrics(
            pdf_path=pdf_path,
            pages=pages_for_extraction,
            document_type=classification.document_type,
            mode=mode,
            config=config,
        )
        if usage is not None:
            llm_usage.append(usage)
        metrics = list(batch.metrics)
        complete_template_rows(
            metrics,
            classification.document_type,
            pages_for_extraction,
        )
        identity = resolve_company_identity(
            pages=pages,
            metadata=metadata,
            source_file=str(pdf_path),
            document_type=classification.document_type,
        )
        apply_company_identity(metrics, identity)
        enrich_capital_return_text(metrics, pages)
        normalize_metrics(metrics)
        # Live extractions can mis-cite a page; snap each quote to the page that
        # actually contains it before the citation validator runs. Recorded
        # cassettes are already page-correct, so this stays live-only to keep
        # demo output byte-for-byte deterministic.
        if mode == "live":
            repair_source_pages(metrics, pages)
        validate_metrics(metrics, pages)

        documents.append(
            SourceDocument(
                source_file=str(pdf_path),
                document_type=classification.document_type,
                page_count=len(pages),
            )
        )
        classifications.append(classification)
        selected_pages[str(pdf_path)] = [
            page.page_number for page in pages_for_extraction
        ]
        all_metrics.extend(metrics)

    if not documents:
        raise ValueError(f"No supported earnings PDFs found in {input_path}")

    draft = DraftRun(
        run_id=new_run_id(),
        created_at=utc_now_iso(),
        mode=mode,
        model=config.model if config is not None else "recorded",
        reasoning_effort=config.reasoning_effort if config is not None else None,
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
