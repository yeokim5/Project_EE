"""Shared, testable helpers for the Phase 11 review-first web API.

This module is a thin orchestration layer over the existing, tested Python core
(`pipeline.extract`, `locate.locate_evidence_bbox`, `review.build_review_items`,
`export.export_reviewed_run`). It adds **no** extraction, locating, review,
normalization, or export logic of its own — it only:

* turns a stored (page, quote) into fractional highlight rectangles for the
  client overlay (reusing `locate.py` and the citation-viewer scaling math),
* shapes per-document metric payloads for the review UI,
* merges several single-document drafts into one `DraftRun` for a single
  workbook, and
* maps the reviewer's per-document decisions onto the merged draft's canonical
  `metric_id`s by stable `(document_index, metric_index_within_document)`
  ordering — never by the pre-merge id, which shifts on merge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from earnings_extractor.locate import locate_evidence_bbox
from earnings_extractor.review import (
    ReviewDecision,
    ReviewDecisionsFile,
    build_review_items,
)
from earnings_extractor.schema import (
    TEMPLATE_FIELDS,
    DraftRun,
    MetricRow,
    new_run_id,
    utc_now_iso,
)

DEFAULT_REVIEWER = "Web reviewer"
DEFAULT_ATTENTION_NOTE = "Reviewed in web UI."


def evidence_for(pdf_path: Path | str, page: int, quote: str) -> dict[str, Any]:
    """Return fractional highlight rectangles for ``quote`` on ``page``.

    Rectangles are expressed as fractions of the page (left/top/width/height in
    [0, 1]) so the client can scale them to any PDF.js render size — exactly the
    contract the citation viewer already proved on the golden docs. Returns
    ``matched=False`` with an empty rect list when the quote cannot be grounded,
    rather than guessing.
    """

    location = locate_evidence_bbox(pdf_path, page, quote)
    rects: list[dict[str, float]] = []
    if location.matched and location.page_width and location.page_height:
        for rect in location.rects:
            rects.append(
                {
                    "left": rect.x0 / location.page_width,
                    "top": rect.y0 / location.page_height,
                    "width": (rect.x1 - rect.x0) / location.page_width,
                    "height": (rect.y1 - rect.y0) / location.page_height,
                }
            )
    return {
        "matched": bool(rects),
        "page_size": {
            "width": location.page_width,
            "height": location.page_height,
        },
        "rects": rects,
    }


def template_metric_payloads(
    draft: DraftRun,
    pdf_path: Path | str,
) -> list[dict[str, Any]]:
    """One payload per template-field metric, with evidence rects attached.

    ``metric_index`` is the metric's index within ``draft.metrics`` (not a
    filtered position), so the client can return decisions keyed by that index
    and the export side can recompute canonical ids after merging drafts.
    """

    payloads: list[dict[str, Any]] = []
    for index, metric in enumerate(draft.metrics):
        if metric.metric_name not in TEMPLATE_FIELDS:
            continue
        payloads.append(
            {
                "metric_index": index,
                "metric_name": metric.metric_name,
                "value": metric.value,
                "unit": metric.unit,
                "scale": metric.scale,
                "source_page": metric.source_page,
                "source_quote": metric.source_quote,
                "confidence": metric.confidence,
                "needs_review": metric.needs_review,
                "review_reason": metric.review_reason,
                "evidence": evidence_for(
                    pdf_path, metric.source_page, metric.source_quote
                ),
            }
        )
    return payloads


def merge_drafts(drafts: list[DraftRun]) -> tuple[DraftRun, list[int]]:
    """Merge single-document drafts into one draft with a fresh ``run_id``.

    Returns the merged draft plus ``offsets``: ``offsets[i]`` is the index in
    ``merged.metrics`` where document ``i``'s metrics begin. Decisions are later
    mapped through these offsets, so the per-document ``metric_index`` the client
    holds resolves to the correct merged metric.
    """

    if not drafts:
        raise ValueError("merge_drafts requires at least one draft")

    documents: list[Any] = []
    classifications: list[Any] = []
    selected_pages: dict[str, list[int]] = {}
    llm_usage: list[Any] = []
    metrics: list[MetricRow] = []
    offsets: list[int] = []

    for draft in drafts:
        offsets.append(len(metrics))
        documents.extend(draft.documents)
        classifications.extend(draft.classifications)
        selected_pages.update(draft.selected_pages)
        llm_usage.extend(draft.llm_usage)
        metrics.extend(draft.metrics)

    mode = "live" if any(draft.mode == "live" for draft in drafts) else "recorded"
    model = next((draft.model for draft in drafts if draft.model), None)
    reasoning_effort = next(
        (draft.reasoning_effort for draft in drafts if draft.reasoning_effort),
        None,
    )

    merged = DraftRun(
        run_id=new_run_id(),
        created_at=utc_now_iso(),
        mode=mode,
        model=model,
        reasoning_effort=reasoning_effort,
        documents=documents,
        classifications=classifications,
        selected_pages=selected_pages,
        llm_usage=llm_usage,
        metrics=metrics,
    )
    return merged, offsets


def build_decisions_file(
    merged: DraftRun,
    offsets: list[int],
    per_document_decisions: list[list[dict[str, Any]]],
    reviewer: str = DEFAULT_REVIEWER,
) -> ReviewDecisionsFile:
    """Map per-document reviewer decisions onto the merged draft's metric ids.

    ``per_document_decisions[i]`` holds the decisions the client made for
    document ``i``, each ``{"metric_index": <local index in that document's
    draft.metrics>, "review_status": ..., "note": ...}``. We translate each local
    index to the merged global index via ``offsets``, then attach decisions to
    the canonical ``metric_id`` recomputed from the merged draft. Every
    template-field metric receives a decision (defaulting sensibly when the
    client did not send one), and attention-flagged items are guaranteed a note
    so the real export gate passes.
    """

    review_items = build_review_items(merged)
    by_index = {item.metric_index: item for item in review_items}

    # global merged metric index -> raw client decision
    provided: dict[int, dict[str, Any]] = {}
    for doc_index, doc_decisions in enumerate(per_document_decisions):
        if doc_index >= len(offsets):
            raise ValueError("More decision groups than merged documents")
        offset = offsets[doc_index]
        for raw in doc_decisions:
            local_index = int(raw["metric_index"])
            provided[offset + local_index] = raw

    reviewed_at = utc_now_iso()
    decisions: list[ReviewDecision] = []
    for item in review_items:
        if item.metric_name not in TEMPLATE_FIELDS:
            continue
        raw = provided.get(item.metric_index)
        status, note = _resolve_decision(item, raw)
        decisions.append(
            ReviewDecision(
                metric_id=item.metric_id,
                review_status=status,
                reviewer_note=note,
                reviewed_at=reviewed_at,
                reviewer=reviewer or DEFAULT_REVIEWER,
            )
        )

    # touch by_index so a malformed offset surfaces as a clear error
    missing = [idx for idx in provided if idx not in by_index]
    if missing:
        raise ValueError(f"Decision references unknown merged metric index: {missing}")

    return ReviewDecisionsFile(
        run_id=merged.run_id,
        created_at=reviewed_at,
        is_demo=False,
        decisions=decisions,
    )


def _resolve_decision(item: Any, raw: dict[str, Any] | None) -> tuple[str, str | None]:
    if raw is None:
        status = "not_applicable" if item.value in (None, "") else "approved"
        note = DEFAULT_ATTENTION_NOTE if item.requires_attention else None
        return status, note

    status = str(raw.get("review_status") or "approved").strip().lower()
    note = raw.get("note")
    note = note.strip() if isinstance(note, str) else None
    note = note or None
    if item.requires_attention and not note:
        note = DEFAULT_ATTENTION_NOTE
    return status, note
